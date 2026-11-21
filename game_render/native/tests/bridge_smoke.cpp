// Exercise the actual view callback against synthetic Windows memory. No game
// module, hook installation, injection or private game data is used by this test.
// Including the implementation keeps this gate on the production callback, not
// a second model of its state machine that could pass while the DLL is broken.
#include "../src/bridge_win.cpp"
// Focus is an observed input to the editor. Supply a synthetic HWND token for
// focus-sensitive enqueue tests without activating or controlling any window.
static HWND editor_foreground_window_stub = nullptr;
static HWND editor_fixture_foreground_window() noexcept {
    return editor_foreground_window_stub ? editor_foreground_window_stub : ::GetForegroundWindow();
}
#define GetForegroundWindow editor_fixture_foreground_window
#include "../src/dolly_editor_win.cpp"
#undef GetForegroundWindow

#include <chrono>
#include <thread>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace smoke {

void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

void close_to(double actual, double expected, const char* message, double tolerance = 1e-4) {
    require(std::isfinite(actual) && std::abs(actual - expected) <= tolerance, message);
}

struct Allocation {
    void* pointer;
    explicit Allocation(std::size_t bytes)
        : pointer(VirtualAlloc(nullptr, bytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) {
        require(pointer != nullptr, "Could not allocate synthetic native test memory");
    }
    ~Allocation() {
        if (pointer)
            VirtualFree(pointer, 0, MEM_RELEASE);
    }
    Allocation(const Allocation&) = delete;
    Allocation& operator=(const Allocation&) = delete;
    std::uintptr_t address() const { return reinterpret_cast<std::uintptr_t>(pointer); }
};

template <typename T> void put(std::uintptr_t address, const T& value) {
    std::memcpy(reinterpret_cast<void*>(address), &value, sizeof(value));
}

float __fastcall original_gameplay_weight(void*) {
    return 0.75f;
}

int __fastcall original_gameplay_option(void*, std::uint32_t key, int fallback) {
    require(key == 123, "Render-option key was changed");
    return fallback;
}

void gameplay_effect_filter_checks(const GameplayEffectsProfile& profile) {
    const auto old_profile = gGameplayEffectsProfile;
    gGameplayEffectsProfile = &profile;
    Allocation controller(0x1000), damage(0x1000), death(0x1000), blinded(0x1000);
    for (unsigned i = 0; i < 3; ++i) {
        put(controller.address() + 0x20 + i * 0x28, std::int32_t(2));
        put(controller.address() + 0x28 + i * 0x28, i == 0   ? damage.address()
                                                    : i == 1 ? death.address()
                                                             : blinded.address());
    }
    const auto old_client = gClient;
    const auto old_command = std::atomic_load(&gCommand);
    const auto old_original = gOriginalGameplayEffectWeight;
    const auto old_option = gOriginalGameplayRenderOption;
    const auto old_beat = gHeartbeatTime.load();
    const auto old_error = gWorkerError.load();
    const auto old_active = gGameplayCameraActive.load();
    gClient = controller.address() - profile.controller;
    gOriginalGameplayEffectWeight = original_gameplay_weight;
    gOriginalGameplayRenderOption = original_gameplay_option;
    gHeartbeatTime = now_seconds();
    gWorkerError = 0;
    gGameplayCameraActive = true;
    auto command = std::make_shared<Command>();
    command->wire.mode = std::uint32_t(Mode::Manual);
    auto publish = [&] { std::atomic_store(&gCommand, std::shared_ptr<const Command>(command)); };
    publish();
    const auto caller = gClient + profile.weight_caller;
    const auto screen_caller = gClient + profile.screen_caller;
    auto screen = [&] { return gameplay_render_option(nullptr, 123, 1, screen_caller); };
    require(screen() == 0, "Dolly retained selected-player screen particles");
    require(gameplay_render_option(nullptr, 123, 7, screen_caller + 1) == 7,
            "Unrelated render option was changed");
    auto weight = [&](std::uintptr_t row, std::uintptr_t from) {
        return gameplay_effect_weight(reinterpret_cast<void*>(row), from);
    };
    close_to(weight(damage.address(), caller), 0, "Damage leaks into Dolly camera");
    close_to(weight(death.address() + profile.row_size, caller), 0,
             "Death leaks into Dolly camera");
    close_to(weight(blinded.address(), caller), .75, "Unrelated game effect was filtered");
    close_to(weight(damage.address() + 1, caller), .75, "Misaligned event was accepted");
    close_to(weight(damage.address() + 2 * profile.row_size, caller), .75,
             "Past-end event was accepted");
    const auto other_stride = profile.row_size == 32 ? 36 : 32;
    close_to(weight(damage.address() + other_stride, caller), .75,
             "Another build's event stride was accepted");
    close_to(weight(damage.address(), caller + 1), .75, "Non-render caller was filtered");
    require(gameplay_damage_or_death(controller.address(), damage.address()),
            "Damage event queue was changed by filtering");
    command->wire.flags = kGamePov;
    publish();
    close_to(weight(death.address(), caller), .75, "Player POV lost death effects");
    require(screen() == 1, "Player POV lost screen particles");
    command->wire.flags = 0;
    command->wire.mode = std::uint32_t(Mode::Release);
    publish();
    close_to(weight(damage.address(), caller), .75, "F9 release lost damage effects");
    require(screen() == 1, "F9 lost screen particles");
    command->wire.mode = std::uint32_t(Mode::Play);
    publish();
    close_to(weight(damage.address(), caller), 0, "Authored shot lost damage filtering");
    require(screen() == 0, "Authored shot retained screen particles");
    gGameplayCameraActive = false;
    close_to(weight(damage.address(), caller), .75, "Unowned camera was filtered");
    require(screen() == 1, "Unowned camera lost screen particles");
    gGameplayCameraActive = true;
    gWorkerError = 30;
    close_to(weight(damage.address(), caller), .75, "Dead editor retained filtering");
    require(screen() == 1, "Failed editor retained screen-particle suppression");
    gWorkerError = 0;
    gHeartbeatTime = now_seconds() - 3;
    close_to(weight(damage.address(), caller), .75, "Expired heartbeat retained filtering");
    require(screen() == 1, "Expired heartbeat retained screen-particle suppression");
    gHeartbeatTime = now_seconds();
    put(controller.address() + 0x20, std::int32_t(-1));
    close_to(weight(damage.address(), caller), .75, "Invalid event count was accepted");
    std::atomic_store(&gCommand, old_command);
    gOriginalGameplayEffectWeight = old_original;
    gOriginalGameplayRenderOption = old_option;
    gClient = old_client;
    gHeartbeatTime = old_beat;
    gWorkerError = old_error;
    gGameplayCameraActive = old_active;
    gGameplayEffectsProfile = old_profile;
}

void gameplay_effect_filter_checks() {
    for (const auto& profile : kGameplayEffectsProfiles)
        gameplay_effect_filter_checks(profile);
    Allocation image(0x1000);
    GameplayEffectsProfile profile{};
    profile.controller = 0x100;
    const auto table = image.address() + 0x800;
    put(image.address() + profile.controller, table);
    require(gameplay_controller_matches(image.address(), profile, table),
            "Verified gameplay controller was refused");
    auto stale = profile;
    stale.controller += 0x100;
    require(!gameplay_controller_matches(image.address(), stale, table),
            "Relocated gameplay controller silently accepted stale queues");
    require(!gameplay_controller_matches(image.address(), profile, 0),
            "Missing named controller vtable was accepted");
    put(image.address() + profile.controller, table + 8);
    require(!gameplay_controller_matches(image.address(), profile, table),
            "Another gameplay object type was accepted");
}

void attach_preview_continuity_checks() {
    AttachPreview preview{};
    preview.camera_command = 7;
    preview.handle = 11;
    preview.entity_index = 4;
    preview.model = 123;
    preview.point = AttachPoint::bone;
    preview.bone_hash = 456;
    preview.cache.pawn = 1000;
    preview.cache.identity = 2000;
    preview.cache.node = 3000;
    preview.cache.model_object = 4000;
    preview.cache.bone_array = 5000;
    EditorAttachConfig config{};
    config.sequence = 2;
    config.hide = 4;
    std::fill(std::begin(config.offsets), std::end(config.offsets), 8u);
    AttachPreviewContinuity continuity{};
    require(!continuity.observe(preview, config, 1), "A new preview reused an old camera view");
    config.sequence = 4;
    config.offset[0] = 25;
    preview.authored[0] = 25;
    preview.smoothing = .5;
    config.hide |= 1; // Visibility changes do not change the camera frame.
    require(continuity.observe(preview, config, 1),
            "An offset acknowledgement reset the same camera's smoothing");
    require(!continuity.observe(preview, config, 2), "A different selected camera reused smoothing");
    require(continuity.observe(preview, config, 2), "Unchanged camera lost continuity");
    config.hide |= 8;
    require(!continuity.observe(preview, config, 2), "A different aim mode reused smoothing");
    preview.bone_hash++;
    require(!continuity.observe(preview, config, 2), "A different bone reused smoothing");
    preview.cache.node++;
    require(!continuity.observe(preview, config, 2), "A reconstructed target reused smoothing");
    config.offsets[0]++;
    require(!continuity.observe(preview, config, 2), "A different schema reused smoothing");
    preview.camera_command++;
    require(!continuity.observe(preview, config, 2), "A different camera command reused smoothing");
    continuity.reset();
    require(!continuity.observe(preview, config, 2), "A detached preview reused its old view");
}

void bone_string_bounds() {
    Allocation text(0x1000);
    char name[65];
    std::memset(name, '!', sizeof(name));
    std::memset(text.pointer, 'a', 64);
    require(!attach_runtime::read_c_string(text.address(), name, sizeof(name)) && name[0] == 0,
            "Unterminated bone name was accepted");
    reinterpret_cast<char*>(text.pointer)[63] = 0;
    require(attach_runtime::read_c_string(text.address(), name, sizeof(name)) &&
                std::strlen(name) == 63 && name[64] == '!',
            "Maximum bone name did not preserve output bounds");
    reinterpret_cast<char*>(text.pointer)[4] = '\x01';
    require(!attach_runtime::read_c_string(text.address(), name, sizeof(name)) && name[0] == 0,
            "Invalid name prefix was silently accepted");
    Allocation guarded(0x2000);
    std::memcpy(reinterpret_cast<void*>(guarded.address() + 0x1000 - 5), "head", 5);
    DWORD previous = 0;
    require(VirtualProtect(reinterpret_cast<void*>(guarded.address() + 0x1000), 0x1000,
                           PAGE_NOACCESS, &previous) != 0,
            "Could not guard string test page");
    require(attach_runtime::read_c_string(guarded.address() + 0x1000 - 5, name, sizeof(name)) &&
                std::strcmp(name, "head") == 0,
            "Valid bone name at a readable page boundary was rejected");
}

void owned_model_bones() {
    Allocation node(0x1000), pawn(0x1000), identity(0x1000), binding(0x1000), model(0x1000),
        path(0x1000), names(0x2000), vector(0x1000), poses(0x1000);
    const char* model_name = "models/heroes_staging/yamato_v2/yamato.vmdl";
    std::strcpy(static_cast<char*>(path.pointer), model_name);
    put(node.address() + 0x150 + 0xa0, binding.address());
    put(binding.address(), model.address());
    put(model.address() + 8, path.address());
    put(model.address() + 0x168, vector.address());
    put(model.address() + 0x170, std::uint64_t(20));
    put(model.address() + 0x178, std::uint64_t(20));
    put(model.address() + 0x174, std::uint32_t(0x61d09e00));
    put(model.address() + 0x17c, std::uint32_t(0x68a0b8e0));
    for (unsigned i = 0; i < 20; ++i) {
        const auto name = names.address() + i * 64;
        std::strcpy(reinterpret_cast<char*>(name), i == 0    ? "root_motion"
                                                   : i == 1  ? "head"
                                                   : i == 19 ? "$cloth_m0p266"
                                                             : "extra_bone");
        put(vector.address() + i * 8, name);
    }
    put(node.address() + 0x150 + 0x80, poses.address());
    put(node.address() + 0x150 + 0x90, std::uint32_t(20));
    const float pose[8] = {0, 0, 0, 1, 0, 0, 0, 1};
    std::memcpy(poses.pointer, pose, sizeof(pose));
    attach_runtime::Offsets offsets{};
    offsets.origin = 200;
    attach_runtime::Cache cache{};
    cache.name_offset = 0x150 + 0xa8;
    cache.model = attach_runtime::model_token(model_name);
    AttachSegment segment{};
    segment.point = AttachPoint::bone;
    segment.bone_hash = attach_runtime::model_token("head");
    const char* error = nullptr;
    require(attach_runtime::resolve_bone(offsets, node.address(), segment, cache, error) &&
                cache.bone_index == 1 && cache.model_object == model.address(),
            "Owned complete skeleton with cloth joints was rejected");
    offsets.scene_node = 816;
    offsets.owner = 48;
    put(pawn.address() + offsets.scene_node, node.address());
    put(node.address() + offsets.owner, pawn.address());
    put(node.address() + cache.name_offset, path.address());
    cache.ready = true;
    cache.pawn = pawn.address();
    cache.identity = identity.address();
    cache.handle = 1234;
    put(pawn.address() + 0x10, identity.address());
    put(identity.address(), pawn.address());
    put(identity.address() + 0x10, cache.handle);
    cache.node = node.address();
    cache.name_pointer = path.address();
    AttachSample sample{};
    require(attach_runtime::sample(offsets, cache, segment, sample, error),
            "Owned bone sample was rejected");
    // A stale skeleton root must not make a correctly resolved Head trail the
    // player's current translation. Keep genuine local head animation intact.
    const float earlier_root[3] = {100, 200, 300};
    const float earlier_head[3] = {102, 203, 380};
    const float current_origin[3] = {112, 194, 315};
    std::memcpy(poses.pointer, earlier_root, sizeof(earlier_root));
    std::memcpy(reinterpret_cast<void*>(poses.address() + 32), earlier_head, sizeof(earlier_head));
    std::memcpy(reinterpret_cast<void*>(node.address() + offsets.origin), current_origin,
                sizeof(current_origin));
    require(attach_runtime::sample(offsets, cache, segment, sample, error),
            "Rebased head rejected");
    close_to(sample.origin[0], 114, "Head trails current scene X");
    close_to(sample.origin[1], 197, "Head trails current scene Y");
    close_to(sample.origin[2], 395, "Head trails current scene Z");
    close_to(sample.motion_anchor[0], 112, "Smoothing anchor uses stale root");
    // Once animation catches up, an unchanged local head pose must not jump.
    const float later_head[3] = {114, 197, 395};
    std::memcpy(poses.pointer, current_origin, sizeof(current_origin));
    std::memcpy(reinterpret_cast<void*>(poses.address() + 32), later_head, sizeof(later_head));
    require(attach_runtime::sample(offsets, cache, segment, sample, error),
            "Current head rejected");
    close_to(sample.origin[0], 114, "Skeleton refresh shifted rebased head");
    close_to(sample.origin[2], 395, "Skeleton refresh shifted rebased head height");
    put(poses.address() + 32 + 8, 397.0f);
    require(attach_runtime::sample(offsets, cache, segment, sample, error),
            "Animated head rejected");
    close_to(sample.origin[2], 397, "Rebase erased local bone animation");
    // Exercise the actual producer handoff, including callback provenance and
    // invalidating history when a paused replay changes tick.
    auto render_owner = std::make_shared<attach_runtime::Cache>(cache);
    rendered_attach_sample(0, render_owner, render_owner.get(), offsets, segment, sample, 4.0, 800,
                           100, false);
    observe_attach_pose(cache.handle, 999); // wrong renderer frame
    require(gRenderAttach[0].recorded == 0, "Wrong renderer frame populated pose history");
    observe_attach_pose(cache.handle + 1, 1);
    require(gRenderAttach[0].recorded == 0, "Wrong owner populated pose history");
    observe_attach_pose(cache.handle, 1);
    require(gRenderAttach[0].recorded == 800, "Completed mesh pose not observed");
    put(poses.address() + 32 + 8, 400.0f);
    require(attach_runtime::sample(offsets, cache, segment, sample, error),
            "Updated head rejected");
    rendered_attach_sample(0, render_owner, render_owner.get(), offsets, segment, sample, 4.01, 801,
                           100, false);
    close_to(sample.origin[2], 397, "Camera reused stepped early pose instead of completed pose");
    observe_attach_pose(cache.handle, 1);
    require(attach_runtime::sample(offsets, cache, segment, sample, error), "Pause head rejected");
    rendered_attach_sample(0, render_owner, render_owner.get(), offsets, segment, sample, 4.02, 802,
                           101, true);
    close_to(sample.origin[2], 400, "Pause transition reused animation history");
    observe_attach_pose(cache.handle, 1);
    put(poses.address() + 32 + 8, 401.0f);
    require(attach_runtime::sample(offsets, cache, segment, sample, error), "Step head rejected");
    rendered_attach_sample(0, render_owner, render_owner.get(), offsets, segment, sample, 4.03, 803,
                           102, true);
    close_to(sample.origin[2], 401, "Paused tick step reused older bone pose");
    gRenderAttach[0].source = {};
    gRenderAttach[0].history.reset();
    gRenderAttach[0].handle.store(0);
    // Bebop starts with pelvis; root_motion is a later bone. The movement
    // correction must preserve anatomical height in both camera and picker.
    const char* pelvis_first[] = {"pelvis", "head",   "spine_0",     "neck_0",
                                  "hand_L", "hand_R", "arm_upper_L", "leg_upper_L"};
    for (unsigned i = 0; i < 64; ++i) {
        const auto name = names.address() + i * 64;
        std::strcpy(reinterpret_cast<char*>(name), i < 8     ? pelvis_first[i]
                                                   : i == 32 ? "root_motion"
                                                             : "extra_bone");
        put(vector.address() + i * 8, name);
    }
    put(model.address() + 0x170, std::uint32_t(64));
    put(model.address() + 0x178, std::uint32_t(64));
    put(node.address() + 0x150 + 0x90, std::uint32_t(64));
    put(poses.address() + 2 * 4, 365.0f);
    std::memcpy(reinterpret_cast<void*>(poses.address() + 32 * 32), current_origin,
                sizeof(current_origin));
    require(attach_runtime::resolve_bone(offsets, node.address(), segment, cache, error) &&
                cache.root_index == 32,
            "Pelvis-first movement root was not resolved by name");
    require(attach_runtime::sample(offsets, cache, segment, sample, error),
            "Pelvis-first skeleton sample rejected");
    close_to(sample.origin[2], 401, "Pelvis height was subtracted from attached Head");
    AttachPreview preview{};
    preview.cache = cache;
    preview.cache.picker = std::make_shared<PickerCatalog>();
    preview.schema = offsets;
    PickerSample picked{};
    require(sample_picker(&preview, picked, error), "Pelvis-first picker sample rejected");
    close_to(picked.transforms[1][2], 401, "Picker Head was lowered by pelvis height");
    close_to(picked.transforms[0][2], 365, "Picker pelvis was moved to the scene origin");
    put(identity.address() + 0x10, std::uint32_t(1235));
    require(!attach_runtime::sample(offsets, cache, segment, sample, error),
            "A recycled entity handle was accepted as the original target");
    put(identity.address() + 0x10, cache.handle);
    put(node.address() + 0x150 + 0x80, poses.address() + 0x100);
    require(!attach_runtime::sample(offsets, cache, segment, sample, error),
            "A replaced pose buffer was sampled through a stale cache");
    put(node.address() + 0x150 + 0x80, poses.address());

    std::strcpy(static_cast<char*>(path.pointer), "models/other.vmdl");
    require(!attach_runtime::resolve_bone(offsets, node.address(), segment, cache, error),
            "A different model's skeleton was accepted");
    std::strcpy(static_cast<char*>(path.pointer), model_name);
    put(node.address() + 0x150 + 0x90, std::uint32_t(19));
    require(!attach_runtime::resolve_bone(offsets, node.address(), segment, cache, error),
            "An undersized pose buffer was accepted");
    for (const char* invalid :
         {"$cloth_m", "$cloth_mp1", "$cloth_m0p", "$cloth_m0p1/path", "$other"})
        require(!attach_runtime::valid_bone_name(invalid), "Malformed generated joint accepted");
}

void bone_vector_selection() {
    Allocation object(0x1000), names(0x4000), vectors(0x1000);
    const char* markers[] = {"pelvis", "spine_0", "neck_0",      "head",
                             "hand_L", "hand_R",  "arm_upper_L", "leg_upper_L"};
    for (unsigned i = 0; i < 40; ++i) {
        const auto text = names.address() + i * 64;
        std::snprintf(reinterpret_cast<char*>(text), 64, "%s",
                      i == 0   ? "root_motion"
                      : i <= 8 ? markers[i - 1]
                               : "extra_bone");
        put(vectors.address() + i * 8, text);
        put(vectors.address() + 0x400 + i * 16, text);
        put(vectors.address() + 0x408 + i * 16,
            std::uint64_t(std::strlen(reinterpret_cast<char*>(text))));
    }
    // A small skeleton appears first; the longer equal-score skeleton must win.
    put(object.address(), vectors.address());
    put(object.address() + 8, std::uint64_t(19));
    put(object.address() + 16, std::uint64_t(19));
    put(object.address() + 0x80, vectors.address() + 0x400);
    put(object.address() + 0x88, std::uint64_t(40));
    put(object.address() + 0x90, std::uint64_t(40));
    std::uintptr_t data = 0;
    std::uint32_t count = 0, stride = 0;
    require(attach_runtime::find_bone_vector(object.address(), data, count, stride),
            "Synthetic skeleton was not resolved");
    require(data == vectors.address() + 0x400 && count == 40 && stride == 16,
            "Later skeleton and its real entry stride must be preserved");
    require(attach_runtime::skeleton_name_score(data, count, stride) == 8,
            "Skeleton score counts unique markers");
    put(vectors.address() + 20 * 8, names.address() + 64); // duplicate pelvis cannot win ranking
    require(attach_runtime::skeleton_name_score(vectors.address(), 40, 8) == 8,
            "Repeated markers must not inflate the skeleton score");
    put(object.address() + 0x88, std::uint64_t(505));
    put(object.address() + 0x90, std::uint64_t(505));
    require(attach_runtime::find_bone_vector(object.address(), data, count, stride) && count == 19,
            "A valid prefix cannot authorize a bloated vector header");
    put(object.address() + 0x88, std::uint64_t(40));
    put(object.address() + 0x90, std::uint64_t(40));
    // A partial prefix must not authorize a vector with invalid name entries.
    put(vectors.address() + 0x400 + 20 * 16, std::uintptr_t(0));
    require(attach_runtime::find_bone_vector(object.address(), data, count, stride) &&
                count == 19 && stride == 8,
            "Malformed larger vector must fall back to the intact smaller vector");
    put(vectors.address(), names.address() + 64);
    require(!attach_runtime::find_bone_vector(object.address(), data, count, stride),
            "Reduced physics skeleton must not supply render bone indices");
}

bool playing = true, paused = true, seeking = false, active = true;
int tick = 4096;
char demo_name[512] = "replays/native-smoke.dem";
bool __fastcall is_playing(void*) {
    return playing;
}
bool __fastcall is_paused(void*) {
    return paused;
}
bool __fastcall is_seeking(void*) {
    return seeking;
}
bool __fastcall is_active(void*) {
    return active;
}
int __fastcall current_tick(void*) {
    return tick;
}
const char* __fastcall current_name(void*) {
    return demo_name;
}
unsigned basis_calls = 0;
void __fastcall synthetic_basis(const float* angles, float* forward, float* right, float* up) {
    ++basis_calls;
    // Distinct output sentinels prove all three destinations and the input pose
    // reach the engine helper; this fixture does not replace the engine math.
    for (unsigned i = 0; i < 3; ++i) {
        forward[i] = angles[i] + 10;
        right[i] = angles[i] + 20;
        up[i] = angles[i] + 30;
    }
}

void camera_cache_decoder() {
    using namespace camera_view;
    std::vector<unsigned char> code(0x860, 0xcc);
    std::memcpy(code.data(), kAuxiliaryCopy, sizeof(kAuxiliaryCopy));
    std::memcpy(code.data() + 0x80, kOriginCopy, sizeof(kOriginCopy));
    std::memcpy(code.data() + 0x100, kCachePattern, sizeof(kCachePattern));
    constexpr std::uintptr_t start = 0x100000, data = 0x300000, text = 0x80000;
    const auto displacement = [&](unsigned operand, std::uintptr_t target) {
        const auto delta = std::int32_t(std::int64_t(target) - (start + 0x100 + operand + 4));
        std::memcpy(code.data() + 0x100 + operand, &delta, 4);
    };
    displacement(3, data + 64);
    displacement(16, data + 48);
    displacement(26, data + 32);
    displacement(31, text + 32); // Negative RIP displacement must remain signed.
    displacement(43, data + 100);
    displacement(51, data);
    displacement(61, data + 8);
    displacement(74, data + 16);
    displacement(83, data + 24);
    const auto resolve = [&] {
        return decode(code.data(), code.size(), start, data, 1024, text, 1024);
    };
    auto cache = resolve();
    require(cache.origin == data && cache.up == data + 64 &&
                reinterpret_cast<std::uintptr_t>(cache.basis) == text + 32,
            "Reviewed camera cache layout did not decode");
    displacement(61, data + 9);
    require(!resolve().basis, "Split origin store mismatch accepted");
    displacement(61, data + 8);
    displacement(31, text + 1010);
    require(!resolve().basis, "Truncated helper outside executable section accepted");
    displacement(31, text + 32);
    require(!decode(code.data(), code.size(), start, data, 75, text, 1024).basis,
            "Truncated camera cache data accepted");
    code[14] ^= 1;
    require(!resolve().basis, "Changed auxiliary camera layout accepted");
    code[14] ^= 1;
    std::memcpy(code.data() + 0x200, code.data() + 0x100, sizeof(kCachePattern));
    require(!resolve().basis, "Ambiguous camera cache block accepted");
}

void updated_camera_cache_decoder() {
    using namespace camera_view;
    std::vector<unsigned char> code(0x200, 0xcc), producer(0x2ad, 0xcc);
    std::memcpy(code.data(), kUpdatedCachePattern, sizeof(kUpdatedCachePattern));
    std::memcpy(producer.data(), kUpdatedAuxiliaryCopy, sizeof(kUpdatedAuxiliaryCopy));
    constexpr std::uintptr_t start = 0x100000, data = 0x300000, text = 0x80000;
    const auto displacement = [&](unsigned operand, std::uintptr_t target) {
        const auto delta = std::int32_t(std::int64_t(target) - (start + operand + 4));
        std::memcpy(code.data() + operand, &delta, 4);
    };
    displacement(3, data + 64);
    displacement(18, data + 48);
    displacement(25, data + 32);
    displacement(37, text + 32);
    displacement(58, data);
    displacement(70, data + 8);
    displacement(86, data + 16);
    displacement(98, data + 24);
    const auto resolve = [&] {
        return decode_updated(code.data(), code.size(), start, producer.data(), producer.size(),
                              data, 1024, text, 1024);
    };
    const auto cache = resolve();
    require(cache.basis && cache.origin == data && cache.view_layout.aspect == 0x4d4 &&
                cache.view_layout.flags == 0x551 && cache.view_layout.extra_origin == 0x56c,
            "Updated camera cache and layout were not decoded");
    displacement(98, data + 28);
    require(!resolve().basis, "Updated cache accepted inconsistent split angle store");
    displacement(98, data + 24);
    producer[22] ^= 4;
    require(!resolve().basis, "Updated cache accepted the wrong auxiliary origin offset");
    producer[22] ^= 4;
    displacement(37, text + 1010);
    require(!resolve().basis, "Updated cache accepted helper outside executable bounds");
    displacement(37, text + 32);
    require(!decode_updated(code.data(), code.size(), start, producer.data(), producer.size(), data,
                            75, text, 1024)
                 .basis,
            "Updated cache accepted truncated data");
    std::memcpy(code.data() + 0x100, code.data(), sizeof(kUpdatedCachePattern));
    require(!resolve().basis, "Updated cache accepted ambiguous wrapper cache stores");
}

template <typename Function> void thunk(std::uintptr_t address, Function function) {
    // mov rax, imm64; jmp rax. The synthetic module keeps the production
    // helper addresses unchanged while redirecting calls to harmless fixtures.
    unsigned char bytes[12] = {0x48, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xe0};
    const auto target = reinterpret_cast<std::uintptr_t>(function);
    std::memcpy(bytes + 2, &target, sizeof(target));
    DWORD previous = 0;
    require(VirtualProtect(reinterpret_cast<void*>(address), sizeof(bytes), PAGE_READWRITE,
                           &previous) != 0,
            "Could not make a synthetic helper writable");
    std::memcpy(reinterpret_cast<void*>(address), bytes, sizeof(bytes));
    require(VirtualProtect(reinterpret_cast<void*>(address), sizeof(bytes), PAGE_EXECUTE_READ,
                           &previous) != 0,
            "Could not make a synthetic helper executable");
    require(FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(address),
                                  sizeof(bytes)) != 0,
            "Could not flush synthetic helper instructions");
}

