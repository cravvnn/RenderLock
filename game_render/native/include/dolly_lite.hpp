#pragma once
#include "dolly_editor.hpp"
#include <limits>
#include <algorithm>
namespace dolly {
constexpr std::size_t kEditorLiteReplayOffset = 2 * 1024 * 1024 + 27712;
struct EditorLiteReplayInfo {
    char magic[8];
    std::uint32_t sequence, abi, revision, first, count, flags;
    std::int64_t start_tick, total_ticks;
    double tick_rate;
    std::int64_t ticks[kEditorCameraListCount];
};
static_assert(sizeof(EditorLiteReplayInfo) == 312, "Lite replay metadata wire size");
static_assert(kEditorLiteReplayOffset + sizeof(EditorLiteReplayInfo) <=
                  2 * 1024 * 1024 + 24576 + 4096,
              "Lite metadata fits the existing shared mapping");
inline bool valid_lite_replay_info(const EditorLiteReplayInfo& value) noexcept {
    if (std::memcmp(value.magic, "DLYLITE1", 8) || value.abi != 1 || !value.revision ||
        value.count > 32 || (value.first % 32 && value.count != 32) || (value.flags & ~7u))
        return false;
    if ((value.flags & 1) &&
        (value.start_tick < 0 || !std::isfinite(value.tick_rate) || value.tick_rate <= 0))
        return false;
    if ((value.flags & 2) && (value.total_ticks < 0 || value.total_ticks > 2147483647))
        return false;
    for (unsigned i = 0; i < value.count; i++)
        if (value.ticks[i] < -1 || value.ticks[i] > 2147483647)
            return false;
    return true;
}
inline bool valid_lite_seek_tick(double value, std::int64_t total = -1) noexcept {
    return std::isfinite(value) && value == std::floor(value) && value >= 0 &&
           value <= 2147483647 && (total < 0 || value < total);
}
inline double lite_fov_factor(double current, double target) noexcept {
    if (!std::isfinite(current) || !std::isfinite(target) || current <= 0 || current >= 180 ||
        target < 5 || target > 170)
        return std::numeric_limits<double>::quiet_NaN();
    constexpr double radians = 3.14159265358979323846 / 360;
    return std::tan(target * radians) / std::tan(current * radians);
}
inline bool snap_camera_time(double time, const EditorLiteReplayInfo& info,
                             double& snapped, std::int64_t& tick) noexcept {
    if (!(info.flags & 1) || !std::isfinite(time) || time < 0 ||
        !std::isfinite(info.tick_rate) || info.tick_rate <= 0 || info.start_tick < 0)
        return false;
    const auto maximum = (info.flags & 2) ? info.total_ticks - 1 : 2147483647;
    if (maximum < info.start_tick)
        return false;
    // nearbyint uses the same ties-to-even policy as Python's round.
    tick = std::int64_t(std::clamp(std::nearbyint(info.start_tick + time * info.tick_rate),
                                  double(info.start_tick), double(maximum)));
    snapped = double(tick - info.start_tick) / info.tick_rate;
    return true;
}
inline double stepped_camera_speed(double current, bool faster) noexcept {
    if (!std::isfinite(current) || current < 1 || current > 10000)
        current = 320;
    return std::clamp(faster ? current * 1.25 : current / 1.25, 1., 10000.);
}
bool editor_lite_replay_info(EditorLiteReplayInfo& out) noexcept;
bool editor_request_framing_factor(double factor) noexcept;
bool lite_mode_requested() noexcept;
}
