#pragma once
#include "dolly_path.hpp"
#include "dolly_visualization.hpp"
#include <array>
#include <cstdint>

namespace dolly {

// Pure placement math for the Object Picker. No engine access, no allocation,
// no clock, no D3D work. The native UI unit supplies a camera view and a screen
// point; these helpers turn them into a world placement. Rendering (proxy or
// engine model) is a different owner.

// Screen point (CSS pixels) -> normalized camera-space ray direction inside the
// view basis. ``view.horizontal_fov`` and viewport size come from the verified
// main-view callback. Returns false for non-finite or behind-camera input.
bool object_screen_ray(const VisualizationView& view, double screen_x, double screen_y,
                       std::array<double, 3>& direction) noexcept;

// Place a fixed distance along a ray from world ``origin``.
bool object_place_distance(const std::array<double, 3>& origin,
                           const std::array<double, 3>& direction, double distance,
                           std::array<double, 3>& point) noexcept;

// Intersect a ray with the horizontal plane at world Z ``plane_z``.
// A ray parallel to the plane, or one pointing away from it, is rejected.
bool object_place_on_plane(const std::array<double, 3>& origin,
                           const std::array<double, 3>& direction, double plane_z,
                           std::array<double, 3>& point) noexcept;

// Convenience: screen point -> world point at a fixed distance, using a view.
bool object_place_screen(const VisualizationView& view, double screen_x, double screen_y,
                         double distance, std::array<double, 3>& point) noexcept;

// Placement mode for the Object Picker. Distance places the object a fixed
// distance ahead of the camera (always valid); Ground intersects the aim ray
// with the horizontal plane at ``plane_z`` and falls back to Distance when the
// ray is parallel to, or pointing away from, the plane.
enum class PlaceMode : std::uint32_t { Distance = 0, Ground = 1 };

// Resolve a scene click into a world placement point using the selected mode.
// Returns false for a non-finite screen point or an invalid view.
bool object_place_view(const VisualizationView& view, double screen_x, double screen_y,
                       PlaceMode mode, double distance, double plane_z,
                       std::array<double, 3>& point) noexcept;

// Rotate an object's authored angles so ``yaw`` follows the camera facing,
// while pitch/roll are preserved. Used when a placed object should look at the
// viewer by default.
void object_face_camera(double camera_yaw, const std::array<double, 3>& authored,
                        std::array<double, 3>& angles) noexcept;

// Clamp a placement into the reviewed world bounds. Returns false when the point
// is non-finite; otherwise the point is clamped in place and true is returned.
bool object_clamp_bounds(std::array<double, 3>& point, double limit) noexcept;

// Snap a world point to a grid of ``step`` world units per axis (nearest
// multiple). Returns false for a non-finite point or a non-positive step, in
// which case the point is left unchanged so a disabled snap never disturbs it.
bool object_snap_point(std::array<double, 3>& point, double step) noexcept;

// Snap an angle (degrees) to the nearest multiple of ``step``, wrapped into
// [-180, 180). A non-finite angle or non-positive step is returned unchanged.
double object_snap_angle(double angle, double step) noexcept;

// --- Gizmo math -----------------------------------------------------------
// Camera right/up world axes from a pose (Source convention, matching the
// projection basis). Always usable for a drag plane; unlike a fixed world axis
// these are never aimed straight at the camera.
void object_camera_axes(const CameraPose& pose, std::array<double, 3>& right,
                        std::array<double, 3>& up) noexcept;

// Move a world point in the camera-facing plane by a mouse drag. Screen pixels
// map to world units using the projected scale of the camera axes at ``origin``
// (one world unit at the object's depth). Fills ``delta`` with the world offset.
// Returns false when the axes are degenerate at this view.
bool object_move_in_view_plane(const VisualizationView& view,
                               const std::array<double, 3>& origin, double screen_dx,
                               double screen_dy, std::array<double, 3>& delta) noexcept;

// Rotate yaw (degrees) from a horizontal drag; positive drag turns right.
double object_rotate_from_drag(double base_yaw, double screen_dx) noexcept;

// Scale from a vertical drag, exponential so it never goes negative; clamped to
// [min_scale, max_scale].
double object_scale_from_drag(double base_scale, double screen_dy, double min_scale,
                              double max_scale) noexcept;

// --- Client-side drop physics ---------------------------------------------
// Dolly's own simple vertical drop, independent of the engine: a paused replay
// does not step the game's physics, so "dropping" is a presentation-side
// animation that carries a just-placed object down to a floor and lets it
// settle. Pure and deterministic; the editor never stores the in-flight state.
struct DropState {
    double z = 0;        // current height (world Z)
    double velocity = 0; // vertical velocity (world units / second)
    bool resting = false;
};

// Advance one step. ``gravity`` is positive-down (units/s^2), ``floor_z`` the
// rest height, ``restitution`` the bounce factor (0..1). Returns true while the
// object is still moving, false once it has come to rest. dt is clamped to a
// sane bound so a stalled frame cannot tunnel the object far below the floor.
bool object_drop_step(DropState& state, double dt, double gravity, double floor_z,
                      double restitution) noexcept;

} // namespace dolly