void u32(std::vector<unsigned char>& bytes, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        bytes.push_back(static_cast<unsigned char>(value >> (8 * i)));
}

void number(std::vector<unsigned char>& bytes, double value) {
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned i = 0; i < 8; ++i)
        bytes.push_back(static_cast<unsigned char>(bits >> (8 * i)));
}

const CameraPose first = {100, 200, 300, -15, 175, 0, 16.0 / 9};
const CameraPose last = {120, 240, 310, -25, 205, 20, 4.0 / 3};

std::shared_ptr<const NativePath> make_path() {
    const char magic[8] = {'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    std::vector<unsigned char> bytes(magic, magic + 8);
    u32(bytes, 1);
    u32(bytes, 1);
    u32(bytes, 7);
    u32(bytes, 0);
    number(bytes, 1);
    number(bytes, 0);
    number(bytes, 1);
    for (double value : first)
        number(bytes, value);
    for (double value : last)
        number(bytes, value);
    number(bytes, 0);
    number(bytes, 1);
    for (std::size_t i = 0; i < first.size(); ++i) {
        u32(bytes, 1);
        u32(bytes, 0);
        number(bytes, first[i]);
        number(bytes, last[i]);
        number(bytes, 0);
        number(bytes, 0);
    }
    auto path = std::make_shared<NativePath>();
    std::string error;
    require(path->load(bytes.data(), bytes.size(), error),
            "Synthetic path must parse through NativePath.load");
    return path;
}

struct CurveViewerFixture {
    HANDLE mapping = nullptr;
    unsigned char* memory = nullptr;
    std::wstring name = L"Local\\DollyCurveEnqueue." + std::to_wstring(GetCurrentProcessId());
    CurveViewerFixture() {
        const auto viewer_name = name + L".viewer";
        mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                     kVisualizationMappingBytes, viewer_name.c_str());
        require(mapping != nullptr, "Cannot create synthetic curve mapping");
        memory = static_cast<unsigned char*>(MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0,
                                                          kVisualizationMappingBytes));
        require(memory != nullptr, "Cannot map synthetic curve packet");
        std::vector<unsigned char> blob{'D','L','Y','P','A','T','H',0};
        u32(blob, 1); u32(blob, 1); u32(blob, 7); u32(blob, 0);
        number(blob, 1); number(blob, 0); number(blob, 1);
        for (double value : first) number(blob, value);
        for (double value : last) number(blob, value);
        number(blob, 0); number(blob, 1);
        for (unsigned channel = 0; channel < 7; ++channel) {
            u32(blob, 1); u32(blob, 0);
            number(blob, first[channel]); number(blob, last[channel]);
            number(blob, 0); number(blob, 0);
        }
        VisualizationHeader header{};
        std::memcpy(header.magic, "DLYVIS01", 8); header.sequence = 2; header.abi = 4;
        header.enabled = 1; header.selected_camera = 1; header.camera_count = 2;
        header.path_bytes = std::uint32_t(blob.size()); header.sample_budget = 2048;
        header.marker_budget = 64; header.project_revision = 7;
        header.session_id = 0xf123456789abcdefULL; header.key_metadata_bytes = 2*136;header.flags=2;
        std::memcpy(memory, &header, sizeof(header));
        std::vector<unsigned char> body; number(body, 0); number(body, 1);
        for (const auto& pose : {first, last}) {
            for (double value : pose) number(body, value);
            u32(body, 0); u32(body, 0);
            for(unsigned axis=0;axis<3;++axis) {u32(body,0);u32(body,0);number(body,0);number(body,0);}
        }
        body.insert(body.end(), blob.begin(), blob.end());
        std::memcpy(memory + 64, body.data(), body.size());
        visualization_worker_tick(name.c_str(), true);
        require(visualization_runtime_state() == VisualizationRuntimeState::Ready,
                "Real viewer worker rejected the synthetic curve packet");
    }
    ~CurveViewerFixture() {
        visualization_worker_tick(nullptr, false);
        if (memory) UnmapViewOfFile(memory);
        if (mapping) CloseHandle(mapping);
    }
};

struct Fixture {
    Allocation client{63733760}, engine{0x969000}, view{0x2000};
    Allocation demo{4096}, engine_client{4096}, globals{4096};
    std::vector<unsigned char> mapping = std::vector<unsigned char>(kMappingBytes);
    std::shared_ptr<const NativePath> path = make_path();
    std::uint32_t next_command = 0;
    const float original_fov = 90, original_aspect = 16.0f / 9;

    Fixture() {
        require(QueryPerformanceFrequency(&gFrequency) != 0, "QPC frequency unavailable");
        gClient = client.address();
        gEngine = engine.address();
        gEngineLayout = kLegacyEngineLayout;
        gMemory = mapping.data();
        // Synthetic profile: the September 11 reviewed layout. The fixture
        // allocates a full-size client image, so the real offsets fit.
        gCompat = CompatResolution{};
        gCompat.resolved = true;
        gCompat.exact = true;
        gCompat.setup = 0x16bd550;
        gCompat.caller = 0x16b6ce4;
        gCompat.view_table = 0x2349418;
        gCompat.globals = 0x2f091f0;
        gCompat.engine_client = 0x37f67c0;
        gCameraCache = {gClient + 0x3800660, gClient + 0x3800670, gClient + 0x3800680,
                        gClient + 0x3800690, gClient + 0x38006a0, synthetic_basis};
        put(gClient + gCompat.engine_client, engine_client.address());
        put(gClient + gCompat.globals, globals.address());
        put(gEngine + kDemoGlobal, demo.address());
        put(demo.address(), gEngine + kDemoTable);
        put(engine_client.address(), gEngine + kEngineTable);
        thunk(gEngine + 0x2ec00, is_playing);
        thunk(gEngine + 0x7bf30, is_active);
        thunk(gEngine + 0x368d0, is_paused);
        thunk(gEngine + 0x2a240, is_seeking);
        thunk(gEngine + 0x36910, current_tick);
        thunk(gEngine + 0x7bf80, current_name);
        put(globals.address() + 0x54, 1.0f / 64);
        clock(64, 4096);
        gWorkerError = 0;
        gHeartbeatTime = now_seconds();
        original_view();
    }

    ~Fixture() {
        std::atomic_store(&gCommand, std::shared_ptr<const Command>());
        gMemory = nullptr;
        gClient = 0;
        gEngine = 0;
        gCameraCache = {};
    }

    void clock(float time, int at_tick) {
        put(globals.address() + 0x30, time);
        tick = at_tick;
    }

    void original_view(unsigned char flags = 0) {
        put(view.address(), gClient + gCompat.view_table);
        const auto camera = view.address() + 0x10;
        const float xyz[3] = {10, 20, 30}, angles[3] = {1, 2, 3};
        std::memcpy(reinterpret_cast<void*>(camera + 0x4a0), xyz, sizeof(xyz));
        std::memcpy(reinterpret_cast<void*>(camera + 0x4b8), angles, sizeof(angles));
        for (auto offset :
             {gCameraCache.view_layout.auxiliary_origin, gCameraCache.view_layout.extra_origin})
            std::memcpy(reinterpret_cast<void*>(camera + offset), xyz, sizeof(xyz));
        std::memcpy(reinterpret_cast<void*>(camera + gCameraCache.view_layout.auxiliary_angles),
                    angles, sizeof(angles));
        std::memcpy(reinterpret_cast<void*>(gCameraCache.origin), xyz, sizeof(xyz));
        std::memcpy(reinterpret_cast<void*>(gCameraCache.angles), angles, sizeof(angles));
        for (auto address : {gCameraCache.forward, gCameraCache.right, gCameraCache.up}) {
            const float seed[3] = {-7, -8, -9};
            std::memcpy(reinterpret_cast<void*>(address), seed, sizeof(seed));
        }
        put(camera + 0x498, original_fov);
        put(camera + gCameraCache.view_layout.aspect, original_aspect);
        put(camera + 0x430, 1280);
        put(camera + 0x438, 720);
        put(camera + gCameraCache.view_layout.flags, flags);
    }

