#pragma once

#include "dolly_path.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace dolly {

// Attach camera (POV / weapon) payload and resolver.
//
// The Python editor compiles one attach segment per camera time range. An
// enabled segment replaces the free path pose for its range with a live player
// or weapon transform plus authored local offsets; a disabled segment keeps
// the free path. Source changes cut unless the arriving segment opts into
// an eased blend ending at its boundary. The
// per-frame provider fills AttachSample from bounded game reads and calls
// resolve_attach_pose, which performs no allocation, lock, engine call or
// clock access.

enum class AttachPoint : std::uint32_t {
    eyes = 0,
    weapon = 1,
    bone = 2,
};

constexpr std::uint64_t attach_bone_hash(const char* name) noexcept {
    std::uint64_t hash = 14695981039346656037ull;
    for (const char* p = name; *p; ++p) {
        hash ^= static_cast<unsigned char>(*p);
        hash *= 1099511628211ull;
    }
    return hash;
}

struct AttachTarget {
    std::uint32_t handle = 0;    // Packed replay pawn handle.
    std::uint32_t entity_id = 0; // Replay entity identifier, 0 when unknown.
    std::uint64_t model = 0;     // Stable model identity, 0 when unknown.
};

struct AttachSegment {
    double begin = 0, end = 0; // Shot seconds; coverage is contiguous from zero.
    std::uint32_t flags = 0;   // 1 enabled, 2 hide, 4 clearance, 8 rigid, 16 vertical aim lock.
    AttachPoint point = AttachPoint::eyes;
    AttachTarget target;
    // Local XYZ and pitch/yaw/roll offsets in the resolved frame.
    std::array<double, 6> offset{};
    double smoothing = 0; // Exponential smoothing time constant (seconds).
    // Version 2: FNV-1a 64 of the bone name for AttachPoint::bone.
    std::uint64_t bone_hash = 0;
    double source_blend = 0; // Version 3: eased arrival, ending at begin.
};

// Describes what the provider actually resolved, not the editor's highlighted
// choice. Picker overview resolves Eyes while its highlighted choice may be Head.
struct AttachResolution {
    AttachPoint point = AttachPoint::eyes;
    std::uint64_t bone_hash = 0;
    bool matches(const AttachSegment& requested) const noexcept {
        return point == requested.point &&
               (point != AttachPoint::bone || bone_hash == requested.bone_hash);
    }
};

// One live sample of the selected target. The provider fills this from bounded
// reads; a partial, stale or identity-mismatched sample stays invalid.
struct AttachSample {
    bool valid = false;
    AttachPoint point = AttachPoint::eyes;
    AttachTarget target;
    std::array<double, 3> eye_local{}; // Decoded m_vecViewOffset.
    std::array<double, 3> origin{};    // Target world origin.
    std::array<double, 3>
        motion_anchor{};            // Current scene origin; bone positions are rebased to it.
    std::array<double, 3> angles{}; // Body angles (eyes) or node angles (weapon).
    std::array<double, 3> aim{};    // Eye angles, used by the eyes point.
};

// Call only after validating a fresh sample. Paused replay pose buffers may
// still oscillate; retain one pose until playback resumes or the tick changes.
// Reset on camera command/target changes. This never bypasses provider checks.
template <typename Sample> class PausedAttachSample {
public:
    void reset() noexcept { held_ = false; }
    void apply(Sample& sample, bool paused, int tick) noexcept {
        if (!paused) {
            held_ = false;
            return;
        }
        if (!held_ || tick != tick_) {
            sample_ = sample;
            tick_ = tick;
            held_ = true;
        } else {
            sample = sample_;
        }
    }

private:
    Sample sample_{};
    int tick_ = 0;
    bool held_ = false;
};

// Completed mesh poses are sampled after animation interpolation. Predict only
// the local bone translation for the immediately following view; the current
// scene anchor and aim always remain live. The caller scopes history by target.
class AttachRenderHistory {
public:
    void reset() noexcept { count_ = 0; }
    void record(const AttachSample& sample, double time, std::uint64_t frame) noexcept;
    bool apply(AttachSample& sample, double time, std::uint64_t frame) const noexcept;

private:
    struct Pose {
        std::array<double, 3> local{};
        double time = 0;
        std::uint64_t frame = 0;
    };
    Pose previous_{}, latest_{};
    unsigned count_ = 0;
};

// While a replay plays, the game can draw a player one tick behind for a frame
// or a few when a tick arrives early, then snap it forward. A camera riding
// that player makes the whole scene jump. Recognise the step back by its size
// (one tick of the player's own movement) and carry the camera along the
// uninterrupted path until the matching step forward. Reset on camera command
// or target changes.
class AttachAnchorFilter {
public:
    void reset() noexcept;
    void apply(AttachSample& sample, double replay_time, double tick_interval,
               bool paused) noexcept;
    bool lagging() const noexcept { return lagging_; }

private:
    std::array<std::array<double, 3>, 3> velocity_{};
    std::array<double, 3> anchor_{}, shift_{};
    double time_ = 0;
    unsigned count_ = 0, held_ = 0;
    bool primed_ = false, lagging_ = false;
};

// A replay stores a player's aim in coarse steps and refreshes it unevenly, so
// a camera that turns with the aim turns in jerks. Ease the aim over a few
// hundredths of a second of replay time. A paused replay shows the stored aim
// exactly, which is what offsets are authored against. Reset on camera command
// or target changes.
inline constexpr double kAttachAimEase = 0.02;
class AttachAimFilter {
public:
    void reset() noexcept { primed_ = false; }
    void apply(AttachSample& sample, double replay_time, bool paused) noexcept;

private:
    std::array<double, 3> first_{}, second_{};
    double time_ = 0;
    bool primed_ = false;
};

