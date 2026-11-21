#pragma once
// Attach camera runtime provider (POV / weapon). Included from bridge_win.cpp
// inside its anonymous namespace after the checked-memory helpers, the compat
// runtime and the editor API.
//
// The worker thread resolves one target per command with a bounded read-only
// entity walk; the render callback re-validates identity with a few small
// reads and never allocates, locks or walks the entity list. Nothing here
// writes game memory. A missing target, moved pointer or unreadable pose fails
// closed with a static message instead of rendering a guessed camera.

#include <cmath>

namespace attach_runtime {

// Build 6712 moved the entity-system global outside the legacy search window.
// Select this reviewed location once, after the bridge's module checks. Runtime
// resolution still validates the live object's primary RTTI vtable every time.
inline HMODULE configured_client = nullptr;
inline bool september_client = false;
inline bool september_entity_code = false;
inline bool september_hotfix = false;
inline bool september_latest = false;
inline bool september_6726 = false;
inline bool september_6730 = false;
inline bool september_6731 = false;
inline bool september_6739 = false;
inline bool september_6742 = false;
inline bool september_6745 = false;
inline void configure_client(HMODULE client) {
    configured_client = client;
    september_6745 = module_matches(client, dolly::reviewed::AttachRuntime::CLIENT_SHA256,
                                    dolly::reviewed::AttachRuntime::CLIENT_IMAGE_SIZE);
    september_6742 = module_matches(
        client, "255395880ac91d37c8b71124906f4f48737159a8cf4c9d1732035d1e27927657", 0x4150000);
    september_6739 = module_matches(
        client, "9979035a0157de0243019c27ad36e3f7ac89b4bb2cfc769dca867f084fcce613", 0x4150000);
    september_6731 = module_matches(
        client, "cb244664a4b42057b02788bc95be489140fbba587266fdb40c5faf7d4d0784e3", 0x40fa000);
    september_6730 = module_matches(
        client, "5c5ef78cf648066d78cf0f8155647eb3521bf31c3cf9607e4c304b121fec6647", 0x40fa000);
    september_6726 = module_matches(
        client, "07f65ab6f862517ef6b1049679f78342d572acc589cba54e9851d61380b19e6e", 0x40f7000);
    september_latest = module_matches(
        client, "14a1f187b4dbe0801c805cfb4050dff495601867b1f0c7a21889d47c633a4bf3", 0x40f5000);
    september_hotfix = module_matches(
        client, "44a50bc28e7a49f52e725b95a62a7046fcbc99cc9107beae4b448cccbd52dbbc", 0x40f5000);
    september_client =
        module_matches(client, "bc0dae383a2cd65dc1616515cdffa6c947fd057e5590edf0f5a01bd953ec19c9",
                       0x40f4000) ||
        september_hotfix || september_latest || september_6726 || september_6730 || september_6731 ||
        september_6739 || september_6742 || september_6745;
    september_entity_code = false;
    if (!september_client)
        return;
    unsigned char initializer[] = {0x0f, 0xb6, 0x44, 0x24, 0x28, 0x88, 0x44, 0x24, 0x28, 0x48, 0x89,
                                   0x0d, 0xc0, 0x7d, 0xcf, 0x01, 0xe9, 0x9b, 0xbf, 0xff, 0xff};
    unsigned char actual[sizeof(initializer)]{};
    if (september_hotfix)
        initializer[12] = 0xc0, initializer[13] = 0x7c;
    const unsigned char latest_initializer[] = {0x0f, 0xb6, 0x44, 0x24, 0x28, 0x88, 0x44,
                                                0x24, 0x28, 0x48, 0x89, 0x0d, 0x90, 0x76,
                                                0xcf, 0x01, 0xe9, 0x9b, 0xbf, 0xff, 0xff};
    const unsigned char initializer6726[] = {0x0f, 0xb6, 0x44, 0x24, 0x28, 0x88, 0x44,
                                             0x24, 0x28, 0x48, 0x89, 0x0d, 0xc0, 0x64,
                                             0xcf, 0x01, 0xe9, 0x9b, 0xbf, 0xff, 0xff};
    const unsigned char initializer6730[] = {0x0f, 0xb6, 0x44, 0x24, 0x28, 0x88, 0x44,
                                             0x24, 0x28, 0x48, 0x89, 0x0d, 0x50, 0x88,
                                             0xcf, 0x01, 0xe9, 0x9b, 0xbf, 0xff, 0xff};
    const unsigned char initializer6731[] = {0x0f, 0xb6, 0x44, 0x24, 0x28, 0x88, 0x44,
                                             0x24, 0x28, 0x48, 0x89, 0x0d, 0x70, 0x80,
                                             0xcf, 0x01, 0xe9, 0x9b, 0xbf, 0xff, 0xff};
    const unsigned char initializer6739[] = {0x0f, 0xb6, 0x44, 0x24, 0x28, 0x88, 0x44,
                                             0x24, 0x28, 0x48, 0x89, 0x0d, 0xb0, 0x11,
                                             0xd2, 0x01, 0xe9, 0x9b, 0xbf, 0xff, 0xff};
    const unsigned char initializer6742[] = {0x0f, 0xb6, 0x44, 0x24, 0x28, 0x88, 0x44,
                                             0x24, 0x28, 0x48, 0x89, 0x0d, 0x50, 0x11,
                                             0xd2, 0x01, 0xe9, 0x9b, 0xbf, 0xff, 0xff};
    september_entity_code =
        read_memory(reinterpret_cast<std::uintptr_t>(client) +
                        (september_6745     ? dolly::reviewed::AttachRuntime::INITIALIZER
                         : september_6742   ? 0x20318d0
                         : september_6739   ? 0x2031870
                         : september_6731   ? 0x200a320
                         : september_6730   ? 0x2009b40
                         : september_6726   ? 0x20088d0
                         : september_latest ? 0x2005120
                         : september_hotfix ? 0x2004af0
                                            : 0x20049f0),
                    actual, sizeof(actual)) &&
        std::memcmp(actual,
                    september_6745     ? dolly::reviewed::AttachRuntime::INITIALIZER_BYTES
                    : september_6742   ? initializer6742
                    : september_6739   ? initializer6739
                    : september_6731   ? initializer6731
                    : september_6730   ? initializer6730
                    : september_6726   ? initializer6726
                    : september_latest ? latest_initializer
                                       : initializer,
                    sizeof(actual)) == 0;
}

struct Offsets {
    std::uint32_t scene_node = 0, owner = 0, origin = 0, angles = 0, view_offset = 0,
                  eye_angles = 0, child = 0, sibling = 0;
};

struct Cache {
    bool ready = false;
    dolly::AttachResolution resolution;
    std::uintptr_t pawn = 0, identity = 0, node = 0, name_pointer = 0;
    std::uint32_t name_offset = 0, handle = 0, entity_index = 0;
    std::uint64_t model = 0;
    // Weapon point: resolved bone index and its per-frame transform array.
    std::uintptr_t bone_array = 0;
    std::uintptr_t model_handle_slot = 0, model_handle = 0, model_object = 0, model_state = 0;
    std::uint32_t bone_count = 0;
    std::uint32_t bone_index = 0;
    std::uint32_t root_index = 0;
    bool bone_ready = false;
    dolly::EditorBones bones{};
    std::shared_ptr<dolly::PickerCatalog> picker;
    const char* error = nullptr;
};

inline Offsets offsets_from(const EditorAttachConfig& config) noexcept {
    Offsets offsets;
    offsets.scene_node =
        config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::scene_node)];
    offsets.owner = config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::owner)];
    offsets.origin =
        config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::player_origin)];
    offsets.angles =
        config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::player_angles)];
    offsets.view_offset =
        config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::eye_offset)];
    offsets.eye_angles =
        config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::eye_angles)];
    offsets.child =
        config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::scene_child)];
    offsets.sibling =
        config.offsets[static_cast<unsigned>(dolly::reviewed::AttachFieldIndex::scene_sibling)];
    return offsets;
}