    std::uint32_t command(Mode mode, double phase = 0, std::uint32_t flags = kAspect) {
        auto value = std::make_shared<Command>();
        value->wire.command = ++next_command;
        value->wire.mode = static_cast<std::uint32_t>(mode);
        value->wire.start_phase = phase;
        value->wire.speed = .1;
        value->wire.flags = flags;
        value->path = path;
        std::snprintf(value->wire.demo_name, sizeof(value->wire.demo_name), "native-smoke.dem");
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(value));
        return next_command;
    }

    void enable_synthetic_editor() {
        // Only this synthetic test translation unit changes internal flags.
        // No production entry point bypasses input/overlay installation.
        EditorConfig config{};
        std::memcpy(config.magic, "DLYEDIT1", 8);
        config.sequence = 2;
        config.abi = kEditorAbi;
        config.enabled = 1;
        config.owner = std::uint32_t(EditorOwner::Flight);
        config.owner_sequence = 1;
        config.speed = 400;
        config.sensitivity = .08;
        std::memcpy(mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        dolly::gInput = true;
        editor_overlay_available(true);
        editor_worker_tick(mapping.data(), true);
    }

    void manual(Mode mode = Mode::Manual, const CameraPose* seed = nullptr) {
        command(mode);
        auto value = std::make_shared<Command>(*std::atomic_load(&gCommand));
        value->manual = true;
        value->path.reset();
        value->has_manual_pose = seed != nullptr;
        if (seed)
            value->manual_pose = *seed;
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(value));
    }

    Status status() const {
        Status result{};
        std::memcpy(&result, mapping.data() + kControlBytes, sizeof(result));
        require(std::memcmp(result.magic, kStatusMagic, 8) == 0 && result.abi == kBridgeAbi,
                "Actual callback did not publish valid bridge status");
        require(!(result.sequence & 1), "Published status must have a complete even sequence");
        return result;
    }

    ConfettiDiagnostics confetti() const {
        ConfettiDiagnostics result{};
        std::memcpy(&result, mapping.data() + kConfettiDiagnosticsOffset, sizeof(result));
        require(std::memcmp(result.magic, kConfettiDiagnosticsMagic, 8) == 0 &&
                    result.abi == kConfettiDiagnosticsAbi,
                "Actual callback did not publish valid confetti diagnostics");
        require(!(result.sequence & 1),
                "Published confetti diagnostics must have a complete even sequence");
        return result;
    }

    Status frame(unsigned char projection_flags = 0, bool renew_lease = true) {
        original_view(projection_flags);
        if (renew_lease)
            gHeartbeatTime = now_seconds();
        on_view(view.pointer, gClient + gCompat.caller);
        const auto value = status();
        if (!value.error &&
            (value.state == unsigned(State::Armed) || value.state == unsigned(State::Playing) ||
             value.state == unsigned(State::Completed))) {
            const auto camera = view.address() + 0x10;
            for (unsigned i = 0; i < 3; ++i) {
                const auto xyz = reinterpret_cast<const float*>(camera + 0x4a0)[i];
                const auto angle = reinterpret_cast<const float*>(camera + 0x4b8)[i];
                for (auto address :
                     {camera + gCameraCache.view_layout.auxiliary_origin,
                      camera + gCameraCache.view_layout.extra_origin, gCameraCache.origin})
                    close_to(reinterpret_cast<const float*>(address)[i], xyz,
                             "Camera left a player-anchored secondary/cached origin");
                for (auto address :
                     {camera + gCameraCache.view_layout.auxiliary_angles, gCameraCache.angles})
                    close_to(reinterpret_cast<const float*>(address)[i], angle,
                             "Camera left stale secondary/cached angles");
                close_to(reinterpret_cast<const float*>(gCameraCache.forward)[i], angle + 10,
                         "Forward cache was not derived from authored angles");
                close_to(reinterpret_cast<const float*>(gCameraCache.right)[i], angle + 20,
                         "Right cache was not derived from authored angles");
                close_to(reinterpret_cast<const float*>(gCameraCache.up)[i], angle + 30,
                         "Up cache was not derived from authored angles");
            }
            require(*reinterpret_cast<const unsigned char*>(
                        camera + gCameraCache.view_layout.flags) == (projection_flags & ~4u),
                    "Camera did not preserve other view flags while selecting its main view");
        }
        return value;
    }

    void unchanged() const {
        const auto camera = view.address() + 0x10;
        float xyz[3]{}, angles[3]{}, fov = 0, aspect = 0;
        require(read_memory(camera + 0x4a0, xyz, sizeof(xyz)) &&
                    read_memory(camera + 0x4b8, angles, sizeof(angles)) &&
                    read_value(camera + 0x498, fov) &&
                    read_value(camera + gCameraCache.view_layout.aspect, aspect),
                "Cannot read synthetic view");
        for (int i = 0; i < 3; ++i) {
            close_to(xyz[i], double((i + 1) * 10), "Released camera overwrote original position");
            close_to(angles[i], double(i + 1), "Released camera overwrote original angles");
        }
        close_to(fov, original_fov, "Released camera overwrote FOV");
        close_to(aspect, original_aspect, "Released camera overwrote aspect");
        for (unsigned i = 0; i < 3; ++i) {
            for (auto address :
                 {camera + gCameraCache.view_layout.auxiliary_origin,
                  camera + gCameraCache.view_layout.extra_origin, gCameraCache.origin})
                close_to(reinterpret_cast<const float*>(address)[i], (i + 1) * 10,
                         "Inactive camera overwrote the game's secondary origin");
            for (auto address :
                 {camera + gCameraCache.view_layout.auxiliary_angles, gCameraCache.angles})
                close_to(reinterpret_cast<const float*>(address)[i], i + 1,
                         "Inactive camera overwrote the game's cached angles");
            for (auto address : {gCameraCache.forward, gCameraCache.right, gCameraCache.up})
                close_to(reinterpret_cast<const float*>(address)[i], -7 - int(i),
                         "Inactive camera overwrote the game's basis");
        }
    }

    void applied(double phase, bool aspect_enabled = true) const {
        CameraPose expected{};
        require(path->evaluate(phase, expected), "Cannot evaluate expected camera");
        const auto camera = view.address() + 0x10;
        float xyz[3]{}, angles[3]{}, fov = 0, aspect = 0;
        require(read_memory(camera + 0x4a0, xyz, sizeof(xyz)) &&
                    read_memory(camera + 0x4b8, angles, sizeof(angles)) &&
                    read_value(camera + 0x498, fov) &&
                    read_value(camera + gCameraCache.view_layout.aspect, aspect),
                "Cannot read applied synthetic view");
        for (int i = 0; i < 3; ++i) {
            close_to(xyz[i], expected[i], "Native callback did not write path XYZ");
            close_to(angles[i], std::remainder(expected[i + 3], 360.0),
                     "Native callback did not write wrapped path rotation");
        }
        constexpr double pi = 3.14159265358979323846;
        const double expected_fov =
            aspect_enabled
                ? 360 / pi *
                      std::atan(std::tan(original_fov * pi / 360) * expected[6] / original_aspect)
                : original_fov;
        close_to(fov, expected_fov, "Aspect/FOV transform differs from actual view lens");
        close_to(aspect, aspect_enabled ? expected[6] : original_aspect,
                 "Native aspect flag was not respected");
    }

    void fresh_hold(double phase = 0) {
        playing = true;
        paused = true;
        seeking = false;
        active = true;
        std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke.dem");
        clock(64, 4096);
        gWorkerError = 0;
        command(Mode::Hold, phase);
        const auto value = frame();
        require(value.state == static_cast<unsigned>(State::Armed) && !value.error,
                "New HOLD must clear previous fault and arm on an actual view");
        applied(phase);
    }
};

void follow_anchor_guard_checks(Fixture& f) {
    // Drive the production private-layout/session gates against owned synthetic
    // memory. Compact RVAs keep this independent of any installed game image.
    const auto saved_profile = gFollowAnchorProfile;
    const auto saved_installed = gFollowAnchorInstalled.load();
    const auto saved_editor = gEditor;
    const auto saved_follow = std::atomic_load(&dolly::gFollowConfig);
    const auto saved_command = std::atomic_load(&gCommand);
    const auto saved_connected = dolly::gConnected.load();
    const auto saved_heartbeat = gHeartbeatTime.load();
    const auto saved_error = gWorkerError.load();
    FollowAnchorProfile profile = kFollowAnchorProfiles[0];
    profile.controller = 0x100;
    profile.manager = 0x200;
    profile.rig = 0x300;
    profile.entity_list = 0x400;
    profile.manager_table = 0x1100;
    profile.camera_table = 0x1200;
    profile.controller_table = 0x1300;
    profile.observer_table = 0x1400;
    profile.services_table = 0x1500;
    profile.observer_base = 0x1600;
    profile.pawn_table = 0x1700;
    require(profile.local_player_table == 0,
            "Retained Follow profile unexpectedly admits a local player pawn");
    profile.observer_services_offset = dolly::reviewed::FollowTarget::OBSERVER_SERVICES_OFFSET;
    require(profile.observer_services_offset == 0xe98,
            "Current Follow fixture must use the separately reviewed observer member");
    gFollowAnchorProfile = &profile;
    Allocation controller(0x1000), observer(0x2000), services(0x1000), old_services(0x1000);
    Allocation camera(0x1000), pawn(0x1000), system(0x1000), chunk(0x1000), cvar(0x1000);
    constexpr std::uint32_t observer_handle = 0x8001, target_handle = 0x10002;
    const auto observer_identity = chunk.address() + 0x70;
    const auto target_identity = chunk.address() + 0xe0;
    put(gClient + profile.manager, gClient + profile.manager_table);
    put(gClient + profile.manager + 0x28, camera.address());
    put(gClient + profile.manager + 0x38, static_cast<unsigned char>(0));
    put(camera.address(), gClient + profile.camera_table);
    put(camera.address() + 0xb0, target_handle);
    put(gClient + profile.controller, controller.address());
    put(controller.address(), gClient + profile.controller_table);
    put(controller.address() + 0x6bc, observer_handle);
    put(gClient + profile.entity_list, system.address());
    put(system.address(), chunk.address());
    for (const auto identity : {observer_identity, target_identity}) {
        const bool local = identity == observer_identity;
        const auto instance = local ? observer.address() : pawn.address();
        put(identity, instance);
        put(identity + 0x10, local ? observer_handle : target_handle);
        put(instance + 0x10, identity);
    }
    put(observer.address(), gClient + profile.observer_table);
    put(pawn.address(), gClient + profile.pawn_table);
    for (const auto service : {services.address(), old_services.address()}) {
        put(service, gClient + profile.services_table);
        put(service + 0x48, static_cast<unsigned char>(2));
        put(service + 0x4c, target_handle);
    }
    put(observer.address() + 0xe98, services.address());
    put(observer.address() + 0xe40, old_services.address());
    const auto target_valid = [&] {
        std::uint32_t selected = 0;
        return follow_anchor_target(camera.address(), pawn.address(), selected) &&
               selected == target_handle;
    };
    require(target_valid(), "Reviewed observer-pawn Follow chain was rejected");
    put(observer.address(), gClient + profile.pawn_table);
    require(!target_valid(), "Historical Follow profile silently widened its local pawn types");
    profile.local_player_table = profile.pawn_table;
    require(target_valid(), "Reviewed player-pawn local Follow chain was rejected");
    put(observer.address(), gClient + profile.observer_table);
    put(observer.address() + 0xe98, std::uintptr_t(0));
    require(!target_valid(), "Old observer member rescued a missing current member");
    profile.observer_services_offset = 0xe40;
    require(target_valid(), "Retained profile lost its independent old observer member");
    put(observer.address() + 0xe40, std::uintptr_t(0));
    put(observer.address() + 0xe98, services.address());
    require(!target_valid(), "Current observer member rescued a missing retained member");
    put(observer.address() + 0xe40, old_services.address());
    profile.observer_services_offset = 0xe98;

    const auto reject_then_restore = [&](auto address, auto wrong, auto original,
                                         const char* message) {
        put(address, wrong);
        require(!target_valid(), message);
        put(address, original);
        require(target_valid(), "Follow target fixture did not recover its valid baseline");
    };
    reject_then_restore(observer.address(), gClient + 0x1800, gClient + profile.observer_table,
                        "Unknown local pawn type was accepted");
    reject_then_restore(
        pawn.address(), gClient + dolly::reviewed::FollowTarget::FAMILIAR_CLONE_PAWN_VTABLE,
        gClient + profile.pawn_table, "Unreviewed native clone target was accepted");
    reject_then_restore(target_identity + 0x10, target_handle + 0x8000, target_handle,
                        "Recycled selected-target serial was accepted");
    reject_then_restore(observer_identity + 0x10, observer_handle + 0x8000, observer_handle,
                        "Recycled local-observer serial was accepted");
    reject_then_restore(pawn.address() + 0x10, std::uintptr_t(0), target_identity,
                        "Selected-target identity backlink was ignored");
    reject_then_restore(observer.address() + 0x10, std::uintptr_t(0), observer_identity,
                        "Local-observer identity backlink was ignored");
    reject_then_restore(camera.address() + 0xb0, target_handle + 1, target_handle,
                        "ThirdPerson camera followed a different target");
    reject_then_restore(services.address() + 0x48, static_cast<unsigned char>(4),
                        static_cast<unsigned char>(2), "Non-chase observer mode was accepted");
    reject_then_restore(services.address() + 0x4c, target_handle + 1, target_handle,
                        "Services target did not match the camera target");
    reject_then_restore(gClient + profile.manager + 0x38, static_cast<unsigned char>(1),
                        static_cast<unsigned char>(0), "Blending camera manager was accepted");
    reject_then_restore(gClient + profile.manager + 0x28, pawn.address(), camera.address(),
                        "Inactive ThirdPerson camera was accepted");
    reject_then_restore(services.address(), gClient + 0x1900, gClient + profile.services_table,
                        "Unknown observer services were accepted");
    put(services.address(), gClient + profile.observer_base);
    require(target_valid(), "Reviewed base observer services were rejected");
    put(services.address(), gClient + profile.services_table);

    constexpr char cvar_name[] = "citadel_camera_overrides_enabled";
    std::memcpy(reinterpret_cast<void*>(cvar.address() + 0x100), cvar_name, sizeof(cvar_name));
    put(cvar.address(), cvar.address() + 0x100);
    put(cvar.address() + 0x28, std::uint16_t(0));
    put(cvar.address() + 0x30, std::uint64_t(0x80080));
    put(cvar.address() + 0x58, static_cast<unsigned char>(1));
    put(gClient + profile.rig, CvarRef{1, cvar.address()});
    auto config = std::make_shared<dolly::EditorFollowConfig>();
    std::memcpy(config->magic, "DLYFOLL1", 8);
    config->sequence = 2;
    config->abi = 1;
    config->flags = 3;
    std::atomic_store(&dolly::gFollowConfig,
                      std::shared_ptr<const dolly::EditorFollowConfig>(config));
    gFollowAnchorInstalled = true;
    gEditor = GetCurrentProcess();
    dolly::gConnected = true;
    gHeartbeatTime = now_seconds();
    gWorkerError = 0;
    f.command(Mode::Release);
    const auto command = std::atomic_load(&gCommand);
    const auto session_valid = [&] {
        dolly::EditorFollowConfig observed{};
        std::shared_ptr<const Command> selected;
        return follow_anchor_session(observed, selected) && selected == command;
    };
    require(session_valid(), "Owned active Follow session was rejected");
    config->flags = 1;
    require(!session_valid(), "Inactive or cancelled Follow retained correction ownership");
    config->flags = 7;
    require(session_valid(), "Pending restoration data invalidated active Follow ownership");
    config->flags = 3;
    dolly::gConnected = false;
    require(!session_valid(), "Disconnected editor retained Follow correction ownership");
    dolly::gConnected = true;
    gHeartbeatTime = now_seconds() - 3;
    require(!session_valid(), "Expired editor lease retained Follow correction ownership");
    gHeartbeatTime = now_seconds();
    gWorkerError = 1;
    require(!session_valid(), "Faulted worker retained Follow correction ownership");
    gWorkerError = 0;
    gEditor = nullptr;
    require(!session_valid(), "Missing editor process retained Follow correction ownership");
    gEditor = GetCurrentProcess();
    seeking = true;
    require(!session_valid(), "Seeking replay retained Follow correction ownership");
    seeking = false;
    std::snprintf(demo_name, sizeof(demo_name), "replays/other-demo.dem");
    require(!session_valid(), "Changed replay retained Follow correction ownership");
    std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke.dem");
    f.command(Mode::Hold);
    dolly::EditorFollowConfig rejected_config{};
    std::shared_ptr<const Command> rejected_command;
    require(!follow_anchor_session(rejected_config, rejected_command),
            "Non-release camera command retained Follow correction ownership");
    // Exercise the actual session gate in each game-owned POV clock mode.
    const auto pov_valid = [&] {
        dolly::EditorFollowConfig observed{};
        std::shared_ptr<const Command> selected;
        const auto current = std::atomic_load(&gCommand);
        return follow_anchor_session(observed, selected) && selected == current;
    };
    for (const auto mode : {Mode::Hold, Mode::Play, Mode::HoldCurrent}) {
        f.command(mode, 0, kGamePov);
        require(pov_valid(), "Game-owned POV clock lost Follow anchor correction");
        const auto pov_command = std::atomic_load(&gCommand);
        config->flags = 1;
        require(!pov_valid(), "POV clock bypassed inactive Follow ownership");
        config->flags = 3;
        gHeartbeatTime = now_seconds() - 3;
        require(!pov_valid(), "POV clock bypassed the editor lease");
        gHeartbeatTime = now_seconds();
        gWorkerError = 1;
        require(!pov_valid(), "Faulted POV worker retained correction ownership");
        gWorkerError = 0;
        seeking = true;
        require(!pov_valid(), "Seeking POV replay retained correction ownership");
        seeking = false;
        std::snprintf(demo_name, sizeof(demo_name), "replays/other-demo.dem");
        require(!pov_valid(), "Changed POV replay retained correction ownership");
        std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke.dem");
        put(cvar.address() + 0x58, static_cast<unsigned char>(0));
        require(!pov_valid(), "POV clock bypassed disabled overrides");
        put(cvar.address() + 0x58, static_cast<unsigned char>(1));
        for (const auto flags : {0u, kAspect, kGamePov | kAspect, kGamePov | kFrozen}) {
            f.command(mode, 0, flags);
            require(!pov_valid(), "Camera-writing command gained POV correction ownership");
        }
        auto invalid = std::make_shared<Command>(*pov_command);
        invalid->manual = true;
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(invalid));
        require(!pov_valid(), "Manual POV command retained correction ownership");
        invalid = std::make_shared<Command>(*pov_command);
        invalid->path.reset();
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(invalid));
        require(!pov_valid(), "Unprepared POV command retained correction ownership");
        std::atomic_store(&gCommand, pov_command);
        require(pov_valid(), "Valid POV clock failed to recover after refusals");
    }
    f.command(Mode::Manual, 0, kGamePov);
    require(!pov_valid(), "Manual mode gained POV correction ownership");
    std::atomic_store(&gCommand, command);
    put(cvar.address() + 0x58, static_cast<unsigned char>(0));
    require(!session_valid(), "Disabled camera overrides retained correction ownership");
    put(cvar.address() + 0x58, static_cast<unsigned char>(1));
    require(session_valid(), "Restored Follow session did not recover its valid baseline");
    gFollowAnchorProfile = saved_profile;
    gFollowAnchorInstalled = saved_installed;
    gEditor = saved_editor;
    std::atomic_store(&dolly::gFollowConfig, saved_follow);
    std::atomic_store(&gCommand, saved_command);
    dolly::gConnected = saved_connected;
    gHeartbeatTime = saved_heartbeat;
    gWorkerError = saved_error;
}

void replay_identity() {
    require(same_demo("practice.session.01.dem", "replays/practice.session.01.dem"),
            "Full custom recording name was rejected");
    require(same_demo("practice.session.01.dem", "replays/practice.session.01"),
            "Optional final .dem suffix removed part of the custom recording name");
    require(same_demo("C:\\selected\\Haze aim.01.dem", "replays/HAZE AIM.01.DEM"),
            "Replay basename case or path separators changed identity");
    for (const char* other : {"", "practice.session", "practice.session.01.info",
                              "practice.session.01.dem.info", "practice.session.02.dem"})
        require(!same_demo("practice.session.01.dem", other),
                "Native replay guard accepted an incomplete or different recording name");
    require(!same_demo("practice.session.01", "practice.session.01"),
            "Selected replay must identify a .dem file");
}

void atomic_exports() {
    static_assert(sizeof(LONG) == 4 && sizeof(LONG64) == 8,
                  "Atomic export widths must match the bridge wire format");
    alignas(8) volatile LONG word = 7;
    const LONG high32 = std::numeric_limits<LONG>::min();
    require(DollyAtomicExchange32(&word, high32) == 7 && word == high32,
            "32-bit atomic exchange lost its return value or sign bit");
    require(DollyAtomicCompareExchange32(&word, 9, 7) == high32 && word == high32,
            "Mismatched atomic compare-exchange changed memory or truncated the sign bit");
    require(DollyAtomicCompareExchange32(&word, -1, high32) == high32 && word == -1,
            "Matched atomic compare-exchange did not preserve signed high bits");
    require(DollyAtomicCompareExchange32(&word, 0, 0) == -1 && word == -1,
            "Atomic sequence read did not return the complete 32-bit value");
    require(DollyAtomicExchange32(&word, 0) == -1 && word == 0,
            "32-bit atomic exchange must return the previous value");

    alignas(8) volatile LONG64 heartbeat = 0;
    const LONG64 high64 = std::numeric_limits<LONG64>::min();
    const LONG64 wide64 = 0x1234567887654321LL;
    require(DollyAtomicExchange64(&heartbeat, high64) == 0 && heartbeat == high64,
            "64-bit atomic exchange lost the sign bit");
    require(DollyAtomicExchange64(&heartbeat, wide64) == high64 && heartbeat == wide64,
            "64-bit atomic exchange truncated a heartbeat or its previous value");
    require(DollyAtomicExchange64(&heartbeat, -1) == wide64 && heartbeat == -1,
            "64-bit atomic exchange did not preserve all high bits");
    require(DollyAtomicExchange64(&heartbeat, 0) == -1 && heartbeat == 0,
            "64-bit atomic exchange must return the previous signed value");
}