// Exponential pose smoothing across rendered views. reset() on mode or target
// changes; apply_anchored follows whole-player movement before filtering.
class AttachSmoothing {
public:
    void reset() noexcept;
    bool primed() const noexcept { return primed_; }
    void apply(CameraPose& pose, double dt, double tau) noexcept;
    void apply_anchored(CameraPose& pose, const std::array<double, 3>& anchor, double dt,
                        double tau, double max_error) noexcept;

private:
    CameraPose pose_{};
    std::array<double, 3> anchor_{};
    bool primed_ = false;
};

inline constexpr double kAttachViewOffsetLimit = 200.0;
inline constexpr double kAttachOffsetLimit = 10000.0;
// A typed position offset is free of the slider's fine range, but the attach
// action refuses anything past the limit. Keep the entry at the limit so the
// field does not snap back to the old value without a reason.
inline float attach_offset_entry(float typed, double saved) noexcept {
    if (std::isnan(typed))
        return float(saved);
    return std::clamp(typed, -float(kAttachOffsetLimit), float(kAttachOffsetLimit));
}
inline constexpr double kAttachSmoothingLimit = 5.0;
// No geometry query is available here. Keep the filtered pose close to its
// resolved bone/eye point so it cannot trail deep into the moving character.
inline double attach_smoothing_error_limit(const AttachSegment& segment) noexcept {
    const auto& offset = segment.offset;
    const double distance = std::hypot(offset[0], offset[1], offset[2]);
    return std::min(3.0, std::max(0.5, distance * 0.2));
}

// Validate the three floats read at m_vecViewOffset +16/+24/+32.
bool attach_view_offset(const std::array<float, 3>& values, std::array<double, 3>& out) noexcept;

// Verified display name for a model path; nullptr for an unknown stem so the
// caller can show a readable stem instead of guessing a hero.
const char* attach_hero_name(const char* model_path) noexcept;

// Fill out[0..5] from the sample and segment; out[6] is preserved so the
// authored aspect keeps applying. Refuses a missing sample, a point or
// identity mismatch, and nonfinite values.
bool resolve_attach_pose(const AttachSample& sample, const AttachSegment& segment,
                         CameraPose& out) noexcept;
// Approximate clearance from the selected pose point. Head POV uses a close
// forward floor; other bones use a wider guide. No rendered mesh query is
// available, so animated limbs can still enter the view. Leaves rotation intact.
void enforce_attach_clearance(const AttachSample& sample, const AttachSegment& segment,
                              CameraPose& pose) noexcept;

// Resolve the visible attached view and its smoothing together. Rigid snaps
// smooth the target frame first so position and orientation remain paired.
bool resolve_attach_view(const AttachSample& sample, const AttachSegment& segment,
                         CameraPose& out, AttachSmoothing& smoothing, double dt) noexcept;

// Authored offsets that place the attached camera exactly at ``camera`` for the
// given live sample. Rigid orientation composes the target and camera frames;
// the default keeps the component-wise convention used by older saved shots.
// Returns false on nonfinite data or out-of-range offsets.
bool attach_snap_offsets(const AttachSample& sample, const CameraPose& camera,
                         std::array<double, 6>& out, bool rigid_orientation = false,
                         bool lock_vertical_aim = false) noexcept;
// Transfer camera-space flight to the authored target-relative offsets. The
// selected segment supplies the same aim-lock and rotation mode as playback.
// Invalid input is refused without partially changing the offsets.
bool attach_apply_camera_motion(const AttachSample& sample, const AttachSegment& segment,
                                const CameraPose& before, const CameraPose& after,
                                CameraPose& offsets) noexcept;
// Quintic easing gives zero slope and acceleration at both ends. Angles take
// their shortest route; aspect stays with the existing authored lens curve.
double attach_blend_weight(double phase, double begin, double end) noexcept;
CameraPose blend_attach_poses(const CameraPose& from, const CameraPose& to, double weight) noexcept;

class AttachTrack {
public:
    static constexpr std::size_t max_segments = 256;
    static constexpr std::size_t header_bytes = 20;
    static constexpr std::size_t segment_bytes = 96;
    static constexpr std::size_t segment_bytes2 = 104;
    static constexpr std::size_t segment_bytes3 = 112;
    static constexpr std::size_t max_bytes = header_bytes + max_segments * segment_bytes;
    static constexpr std::size_t max_bytes2 = header_bytes + max_segments * segment_bytes2;
    static constexpr std::size_t max_bytes3 = header_bytes + max_segments * segment_bytes3;

    // Load one DLYATT01 block on the non-render thread. Zero bytes is a valid
    // empty track (every camera key is free). Failure leaves a previous valid
    // object untouched.
    bool load(const void* data, std::size_t bytes, double duration, std::string& error);

    // Hard cuts: the segment covering phase, or nullptr before the first
    // segment. The caller checks the enabled flag.
    const AttachSegment* at(double phase) const noexcept;
    const AttachSegment* arriving(double phase, double& weight) const noexcept;
    // First enabled segment, used by the worker to resolve a target once.
    const AttachSegment* first_enabled() const noexcept;
    bool empty() const noexcept { return segments_.empty(); }
    const std::vector<AttachSegment>& segments() const noexcept { return segments_; }
    double duration() const noexcept { return duration_; }

private:
    std::vector<AttachSegment> segments_;
    double duration_ = 0;
};

} // namespace dolly
