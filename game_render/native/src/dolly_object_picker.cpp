#include "dolly_object_picker.hpp"
#include <algorithm>
#include <cmath>

namespace dolly {
namespace {
constexpr double kPi = 3.14159265358979323846;
using Vec3 = std::array<double, 3>;

bool finite(const Vec3& v) noexcept {
    return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]);
}

// Same Source convention as dolly_visualization: Z up, yaw 0 faces +X, positive
// pitch looks down. Kept local so this unit owns no cross-module coupling.
struct Basis {
    Vec3 forward{}, right{}, up{};
};

Basis angle_vectors(const CameraPose& p) noexcept {
    constexpr double radians = kPi / 180;
    const double pitch = std::remainder(p[3], 360.0) * radians;
    const double yaw = std::remainder(p[4], 360.0) * radians;
    const double roll = std::remainder(p[5], 360.0) * radians;
    const double sp = std::sin(pitch), cp = std::cos(pitch), sy = std::sin(yaw), cy = std::cos(yaw);
    const double sr = std::sin(roll), cr = std::cos(roll);
    return {{cp * cy, cp * sy, -sp},
            {-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp},
            {cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp}};
}

double dot(const Vec3& a, const Vec3& b) noexcept {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

bool valid_view(const VisualizationView& view) noexcept {
    return std::isfinite(view.horizontal_fov) && view.horizontal_fov > 1 &&
           view.horizontal_fov < 179 && std::isfinite(view.width) && std::isfinite(view.height) &&
           view.width > 0 && view.height > 0 && view.width <= 65536 && view.height <= 65536;
}
} // namespace

bool object_screen_ray(const VisualizationView& view, double screen_x, double screen_y,
                       std::array<double, 3>& direction) noexcept {
    if (!valid_view(view) || !std::isfinite(screen_x) || !std::isfinite(screen_y))
        return false;
    const double tan_x = std::tan(view.horizontal_fov * kPi / 360);
    const double tan_y = tan_x / (view.width / view.height);
    if (!std::isfinite(tan_x) || !std::isfinite(tan_y) || tan_x <= 0 || tan_y <= 0)
        return false;
    // Normalized device coordinates in [-1, 1]; +x right, +y up, +z forward.
    const double ndc_x = (screen_x / view.width) * 2.0 - 1.0;
    const double ndc_y = 1.0 - (screen_y / view.height) * 2.0;
    const Basis basis = angle_vectors(view.pose);
    Vec3 dir{basis.forward[0] + basis.right[0] * ndc_x * tan_x + basis.up[0] * ndc_y * tan_y,
             basis.forward[1] + basis.right[1] * ndc_x * tan_x + basis.up[1] * ndc_y * tan_y,
             basis.forward[2] + basis.right[2] * ndc_x * tan_x + basis.up[2] * ndc_y * tan_y};
    const double length = std::sqrt(dot(dir, dir));
    if (!std::isfinite(length) || length <= 0)
        return false;
    direction = {dir[0] / length, dir[1] / length, dir[2] / length};
    return finite(direction);
}

bool object_place_distance(const std::array<double, 3>& origin,
                           const std::array<double, 3>& direction, double distance,
                           std::array<double, 3>& point) noexcept {
    if (!finite(origin) || !finite(direction) || !std::isfinite(distance) || distance <= 0)
        return false;
    point = {origin[0] + direction[0] * distance,
             origin[1] + direction[1] * distance,
             origin[2] + direction[2] * distance};
    return finite(point);
}

bool object_place_on_plane(const std::array<double, 3>& origin,
                           const std::array<double, 3>& direction, double plane_z,
                           std::array<double, 3>& point) noexcept {
    if (!finite(origin) || !finite(direction) || !std::isfinite(plane_z))
        return false;
    if (std::abs(direction[2]) < 1e-9)
        return false;  // Parallel to the plane.
    const double t = (plane_z - origin[2]) / direction[2];
    if (!std::isfinite(t) || t <= 0)
        return false;  // Behind the camera or degenerate.
    point = {origin[0] + direction[0] * t, origin[1] + direction[1] * t, plane_z};
    return finite(point);
}

bool object_place_screen(const VisualizationView& view, double screen_x, double screen_y,
                         double distance, std::array<double, 3>& point) noexcept {
    std::array<double, 3> direction{};
    if (!object_screen_ray(view, screen_x, screen_y, direction))
        return false;
    const std::array<double, 3> origin{view.pose[0], view.pose[1], view.pose[2]};
    return object_place_distance(origin, direction, distance, point);
}

bool object_place_view(const VisualizationView& view, double screen_x, double screen_y,
                       PlaceMode mode, double distance, double plane_z,
                       std::array<double, 3>& point) noexcept {
    std::array<double, 3> direction{};
    if (!object_screen_ray(view, screen_x, screen_y, direction))
        return false;
    const std::array<double, 3> origin{view.pose[0], view.pose[1], view.pose[2]};
    if (mode == PlaceMode::Ground && object_place_on_plane(origin, direction, plane_z, point))
        return true;
    // Distance mode, or a ground ray that misses the plane (parallel/behind).
    return object_place_distance(origin, direction, distance, point);
}