// Test the production typed effect binding and lifecycle with synthetic convars.
Allocation* effect_data = nullptr;
bool reject_effect_write = false;
unsigned vector_effect_writes = 0;
void* __fastcall find_effect(void*, std::uint64_t* out, const char* name, int) {
    *out = 0xffffffff;
    for (unsigned i = 0; i < kEffects.size(); ++i)
        if (!std::strcmp(name, kEffects[i].name))
            *out = i;
    return out;
}
std::uintptr_t __fastcall get_effect(void*, std::uint64_t id) {
    return id < kEffects.size() ? effect_data->address() + id * 0x100 : 0;
}
void __fastcall set_effect(CvarRef* ref, int slot, const void* value, void* current,
                           void* context) {
    require(slot == 0 && context == nullptr, "Incorrect verified setter call arguments");
    require(current == reinterpret_cast<void*>(ref->data + 0x58),
            "Setter used an incorrect value slot");
    if (reject_effect_write)
        return;
    if (kEffects[ref->id].components == 4)
        ++vector_effect_writes;
    std::memcpy(current, value, kEffects[ref->id].type == 0 ? 1 : 4 * kEffects[ref->id].components);
}
void effect_checks(Fixture& f) {
    Allocation values{4096};
    effect_data = &values;
    gCvar = 1;
    gFindCvar = find_effect;
    gGetCvarData = get_effect;
    gSetCvar = set_effect;
    for (unsigned i = 0; i < kEffects.size(); ++i) {
        auto at = values.address() + i * 0x100;
        put(at, reinterpret_cast<std::uintptr_t>(kEffects[i].name));
        put(at + 0x28, static_cast<std::uint16_t>(kEffects[i].type));
        put(at + 0x30, std::uint64_t(8));
        if (kEffects[i].type == 7)
            put(at + 0x58, 600.0f);
    }
    auto shot = std::make_shared<NativeShot>();
    shot->camera = *f.path;
    EffectTrack focus;
    focus.id = 1;
    focus.first = 100;
    focus.last = 900;
    focus.last_time = 1;
    focus.segments.push_back({0, 1, 1, 1, 100, 900, 0, 0});
    EffectTrack enabled;
    enabled.id = 0;
    enabled.first = 0;
    enabled.last = 1;
    enabled.last_time = .5;
    enabled.segments.push_back({0, .5, 0, 1, 0, 1, 0, 0});
    EffectTrack mode = enabled;
    mode.id = 4;
    mode.last = 2;
    mode.segments[0].right = 2;
    shot->effects = {focus, enabled, mode};
    auto command = [&](Mode mode) {
        f.command(mode);
        auto c = std::make_shared<Command>(*std::atomic_load(&gCommand));
        c->shot = shot;
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(c));
    };
    f.fresh_hold();
    command(Mode::Hold);
    auto status = f.frame();
    require(status.effect_count == 3 && !status.error, "Native effects did not arm");
    double value = 0;
    require(gEffects.bindings[0].read(value) && value == 100,
            "First effect not applied before resume");
    command(Mode::Play);
    f.frame();
    paused = false;
    for (int n = 1; n <= 8; ++n) {
        f.clock(64 + n / 1024.0f, 4096);
        status = f.frame();
        require(status.effect_phase == status.phase && !status.effect_error,
                "Effect and camera phases differ");
        require(gEffects.bindings[0].read(value), "Effect readback failed");
        close_to(value, 100 + 800 * status.phase,
                 "Effect stepped instead of following fractional view time");
    }
    for (int n = 1; n <= 4; ++n) {
        f.clock(64 + n / 8.0f, 4096 + n * 8);
        status = f.frame();
        require(!status.error, "DOF progression failed");
    }
    require(gEffects.bindings[1].read(value) && value == 1, "Boolean DOF step did not apply");
    require(gEffects.bindings[2].read(value) && value == 2, "Integer DOF mode did not apply");
    command(Mode::HoldCurrent);
    status = f.frame();
    double held = status.effect_phase;
    f.clock(64.125f, 4104);
    status = f.frame();
    close_to(status.effect_phase, held, "Held effect advanced");
    // Manual reframing must retain the currently selected effect phase.
    paused = true;
    f.manual();
    status = f.frame();
    require(!status.error && status.effect_count == 3, "Entering manual flight lost selected DOF");
    close_to(status.effect_phase, held, "Manual flight changed the selected effect phase");
    command(Mode::Release);
    status = f.frame();
    float restored = 0;
    read_value(values.address() + 0x100 + 0x58, restored);
    require(status.effect_count == 0 && restored == 600, "Stop did not restore original effect");
    // Force a setter/readback mismatch: stop before camera write, then restore.
    f.fresh_hold();
    command(Mode::Hold);
    reject_effect_write = true;
    status = f.frame();
    require(status.error == 35, "A refused effect setter was reported as synchronized");
    f.unchanged();
    reject_effect_write = false;
    command(Mode::Release);
    f.frame();
    // Lease expiry must restore settings without an editor console round trip.
    f.fresh_hold();
    command(Mode::Hold);
    f.frame();
    gHeartbeatTime = now_seconds() - 3;
    status = f.frame(0, false);
    read_value(values.address() + 0x100 + 0x58, restored);
    require(status.error == 10 && restored == 600 && status.effect_count == 0,
            "Editor loss did not restore effects");
    f.command(Mode::Release);
    f.frame();
    // Invalid binding is refused before any shot cvar changes.
    put(values.address() + 0x100 + 0x28, std::uint16_t(3));
    f.fresh_hold();
    command(Mode::Hold);
    status = f.frame();
    require(status.error == 35 && status.effect_count == 0, "Wrong native type was accepted");
    f.unchanged();
    put(values.address() + 0x100 + 0x28, std::uint16_t(7));
    f.command(Mode::Release);
    f.frame();
    // Failed restoration must not acknowledge a successful release.
    f.fresh_hold();
    command(Mode::Hold);
    f.frame();
    reject_effect_write = true;
    command(Mode::Release);
    status = f.frame();
    require(status.error == 36 && status.effect_count == 3, "Failed restore was acknowledged");
    reject_effect_write = false;
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Stopped) && status.effect_count == 0,
            "Restore retry did not clear ownership");
    // Explicit restore overrides survive native completion/release.
    shot->effects[0].restore_override = true;
    shot->effects[0].restore = 123;
    f.fresh_hold();
    command(Mode::Hold);
    f.frame();
    command(Mode::Release);
    status = f.frame();
    read_value(values.address() + 0x100 + 0x58, restored);
    require(restored == 123 && !status.error, "Restore override was lost");
    // Four authored lanes must become one typed Vector4 write and restore.
    auto vector_shot = std::make_shared<NativeShot>();
    vector_shot->camera = *f.path;
    const std::array<float, 4> original{{-100, 0, 180, 2000}};
    put(values.address() + 8 * 0x100 + 0x58, original);
    for (unsigned component = 0; component < 4; ++component) {
        EffectTrack lane;
        lane.id = 8;
        lane.component = component;
        lane.last_time = 1;
        lane.first = 10 + component;
        lane.last = 20 + component;
        lane.segments.push_back({0, 1, 1, 1, lane.first, lane.last, 0, 0});
        vector_shot->effects.push_back(lane);
    }
    gExtendedCvarSupported = false;
    require(!gEffects.apply(vector_shot, 0) && gEffects.count == 0,
            "Unverified Vector4 profile was accepted");
    gExtendedCvarSupported = true;
    vector_effect_writes = 0;
    require(gEffects.apply(vector_shot, .5) && gEffects.count == 1 && vector_effect_writes == 1,
            "Vector was not written once as a complete value");
    EffectValue vector_value{};
    require(gEffects.bindings[0].read(vector_value), "Vector readback failed");
    for (unsigned component = 0; component < 4; ++component)
        require(vector_value[component] == 15 + component, "Vector lane order changed");
    require(gEffects.apply(vector_shot, .5) && vector_effect_writes == 1,
            "Unchanged vector was rewritten");
    require(gEffects.restore() && vector_effect_writes == 2,
            "Vector original was not restored in one call");
    std::array<float, 4> restored_vector{};
    read_value(values.address() + 8 * 0x100 + 0x58, restored_vector);
    require(restored_vector == original, "Vector restore lost a component");
    gExtendedCvarSupported = false;
    gCvar = 0;
    gFindCvar = nullptr;
    gGetCvarData = nullptr;
    gSetCvar = nullptr;
    effect_data = nullptr;
}

std::uint64_t cursor_clip_calls = 0;
BOOL WINAPI observe_cursor_clip(const RECT*) {
    ++cursor_clip_calls;
    return TRUE;
}

void editor_cursor_startup_checks() {
    const auto saved_window = dolly::gWindow.load();
    const auto saved_clip = dolly::gClipCursor;
    const auto saved_owner = dolly::gOwner.load();
    const auto saved_view = editor_snapshot();
    dolly::gWindow = nullptr;
    dolly::gFocused = false;
    dolly::gClipCursor = observe_cursor_clip;
    editor_update_view(false, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Flight);
    editor_worker_tick(nullptr, true);
    const auto initial_calls = cursor_clip_calls;

    // First view readiness is a later callback than the owner's Flight request.
    // The callback may only publish a refresh; the worker owns OS cursor calls.
    dolly::gKeys['W'] = true;
    dolly::gBlocked['W'] = false;
    dolly::gMouseX = 17;
    dolly::gMouseY = -9;
    editor_update_view(true, true, true, saved_view.pose);
    require(cursor_clip_calls == initial_calls,
            "View readiness performed an OS cursor operation in the render callback");
    editor_worker_tick(nullptr, true);
    require(cursor_clip_calls == initial_calls + 1,
            "First ready view did not reconcile Flight cursor ownership");
    require(dolly::key_down('W') && dolly::gMouseX == 17 && dolly::gMouseY == -9,
            "Readiness reconciliation reset active camera movement");
    editor_worker_tick(nullptr, true);
    editor_update_view(true, true, true, saved_view.pose);
    editor_worker_tick(nullptr, true);
    require(cursor_clip_calls == initial_calls + 1,
            "Unchanged ready views repeatedly changed OS cursor ownership");
    editor_update_view(false, true, false, saved_view.pose);
    editor_worker_tick(nullptr, true);
    require(cursor_clip_calls == initial_calls + 2,
            "Losing view readiness did not release cursor ownership");

    dolly::gKeys['W'] = false;
    dolly::gMouseX = 0;
    dolly::gMouseY = 0;
    dolly::gClipCursor = saved_clip;
    dolly::gWindow = saved_window;
    editor_set_owner(saved_owner);
    editor_update_view(saved_view.ready, saved_view.paused, saved_view.manual_active,
                       saved_view.pose, saved_view.phase, saved_view.tick,
                       saved_view.horizontal_fov, saved_view.view_width, saved_view.view_height);
}

void editor_input_checks() {
    // Feed the real key-transition handler after its callers' focus checks.
    // No foreground window or OS input injection is needed for this fixture.
    auto saved = std::atomic_load(&dolly::gConfig);
    const auto saved_view = editor_snapshot();
    auto configured = std::make_shared<EditorConfig>(*saved);
    configured->playback_flags = 0;
    configured->bindings[0] = {'K', 0};
    configured->bindings[2] = {'P', 0};
    configured->bindings[9] = {VK_F8, 0};
    configured->bindings[10] = {VK_F9, 0};
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    dolly::gWindow = nullptr;
    dolly::gTextInput = false;
    for (unsigned i = 0; i < 256; ++i) {
        dolly::gKeys[i] = false;
        dolly::gBlocked[i] = false;
        dolly::gFocusBlocked[i] = false;
    }
    dolly::gAcknowledged = dolly::gLastEvent;
    editor_update_view(true, true, true, saved_view.pose);
    editor_set_owner(EditorOwner::Flight);
    auto event_count = [] { return dolly::gLastEvent; };
    auto console_event = [](double value) {
        const auto& event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
        require(event.action == std::uint32_t(EditorAction::Console) && event.value == value,
                "Console shortcut queued the wrong requested visibility");
    };

    dolly::gSpeed = 320;
    const auto before_speed = event_count();
    dolly::key_event(VK_OEM_PLUS, true);
    require(dolly::gSpeed > 320 && event_count() == before_speed + 1,
            "Plus did not increase camera movement speed");
    const auto faster = dolly::gSpeed.load();
    dolly::key_event(VK_OEM_PLUS, true);
    require(dolly::gSpeed == faster, "Held plus repeatedly queued camera speed edits");
    dolly::key_event(VK_OEM_PLUS, false);
    dolly::key_event(VK_OEM_MINUS, true);
    require(std::abs(dolly::gSpeed.load() - 320) < .001,
            "Minus did not restore the preceding camera speed");
    dolly::key_event(VK_OEM_MINUS, false);
    editor_set_owner(EditorOwner::Panel);
    dolly::gTextInput = true;
    dolly::key_event(VK_OEM_MINUS, true);
    require(dolly::gSpeed == 320, "Typing minus changed camera movement speed");
    dolly::key_event(VK_OEM_MINUS, false);
    dolly::gTextInput = false;
    editor_set_owner(EditorOwner::Console);
    dolly::key_event(VK_ADD, true);
    require(dolly::gSpeed == 320, "Console plus changed camera speed");
    dolly::key_event(VK_ADD, false);
    editor_set_owner(EditorOwner::Flight);
    dolly::key_event(VK_ADD, true);
    require(dolly::gSpeed > 320, "Numpad plus did not increase camera speed");
    dolly::key_event(VK_ADD, false);
    dolly::key_event(VK_SUBTRACT, true);
    require(std::abs(dolly::gSpeed.load() - 320) < .001, "Numpad minus did not decrease camera speed");
    dolly::key_event(VK_SUBTRACT, false);
    dolly::key_event(VK_CONTROL, true);
    dolly::key_event(VK_OEM_PLUS, true);
    require(dolly::gSpeed == 320, "Control-plus changed camera movement speed");
    dolly::key_event(VK_OEM_PLUS, false);
    dolly::key_event(VK_CONTROL, false);
    dolly::gAcknowledged = dolly::gLastEvent;
    const auto pause_key = configured->bindings[2].vk;
    const auto capture_key = configured->bindings[0].vk;
    editor_update_view(true,false,true,saved_view.pose);
    dolly::key_event('W',true);
    dolly::key_event(VK_SHIFT,true);
    auto before_toggle = event_count();
    dolly::key_event(pause_key,true);
    require(event_count()==before_toggle+1 &&
            dolly::gEvents[(dolly::gLastEvent-1)%kEditorEventCount].action==std::uint32_t(EditorAction::PlayPause),
            "Pause/resume did not work while moving with Shift");
    dolly::key_event(pause_key,false);
    dolly::key_event(capture_key,true);
    require(event_count()==before_toggle+2 &&
            dolly::gEvents[(dolly::gLastEvent-1)%kEditorEventCount].action==std::uint32_t(EditorAction::Capture),
            "Capture did not work while moving with Shift");
    dolly::key_event(capture_key,false);
    require(!dolly::gEvents[(dolly::gLastEvent-1)%kEditorEventCount].paused,
            "Capture event lost the playing replay state");
    dolly::key_event(VK_SHIFT,false);
    dolly::key_event(VK_MENU,true);
    before_toggle = event_count();
    dolly::key_event(pause_key,true);
    require(event_count()==before_toggle+1, "Pause/resume failed while holding the slow-movement modifier");
    dolly::key_event(pause_key,false);
    dolly::key_event(VK_MENU,false);
    dolly::key_event('W',false);
    editor_update_view(true,true,true,saved_view.pose);
    dolly::gAcknowledged = dolly::gLastEvent;
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel, "F8 did not open the panel");
    // Raw + legacy copies and held-key repeat must not toggle the panel back.
    dolly::key_event(VK_F8, true);
    dolly::key_event('X', true);
    dolly::key_event('X', false);
    dolly::unblock_released(); // OS key polling cannot erase a recorded shortcut edge.
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel, "Repeated/duplicate F8 flashed the panel");
    dolly::key_event(VK_F8, false);
    dolly::key_event(VK_F8, false);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight, "A new F8 press did not close the panel");
    dolly::key_event(VK_F8, false);

    // After a path endpoint or Stop, F8 must actually re-arm the manual
    // camera. Changing only the input owner leaves every movement key dead.
    editor_update_view(true, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Panel);

    const auto before_return = event_count();
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel && event_count() == before_return + 1,
            "F8 claimed flight input before re-arming a held camera");
    require(dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount].action ==
                std::uint32_t(EditorAction::Flight),
            "F8 did not request the acknowledged camera flight handoff");
    dolly::key_event(VK_F8, true);
    require(event_count() == before_return + 1, "Held F8 repeatedly requested flight");
    editor_update_view(true, true, true, saved_view.pose);
    editor_set_owner(EditorOwner::Flight); // controller/render acknowledgement
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight && event_count() == before_return + 1,
            "Flight acknowledgement reopened the panel for the same F8 press");
    dolly::key_event(VK_F8, false);

    // F8 remains a panel visibility control during a running shot; it must
    // never send a manual-flight command that stops/replaces that shot.
    configured = std::make_shared<EditorConfig>(*configured);
    configured->playback_flags = 1;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_update_view(true, true, false, saved_view.pose); // includes frozen previews
    editor_set_owner(EditorOwner::Panel);
    const auto during_shot = event_count();
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight && event_count() == during_shot,
            "F8 interrupted a playing shot with a manual-flight request");
    dolly::key_event(VK_F8, false);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel && event_count() == during_shot,
            "F8 could not reopen the workspace without interrupting a playing shot");
    dolly::key_event(VK_F8, false);
    editor_update_view(false, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight && event_count() == during_shot,
            "F8 interrupted a known playing shot when its view was temporarily unavailable");
    dolly::key_event(VK_F8, false);
    configured = std::make_shared<EditorConfig>(*configured);
    configured->playback_flags = 2;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel && event_count() == during_shot,
            "F8 changed camera ownership during an unfinished operation");
    dolly::key_event(VK_F8, false);
    configured = std::make_shared<EditorConfig>(*configured);
    configured->playback_flags = 0;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_update_view(false, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel && event_count() == during_shot,
            "F8 hid recovery controls before a usable camera view was available");
    dolly::key_event(VK_F8, false);
    editor_update_view(true, true, true, saved_view.pose);
    editor_set_owner(EditorOwner::Flight);

    const auto before_console = event_count();
    dolly::key_event(VK_F7, true);
    require(dolly::gOwner == EditorOwner::Console && event_count() == before_console + 1,
            "F7 did not request the console once");
    console_event(1);
    dolly::key_event(VK_F7, true);
    dolly::key_event('X', true);
    dolly::key_event('X', false);
    dolly::unblock_released();
    dolly::key_event(VK_F7, true);
    require(event_count() == before_console + 1, "Held F7 queued duplicate console toggles");
    dolly::key_event(VK_F7, false);
    dolly::key_event(VK_F7, false);
    dolly::key_event(VK_F7, true);
    require(event_count() == before_console + 2 && dolly::gOwner == EditorOwner::Console,
            "Console input was released before hideconsole confirmation");
    console_event(0);
    editor_set_owner(EditorOwner::Panel); // asynchronous hideconsole confirmation
    dolly::key_event(VK_F7, true);
    require(event_count() == before_console + 2 && dolly::gOwner == EditorOwner::Panel,
            "Controller confirmation converted a held F7 into another toggle");
    dolly::key_event(VK_F7, false);

    editor_set_owner(EditorOwner::Console);
    dolly::key_event(VK_F8, true);
    {
        const auto& event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
        require(event.action == std::uint32_t(EditorAction::Panel) && event.value == 1,
                "F8 from console must return to Dolly rather than the underlying game UI");
    }
    const auto close_requested = event_count();
    require(dolly::gOwner == EditorOwner::Console, "F8 bypassed console close confirmation");
    dolly::key_event(VK_F8, true);
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(event_count() == close_requested && dolly::gOwner == EditorOwner::Panel,
            "F8 close confirmation flashed the panel");
    dolly::key_event(VK_F8, false);

    // F9 returns input only after the controller has hidden the game's replay
    // HUD and the native view has acknowledged flight. Alt-tab preserves owner.
    editor_set_owner(EditorOwner::Flight);
    dolly::key_event(VK_F9, true);
    dolly::key_event(VK_F9, false);
    require(dolly::gOwner == EditorOwner::GameUI, "F9 did not suspend editor input");
    auto unfocused = editor_snapshot();
    require(!unfocused.focused && unfocused.owner == EditorOwner::GameUI,
            "Alt-tab erased game UI ownership from native status");
    dolly::key_event(VK_F9, true);
    dolly::key_event(VK_F9, false);
    require(dolly::gOwner == EditorOwner::GameUI, "F9 released input before flight confirmation");
    const auto& return_event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
    require(return_event.action == std::uint32_t(EditorAction::GameUI) && return_event.value == 0,
            "F9 did not request explicit game UI close");
    editor_set_owner(EditorOwner::Flight);
    const auto after_game_ui = event_count();

    // A configurable text key must remain available for typing in the console.
    configured = std::make_shared<EditorConfig>(*configured);
    configured->bindings[9] = {'H', 0};
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_set_owner(EditorOwner::Console);
    dolly::key_event('H', true);
    dolly::key_event('H', false);
    require(event_count() == after_game_ui, "Custom text binding stole console typing");
    dolly::key_event(VK_ESCAPE, true);
    console_event(0);
    const auto escape_requested = event_count();
    dolly::key_event(VK_ESCAPE, true);
    require(event_count() == escape_requested, "Held Escape queued duplicate close requests");
    dolly::key_event(VK_ESCAPE, false);

    // Escape closes the Dolly panel without a camera command, but an active
    // text edit keeps Escape for ImGui instead.
    editor_set_owner(EditorOwner::Panel);
    const auto panel_escape = event_count();
    dolly::key_event(VK_ESCAPE, true);
    require(dolly::gOwner == EditorOwner::Flight && event_count() == panel_escape,
            "Escape did not close the Dolly panel");
    dolly::key_event(VK_ESCAPE, false);
    editor_set_owner(EditorOwner::Panel);
    dolly::gTextInput = true;
    dolly::key_event(VK_ESCAPE, true);
    require(dolly::gOwner == EditorOwner::Panel, "Escape closed the panel during a text edit");
    dolly::key_event(VK_ESCAPE, false);
    dolly::gTextInput = false;

    editor_set_owner(EditorOwner::Flight);
    // A movement key physically held during this offline test must not
    // masquerade as a blocked post-transition press.
    dolly::gBlocked['W'] = false;
    dolly::gFocusBlocked['W'] = false;
    dolly::key_event('W', true);
    require(dolly::key_down('W'), "Fresh flight movement was not recorded");
    editor_set_owner(EditorOwner::Flight);
    require(dolly::key_down('W'), "Reasserting the same owner interrupted movement");
    editor_set_owner(EditorOwner::Panel);
    editor_set_owner(EditorOwner::Flight);
    dolly::key_event('W', true);
    require(!dolly::key_down('W'), "Held movement leaked across an ownership transition");
    dolly::key_event('W', false);
    dolly::key_event('W', true);
    require(dolly::key_down('W'), "Movement did not resume after release and a fresh press");
    dolly::key_event('W', false);
    std::atomic_store(&dolly::gConfig, saved);
    editor_update_view(saved_view.ready, saved_view.paused, saved_view.manual_active,
                       saved_view.pose, saved_view.phase, saved_view.tick,
                       saved_view.horizontal_fov, saved_view.view_width, saved_view.view_height);
    dolly::gAcknowledged = dolly::gLastEvent;
}