// FNV-1a 64 over a model path, matching dolly.native_effects.model_token.
inline std::uint64_t model_token(const char* text) noexcept {
    std::uint64_t value = 14695981039346656037ull;
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(text); *p; ++p) {
        value ^= *p;
        value *= 1099511628211ull;
    }
    return value;
}

inline bool text_looks_like_model(const char* text) noexcept {
    if (std::strncmp(text, "models/", 7) != 0)
        return false;
    std::size_t length = 0;
    while (length < 255 && text[length]) {
        const unsigned char c = static_cast<unsigned char>(text[length]);
        if (c < 32 || c > 126)
            return false;
        ++length;
    }
    return length >= 11 && length < 255 && std::strcmp(text + length - 5, ".vmdl") == 0;
}

struct PlayerEntity {
    std::uintptr_t instance = 0, identity = 0;
    std::uint32_t handle = 0;
};

inline bool read_class_name(std::uintptr_t identity, char* out, std::size_t capacity) noexcept {
    std::uintptr_t pointer = 0;
    if (!capacity || !read_value(identity + 0x20, pointer) || !pointer)
        return false;
    unsigned char raw[64]{};
    if (!read_memory(pointer, raw, sizeof(raw) - 1))
        return false;
    std::memcpy(out, raw, capacity - 1);
    out[capacity - 1] = 0;
    return true;
}

inline bool identity_list_has_player(std::uintptr_t entity_system, unsigned head) noexcept {
    std::uintptr_t identity = 0;
    if (!read_value(entity_system + head, identity))
        return false;
    for (unsigned step = 0; step < 64 && identity; ++step) {
        char name[64]{};
        if (read_class_name(identity, name, sizeof(name)) && std::strcmp(name, "player") == 0)
            return true;
        if (!read_value(identity + 0x58, identity))
            return false;
    }
    return false;
}

inline bool locate_entity_system(HMODULE client, std::uintptr_t& out) noexcept {
    if (!client)
        return false;
    const std::uintptr_t base = reinterpret_cast<std::uintptr_t>(client);
    IMAGE_DOS_HEADER dos{};
    IMAGE_NT_HEADERS64 nt{};
    if (!read_value(base, dos) || dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0 ||
        !read_value(base + dos.e_lfanew, nt) || nt.Signature != IMAGE_NT_SIGNATURE)
        return false;
    const std::uintptr_t size = nt.OptionalHeader.SizeOfImage;
    // The reviewed FollowTarget entity-list global is the same current
    // CGameEntitySystem object the follow resolver uses. Prefer it, with the
    // vtable check, before the legacy build-specific locator search.
    std::uintptr_t reviewed_system = 0;
    if (read_value(base + dolly::reviewed::FollowTarget::ENTITY_LIST, reviewed_system) &&
        reviewed_system) {
        // This is the exact global the reviewed Follow resolver reads. Its
        // object is not guaranteed to expose CGameEntitySystem's primary
        // vtable at offset zero, so validating that vtable here rejected the
        // same system Follow was already using. Trust the reviewed global;
        // the legacy locator below remains the fallback when it is null.
        out = reviewed_system;
        return true;
    }
    const std::uintptr_t vtable = compat_detail::locate_vtable(client, "CGameEntitySystem");
    if (!vtable)
        return false;
    if (client == configured_client && september_client) {
        std::uintptr_t candidate = 0, actual_vtable = 0;
        const std::uintptr_t reviewed_vtable = september_6745
                                                   ? dolly::reviewed::AttachRuntime::ENTITY_VTABLE
                                               : september_6742   ? 0x2a40df0
                                               : september_6739   ? 0x2a40df0
                                               : september_6731   ? 0x2a0c900
                                               : september_6730   ? 0x2a0c8d0
                                               : september_6726   ? 0x2a0af50
                                               : september_latest ? 0x2a07c00
                                               : september_hotfix ? 0x2a07c10
                                                                  : 0x2a07c30;
        if (!september_entity_code || vtable != base + reviewed_vtable ||
            !read_value(base + (september_6745   ? dolly::reviewed::AttachRuntime::ENTITY_GLOBAL
                                : september_6742 ? 0x3d52a30
                                : september_6739 ? 0x3d52a30
                                : september_6731 ? 0x3d023a0
                                : september_6730 ? 0x3d023a0
                                : september_6726 ? 0x3cfeda0
                                                 : 0x3cfc7c0),
                        candidate) ||
            !candidate || !read_value(candidate, actual_vtable) || actual_vtable != vtable)
            return false;
        out = candidate;
        return true;
    }
    constexpr std::uintptr_t previous_rva = 0x391EDA8, window = 0x40000;
    auto valid = [&](std::uintptr_t candidate) {
        std::uintptr_t first = 0;
        return candidate && read_value(candidate, first) && first == vtable;
    };
    std::uintptr_t previous = 0;
    if (read_value(base + previous_rva, previous) && valid(previous)) {
        out = previous;
        return true;
    }
    const std::uintptr_t first = base + (previous_rva > window ? previous_rva - window : 0);
    const std::uintptr_t last = base + std::min<std::uintptr_t>(size, previous_rva + window);
    std::uintptr_t fallback = 0;
    for (std::uintptr_t address = first; address + 8 <= last; address += 8) {
        const std::uintptr_t value = *reinterpret_cast<const std::uintptr_t*>(address);
        if (value < 0x10000000000ull || value > 0x7f0000000000ull || (value & 7) ||
            (value >= base && value < base + size) || !valid(value))
            continue;
        if (identity_list_has_player(value, 0x210) || identity_list_has_player(value, 0x230)) {
            out = value;
            return true;
        }
        if (!fallback)
            fallback = value;
    }
    if (fallback) {
        out = fallback;
        return true;
    }
    return false;
}

