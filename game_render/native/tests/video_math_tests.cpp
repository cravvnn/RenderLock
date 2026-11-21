#include "dolly_video_math.hpp"
#include "dolly_encoder_error.hpp"
#include "dolly_capture_timing.hpp"
#include <array>
#include <cstdlib>
#include <cstdio>
#include <limits>

void require(bool value) {
    if (!value) {
        std::fputs("Video math regression failed.\n", stderr);
        std::exit(1);
    }
}

int main() {
    using namespace dolly::video;
    const std::wstring first = L"Driver does not support the required nvenc API version.";
    const std::wstring log = L"\r\n[h264_nvenc @ 00000123] " + first + L"\r\n" +
        std::wstring(700, L'x') + L"\n[out @ 0123] Nothing was written into output file, "
        L"because at least one of its streams received no packets.\n";
    require(encoder_error_summary(log) == first);
    require(encoder_error_summary(L"Permission denied\nNothing was written") == L"Permission denied");
    require(encoder_error_summary(L" \r\n\t").empty());
    require(encoder_error_summary(L"[unprefixed] failure") == L"[unprefixed] failure");
    require(encoder_error_summary(std::wstring(200, L'x')) == std::wstring(107, L'x') + L"...");
    require(encoder_error_summary(L"error", 0).empty());
    CaptureTrace capture_trace;
    require(!capture_trace.begin(false, 0, 1, 0));
    require(!capture_trace.begin(true, -1, 1, 0));
    require(!capture_trace.begin(true, std::numeric_limits<double>::quiet_NaN(), 1, 0));
    require(capture_trace.size == 0);
    for (unsigned i = 0; i < 512; ++i) {
        auto* sample = capture_trace.begin(true, i / 120.0, i + 1, 2);
        require(sample && sample->entry == i + 1 && sample->pending == 2);
        sample->outcome = CaptureTrace::admitted;
    }
    require(!capture_trace.begin(true, 5, 999, 0));
    require(capture_trace.size == 512 && capture_trace.samples[0].entry == 1 &&
            capture_trace.samples[511].entry == 512);

    require(frame_admission_open(0, 2) && frame_admission_open(1, 2));
    require(!frame_admission_open(2, 2) && !frame_admission_open(1000, 2));
    require(frame_admission_open(std::numeric_limits<std::uint64_t>::max(), 0));
    ShotClockTrace trace;
    trace.observe(0, -1, false);
    trace.observe(0, std::numeric_limits<double>::quiet_NaN(), true);
    require(trace.count == 0);
    for (unsigned i = 0; i < 1000; ++i)
        trace.observe(i, i / 60.0, i % 2 == 0);
    require(trace.count == 256 && trace.native_frames == 500 && trace.fallback_frames == 500);
    require(trace.samples[0].frame == 0 && trace.samples[0].native);
    require(trace.samples[255].frame == 255 && !trace.samples[255].native);
    require(trace.samples[255].phase == 255 / 60.0);

    // Recorded startup repeats must not create a gap in real-time video.
    // At120rendered frames/s, a30Hz replay clock can repeat four times.
    for (bool fixed : {false, true}) {
        Cadence clock;
        clock.frequency = 120000;
        clock.fps = 60;
        double previous = -1;
        unsigned admitted = 0;
        std::uint64_t total_missed = 0;
        for (unsigned i = 0; i < 120; ++i) {
            const double phase = (i / 4) / 30.0;
            if (!dolly::capture_video_phase(previous, phase, fixed, true, false))
                continue;
            previous = phase;
            std::uint64_t stamp = 0, dropped = 0;
            if (clock.sample(i * 1000, stamp, dropped)) {
                ++admitted;
                total_missed += dropped;
            }
        }
        require(admitted == (fixed ? 30u : 60u));
        require(fixed || total_missed == 0);
    }
    require(!dolly::capture_video_phase(1, 1, false, true, true));
    require(!dolly::capture_video_phase(1, 1, false, false, false));
    require(!dolly::capture_video_phase(1, .9, false, true, false));
    require(!dolly::capture_video_phase(0, -1, false, true, false));
    require(dolly::capture_video_phase(.9, 1, false, true, true));
    Cadence cadence;
    cadence.frequency = 10000000;
    cadence.fps = 60;
    std::uint64_t pts = 99, missed = 99;
    require(cadence.sample(50000000, pts, missed) && pts == 0 && missed == 0);
    require(!cadence.sample(50050000, pts, missed));
    require(cadence.sample(50170000, pts, missed) && pts == 170000 && missed == 0);
    require(cadence.sample(51000000, pts, missed) && pts == 1000000 && missed == 4);
    require(!cadence.sample(49999999, pts, missed));
    require(!cadence.sample(51000000, pts, missed));
    require(clock_units(3600ULL * 3579545, 3579545, 10000000) == 36000000000ULL);
    require(clock_units(1, 0, 10000000) == 0);
    // A slow export must not append wall-clock time to its final frame.
    require(final_sample_end(37166666, 60, 43996730) == 37333333);
    require(final_sample_end(37166666, 60, 10000000) == 37333333);
    require(final_sample_end(0, 60, 90000000) == 166666);
    for (const auto fps : {30u, 60u, 120u, 300u, 600u}) {
        for (const auto frame : {0ull, 1ull, 2ull, 59ull, 223ull, 2160000ull}) {
            const auto start = clock_units(frame, fps, 10000000);
            const auto expected = clock_units(frame + 1, fps, 10000000);
            require(final_sample_end(start, fps, 900000000000ull) == expected);
        }
    }
    // Real-time recordings continue to preserve elapsed time through stop.
    require(final_sample_end(200000, 0, 350000) == 350000);
    require(final_sample_end(200000, 0, 150000) == 200001);
    Cadence ntsc_clock;
    ntsc_clock.frequency = 3579545;
    ntsc_clock.fps = 30;
    require(ntsc_clock.sample(0, pts, missed));
    require(ntsc_clock.sample(3579545, pts, missed) && pts == 10000000 && missed == 29);
    // At 120 Hz the rational timestamp clock must not drift over one hour.
    // A 60 Hz game cannot supply 120 distinct frames: account for skipped slots.
    Cadence high_rate;
    high_rate.frequency = 12000000;
    high_rate.fps = 120;
    require(high_rate.sample(0, pts, missed));
    require(high_rate.sample(100000, pts, missed) && pts == 83333 && missed == 0);
    require(!high_rate.sample(100001, pts, missed));
    require(high_rate.sample(300000, pts, missed) && pts == 250000 && missed == 1);
    require(high_rate.sample(3600ULL * high_rate.frequency, pts, missed) && pts == 36000000000ULL &&
            high_rate.last_slot == 432000);
    // RGB ordering and row stride are independent of Windows' RGB DIB
    // conventions. Top is red, bottom blue; padding must never be sampled.
    const std::array<std::uint8_t, 24> rgba = {255, 0, 0,   255, 255, 0, 0,   255, 99, 99, 99, 99,
                                               0,   0, 255, 255, 0,   0, 255, 255, 99, 99, 99, 99};
    std::array<std::uint8_t, 6> nv12{};
    rgb_to_nv12(rgba.data(), 12, nv12.data(), 2, 2, true);
    require(nv12[0] == 63 && nv12[1] == 63);
    require(nv12[2] == 32 && nv12[3] == 32);
    const std::array<std::uint8_t, 16> bgra_red = {0, 0, 255, 255, 0, 0, 255, 255,
                                                   0, 0, 255, 255, 0, 0, 255, 255};
    rgb_to_nv12(bgra_red.data(), 8, nv12.data(), 2, 2, false);
    require(nv12[0] == 63 && nv12[4] == 102 && nv12[5] == 240);
    std::array<std::uint8_t, 16> solid{};
    rgb_to_nv12(solid.data(), 8, nv12.data(), 2, 2, true);
    require(nv12[0] == 16 && nv12[4] == 128 && nv12[5] == 128);
    solid.fill(255);
    rgb_to_nv12(solid.data(), 8, nv12.data(), 2, 2, true);
    require(nv12[0] == 235 && nv12[4] == 128 && nv12[5] == 128);
    for (unsigned i = 0; i < 4; ++i) {
        solid[i * 4] = 0;
        solid[i * 4 + 1] = 255;
        solid[i * 4 + 2] = 0;
    }
    rgb_to_nv12(solid.data(), 8, nv12.data(), 2, 2, true);
    require(nv12[0] == 173 && nv12[4] == 42 && nv12[5] == 26);
    // Sidecar shot range: only frames captured with a replay-time phase count.
    ShotRange shot;
    require(!shot.open());
    shot.observe(5, -1.0);
    require(!shot.open());
    shot.observe(3, 0.0);
    require(shot.open() && shot.first == 3 && shot.last == 3);
    require(shot.first_time == 0.0 && shot.last_time == 0.0);
    shot.observe(9, 0.1);
    require(shot.first == 3 && shot.last == 9 && shot.last_time == 0.1);
    shot.observe(11, -1.0);
    require(shot.last == 9 && shot.last_time == 0.1);
    // Paired depth master mapping: 0..kDepthUnitMax spans the full 16-bit
    // range linearly; sky and invalid values clamp to white.
    require(depth_gray16(0.0f, kDepthUnitMax) == 0);
    require(depth_gray16(float(kDepthUnitMax), kDepthUnitMax) == 65535);
    require(depth_gray16(float(kDepthUnitMax / 2), kDepthUnitMax) == 32768);
    require(depth_gray16(float(kDepthUnitMax * 4), kDepthUnitMax) == 65535);
    require(depth_gray16(-1.0f, kDepthUnitMax) == 65535);
    require(depth_gray16(std::numeric_limits<float>::infinity(), kDepthUnitMax) == 65535);
    require(depth_gray16(std::numeric_limits<float>::quiet_NaN(), kDepthUnitMax) == 65535);
    require(depth_gray16(0.25f, 1.0) == 16384);
    std::puts(
        "Video cadence, timestamps, color conversion, depth mapping and row orientation passed.");
}