void editor_framing_checks() {
    const auto saved = std::atomic_load(&dolly::gConfig);
    const auto view = editor_snapshot();
    auto config = std::make_shared<EditorConfig>(*saved);
    config->camera_count = 2;
    config->selected_camera = 1;
    config->playback_flags = kEditorPauseAfterCapture;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(config));
    dolly::gAcknowledged = dolly::gLastEvent;
    CameraPose pose{1, 2, 3, 4, 5, 6, 16.0 / 9};
    editor_update_view(true, true, true, pose);
    require(editor_snapshot().pause_after_capture && !editor_snapshot().playing &&
                !editor_snapshot().busy,
            "Capture pause preference changes playback state");
    // Optional desktop DOF publication is a separate bounded mapping block.
    std::vector<unsigned char> dof_memory(kMappingBytes);
    EditorDofConfig dof{};
    std::memcpy(dof.magic, "DLYDOF01", 8);
    dof.sequence = 2;
    dof.abi = 1;
    dof.enabled = 1;
    dof.values[0] = 1;
    dof.values[4] = 180;
    std::memcpy(dof_memory.data() + kEditorDofOffset, &dof, sizeof(dof));
    editor_worker_tick(dof_memory.data(), true);
    require(editor_snapshot().dof_available && editor_snapshot().dof[4] == 180,
            "Desktop DOF settings did not reach the editor snapshot");
    editor_set_owner(EditorOwner::Panel);
    {
        EditorAttachConfig attach{};
        std::memcpy(attach.magic, "DLYATTC1", 8);
        attach.sequence = 2;
        attach.abi = 2;
        attach.flags = 3;
        std::fill(std::begin(attach.offsets), std::end(attach.offsets), 8u);
        attach.handle = 11;
        attach.entity_id = 4;
        attach.point = 2;
        attach.model = 123;
        attach.bone_hash = attach_runtime::model_token("head");
        std::memcpy(attach.bone_name, "head", 4);
        attach.attached_keys = attach.key_count = 1;
        std::vector<unsigned char> attach_memory(kMappingBytes);
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        require(editor_snapshot().attach_point == 2 &&
                    std::strcmp(editor_snapshot().attach_bone, "head") == 0,
                "Bone preview publication lost the named source");
        require(editor_enqueue(EditorAction::AttachPreview, 1) &&
                    editor_enqueue(EditorAction::AttachSnap, 0),
                "Attach preview or snap was rejected by the action range");
        attach.flags = 7;
        attach.sequence = 4;
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        require(editor_snapshot().attach_preview && !editor_enqueue(EditorAction::AttachSnap, 0),
                "Attached preview accepted a free-camera snap");
        attach.flags = 3;
        attach.sequence = 6;
        attach.abi = 6;
        attach.hide = 7;
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        EditorAttachConfig rigid_config{};
        require(editor_attach_config(rigid_config) && rigid_config.abi == 6 && rigid_config.hide == 7,
                "Rigid orientation was lost from the attachment editor config");
        const auto saved_camera_list = std::atomic_load(&dolly::gCameraList);
        auto aim_list = std::make_shared<EditorCameraList>();
        aim_list->revision = 37;
        aim_list->total = config->camera_count;
        std::atomic_store(&dolly::gCameraList, std::shared_ptr<const EditorCameraList>(aim_list));
        attach.hide = 15;
        attach.sequence = 8;
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        require(editor_snapshot().attach_lock_vertical_aim,
                "The version-six level-aim option did not reach the editor");
        attach.abi = 5;
        attach.sequence = 10;
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        require(editor_attach_config(rigid_config) && rigid_config.sequence == 8,
                "An older editor ABI accepted the new option");
        CameraPose aim_request{};
        aim_request[0] = 37;
        aim_request[1] = config->selected_camera;
        dolly::gAcknowledged = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::SetAttachVerticalAimLock, 1, &aim_request),
                "A valid level-aim change was rejected");
        const auto& aim_event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
        require(aim_event.action == std::uint32_t(EditorAction::SetAttachVerticalAimLock) &&
                    aim_event.pose[0] == 37 && aim_event.pose[1] == config->selected_camera,
                "Level-aim event lost the selected-camera identity");
        aim_request[0] = 36;
        require(!editor_enqueue(EditorAction::SetAttachVerticalAimLock, 1, &aim_request),
                "A stale level-aim change was accepted");
        aim_request[0] = 37;
        aim_request[1] = 0;
        require(!editor_enqueue(EditorAction::SetAttachVerticalAimLock, 1, &aim_request),
                "A level-aim change for another camera was accepted");
        aim_request[1] = config->selected_camera;
        attach.abi = 6;
        attach.flags = 7;
        attach.sequence = 12;
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        require(!editor_enqueue(EditorAction::SetAttachVerticalAimLock, 1, &aim_request),
                "Attached preview accepted a level-aim change");
        std::atomic_store(&dolly::gCameraList, saved_camera_list);
        attach.abi = 2;
        attach.hide = 0;
        attach.flags = 3;
        attach.sequence = 16;
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        const auto saved_follow = std::atomic_load(&dolly::gFollowConfig);
        auto active_follow = std::make_shared<EditorFollowConfig>();
        active_follow->flags = 3;
        std::atomic_store(&dolly::gFollowConfig,
                          std::shared_ptr<const EditorFollowConfig>(active_follow));
        editor_update_view(true, true, false, pose);
        require(editor_enqueue(EditorAction::OpenBonePicker, 0) &&
                    editor_enqueue(EditorAction::AttachPreview, 1),
                "Paused Follow cannot request the normal bone-camera handoff");
        require(!editor_enqueue(EditorAction::AttachSnap, 0),
                "Follow must not snap offsets without native camera ownership");
        editor_update_view(true, false, false, pose);
        require(!editor_enqueue(EditorAction::OpenBonePicker, 0) &&
                    !editor_enqueue(EditorAction::AttachPreview, 1),
                "Playing Follow bypassed the paused handoff gate");
        std::atomic_store(&dolly::gFollowConfig, saved_follow);
        editor_update_view(true, true, false, pose);
        require(!editor_enqueue(EditorAction::OpenBonePicker, 0) &&
                    !editor_enqueue(EditorAction::AttachPreview, 1),
                "Idle camera acquired a Follow-only handoff exception");
        editor_update_view(true, true, true, pose);
        auto saved_roster = std::atomic_load(&dolly::gRoster);
        auto roster = std::make_shared<EditorRoster>();
        roster->count = 1;
        std::atomic_store(&dolly::gRoster, std::shared_ptr<const EditorRoster>(roster));
        const auto saved_count = config->camera_count;
        config->camera_count = 0;
        require(editor_enqueue(EditorAction::SetAttachTarget, 0),
                "First player selection still required a captured camera");
        require(!editor_enqueue(EditorAction::SetAttachTarget, 1) &&
                    !editor_enqueue(EditorAction::SetAttachPoint, 0),
                "Empty shot bypassed roster or selected-camera validation");
        config->camera_count = saved_count;
        std::atomic_store(&dolly::gRoster, saved_roster);
        // Exercise the exact versioned payload emitted by the in-game bone combo.
        EditorBones bones{};
        std::memcpy(bones.magic, "DLYBONE1", 8);
        bones.abi = 1;
        bones.sequence = 2;
        bones.handle = attach.handle;
        bones.entity_index = attach.entity_id;
        bones.model = attach.model;
        bones.count = bones.total = 2;
        std::memcpy(bones.names[0], "head", 4);
        std::memcpy(bones.names[1], "hand_R", 6);
        std::memcpy(attach_memory.data() + kEditorBonesOffset, &bones, sizeof(bones));
        editor_worker_tick(attach_memory.data(), true);
        CameraPose bone_version{};
        bone_version[0] = bones.sequence;
        require(editor_enqueue(EditorAction::SetAttachBone, 1, &bone_version),
                "In-game bone picker rejected a current versioned selection");
        const auto& bone_event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
        require(bone_event.action == std::uint32_t(EditorAction::SetAttachBone) &&
                    bone_event.value == 1 && bone_event.pose[0] == bones.sequence,
                "Bone selection payload was not preserved");
        bone_version[0] = 4;
        require(!editor_enqueue(EditorAction::SetAttachBone, 1, &bone_version),
                "Stale bone selection was accepted");
        bone_version[0] = bones.sequence;
        require(!editor_enqueue(EditorAction::SetAttachBone, 2, &bone_version) &&
                    !editor_enqueue(EditorAction::SetAttachBone, .5, &bone_version) &&
                    !editor_enqueue(EditorAction::SetAttachBone, 1),
                "Invalid or unversioned bone selection was accepted");
        attach.sequence = 18;
        attach.bone_hash ^= 1;
        std::memcpy(attach_memory.data() + kEditorAttachOffset, &attach, sizeof(attach));
        editor_worker_tick(attach_memory.data(), true);
        EditorAttachConfig accepted{};
        require(editor_attach_config(accepted) && accepted.sequence == 16,
                "Mismatched bone name/hash replaced the valid preview");
        std::atomic_store(&dolly::gAttachConfig, std::shared_ptr<const EditorAttachConfig>{});
        dolly::gAcknowledged = dolly::gLastEvent;
    }
    require(editor_enqueue(EditorAction::SetDofRangeFarCrisp, 350),
            "Valid native DOF edit could not enter the event queue");
    require(!editor_enqueue(EditorAction::SetDofOverride, .5) &&
                !editor_enqueue(EditorAction::SetDofTilt, 1e39),
            "Invalid native DOF value entered the event queue");
    editor_set_owner(EditorOwner::Flight);
    require(!editor_enqueue(EditorAction::SetDofOverride, 1), "DOF edit bypassed panel ownership");
    dof.sequence = 4;
    dof.values[4] = std::numeric_limits<double>::quiet_NaN();
    std::memcpy(dof_memory.data() + kEditorDofOffset, &dof, sizeof(dof));
    editor_worker_tick(dof_memory.data(), true);
    require(editor_snapshot().dof[4] == 180, "Invalid publication replaced the valid DOF snapshot");
    dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA);
    close_to(pose[6], 1.6, "Wheel up did not zoom into the aspect framing curve");
    auto event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
    require(event.action == 40 && event.value == 1 && event.pose[0] == 1,
            "Wheel event did not retain the selected camera and exact pose");
    close_to(event.pose[6], .9, "Wheel event did not carry the scale factor");
    dolly::apply_framing_wheel(pose, *config, -WHEEL_DELTA);
    close_to(pose[6], 16.0 / 9, "Wheel down did not reverse wheel up");
    dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA / 2);
    dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA / 2);
    close_to(pose[6], 1.6, "Partial wheel detents were lost");
    dolly::apply_framing_wheel(pose, *config, 12000);
    close_to(pose[6], .5, "Wheel exceeded the minimum framing curve bound");
    dolly::apply_framing_wheel(pose, *config, -12000);
    close_to(pose[6], 4, "Wheel exceeded the maximum framing curve bound");
    const auto serial = dolly::gLastEvent;
    dolly::apply_framing_wheel(pose, *config, -WHEEL_DELTA);
    require(dolly::gLastEvent == serial, "Clamped wheel queued a redundant edit");
    for (unsigned i = 0; i < kEditorEventCount; ++i)
        editor_enqueue(EditorAction::Capture);
    dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA);
    close_to(pose[6], 4, "Full event queue left visible framing ahead of the saved curve");
    // A wheel burst must keep compounding from its own live value. The committed
    // curve republishes on the UI poll, so re-reading it for every event used to
    // recompute one target from a stale base and stall the zoom.
    dolly::gFramingWheelState = {};
    double burst = 1.8;
    burst = dolly::framing_wheel_step(burst, 1.8, 0, .9);
    close_to(burst, 1.62, "Wheel burst did not start from the published curve");
    burst = dolly::framing_wheel_step(burst, 1.8, 0, .9);
    close_to(burst, 1.458, "A stale published curve stalled the wheel burst");
    burst = dolly::framing_wheel_step(burst, 1.458, 0, .9);
    close_to(burst, 1.3122, "The wheel burst did not compound across updates");
    // Resting on a clamp, or pausing, then scrolling back must not rebase to
    // the selected camera's stored framing. Only a real camera, pose or curve
    // change re-anchors the burst.
    dolly::gFramingWheelState = {};
    double rest = dolly::framing_wheel_step(1.25, 1.25, 0, .9);
    close_to(rest, 1.125, "Wheel burst did not start from the published curve");
    rest = dolly::framing_wheel_step(rest, 1.25, 0, .9);
    close_to(rest, 1.0125, "A resting wheel did not keep compounding");
    rest = dolly::framing_wheel_step(rest, 1.0125, 0, .9);
    close_to(rest, .91125, "A parked commit did not compound on the wheel value");
    close_to(dolly::framing_wheel_step(rest, 1.25, 1, .9), 1.125,
             "A camera change did not re-anchor the wheel to the published curve");
    close_to(dolly::framing_wheel_step(1.125, 1.25, 1, .9), 1.0125,
             "A same-camera wheel did not keep compounding");
    close_to(dolly::framing_wheel_step(1.0125, 2.0, 1, .9), 1.8,
             "A desktop curve edit did not re-anchor the wheel");
    close_to(dolly::framing_wheel_step(1.6, 1.6, 1, .9), 1.44,
             "An outside pose change did not re-anchor the wheel");
    dolly::gAcknowledged = dolly::gLastEvent;
    editor_update_view(true, false, true, pose);
    dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA);
    close_to(pose[6], 4, "Wheel changed framing during replay playback");
    editor_update_view(true, true, true, pose);
    config->playback_flags = 1;
    dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA);
    close_to(pose[6], 4, "Wheel changed framing during shot playback");
    config->playback_flags = 0;
    config->camera_count = 0;
    config->selected_camera = 0;
    dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA);
    event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
    require(event.value == -1, "Uncaptured wheel zoom selected a nonexistent key");
    dolly::gFramingWheel = 120;
    editor_reset_motion();
    require(dolly::gFramingWheel == 0, "Wheel input leaked through an owner transition");
    dolly::gFramingWheel = 120;
    editor_integrate_flight(pose, 1);
    require(dolly::gFramingWheel == 0, "Wheel suspension backlog was not discarded");
    // Typed FOV must update the actual render pose while the panel owns input.
    editor_set_owner(EditorOwner::Panel);
    pose[6] = 1.8;
    editor_update_view(true, true, true, pose);
    dolly::gAcknowledged = dolly::gLastEvent;
    require(editor_request_framing_factor(.8), "Typed FOV request was rejected");
    const auto before_lens = dolly::gLastEvent;
    dolly::apply_panel_framing(pose, .016, true);
    close_to(pose[6], 1.44, "Typed FOV failed to change panel-owned live framing");
    require(dolly::gLastEvent == before_lens + 1, "Typed FOV must enqueue exactly one saved-key edit");
    dolly::apply_panel_framing(pose, .016, true);
    close_to(pose[6], 1.44, "Typed FOV factor was applied twice");
    editor_update_view(true, false, true, pose);
    require(!editor_request_framing_factor(.8), "Typed FOV accepted during playback");
    editor_update_view(true, true, true, pose);
    // Live level view: R levels the paused camera and returns the live framing
    // to the project's normal aspect without enqueueing any project edit.
    {
        std::vector<unsigned char> level_memory(kMappingBytes);
        EditorLevelViewConfig level{};
        std::memcpy(level.magic, "DLYLEVL1", 8);
        level.sequence = 2;
        level.abi = 1;
        level.flags = 1;
        level.reserved = std::uint32_t('R');
        level.standard_aspect = 16.0 / 9;
        std::memcpy(level_memory.data() + kEditorLevelViewOffset, &level, sizeof(level));
        editor_worker_tick(level_memory.data(), true);
        EditorLevelViewConfig published{};
        require(editor_level_view_config(published) && published.reserved == std::uint32_t('R'),
                "Level view binding did not publish");
        close_to(published.standard_aspect, 16.0 / 9,
                 "Level view normal framing did not publish");
        EditorLevelViewConfig rejected{};
        level.reserved = std::uint32_t('R') | 8u << 16;
        std::memcpy(level_memory.data() + kEditorLevelViewOffset, &level, sizeof(level));
        editor_worker_tick(level_memory.data(), true);
        require(editor_level_view_config(rejected) && rejected.sequence == 2,
                "Invalid level view modifiers replaced the published block");

        CameraPose live{1, 2, 3, 4, 5, 6, 1.8};
        editor_set_owner(EditorOwner::Panel);
        editor_update_view(true, true, true, live);
        dolly::gAcknowledged = dolly::gLastEvent;
        const auto idle_serial = dolly::gLastEvent;
        require(!editor_apply_level_view(live) && live[5] == 6 && live[6] == 1.8,
                "Level view applied without a pending request");
        // The key is swallowed from the game (reserved_input needs a real
        // foreground window, so this fixture drives the same transition).
        dolly::key_event('R', true);
        dolly::key_event('R', false);
        require(editor_apply_level_view(live) && live[5] == 0,
                "Level view key did not level the live camera");
        close_to(live[6], 16.0 / 9, "Level view did not restore the normal framing");
        require(dolly::gLastEvent == idle_serial, "Level view enqueued a project edit");
        require(!editor_apply_level_view(live), "A level-view request applied twice");
        // Playback and a non-manual view never arm the reset.
        editor_update_view(true, false, true, live);
        dolly::key_event('R', true);
        dolly::key_event('R', false);
        require(!editor_apply_level_view(live), "Level view armed during replay playback");
        editor_update_view(true, true, false, live);
        dolly::key_event('R', true);
        dolly::key_event('R', false);
        require(!editor_apply_level_view(live),
                "Level view armed without the live manual camera");
        editor_update_view(true, true, true, live);
    }
    config->camera_count = 2;
    auto saved_list = std::atomic_load(&dolly::gCameraList);
    auto list = std::make_shared<EditorCameraList>();
    std::memcpy(list->magic,"DLYCAMS1",8);list->abi=1;list->revision=7;list->total=2;list->count=2;
    std::atomic_store(&dolly::gCameraList,std::shared_ptr<const EditorCameraList>(list));
    CameraPose request{};request[0]=7;
    dolly::gAcknowledged = dolly::gLastEvent;
    {
        const auto selected = config->selected_camera;
        config->selected_camera = 1;
        const auto serial = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::SeekCamera, 1, &request),
                "Selected camera seek cannot enter the real event ring");
        const auto& event = dolly::gEvents[serial % kEditorEventCount];
        require(event.action == 119 && event.value == 1 && event.pose[0] == 7,
                "Selected camera seek lost its appended action, index or revision");
        const auto after_valid = dolly::gLastEvent;
        for (double index : {-1., .5, 0., 2.})
            require(!editor_enqueue(EditorAction::SeekCamera, index, &request),
                    "Invalid or no-longer-selected camera seek entered the event ring");
        request[0] = 6;
        require(!editor_enqueue(EditorAction::SeekCamera, 1, &request),
                "Stale camera revision entered the seek event ring");
        request[0] = 7;
        require(!editor_enqueue(EditorAction::SeekCamera, 1),
                "Camera seek without revision identity entered the event ring");
        editor_set_owner(EditorOwner::Flight);
        require(!editor_enqueue(EditorAction::SeekCamera, 1, &request),
                "Camera seek bypassed panel ownership");
        editor_set_owner(EditorOwner::Panel);
        editor_update_view(true, false, true, pose);
        require(!editor_enqueue(EditorAction::SeekCamera, 1, &request),
                "Unpaused replay accepted a selected camera seek");
        editor_update_view(true, true, true, pose);
        const auto playback = config->playback_flags;
        for (unsigned flag : {1u, 2u}) {
            config->playback_flags = flag;
            require(!editor_enqueue(EditorAction::SeekCamera, 1, &request),
                    "Busy or playing editor accepted a selected camera seek");
        }
        config->playback_flags = playback;
        require(dolly::gLastEvent == after_valid,
                "Rejected camera seeks advanced the real event ring");
        config->camera_count = list->total = 40;
        list->first = 32;
        list->count = 8;
        require(editor_enqueue(EditorAction::SeekCamera, 1, &request) &&
                    !editor_enqueue(EditorAction::ViewCamera, 1, &request),
                "Global selected camera seek changed the visible-page view contract");
        config->camera_count = list->total = list->count = 2;
        list->first = 0;
        config->selected_camera = selected;
    }
    dolly::gAcknowledged = dolly::gLastEvent;
    const auto preparation_serial = dolly::gLastEvent;
    require(editor_enqueue(EditorAction::PrepareCinematic,42,&request),
            "Cinematic preparation cannot enter the real event ring");
    const auto preparation_event = dolly::gEvents[preparation_serial % kEditorEventCount];
    require(preparation_event.action==109 && preparation_event.value==42 && preparation_event.pose[0]==7,
            "Cinematic preparation token/revision differs from Python");
    auto preparation = std::make_shared<EditorCinematicStatus>();
    std::memcpy(preparation->magic,"DLYCIN01",8);preparation->abi=1;
    preparation->token=42;preparation->revision=7;preparation->state=2;
    std::atomic_store(&dolly::gCinematicStatus,std::shared_ptr<const EditorCinematicStatus>(preparation));
    EditorCinematicStatus result{};
    require(editor_cinematic_status(result) && result.state==2,"Completed cinematic is not ready");
    dispatch(EditorAction::Stop);
    require(editor_cinematic_status(result) && result.token==42 && result.state==3,
            "Keyboard Stop does not invalidate cinematic readiness");
    require(editor_enqueue(EditorAction::PrepareCinematic,43,&request),"A stopped preparation cannot retry");
    request[0]=6;
    require(!editor_enqueue(EditorAction::PrepareCinematic,44,&request),"Stale cinematic revision was accepted");
    request[0]=7;
    require(!editor_enqueue(EditorAction::PrepareCinematic,0,&request) &&
            !editor_enqueue(EditorAction::PrepareCinematic,44),"Invalid cinematic request entered the ring");
    require(editor_enqueue(EditorAction::ClearCameraPath,0,&request), "Confirmed Clear cannot enter event ring");
    request[0]=6;
    require(!editor_enqueue(EditorAction::ClearCameraPath,0,&request), "Stale Clear revision was accepted");
    request[0]=7;
    dolly::gAcknowledged = dolly::gLastEvent;
    for (auto action : {EditorAction::Capture, EditorAction::Replace, EditorAction::ResetCameraPath})
        require(editor_enqueue(action), "Camera guard fixture could not enqueue an idle capture action");
    {
        const auto saved_window = dolly::gWindow.load();
        struct RestoreWindow {
            HWND previous;
            ~RestoreWindow() { dolly::gWindow = previous; editor_foreground_window_stub = nullptr; }
        } restore{saved_window};
        CameraPose position_request{7, 1, 12.125, 0, 0, 0, 0};
        require(!editor_enqueue(EditorAction::SetCameraPosition, 1, &position_request),
                "Position gesture ignored lost foreground ownership");
        editor_foreground_window_stub = reinterpret_cast<HWND>(std::uintptr_t(1));
        dolly::gWindow = editor_foreground_window_stub;
        const auto serial = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::SetCameraPosition, 1, &position_request),
                "A valid position gesture was rejected after native validation");
        const auto& position_event = dolly::gEvents[serial % kEditorEventCount];
        require(position_event.action == 111 && position_event.value == 1 &&
                    position_event.pose[0] == 7 && position_event.pose[1] == 1 &&
                    position_event.pose[2] == 12.125 && position_event.pose[3] == 0,
                "Real enqueue changed the position gesture payload");
        position_request[0] = 6;
        require(!editor_enqueue(EditorAction::SetCameraPosition, 1, &position_request),
                "A stale position gesture bypassed its revision guard");
        position_request[0] = 7;
        position_request[1] = .5;
        require(!editor_enqueue(EditorAction::SetCameraPosition, 1, &position_request),
                "A fractional position axis entered the real event ring");
        position_request[1] = 1;
        position_request[3] = 1;
        require(!editor_enqueue(EditorAction::SetCameraPosition, 1, &position_request),
                "A retimed position gesture entered the real event ring");
        position_request[3] = 0;
        list->rows[1].source = 1;
        require(!editor_enqueue(EditorAction::SetCameraPosition, 1, &position_request),
                "A tracked camera accepted a free-position gesture");
        list->rows[1].source = 0;
        require(dolly::gLastEvent == serial + 1,
                "A rejected position gesture advanced the real event ring");
        CurveViewerFixture viewer;
        CameraPose curve_request{7, 4, 721.125, 1, 0x89abcdef, 0xf1234567, 0};
        const auto curve_serial = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::SetCameraCurveValue, 1, &curve_request),
                "Valid unified curve edit did not enter the real event ring");
        const auto& curve_event = dolly::gEvents[curve_serial % kEditorEventCount];
        require(curve_event.action == 112 && curve_event.value == 1 && std::equal(std::begin(curve_event.pose), std::end(curve_event.pose), curve_request.begin()),
                "Real enqueue changed the unified curve authoring/session payload");
        for (unsigned field : {0u, 3u, 4u, 5u, 6u}) {
            auto stale = curve_request; stale[field] += 1;
            require(!editor_enqueue(EditorAction::SetCameraCurveValue, 1, &stale),
                    "Stale unified curve request entered the real event ring");
        }
        auto select_request = curve_request;
        select_request[1] = select_request[2] = select_request[3] = 0;
        require(editor_enqueue(EditorAction::SelectCurveCamera, 0, &select_request),
                "Full-shot graph selection cannot enter the real event ring");
        const auto& select_event = dolly::gEvents[(curve_serial + 1) % kEditorEventCount];
        require(select_event.action == 113 && select_event.value == 0 && std::equal(std::begin(select_event.pose), std::end(select_event.pose), select_request.begin()),
                "Real enqueue changed full-shot selection payload");
        require(dolly::gLastEvent == curve_serial + 2,
                "Rejected unified curve gestures advanced the event ring");
        CameraPose tangent_request{7,12.125,-8.25,1,0x89abcdef,0xf1234567,0};
        const auto tangent_serial=dolly::gLastEvent;
        const auto saved_selection=config->selected_camera;
        require(saved_selection==0 && !editor_enqueue(EditorAction::SetCameraPositionTangents,15,&tangent_request),
                "XYZ tangent request ignored a mismatched editor/viewer selection");
        config->selected_camera=1;
        require(editor_enqueue(EditorAction::SetCameraPositionTangents,15,&tangent_request),
                "Atomic XYZ tangent request did not enter the real event ring");
        const auto& tangent_event=dolly::gEvents[tangent_serial%kEditorEventCount];
        require(tangent_event.action==114 && tangent_event.value==15 &&
                std::equal(std::begin(tangent_event.pose),std::end(tangent_event.pose),tangent_request.begin()),
                "Real enqueue changed XYZ tangent mode/pair/identity payload");
        for(unsigned field:{0u,3u,4u,5u,6u}) {
            auto stale=tangent_request;stale[field]+=1;
            require(!editor_enqueue(EditorAction::SetCameraPositionTangents,15,&stale),
                    "Stale XYZ tangent gesture entered the real event ring");
        }
        require(!editor_enqueue(EditorAction::SetCameraPositionTangents,14,&tangent_request),
                "Unequal linked tangent request entered the real event ring");
        require(dolly::gLastEvent==tangent_serial+1,"Rejected tangent requests advanced the event ring");
        const auto graph=visualization_snapshot();const auto preview_token=visualization_begin_native_preview(*graph,1,4);
        auto preview_request=curve_request;preview_request[6]=preview_token;
        const auto preview_serial=dolly::gLastEvent;
        require(editor_enqueue(EditorAction::PreviewCameraCurveValue,1,&preview_request),
                "Guarded render-only value draft did not enter the event ring");
        const auto& preview_event=dolly::gEvents[preview_serial%kEditorEventCount];
        require(preview_event.action==115 && preview_event.pose[6]==preview_token &&
                visualization_snapshot()==graph && config->selected_camera==1,
                "Preview event changed authoritative geometry, selection or its token");
        auto wrong_token=preview_request;wrong_token[6]+=1;
        require(!editor_enqueue(EditorAction::PreviewCameraCurveValue,1,&wrong_token),"Wrong preview token entered the ring");
        auto wrong_channel=preview_request;wrong_channel[1]=3;
        require(!editor_enqueue(EditorAction::PreviewCameraCurveValue,1,&wrong_channel),"Preview token changed its gesture channel");
        CameraPose clear_request{7,0,0,1,0x89abcdef,0xf1234567,double(preview_token)};
        const auto enabled=config->enabled;config->enabled=0;editor_foreground_window_stub=nullptr;
        require(!editor_enqueue(EditorAction::PreviewCameraCurveValue,1,&preview_request) &&
                editor_enqueue(EditorAction::ClearCameraCurvePreview,1,&clear_request),
                "Lost focus/disabled editor blocked terminal clear or admitted another draft");
        config->enabled=enabled;editor_foreground_window_stub=dolly::gWindow.load();
        visualization_end_native_preview(preview_token);
        require(!editor_enqueue(EditorAction::PreviewCameraCurveValue,1,&preview_request),"Ended preview token admitted a late draft");
        const auto tangent_token=visualization_begin_native_preview(*graph,1,0);
        auto tangent_preview=tangent_request;tangent_preview[6]=tangent_token;
        require(editor_enqueue(EditorAction::PreviewCameraPositionTangents,15,&tangent_preview),
                "Guarded atomic tangent draft did not enter the event ring");
        clear_request[6]=tangent_token;
        require(editor_enqueue(EditorAction::ClearCameraCurvePreview,1,&clear_request),"Tangent preview cannot terminate");
        visualization_end_native_preview(tangent_token);
        config->selected_camera=saved_selection;

    }
    {
        // Without a Present, the real recorder waits in starting and never
        // loads Media Foundation, creates an encoder, or opens an output file.
        // Exercise the actual enqueue boundary while replay playback is paused.
        wchar_t folder[MAX_PATH]{};
        require(GetTempPathW(MAX_PATH, folder) != 0, "Recorder guard fixture has no temporary directory");
        const std::wstring output = std::wstring(folder) + L"Dolly.CameraGuard." +
            std::to_wstring(GetCurrentProcessId()) + L"." + std::to_wstring(GetTickCount64()) + L".mp4";
        require(GetFileAttributesW(output.c_str()) == INVALID_FILE_ATTRIBUTES,
                "Recorder guard fixture output already exists");
        struct RecorderCleanup {
            ~RecorderCleanup() { video::stop(true); video::shutdown(); }
        } cleanup;
        video::Options options{};
        options.path = output.c_str();
        CameraPose camera_seek_request{7, 0, 0, 0, 0, 0, 0};
        require(
            editor_enqueue(EditorAction::SeekCamera, config->selected_camera, &camera_seek_request),
            "Idle camera seek failed before the recording guard test");
        require(video::start(options), "Cannot start the no-Present recorder guard fixture");
        require(editor_recording_status().state == video::State::starting &&
                    editor_recording_locks_cameras(editor_recording_status().state) &&
                    editor_snapshot().paused && !editor_snapshot().playing,
                "The recorder fixture did not keep replay and recording state separate");
        dolly::gAcknowledged = dolly::gLastEvent;
        const auto locked_serial = dolly::gLastEvent;
        require(!editor_enqueue(EditorAction::SeekCamera, config->selected_camera,
                                &camera_seek_request),
                "Paused recording accepted a selected camera seek");
        for (auto action : {EditorAction::Capture, EditorAction::Replace, EditorAction::ResetCameraPath})
            require(!editor_enqueue(action), "Paused recording accepted a capture action into the real event ring");
        for (auto action : {EditorAction::ClearCameraPath, EditorAction::DeleteCamera, EditorAction::UndoShot,
                            EditorAction::RedoShot, EditorAction::SetCameraTime, EditorAction::SetCameraRoll,
                            EditorAction::SetCameraPosition, EditorAction::SetCameraCurveValue, EditorAction::SetCameraPositionTangents,
                            EditorAction::PreviewCameraCurveValue,EditorAction::PreviewCameraPositionTangents, EditorAction::SetFraming, EditorAction::SetAttachTarget,
                            EditorAction::SetAttachPoint, EditorAction::SetAttachOffsets, EditorAction::SetAttachSmoothing,
                            EditorAction::SetAttachHide, EditorAction::AttachCycleTarget, EditorAction::AttachCyclePoint,
                            EditorAction::AttachReset, EditorAction::AttachSnap, EditorAction::SetAttachBone,
                            EditorAction::SetSourceBlend})
            require(!editor_enqueue(action, 0, &request),
                    "Paused recording accepted a camera mutation into the real event ring");
        require(dolly::gLastEvent == locked_serial,
                "A rejected recording-time camera mutation advanced the event ring");
        require(!editor_request_framing_factor(.8), "Paused recording accepted typed framing");
        const auto locked_aspect = pose[6];
        dolly::apply_framing_wheel(pose, *config, WHEEL_DELTA);
        close_to(pose[6], locked_aspect, "Paused recording changed live framing before rejecting its saved edit");
        require(editor_enqueue(EditorAction::StopVideo) && editor_enqueue(EditorAction::Stop) &&
                    editor_enqueue(EditorAction::Console),
                "Recording camera guard blocked Finish, Stop, or console recovery");
        video::stop(true);
        video::shutdown();
        require(!editor_recording_locks_cameras(editor_recording_status().state) &&
                    GetFileAttributesW(output.c_str()) == INVALID_FILE_ATTRIBUTES,
                "No-Present recorder did not cancel without producing an output file");
        require(editor_enqueue(EditorAction::ClearCameraPath, 0, &request),
                "Finishing the recorder did not restore normal camera editing");
    }
    std::atomic_store(&dolly::gCameraList,saved_list);
    std::atomic_store(&dolly::gConfig, saved);
    editor_update_view(view.ready, view.paused, view.manual_active, view.pose);
    dolly::gAcknowledged = dolly::gLastEvent;
}