inline bool scan_identity_players(std::uintptr_t entity_system, std::vector<PlayerEntity>& out) noexcept {
    // The linked-list heads moved/renamed on 6817. Walk the regular identity
    // chunks instead, accepting only reviewed player/clone pawn instances.
    const auto base = reinterpret_cast<std::uintptr_t>(configured_client);
    if (!base)
        return false;
    const std::uintptr_t player_vtable = base + dolly::reviewed::FollowTarget::PLAYER_PAWN_VTABLE;
    const std::uintptr_t clone_vtable =
        base + dolly::reviewed::FollowTarget::FAMILIAR_CLONE_PAWN_VTABLE;
    constexpr unsigned chunk_count = 0x40;   // 32,768 identities, enough for 15-bit handles
    constexpr unsigned chunk_slots = 512;
    for (unsigned chunk_index = 0; chunk_index < chunk_count; ++chunk_index) {
        std::uintptr_t chunk = 0;
        if (!read_value(entity_system + 8 * chunk_index, chunk) || !chunk)
            continue;
        for (unsigned slot = 0; slot < chunk_slots; ++slot) {
            const std::uintptr_t identity =
                chunk + dolly::reviewed::FollowTarget::IDENTITY_STRIDE * slot;
            std::uintptr_t instance = 0, backlink = 0, vtable = 0;
            if (!read_value(identity, instance) || !instance ||
                !read_value(instance + dolly::reviewed::FollowTarget::ENTITY_IDENTITY, backlink) ||
                backlink != identity || !read_value(instance, vtable) ||
                (vtable != player_vtable && vtable != clone_vtable))
                continue;
            PlayerEntity player;
            player.instance = instance;
            player.identity = identity;
            read_value(identity + dolly::reviewed::FollowTarget::IDENTITY_HANDLE, player.handle);
            out.push_back(player);
            if (out.size() >= 64)
                return true;
        }
    }
    return !out.empty();
}

inline bool walk_players(std::uintptr_t entity_system, std::vector<PlayerEntity>& out) noexcept {
    out.clear();
    // The 6817/6819 linked-list heads can contain controller identities named
    // "player" that are not attachable pawns. Prefer the reviewed pawn-vtable
    // chunk scan, then retain the legacy list walk only as a fallback.
    if (scan_identity_players(entity_system, out))
        return true;
    std::vector<std::uintptr_t> seen;
    for (unsigned head : {0x210u, 0x230u}) {
        std::uintptr_t identity = 0;
        if (!read_value(entity_system + head, identity))
            continue;
        while (identity) {
            if (seen.size() >= 20000)
                return false;
            bool duplicate = false;
            for (std::uintptr_t visited : seen)
                if (visited == identity) {
                    duplicate = true;
                    break;
                }
            if (duplicate)
                break;
            seen.push_back(identity);
            std::uintptr_t instance = 0, backlink = 0;
            if (!read_value(identity, instance) || !instance ||
                !read_value(instance + 0x10, backlink) || backlink != identity) {
                if (!read_value(identity + 0x58, identity))
                    break;
                continue;
            }
            char name[64]{};
            if (read_class_name(identity, name, sizeof(name)) && std::strcmp(name, "player") == 0) {
                PlayerEntity player;
                player.instance = instance;
                player.identity = identity;
                read_value(identity + 0x10, player.handle);
                out.push_back(player);
            }
            if (!read_value(identity + 0x58, identity))
                break;
        }
    }
    return !out.empty();
}

inline bool discover_model_name(std::uintptr_t node, std::uint32_t& offset, std::uintptr_t& pointer,
                                std::uint64_t& token) noexcept {
    for (std::uintptr_t candidate = 0x100; candidate + 8 <= 0x400; candidate += 8) {
        std::uintptr_t value = 0;
        if (!read_value(node + candidate, value) || value < 0x10000)
            continue;
        char text[128]{};
        if (!read_memory(value, text, sizeof(text) - 1))
            continue;
        text[sizeof(text) - 1] = 0;
        if (!text_looks_like_model(text))
            continue;
        offset = std::uint32_t(candidate);
        pointer = value;
        token = model_token(text);
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Weapon point: resolve a bone name (weapon_bone_R and fallbacks) to the
// selected model's pose transform index. Resolve the resource through that
// model's own binding and verify its name before reading its complete skeleton.
// The current pose buffer can include merged bones beyond the base skeleton.
// Resolution runs on the worker; the callback validates cached identities and
// reads one 32-byte transform per frame.
// ---------------------------------------------------------------------------

inline bool read_c_string(std::uintptr_t address, char* out, std::size_t capacity) noexcept {
    if (!out || capacity < 2)
        return false;
    out[0] = 0;
    // Bone wire names hold at most 63 characters plus NUL. Never copy a
    // caller-sized range from a smaller stack buffer, or accept a truncated
    // valid-looking prefix of an invalid string.
    unsigned char raw[64]{};
    const auto size = (std::min)(capacity, sizeof(raw));
    if (!address)
        return false;
    const bool block_read = read_memory(address, raw, size);
    for (std::size_t length = 0; length < size; ++length) {
        // A short NUL-terminated string may end immediately before an
        // inaccessible page. Do not require readable padding past its NUL.
        if (!block_read && !read_value(address + length, raw[length]))
            return false;
        if (raw[length] == 0) {
            if (!length)
                return false;
            std::memcpy(out, raw, length + 1);
            return true;
        }
        if (raw[length] < 32 || raw[length] > 126)
            return false;
    }
    return false;
}

inline bool valid_bone_name(const char* text) noexcept {
    if (!text || !text[0])
        return false;
    if (std::strncmp(text, "$cloth_m", 8) == 0) {
        const char* p = text + 8;
        if (*p < '0' || *p > '9')
            return false;
        while (*p >= '0' && *p <= '9')
            ++p;
        if (*p++ != 'p' || *p < '0' || *p > '9')
            return false;
        while (*p >= '0' && *p <= '9')
            ++p;
        return *p == 0;
    }
    for (const char* p = text; *p; ++p) {
        const char c = *p;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '.'))
            return false;
    }
    return true;
}

