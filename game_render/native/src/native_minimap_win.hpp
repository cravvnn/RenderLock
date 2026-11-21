// Included in bridge scope after read_demo. Exact-build HUD repair only.
#pragma once
using MinimapUpdate = void(__fastcall*)(void*, void*);
MinimapUpdate gOriginalMinimapUpdate = nullptr;
std::atomic<std::uint64_t> gMinimapSkipped{0};
bool gMinimapGuardInstalled = false;

__declspec(noinline) static void __fastcall minimap_update_hook(void* self, void* icon) {
    namespace profile = reviewed::ReplayCamera;
    const auto caller = reinterpret_cast<std::uintptr_t>(_ReturnAddress());
    bool known_caller = false;
    for (auto site : profile::MINIMAP_CALLERS)
        known_caller |= caller == gClient + site;
    const MinimapIconLayout layout{gClient + profile::MINIMAP_VTABLE, profile::MINIMAP_ICON_TYPE,
                                   profile::MINIMAP_ICON_CAST_RANGE};
    const auto read = [](std::uintptr_t address, auto& value) noexcept {
        return read_value(address, value);
    };
    const auto replay = []() noexcept {
        // Full-packet reconstruction may temporarily deactivate the client.
        // The reviewed demo player's Playing accessor scopes the guard without
        // requiring an already reconstructed camera or entity scene.
        std::uintptr_t demo = 0, table = 0;
        if (!read_value(gEngine + gEngineLayout.demo_global, demo) || !read_value(demo, table) ||
            table != gEngine + gEngineLayout.demo_table)
            return false;
        const auto playing =
            reinterpret_cast<bool(__fastcall*)(void*)>(gEngine + gEngineLayout.playing);
        return playing(reinterpret_cast<void*>(demo));
    };
    if (incomplete_minimap_icon(known_caller, layout, reinterpret_cast<std::uintptr_t>(self),
                                reinterpret_cast<std::uintptr_t>(icon), read, replay)) {
        gMinimapSkipped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    gOriginalMinimapUpdate(self, icon);
}

static bool install_minimap_guard(HMODULE client) {
    namespace profile = reviewed::ReplayCamera;
    if (!module_matches(client, profile::CLIENT_SHA256, profile::MINIMAP_CLIENT_IMAGE_SIZE))
        return true; // This optional repair cannot widen older/unknown profiles.
    const auto expected = gClient + profile::MINIMAP_VTABLE;
    if (compat_detail::locate_vtable(client, "CHudMinimap") != expected)
        return false;
    unsigned char prefix[sizeof(profile::MINIMAP_UPDATE_PREFIX)]{};
    unsigned char range[sizeof(profile::MINIMAP_RANGE_READ_BYTES)]{};
    if (!read_memory(gClient + profile::MINIMAP_UPDATE, prefix, sizeof(prefix)) ||
        std::memcmp(prefix, profile::MINIMAP_UPDATE_PREFIX, sizeof(prefix)) ||
        !read_memory(gClient + profile::MINIMAP_RANGE_READ, range, sizeof(range)) ||
        std::memcmp(range, profile::MINIMAP_RANGE_READ_BYTES, sizeof(range)))
        return false;
    auto target = reinterpret_cast<void*>(gClient + profile::MINIMAP_UPDATE);
    // Publish the original before enabling; both callback and trampoline remain
    // resident until process exit, like the core camera and other HUD hooks.
    if (MH_CreateHook(target, reinterpret_cast<void*>(minimap_update_hook),
                      reinterpret_cast<void**>(&gOriginalMinimapUpdate)) != MH_OK ||
        !gOriginalMinimapUpdate || MH_EnableHook(target) != MH_OK)
        return false;
    gMinimapGuardInstalled = true;
    return true;
}

static void minimap_guard_diagnostics() noexcept {
    if (!gMinimapGuardInstalled)
        return;
    static ULONGLONG next = 0;
    static std::uint64_t written = UINT64_MAX;
    const auto now = GetTickCount64();
    const auto skipped = gMinimapSkipped.load(std::memory_order_relaxed);
    if (now < next || skipped == written)
        return;
    next = now + 1000;
    // Worker-only bounded evidence. Never perform file I/O in the UI callback.
    FILE* file = nullptr;
    const auto path = beside_module(L"dolly_minimap_guard.txt");
    if (!_wfopen_s(&file, path.c_str(), L"wb") && file) {
        std::fprintf(file, "DOLLY_MINIMAP_GUARD_1\ninstalled 1\nskipped %llu\n",
                     static_cast<unsigned long long>(skipped));
        std::fclose(file);
        written = skipped;
    }
}