void editor_object_checks() {
    // Regression: ObjectTransform is action id 104, so the editor_enqueue range
    // guard must bound at the last declared action. Bounding at ObjectDelete
    // (103) silently dropped every gizmo commit before it reached the ring.
    const auto saved_config = std::atomic_load(&dolly::gConfig);
    const auto saved_view = editor_snapshot();
    auto config = std::make_shared<EditorConfig>(*saved_config);
    config->camera_count = 1;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(config));
    dolly::gAcknowledged = dolly::gLastEvent;
    CameraPose pose{1, 2, 3, 4, 5, 6, 16.0 / 9};
    editor_set_owner(EditorOwner::Panel);
    editor_update_view(true, true, true, pose);

    // Publish an accepted object config with the picker open, like the desktop.
    std::vector<unsigned char> memory(kMappingBytes);
    EditorObjectConfig objects{};
    std::memcpy(objects.magic, "DLYOBJ01", 8);
    objects.sequence = 2;
    objects.abi = 1;
    objects.flags = 1;  // open
    objects.count = 1;
    objects.selected = 0;
    objects.distance = 600;
    objects.items[0].shape = 3;  // crate
    objects.items[0].scale = 1.0f;
    std::memcpy(memory.data() + kEditorObjectOffset, &objects, sizeof(objects));
    editor_worker_tick(memory.data(), true);
    require(editor_snapshot().object_picker && editor_snapshot().object_count == 1,
            "Object Picker config did not reach the snapshot");

    // Open and cancel round-trip.
    require(editor_enqueue(EditorAction::ObjectPickerCancel),
            "Object picker cancel was rejected by the action range");
    dolly::gAcknowledged = dolly::gLastEvent;

    // Place carries a world point; accepted while open with a finite pose.
    CameraPose point{10, 20, 30, 0, 0, 0, 0};
    require(editor_enqueue(EditorAction::ObjectPlace, 3, &point),
            "Object place was rejected by the action range");
    require(!editor_enqueue(EditorAction::ObjectPlace, 3, nullptr),
            "Object place without a world point was accepted");
    require(!editor_enqueue(EditorAction::ObjectPlace, 9, &point),
            "Object place with an out-of-range library index was accepted");
    require(!editor_enqueue(EditorAction::ObjectPlace, 3.5, &point),
            "Object place with a fractional library index was accepted");

    // The transform event (id 104) must survive both the ID bound and the
    // pose_override allowlist, and preserve the whole pose.
    CameraPose transform{5, 6, 7, 10, 20, 30, 2.5};
    require(editor_enqueue(EditorAction::ObjectTransform, 0, &transform),
            "Object transform (id 104) was dropped by the action range guard");
    const auto& event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
    require(event.action == std::uint32_t(EditorAction::ObjectTransform) && event.value == 0 &&
                event.pose[0] == 5 && event.pose[4] == 20 && event.pose[6] == 2.5,
            "Object transform payload was not preserved");
    require(!editor_enqueue(EditorAction::ObjectTransform, 0, nullptr),
            "Object transform without a pose was accepted");
    CameraPose bad_transform{5, 6, 7, 10, 20, 30, 50};
    require(!editor_enqueue(EditorAction::ObjectTransform, 0, &bad_transform),
            "Object transform with an out-of-range scale was accepted");

    // Select and delete are only valid while the picker is open.
    dolly::gAcknowledged = dolly::gLastEvent;
    require(editor_enqueue(EditorAction::ObjectSelect, 0) &&
                editor_enqueue(EditorAction::ObjectDelete, 0),
            "Object select/delete was rejected while open");
    // Close the mode; select/delete must now be refused.
    std::atomic_store(&dolly::gObjectConfig,
                      std::shared_ptr<const EditorObjectConfig>{});
    std::memset(memory.data() + kEditorObjectOffset, 0, sizeof(EditorObjectConfig));
    editor_worker_tick(memory.data(), true);
    require(!editor_snapshot().object_picker, "Object picker stayed open after clearing");
    dolly::gAcknowledged = dolly::gLastEvent;
    require(!editor_enqueue(EditorAction::ObjectSelect, 0) &&
                !editor_enqueue(EditorAction::ObjectDelete, 0),
            "Object select/delete was accepted while closed");
    require(!editor_enqueue(EditorAction::ObjectTransform, 0, &transform),
            "Object transform was accepted while closed");

    std::atomic_store(&dolly::gConfig, saved_config);
    dolly::gAcknowledged = dolly::gLastEvent;
}

void editor_heartbeat_reconnect_checks() {
    // A >2 s heartbeat lapse disables editor input. When the desktop heartbeat
    // returns, the config it already published (unchanged sequence) must
    // restore its owner instead of leaving the panel off until F8.
    std::vector<unsigned char> memory(kMappingBytes);
    EditorConfig config{};
    std::memcpy(config.magic, "DLYEDIT1", 8);
    config.sequence = 0x7000;
    config.abi = kEditorAbi;
    config.enabled = 1;
    config.owner = std::uint32_t(EditorOwner::Panel);
    config.owner_sequence = 0x7001;
    config.speed = 400;
    config.sensitivity = .08;
    std::memcpy(memory.data() + kEditorConfigOffset, &config, sizeof(config));
    editor_worker_tick(memory.data(), true);
    require(dolly::gOwner == EditorOwner::Panel, "Published panel owner was not applied");
    editor_worker_tick(memory.data(), false);
    require(dolly::gOwner == EditorOwner::Disabled, "A heartbeat lapse kept editor input");
    editor_worker_tick(memory.data(), true);
    require(dolly::gOwner == EditorOwner::Panel,
            "A returning heartbeat did not restore the published editor owner");
}