inline unsigned skeleton_name_score(std::uintptr_t data, std::uint32_t entries,
                                    std::uint32_t stride) noexcept;

// Rank all candidate headers within the existing bounded object window.
// A reduced physics skeleton may precede the render skeleton in one object.
inline bool find_bone_vector(std::uintptr_t object, std::uintptr_t& data, std::uint32_t& entries,
                             std::uint32_t& entry_stride) noexcept {
    unsigned best_score = 0;
    bool found = false;
    unsigned name_budget = 4096; // Shared across candidate headers in this object.
    for (int pass = 0; pass < 2; ++pass) {
        const std::uint32_t stride = pass == 0 ? 8 : 16;
        // Large hero models (for example Bebop) keep the render-skeleton name
        // vector deeper in the resource object than the original 0x600 window.
        for (std::uint32_t offset = 0; offset + 0x18 <= 0x4000; offset += 8) {
            std::uintptr_t pointer = 0;
            // Count fields are 32-bit. Adjacent bytes can be nonzero in
            // live resource objects; reading QWORDs made valid models fail
            // depending on those unrelated bytes.
            std::uint32_t count = 0, capacity = 0;
            if (!read_value(object + offset, pointer) || !read_value(object + offset + 8, count) ||
                !read_value(object + offset + 0x10, capacity))
                continue;
            if (count < 16 || count > 4096 || capacity < count || capacity > 200000)
                continue;
            if (pointer < 0x10000 || pointer > 0x7fffffffffff || (pointer & 7))
                continue;
            std::uint32_t index = 0;
            bool root_first = false;
            for (; index < count && name_budget; ++index) {
                --name_budget;
                std::uintptr_t text = 0;
                char name[65]{};
                if (!read_value(pointer + std::uintptr_t(index) * stride, text) ||
                    !read_c_string(text, name, sizeof(name)) || !valid_bone_name(name))
                    break;
                if (index == 0)
                    root_first = std::strcmp(name, "root_motion") == 0;
                if (stride == 16) {
                    std::uint32_t length = 0;
                    if (!read_value(pointer + std::uintptr_t(index) * stride + 8, length) ||
                        length < 1 || length > 96)
                        break;
                }
            }
            if (index != count)
                continue;
            const unsigned score =
                skeleton_name_score(pointer, static_cast<std::uint32_t>(count), stride);
            // Reviewed render skeletons normally begin with root_motion, but
            // Bebop's starts at the pelvis; accept a strongly marker-scored,
            // full-length list too. Short reduced physics/attachment tables
            // stay rejected (the smoke fixture covers that case).
            if (!root_first && (score < 6 || count < 64))
                continue;
            if (score &&
                (!found || score > best_score || (score == best_score && count > entries))) {
                data = pointer;
                entries = static_cast<std::uint32_t>(count);
                entry_stride = stride;
                best_score = score;
                found = true;
            }
        }
    }
    return found;
}

struct VectorHit {
    std::uintptr_t object = 0, data = 0;
    std::uint32_t entries = 0, stride = 8;
};

inline const char* const* weapon_bone_candidates(unsigned& count) noexcept {
    static const char* const kNames[] = {"weapon_bone_R", "weapon_bone_L", "weaponHand_R",
                                         "weaponHand_L",  "hand_R",        "hand_L"};
    count = sizeof(kNames) / sizeof(kNames[0]);
    return kNames;
}

// How many skeleton markers a vector names: the bone list scores high, while
// attachment/prop lists score low or zero.
inline unsigned skeleton_name_score(std::uintptr_t data, std::uint32_t entries,
                                    std::uint32_t stride) noexcept {
    static const char* const kMarkers[] = {"pelvis", "spine_0", "neck_0",      "head",
                                           "hand_L", "hand_R",  "arm_upper_L", "leg_upper_L"};
    unsigned score = 0;
    bool seen[8]{};
    const std::uint32_t limit = entries < 256 ? entries : 256;
    for (std::uint32_t entry = 0; entry < limit; ++entry) {
        std::uintptr_t text = 0;
        char name[65]{};
        if (!read_value(data + std::uintptr_t(entry) * stride, text) ||
            !read_c_string(text, name, sizeof(name)))
            continue;
        for (unsigned marker = 0; marker < 8; ++marker)
            if (!seen[marker] && std::strcmp(name, kMarkers[marker]) == 0) {
                seen[marker] = true;
                ++score;
                break;
            }
    }
    return score;
}

// A candidate pose buffer: transform 0 carries a scale and unit quaternion,
// and its position stays near the node origin.
inline bool pose_root_valid(std::uintptr_t pointer, const float origin[3],
                            double max_distance) noexcept {
    float first[8]{};
    if (!read_memory(pointer, first, sizeof(first)))
        return false;
    const double dx = first[0] - origin[0], dy = first[1] - origin[1], dz = first[2] - origin[2];
    if (dx * dx + dy * dy + dz * dz > max_distance * max_distance)
        return false;
    const double scale = first[3];
    const double norm = std::sqrt(double(first[4]) * first[4] + double(first[5]) * first[5] +
                                  double(first[6]) * first[6] + double(first[7]) * first[7]);
    return scale > 0.01 && scale < 100.0 && norm > 0.5 && norm < 1.5;
}

