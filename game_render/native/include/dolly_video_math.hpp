#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace dolly::video {
inline bool frame_admission_open(std::uint64_t submitted, std::uint32_t limit) noexcept {
    return !limit || submitted < limit;
}
// Canonical normalization for the paired depth master and its preview: depths
// at or beyond this positive camera-axis distance are the far plane (sky).
constexpr double kDepthUnitMax = 8192.0;
// Linear camera-axis depth to the depth.mov input (gray16le). 0..max maps to
// 0..65535; non-finite values (sky) and at-or-beyond-far map to 65535.
inline std::uint16_t depth_gray16(float value, double max_units) noexcept {
    if (!std::isfinite(value) || value < 0)
        return 65535;
    const double clamped = std::clamp(double(value) / max_units, 0.0, 1.0);
    return static_cast<std::uint16_t>(std::lround(clamped * 65535.0));
}
// Integer quotient/remainder avoids accumulating rounded frame periods.
inline std::uint64_t clock_units(std::uint64_t ticks, std::uint64_t frequency,
                                 std::uint64_t units) noexcept {
    if (!frequency)
        return 0;
    return ticks / frequency * units + ticks % frequency * units / frequency;
}
// Fixed-step samples end at the next rational frame boundary. Using the
// stop wall clock here stretches the final frame when rendering is slow.
inline std::uint64_t final_sample_end(std::uint64_t previous_pts, std::uint32_t fixed_fps,
                                      std::uint64_t elapsed_pts) noexcept {
    if (!fixed_fps)
        return std::max(elapsed_pts, previous_pts + 1);
    const auto frame = clock_units(previous_pts, 10000000, fixed_fps);
    auto end = clock_units(frame + 1, fixed_fps, 10000000);
    if (end <= previous_pts)
        end = clock_units(frame + 2, fixed_fps, 10000000);
    return end;
}
// Bounded diagnostic trace for explicitly capped takes. The render producer
// owns writes; the encoder reads only after ready=false and producer quiescence.
struct CaptureTrace {
    enum Outcome : unsigned { other, admitted, cadence_wait, repeated_phase, queue_full, count };
    struct Sample {
        std::uint64_t entry = 0, readback = 0, end = 0, missed = 0;
        double phase = 0;
        unsigned pending = 0;
        Outcome outcome = other;
    };
    std::array<Sample, 512> samples{};
    std::size_t size = 0;
    Sample* begin(bool enabled, double phase, std::uint64_t entry, unsigned pending) noexcept {
        if (!enabled || !std::isfinite(phase) || phase < 0 || size == samples.size())
            return nullptr;
        auto& sample = samples[size++];
        sample.entry = entry;
        sample.phase = phase;
        sample.pending = pending;
        return &sample;
    }
};
struct Cadence {
    std::uint64_t frequency = 1, start = 0, last_slot = 0;
    std::uint32_t fps = 30;
    bool started = false;
    bool sample(std::uint64_t now, std::uint64_t& pts, std::uint64_t& missed) noexcept {
        missed = 0;
        if (!started) {
            started = true;
            start = now;
            last_slot = 0;
            pts = 0;
            return true;
        }
        if (now < start)
            return false;
        const auto elapsed = now - start;
        const auto slot = clock_units(elapsed, frequency, fps);
        if (slot <= last_slot)
            return false;
        missed = slot - last_slot - 1;
        last_slot = slot;
        pts = clock_units(elapsed, frequency, 10000000);
        return true;
    }
};

// Bounded provenance for written shot frames. No allocation or I/O occurs on
// the render thread. The sidecar retains the first samples and whole-take counts.
struct ShotClockTrace {
    struct Sample {
        std::uint64_t frame = 0;
        double phase = 0;
        bool native = false;
    };
    std::array<Sample, 256> samples{};
    std::size_t count = 0;
    std::uint64_t native_frames = 0, fallback_frames = 0;
    void observe(std::uint64_t frame, double phase, bool native) noexcept {
        if (!std::isfinite(phase) || phase < 0)
            return;
        if (native)
            ++native_frames;
        else
            ++fallback_frames;
        if (count < samples.size())
            samples[count++] = {frame, phase, native};
    }
};

// Encoded-shot range for the sidecar metadata: the first and last output
// frame captured while a replay-time phase was supplied, plus the matching
// replay times. Frames without a phase (pre-roll, tails, manual recording)
// never extend the range.
struct ShotRange {
    std::uint64_t first = 0, last = 0;
    double first_time = -1, last_time = -1;
    bool open() const noexcept { return first_time >= 0; }
    void observe(std::uint64_t frame, double replay_time) noexcept {
        if (!(replay_time >= 0))
            return;
        if (!open()) {
            first = last = frame;
            first_time = last_time = replay_time;
            return;
        }
        last = frame;
        last_time = replay_time;
    }
};

// Full-range SDR RGB to limited-range BT.709 NV12. Both rows and chroma
// samples stay top-down. RGB bytes are read explicitly; alpha is ignored.
// Width and height must be positive/even and both buffers suitably sized.
inline void rgb_to_nv12(const std::uint8_t* source, std::size_t stride, std::uint8_t* destination,
                        std::uint32_t width, std::uint32_t height, bool rgba) noexcept {
    auto* chroma = destination + std::size_t(width) * height;
    const auto byte = [](int value, int low, int high) {
        return std::uint8_t(std::max(low, std::min(high, value)));
    };
    const auto rounded = [](int value) {
        return value >= 0 ? (value + 131072) / 262144 : -((-value + 131072) / 262144);
    };
    for (std::uint32_t y = 0; y < height; y += 2) {
        for (std::uint32_t x = 0; x < width; x += 2) {
            int red = 0, green = 0, blue = 0;
            for (unsigned dy = 0; dy < 2; ++dy) {
                const auto* row = source + std::size_t(y + dy) * stride;
                for (unsigned dx = 0; dx < 2; ++dx) {
                    const auto* pixel = row + std::size_t(x + dx) * 4;
                    const int r = pixel[rgba ? 0 : 2], g = pixel[1], b = pixel[rgba ? 2 : 0];
                    red += r;
                    green += g;
                    blue += b;
                    destination[std::size_t(y + dy) * width + x + dx] =
                        byte(16 + (11966 * r + 40254 * g + 4064 * b + 32768) / 65536, 16, 235);
                }
            }
            // Average the complete 2x2 block before chroma quantization.
            chroma[std::size_t(y / 2) * width + x] =
                byte(128 + rounded(-6596 * red - 22189 * green + 28784 * blue), 16, 240);
            chroma[std::size_t(y / 2) * width + x + 1] =
                byte(128 + rounded(28784 * red - 26145 * green - 2639 * blue), 16, 240);
        }
    }
}
}