void independent_heartbeat() {
    alignas(8) unsigned char memory[64]{};
    auto* beat = reinterpret_cast<volatile LONG64*>(memory + 32);
    gWorkerError = 0;
    gHeartbeatTime = now_seconds() - 3;
    {
        HeartbeatMonitor monitor(memory, GetCurrentProcess());
        InterlockedExchange64(beat, 1);
        const double deadline = now_seconds() + 1;
        while (now_seconds() - gHeartbeatTime.load() > 2 && now_seconds() < deadline)
            Sleep(5);
        require(now_seconds() - gHeartbeatTime.load() < 2,
                "Heartbeat depended on the busy discovery worker");
        // A still-open process with an unchanged beat must not renew its lease.
        Sleep(50);
        const double observed = gHeartbeatTime.load();
        Sleep(75);
        require(gHeartbeatTime.load() == observed, "An unchanged heartbeat renewed its lease");
    }
    const double stopped = gHeartbeatTime.load();
    InterlockedExchange64(beat, 2);
    Sleep(50);
    require(gHeartbeatTime.load() == stopped, "Heartbeat monitor outlived its scope");
}

void confetti_control_updates() {
    Fixture f;
    f.command(Mode::Hold, .25);
    auto current = std::make_shared<Command>(*std::atomic_load(&gCommand));
    auto& original = current->wire;
    std::memcpy(original.magic, kControlMagic, 8);
    original.abi = kBridgeAbi;
    original.sequence = 2;
    std::atomic_store(&gCommand, std::shared_ptr<const Command>(current));
    ControlHeader incoming{}, observed{};
    std::vector<unsigned char> payload;
    const auto read = [&] {
        std::memcpy(gMemory, &incoming, sizeof(incoming));
        return read_control(observed, payload, original.command);
    };
    incoming = original;
    require(!read(), "Unchanged command should not be reprocessed");
    incoming.flags ^= kNoSeekRelief;
    require(read(), "Seek relief refresh was ignored under the active command");
    incoming.flags ^= kNoSeekRelief;
    incoming.flags |= kConfetti | (250u << kConfettiHeightShift);
    require(read(), "Immediate confetti settings were ignored under the active command");
    incoming.flags |= (1u << kParticlePresetShift) | (40u << kParticleIntensityShift);
    require(read(), "Particle preset and intensity refresh were ignored under the active command");
    incoming.mode = std::uint32_t(Mode::Play);
    require(!read(), "Same-ID flag refresh must not change playback mode");
    incoming.mode = original.mode;
    incoming.flags |= kGamePov;
    require(!read(), "Same-ID flag refresh must not change camera ownership");
    incoming.flags &= ~kGamePov;
    incoming.start_phase += 1;
    require(!read(), "Same-ID flag refresh must not change the playback clock");
}

void confetti_diagnostics_publish() {
    dolly::confetti::Camera camera{};
    dolly::confetti::on_frame(10.0, true, false, &camera, true, 250.0f, false, 0, 1.0f);
    Fixture f;
    f.frame();
    const auto diag = f.confetti();
    require(diag.frames >= 1, "Confetti frame calls were not counted");
    require(diag.state == 0, "Unsupported synthetic builds must report confetti unavailable");
    require(diag.handles == 0, "No particle handles may exist without a real client build");
    require(diag.starts == 0 && diag.start_failures == 0,
            "The synthetic client must not create real particle effects");
}

// The synthetic engine's IsPlaying accessor pauses one chosen thread inside the
// real on_view, after its command snapshot and main-view filter.
struct ViewPause {
    std::atomic<DWORD> thread{0};
    HANDLE arrived = nullptr, resume = nullptr;
} gViewPause;
bool __fastcall paused_is_playing(void*) {
    if (gViewPause.thread.load() == GetCurrentThreadId()) {
        gViewPause.thread = 0;
        SetEvent(gViewPause.arrived);
        WaitForSingleObject(gViewPause.resume, 10000);
    }
    return playing;
}

void overlapping_main_view(Fixture& f) {
    // Thread A holds a camera command inside the verified main view while the
    // editor publishes Release and thread B runs another verified main view.
    // B must not acknowledge Release ahead of A's stale camera write.
    const auto hold_id = f.command(Mode::Hold, .25);
    auto status = f.frame();
    require(status.ack_command == hold_id && status.state == unsigned(State::Armed),
            "Overlap setup did not arm a held camera");
    gViewPause.arrived = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    gViewPause.resume = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    require(gViewPause.arrived && gViewPause.resume, "Could not create overlap events");
    thunk(gEngine + gEngineLayout.playing, paused_is_playing);
    const auto before = status.frame_count;
    const auto overlaps = gMainViewOverlaps.load();
    f.original_view();
    gHeartbeatTime = now_seconds();
    std::thread holder([&] {
        gViewPause.thread = GetCurrentThreadId();
        on_view(f.view.pointer, gClient + gCompat.caller);
    });
    std::string failure;
    if (WaitForSingleObject(gViewPause.arrived, 10000) != WAIT_OBJECT_0) {
        failure = "First main view never reached the synthetic engine accessor";
    } else {
        const auto release_id = f.command(Mode::Release);
        const auto calls = gHookCalls.load();
        on_view(f.view.pointer, gClient + gCompat.caller + 1);
        if (gHookCalls.load() != calls + 1 || gMainViewOverlaps.load() != overlaps)
            failure = "A non-main view was counted as a main-view overlap";
        on_view(f.view.pointer, gClient + gCompat.caller);
        status = f.status();
        if (failure.empty() && (status.frame_count != before || status.ack_command != hold_id))
            failure = "Overlapping main view ran the callback and acknowledged Release early";
        else if (failure.empty() && gMainViewOverlaps.load() != overlaps + 1)
            failure = "Overlapping main view was not counted";
        SetEvent(gViewPause.resume);
        holder.join();
        if (failure.empty()) {
            status = f.status();
            if (status.frame_count != before + 1 || status.ack_command != hold_id ||
                status.state != unsigned(State::Armed))
                failure = "Admitted main view did not finish its held camera frame";
        }
        thunk(gEngine + gEngineLayout.playing, is_playing);
        if (failure.empty()) {
            status = f.frame();
            if (status.ack_command != release_id || status.state != unsigned(State::Stopped) ||
                status.error)
                failure = "Release was not acknowledged on the next admitted main view";
            else
                f.unchanged();
        }
    }
    if (holder.joinable()) {
        SetEvent(gViewPause.resume);
        holder.join();
        thunk(gEngine + gEngineLayout.playing, is_playing);
    }
    CloseHandle(gViewPause.arrived);
    CloseHandle(gViewPause.resume);
    gViewPause.arrived = gViewPause.resume = nullptr;
    require(failure.empty(), failure.c_str());
}

// The experimental prop probe calls into the client entity system, so it may
// only run on the admitted main view (the confetti frame's thread). A synthetic
// client matching the reviewed row, with an empty entity-system global, makes
// the probe stop at "no entity system" instead of calling the engine.
void prop_probe_main_view_only(Fixture& f) {
    constexpr std::uintptr_t kCreate = 0x20915c0, kGlobal = 0x3e1ed10, kPage = 0x1000;
    auto* region = static_cast<unsigned char*>(
        VirtualAlloc(nullptr, kGlobal + kPage, MEM_RESERVE, PAGE_NOACCESS));
    require(region != nullptr, "Could not reserve the synthetic probe client");
    const auto base = reinterpret_cast<std::uintptr_t>(region);
    require(VirtualAlloc(region + (kCreate & ~(kPage - 1)), 2 * kPage, MEM_COMMIT,
                         PAGE_READWRITE) &&
                VirtualAlloc(region + (kGlobal & ~(kPage - 1)), kPage, MEM_COMMIT,
                             PAGE_READWRITE),
            "Could not commit the synthetic probe client");
    const unsigned char prologue[] = {0x48, 0x89, 0x5c, 0x24, 0x08, 0x48,
                                      0x89, 0x6c, 0x24, 0x10, 0x48, 0x89};
    std::memcpy(region + kCreate, prologue, sizeof prologue);
    std::string failure;
    if (!prop_runtime::initialize(base) || prop_runtime::diagnostics().state != 1) {
        failure = "Synthetic client did not match the reviewed probe row";
    } else {
        prop_runtime::request(false);
        gHeartbeatTime = now_seconds();
        f.original_view();
        on_view(f.view.pointer, gClient + gCompat.caller + 1);
        if (prop_runtime::diagnostics().state != 1)
            failure = "A non-main view consumed the prop probe";
        gMainViewBusy = true;
        f.original_view();
        on_view(f.view.pointer, gClient + gCompat.caller);
        gMainViewBusy = false;
        if (failure.empty() && prop_runtime::diagnostics().state != 1)
            failure = "An overlapping main view consumed the prop probe";
        f.original_view();
        on_view(f.view.pointer, gClient + gCompat.caller);
        const auto d = prop_runtime::diagnostics();
        if (failure.empty() && (d.state != 3 || d.error != 2))
            failure = "The admitted main view did not run the prop probe";
    }
    prop_runtime::disable();
    VirtualFree(region, 0, MEM_RELEASE);
    require(failure.empty(), failure.c_str());
}

