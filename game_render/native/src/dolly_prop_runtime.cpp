#include "dolly_prop_runtime.hpp"

#include <Windows.h>
#include <array>
#include <atomic>
#include <cstring>
#include <mutex>

namespace dolly::prop_runtime {
namespace {

// Minimal in-process read; this unit owns its own copy so it has no dependency
// on the large attach-runtime header.
template <typename T>
bool read_value(std::uintptr_t address, T& out) noexcept {
    SIZE_T read{};
    return address &&
           ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(address),
                             &out, sizeof(T), &read) &&
           read == sizeof(T);
}

std::mutex mutex;
std::uintptr_t client{};
std::uintptr_t create_offset{};   // CreateEntityFromClassname
std::uintptr_t entity_global{};   // CGameEntitySystem global RVA
bool resolved{};
std::atomic<int> pending{0};      // 0 none, 1 create+count, 2 recount only
Diagnostics counts{};

// Reviewed 6774 client build. The RVAs come from FINDINGS-model-create.md; the
// byte prefixes below confirm the module still matches before any call is made.
// A wrong call would be a hard Plat_FatalError, so this fails closed.
struct Row {
    std::uintptr_t create, lookup, entity_global;
    std::array<unsigned char, 12> create_bytes;
};
constexpr std::array<Row, 1> kRows{{
    // create FUN_1820915c0 prologue, lookup FUN_182095c00, ENTITY_GLOBAL
    {0x20915c0,
     0x2095c00,
     0x3e1ed10,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x6c, 0x24, 0x10, 0x48, 0x89}},
}};

bool matches(std::uintptr_t base, const Row& row) noexcept {
    std::array<unsigned char, 12> actual{};
    SIZE_T read{};
    return ReadProcessMemory(GetCurrentProcess(),
                             reinterpret_cast<const void*>(base + row.create),
                             actual.data(), actual.size(), &read) &&
           read == actual.size() && actual == row.create_bytes;
}

// The entity identity walk (matches dolly_attach_runtime.hpp): two linked lists
// headed at +0x210 / +0x230, each identity names a class at +0x20 and links at
// +0x58. Counting identities is our before/after evidence.
std::uint32_t count_entities(std::uintptr_t system) noexcept {
    if (!system)
        return 0;
    std::uint32_t total = 0;
    std::uintptr_t seen_head[2]{};
    for (unsigned head_index = 0; head_index < 2; ++head_index) {
        const std::uintptr_t head = head_index == 0 ? 0x210 : 0x230;
        std::uintptr_t identity = 0;
        if (!read_value(system + head, identity))
            continue;
        unsigned steps = 0;
        while (identity && steps < 60000) {
            ++total;
            ++steps;
            std::uintptr_t next = 0;
            if (!read_value(identity + 0x58, next) || next == identity)
                break;
            identity = next;
        }
        seen_head[head_index] = total;
    }
    return total;
}

// CreateEntityFromClassname( entity_system, slot_hint, classname, a4, a5, a6, a7 )
// The game's own caller passes slot_hint = -1 (auto-assign) and looks the class
// up by name internally, so an unknown or not-spawnable name returns 0 rather
// than crashing.
using CreateFn = void* (*)(void*, int, const char*, int, int, int, char);
} // namespace

bool initialize(std::uintptr_t client_base) noexcept {
    std::scoped_lock lock(mutex);
    client = client_base;
    resolved = false;
    counts = Diagnostics{};
    if (!client) {
        counts.state = 0;
        return false;
    }
    for (const auto& row : kRows) {
        if (!matches(client, row))
            continue;
        create_offset = row.create;
        entity_global = row.entity_global;
        resolved = true;
        counts.state = 1;
        counts.resolved = 1;
        return true;
    }
    client = 0;
    counts.state = 0;
    counts.error = 1;
    return false;
}

void disable() noexcept {
    std::scoped_lock lock(mutex);
    client = 0;
    create_offset = entity_global = 0;
    resolved = false;
    pending.store(0);
    counts.state = 0;
}

