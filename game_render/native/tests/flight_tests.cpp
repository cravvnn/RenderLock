#include "dolly_flight.hpp"
#include "dolly_editor.hpp"
#include <limits>
#include <stdexcept>
#include <cmath>
#include <iostream>
using namespace dolly;
static void require(bool condition) {
    if (!condition)
        throw std::runtime_error("Native flight integration invariant failed");
}
static bool near_value(double a, double b) {
    return std::abs(a - b) < 1e-7;
}
int main() {
    EditorCameraList cameras{};
    std::memcpy(cameras.magic, "DLYCAMS1", 8);
    cameras.abi = 1;
    cameras.revision = 7;
    cameras.total = 40;
    cameras.first = 32;
    cameras.count = 8;
    cameras.flags = 3;
    for (unsigned i = 0; i < cameras.count; ++i)
        cameras.rows[i] = {double(i + 32), 16.0 / 9, 0, 0, 0};
    require(valid_editor_camera_list(cameras));
    require(valid_editor_camera_action(cameras, EditorAction::DeleteCamera, 39, 7));
    require(!valid_editor_camera_action(cameras, EditorAction::DeleteCamera, 39, 6));
    require(!valid_editor_camera_action(cameras, EditorAction::DeleteCamera, 31, 7));
    require(!valid_editor_camera_action(cameras, EditorAction::DeleteCamera, 40, 7));
    require(!valid_editor_camera_action(cameras, EditorAction::DeleteCamera, 32.5, 7));
    CameraPose position_request{7, 2, 1234.125, 32, 0, 0, 0};
    require(valid_editor_position_action(cameras, 32, position_request));
    position_request[0]=6; require(!valid_editor_position_action(cameras,32,position_request));
    position_request[0]=7;position_request[1]=.5;
    require(!valid_editor_position_action(cameras,32,position_request));
    position_request[1]=3; require(!valid_editor_position_action(cameras,32,position_request));
    position_request[1]=0;position_request[2]=1e8+1;
    require(!valid_editor_position_action(cameras,32,position_request));
    position_request[2]=0;position_request[3]=33;
    require(!valid_editor_position_action(cameras,32,position_request));
    position_request[3]=32;cameras.rows[0].source=1;
    require(!valid_editor_position_action(cameras,32,position_request));
    cameras.rows[0].source=0;position_request[2]=std::numeric_limits<double>::quiet_NaN();
    require(!valid_editor_position_action(cameras,32,position_request));
    require(valid_editor_camera_action(cameras, EditorAction::CameraPage, 0, 7));
    require(!valid_editor_camera_action(cameras, EditorAction::CameraPage, 40, 7));
    require(valid_editor_camera_action(cameras, EditorAction::UndoShot, 0, 7));
    require(valid_editor_camera_action(cameras, EditorAction::RedoShot, 0, 7));
    cameras.flags = 0;
    require(!valid_editor_camera_action(cameras, EditorAction::UndoShot, 0, 7));
    require(!valid_editor_camera_action(cameras, EditorAction::RedoShot, 0, 7));
    cameras.rows[1].time = cameras.rows[0].time;
    require(!valid_editor_camera_list(cameras));
    cameras.rows[1].time = 33;
    cameras.rows[0].aspect = std::numeric_limits<double>::quiet_NaN();
    require(!valid_editor_camera_list(cameras));
    CameraPose start = {100, 200, 300, 0, 0, 0, 16.0 / 9};
    FlightInput input{};
    input.forward = 1;
    CameraPose at60 = start, at120 = start;
    for (int i = 0; i < 60; ++i)
        integrate_flight(at60, input, 1.0 / 60, 400, .08);
    for (int i = 0; i < 120; ++i)
        integrate_flight(at120, input, 1.0 / 120, 400, .08);
    require(near_value(at60[0], 500));
    for (int i = 0; i < 7; ++i)
        require(near_value(at60[i], at120[i]));
    // World Z is up; right is -Y at yaw0, +X at yaw90.
    CameraPose right = start;
    input = {};
    input.right = 1;
    integrate_flight(right, input, .025, 400, .08);
    require(near_value(right[1], 190));
    right = start;
    right[4] = 90;
    integrate_flight(right, input, .025, 400, .08);
    require(near_value(right[0], 110));
    CameraPose rise = start;
    input = {};
    input.up = 1;
    integrate_flight(rise, input, .025, 400, .08);
    require(near_value(rise[2], 310));
    // Diagonal travel has the same speed; mouse sensitivity is independent of dt.
    CameraPose diagonal = start;
    input.forward = 1;
    integrate_flight(diagonal, input, .025, 400, .08);
    double distance = std::hypot(diagonal[0] - 100, diagonal[2] - 300);
    require(near_value(distance, 10));
    input = {};
    input.mouse_x = 10;
    input.mouse_y = 20;
    CameraPose mouseA = start, mouseB = start;
    integrate_flight(mouseA, input, .01, 400, .08);
    integrate_flight(mouseB, input, .02, 400, .08);
    require(near_value(mouseA[3], 1.6) && near_value(mouseA[4], -.8));
    require(near_value(mouseA[3], mouseB[3]) && near_value(mouseA[4], mouseB[4]));
    CameraPose inverted = start;
    integrate_flight(inverted, input, .01, 400, .08, true);
    require(near_value(inverted[3], -1.6));
    // A suspend/refocus backlog is discarded, and pitching cannot cross poles.
    CameraPose stalled = start;
    input.forward = 1;
    integrate_flight(stalled, input, 4, 400, .08);
    require(stalled == start);
    input = {};
    input.mouse_y = 100000;
    integrate_flight(stalled, input, .01, 400, .08);
    require(near_value(stalled[3], 89.9));
    // A pitch the game reports just under 360 is a view slightly above level,
    // not one past straight down.
    CameraPose wrapped = start;
    wrapped[3] = 435.1;
    input = {};
    integrate_flight(wrapped, input, .01, 400, .08);
    require(near_value(wrapped[3], 75.1));
    input.mouse_y = -100;
    integrate_flight(wrapped, input, .01, 400, .08);
    require(near_value(wrapped[3], 67.1));
    require(near_value(flight_pitch(359.3), -.7) && near_value(flight_pitch(95), 89.9) &&
            near_value(flight_pitch(-95), -89.9));
    // Projection and authored bank are not silently changed by translation.
    require(near_value(at60[5], start[5]) && near_value(at60[6], start[6]));
    std::cout << "Native flight integration tests passed.\n";
}