void run() {
    gameplay_effect_filter_checks();
    confetti_control_updates();
    confetti_diagnostics_publish();
    attach_preview_continuity_checks();
    bone_string_bounds();
    owned_model_bones();
    camera_cache_decoder();
    updated_camera_cache_decoder();
    atomic_exports();
    independent_heartbeat();
    replay_identity();
    Fixture f;
    follow_anchor_guard_checks(f);
    // Exercise the reviewed September engine dispatch with harmless synthetic
    // accessors, including rejection of a stale demo/engine vtable identity.
    gEngineLayout = kSeptemberEngineLayout;
    put(gEngine + gEngineLayout.demo_global, f.demo.address());
    put(f.demo.address(), gEngine + gEngineLayout.demo_table);
    put(f.engine_client.address(), gEngine + gEngineLayout.engine_table);
    thunk(gEngine + gEngineLayout.playing, is_playing);
    thunk(gEngine + gEngineLayout.active, is_active);
    thunk(gEngine + gEngineLayout.paused, is_paused);
    thunk(gEngine + gEngineLayout.seeking, is_seeking);
    thunk(gEngine + gEngineLayout.tick, current_tick);
    thunk(gEngine + gEngineLayout.filename, current_name);
    DemoState updated_demo{};
    require(read_demo(updated_demo) && updated_demo.playing && updated_demo.tick == tick &&
                std::strcmp(updated_demo.name, demo_name) == 0,
            "Updated engine dispatch did not read the synthetic replay");
    put(f.demo.address(), gEngine + kDemoTable);
    require(!read_demo(updated_demo), "Updated engine accepted the old demo vtable");
    put(f.demo.address(), gEngine + gEngineLayout.demo_table);
    put(f.engine_client.address(), gEngine + kEngineTable);
    require(!read_demo(updated_demo), "Updated engine accepted the old engine-client vtable");
    gEngineLayout = kLegacyEngineLayout;
    put(f.demo.address(), gEngine + kDemoTable);
    auto status = f.frame();
    require(status.state == static_cast<unsigned>(State::Probe),
            "No command must leave the hook in probe mode");
    f.unchanged();

    // Calling another view/caller must neither take camera ownership nor
    // acknowledge a command that never reached the verified main-view site.
    f.command(Mode::Hold, .25);
    f.original_view();
    on_view(f.view.pointer, gClient + gCompat.caller + 1);
    require(f.status().frame_count == status.frame_count,
            "Unverified caller updated native status");
    f.unchanged();
    status = f.frame();
    require(status.ack_command == f.next_command &&
                status.state == static_cast<unsigned>(State::Armed),
            "HOLD must acknowledge from the actual verified callback");
    f.applied(.25);

    status = f.frame(0x84);
    require(!status.error, "Secondary view selection blocked the authored camera");
    f.applied(.25);

    std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke");
    f.command(Mode::Hold, .25);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Armed) && !status.error,
            "The actual native view rejected the selected replay without its .dem suffix");
    f.applied(.25);
    std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke.dem");

    const auto play_id = f.command(Mode::Play, 0);
    status = f.frame();
    require(status.ack_command == play_id && status.paused && !status.error,
            "PLAY must acknowledge while waiting for demo_resume");
    close_to(status.phase, .25, "PLAY while paused must retain the held camera");
    f.applied(.25);

    // Eight interpolated engine times inside one tick must produce eight
    // distinct view positions; this catches a return to tick-only stepping.
    paused = false;
    double previous_x = status.applied_pose[0];
    for (int frame = 1; frame <= 8; ++frame) {
        f.clock(64 + frame / 1024.0f, 4096);
        status = f.frame();
        close_to(status.phase, .25 + frame / 1024.0,
                 "Main-view engine time was not evaluated continuously");
        require(status.applied_pose[0] > previous_x && !status.error,
                "Subtick camera position repeated or went backwards");
        f.applied(status.phase);
        previous_x = status.applied_pose[0];
    }

    const auto held_phase = status.phase;
    f.command(Mode::HoldCurrent, 0);
    status = f.frame();
    close_to(status.phase, held_phase, "HoldCurrent changed the displayed phase");
    f.clock(64.0625f, 4100);
    status = f.frame();
    close_to(status.phase, held_phase, "HoldCurrent followed a moving replay clock");
    f.command(Mode::Play, 0);
    status = f.frame();
    close_to(status.phase, held_phase, "Resuming a held camera jumped to command start_phase");
    f.clock(64.125f, 4104);
    status = f.frame();
    close_to(status.phase, held_phase + .0625, "Resume did not advance from held phase");
    f.applied(status.phase);

    f.fresh_hold(.5);
    f.applied(.5); // Non-native aspect must alter FOV as well as view aspect.
    f.command(Mode::Hold, .5, 0);
    f.frame();
    f.applied(.5, false);

    f.fresh_hold();
    f.command(Mode::Play);
    f.frame();
    paused = false;
    for (int quarter = 1; quarter <= 4; ++quarter) {
        f.clock(64 + quarter / 4.0f, 4096 + quarter * 16);
        status = f.frame();
        require(!status.error, "Ordinary native progression faulted before endpoint");
    }
    require(status.state == static_cast<unsigned>(State::Completed),
            "Exact endpoint was not marked completed");
    close_to(status.phase, 1, "Final phase is not exact", 0);
    f.applied(1);
    f.clock(65.125f, 4168);
    status = f.frame();
    close_to(status.phase, 1, "Completed camera did not keep final pose", 0);
    f.applied(1);

    f.fresh_hold();
    std::snprintf(demo_name, sizeof(demo_name), "another-demo.dem");
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Fault) && status.error == 12,
            "Changing demos must fault before writing the camera");
    f.unchanged();
    f.command(Mode::Release);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Stopped) && !status.error,
            "Release after a fault must acknowledge and relinquish camera ownership");
    f.unchanged();

    f.fresh_hold();
    gHeartbeatTime = now_seconds() - 3;
    status = f.frame(0, false);
    require(status.error == 10, "Expired editor lease must release the view");
    f.unchanged();
    f.command(Mode::Release);
    status = f.frame(0, false);
    require(status.state == static_cast<unsigned>(State::Stopped),
            "Expired lease prevented explicit release");
    f.unchanged();
    overlapping_main_view(f);
    prop_probe_main_view_only(f);

    f.fresh_hold();
    f.command(Mode::Play);
    f.frame();
    paused = false;
    f.clock(70, 4480);
    status = f.frame();
    require(status.error == 15,
            "A large replay time jump must fault before evaluating a distant camera");
    f.unchanged();

    f.fresh_hold();
    status = f.frame(2);
    require(status.error == 11, "Alternate projection must be rejected");
    f.unchanged();
    f.fresh_hold();
    seeking = true;
    status = f.frame();
    require(status.error == 12, "Seeking replay must release native camera ownership");
    f.unchanged();

    f.fresh_hold(.25);
    f.command(Mode::Play, 0, kFrozen | kAspect);
    status = f.frame();
    const double frozen_start = status.phase;
    Sleep(10);
    status = f.frame();
    require(status.phase > frozen_start && status.paused && !status.error,
            "Frozen native playback must advance from real time while replay remains paused");
    f.applied(status.phase);
    f.clock(64, 4097);
    status = f.frame();
    require(status.error == 14, "Moving frozen replay must release the native camera");
    f.unchanged();

    f.enable_synthetic_editor();
    // Configuration is published separately from camera commands. A view can
    // observe Manual just before the worker consumes the new enabled config.
    auto enabled_config = std::atomic_load(&dolly::gConfig);
    auto pending_config = std::make_shared<EditorConfig>(*enabled_config);
    pending_config->enabled = 0;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(pending_config));
    f.manual(Mode::Manual, &first);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Starting) && !status.error &&
                !editor_snapshot().manual_active,
            "A pending editor config latched a camera fault or armed input");
    f.unchanged();
    std::atomic_store(&dolly::gConfig, enabled_config);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Armed) && !status.error,
            "The same manual command did not recover after configuration arrived");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], first[i], "Pending configuration lost the manual seed");
    editor_overlay_available(false);
    f.manual(Mode::Manual, &last);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Starting) && !status.error &&
                !editor_snapshot().manual_active,
            "Pending DX11 initialization latched a camera fault or armed input");
    f.unchanged();
    editor_overlay_available(true);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Armed) && !status.error,
            "Manual flight did not recover when the DX11 panel became available");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], last[i], "Pending DX11 setup lost the manual seed");
    f.fresh_hold(.5);
    auto selected = f.status();
    f.manual();
    status = f.frame();
    require(!status.error && status.state == static_cast<unsigned>(State::Armed),
            "Manual flight without a path did not arm");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], selected.applied_pose[i],
                 "Manual camera jumped away from the displayed preview");
    f.manual(Mode::HoldCurrent);
    status = f.frame();
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], selected.applied_pose[i],
                 "HoldCurrent lost the manual camera");
    paused = false;
    f.clock(65, 4160);
    status = f.frame();
    require(!status.error, "Resuming replay while holding a manual camera faulted");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], selected.applied_pose[i],
                 "Resuming replay moved the held manual view");
    // Playback free-cam gate: the view's manual bit enables movement in both
    // replay states, the paused bit alone must not, and losing input or Flight
    // ownership disables it.
    require(dolly::flight_movement_active(1u | 4u, EditorOwner::Flight, true) &&
                dolly::flight_movement_active(1u | 2u | 4u, EditorOwner::Flight, true),
            "Manual flight gate does not follow the view's manual bit");
    require(!dolly::flight_movement_active(1u | 2u, EditorOwner::Flight, true) &&
                !dolly::flight_movement_active(1u | 4u, EditorOwner::Panel, true) &&
                !dolly::flight_movement_active(1u | 4u, EditorOwner::Flight, false) &&
                !dolly::flight_movement_active(4u, EditorOwner::Flight, true),
            "Manual flight gate accepted a view without ready input or ownership");
    paused = true;
    CameraPose seed = {500, 600, 12, -30, 120, 8, 1.5};
    f.manual(Mode::Manual, &seed);
    status = f.frame();
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], seed[i], "Explicit manual seed was ignored");
    // Ring never overwrites unacknowledged actions or inserts sequence gaps.
    for (unsigned i = 0; i < kEditorEventCount; ++i)
        require(editor_enqueue(EditorAction::Capture), "Editor event ring filled early");
    require(!editor_enqueue(EditorAction::Capture), "Full event ring overwrote a capture");
    editor_worker_tick(f.mapping.data(), true);
    EditorStatus events{};
    std::memcpy(&events, f.mapping.data() + kEditorStatusOffset, sizeof(events));
    EditorInputDiagnostics input{};
    std::memcpy(&input, f.mapping.data() + kEditorInputDiagnosticsOffset, sizeof(input));
    require(std::memcmp(input.magic, "DLYINP01", 8) == 0 && input.abi == 1 && input.sequence &&
                !(input.sequence & 1),
            "Optional input diagnostics were not published as a committed snapshot");
    require(input.raw_mouse_packets == dolly::gRawMousePackets.load() &&
                input.consumed_motion_frames == dolly::gConsumedMotionFrames.load() &&
                input.cursor_syncs == dolly::gCursorSyncs.load(),
            "Input diagnostic counters differ from the native input path");
    require(events.last_event == kEditorEventCount && events.dropped_events == 1,
            "Overflow changed editor event numbering");
    for (const auto& event : events.events) {
        require(event.tick == tick && event.paused, "Capture lost its replay-time snapshot");
        for (int i = 0; i < 7; ++i)
            close_to(event.pose[i], seed[i], "Capture did not contain the displayed camera");
    }
    EditorConfig config{};
    std::memcpy(&config, f.mapping.data() + kEditorConfigOffset, sizeof(config));
    config.sequence += 2;
    config.ack_event = kEditorEventCount;
    config.sensitivity = 10;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    // Force the producer lock busy exactly when a fresh config/ack is read.
    require(!dolly::gEventLock.test_and_set(std::memory_order_acquire),
            "Synthetic event lock was already occupied");
    editor_worker_tick(f.mapping.data(), true);
    require(dolly::gAcknowledged == 0, "Worker mutated the ring without its lock");
    dolly::gEventLock.clear(std::memory_order_release);
    editor_worker_tick(f.mapping.data(), true); // same config sequence: retry ack
    require(dolly::gAcknowledged == kEditorEventCount,
            "Contended acknowledgement was lost after accepting config");
    close_to(editor_snapshot().sensitivity, 10,
             "Native sensitivity limit differs from Python settings");
    require(editor_enqueue(EditorAction::Capture), "Acknowledged ring space was not reusable");
    // An action that arrives while the worker briefly holds the ring waits
    // for it instead of being dropped.
    {
        std::atomic<bool> go{false};
        std::thread holder([&go] {
            while (!go.load(std::memory_order_acquire)) {
            }
            const auto until =
                std::chrono::steady_clock::now() + std::chrono::microseconds(100);
            while (std::chrono::steady_clock::now() < until) {
            }
            dolly::gEventLock.clear(std::memory_order_release);
        });
        const auto dropped = dolly::gDropped.load();
        const auto serial = dolly::gLastEvent;
        require(!dolly::gEventLock.test_and_set(std::memory_order_acquire),
                "Synthetic event lock was already occupied");
        go.store(true, std::memory_order_release);
        const bool queued = editor_enqueue(EditorAction::Capture);
        holder.join();
        dolly::gEventLock.clear(std::memory_order_release);
        require(queued && dolly::gDropped.load() == dropped && dolly::gLastEvent == serial + 1,
                "A briefly held event ring dropped an editor action");
        dolly::gLastEvent = serial; // Leave the ring as the following checks expect.
    }
    // Playback settings use appended event IDs and the existing shared
    // configuration; they must never alter the live camera clock.
    config.sequence += 2;
    config.playback_speed = .1;
    config.playback_rate = 120;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    editor_worker_tick(f.mapping.data(), true);
    close_to(editor_snapshot().playback_speed, .1,
             "In-game playback speed differs from the desktop setting");
    require(editor_snapshot().playback_rate == 120,
            "In-game update rate differs from the desktop setting");
    // Optional Follow controls must validate identities before queuing a stock selector.
    {
        const auto saved_roster = std::atomic_load(&dolly::gRoster);
        auto roster = std::make_shared<EditorRoster>();
        roster->count = 1;
        roster->players[0].handle = 0x10002;
        roster->players[0].entity_index = 2;
        roster->players[0].model = 0x123456789abcdef0ull;
        std::atomic_store(&dolly::gRoster, std::shared_ptr<const EditorRoster>(roster));
        EditorFollowConfig follow{};
        std::memcpy(follow.magic, "DLYFOLL1", 8);
        follow.sequence = 2;
        follow.abi = 1;
        follow.flags = 1;
        follow.distance = 200;
        follow.shoulder = 40;
        follow.height = 20;
        std::memcpy(f.mapping.data() + kEditorFollowOffset, &follow, sizeof(follow));
        editor_worker_tick(f.mapping.data(), true);
        require(editor_snapshot().follow_available && editor_snapshot().follow_distance == 200,
                "Follow configuration did not publish");
        editor_set_owner(EditorOwner::Panel);
        CameraPose request{40, 20, 0, 0x10002, 2, 0x9abcdef0, 0x12345678};
        require(!editor_enqueue(EditorAction::StartGameFollow, 401, &request),
                "Follow distance escaped its reviewed range");
        request[3] = 0x18002;
        require(!editor_enqueue(EditorAction::StartGameFollow, 200, &request),
                "Follow accepted a recycled target handle");
        request[3] = 0x10002;
        request[6] = 0;
        require(!editor_enqueue(EditorAction::StartGameFollow, 200, &request),
                "Follow accepted a changed model identity");
        request[6] = 0x12345678;
        const auto serial = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::StartGameFollow, 200, &request),
                "Verified Follow request failed to queue");
        require(dolly::gEvents[serial % kEditorEventCount].action == 86,
                "Follow action differs from Python");
        follow.sequence = 4;
        follow.distance = 401;
        std::memcpy(f.mapping.data() + kEditorFollowOffset, &follow, sizeof(follow));
        editor_worker_tick(f.mapping.data(), true);
        require(editor_snapshot().follow_distance == 200,
                "Invalid Follow block replaced the last valid settings");
        std::atomic_store(&dolly::gRoster, saved_roster);
        std::atomic_store(&dolly::gFollowConfig, std::shared_ptr<const EditorFollowConfig>{});
    }
    // A shot seek queues one bounded request; it cannot bypass UI ownership,
    // the authored timeline, busy work or active path playback.
    {
        auto saved_config = config;
        config.sequence += 2;
        config.duration = 2;
        config.playhead = .375;
        config.camera_count = 2;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        editor_worker_tick(f.mapping.data(), true);
        editor_set_owner(EditorOwner::Panel);
        close_to(editor_snapshot().playhead, .375, "Shot cursor differs from desktop");
        require(
            !editor_enqueue(EditorAction::SeekShot, -1) &&
                !editor_enqueue(EditorAction::SeekShot, 2.01) &&
                !editor_enqueue(EditorAction::SeekShot, std::numeric_limits<double>::quiet_NaN()),
            "Invalid shot time entered the event queue");
        const auto seek_serial = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::SeekShot, .75), "Shot seek could not queue");
        const auto& seek_event = dolly::gEvents[seek_serial % kEditorEventCount];
        require(seek_event.action == 75 && seek_event.value == .75,
                "Shot seek action differs from Python");
        // The Shot slider holds a float. At the end of a 2.7 s shot it reads
        // just past the duration; that is still a seek to the last moment.
        {
            config.sequence += 2;
            config.duration = 2.7;
            std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
            editor_worker_tick(f.mapping.data(), true);
            const double slider_end = double(float(2.7));
            require(slider_end > 2.7, "Float rounding fixture no longer rounds up");
            const auto end_serial = dolly::gLastEvent;
            require(editor_enqueue(EditorAction::SeekShot, slider_end) &&
                        dolly::gEvents[end_serial % kEditorEventCount].value == 2.7,
                    "Seeking from the end of the Shot slider was refused");
            require(!editor_enqueue(EditorAction::SeekShot, 2.71),
                    "A time past the shot entered the event queue");
            dolly::gLastEvent = end_serial;
            config.sequence += 2;
            config.duration = 2;
            std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
            editor_worker_tick(f.mapping.data(), true);
        }
        const auto step_serial = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::StepReplayTicks, -25), "Tick step could not queue");
        require(dolly::gEvents[step_serial % kEditorEventCount].action == 76 &&
                    dolly::gEvents[step_serial % kEditorEventCount].value == -25,
                "Tick step action differs from Python");
        const auto reset_serial = dolly::gLastEvent;
        require(editor_enqueue(EditorAction::ResetCameraPath), "Camera reset could not queue");
        require(dolly::gEvents[reset_serial % kEditorEventCount].action == 77,
                "Camera reset action differs from Python");
        require(!editor_enqueue(EditorAction::ResetCameraPath, 1),
                "Invalid camera reset value entered event queue");
        require(!editor_enqueue(EditorAction::StepReplayTicks, 0) &&
                    !editor_enqueue(EditorAction::StepReplayTicks, 3) &&
                    !editor_enqueue(EditorAction::StepReplayTicks, 1.5),
                "Invalid tick step entered event queue");
        editor_set_owner(EditorOwner::Flight);
        require(!editor_enqueue(EditorAction::StepReplayTicks, 1),
                "Tick step bypassed panel ownership");
        require(!editor_enqueue(EditorAction::ResetCameraPath),
                "Camera reset bypassed panel ownership");
        require(!editor_enqueue(EditorAction::SeekShot, .75), "Shot seek bypassed panel ownership");
        editor_set_owner(EditorOwner::Panel);
        for (auto flags : {1u, 2u}) {
            config.sequence += 2;
            config.playback_flags = flags;
            std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
            editor_worker_tick(f.mapping.data(), true);
            require(!editor_enqueue(EditorAction::SeekShot, .75),
                    "Busy/playing shot accepted a seek");
            require(!editor_enqueue(EditorAction::StepReplayTicks, 1),
                    "Busy/playing shot accepted a tick step");
            require(!editor_enqueue(EditorAction::ResetCameraPath),
                    "Busy/playing shot accepted a camera reset");
        }
        saved_config.sequence = config.sequence + 2;
        config = saved_config;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        editor_worker_tick(f.mapping.data(), true);
        editor_set_owner(EditorOwner::Flight);
    }
    {
        const auto saved_config = config;
        require(!editor_snapshot().pause_after_capture,
                "Capture pause preference is enabled without its published flag");
        config.sequence += 2;
        config.playback_flags = kEditorPauseAfterCapture;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        editor_worker_tick(f.mapping.data(), true);
        editor_set_owner(EditorOwner::Panel);
        require(editor_snapshot().pause_after_capture && !editor_snapshot().playing &&
                    !editor_snapshot().busy,
                "Published capture pause preference was rejected or changes playback state");
        dolly::gAcknowledged = dolly::gLastEvent;
        for (double value : {1., 0.}) {
            const auto serial = dolly::gLastEvent;
            require(editor_enqueue(EditorAction::SetPauseAfterCapture, value),
                    "Valid capture pause preference cannot enter the event queue");
            require(dolly::gEvents[serial % kEditorEventCount].action == 110 &&
                        dolly::gEvents[serial % kEditorEventCount].value == value,
                    "Capture pause preference action differs from Python");
        }
        require(!editor_enqueue(EditorAction::SetPauseAfterCapture, -1) &&
                    !editor_enqueue(EditorAction::SetPauseAfterCapture, .5) &&
                    !editor_enqueue(EditorAction::SetPauseAfterCapture, 2) &&
                    !editor_enqueue(EditorAction::SetPauseAfterCapture,
                                    std::numeric_limits<double>::quiet_NaN()) &&
                    !editor_enqueue(EditorAction::SetPauseAfterCapture, 1, &first),
                "Malformed capture pause preference entered the event queue");
        editor_set_owner(EditorOwner::Flight);
        require(!editor_enqueue(EditorAction::SetPauseAfterCapture, 1),
                "Capture pause preference bypassed panel ownership");
        editor_set_owner(EditorOwner::Panel);
        config.sequence += 2;
        config.scene_appearance_flags = 7;
        config.playback_flags = kEditorPauseAfterCapture | 2;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        editor_worker_tick(f.mapping.data(), true);
        require(!editor_enqueue(EditorAction::SetPauseAfterCapture, 1),
                "Busy editor changed capture pause preference");
        require(editor_snapshot().scene_appearance_flags == 7,
                "Scene appearance state was lost in the desktop/native publication");
        config.sequence += 2;
        config.scene_appearance_flags = 8;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        editor_worker_tick(f.mapping.data(), true);
        require(editor_snapshot().scene_appearance_flags == 7,
                "Unknown scene appearance flags replaced a valid publication");
        config.scene_appearance_flags = 7;
        config.sequence += 2;
        config.playback_flags = 8;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        editor_worker_tick(f.mapping.data(), true);
        require(editor_snapshot().pause_after_capture && editor_snapshot().busy,
                "Unknown playback flag replaced a valid publication");
        const auto next_sequence = config.sequence + 2;
        config = saved_config;
        config.sequence = next_sequence;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        editor_worker_tick(f.mapping.data(), true);
        editor_set_owner(EditorOwner::Flight);
        dolly::gAcknowledged = dolly::gLastEvent;
    }
    const auto before_settings = dolly::gLastEvent;
    require(editor_enqueue(EditorAction::DestroyRagdolls),
            "Ragdoll cleanup could not enter the editor event queue");
    require(dolly::gEvents[before_settings % kEditorEventCount].action == 39,
            "Ragdoll cleanup action ID differs from Python");
    const auto before_playback_settings = dolly::gLastEvent;
    require(!editor_enqueue(EditorAction::SetPlaybackSpeed, 0) &&
                !editor_enqueue(EditorAction::SetPlaybackSpeed, 4.1),
            "Invalid playback speed entered the event queue");
    require(!editor_enqueue(EditorAction::SetPlaybackRate, 90),
            "Invalid playback rate entered the event queue");
    require(editor_enqueue(EditorAction::SetPlaybackSpeed, .25) &&
                editor_enqueue(EditorAction::SetPlaybackRate, 60),
            "Playback controls could not queue valid choices");
    require(dolly::gLastEvent == before_playback_settings + 2,
            "Playback control validation changed event numbering");
    const auto& speed_event = dolly::gEvents[before_playback_settings % kEditorEventCount];
    const auto& rate_event = dolly::gEvents[(before_playback_settings + 1) % kEditorEventCount];
    require(speed_event.action == 29 && speed_event.value == .25 && rate_event.action == 30 &&
                rate_event.value == 60,
            "Playback action IDs or values differ from Python");
    config.sequence += 2;
    config.playback_flags = 1;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    editor_worker_tick(f.mapping.data(), true);
    require(editor_enqueue(EditorAction::SetPlaybackSpeed, .5),
            "Live playback speed was rejected during a playing shot");
    require(!editor_enqueue(EditorAction::SetPlaybackRate, 30),
            "The update rate changed during a playing shot");
    config.sequence += 2;
    config.playback_flags = 0;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    editor_worker_tick(f.mapping.data(), true);
    f.manual();
    dolly::gInput = false;
    status = f.frame();
    require(status.error == 19, "Manual flight bypassed missing input hooks");
    f.unchanged();
    dolly::gInput = true;
    f.manual();
    seeking = true;
    status = f.frame();
    require(status.error == 12, "Manual flight ignored a replay seek");
    f.unchanged();
    seeking = false;
    f.manual();
    gHeartbeatTime = now_seconds() - 3;
    status = f.frame(0, false);
    require(status.error == 10, "Manual flight retained camera after editor loss");
    f.unchanged();
    f.command(Mode::Release);
    f.frame();
    effect_checks(f);
    editor_cursor_startup_checks();
    editor_input_checks();
    editor_framing_checks();
    editor_object_checks();
    // The depth master uses video_flags bit 1. The worker must accept the whole
    // config with that bit set; rejecting it silently kept the old config, so
    // the playing state and in-game toggles never applied.
    {
        auto base = std::atomic_load(&dolly::gConfig);
        require(base != nullptr, "Synthetic editor config missing");
        auto config = std::make_shared<EditorConfig>(*base);
        config->sequence += 2;
        config->video_flags = 63;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, config.get(), sizeof(*config));
        editor_worker_tick(f.mapping.data(), true);
        auto applied = std::atomic_load(&dolly::gConfig);
        require(applied && applied->video_flags == 63 && editor_snapshot().video_depth &&
                    editor_snapshot().video_depth_exr && editor_snapshot().video_layer_world &&
                    editor_snapshot().video_layer_players && editor_snapshot().video_layer_effects,
                "Depth video flag was rejected with the whole config");
    }
    // Camera export formats live in their own byte
    {
        auto base = std::atomic_load(&dolly::gConfig);
        auto config = std::make_shared<EditorConfig>(*base);
        config->sequence += 2;
        config->video_flags = 255;
        config->camera_export_flags = 1;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, config.get(), sizeof(*config));
        editor_worker_tick(f.mapping.data(), true);
        auto applied = std::atomic_load(&dolly::gConfig);
        require(applied && applied->camera_export_flags == 1 && editor_snapshot().video_usd_camera &&
                    editor_snapshot().video_ae_camera,
                "USD camera export flag was rejected with the whole config");
        config->sequence += 2;
        config->camera_export_flags = 3;  // bit 1: a RenderLock .crav export is running
        std::memcpy(f.mapping.data() + kEditorConfigOffset, config.get(), sizeof(*config));
        editor_worker_tick(f.mapping.data(), true);
        require(std::atomic_load(&dolly::gConfig)->camera_export_flags == 3 &&
                    editor_snapshot().renderlock_exporting && editor_snapshot().video_usd_camera,
                "RenderLock export state flag was rejected with the whole config");
        config->sequence += 2;
        config->camera_export_flags = 4;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, config.get(), sizeof(*config));
        editor_worker_tick(f.mapping.data(), true);
        require(std::atomic_load(&dolly::gConfig)->camera_export_flags == 3,
                "An unknown camera export flag was accepted");
        config->sequence += 2;
        config->video_flags = 0;
        config->camera_export_flags = 0;
        std::memcpy(f.mapping.data() + kEditorConfigOffset, config.get(), sizeof(*config));
        editor_worker_tick(f.mapping.data(), true);
        require(!editor_snapshot().video_usd_camera, "USD camera export did not turn off");
    }

    // Run the actual camera callback against the changed view layout. Guard
    // bytes around the new auxiliary block detect writes using old offsets.
    gCameraCache.view_layout = camera_view::layout(1);
    const auto updated_view = f.view.address() + 0x10;
    put(updated_view + 0x578, std::uint32_t(0xaabbccdd));
    f.command(Mode::Hold, .25);
    status = f.frame(4);
    require(status.error == 0, "Updated camera callback failed its synthetic view check");
    f.applied(.25);
    require(*reinterpret_cast<const std::uint32_t*>(updated_view + 0x578) == 0xaabbccdd,
            "Updated camera wrote the old extra-origin tail beyond its reviewed layout");
    f.command(Mode::Release);
    f.frame();
    f.unchanged();
    editor_heartbeat_reconnect_checks();

    std::cout
        << "Actual native callback smoke tests passed (synthetic Windows memory; no game runtime claim)\n";
}

} // namespace smoke

int main(int argc, char** argv) {
    try {
        smoke::bone_vector_selection();
        if ((argc == 4 || argc == 6) && std::strcmp(argv[1], "--camera-layout") == 0) {
            // Static verification only: SEC_IMAGE_NO_EXECUTE never invokes
            // DllMain, resolves imports or executes the inspected game image.
            HANDLE file = CreateFileA(argv[2], GENERIC_READ, FILE_SHARE_READ, nullptr,
                                      OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
            smoke::require(file != INVALID_HANDLE_VALUE,
                           "Could not read the supplied client image");
            HANDLE mapping = CreateFileMappingW(file, nullptr, PAGE_READONLY | SEC_IMAGE_NO_EXECUTE,
                                                0, 0, nullptr);
            CloseHandle(file);
            smoke::require(mapping != nullptr, "Could not map the client for static inspection");
            void* image = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 0);
            CloseHandle(mapping);
            smoke::require(image != nullptr, "Could not view the client image");
            const auto base = reinterpret_cast<std::uintptr_t>(image);
            const auto setup = std::stoull(argv[3], nullptr, 16);
            const unsigned revision = argc == 6 ? unsigned(std::stoul(argv[4])) : 0;
            const auto delegate = argc == 6 ? base + std::stoull(argv[5], nullptr, 16) : 0;
            const auto result =
                camera_view::resolve(static_cast<HMODULE>(image), base + setup, revision, delegate);
            if (result.basis)
                std::printf(
                    "Camera layout verified: origin=%llx angles=%llx forward=%llx right=%llx up=%llx basis=%llx\n",
                    result.origin - base, result.angles - base, result.forward - base,
                    result.right - base, result.up - base,
                    reinterpret_cast<std::uintptr_t>(result.basis) - base);
            UnmapViewOfFile(image);
            smoke::require(result.basis != nullptr, "Camera cache layout rejected");
            return 0;
        }
        smoke::run();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Native callback smoke test failed: " << error.what() << '\n';
        return 1;
    }
}
