// Included after checked memory, replay and editor helpers, inside bridge scope.
// Exact reviewed profiles. Correct a private blend input, never persistent game state.
#include "dolly_follow_anchor_generated.hpp"

const FollowAnchorProfile* gFollowAnchorProfile = &kFollowAnchorProfiles[0];

using FollowCompositionFn = void(__fastcall*)(void*, void*, float, dolly::FollowDescriptor*);
using FollowBlendFn = void(__fastcall*)(float, const dolly::FollowDescriptor*,
                                        const dolly::FollowDescriptor*, dolly::FollowDescriptor*);
FollowCompositionFn gOriginalFollowComposition = nullptr;
FollowBlendFn gOriginalFollowBlend = nullptr;
std::atomic<bool> gFollowAnchorInstalled{false};
std::atomic<std::uint64_t> gFollowAnchorScopes{0}, gFollowAnchorBlends{0},
    gFollowAnchorCorrections{0}, gFollowAnchorRejected{0};
std::atomic<std::uint32_t> gFollowAnchorTarget{0};

struct FollowAnchorContext {
    std::uintptr_t camera = 0, pawn = 0;
    std::uint32_t sequence = 0, handle = 0;
    std::array<float, 3> anchor{};
    std::shared_ptr<const Command> command;
};
thread_local const FollowAnchorContext* gFollowAnchorContext = nullptr;

static bool follow_anchor_enabled() noexcept {
    CvarRef ref{}, after{};
    std::uintptr_t name = 0;
    std::uint16_t type = 0;
    std::uint64_t flags = 0;
    unsigned char enabled = 0;
    constexpr char expected[] = "citadel_camera_overrides_enabled";
    char actual[sizeof(expected)]{};
    constexpr auto blocked = (1ull << 2) | (1ull << 9) | (1ull << 10) | (1ull << 13) |
                             (1ull << 15) | (1ull << 18) | (1ull << 22);
    return read_value(gClient + gFollowAnchorProfile->rig, ref) && (ref.id & 0xffff) != 0xffff &&
           ref.data && read_value(ref.data, name) && read_memory(name, actual, sizeof(actual)) &&
           !std::memcmp(actual, expected, sizeof(actual)) && read_value(ref.data + 0x28, type) &&
           type == 0 && read_value(ref.data + 0x30, flags) && !(flags & blocked) &&
           (flags & 0x80080) == 0x80080 && read_value(ref.data + 0x58, enabled) && enabled == 1 &&
           read_value(gClient + gFollowAnchorProfile->rig, after) && after.id == ref.id &&
           after.data == ref.data;
}

// Bind the exact current ThirdPerson camera, observer serial and tracked pawn.
// The local player-pawn alternative is explicitly opted into by each profile.
static bool follow_anchor_target(std::uintptr_t camera, std::uintptr_t pawn,
                                 std::uint32_t& target) noexcept {
    std::uintptr_t current = 0, table = 0, controller = 0, system = 0, observer = 0, services = 0;
    unsigned char blending = 0, mode = 0;
    std::uint32_t observer_handle = 0, camera_handle = 0;
    if (!read_value(gClient + gFollowAnchorProfile->manager, table) ||
        table != gClient + gFollowAnchorProfile->manager_table ||
        !read_value(gClient + gFollowAnchorProfile->manager + 0x28, current) || current != camera ||
        !read_value(gClient + gFollowAnchorProfile->manager + 0x38, blending) || blending ||
        !read_value(camera, table) || table != gClient + gFollowAnchorProfile->camera_table ||
        !read_value(gClient + gFollowAnchorProfile->controller, controller) || !controller ||
        !read_value(controller, table) ||
        table != gClient + gFollowAnchorProfile->controller_table ||
        !read_value(controller + 0x6bc, observer_handle) ||
        !read_value(gClient + gFollowAnchorProfile->entity_list, system) || !system)
        return false;
    const auto resolve = [&](std::uint32_t handle, std::uintptr_t& instance) {
        auto index = handle & 0x7fff;
        std::uintptr_t chunk = 0, identity = 0, back = 0;
        std::uint32_t actual = 0;
        if (handle == 0xffffffff || handle == 0xfffffffe || index >= 0x7fff ||
            !read_value(system + 8 * (index >> 9), chunk) || !chunk)
            return false;
        identity = chunk + 0x70 * (index & 0x1ff);
        return read_value(identity, instance) && instance && read_value(identity + 0x10, actual) &&
               actual == handle && read_value(instance + 0x10, back) && back == identity;
    };
    if (!resolve(observer_handle, observer) || !read_value(observer, table))
        return false;
    const auto observer_type = table;
    if ((table != gClient + gFollowAnchorProfile->observer_table &&
         (!gFollowAnchorProfile->local_player_table ||
          table != gClient + gFollowAnchorProfile->local_player_table)) ||
        !read_value(observer + gFollowAnchorProfile->observer_services_offset, services) ||
        !services || !read_value(services, table) ||
        (table != gClient + gFollowAnchorProfile->services_table &&
         table != gClient + gFollowAnchorProfile->observer_base) ||
        !read_value(services + 0x48, mode) || (mode != 2 && mode != 3) ||
        !read_value(services + 0x4c, target) || !read_value(camera + 0xb0, camera_handle) ||
        camera_handle != target)
        return false;
    const auto service_type = table;
    std::uintptr_t selected = 0;
    if (!resolve(target, selected) || selected != pawn || !read_value(pawn, table) ||
        table != gClient + gFollowAnchorProfile->pawn_table)
        return false;
    // Recheck the ownership chain rather than treating mixed reads as a sample.
    std::uintptr_t again = 0;
    std::uint32_t handle_again = 0;
    unsigned char mode_again = 0;
    return read_value(gClient + gFollowAnchorProfile->manager, again) &&
           again == gClient + gFollowAnchorProfile->manager_table && read_value(camera, again) &&
           again == gClient + gFollowAnchorProfile->camera_table &&
           read_value(gClient + gFollowAnchorProfile->manager + 0x38, mode_again) &&
           mode_again == 0 && read_value(gClient + gFollowAnchorProfile->manager + 0x28, again) &&
           again == camera && read_value(gClient + gFollowAnchorProfile->entity_list, again) &&
           again == system && read_value(gClient + gFollowAnchorProfile->controller, again) &&
           again == controller && read_value(controller + 0x6bc, handle_again) &&
           handle_again == observer_handle && read_value(controller, again) &&
           again == gClient + gFollowAnchorProfile->controller_table &&
           resolve(observer_handle, again) && again == observer && read_value(observer, again) &&
           again == observer_type &&
           read_value(observer + gFollowAnchorProfile->observer_services_offset, again) &&
           again == services && read_value(services, again) && again == service_type &&
           read_value(services + 0x48, mode_again) && mode_again == mode &&
           read_value(services + 0x4c, handle_again) && handle_again == target &&
           read_value(camera + 0xb0, handle_again) && handle_again == target &&
           resolve(target, again) && again == pawn && read_value(pawn, again) &&
           again == gClient + gFollowAnchorProfile->pawn_table;
}