// Pose transform array near the model state. The reviewed layout stores the
// active pose buffer at +0x80 (the caller re-checks that same field), so try
// it first with a realistic distance: the render root can sit well away from
// the node origin (Bebop's first pose is his elevated pelvis). The scan fallback
// keeps the tighter reviewed gate for other layouts.
inline bool locate_bone_array(const Offsets& offsets, std::uintptr_t node,
                              std::uintptr_t model_state, std::uintptr_t& out) noexcept {
    float origin[3]{};
    if (!read_memory(node + offsets.origin, origin, sizeof(origin)))
        return false;
    std::uintptr_t preferred = 0;
    if (read_value(model_state + 0x80, preferred) && preferred >= 0x10000 &&
        preferred <= 0x7fffffffffff && !(preferred & 7) &&
        pose_root_valid(preferred, origin, 100.0)) {
        out = preferred;
        return true;
    }
    for (std::uintptr_t candidate = 0x40; candidate + 8 <= 0x400; candidate += 8) {
        if (candidate == 0x80)
            continue;
        std::uintptr_t pointer = 0;
        if (!read_value(model_state + candidate, pointer) || pointer < 0x10000 ||
            pointer > 0x7fffffffffff || (pointer & 7))
            continue;
        if (pose_root_valid(pointer, origin, 5.0)) {
            out = pointer;
            return true;
        }
    }
    return false;
}

[[maybe_unused]] inline void quat_to_angles(const float* transform,
                                            std::array<double, 3>& out) noexcept {
    double x = transform[4], y = transform[5], z = transform[6], w = transform[7];
    const double norm = std::sqrt(x * x + y * y + z * z + w * w);
    if (norm < 1e-6) {
        x = y = z = 0;
        w = 1;
    } else {
        x /= norm;
        y /= norm;
        z /= norm;
        w /= norm;
    }
    const double r00 = 1 - 2 * (y * y + z * z), r10 = 2 * (x * y + w * z),
                 r20 = 2 * (x * z - w * y), r21 = 2 * (y * z + w * x),
                 r22 = 1 - 2 * (x * x + y * y);
    constexpr double pi = 3.14159265358979323846;
    out[0] = -std::asin(std::fmin(1.0, std::fmax(-1.0, r20))) * 180.0 / pi;
    out[1] = std::atan2(r10, r00) * 180.0 / pi;
    out[2] = std::atan2(-r21, r22) * 180.0 / pi;
}

// A texture/skin mod may replace a hero model with a resource whose internal
// name differs (for example "magician_default.vmdl") while it still occupies
// the same model slot. The exact name token is the strict identity; when it
// differs, accept only a resource in the SAME folder whose file stem is a
// "_"-suffixed variant of the selected player's reported model stem (or the
// reverse), which still rejects an unrelated model.
inline bool same_model_variant(const char* resource, const char* reported) noexcept {
    if (!resource || !reported)
        return false;
    auto fold = [](char c) { return c >= 'A' && c <= 'Z' ? char(c + 32) : c; };
    auto folder_end = [](const char* text) -> int {
        int last = -1;
        for (int index = 0; text[index]; ++index)
            if (text[index] == '/' || text[index] == '\\')
                last = index;
        return last;
    };
    auto stem_end = [](const char* text, int start) -> int {
        int end = start;
        while (text[end] && text[end] != '.')
            ++end;
        return end;
    };
    auto extends = [&](const char* base, int base_begin, int base_end, const char* full,
                       int full_begin, int full_end) -> bool {
        const int base_len = base_end - base_begin;
        const int full_len = full_end - full_begin;
        if (base_len <= 0 || full_len <= base_len + 1)
            return false;
        for (int index = 0; index < base_len; ++index)
            if (fold(base[base_begin + index]) != fold(full[full_begin + index]))
                return false;
        return full[full_begin + base_len] == '_';
    };
    const int resource_folder = folder_end(resource);
    const int reported_folder = folder_end(reported);
    if (resource_folder <= 0 || resource_folder != reported_folder)
        return false;
    for (int index = 0; index < resource_folder; ++index)
        if (fold(resource[index]) != fold(reported[index]))
            return false;
    const int resource_stem = resource_folder + 1;
    const int reported_stem = reported_folder + 1;
    const int resource_end = stem_end(resource, resource_stem);
    const int reported_end = stem_end(reported, reported_stem);
    return extends(resource, resource_stem, resource_end, reported, reported_stem, reported_end) ||
           extends(reported, reported_stem, reported_end, resource, resource_stem, resource_end);
}

