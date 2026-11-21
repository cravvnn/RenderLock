#include "dolly_replay_clock.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <initializer_list>

static void require(bool okay) {
    if (!okay) {
        std::fputs("Replay clock regression failed.\n", stderr);
        std::exit(1);
    }
}
int main() {
    using dolly::replay_view_time;
    double a = 0, b = 0;
    // A two-tick packet boundary must not create the observed short/long pair.
    require(replay_view_time(50718, .9372723, 1.0 / 64, a));
    require(replay_view_time(50720, .003939043, 1.0 / 64, b));
    require(std::abs(b - a - 1.0 / 60) < 1e-8);
    // Case03: an absolute float curtime at tick17920 loses 8.138us
    // on the very first 30FPS step. Keep the measured fractional field
    // as float, but combine it with the integer tick in double precision.
    const float absolute_start = 280.0f;
    const float absolute_next = static_cast<float>(280.0 + 1.0 / 30);
    require(std::abs(double(absolute_next - absolute_start) - 1.0 / 30) > 1e-6);
    for (std::int32_t start_tick : {17920, 50705}) {
        require(replay_view_time(start_tick, 0, 1.0 / 64, a));
        for (int frame = 1; frame <= 1000; ++frame) {
            const double ticks = start_tick + frame * 64.0 / 30;
            const auto whole = static_cast<std::int32_t>(std::floor(ticks));
            const float observed_fraction = static_cast<float>(ticks - whole);
            require(replay_view_time(whole, observed_fraction, 1.0 / 64, b));
            require(std::abs((b - a) - frame / 30.0) < 1e-8);
        }
    }
    // Render rates and slow motion remain independent of replay tick rate.
    for (double speed : {.05, .5, 1.0, 4.0}) {
        for (double fps : {30.0, 60.0, 120.0, 300.0, 600.0}) {
            const double start = 50705.375;
            double previous = 0;
            require(replay_view_time(50705, .375, 1.0 / 64, previous));
            for (int frame = 1; frame <= 1000; ++frame) {
                const double ticks = start + frame * speed * 64 / fps;
                const auto whole = static_cast<std::int32_t>(std::floor(ticks));
                double current = 0;
                require(replay_view_time(whole, ticks - whole, 1.0 / 64, current));
                require(std::abs(current - previous - speed / fps) < 1e-10);
                previous = current;
            }
        }
    }
    require(replay_view_time(0, 0, 1.0 / 64, a) && a == 0);
    require(!replay_view_time(-1, 0, 1.0 / 64, a));
    require(!replay_view_time(1, -.001, 1.0 / 64, a));
    require(!replay_view_time(1, 1.001, 1.0 / 64, a));
    require(!replay_view_time(1, std::numeric_limits<double>::quiet_NaN(), 1.0 / 64, a));
    require(!replay_view_time(1, 0, 0, a));
    require(!replay_view_time(1, 0, std::numeric_limits<double>::infinity(), a));
    std::puts("Replay tick and render-fraction clock passed.");
}