static bool follow_anchor_session(dolly::EditorFollowConfig& config,
                                  std::shared_ptr<const Command>& command) noexcept {
    if (!gFollowAnchorInstalled.load() || !gEditor ||
        WaitForSingleObject(gEditor, 0) != WAIT_TIMEOUT || gWorkerError.load() ||
        now_seconds() - gHeartbeatTime.load() >= 2 || !dolly::editor_follow_config(config) ||
        !dolly::follow_anchor_flags(config.flags) || !follow_anchor_enabled())
        return false;
    command = std::atomic_load(&gCommand);
    DemoState demo{};
    // A POV segment times the take without replacing the game spectator view.
    // It needs the same anchor correction as released Follow, including arm/hold.
    const bool game_owned = command &&
        (command->wire.mode == std::uint32_t(Mode::Release) ||
         ((command->wire.flags & kGamePov) && !(command->wire.flags & (kFrozen | kAspect)) &&
          !command->manual && command->path &&
          (command->wire.mode == std::uint32_t(Mode::Hold) ||
           command->wire.mode == std::uint32_t(Mode::Play) ||
           command->wire.mode == std::uint32_t(Mode::HoldCurrent))));
    return game_owned && read_demo(demo) && demo.playing && !demo.seeking &&
           same_demo(command->wire.demo_name, demo.name);
}

__declspec(noinline) static void __fastcall follow_composition_hook(
    void* camera, void* pawn, float delta, dolly::FollowDescriptor* descriptor) {
    FollowAnchorContext context{};
    dolly::EditorFollowConfig config{};
    dolly::FollowDescriptor input{};
    context.camera = reinterpret_cast<std::uintptr_t>(camera);
    context.pawn = reinterpret_cast<std::uintptr_t>(pawn);
    const auto previous = gFollowAnchorContext;
    // Every invocation scopes its own context, including nested rejected calls.
    gFollowAnchorContext = nullptr;
    if (follow_anchor_session(config, context.command) &&
        follow_anchor_target(context.camera, context.pawn, context.handle) &&
        read_value(reinterpret_cast<std::uintptr_t>(descriptor), input)) {
        context.sequence = config.sequence;
        std::copy_n(input.begin() + 3, 3, context.anchor.begin());
        if (std::all_of(context.anchor.begin(), context.anchor.end(),
                        [](float v) { return std::isfinite(v); })) {
            gFollowAnchorContext = &context;
            ++gFollowAnchorScopes;
        }
    }
    struct RestoreContext {
        const FollowAnchorContext* previous;
        ~RestoreContext() { gFollowAnchorContext = previous; }
    } restore{previous};
    gOriginalFollowComposition(camera, pawn, delta, descriptor);
}

