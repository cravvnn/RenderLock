#pragma once
#include <cmath>

namespace dolly {
// Replay sub-tick corrections can produce short/long steps. Admission follows
// actual advancing rendered views, never floor(phase * FPS).
inline bool capture_phase_advances(double previous, double phase) noexcept {
    return std::isfinite(phase) && phase >= 0 && phase > previous;
}
// Real-time video follows rendered wall time. An unchanged replay clock is
// still a valid rendered frame; suppress duplicates only for fixed-step/layer
// pairing, unverified fallback clocks, or the completed path endpoint.
inline bool capture_video_phase(double previous, double phase, bool fixed_step, bool native_clock,
                                bool completed) noexcept {
    return capture_phase_advances(previous, phase) ||
           (!fixed_step && native_clock && !completed && std::isfinite(phase) && phase >= 0 &&
            phase == previous);
}

enum class CaptureImageAdmission { skip, capture, missing, inconsistent };
inline CaptureImageAdmission capture_image_admission(double previous, double phase,
                                                     bool image) noexcept {
    if (!capture_phase_advances(previous, phase))
        return image ? CaptureImageAdmission::inconsistent : CaptureImageAdmission::skip;
    return image ? CaptureImageAdmission::capture : CaptureImageAdmission::missing;
}
} // namespace dolly