void object_face_camera(double camera_yaw, const std::array<double, 3>& authored,
                        std::array<double, 3>& angles) noexcept {
    angles = authored;
    if (std::isfinite(camera_yaw))
        angles[1] = camera_yaw;  // Face the same heading as the camera.
}

bool object_clamp_bounds(std::array<double, 3>& point, double limit) noexcept {
    if (!finite(point) || !std::isfinite(limit) || limit <= 0)
        return false;
    point[0] = std::clamp(point[0], -limit, limit);
    point[1] = std::clamp(point[1], -limit, limit);
    point[2] = std::clamp(point[2], -limit, limit);
    return true;
}

bool object_snap_point(std::array<double, 3>& point, double step) noexcept {
    if (!finite(point) || !std::isfinite(step) || step <= 0)
        return false;
    for (double& value : point)
        value = std::round(value / step) * step;
    return finite(point);
}

double object_snap_angle(double angle, double step) noexcept {
    if (!std::isfinite(angle) || !std::isfinite(step) || step <= 0)
        return angle;
    const double snapped = std::round(angle / step) * step;
    if (!std::isfinite(snapped))
        return angle;
    return std::remainder(snapped, 360.0);
}

void object_camera_axes(const CameraPose& pose, std::array<double, 3>& right,
                        std::array<double, 3>& up) noexcept {
    const Basis b = angle_vectors(pose);
    right = b.right;
    up = b.up;
}

bool object_move_in_view_plane(const VisualizationView& view,
                               const std::array<double, 3>& origin, double screen_dx,
                               double screen_dy, std::array<double, 3>& delta) noexcept {
    if (!valid_view(view) || !finite(origin) || !std::isfinite(screen_dx) ||
        !std::isfinite(screen_dy))
        return false;
    Vec3 right{}, up{};
    object_camera_axes(view.pose, right, up);
    const double probe = 50.0;
    VisualizationPoint base{}, rtip{}, utip{};
    const Vec3 rprobe{origin[0] + right[0] * probe, origin[1] + right[1] * probe,
                      origin[2] + right[2] * probe};
    const Vec3 uprobe{origin[0] + up[0] * probe, origin[1] + up[1] * probe,
                      origin[2] + up[2] * probe};
    if (!project_visualization_point(view, origin, base) ||
        !project_visualization_point(view, rprobe, rtip) ||
        !project_visualization_point(view, uprobe, utip))
        return false;
    // Screen basis: right axis projected (x), and screen-up is -y for the up axis.
    const double rx = double(rtip.x) - double(base.x), ry = double(rtip.y) - double(base.y);
    const double ux = double(utip.x) - double(base.x), uy = double(utip.y) - double(base.y);
    // Invert the 2x2 mapping [rx ux; ry uy] * (a, b) = (screen_dx, screen_dy) so a
    // grab-style drag moves the object with the cursor.
    const double det = rx * uy - ux * ry;
    if (!std::isfinite(det) || std::abs(det) < 1e-6)
        return false;
    // [rx ux; ry uy] * (a,b) = probe * (screen_dx, screen_dy).
    const double a = (probe * screen_dx * uy - probe * screen_dy * ux) / det;  // along right
    const double b = (rx * probe * screen_dy - ry * probe * screen_dx) / det;  // along up
    if (!std::isfinite(a) || !std::isfinite(b))
        return false;
    delta = {right[0] * a + up[0] * b, right[1] * a + up[1] * b, right[2] * a + up[2] * b};
    return finite(delta);
}

double object_rotate_from_drag(double base_yaw, double screen_dx) noexcept {
    if (!std::isfinite(base_yaw) || !std::isfinite(screen_dx))
        return base_yaw;
    // ~0.5 degrees per pixel; positive drag turns right (increasing yaw).
    return std::remainder(base_yaw + screen_dx * 0.5, 360.0);
}

double object_scale_from_drag(double base_scale, double screen_dy, double min_scale,
                              double max_scale) noexcept {
    if (!std::isfinite(base_scale) || !std::isfinite(screen_dy) || base_scale <= 0)
        return base_scale;
    // Drag up (negative dy) grows the object.
    const double factor = std::exp(-screen_dy / 200.0);
    const double result = base_scale * factor;
    if (!std::isfinite(result))
        return base_scale;
    return std::clamp(result, min_scale, max_scale);
}

bool object_drop_step(DropState& state, double dt, double gravity, double floor_z,
                      double restitution) noexcept {
    if (!std::isfinite(state.z) || !std::isfinite(state.velocity) ||
        !std::isfinite(dt) || !std::isfinite(gravity) || !std::isfinite(floor_z) ||
        !std::isfinite(restitution) || gravity <= 0)
        return false;
    if (state.resting)
        return false;
    // Bound dt so a long stall cannot integrate a huge step and tunnel.
    const double step = std::clamp(dt, 0.0, 0.05);
    state.velocity -= gravity * step;  // positive gravity accelerates downward
    state.z += state.velocity * step;
    if (state.z <= floor_z) {
        state.z = floor_z;
        if (std::abs(state.velocity) < 40.0 || restitution <= 0.0) {
            state.velocity = 0;
            state.resting = true;
            return false;
        }
        state.velocity = -state.velocity * std::clamp(restitution, 0.0, 1.0);
    }
    return true;
}

} // namespace dolly