__declspec(noinline) static void __fastcall follow_blend_hook(float weight,
                                                              const dolly::FollowDescriptor* a,
                                                              const dolly::FollowDescriptor* b,
                                                              dolly::FollowDescriptor* output) {
    const auto caller = reinterpret_cast<std::uintptr_t>(_ReturnAddress());
    const auto context = gFollowAnchorContext;
    if (caller == gClient + gFollowAnchorProfile->blend_caller && context) {
        ++gFollowAnchorBlends;
        dolly::EditorFollowConfig config{};
        std::shared_ptr<const Command> command;
        std::uint32_t target = 0;
        dolly::FollowDescriptor av{}, bv{};
        alignas(16) dolly::FollowDescriptor corrected{};
        float crouch = 0;
        const bool owned =
            a != b && a != output && b != output && follow_anchor_session(config, command) &&
            config.sequence == context->sequence && command == context->command &&
            follow_anchor_target(context->camera, context->pawn, target) &&
            target == context->handle && read_value(context->camera + 0x158, crouch) &&
            crouch == weight && read_value(reinterpret_cast<std::uintptr_t>(a), av) &&
            read_value(reinterpret_cast<std::uintptr_t>(b), bv);
        if (dolly::follow_anchor_copy(weight, owned, av, bv, context->anchor, corrected)) {
            gFollowAnchorTarget = target;
            ++gFollowAnchorCorrections;
            gOriginalFollowBlend(weight, &corrected, b, output);
            return;
        }
        ++gFollowAnchorRejected;
    }
    gOriginalFollowBlend(weight, a, b, output);
}

static CapabilityResult install_follow_anchor(HMODULE client) {
    gFollowAnchorProfile = nullptr;
    for (const auto& profile : kFollowAnchorProfiles)
        if (module_matches(client, profile.hash, profile.image_size)) {
            gFollowAnchorProfile = &profile;
            break;
        }
    if (!gFollowAnchorProfile)
        return {CapabilityState::NotRequired,
                CapabilityReason::None}; // Existing reviewed behavior.
    for (std::size_t index = 0; index < gFollowAnchorProfile->count; ++index) {
        const auto& span = gFollowAnchorProfile->spans[index];
        std::vector<unsigned char> actual(span.size);
        if (!read_memory(gClient + span.rva, actual.data(), actual.size()) ||
            std::memcmp(actual.data(), span.bytes, span.size))
            return {CapabilityState::Unavailable, CapabilityReason::SignatureMismatch};
    }
    struct Hooks {
        void* targets[2];
        void* callbacks[2];
        void** originals[2];
        bool create(unsigned i) {
            return MH_CreateHook(targets[i], callbacks[i], originals[i]) == MH_OK;
        }
        bool queue_enable(unsigned i) { return MH_QueueEnableHook(targets[i]) == MH_OK; }
        void queue_disable(unsigned i) { MH_QueueDisableHook(targets[i]); }
        void remove(unsigned i) { MH_RemoveHook(targets[i]); }
        void disable(unsigned i) { MH_DisableHook(targets[i]); }
        bool apply() { return MH_ApplyQueued() == MH_OK; }
    } hooks{{reinterpret_cast<void*>(gClient + gFollowAnchorProfile->composition),
             reinterpret_cast<void*>(gClient + gFollowAnchorProfile->blend)},
            {reinterpret_cast<void*>(follow_composition_hook),
             reinterpret_cast<void*>(follow_blend_hook)},
            {reinterpret_cast<void**>(&gOriginalFollowComposition),
             reinterpret_cast<void**>(&gOriginalFollowBlend)}};
    if (!install_optional_pair(hooks))
        return {CapabilityState::Unavailable, CapabilityReason::HookInstallation};
    gFollowAnchorInstalled = true;
    return {CapabilityState::Ready, CapabilityReason::None};
}

static void publish_follow_anchor(unsigned char* memory) noexcept {
    struct Diagnostics {
        char magic[8];
        std::uint32_t sequence, abi, flags, target;
        std::uint64_t scopes, blends, corrections, rejected;
    } value{};
    static_assert(sizeof(value) == 56, "Optional Follow diagnostics layout");
    std::memcpy(value.magic, "DLYFANC1", 8);
    value.abi = 1;
    value.flags = gFollowAnchorInstalled.load() ? 1 : 0;
    value.target = gFollowAnchorTarget.load();
    value.scopes = gFollowAnchorScopes.load();
    value.blends = gFollowAnchorBlends.load();
    value.corrections = gFollowAnchorCorrections.load();
    value.rejected = gFollowAnchorRejected.load();
    constexpr std::size_t offset = 2 * 1024 * 1024 + 23088;
    auto out = memory + offset;
    auto sequence = reinterpret_cast<volatile LONG*>(out + 8);
    auto old = InterlockedCompareExchange(sequence, 0, 0);
    auto even = (old & 1) ? old + 1 : old;
    InterlockedExchange(sequence, even + 1);
    value.sequence = even + 2;
    std::memcpy(out, value.magic, 8);
    std::memcpy(out + 12, reinterpret_cast<unsigned char*>(&value) + 12, sizeof(value) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, even + 2);
}