// Worker thread: resolve the attach bone for a target whose node is known.
// Weapon points pick the best-ranked candidate bone; bone points match the
// authored 64-bit name hash against the skeleton name vector.
inline bool resolve_bone(const Offsets& offsets, std::uintptr_t node,
                         const dolly::AttachSegment& segment, Cache& cache, const char*& error,
                         bool collect_picker = false) noexcept {
    // The reviewed CModelState stores the resource binding immediately before
    // its model-name field (0xa0/0xa8). The binding's first pointer is CModel;
    // CModel's name at +8 must identify the SAME model before inspecting its
    // skeleton. Never walk neighboring bindings or arbitrary nested pointers.
    if (cache.name_offset < 0xa8) {
        error = "The selected model resource layout is unavailable. Choose Eyes.";
        return false;
    }
    const std::uintptr_t model_state = node + cache.name_offset - 0xa8;
    std::uintptr_t binding = 0, object = 0, resource_name = 0;
    char resource_path[256]{};
    if (!read_value(model_state + 0xa0, binding) || !binding || !read_value(binding, object) ||
        !object || !read_value(object + 8, resource_name) ||
        !read_memory(resource_name, resource_path, sizeof(resource_path) - 1) ||
        !text_looks_like_model(resource_path)) {
        error = "The selected model resource could not be verified. Choose Eyes.";
        return false;
    }
    if (model_token(resource_path) != cache.model) {
        // A skin/mod may replace the model with a differently named resource in
        // the same folder; accept it as the same model family so its skeleton
        // can still be resolved.
        char reported_path[256]{};
        if (!read_c_string(cache.name_pointer, reported_path, sizeof(reported_path)) ||
            !same_model_variant(resource_path, reported_path)) {
            error = "The selected model resource could not be verified. Choose Eyes.";
            return false;
        }
    }
    VectorHit hit{};
    if (!find_bone_vector(object, hit.data, hit.entries, hit.stride)) {
        error = "No complete render skeleton was found for this model. Choose Eyes.";
        return false;
    }
    cache.model_handle_slot = model_state + 0xa0;
    cache.model_handle = binding;
    cache.model_object = object;
    cache.model_state = model_state;
    unsigned candidate_count = 0;
    const char* const* candidates = weapon_bone_candidates(candidate_count);
    cache.bones.total = hit.entries;
    bool root_found = false;
    for (std::uint32_t entry = 0; entry < hit.entries; ++entry) {
        std::uintptr_t text = 0;
        char name[65]{};
        if (!read_value(hit.data + std::uintptr_t(entry) * hit.stride, text) ||
            !read_c_string(text, name, sizeof(name)) || !valid_bone_name(name) ||
            !((name[0] >= 'A' && name[0] <= 'Z') || (name[0] >= 'a' && name[0] <= 'z') ||
              name[0] == '_' || name[0] == '$'))
            continue;
        if (std::strcmp(name, "root_motion") == 0) {
            cache.root_index = entry;
            root_found = true;
        }
        if (cache.bones.count < kEditorBoneCount)
            std::memcpy(cache.bones.names[cache.bones.count++], name, std::strlen(name));
    }
    if (!root_found) {
        error = "The skeleton's movement root could not be verified. Choose Eyes.";
        return false;
    }
    std::uint32_t index = 0;
    bool found = false;
    if (segment.point == dolly::AttachPoint::bone) {
        for (std::uint32_t entry = 0; entry < hit.entries && !found; ++entry) {
            std::uintptr_t text = 0;
            char name[65]{};
            if (!read_value(hit.data + std::uintptr_t(entry) * hit.stride, text) ||
                !read_c_string(text, name, sizeof(name)))
                continue;
            if (model_token(name) == segment.bone_hash) {
                index = entry;
                found = true;
            }
        }
        if (!found) {
            error = "The attach bone name was not found on this model.";
            return false;
        }
    } else if (segment.point != dolly::AttachPoint::eyes) {
        std::uint32_t best_rank = candidate_count;
        for (std::uint32_t entry = 0; entry < hit.entries; ++entry) {
            std::uintptr_t text = 0;
            char name[65]{};
            if (!read_value(hit.data + std::uintptr_t(entry) * hit.stride, text) ||
                !read_c_string(text, name, sizeof(name)))
                continue;
            for (unsigned candidate = 0; candidate < candidate_count; ++candidate)
                if (std::strcmp(name, candidates[candidate]) == 0) {
                    if (candidate < best_rank) {
                        index = entry;
                        best_rank = candidate;
                    }
                    break;
                }
        }
        if (best_rank == candidate_count) {
            error = "The attach weapon point found no weapon bone on this model.";
            return false;
        }
    }
    std::uintptr_t bone_array = 0;
    std::uint32_t pose_count = 0;
    // The game can replace the pose buffer between these reads. That passes
    // within a frame or two, so look again before refusing the bone.
    for (unsigned attempt = 0;; ++attempt) {
        if (!locate_bone_array(offsets, node, model_state, bone_array)) {
            error = "The attach weapon point could not locate the target pose transforms.";
            return false;
        }
        std::uintptr_t current_array = 0;
        if (read_value(model_state + 0x80, current_array) && current_array == bone_array &&
            read_value(model_state + 0x90, pose_count) && pose_count >= hit.entries &&
            pose_count <= 4096)
            break;
        if (attempt == 4) {
            error = "The model skeleton does not match its current pose buffer. Choose Eyes.";
            return false;
        }
        Sleep(4);
    }
    cache.bone_count = pose_count;
    cache.bone_array = bone_array;
    cache.bone_index = index;
    cache.bone_ready = true;
    if (collect_picker) {
        try {
            auto catalog = std::make_shared<dolly::PickerCatalog>();
            catalog->total = hit.entries;
            catalog->handle = cache.handle;
            catalog->entity = cache.entity_index;
            catalog->model = cache.model;
            char model_path[256]{};
            if (read_c_string(cache.name_pointer, model_path, sizeof(model_path))) {
                // Normal launcher executable: <game>/bin/win64/citadel.exe.
                // Disk access stays on the catalog worker, never Camera/Present.
                const auto exe = std::filesystem::path(module_path(nullptr));
                catalog->portrait = dolly::load_hero_portrait(
                    exe.parent_path().parent_path().parent_path() / "citadel/pak01_dir.vpk",
                    model_path);
            }
            catalog->bones.reserve(hit.entries);
            for (std::uint32_t entry = 0; entry < hit.entries; ++entry) {
                std::uintptr_t text = 0;
                dolly::PickerBone bone{};
                bone.source_index = entry;
                if (!read_value(hit.data + std::uintptr_t(entry) * hit.stride, text) ||
                    !read_c_string(text, bone.name, sizeof(bone.name)) ||
                    !valid_bone_name(bone.name)) {
                    error = "The full bone catalog changed while it was read. Reopen the picker.";
                    return false;
                }
                // These are the same serializable names accepted by AttachKey.
                if ((bone.name[0] >= 'A' && bone.name[0] <= 'Z') ||
                    (bone.name[0] >= 'a' && bone.name[0] <= 'z') || bone.name[0] == '_' ||
                    bone.name[0] == '$')
                    catalog->bones.push_back(bone);
            }
            cache.picker = std::move(catalog);
        } catch (...) {
            error = "Could not allocate the bounded bone catalog.";
            return false;
        }
    }
    return true;
}

