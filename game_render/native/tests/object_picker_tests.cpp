#include "dolly_object_picker.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace dolly;
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
int main() {
    try {
        const CameraPose pose = {100, 200, 300, 0, 0, 0, 16.0 / 9.0};  // yaw 0 faces +X
        VisualizationView view{pose, 90, 1600, 900, 1};
        std::array<double, 3> dir{};

        // Center of screen must look straight down camera forward (+X for yaw 0).
        require(object_screen_ray(view, 800, 450, dir), "Center ray");
        require(std::abs(dir[0] - 1) < 1e-9 && std::abs(dir[1]) < 1e-9 && std::abs(dir[2]) < 1e-9,
                "Center ray is +X at yaw 0");

        // Right of center leans toward camera right. In Source coordinates yaw 0
        // faces +X and the camera's right is -Y, matching the verified
        // projection basis in dolly_visualization.
        require(object_screen_ray(view, 1600, 450, dir), "Right ray");
        require(dir[1] < -0.5 && std::abs(dir[2]) < 1e-9, "Right ray leans -Y");

        // Above center leans toward camera up (+Z for pitch 0). A 90-degree
        // horizontal FOV on 16:9 is a narrower vertical FOV, so the vertical
        // component is positive but smaller than the forward component.
        require(object_screen_ray(view, 800, 0, dir), "Top ray");
        require(dir[2] > 0.3 && dir[0] > 0.5, "Top ray leans +Z");

        // Non-finite and invalid-view rejection.
        require(!object_screen_ray(view, std::numeric_limits<double>::infinity(), 0, dir),
                "Reject nonfinite screen point");
        VisualizationView bad = view;
        bad.horizontal_fov = 0;
        require(!object_screen_ray(bad, 800, 450, dir), "Reject invalid FOV");

        // Fixed-distance placement.
        std::array<double, 3> point{};
        require(object_place_screen(view, 800, 450, 500, point), "Place at center");
        require(std::abs(point[0] - 600) < 1e-6 && std::abs(point[1] - 200) < 1e-6 &&
                    std::abs(point[2] - 300) < 1e-6,
                "Center place is 500 units ahead");
        require(!object_place_screen(view, 800, 450, 0, point), "Reject zero distance");
        require(!object_place_screen(view, 800, 450, -5, point), "Reject negative distance");

        // Ground/plane placement. Camera at z=300 looking level never hits z=0.
        const std::array<double, 3> level{1, 0, 0};
        require(!object_place_on_plane({100, 200, 300}, level, 0, point), "Level ray misses plane");
        const std::array<double, 3> down{1, 0, -1};
        require(object_place_on_plane({0, 0, 100}, down, 0, point), "Downward ray hits plane");
        require(std::abs(point[2]) < 1e-9 && std::abs(point[0] - 100) < 1e-6,
                "Plane hit lands at the ray/plane crossing");
        // Ray above the plane pointing further up can never cross it.
        const std::array<double, 3> upward{1, 0, 1};
        require(!object_place_on_plane({0, 0, 100}, upward, 0, point),
                "Ray pointing away from the plane is rejected");
        // Ray below the plane pointing further down also misses it.
        const std::array<double, 3> downward2{1, 0, -1};
        require(!object_place_on_plane({0, 0, 100}, downward2, 200, point),
                "Ray behind the plane is rejected");

        // Face-camera rotation keeps pitch/roll, adopts camera yaw.
        std::array<double, 3> angles{};
        object_face_camera(45.0, {10, 20, 30}, angles);
        require(angles[0] == 10 && angles[1] == 45 && angles[2] == 30, "Face camera adopts yaw only");
        object_face_camera(std::numeric_limits<double>::quiet_NaN(), {10, 20, 30}, angles);
        require(angles[1] == 20, "Nonfinite camera yaw leaves authored yaw");

        // Bounds clamp.
        std::array<double, 3> far{5e6, -5e6, 100};
        require(object_clamp_bounds(far, 100000.0), "Clamp within limit");
        require(far[0] == 100000.0 && far[1] == -100000.0 && far[2] == 100.0, "Clamp bounds applied");
        std::array<double, 3> nan{std::numeric_limits<double>::quiet_NaN(), 0, 0};
        require(!object_clamp_bounds(nan, 100000.0), "Reject nonfinite point");

        // Grid snap: nearest multiple per axis; a bad step leaves it untouched.
        std::array<double, 3> snap{17.0, -9.0, 31.5};
        require(object_snap_point(snap, 16.0), "Snap point");
        require(snap[0] == 16.0 && snap[1] == -16.0 && snap[2] == 32.0, "Snap rounds to grid");
        std::array<double, 3> unsnapped{17.0, -9.0, 31.5};
        require(!object_snap_point(unsnapped, 0.0), "Reject zero snap step");
        require(unsnapped[0] == 17.0 && unsnapped[2] == 31.5, "Rejected snap leaves point unchanged");
        std::array<double, 3> nan_snap{std::numeric_limits<double>::quiet_NaN(), 0, 0};
        require(!object_snap_point(nan_snap, 16.0), "Reject nonfinite snap point");

        // Angle snap wraps into [-180, 180); a bad step is returned unchanged.
        require(std::abs(object_snap_angle(7.0, 15.0) - 0.0) < 1e-9, "Angle snaps down");
        require(std::abs(object_snap_angle(8.0, 15.0) - 15.0) < 1e-9, "Angle snaps up");
        require(std::abs(object_snap_angle(359.0, 45.0)) < 1e-9, "Angle wraps 360 to 0");
        require(object_snap_angle(30.0, 0.0) == 30.0, "Reject zero angle step");
        require(std::isnan(object_snap_angle(std::numeric_limits<double>::quiet_NaN(), 15.0)),
                "Nonfinite angle is unchanged (NaN stays NaN)");

        // Unified placement mode: distance always resolves; ground hits the
        // plane when aimed at it and falls back to distance when it misses.
        std::array<double, 3> mode_point{};
        require(object_place_view(view, 800, 450, PlaceMode::Distance, 500, 0, mode_point),
                "Distance mode places");
        require(std::abs(mode_point[0] - 600) < 1e-6, "Distance mode is 500 ahead as before");
        const CameraPose high_pose = {0, 0, 500, 0, 0, 0, 16.0 / 9.0};
        VisualizationView down_view{high_pose, 90, 1600, 900, 1};
        require(object_place_view(down_view, 800, 900, PlaceMode::Ground, 500, 0, mode_point),
                "Ground mode places on a plane");
        require(std::abs(mode_point[2]) < 1e-6, "Ground mode lands on the plane");
        // Looking level never crosses the plane, so ground falls back to ahead.
        const CameraPose level_pose = {0, 0, 500, 0, 0, 0, 16.0 / 9.0};
        VisualizationView level_view{level_pose, 90, 1600, 900, 1};
        require(object_place_view(level_view, 800, 450, PlaceMode::Ground, 500, 0, mode_point),
                "Ground mode falls back when the ray misses");
        require(std::abs(mode_point[2] - 500) < 1e-6, "Ground fallback is the distance point");

        // --- Gizmo math ---
        VisualizationView gview{pose, 90, 1600, 900, 1};
        // Camera axes at yaw 0: right is -Y, up is +Z (Source convention).
        std::array<double, 3> right{}, up{};
        object_camera_axes(pose, right, up);
        require(std::abs(right[0]) < 1e-9 && std::abs(right[1] + 1.0) < 1e-9, "Camera right at yaw 0");
        require(std::abs(up[2] - 1.0) < 1e-9, "Camera up at yaw 0");

        // Point 600 units ahead of the camera (camera at 100,200,300 faces +X).
        const std::array<double, 3> ahead{700, 200, 300};
        // Move in the view plane: a rightward drag moves the object toward camera
        // right; an upward drag (negative screen y) moves it toward camera up.
        std::array<double, 3> delta{};
        require(object_move_in_view_plane(gview, ahead, 100, 0, delta), "Move right drag");
        require(delta[1] < -1.0 && std::abs(delta[2]) < 1e-6, "Right drag moves -Y");
        require(object_move_in_view_plane(gview, ahead, 0, -100, delta), "Move up drag");
        require(delta[2] > 1.0, "Up drag moves +Z");
        // A zero drag does not move.
        require(object_move_in_view_plane(gview, ahead, 0, 0, delta), "Zero drag");
        require(std::abs(delta[0]) + std::abs(delta[1]) + std::abs(delta[2]) < 1e-9,
                "Zero drag is no movement");

        // Rotate: drag right increases yaw, wraps at 360.
        require(std::abs(object_rotate_from_drag(0, 100) - 50.0) < 1e-9, "Rotate right");
        require(std::abs(object_rotate_from_drag(0, -100) + 50.0) < 1e-9, "Rotate left");
        require(std::abs(object_rotate_from_drag(350, 40)) < 360.0, "Rotate wraps");
        require(object_rotate_from_drag(30, std::numeric_limits<double>::quiet_NaN()) == 30,
                "Nonfinite rotate drag keeps yaw");

        // Scale: drag up grows, drag down shrinks, clamped.
        require(object_scale_from_drag(1.0, -200, 0.05, 20) > 1.0, "Drag up grows");
        require(object_scale_from_drag(1.0, 200, 0.05, 20) < 1.0, "Drag down shrinks");
        require(object_scale_from_drag(1.0, -100000, 0.05, 20) == 20.0, "Scale clamps high");
        require(object_scale_from_drag(1.0, 100000, 0.05, 20) == 0.05, "Scale clamps low");

        // --- Client-side drop physics ---
        // Started above the floor, the object falls, bounces, and settles.
        DropState drop{1000.0, 0.0, false};
        bool moving = true;
        int steps = 0;
        while (moving && steps < 100000) {
            moving = object_drop_step(drop, 1.0 / 60.0, 1200.0, 0.0, 0.4);
            ++steps;
        }
        require(!moving && drop.resting, "Drop settles");
        require(std::abs(drop.z) < 1e-6, "Drop rests on the floor");
        require(steps > 10 && steps < 100000, "Drop took a finite number of steps");
        // Resting state is idempotent.
        require(!object_drop_step(drop, 1.0 / 60.0, 1200.0, 0.0, 0.4), "Resting drop does not move");
        // A huge dt cannot tunnel far below the floor.
        DropState fast{100.0, -5000.0, false};
        object_drop_step(fast, 10.0, 1200.0, 0.0, 0.5);
        require(fast.z >= 0.0, "Clamped dt prevents tunneling below the floor");
        // Zero restitution stops on first contact.
        DropState flat{100.0, 0.0, false};
        object_drop_step(flat, 1.0 / 60.0, 1200.0, 0.0, 0.0);
        for (int i = 0; i < 100000 && !flat.resting; ++i)
            object_drop_step(flat, 1.0 / 60.0, 1200.0, 0.0, 0.0);
        require(flat.resting && flat.velocity == 0, "Zero restitution settles immediately");
        // Non-finite inputs are rejected.
        DropState nonfinite_drop{0, 0, false};
        require(!object_drop_step(nonfinite_drop, std::numeric_limits<double>::quiet_NaN(), 1200, 0,
                                  0.4),
                "Nonfinite dt rejected");

        std::cout << "Object picker tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
