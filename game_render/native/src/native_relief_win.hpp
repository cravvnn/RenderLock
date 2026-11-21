// Reversible render relief applied while a replay is seeking or the engine's
// vertex-buffer queue is near capacity.
//
// Included from bridge_win.cpp's anonymous namespace after native_effects_win.hpp,
// so it reuses the verified typed ConVar interface (gFindCvar / gGetCvarData /
// gSetCvar) and the checked read helpers. Every cvar value is saved before it is
// changed and restored exactly; a missing, unsafe or unsupported cvar is skipped
// rather than guessed. No name is written without verifying the resolved object
// still reports that name.
#pragma once
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>

struct ReliefSpec {
    const char* name;
    double value;
};

// While seeking, the engine can race ahead of the GPU: it creates CVertexBufferDx11
// objects faster than they retire (fatal EnsureCapacity overflow) and lets particle
// and effect state linger so scenes stay bright after catching up.
//
// r_wait_on_present makes the render thread wait for the GPU each present instead of
// racing, which is the primary fix; the effect toggles cut the remaining per-frame
// load. Values are the relief value applied only while seeking or under pressure.
inline constexpr ReliefSpec kReliefSpecs[] = {
    {"r_wait_on_present", 1.0},     {"r_effects_bloom", 0.0},
    {"cl_impacteffects", 0.0},      {"r_citadel_screenspace_particles_full_res", 0.0},
    {"r_RainParticleDensity", 0.0},
};
inline constexpr std::size_t kReliefCount = sizeof(kReliefSpecs) / sizeof(kReliefSpecs[0]);

struct ReliefCvar {
    const char* name = nullptr;
    double relief = 0;
    CvarRef ref{};
    unsigned type = 0;
    bool bound = false;
    bool active = false;
    double restore = 0;

    bool bind(const char* cvar_name, double relief_value) noexcept {
        name = cvar_name;
        relief = relief_value;
        ref = {};
        type = 0;
        bound = false;
        active = false;
        if (!gCvar || !gFindCvar || !gGetCvarData || !gSetCvar)
            return false;
        std::uint64_t id = 0xffffffffull;
        gFindCvar(reinterpret_cast<void*>(gCvar), &id, name, 0);
        if ((id & 0xffffull) == 0xffffull)
            return false;
        CvarRef candidate;
        candidate.id = id;
        candidate.data = gGetCvarData(reinterpret_cast<void*>(gCvar), id);
        if (!candidate.data)
            return false;
        const std::size_t length = std::strlen(name) + 1;
        if (length > 96)
            return false;
        std::uintptr_t stored_name = 0;
        std::uint16_t candidate_type = 0;
        std::uint64_t flags = 0;
        char text[96]{};
        if (!read_value(candidate.data, stored_name) || !read_memory(stored_name, text, length) ||
            std::strcmp(text, name) || !read_value(candidate.data + 0x28, candidate_type) ||
            !read_value(candidate.data + 0x30, flags))
            return false;
        // Reject replicated/per-user/server-controlled references and callback
        // reentry, matching the effect bindings' safety mask.
        if (flags & ((1ull << 2) | (1ull << 9) | (1ull << 10) | (1ull << 13) | (1ull << 15) |
                     (1ull << 18) | (1ull << 22)))
            return false;
        candidate_type = static_cast<std::uint16_t>(relief_cvar_type(
            candidate_type, season2_cvar_types(gTier0, reinterpret_cast<std::uintptr_t>(gSetCvar))));
        if (candidate_type != 0 && candidate_type != 2 && candidate_type != 3)
            return false;
        ref = candidate;
        type = candidate_type;
        bound = true;
        return true;
    }

    bool read(double& out) const noexcept {
        if (!bound)
            return false;
        if (type == 0) {
            unsigned char v = 0;
            if (!read_value(ref.data + 0x58, v) || v > 1)
                return false;
            out = v;
        } else if (type == 3) {
            std::int32_t v = 0;
            if (!read_value(ref.data + 0x58, v))
                return false;
            out = v;
        } else {
            float v = 0;
            if (!read_value(ref.data + 0x58, v))
                return false;
            out = v;
        }
        return std::isfinite(out);
    }

    bool write(double value) noexcept {
        if (!bound)
            return false;
        alignas(16) unsigned char typed[16]{};
        double expected = value;
        if (type == 0) {
            if (value != 0 && value != 1)
                return false;
            typed[0] = static_cast<unsigned char>(value);
        } else if (type == 3) {
            if (value != std::floor(value) || value < INT32_MIN || value > INT32_MAX)
                return false;
            std::int32_t v = static_cast<std::int32_t>(value);
            std::memcpy(typed, &v, 4);
        } else {
            float v = static_cast<float>(value);
            if (!std::isfinite(v))
                return false;
            std::memcpy(typed, &v, 4);
            expected = v;
        }
        // Preserves native change callbacks, clamping and change counts.
        gSetCvar(&ref, 0, typed, reinterpret_cast<void*>(ref.data + 0x58), nullptr);
        double actual = 0;
        return read(actual) && actual == expected;
    }
};

struct SeekRelief {
    std::array<ReliefCvar, kReliefCount> items{};
    bool active = false;
    unsigned restore_failures = 0;

    void enter() noexcept {
        if (active)
            return;
        for (std::size_t i = 0; i < items.size(); ++i) {
            auto& item = items[i];
            if (!item.bound && !item.bind(kReliefSpecs[i].name, kReliefSpecs[i].value))
                continue;
            double current = 0;
            if (!item.read(current))
                continue;
            item.restore = current;
            item.active = item.write(kReliefSpecs[i].value);
        }
        active = true;
    }

    void leave() noexcept {
        if (!active)
            return;
        bool all = true;
        for (auto& item : items) {
            if (!item.bound || !item.active)
                continue;
            if (item.write(item.restore))
                item.active = false;
            else
                all = false;
        }
        if (all) {
            active = false;
            restore_failures = 0;
        } else if (++restore_failures > 8) {
            // Give up retrying rather than spin forever; the game resets its own
            // cvars on exit and the user can retune after the session.
            active = false;
        }
    }
};

SeekRelief gSeekRelief;
inline void seek_relief_tick(bool engage) noexcept {
    if (engage)
        gSeekRelief.enter();
    else
        gSeekRelief.leave();
}
