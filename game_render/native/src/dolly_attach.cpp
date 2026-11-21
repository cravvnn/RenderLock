#include "dolly_attach.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <utility>

namespace dolly {
namespace {

constexpr double kDegreesToRadians = 3.14159265358979323846 / 180.0;
class Reader {
public:
    explicit Reader(const void* data) : cursor_(static_cast<const std::uint8_t*>(data)) {}

    std::uint32_t u32() noexcept {
        std::uint32_t value = 0;
        for (unsigned i = 0; i != 4; ++i)
            value |= std::uint32_t(*cursor_++) << (i * 8);
        return value;
    }
    std::uint64_t u64() noexcept {
        std::uint64_t value = 0;
        for (unsigned i = 0; i != 8; ++i)
            value |= std::uint64_t(*cursor_++) << (i * 8);
        return value;
    }
    double number() noexcept {
        std::uint64_t bits = 0;
        for (unsigned i = 0; i != 8; ++i)
            bits |= std::uint64_t(*cursor_++) << (i * 8);
        double value;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }
    void skip(std::size_t bytes) noexcept { cursor_ += bytes; }

private:
    const std::uint8_t* cursor_;
};

bool finite3(const std::array<double, 3>& value, double limit) noexcept {
    for (double component : value)
        if (!std::isfinite(component) || std::abs(component) > limit)
            return false;
    return true;
}

// Source pitch/yaw/roll degrees, the same convention as camera poses: yaw zero
// faces +X, pitch is positive down, and right is -Y at yaw zero.
void source_basis(const std::array<double, 3>& angles, double basis[3][3]) noexcept {
    const double sp = std::sin(angles[0] * kDegreesToRadians),
                 cp = std::cos(angles[0] * kDegreesToRadians);
    const double sy = std::sin(angles[1] * kDegreesToRadians),
                 cy = std::cos(angles[1] * kDegreesToRadians);
    const double sr = std::sin(angles[2] * kDegreesToRadians),
                 cr = std::cos(angles[2] * kDegreesToRadians);
    const double forward[3] = {cp * cy, cp * sy, -sp};
    const double right[3] = {-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp};
    const double up[3] = {cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp};
    for (int i = 0; i < 3; ++i) {
        basis[i][0] = forward[i];
        basis[i][1] = right[i];
        basis[i][2] = up[i];
    }
}

void rotate(const double basis[3][3], const std::array<double, 3>& local,
            std::array<double, 3>& out) noexcept {
    for (int i = 0; i < 3; ++i)
        out[i] = basis[i][0] * local[0] + basis[i][1] * local[1] + basis[i][2] * local[2];
}

// Source's columns are forward, right and up, with right = -Y at zero
// angles. Flip that column before composing the two proper orientations.
void compose_orientation(const double base[3][3], const double local[3][3],
                         double world[3][3]) noexcept {
    constexpr double sign[3] = {1, -1, 1};
    for (int row = 0; row < 3; ++row)
        for (int col = 0; col < 3; ++col) {
            world[row][col] = 0;
            for (int axis = 0; axis < 3; ++axis)
                world[row][col] += base[row][axis] * sign[axis] * local[axis][col];
        }
}

void relative_orientation(const double base[3][3], const double world[3][3],
                          double local[3][3]) noexcept {
    constexpr double sign[3] = {1, -1, 1};
    for (int row = 0; row < 3; ++row)
        for (int col = 0; col < 3; ++col) {
            local[row][col] = 0;
            for (int axis = 0; axis < 3; ++axis)
                local[row][col] += sign[row] * base[axis][row] * world[axis][col];
        }
}

std::array<double, 3> basis_angles(const double basis[3][3]) noexcept {
    const double horizontal = std::hypot(basis[0][0], basis[1][0]);
    const double pitch = std::atan2(-basis[2][0], horizontal);
    double yaw = 0, roll = 0;
    if (horizontal > 1e-8) {
        yaw = std::atan2(basis[1][0], basis[0][0]);
        roll = std::atan2(-basis[2][1], basis[2][2]);
    } else {
        // At a vertical view yaw and roll are coupled; choose yaw zero and
        // preserve the full orientation through the remaining roll.
        roll = std::atan2(basis[0][1] * basis[2][0], -basis[1][1]);
    }
    return {pitch / kDegreesToRadians, yaw / kDegreesToRadians, roll / kDegreesToRadians};
}

} // namespace

void AttachRenderHistory::record(const AttachSample& sample, double time,
                                 std::uint64_t frame) noexcept {
    if (!sample.valid || !std::isfinite(time) || !finite3(sample.origin, 1e8) ||
        !finite3(sample.motion_anchor, 1e8) || (count_ && frame <= latest_.frame))
        return;
    previous_ = latest_;
    for (unsigned i = 0; i < 3; ++i)
        latest_.local[i] = sample.origin[i] - sample.motion_anchor[i];
    latest_.time = time;
    latest_.frame = frame;
    count_ = std::min(2u, count_ + 1);
}
bool AttachRenderHistory::apply(AttachSample& sample, double time,
                                std::uint64_t frame) const noexcept {
    if (!sample.valid || !count_ || latest_.frame + 1 != frame || !std::isfinite(time))
        return false;
    const double age = time - latest_.time;
    if (age <= 0 || age > .05)
        return false;
    auto local = latest_.local;
    const double interval = latest_.time - previous_.time;
    if (count_ == 2 && previous_.frame + 1 == latest_.frame && interval >= .001 &&
        interval <= .05) {
        const double ratio = std::min(2.0, age / interval);
        std::array<double, 3> delta{};
        double distance = 0;
        for (unsigned i = 0; i < 3; ++i) {
            delta[i] = (latest_.local[i] - previous_.local[i]) * ratio;
            distance += delta[i] * delta[i];
        }
        // Never extrapolate an animation cut into a large camera jump.
        const double scale = distance > 4.0 ? 2.0 / std::sqrt(distance) : 1.0;
        for (unsigned i = 0; i < 3; ++i)
            local[i] += delta[i] * scale;
    }
    double discontinuity = 0;
    for (unsigned i = 0; i < 3; ++i) {
        const double difference = local[i] - (sample.origin[i] - sample.motion_anchor[i]);
        discontinuity += difference * difference;
    }
    if (discontinuity > 32.0 * 32.0)
        return false;
    for (unsigned i = 0; i < 3; ++i)
        sample.origin[i] = sample.motion_anchor[i] + local[i];
    return true;
}

void AttachAnchorFilter::reset() noexcept {
    primed_ = lagging_ = false;
    count_ = held_ = 0;
    shift_.fill(0);
}

void AttachAnchorFilter::apply(AttachSample& sample, double replay_time, double tick_interval,
                               bool paused) noexcept {
    if (!sample.valid || !finite3(sample.motion_anchor, 1e8) || !std::isfinite(replay_time) ||
        !(tick_interval > 0) || tick_interval > 1) {
        reset();
        return;
    }
    const auto shift_sample = [&] {
        for (unsigned i = 0; i < 3; ++i) {
            sample.origin[i] += shift_[i];
            sample.motion_anchor[i] += shift_[i];
        }
    };
    if (paused) {
        // The held view keeps the place it had when the replay stopped.
        if (primed_)
            shift_sample();
        return;
    }
    const auto anchor = sample.motion_anchor;
    const double dt = replay_time - time_;
    if (primed_ && dt >= 0 && dt <= 1e-5) {
        shift_sample(); // The same replay moment drawn again.
        return;
    }
    if (!primed_ || dt < 0 || dt > .1) {
        reset();
        primed_ = true;
        anchor_ = anchor;
        time_ = replay_time;
        return;
    }
    std::array<double, 3> velocity{};
    for (unsigned i = 0; i < 3; ++i)
        velocity[i] = (anchor[i] - anchor_[i]) / dt;
    bool usual = true;
    if (count_ == 3) {
        std::array<double, 3> tick{}, error{};
        double size = 0, back = 0, forward = 0, stay = 0;
        for (unsigned i = 0; i < 3; ++i) {
            const double a = velocity_[0][i], b = velocity_[1][i], c = velocity_[2][i];
            const double median = std::max(std::min(a, b), std::min(std::max(a, b), c));
            tick[i] = median * tick_interval;
            error[i] = anchor[i] - anchor_[i] - median * dt;
            size += tick[i] * tick[i];
            back += (error[i] + tick[i]) * (error[i] + tick[i]);
            forward += (error[i] - tick[i]) * (error[i] - tick[i]);
            stay += error[i] * error[i];
        }
        size = std::sqrt(size);
        const double tolerance = .35 * size + .5;
        if (!lagging_ && size > 1 && std::sqrt(back) < tolerance) {
            lagging_ = true;
            held_ = 0;
            usual = false;
        } else if (lagging_ && forward < stay) {
            // Nearer to the step forward than to staying behind. A jump or a
            // turn in the same frame must not hide the step forward.
            lagging_ = false;
            usual = false;
        } else if (lagging_ && ++held_ > 12) {
            lagging_ = false; // Not the pattern after all; follow the game.
        }
        for (unsigned i = 0; i < 3; ++i)
            shift_[i] = lagging_ ? tick[i] : 0;
    }
    if (usual) {
        velocity_[0] = velocity_[1];
        velocity_[1] = velocity_[2];
        velocity_[2] = velocity;
        count_ = std::min(3u, count_ + 1);
    }
    anchor_ = anchor;
    time_ = replay_time;
    shift_sample();
}

void AttachAimFilter::apply(AttachSample& sample, double replay_time, bool paused) noexcept {
    if (!sample.valid || !finite3(sample.aim, 1e6) || !std::isfinite(replay_time) || paused) {
        reset();
        return;
    }
    const double dt = replay_time - time_;
    if (!primed_ || dt < 0 || dt > .1) {
        first_ = second_ = sample.aim;
        time_ = replay_time;
        primed_ = true;
        return;
    }
    // Two equal stages leave no corner where the aim starts or stops turning.
    const double alpha = dt > 0 ? 1 - std::exp(-dt / kAttachAimEase) : 0;
    for (unsigned i = 0; i < 3; ++i) {
        first_[i] += std::remainder(sample.aim[i] - first_[i], 360.0) * alpha;
        second_[i] += std::remainder(first_[i] - second_[i], 360.0) * alpha;
    }
    time_ = replay_time;
    // Bone and weapon points take their direction from the aim as well.
    const bool shared = sample.point != AttachPoint::eyes;
    sample.aim = second_;
    if (shared)
        sample.angles = second_;
}

void AttachSmoothing::reset() noexcept {
    primed_ = false;
    pose_.fill(0);
    anchor_.fill(0);
    pose_[6] = 16.0 / 9;
}

void AttachSmoothing::apply(CameraPose& pose, double dt, double tau) noexcept {
    if (!primed_ || !(tau > 0) || !(dt > 0)) {
        pose_ = pose;
        primed_ = true;
        return;
    }
    dt = std::min(dt, 0.25); // A stall must not snap the camera.
    const double alpha = 1 - std::exp(-dt / tau);
    for (int i = 0; i < 3; ++i)
        pose_[i] += (pose[i] - pose_[i]) * alpha;
    for (int i = 3; i < 6; ++i)
        pose_[i] += std::remainder(pose[i] - pose_[i], 360.0) * alpha;
    pose_[6] = pose[6];
    pose = pose_;
}

void AttachSmoothing::apply_anchored(CameraPose& pose, const std::array<double, 3>& anchor,
                                     double dt, double tau, double max_error) noexcept {
    if (!finite3(anchor, 1e8) || !std::isfinite(max_error) || max_error < 0) {
        reset();
        return;
    }
    if (!primed_ || !(tau > 0) || !(dt > 0)) {
        pose_ = pose;
        anchor_ = anchor;
        primed_ = true;
        return;
    }
    dt = std::min(dt, 0.25);
    const double alpha = 1 - std::exp(-dt / tau);
    for (int i = 0; i < 3; ++i) {
        // Follow whole-player movement immediately; only filter motion inside
        // that moving frame. A world-space filter trails during dashes.
        pose_[i] += anchor[i] - anchor_[i];
        pose_[i] += (pose[i] - pose_[i]) * alpha;
    }
    for (int i = 3; i < 6; ++i)
        pose_[i] += std::remainder(pose[i] - pose_[i], 360.0) * alpha;
    double error_squared = 0;
    for (int i = 0; i < 3; ++i)
        error_squared += (pose_[i] - pose[i]) * (pose_[i] - pose[i]);
    if (error_squared > max_error * max_error) {
        const double fraction = max_error / std::sqrt(error_squared);
        for (int i = 0; i < 3; ++i)
            pose_[i] = pose[i] + (pose_[i] - pose[i]) * fraction;
    }
    anchor_ = anchor;
    pose_[6] = pose[6];
    pose = pose_;
}

bool attach_view_offset(const std::array<float, 3>& values, std::array<double, 3>& out) noexcept {
    for (int i = 0; i < 3; ++i) {
        const double value = values[i];
        if (!std::isfinite(value) || std::abs(value) > kAttachViewOffsetLimit)
            return false;
        out[i] = value;
    }
    return true;
}

const char* attach_hero_name(const char* model_path) noexcept {
    if (!model_path)
        return nullptr;
    const char* stem = model_path;
    for (const char* p = model_path; *p; ++p)
        if (*p == '/' || *p == '\\')
            stem = p + 1;
    std::size_t length = 0;
    while (stem[length] && stem[length] != '.')
        ++length;
    struct Entry {
        const char* stem;
        const char* name;
    };
    // Generated from scripts/heroes.vdata; released additions reviewed 2026-10-09.
    static constexpr Entry kNames[] = {
        {"abrams", "Abrams"},
        {"archer", "Grey Talon"},
        {"astro", "Holliday"},
        {"baba", "Baba"},
        {"bebop", "Bebop"},
        {"boho", "Boho"},
        {"bookworm", "Paige"},
        {"chessmaster", "Solomon"},
        {"chrono", "Paradox"},
        {"digger", "Mo and Krill"},
        {"doorman", "Doorman"},
        {"drifter", "Drifter"},
        {"dynamo", "Dynamo"},
        {"familiar_wip", "Rem"},
        {"fencer", "Fencer"},
        {"fortuna", "Fortuna"},
        {"frank", "Victor"},
        {"geist", "Lady Geist"},
        {"gen_man", "Gen Man"},
        {"gigawatt_prisoner", "Seven"},
        {"graffiti_girl", "Graffiti Girl"},
        {"haze", "Haze"},
        {"hornet", "Vindicta"},
        {"inferno", "Infernus"},
        {"ivy", "Ivy"},
        {"kelvin", "Kelvin"},
        {"lash", "Lash"},
        {"magician", "Sinclair"},
        {"mcginnis", "McGinnis"},
        {"mirage", "Mirage"},
        {"nano", "Calico"},
        {"necro", "Graves"},
        {"operative", "Operative"},
        {"pocket", "Pocket"},
        {"priest", "Priest"},
        {"punkgoat", "Billy"},
        {"ratking", "Rat King"},
        {"shiv", "Shiv"},
        {"unicorn", "Unicorn"},
        {"vampirebat", "Mina"},
        {"viper", "Vyper"},
        {"viscous", "Viscous"},
        {"warden", "Warden"},
        {"werewolf", "Werewolf"},
        {"wraith", "Wraith"},
        {"wrecker", "Wrecker"},
        {"yamato", "Yamato"},
    };
    for (const auto& entry : kNames) {
        if (std::strlen(entry.stem) == length && std::strncmp(stem, entry.stem, length) == 0)
            return entry.name;
    }
    return nullptr;
}

// ExoCalibra's level-aim option uses the target's yaw while retaining its live
// position. Eyes always follows the full aim; the option applies to bone/weapon.
static std::array<double, 3> effective_attach_angles(const AttachSample& sample,
                                                     AttachPoint point, bool locked) noexcept {
    auto angles = point == AttachPoint::eyes ? sample.aim : sample.angles;
    // The game reports a view above the horizon as a pitch just under 360.
    // Use the signed form so offsets add up to an ordinary camera angle.
    for (auto& angle : angles)
        angle = std::remainder(angle, 360.0);
    if (locked && point != AttachPoint::eyes) {
        angles[0] = 0;
        angles[2] = 0;
    }
    return angles;
}

bool attach_snap_offsets(const AttachSample& sample, const CameraPose& camera,
                         std::array<double, 6>& out, bool rigid_orientation,
                         bool lock_vertical_aim) noexcept {
    if (!sample.valid || !finite3(sample.origin, 1e8) || !finite3(sample.angles, 1e6) ||
        !finite3(sample.aim, 1e6) || !finite3(sample.eye_local, kAttachViewOffsetLimit))
        return false;
    for (int i = 0; i < 6; ++i)
        if (!std::isfinite(camera[i]))
            return false;
    const auto base_angles = effective_attach_angles(sample, sample.point, lock_vertical_aim);
    std::array<double, 3> base_position = sample.origin;
    if (sample.point == AttachPoint::eyes) {
        double body[3][3];
        source_basis(sample.angles, body);
        std::array<double, 3> local{};
        rotate(body, sample.eye_local, local);
        for (int i = 0; i < 3; ++i)
            base_position[i] += local[i];
    }
    double basis[3][3];
    source_basis(base_angles, basis);
    const std::array<double, 3> delta = {camera[0] - base_position[0], camera[1] - base_position[1],
                                         camera[2] - base_position[2]};
    for (int axis = 0; axis < 3; ++axis) {
        double value = 0.0;
        for (int i = 0; i < 3; ++i)
            value += delta[i] * basis[i][axis];
        if (!std::isfinite(value) || std::abs(value) > kAttachOffsetLimit)
            return false;
        out[axis] = value;
    }
    std::array<double, 3> angles{};
    if (rigid_orientation) {
        double camera_basis[3][3], local_basis[3][3];
        source_basis({camera[3], camera[4], camera[5]}, camera_basis);
        relative_orientation(basis, camera_basis, local_basis);
        angles = basis_angles(local_basis);
    } else {
        for (int axis = 0; axis < 3; ++axis)
            angles[axis] = std::remainder(camera[3 + axis] - base_angles[axis], 360.0);
    }
    for (int axis = 0; axis < 3; ++axis) {
        const double value = angles[axis];
        if (!std::isfinite(value) || std::abs(value) > kAttachOffsetLimit)
            return false;
        out[3 + axis] = value;
    }
    return true;
}

bool resolve_attach_pose(const AttachSample& sample, const AttachSegment& segment,
                         CameraPose& out) noexcept {
    if (!sample.valid || sample.point != segment.point)
        return false;
    if (segment.target.handle && sample.target.handle != segment.target.handle)
        return false;
    if (segment.target.entity_id && sample.target.entity_id != segment.target.entity_id)
        return false;
    if (segment.target.model && sample.target.model != segment.target.model)
        return false;
    if (!finite3(sample.origin, 1e8) || !finite3(sample.angles, 1e6) || !finite3(sample.aim, 1e6) ||
        !finite3(sample.eye_local, kAttachViewOffsetLimit))
        return false;
    const auto base_angles = effective_attach_angles(sample, segment.point, (segment.flags & 16) != 0);
    std::array<double, 3> base_position = sample.origin;
    if (segment.point == AttachPoint::eyes) {
        double body[3][3];
        source_basis(sample.angles, body);
        std::array<double, 3> local{};
        rotate(body, sample.eye_local, local);
        for (int i = 0; i < 3; ++i)
            base_position[i] += local[i];
    }
    double basis[3][3];
    source_basis(base_angles, basis);
    std::array<double, 3> position_offset{};
    rotate(basis, {segment.offset[0], segment.offset[1], segment.offset[2]}, position_offset);
    for (int i = 0; i < 3; ++i)
        out[i] = base_position[i] + position_offset[i];
    if (segment.flags & 8) {
        double local_basis[3][3], world_basis[3][3];
        source_basis({segment.offset[3], segment.offset[4], segment.offset[5]}, local_basis);
        compose_orientation(basis, local_basis, world_basis);
        const auto world_angles = basis_angles(world_basis);
        for (int i = 0; i < 3; ++i)
            out[i + 3] = world_angles[i];
    } else {
        for (int i = 0; i < 3; ++i)
            out[i + 3] = base_angles[i] + segment.offset[i + 3];
        // The target's aim adds to the authored pitch. Stop at straight up or
        // down instead of tipping the camera over onto its back.
        out[3] = std::clamp(out[3], -89.9, 89.9);
    }
    for (int i = 0; i < 6; ++i)
        if (!std::isfinite(out[i]) || std::abs(out[i]) > 1e8)
            return false;
    return true;
}

bool attach_apply_camera_motion(const AttachSample& sample, const AttachSegment& segment,
                                const CameraPose& before, const CameraPose& after,
                                CameraPose& offsets) noexcept {
    // Resolve the same target frame used for preview and playback. Keep the
    // update transactional if the target or any of the input values is invalid.
    CameraPose current = offsets;
    if (!resolve_attach_pose(sample, segment, current))
        return false;
    for (int i = 0; i < 7; ++i)
        if (!std::isfinite(before[i]) || !std::isfinite(after[i]) || !std::isfinite(offsets[i]) ||
            (i < 6 && offsets[i] != segment.offset[i]))
            return false;
    const std::array<double, 3> delta{after[0] - before[0], after[1] - before[1],
                                      after[2] - before[2]};
    if (!finite3(delta, kAttachOffsetLimit))
        return false;
    const bool locked = (segment.flags & 16) != 0;
    const auto base_angles = effective_attach_angles(sample, segment.point, locked);
    double basis[3][3];
    source_basis(base_angles, basis);
    CameraPose next = offsets;
    for (int axis = 0; axis < 3; ++axis) {
        double local_delta = 0;
        for (int i = 0; i < 3; ++i)
            local_delta += delta[i] * basis[i][axis];
        next[axis] = std::clamp(offsets[axis] + local_delta, -kAttachOffsetLimit,
                                kAttachOffsetLimit);
    }
    bool turned = false;
    std::array<double, 3> angle_delta{};
    for (int axis = 0; axis < 3; ++axis) {
        const double difference = after[axis + 3] - before[axis + 3];
        if (!std::isfinite(difference))
            return false;
        angle_delta[axis] = std::remainder(difference, 360.0);
    }
    // Input may not push the composed view past straight up or down. A
    // smoothed view trails the composed one, so limit against the latter. The
    // target's own aim can still carry the view past; input can bring it back.
    const double pitch = std::remainder(current[3], 360.0);
    if (angle_delta[0] > 0)
        angle_delta[0] = std::max(0.0, std::min(angle_delta[0], 89.9 - pitch));
    else if (angle_delta[0] < 0)
        angle_delta[0] = std::min(0.0, std::max(angle_delta[0], -89.9 - pitch));
    for (int axis = 0; axis < 3; ++axis)
        turned = turned || std::abs(angle_delta[axis]) > 1e-10;
    if (turned) {
        const std::array<double, 3> desired_angles{
            current[3] + angle_delta[0], current[4] + angle_delta[1], current[5] + angle_delta[2]};
        std::array<double, 3> local_angles{};
        if (segment.flags & 8) {
            double camera_basis[3][3], local_basis[3][3];
            source_basis(desired_angles, camera_basis);
            relative_orientation(basis, camera_basis, local_basis);
            local_angles = basis_angles(local_basis);
        } else {
            for (int axis = 0; axis < 3; ++axis)
                local_angles[axis] = std::remainder(desired_angles[axis] - base_angles[axis], 360.0);
        }
        for (int axis = 0; axis < 3; ++axis)
            next[axis + 3] = local_angles[axis];
    }
    next[6] = after[6];
    offsets = next;
    return true;
}

void enforce_attach_clearance(const AttachSample& sample, const AttachSegment& segment,
                              CameraPose& pose) noexcept {
    if (!(segment.flags & 4) || !sample.valid)
        return;
    const auto base_angles = effective_attach_angles(sample, segment.point, (segment.flags & 16) != 0);
    double basis[3][3];
    source_basis(base_angles, basis);
    std::array<double, 3> center = sample.origin;
    if (segment.point == AttachPoint::eyes) {
        double body[3][3];
        source_basis(sample.angles, body);
        std::array<double, 3> local{};
        rotate(body, sample.eye_local, local);
        for (int i = 0; i < 3; ++i)
            center[i] += local[i];
    }
    double local[3]{};
    for (int axis = 0; axis < 3; ++axis)
        for (int i = 0; i < 3; ++i)
            local[axis] += (pose[i] - center[i]) * basis[i][axis];
    // A head POV should remain close to the hero. A small forward floor keeps
    // smoothing from drawing the lens back into the face/shoulders without
    // turning this view into the wide, generic bone camera below. This is a
    // spacing guide, not a mesh collision test; exact offset remains opt-in.
    if (segment.point == AttachPoint::bone && segment.bone_hash == attach_bone_hash("head") &&
        segment.offset[0] >= 0 && std::hypot(segment.offset[1], segment.offset[2]) <= 24.0) {
        const double correction = std::max(0.0, 4.0 - local[0]);
        for (int i = 0; i < 3; ++i)
            pose[i] += correction * basis[i][0];
        return;
    }
    // A bounded pose-point sphere is a guide, not model collision detection.
    const double radius = segment.point == AttachPoint::bone ? 48.0 : 30.0;
    const double remaining = radius * radius - local[1] * local[1] - local[2] * local[2];
    if (remaining <= 0)
        return;
    const double minimum_forward = std::sqrt(remaining);
    if (std::abs(local[0]) >= minimum_forward)
        return;
    const double sign = segment.offset[0] < 0 ? -1.0 : 1.0;
    const double correction = sign * minimum_forward - local[0];
    for (int i = 0; i < 3; ++i)
        pose[i] += correction * basis[i][0];
}

bool resolve_attach_view(const AttachSample& sample, const AttachSegment& segment,
                         CameraPose& out, AttachSmoothing& smoothing, double dt) noexcept {
    if (segment.flags & 8) {
        // Filter the point and its orientation together before composing the
        // authored camera. Filtering the resulting camera separately makes a
        // long offset sweep across the screen while its angle trails behind.
        AttachSegment point = segment;
        point.offset.fill(0);
        point.flags = segment.flags & 16;
        CameraPose frame = out;
        if (!resolve_attach_pose(sample, point, frame))
            return false;
        smoothing.apply_anchored(frame, sample.motion_anchor, dt, segment.smoothing,
                                 attach_smoothing_error_limit(segment));
        AttachSample filtered = sample;
        for (int i = 0; i < 3; ++i) {
            filtered.origin[i] = frame[i];
            filtered.angles[i] = filtered.aim[i] = frame[i + 3];
            filtered.eye_local[i] = 0;
        }
        if (!resolve_attach_pose(filtered, segment, out))
            return false;
        enforce_attach_clearance(filtered, segment, out);
    } else {
        if (!resolve_attach_pose(sample, segment, out))
            return false;
        smoothing.apply_anchored(out, sample.motion_anchor, dt, segment.smoothing,
                                 attach_smoothing_error_limit(segment));
        enforce_attach_clearance(sample, segment, out);
    }
    return true;
}

bool AttachTrack::load(const void* data, std::size_t bytes, double duration, std::string& error) {
    auto fail = [&error](const char* reason) {
        error = reason;
        return false;
    };
    error.clear();
    if (!std::isfinite(duration) || duration < 0)
        return fail("Attach track duration is invalid");
    if (bytes == 0) {
        AttachTrack empty;
        empty.duration_ = duration;
        *this = std::move(empty);
        return true;
    }
    if (!data || bytes < header_bytes || bytes > max_bytes3)
        return fail("Attach block has an invalid byte length");
    if (std::memcmp(data, "DLYATT01", 8) != 0)
        return fail("Attach block magic does not match");
    Reader reader(data);
    reader.skip(8);
    const auto version = reader.u32();
    const auto reserved = reader.u32();
    const auto count = reader.u32();
    if ((version != 1 && version != 2 && version != 3 && version != 4) ||
        reserved != 0 || count > max_segments)
        return fail("Attach block header is invalid");
    const std::size_t stride = version >= 3   ? segment_bytes3
                               : version == 2 ? segment_bytes2
                                              : segment_bytes;
    if (bytes != header_bytes + std::size_t(count) * stride)
        return fail("Attach block header is invalid");
    AttachTrack candidate;
    try {
        candidate.segments_.reserve(count);
        double previous_end = 0;
        for (std::uint32_t index = 0; index < count; ++index) {
            AttachSegment segment;
            segment.begin = reader.number();
            segment.end = reader.number();
            segment.flags = reader.u32();
            const auto point = reader.u32();
            segment.target.handle = reader.u32();
            segment.target.entity_id = reader.u32();
            segment.target.model = reader.u64();
            for (double& value : segment.offset)
                value = reader.number();
            segment.smoothing = reader.number();
            if (version >= 2)
                segment.bone_hash = reader.u64();
            if (version >= 3)
                segment.source_blend = reader.number();
            if (!std::isfinite(segment.begin) || !std::isfinite(segment.end) ||
                segment.begin != previous_end ||
                (segment.end < segment.begin ||
                 (segment.end == segment.begin &&
                  !(version >= 3 && index + 1 == count && segment.begin == duration))) ||
                segment.end > duration + 1e-6 || segment.flags > (version >= 4 ? 31u : 7u) ||
                ((segment.flags & 24) && !(segment.flags & 1)) ||
                point > static_cast<std::uint32_t>(version >= 2 ? AttachPoint::bone
                                                                : AttachPoint::weapon))
                return fail("Attach segments are not ordered and contiguous");
            if (!std::isfinite(segment.source_blend) || segment.source_blend < 0 ||
                segment.source_blend > 10)
                return fail("Source blend duration is invalid");
            segment.point = static_cast<AttachPoint>(point);
            if (segment.point == AttachPoint::bone && segment.bone_hash == 0)
                return fail("A bone attach segment has no bone identity");
            for (double value : segment.offset)
                if (!std::isfinite(value) || std::abs(value) > kAttachOffsetLimit)
                    return fail("Attach segment offset is invalid");
            if (!std::isfinite(segment.smoothing) || segment.smoothing < 0 ||
                segment.smoothing > kAttachSmoothingLimit)
                return fail("Attach segment smoothing is invalid");
            if ((segment.flags & 1) && !segment.target.handle && !segment.target.model)
                return fail("An enabled attach segment has no target identity");
            candidate.segments_.push_back(segment);
            previous_end = segment.end;
        }
        if (!candidate.segments_.empty() && candidate.segments_.front().begin != 0)
            return fail("Attach segment coverage must start at zero");
    } catch (const std::exception&) {
        return fail("Could not allocate attach track storage");
    }
    candidate.duration_ = duration;
    *this = std::move(candidate);
    return true;
}

const AttachSegment* AttachTrack::at(double phase) const noexcept {
    if (!std::isfinite(phase) || segments_.empty() || phase < segments_.front().begin)
        return nullptr;
    std::size_t low = 0, high = segments_.size();
    while (low < high) {
        const auto mid = low + (high - low) / 2;
        if (segments_[mid].begin <= phase)
            low = mid + 1;
        else
            high = mid;
    }
    return &segments_[low - 1];
}

double attach_blend_weight(double phase, double begin, double end) noexcept {
    if (!(end > begin))
        return 1;
    const double t = std::clamp((phase - begin) / (end - begin), 0.0, 1.0);
    return t * t * t * (t * (t * 6 - 15) + 10);
}
CameraPose blend_attach_poses(const CameraPose& from, const CameraPose& to,
                              double weight) noexcept {
    CameraPose result = to;
    const double w = std::clamp(weight, 0.0, 1.0);
    for (int axis = 0; axis < 6; ++axis) {
        const double delta =
            axis < 3 ? to[axis] - from[axis] : std::remainder(to[axis] - from[axis], 360.0);
        result[axis] = from[axis] + delta * w;
    }
    return result;
}
const AttachSegment* AttachTrack::arriving(double phase, double& weight) const noexcept {
    weight = 0;
    const auto current = at(phase);
    if (!current)
        return nullptr;
    const auto index = std::size_t(current - segments_.data());
    if (index + 1 >= segments_.size())
        return nullptr;
    const auto& next = segments_[index + 1];
    if (!(next.source_blend > 0))
        return nullptr;
    const double start = std::max(current->begin, next.begin - next.source_blend);
    if (phase < start || !(next.begin > start))
        return nullptr;
    weight = attach_blend_weight(phase, start, next.begin);
    return &next;
}

const AttachSegment* AttachTrack::first_enabled() const noexcept {
    for (const auto& segment : segments_)
        if (segment.flags & 1)
            return &segment;
    return nullptr;
}

} // namespace dolly
