#include "dolly_bone_picker.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <mutex>

namespace dolly {
namespace {
std::mutex guard;
PickerFrame frame;
PausedAttachSample<PickerSample> paused_sample;
std::shared_ptr<const PickerCatalog> catalog;
std::uint32_t active_request = 0, active_command = 0;
bool saved = false, was_requested = false, rejected = false, framed = false;
std::array<double, 3> orbit_center{};
double orbit_radius = 0, orbit_yaw = 0, orbit_pitch = 0;
double pending_yaw = 0, pending_pitch = 0;
constexpr double pi = 3.14159265358979323846;
bool contains(const char* text, const char* needle) noexcept {
    if (!needle || !*needle)
        return true;
    auto lower = [](char c) { return c >= 'A' && c <= 'Z' ? char(c + 'a' - 'A') : c; };
    for (; *text; ++text) {
        const char *a = text, *b = needle;
        while (*a && *b && lower(*a) == lower(*b)) {
            ++a;
            ++b;
        }
        if (!*b)
            return true;
    }
    return false;
}
bool fresh() noexcept {
    return frame.active && frame.ready && frame.stamp && picker_now() - frame.stamp <= 250;
}
}
std::uint64_t picker_now() noexcept {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}
const char* picker_friendly_name(const char* name) noexcept {
    struct Alias {
        const char *rig, *label;
    };
    static constexpr Alias aliases[] = {{"head", "Head"},
                                        {"neck_0", "Neck"},
                                        {"pelvis", "Pelvis"},
                                        {"spine_0", "Lower spine"},
                                        {"spine_1", "Mid spine"},
                                        {"spine_2", "Chest"},
                                        {"spine_3", "Upper chest"},
                                        {"clavicle_L", "Left collarbone"},
                                        {"clavicle_R", "Right collarbone"},
                                        {"arm_upper_L", "Left shoulder"},
                                        {"arm_upper_R", "Right shoulder"},
                                        {"arm_lower_L", "Left elbow"},
                                        {"arm_lower_R", "Right elbow"},
                                        {"hand_L", "Left hand"},
                                        {"hand_R", "Right hand"},
                                        {"leg_upper_L", "Left hip"},
                                        {"leg_upper_R", "Right hip"},
                                        {"leg_lower_L", "Left knee"},
                                        {"leg_lower_R", "Right knee"},
                                        {"ankle_L", "Left ankle"},
                                        {"ankle_R", "Right ankle"},
                                        {"ball_L", "Left foot"},
                                        {"ball_R", "Right foot"}};
    for (const auto& alias : aliases)
        if (std::strcmp(name, alias.rig) == 0)
            return alias.label;
    return nullptr;
}
bool picker_position(const PickerSample& sample, const PickerBone& bone,
                     std::array<double, 3>& out) noexcept {
    if (sample.count > kPickerMaxBones || bone.source_index >= sample.count)
        return false;
    for (int axis = 0; axis < 3; ++axis) {
        const auto value = sample.transforms[bone.source_index][axis];
        if (!std::isfinite(value) || std::abs(value) > 1e8f)
            return false;
        out[axis] = value;
    }
    return true;
}
bool picker_matches(const PickerBone& bone, const char* search, bool common_only) noexcept {
    const char* friendly = picker_friendly_name(bone.name);
    if (search && *search)
        return contains(bone.name, search) || (friendly && contains(friendly, search));
    return !common_only || friendly;
}
void picker_stabilize_marker(float raw_x, float raw_y, float scale, float& shown_x, float& shown_y,
                             bool& initialized) noexcept {
    if (!initialized) {
        shown_x = raw_x;
        shown_y = raw_y;
        initialized = true;
        return;
    }
    const float dx = raw_x - shown_x, dy = raw_y - shown_y;
    const float distance = std::hypot(dx, dy);
    if (distance <= 2.0f * scale)
        return;
    if (distance >= 24.0f * scale) {
        shown_x = raw_x;
        shown_y = raw_y;
        return;
    }
    const float fraction = 0.2f * (distance - 2.0f * scale) / distance;
    shown_x += dx * fraction;
    shown_y += dy * fraction;
}
bool picker_front_view(const PickerCatalog& bones, const PickerSample& sample, double fov,
                       CameraPose& pose, std::array<double, 3>* center) noexcept {
    if (!std::isfinite(fov) || fov <= 1 || fov >= 179 || !std::isfinite(pose[6]) || pose[6] <= 0 ||
        !std::isfinite(sample.facing_yaw))
        return false;
    std::array<double, 3> low{}, high{}, p{};
    unsigned count = 0;
    for (const auto& bone : bones.bones) {
        if (!picker_friendly_name(bone.name) || !picker_position(sample, bone, p))
            continue;
        if (!count++)
            low = high = p;
        else
            for (int axis = 0; axis < 3; ++axis) {
                low[axis] = std::min(low[axis], p[axis]);
                high[axis] = std::max(high[axis], p[axis]);
            }
    }
    if (count < 4 || high[2] - low[2] < 5 || high[2] - low[2] > 2000)
        return false;
    const double radius = std::sqrt(std::pow(high[0] - low[0], 2) + std::pow(high[1] - low[1], 2) +
                                    std::pow(high[2] - low[2], 2)) *
                              .5 +
                          12;
    const double half_fov = std::atan(std::tan(fov * pi / 360) / std::max(1.0, pose[6]));
    const double distance = radius / std::sin(half_fov) * 1.25;
    if (!std::isfinite(distance) || distance > 10000)
        return false;
    const double yaw = sample.facing_yaw * pi / 180;
    if (center)
        for (int axis = 0; axis < 3; ++axis)
            (*center)[axis] = (low[axis] + high[axis]) * .5;
    pose[0] = (low[0] + high[0]) * .5 + std::cos(yaw) * distance;
    pose[1] = (low[1] + high[1]) * .5 + std::sin(yaw) * distance;
    pose[2] = (low[2] + high[2]) * .5;
    pose[3] = 0;
    pose[4] = std::remainder(sample.facing_yaw + 180, 360.0);
    pose[5] = 0;
    return true;
}
void picker_publish(std::shared_ptr<const PickerCatalog> value) noexcept {
    std::lock_guard<std::mutex> lock(guard);
    catalog = std::move(value);
}
bool picker_camera(std::uint32_t request, std::uint32_t command, bool requested, bool permitted,
                   CameraPose& pose, double fov, const std::array<double, 6>& offsets,
                   PickerSampler sampler, void* context, bool* attached_preview,
                   bool auto_clearance, int paused_tick, bool rigid_orientation,
                   bool lock_vertical_aim) noexcept {
    if (attached_preview)
        *attached_preview = false;
    std::unique_lock<std::mutex> lock(guard, std::try_to_lock);
    if (!lock.owns_lock())
        return requested;
    if (!requested) {
        if (saved && command == active_command && permitted)
            pose = frame.original;
        frame.active = frame.ready = false;
        saved = was_requested = rejected = framed = false;
        paused_sample.reset();
        return false;
    }
    if (!was_requested) {
        active_request = request;
        active_command = command;
        frame.original = pose;
        frame.view = pose;
        frame.selected = -1;
        frame.preview = frame.finishing = false;
        frame.error[0] = 0;
        frame.catalog.reset();
        saved = was_requested = true;
        framed = rejected = false;
        orbit_radius = pending_yaw = pending_pitch = 0;
        paused_sample.reset();
    }
    frame.active = true;
    frame.ready = false;
    if (request != active_request || command != active_command) {
        rejected = true;
        const char* reason = request != active_request ? "Picker configuration changed"
                                                       : "Camera command changed";
        std::snprintf(frame.error, sizeof(frame.error), "%s. Cancel and reopen Bone Picker.",
                      reason);
    }
    if (rejected)
        return true;
    if (!permitted) {
        // The paused editor can briefly drop ownership while it settles (for
        // example while a heavy model finishes loading or the replay re-pauses).
        // Retry on the next frame instead of rejecting the whole session, so the
        // picker recovers on its own once the editor is ready again.
        std::snprintf(frame.error, sizeof(frame.error), "Preparing the paused editor...");
        return true;
    }
    if (!catalog || catalog->sequence != request) {
        std::snprintf(frame.error, sizeof(frame.error), "Resolving this player's skeleton...");
        return true;
    }
    frame.catalog = catalog;
    if (frame.catalog->bones.empty() && frame.catalog->error[0]) {
        // Report why the model produced no joints (for example an unsupported
        // skeleton layout) instead of an unexplained empty list.
        std::snprintf(frame.error, sizeof(frame.error), "%s", frame.catalog->error);
    }
    const char* error = nullptr;
    if (!sampler || !sampler(context, frame.sample, error)) {
        std::snprintf(frame.error, sizeof(frame.error), "%s",
                      error ? error : "Bone pose unavailable. Cancel and retry.");
        // Once identity is lost, old hit targets cannot become valid again.
        rejected = true;
        pose = frame.original;
        return true;
    }
    paused_sample.apply(frame.sample, true, paused_tick);
    if (!framed) {
        frame.view = frame.original;
        if (!picker_front_view(*catalog, frame.sample, fov, frame.view, &orbit_center)) {
            std::snprintf(frame.error, sizeof(frame.error),
                          "Automatic framing unavailable; showing your original view.");
        } else {
            frame.error[0] = 0;
            orbit_radius =
                std::hypot(frame.view[0] - orbit_center[0], frame.view[1] - orbit_center[1]);
            orbit_yaw = frame.view[4];
            orbit_pitch = 0;
        }
        framed = true;
    }
    if (!frame.preview && orbit_radius > 0 && (pending_yaw || pending_pitch)) {
        orbit_yaw = std::remainder(orbit_yaw + pending_yaw, 360.0);
        orbit_pitch = std::clamp(orbit_pitch + pending_pitch, -85.0, 85.0);
        const double yaw = orbit_yaw * pi / 180, pitch = orbit_pitch * pi / 180;
        frame.view[0] = orbit_center[0] - orbit_radius * std::cos(pitch) * std::cos(yaw);
        frame.view[1] = orbit_center[1] - orbit_radius * std::cos(pitch) * std::sin(yaw);
        frame.view[2] = orbit_center[2] + orbit_radius * std::sin(pitch);
        frame.view[3] = orbit_pitch;
        frame.view[4] = orbit_yaw;
    }
    pending_yaw = pending_pitch = 0;
    if (frame.preview && frame.selected >= 0 &&
        std::size_t(frame.selected) < catalog->bones.size()) {
        AttachSample sample{};
        sample.valid = true;
        sample.point = AttachPoint::bone;
        sample.angles = sample.aim = frame.sample.aim;
        AttachSegment segment{};
        segment.point = AttachPoint::bone;
        segment.bone_hash = attach_bone_hash(catalog->bones[frame.selected].name);
        segment.offset = offsets;
        segment.flags = (auto_clearance ? 4u : 0u) | (rigid_orientation ? 8u : 0u) |
                        (lock_vertical_aim ? 16u : 0u);
        if (!picker_position(frame.sample, catalog->bones[frame.selected], sample.origin) ||
            !resolve_attach_pose(sample, segment, pose)) {
            std::snprintf(frame.error, sizeof(frame.error),
                          "This bone has no valid pose at this moment.");
            frame.preview = false;
            pose = frame.view;
        } else
            enforce_attach_clearance(sample, segment, pose);
    } else
        pose = frame.view;
    frame.fov = fov;
    frame.stamp = picker_now();
    frame.ready = true;
    if (attached_preview)
        *attached_preview = frame.preview;
    return true;
}
bool picker_snapshot(PickerFrame& out) noexcept {
    std::unique_lock<std::mutex> lock(guard, std::try_to_lock);
    if (!lock.owns_lock())
        return false;
    out = frame;
    if (!fresh())
        out.ready = false;
    return frame.active;
}
bool picker_select(std::uint32_t request, int index) noexcept {
    std::unique_lock<std::mutex> lock(guard, std::try_to_lock);
    std::array<double, 3> position{};
    if (!lock.owns_lock() || !fresh() || frame.finishing || !frame.catalog ||
        request != active_request || index < 0 ||
        std::size_t(index) >= frame.catalog->bones.size() ||
        !picker_position(frame.sample, frame.catalog->bones[index], position))
        return false;
    frame.selected = index;
    return true;
}
bool picker_preview(std::uint32_t request, bool preview) noexcept {
    std::unique_lock<std::mutex> lock(guard, std::try_to_lock);
    if (!lock.owns_lock() || !fresh() || frame.finishing || request != active_request ||
        (preview && frame.selected < 0))
        return false;
    frame.preview = preview;
    pending_yaw = pending_pitch = 0;
    return true;
}
bool picker_orbit(std::uint32_t request, double yaw, double pitch) noexcept {
    std::unique_lock<std::mutex> lock(guard, std::try_to_lock);
    if (!lock.owns_lock() || !fresh() || frame.finishing || frame.preview ||
        request != active_request || orbit_radius <= 0 || !std::isfinite(yaw) ||
        !std::isfinite(pitch))
        return false;
    pending_yaw = std::remainder(pending_yaw + std::remainder(yaw, 360.0), 360.0);
    pending_pitch = std::clamp(pending_pitch + std::clamp(pitch, -170.0, 170.0), -170.0, 170.0);
    return true;
}
bool picker_finish(std::uint32_t request, int index) noexcept {
    std::unique_lock<std::mutex> lock(guard, std::try_to_lock);
    if (!lock.owns_lock() || !fresh() || frame.finishing || request != active_request ||
        index < 0 || frame.selected != index)
        return false;
    frame.finishing = true;
    return true;
}
void picker_result(PickerResult& out) noexcept {
    std::lock_guard<std::mutex> lock(guard);
    out = {};
    std::memcpy(out.magic, "DLYPICK1", 8);
    out.abi = 1;
    out.flags = (frame.active ? 1u : 0u) | (fresh() ? 2u : 0u) | (frame.preview ? 4u : 0u) |
                (frame.finishing ? 8u : 0u);
    out.request = active_request;
    out.selected = frame.selected;
    std::copy(frame.original.begin(), frame.original.end(), out.original);
    if (frame.catalog) {
        out.handle = frame.catalog->handle;
        out.entity = frame.catalog->entity;
        out.model = frame.catalog->model;
        out.total = frame.catalog->total;
        if (frame.selected >= 0 && std::size_t(frame.selected) < frame.catalog->bones.size())
            std::memcpy(out.name, frame.catalog->bones[frame.selected].name, sizeof(out.name));
    }
}
void picker_abandon() noexcept {
    std::lock_guard<std::mutex> lock(guard);
    frame.active = frame.ready = false;
    saved = false;
    rejected = true;
}
} // namespace dolly
