#pragma once
#include "dolly_attach.hpp"
#include "dolly_path.hpp"
#include <array>
#include <cstdint>
#include <vector>
#include <string>
#include <limits>

namespace dolly {
// Internal effect/wire types stay stable; only the exact reviewed Tier0 ABI
// selects its runtime representation. This is a stateless value conversion.
inline constexpr unsigned effect_cvar_type(unsigned legacy_type, bool season2) noexcept {
    return season2 && legacy_type != 0 ? legacy_type + 2 : legacy_type;
}
inline constexpr bool season2_cvar_types(std::uintptr_t base, std::uintptr_t setter) noexcept {
    return base != 0 && (setter == base + 0x20ffa0 || setter == base + 0x20fe50);
}
// Normalize into the relief owner's existing bool/int32/float representation.
// Other runtime types are refused before any binding is published.
inline constexpr unsigned relief_cvar_type(unsigned runtime_type, bool season2) noexcept {
    if (!season2) return runtime_type;
    return runtime_type == 0 ? 0 : runtime_type == 5 ? 3 : runtime_type == 9 ? 2 : 0xffff;
}
struct EffectSpec {
    const char* name;
    unsigned type;
    double minimum, maximum;
    bool discrete;
    unsigned components = 1;
};
inline constexpr double kEffectFloatMax = std::numeric_limits<float>::max();
inline constexpr std::array<EffectSpec, 14> kEffects{
    {{"r_citadel_depthoffield_enable", 0, 0, 1, true},
     {"r_citadel_depthoffield_focus_distance", 7, 0, 10000, false},
     {"r_citadel_depthoffield_aperture_diameter", 7, 0, 3, false},
     {"r_citadel_depthoffield_sensor_size", 7, .5, 3, false},
     {"r_citadel_depthoffield_mode", 3, 0, 2, true},
     {"r_citadel_depthoffield_debug", 0, 0, 1, true},
     {"r_depth_of_field", 0, 0, 1, true},
     {"r_dof_override", 0, 0, 1, true},
     {"r_dof_override_ranges", 13, -kEffectFloatMax, kEffectFloatMax, false, 4},
     {"r_dof_override_near_blurry", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_near_crisp", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_far_crisp", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_far_blurry", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_tilt_to_ground", 7, -kEffectFloatMax, kEffectFloatMax, false}}};
struct EffectSegment {
    double begin, end;
    unsigned kind, flags;
    double left, right, left_derivative, right_derivative;
};
// Keep authored range curves intact while the lens-based Citadel mode is active.
inline std::array<double, 4> effective_dof_ranges(const std::array<double, 4>& authored,
                                                  bool citadel, bool range_override) noexcept {
    return citadel && !range_override ? std::array<double, 4>{} : authored;
}
struct EffectTrack {
    unsigned id = 0;
    bool restore_override = false;
    unsigned component = 0;
    double first_time = 0, last_time = 0, first = 0, last = 0, restore = 0;
    std::vector<EffectSegment> segments;
    bool evaluate(double phase, double& value) const noexcept;
};
class NativeShot {
public:
    NativePath camera;
    std::vector<EffectTrack> effects;
    AttachTrack attach;
    bool load(const void* data, std::size_t size, std::string& error);
};
}