// Worker thread: resolve the recorded target by handle first, then by a unique
// model match. Handles are recycled, so a handle match additionally requires
// the model to agree when the payload recorded one. The weapon point also
// resolves the target's weapon bone here.
inline bool resolve(HMODULE client, const Offsets& offsets, const dolly::AttachTarget& expected,
                    const dolly::AttachSegment& segment, Cache& out, const char*& error,
                    bool collect_picker = false) noexcept {
    out = Cache{};
    out.resolution = {segment.point, segment.bone_hash};
    std::uintptr_t entity_system = 0;
    if (!locate_entity_system(client, entity_system)) {
        error = "The attach camera could not locate the game entity system on this build.";
        return false;
    }
    std::vector<PlayerEntity> players;
    if (!walk_players(entity_system, players)) {
        error = "The attach camera found no players in the loaded replay.";
        return false;
    }
    struct Found {
        PlayerEntity entity;
        std::uintptr_t node, name_pointer;
        std::uint32_t name_offset, entity_index;
        std::uint64_t model;
    };
    std::vector<Found> handle_matches, model_matches;
    for (const auto& player : players) {
        std::uintptr_t node = 0, owner = 0;
        if (!read_value(player.instance + offsets.scene_node, node) || !node ||
            !read_value(node + offsets.owner, owner) || owner != player.instance)
            continue;
        Found found{};
        found.entity = player;
        found.node = node;
        found.entity_index = player.handle & 0x7fffu;
        if (!discover_model_name(node, found.name_offset, found.name_pointer, found.model))
            continue;
        if (expected.handle && player.handle == expected.handle)
            handle_matches.push_back(found);
        if (expected.model && found.model == expected.model)
            model_matches.push_back(found);
    }
    const Found* selected = nullptr;
    if (!handle_matches.empty()) {
        for (const auto& found : handle_matches)
            if (!expected.model || found.model == expected.model) {
                selected = &found;
                break;
            }
    } else if (expected.model) {
        if (model_matches.size() == 1) {
            selected = &model_matches.front();
        } else if (model_matches.size() > 1) {
            error = "Several players share the recorded attach model; select the target again.";
            return false;
        }
    }
    if (!selected) {
        error = "The attach target is not present in the loaded replay at this time.";
        return false;
    }
    out.ready = true;
    out.pawn = selected->entity.instance;
    out.identity = selected->entity.identity;
    out.node = selected->node;
    out.handle = selected->entity.handle;
    out.entity_index = selected->entity_index;
    out.name_offset = selected->name_offset;
    out.name_pointer = selected->name_pointer;
    out.model = selected->model;
    out.bones.handle = out.handle;
    out.bones.entity_index = out.entity_index;
    out.bones.model = out.model;
    // Eyes needs only the validated player fields. Discover a skeleton only
    // when Weapon/Bone is requested; optional picker work must not stall Eyes.
    if ((collect_picker || segment.point == dolly::AttachPoint::weapon ||
         segment.point == dolly::AttachPoint::bone) &&
        !resolve_bone(offsets, out.node, segment, out, error, collect_picker)) {
        out.ready = false;
        return false;
    }
    return true;
}

// Render callback: allocation-free identity re-check and pose read.
inline bool sample(const Offsets& offsets, const Cache& cache, const dolly::AttachSegment& segment,
                   dolly::AttachSample& out, const char*& error) noexcept {
    if (!cache.ready) {
        error = cache.error ? cache.error
                            : "The attach target was not resolved for this shot; re-play the shot.";
        return false;
    }
    std::uintptr_t identity = 0, instance = 0;
    std::uint32_t handle = 0;
    if (!read_value(cache.pawn + 0x10, identity) || identity != cache.identity ||
        !read_value(identity, instance) || instance != cache.pawn ||
        !read_value(identity + 0x10, handle) || handle != cache.handle) {
        error = "The attach target is no longer the same entity; retry the attach camera.";
        return false;
    }
    std::uintptr_t node = 0, owner = 0, name_pointer = 0;
    if (!read_value(cache.pawn + offsets.scene_node, node) || node != cache.node ||
        !read_value(node + offsets.owner, owner) || owner != cache.pawn ||
        !read_value(node + cache.name_offset, name_pointer) || name_pointer != cache.name_pointer) {
        error = "The attach target changed since it was resolved; re-play the shot.";
        return false;
    }
    if (segment.point == dolly::AttachPoint::weapon || segment.point == dolly::AttachPoint::bone) {
        if (!cache.bone_ready) {
            error = "The attach bone was not resolved for this shot; re-play the shot.";
            return false;
        }
        std::uintptr_t binding = 0, object = 0, array = 0;
        std::uint32_t count = 0;
        if (!read_value(cache.model_handle_slot, binding) || binding != cache.model_handle ||
            !read_value(binding, object) || object != cache.model_object ||
            !read_value(cache.model_state + 0x80, array) || array != cache.bone_array ||
            !read_value(cache.model_state + 0x90, count) || count != cache.bone_count ||
            cache.bone_index >= count || cache.root_index >= count) {
            error = "The model or bone pose buffer changed; retry the attach camera.";
            return false;
        }
        float transform[8]{};
        if (!read_memory(cache.bone_array + std::uint64_t(cache.bone_index) * 32, transform,
                         sizeof(transform))) {
            error = "The attach weapon bone transform was not readable; the shot stopped.";
            return false;
        }
        if (!std::isfinite(transform[0]) || !std::isfinite(transform[1]) ||
            !std::isfinite(transform[2]) || std::abs(transform[0]) > 1e8 ||
            std::abs(transform[1]) > 1e8 || std::abs(transform[2]) > 1e8) {
            error = "The attach weapon bone pose was outside the verified range; the shot stopped.";
            return false;
        }
        // Position follows the weapon bone; the default aim follows the
        // player's eye angles (the bone quaternion points along the rig's own
        // axes and would face the camera into the body). Authored offsets
        // still adjust both.
        float aim[3]{};
        float player_origin[3]{};
        float root_transform[8]{};
        if (!read_memory(cache.pawn + offsets.eye_angles, aim, sizeof(aim))) {
            error = "The attach weapon aim was not readable; the shot stopped.";
            return false;
        }
        if (!read_memory(node + offsets.origin, player_origin, sizeof(player_origin)) ||
            !std::isfinite(player_origin[0]) || !std::isfinite(player_origin[1]) ||
            !std::isfinite(player_origin[2]) || std::abs(player_origin[0]) > 1e8 ||
            std::abs(player_origin[1]) > 1e8 || std::abs(player_origin[2]) > 1e8) {
            error = "The attach player motion was not readable; the shot stopped.";
            return false;
        }
        // At view setup the readable skeleton may still contain the previous
        // root translation. Mesh submission later uses the current scene origin.
        // Preserve the bone's local animation, then rebase the whole skeleton
        // onto that current origin before camera offsets or smoothing.
        // Bone zero is not necessarily the movement root (Bebop starts with
        // pelvis). Subtract only the named root, preserving the pelvis height.
        if (!read_memory(cache.bone_array + std::uint64_t(cache.root_index) * 32, root_transform,
                         sizeof(root_transform)) ||
            !std::isfinite(root_transform[0]) || !std::isfinite(root_transform[1]) ||
            !std::isfinite(root_transform[2]) || std::abs(root_transform[0]) > 1e8 ||
            std::abs(root_transform[1]) > 1e8 || std::abs(root_transform[2]) > 1e8) {
            error = "The attach skeleton root was not readable; the shot stopped.";
            return false;
        }
        out.valid = true;
        out.point = segment.point;
        out.target.handle = cache.handle;
        out.target.entity_id = cache.entity_index;
        out.target.model = cache.model;
        for (unsigned axis = 0; axis < 3; ++axis)
            out.origin[axis] = double(transform[axis]) - root_transform[axis] + player_origin[axis];
        out.motion_anchor = {player_origin[0], player_origin[1], player_origin[2]};
        out.angles = {aim[0], aim[1], aim[2]};
        out.aim = out.angles;
        out.eye_local = {0.0, 0.0, 0.0};
        return true;
    }
    float origin[3]{}, angles[3]{}, aim[3]{}, local[3]{};
    if (!read_memory(node + offsets.origin, origin, sizeof(origin)) ||
        !read_memory(node + offsets.angles, angles, sizeof(angles)) ||
        !read_memory(cache.pawn + offsets.eye_angles, aim, sizeof(aim)) ||
        !read_memory(cache.pawn + offsets.view_offset + dolly::reviewed::kViewOffsetComponents[0],
                     &local[0], 4) ||
        !read_memory(cache.pawn + offsets.view_offset + dolly::reviewed::kViewOffsetComponents[1],
                     &local[1], 4) ||
        !read_memory(cache.pawn + offsets.view_offset + dolly::reviewed::kViewOffsetComponents[2],
                     &local[2], 4)) {
        error = "The attach target pose was not readable; the shot stopped.";
        return false;
    }
    out.valid = true;
    out.point = segment.point;
    out.target.handle = cache.handle;
    out.target.entity_id = cache.entity_index;
    out.target.model = cache.model;
    out.origin = {origin[0], origin[1], origin[2]};
    out.motion_anchor = out.origin;
    out.angles = {angles[0], angles[1], angles[2]};
    out.aim = {aim[0], aim[1], aim[2]};
    if (!dolly::attach_view_offset({local[0], local[1], local[2]}, out.eye_local)) {
        error = "The attach eye offset was outside its verified range; the shot stopped.";
        return false;
    }
    return true;
}

