#pragma once
#include <cmath>
#include <cstdint>

namespace dolly {
// The replay tick and its render fraction share the playback clock. Client
// curtime can be corrected by simulation and is not a continuous camera clock.
inline bool replay_view_time(std::int32_t tick, double fraction, double interval,
                             double& time) noexcept {
    if (tick < 0 || !std::isfinite(fraction) || fraction < 0 || fraction > 1 ||
        !std::isfinite(interval) || interval < .001 || interval > .2)
        return false;
    time = (double(tick) + fraction) * interval;
    return true;
}
}