void request(bool recount_only) noexcept {
    pending.store(recount_only ? 2 : 1, std::memory_order_release);
}

void tick() noexcept {
    const int mode = pending.exchange(0, std::memory_order_acq_rel);
    if (!mode)
        return;
    std::scoped_lock lock(mutex);
    if (!resolved || !client) {
        counts.state = 0;
        return;
    }
    std::uintptr_t system = 0;
    if (!read_value(client + entity_global, system) || !system) {
        counts.state = 3;
        counts.error = 2;
        return;
    }
    // Direct liveness of the created entity, independent of identity-list
    // membership: an entity slot at `entity_ptr` stores its identity at +0x10,
    // whose class name pointer is at identity+0x20. If the stream frees the
    // slot, the identity/class read fails or changes. The recorded class is the
    // baseline; a mismatching name means the slot was reused by another entity.
    const auto alive = [&]() -> std::uint32_t {
        if (!counts.entity_ptr)
            return 0;
        std::uintptr_t identity = 0, name_ptr = 0;
        if (!read_value(counts.entity_ptr + 0x10, identity) || !identity)
            return 0;
        if (!read_value(identity + 0x20, name_ptr) || !name_ptr)
            return 0;
        char name[32]{};
        if (!read_value(name_ptr, name) || !name[0])
            return 0;
        // Same-entity check: the identity pointer must be unchanged. A different
        // identity means the slot was recycled (a different entity now lives
        // there), which is NOT survival.
        if (counts.identity_ptr && identity != counts.identity_ptr)
            return 3u;
        if (!counts.entity_class[0])
            return 1u;
        return std::memcmp(name, counts.entity_class, sizeof(name)) == 0 ? 1u : 2u;
    };
    if (mode == 2) {
        // Recount-only: test survival after a tick/seek.
        counts.after_count = count_entities(system);
        counts.alive_after_seek = alive();
        return;
    }
    ++counts.calls;
    counts.before_count = count_entities(system);
    auto* fn = reinterpret_cast<CreateFn>(client + create_offset);
    // Model-less class: prop_dynamic binds no model here, so the
    // CSkeletonInstance::SetModel nonresident-asset fatal assert cannot fire.
    void* entity = fn(reinterpret_cast<void*>(system), -1, "prop_dynamic", 0, 0, 0, 1);
    counts.entity_ptr = reinterpret_cast<std::uintptr_t>(entity);
    counts.entity_handle = static_cast<std::uint32_t>(counts.entity_ptr & 0xffffffffu);
    // Record the created entity's class name and identity as the survival
    // baseline. The identity pointer is the same-entity token: if the slot is
    // recycled by a different entity, the identity changes.
    std::memset(counts.entity_class, 0, sizeof(counts.entity_class));
    counts.identity_ptr = 0;
    counts.entity_index = 0;
    if (entity) {
        std::uintptr_t identity = 0, name_ptr = 0;
        if (read_value(counts.entity_ptr + 0x10, identity) &&
            read_value(identity + 0x20, name_ptr)) {
            counts.identity_ptr = identity;
            read_value(name_ptr, counts.entity_class);
        }
        std::uint32_t index = 0;
        if (read_value(counts.entity_ptr + 0x34, index))
            counts.entity_index = index;
    }
    counts.after_count = count_entities(system);
    counts.alive_after_create = alive();
    counts.state = entity ? 2 : 3;
    counts.error = entity ? 0 : 3;
}

const char* status() noexcept {
    std::scoped_lock lock(mutex);
    switch (counts.state) {
    case 1: return "prop runtime ready";
    case 2: return "prop runtime created a client entity";
    case 3: return "prop runtime could not create a client entity";
    default: return "prop runtime unavailable for this build";
    }
}

Diagnostics diagnostics() noexcept {
    std::scoped_lock lock(mutex);
    return counts;
}

} // namespace dolly::prop_runtime