// Worker thread: publish the live player roster for the editor target picker.
inline void publish_bones(unsigned char* mapping, const Cache* cache) noexcept {
    if (!mapping)
        return;
    EditorBones bones = cache ? cache->bones : EditorBones{};
    std::memcpy(bones.magic, "DLYBONE1", 8);
    bones.abi = 1;
    auto destination = mapping + kEditorBonesOffset;
    auto sequence = reinterpret_cast<volatile LONG*>(destination + 8);
    const LONG current = InterlockedCompareExchange(sequence, 0, 0);
    const LONG odd = (current & 1) ? current + 2 : current + 1;
    InterlockedExchange(sequence, odd);
    MemoryBarrier();
    std::memcpy(destination, &bones, 8);
    std::memcpy(destination + 12, reinterpret_cast<const unsigned char*>(&bones) + 12,
                sizeof(bones) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, odd + 1);
}

// Worker thread: publish the live player roster for the editor target picker.
// The seqlock mirrors the status writer: odd while writing, even when done.
inline void publish_roster(unsigned char* mapping, HMODULE client) noexcept {
    if (!mapping)
        return;
    EditorRoster roster{};
    std::memcpy(roster.magic, "DLYROS01", 8);
    roster.abi = kEditorRosterAbi;
    EditorAttachConfig config{};
    if (editor_attach_config(config)) {
        roster.flags = 1;
        const Offsets offsets = offsets_from(config);
        std::uintptr_t entity_system = 0;
        std::vector<PlayerEntity> players;
        if (locate_entity_system(client, entity_system) && walk_players(entity_system, players)) {
            for (const auto& player : players) {
                if (roster.count >= kEditorRosterPlayers)
                    break;
                std::uintptr_t node = 0, owner = 0;
                if (!read_value(player.instance + offsets.scene_node, node) || !node ||
                    !read_value(node + offsets.owner, owner) || owner != player.instance)
                    continue;
                EditorRosterEntry entry{};
                entry.handle = player.handle;
                entry.entity_index = player.handle & 0x7fffu;
                std::uint32_t name_offset = 0;
                std::uintptr_t name_pointer = 0;
                std::uint64_t token = 0;
                if (discover_model_name(node, name_offset, name_pointer, token) &&
                    read_memory(name_pointer, entry.model_path, sizeof(entry.model_path) - 1)) {
                    entry.model_path[sizeof(entry.model_path) - 1] = 0;
                    entry.model = token;
                }
                roster.players[roster.count++] = entry;
            }
        }
    }
    auto sequence = reinterpret_cast<volatile LONG*>(mapping + kEditorRosterOffset + 8);
    const LONG current = InterlockedCompareExchange(sequence, 0, 0);
    const LONG odd = (current & 1) ? current + 2 : current + 1;
    InterlockedExchange(sequence, odd);
    MemoryBarrier();
    unsigned char* destination = mapping + kEditorRosterOffset;
    std::memcpy(destination, &roster, 8);
    std::memcpy(destination + 12, reinterpret_cast<unsigned char*>(&roster) + 12,
                sizeof(roster) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, odd + 1);
}

} // namespace attach_runtime
