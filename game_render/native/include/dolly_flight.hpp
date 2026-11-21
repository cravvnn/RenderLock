#pragma once
#include <algorithm>
#include <cmath>
#include "dolly_path.hpp"
namespace dolly {
// Engine uses Z up; yaw0 looks +X, positive pitch looks down. Integrate from
// real rendered-frame time, independently of replay ticks or demo_timescale.
struct FlightInput {
    double forward = 0, right = 0, up = 0, pitch = 0, yaw = 0, roll = 0, mouse_x = 0, mouse_y = 0;
};
// A pitch the flight controls accept: signed, and short of straight up/down.
inline double flight_pitch(double pitch) noexcept {
    return std::clamp(std::remainder(pitch, 360.0), -89.9, 89.9);
}
inline void integrate_flight(CameraPose& p, const FlightInput& in, double dt, double speed,
                             double sensitivity, bool invert_y = false) noexcept {
    if (!std::isfinite(dt) || dt < 0 || dt > .1)
        return; // discard suspension/Alt-Tab backlog
    dt = std::min(dt, .05);
    constexpr double rad = 3.14159265358979323846 / 180;
    p[3] = std::clamp(flight_pitch(p[3]) + in.pitch * 75 * dt +
                          in.mouse_y * sensitivity * (invert_y ? -1 : 1),
                      -89.9, 89.9);
    p[4] = std::remainder(p[4] + in.yaw * 75 * dt - in.mouse_x * sensitivity, 360.0);
    p[5] = std::remainder(p[5] + in.roll * 60 * dt, 360.0);
    double pitch = p[3] * rad, yaw = p[4] * rad;
    double x = std::cos(pitch) * std::cos(yaw) * in.forward + std::sin(yaw) * in.right;
    double y = std::cos(pitch) * std::sin(yaw) * in.forward - std::cos(yaw) * in.right;
    double z = -std::sin(pitch) * in.forward + in.up;
    double norm = std::sqrt(x * x + y * y + z * z);
    if (norm > 1) {
        x /= norm;
        y /= norm;
        z /= norm;
    }
    p[0] += x * speed * dt;
    p[1] += y * speed * dt;
    p[2] += z * speed * dt;
}
}
