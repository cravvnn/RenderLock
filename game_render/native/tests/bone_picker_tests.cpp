#include "dolly_bone_picker.hpp"
#include <cstring>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace dolly;
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
bool sample(void* context, PickerSample& result, const char*& error) noexcept {
    if (!context) {
        error = "Identity changed";
        return false;
    }
    result = *static_cast<PickerSample*>(context);
    return true;
}
int main() {
    try {
        auto catalog = std::make_shared<PickerCatalog>();
        catalog->sequence = 42;
        catalog->total = 700;
        catalog->handle = 11;
        catalog->entity = 11;
        catalog->model = 123;
        static PickerSample pose;
        pose.count = 700;
        const char* names[] = {"head", "hand_R", "pelvis", "ankle_L", "cloth_12"};
        const unsigned indices[] = {13, 622, 8, 30, 680};
        for (unsigned i = 0; i < 5; ++i) {
            PickerBone bone{};
            bone.source_index = indices[i];
            std::strcpy(bone.name, names[i]);
            catalog->bones.push_back(bone);
            pose.transforms[indices[i]][0] = 10 + float(i);
            pose.transforms[indices[i]][1] = 20 + float(i);
            pose.transforms[indices[i]][2] = 100 - 20 * float(i);
        }
        std::array<double, 3> point{};
        require(picker_position(pose, catalog->bones[1], point) && point[2] == 80,
                "Catalog row must use source index 622, not compact row 1");
        require(picker_matches(catalog->bones[4], "CLOTH", true),
                "Full-rig search bypasses common filter");
        require(!picker_matches(catalog->bones[4], "", true), "Cloth excluded from body default");
        float marker_x = 0, marker_y = 0;
        bool initialized = false;
        picker_stabilize_marker(100, 200, 1, marker_x, marker_y, initialized);
        picker_stabilize_marker(101, 199, 1, marker_x, marker_y, initialized);
        require(marker_x == 100 && marker_y == 200, "Paused subpixel drift remains still");
        picker_stabilize_marker(110, 200, 1, marker_x, marker_y, initialized);
        require(marker_x > 100 && marker_x < 110, "Small real movement eases");
        picker_stabilize_marker(200, 300, 1, marker_x, marker_y, initialized);
        require(marker_x == 200 && marker_y == 300, "Large pose changes update immediately");
        require(picker_matches(catalog->bones[1], "right hand", true), "Friendly-name search");
        auto invalid = catalog->bones[1];
        invalid.source_index = 700;
        require(!picker_position(pose, invalid, point), "Reject index at buffer boundary");
        pose.transforms[622][0] = std::numeric_limits<float>::quiet_NaN();
        require(!picker_position(pose, catalog->bones[1], point), "Reject nonfinite marker");
        pose.transforms[622][0] = 11;
        const CameraPose original = {200, 300, 400, 12, 35, 0, 16.0 / 9.0};
        auto view = original;
        require(picker_front_view(*catalog, pose, 90, view), "Frame body bounds");
        VisualizationView projection{view, 90, 2560, 1440, 1};
        for (unsigned i = 0; i < 4; ++i) {
            VisualizationPoint screen{};
            require(picker_position(pose, catalog->bones[i], point) &&
                        project_visualization_point(projection, point, screen),
                    "Every common joint framed");
        }
        picker_publish(catalog);
        view = original;
        require(picker_camera(42, 10, true, true, view, 90, {}, sample, &pose), "Enter inspection");
        require(view != original, "Overview uses temporary native view");
        const auto front = view;
        std::array<double, 3> center{};
        auto calculated = original;
        require(picker_front_view(*catalog, pose, 90, calculated, &center),
                "Orbit pivot available");
        auto distance = [&](const CameraPose& camera) {
            return std::sqrt(std::pow(camera[0] - center[0], 2) +
                             std::pow(camera[1] - center[1], 2) +
                             std::pow(camera[2] - center[2], 2));
        };
        const auto radius = distance(front);
        require(!picker_orbit(41, 10, 0), "Stale orbit refused");
        require(!picker_orbit(42, std::numeric_limits<double>::infinity(), 0),
                "Nonfinite orbit refused");
        for (unsigned step = 0; step < 72; ++step) {
            require(picker_orbit(42, 5, 0), "Accept full-circle orbit input");
            PickerFrame before{};
            require(picker_snapshot(before) && before.view == view,
                    "Present input cannot move projection ahead of camera");
            pose.transforms[13][0] += 1;
            picker_camera(42, 10, true, true, view, 90, {}, sample, &pose);
            PickerFrame after{};
            require(picker_snapshot(after) && after.sample.transforms[13][0] == 10,
                    "Orbit preserves paused bone pose despite animated sampler drift");
            require(std::abs(distance(view) - radius) < 1e-8, "Orbit radius is invariant");
            VisualizationPoint target{};
            require(project_visualization_point({view, 90, 2560, 1440, 1}, center, target) &&
                        std::abs(target.x - 1280) < 1e-6 && std::abs(target.y - 720) < 1e-6,
                    "Orbit keeps looking at the fixed hero center");
        }
        pose.transforms[13][0] = 10;
        for (unsigned axis = 0; axis < 6; ++axis)
            require(std::abs(front[axis] - view[axis]) < 1e-8,
                    "Full circle returns to opening view");
        require(picker_orbit(42, 0, 100), "Vertical orbit accepted");
        picker_camera(42, 10, true, true, view, 90, {}, sample, &pose);
        require(view[3] == 85 && std::abs(distance(view) - radius) < 1e-8,
                "Elevation clamps before pole without changing distance");
        require(picker_orbit(42, 0, -85), "Return to level orbit");
        picker_camera(42, 10, true, true, view, 90, {}, sample, &pose);
        require(!picker_select(40, 1), "Stale catalog click refused");
        require(picker_select(42, 1) && picker_preview(42, true), "Choose hand and preview");
        require(!picker_orbit(42, 10, 0), "Attached preview rejects overview input");
        picker_camera(42, 10, true, true, view, 90, {}, sample, &pose);
        require(std::abs(view[0] - 11) < .001 && std::abs(view[2] - 80) < .001,
                "Attached preview follows the selected source transform");
        require(picker_preview(42, false), "Return to overview without closing list");
        picker_camera(42, 10, true, true, view, 90, {}, sample, &pose);
        require(view[0] != 11, "Overview restored");
        require(picker_finish(42, 1), "Confirm current selected bone");
        require(!picker_orbit(42, 10, 0), "Finishing picker rejects orbit");
        require(!picker_select(42, 2), "Selection frozen while awaiting editor acknowledgement");
        PickerResult result{};
        picker_result(result);
        require(result.flags & 8 && std::strcmp(result.name, "hand_R") == 0 && result.selected == 1,
                "Complete exact name beyond old 256-row cap");
        picker_camera(44, 10, false, true, view, 90, {}, nullptr, nullptr);
        require(view == original, "Cancel/close restores original pose exactly");
        auto head_catalog = std::make_shared<PickerCatalog>(*catalog);
        head_catalog->sequence = 46;
        picker_publish(head_catalog);
        require(picker_camera(46, 12, true, true, view, 90, {}, sample, &pose),
                "Reopen picker for head clearance preview");
        require(picker_select(46, 0) && picker_preview(46, true), "Preview head bone");
        const std::array<double, 6> head_offset = {0, 0, 6, 0, 0, 0};
        picker_camera(46, 12, true, true, view, 90, head_offset, sample, &pose, nullptr, true);
        require(std::abs(view[0] - 14) < .001 && std::abs(view[2] - 106) < .001,
                "Automatic picker head preview uses close forward clearance");
        picker_camera(46, 12, true, true, view, 90, head_offset, sample, &pose, nullptr, false);
        require(std::abs(view[0] - 10) < .001 && std::abs(view[2] - 106) < .001,
                "Exact picker head preview preserves authored offset");
        pose.transforms[13][0] = 12;
        picker_camera(46, 12, true, true, view, 90, head_offset, sample, &pose, nullptr, false);
        require(std::abs(view[0] - 10) < .001,
                "Paused picker preview does not follow oscillating bone data");
        PickerFrame held_frame;
        require(picker_snapshot(held_frame) && held_frame.sample.transforms[13][0] == 10,
                "Paused markers use the same held sample as the attached preview");
        picker_camera(46, 12, true, true, view, 90, head_offset, sample, &pose, nullptr, false, 1);
        require(std::abs(view[0] - 12) < .001, "New paused tick updates picker pose");
        pose.aim = {45, 0, 0};
        const std::array<double, 6> face_offset = {100, 0, 0, 0, 180, 0};
        picker_camera(46, 12, true, true, view, 90, face_offset, sample, &pose, nullptr,
                      false, 2, true);
        require(std::abs(view[3] + 45) < .001 && std::abs(std::abs(view[4]) - 180) < .001,
                "Picker preview preserves rigid snap orientation");
        picker_camera(46, 12, true, true, view, 90, face_offset, sample, &pose, nullptr,
                      false, 2, false);
        require(std::abs(view[3] - 45) < .001,
                "Picker preview retains legacy attached-camera orientation");
        picker_camera(46, 12, true, true, view, 90, face_offset, sample, &pose, nullptr,
                      false, 2, true, true);
        require(std::abs(view[3]) < .001 && std::abs(std::abs(view[4]) - 180) < .001 &&
                    std::abs(view[0] - 112) < .001,
                "Picker preview must use the same level-aim frame as playback");
        pose.aim = {0, 0, 0};
        pose.transforms[13][0] = 10;
        picker_camera(48, 12, false, true, view, 90, {}, nullptr, nullptr);
        picker_publish(catalog);
        picker_camera(42, 10, true, true, view, 90, {}, sample, &pose);
        picker_camera(42, 10, true, true, view, 90, {}, sample, nullptr);
        require(view == original && !picker_select(42, 1),
                "Identity failure restores and invalidates picks");
        CameraPose newer = {1, 2, 3, 4, 5, 6, 1.5};
        view = newer;
        picker_camera(42, 11, false, true, view, 90, {}, nullptr, nullptr);
        require(view == newer, "Close must not overwrite a newer camera command");
        std::cout << "Bone picker tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
