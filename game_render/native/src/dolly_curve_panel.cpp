// Shared shot-time curve workspace. Rendering never evaluates or seeks the game.
#include "dolly_overlay_panel.hpp"
#include "dolly_lite.hpp"
#include "dolly_rail_design.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_ui_tokens_generated.hpp"
#include "imgui_internal.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace dolly {
namespace {
ImVec4 curve_color(unsigned rgb, float alpha = 1) {
    return ImVec4(float((rgb >> 16) & 255) / 255, float((rgb >> 8) & 255) / 255,
                  float(rgb & 255) / 255, alpha);
}
const char* const groups[]{"Position", "Rotation", "Framing"};
const char* const channels[]{"X", "Y", "Z", "Pitch", "Yaw", "Roll", "Aspect ratio"};
unsigned channel_color(unsigned channel) {
    return channel == 0   ? ui::CURVE_X
           : channel == 1 ? ui::CURVE_Y
           : channel == 2 ? ui::CURVE_Z
           : channel < 6  ? ui::CURVE_ROTATION
                          : ui::ACCENT;
}
double channel_min(unsigned channel) {
    return channel == 6 ? .5 : -1e8;
}
double channel_max(unsigned channel) {
    return channel == 6 ? 4 : 1e8;
}
bool request_curve(EditorAction action, const VisualizationPath& graph, unsigned index,
                   unsigned channel = 0, double value = 0) {
    CameraPose request{};
    request[0] = graph.project_revision();
    request[1] = channel;
    request[2] = value;
    request[3] = graph.graph_keys()[index].time;
    request[4] = std::uint32_t(graph.session_id());
    request[5] = std::uint32_t(graph.session_id() >> 32);
    return editor_enqueue(action, index, &request);
}
bool request_tangent(const VisualizationPath& graph, unsigned index, unsigned axis, unsigned mode,
                     double incoming, double outgoing) {
    CameraPose request{double(graph.project_revision()),
                       incoming,
                       outgoing,
                       graph.graph_keys()[index].time,
                       double(std::uint32_t(graph.session_id())),
                       double(std::uint32_t(graph.session_id() >> 32)),
                       0};
    return editor_enqueue(EditorAction::SetCameraPositionTangents, index * 12 + axis * 4 + mode,
                          &request);
}
void dashed_line(ImDrawList* draw, ImVec2 a, ImVec2 b, ImU32 color, float scale, float& phase) {
    const float dx = b.x - a.x, dy = b.y - a.y;
    const float length = std::sqrt(dx * dx + dy * dy);
    if (!(length > 0))
        return;
    const float period = 10 * scale, dash = 5 * scale;
    // A clipped segment can be very long when zoomed; bound the work to its
    // visible portion before calling this helper.
    for (float at = -phase; at < length; at += period) {
        const float begin = std::max(0.f, at), end = std::min(length, at + dash);
        if (end <= begin)
            continue;
        draw->AddLine(ImVec2(a.x + dx * begin / length, a.y + dy * begin / length),
                      ImVec2(a.x + dx * end / length, a.y + dy * end / length), color, scale);
    }
    phase = std::fmod(phase + length, period);
}
bool clip_segment(ImVec2& a, ImVec2& b, const ImVec2& low, const ImVec2& high) {
    const float dx = b.x - a.x, dy = b.y - a.y;
    float enter = 0, leave = 1;
    const float p[]{-dx, dx, -dy, dy}, q[]{a.x - low.x, high.x - a.x, a.y - low.y, high.y - a.y};
    for (int i = 0; i < 4; ++i) {
        if (p[i] == 0) {
            if (q[i] < 0)
                return false;
        } else if (p[i] < 0)
            enter = std::max(enter, q[i] / p[i]);
        else
            leave = std::min(leave, q[i] / p[i]);
        if (enter > leave)
            return false;
    }
    const auto original = a;
    a = ImVec2(original.x + enter * dx, original.y + enter * dy);
    b = ImVec2(original.x + leave * dx, original.y + leave * dy);
    return true;
}
void format_value(char* label, std::size_t size, double value, unsigned channel) {
    // Keep the ordinary value readable, without scientific notation at normal
    // world-coordinate magnitudes. Numeric editing retains the complete double.
    std::snprintf(label, size,
                  channel == 6              ? "%.3f"
                  : std::abs(value) < 10000 ? "%.1f"
                                            : "%.0f",
                  value);
}
void curve_tooltip(float scale, const char* title, const char* value, const char* help) {
    using namespace ImGui;
    // Plot children intentionally have no padding. Tooltips are separate
    // windows and must not inherit that zero-padding plotting style.
    PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(10 * scale, 8 * scale));
    PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(6 * scale, 4 * scale));
    const auto* viewport = GetMainViewport();
    const float width = std::max(1.f, std::min(310 * scale, viewport->WorkSize.x - 36 * scale));
    SetNextWindowSizeConstraints(ImVec2(0, 0),
                                 ImVec2(std::max(1.f, viewport->WorkSize.x - 16 * scale),
                                        std::max(1.f, viewport->WorkSize.y - 16 * scale)));
    if (BeginTooltip()) {
        PushTextWrapPos(GetCursorPosX() + width);
        TextUnformatted(title);
        if (value && *value)
            TextUnformatted(value);
        PushStyleColor(ImGuiCol_Text, GetStyleColorVec4(ImGuiCol_TextDisabled));
        for (const char* line = help; line && *line;) {
            const char* end = std::strchr(line, '\n');
            TextUnformatted(line, end);
            line = end ? end + 1 : nullptr;
        }
        PopStyleColor();
        PopTextWrapPos();
        EndTooltip();
    }
    PopStyleVar(2);
}
} // namespace

void OverlayPanel::clear_curve_preview() noexcept {
    if (!curve_preview_token_)
        return;
    editor_enqueue(EditorAction::ClearCameraCurvePreview, curve_preview_index_,
                   &curve_preview_anchor_);
    visualization_end_native_preview(curve_preview_token_);
    curve_preview_token_ = 0;
}
void OverlayPanel::start_curve_preview(const VisualizationPath& graph, unsigned index,
                                       unsigned channel) {
    clear_curve_preview();
    curve_preview_token_ = visualization_begin_native_preview(graph, index, channel);
    curve_preview_index_ = index;
    curve_preview_anchor_ = {double(graph.project_revision()),
                             0,
                             0,
                             graph.graph_keys()[index].time,
                             double(std::uint32_t(graph.session_id())),
                             double(std::uint32_t(graph.session_id() >> 32)),
                             double(curve_preview_token_)};
    curve_preview_sent_at_ = -1e30;
}

bool OverlayPanel::draw_timeline_modes() {
    using namespace ImGui;
    const float s = panel_scale;
    if (rail_drawing_) {
        namespace k = rail_kit;
        k::BeginRegion(GetWindowPos(), 1120);
        const int mode = curve_view_ ? 2 : lite_campath_timeline_ ? 1 : 0;
        bool changed = false;
        const char* names[]{"Replay", "Campaths", "Curves"};
        const float x[]{17, 85, 173}, w[]{64, 84, 67};
        for (int i = 0; i < 3; ++i) {
            k::At(x[i], 17);
            if (k::Tab(names[i], i == mode, w[i]) && i != mode) {
                curve_view_ = i == 2;
                if (i != 2)
                    lite_campath_timeline_ = i == 1;
                lite_replay_dragging_ = false;
                cancel_position_graph();
                ClearActiveID();
                changed = true;
            }
        }
        return changed;
    }
    const int current = curve_view_ ? 2 : lite_campath_timeline_ ? 1 : 0;
    bool changed = false;
    const char* labels[]{"Replay###lite-timeline-mode", "Campaths###lite-campath-mode",
                         "Curves###lite-curves-mode"};
    if (GetWindowSize().x < (curve_view_ ? 790.f : 650.f) * s) {
        // A narrow custom timeline keeps its one-row transport. The same three
        // modes remain one click away rather than pushing the player selector
        // outside the window.
        const char* names[]{"Replay", "Campaths", "Curves"};
        SetNextItemWidth(94 * s);
        if (BeginCombo("##timeline-view", names[current], ImGuiComboFlags_HeightSmall)) {
            for (int i = 0; i < 3; ++i)
                if (Selectable(names[i], i == current) && i != current) {
                    curve_view_ = i == 2;
                    if (i != 2)
                        lite_campath_timeline_ = i == 1;
                    lite_replay_dragging_ = false;
                    curve_resize_pending_ |= (current == 2) != (i == 2);
                    cancel_position_graph();
                    changed = true;
                    save_layout();
                }
            EndCombo();
        }
        return changed;
    }
    for (int i = 0; i < 3; ++i) {
        if (i)
            SameLine(0, 3 * s);
        if (i == current)
            PushStyleColor(ImGuiCol_Button, curve_color(ui::SELECTED));
        if (Button(labels[i], ImVec2((i == 1 ? 78.f : 64.f) * s, 0)) && current != i) {
            curve_view_ = i == 2;
            if (i != 2)
                lite_campath_timeline_ = i == 1;
            lite_replay_dragging_ = false;
            curve_resize_pending_ |= (current == 2) != (i == 2);
            cancel_position_graph();
            changed = true;
            save_layout();
        }
        if (i == current)
            PopStyleColor();
    }
    return changed;
}

void OverlayPanel::draw_curve_workspace(const EditorSnapshot& state, const EditorCameraList& list) {
    using namespace ImGui;
    const auto curve_color = [&](unsigned rgb, float alpha = 1.f) {
        return panel_color(rgb, alpha);
    };
    const float s = panel_scale;
    auto& io = GetIO();
    const auto graph = visualization_snapshot();
    const bool matching =
        graph && graph->enabled() && graph->session_id() &&
        graph->project_revision() == list.revision && graph->camera_count() == list.total &&
        graph->graph_keys().size() == list.total && list.total == state.camera_count &&
        graph->selected_camera() == state.selected_camera;
    const bool selected = matching && state.selected_camera < graph->graph_keys().size();
    const bool free_key = selected && !graph->graph_keys()[state.selected_camera].source;
    const bool locked = editor_recording_locks_cameras(editor_recording_status().state);
    const bool can_select = matching && state.enabled && state.focused && state.ready &&
                            state.paused && !state.busy && !state.playing &&
                            !state.attach_preview && !state.bone_picker && !state.object_picker &&
                            state.owner == EditorOwner::Panel && !locked;
    const bool editable = can_select && free_key;
    // Cancel before DragScalar can blur/commit a typed value. The same right
    // click must not turn into a horizontal pan after the edit is cleared.
    const bool right_cancelled = curve_editing() && IsMouseClicked(ImGuiMouseButton_Right);
    if (right_cancelled) {
        position_graph_draft_ = position_graph_original_;
        cancel_position_graph();
        ClearActiveID();
    }
    auto refit = [&] {
        curve_fit_pending_ = true;
        for (auto& scale : curve_scales_)
            scale.fitted = false;
    };
    if (curve_value_pending_ && graph) {
        if (curve_value_pending_session_ != graph->session_id() ||
            GetTime() > curve_value_pending_until_) {
            curve_value_pending_ = curve_frame_after_value_ = false;
        } else if (matching && graph->project_revision() != curve_value_pending_revision_) {
            if (curve_frame_after_value_)
                refit();
            curve_value_pending_ = curve_frame_after_value_ = false;
        }
    }
    if (graph && (!graph->session_id() || curve_session_ != graph->session_id())) {
        cancel_position_graph();
        curve_before_.reset();
        curve_show_before_ = false;
        curve_session_ = graph->session_id();
        curve_fit_pending_ = true;
        curve_angle_seeded_ = false;
        curve_duration_seen_ = -1;
        for (auto& scale : curve_scales_)
            scale.fitted = false;
    }
    if ((tangent_dragging_ || tangent_value_editing_) &&
        (!editable || !graph->smooth_position() || tangent_session_ != graph->session_id() ||
         tangent_revision_ != list.revision || tangent_index_ != state.selected_camera ||
         tangent_time_ != graph->graph_keys()[state.selected_camera].time ||
         IsKeyPressed(ImGuiKey_Escape))) {
        cancel_position_graph();
        ClearActiveID();
    }
    auto commit_tangent = [&] {
        if ((!tangent_dragging_ && !tangent_value_editing_) || !editable ||
            !graph->smooth_position() || tangent_session_ != graph->session_id() ||
            tangent_revision_ != list.revision || tangent_index_ != state.selected_camera ||
            tangent_time_ != graph->graph_keys()[state.selected_camera].time ||
            (tangent_draft_[0] == tangent_original_[0] &&
             tangent_draft_[1] == tangent_original_[1]))
            return;
        request_tangent(*graph, tangent_index_, tangent_axis_, tangent_mode_, tangent_draft_[0],
                        tangent_draft_[1]);
    };
    auto start_tangent = [&](unsigned axis, unsigned side, bool dragging) {
        const auto& key = graph->graph_keys()[state.selected_camera];
        const auto& slope = key.tangents[axis];
        tangent_dragging_ = dragging;
        tangent_value_editing_ = !dragging;
        tangent_side_ = side;
        tangent_axis_ = axis;
        tangent_mode_ = slope.mode == 2 ? 2 : 3;
        tangent_revision_ = list.revision;
        tangent_index_ = state.selected_camera;
        tangent_session_ = graph->session_id();
        tangent_time_ = key.time;
        tangent_original_[0] = tangent_draft_[0] = slope.incoming;
        tangent_original_[1] = tangent_draft_[1] = slope.outgoing;
        tangent_mouse_y_ = io.MousePos.y;
        start_curve_preview(*graph, state.selected_camera, axis);
    };
    const bool editing = position_graph_dragging_ || position_value_editing_;
    if (editing &&
        (!editable || curve_edit_session_ != graph->session_id() ||
         position_graph_revision_ != list.revision ||
         position_graph_index_ != state.selected_camera ||
         position_graph_time_ != graph->graph_keys()[state.selected_camera].time ||
         std::abs(graph->graph_keys()[state.selected_camera].authored_pose[position_graph_axis_]) >
             1e8 ||
         IsKeyPressed(ImGuiKey_Escape))) {
        cancel_position_graph();
        ClearActiveID();
    }
    auto commit_numeric = [&] {
        // Match ordinary field deactivation, including its original guards.
        // Fit all can be activated before this frame's numeric item is drawn.
        if (!position_value_editing_ || position_graph_escape_ || !editable ||
            curve_edit_session_ != graph->session_id() ||
            position_graph_revision_ != list.revision ||
            position_graph_index_ != state.selected_camera ||
            position_graph_time_ != graph->graph_keys()[state.selected_camera].time ||
            position_graph_draft_ == position_graph_original_)
            return;
        if (request_curve(EditorAction::SetCameraCurveValue, *graph, position_graph_index_,
                          position_graph_axis_, position_graph_draft_)) {
            curve_value_pending_ = true;
            curve_value_pending_revision_ = list.revision;
            curve_value_pending_session_ = graph->session_id();
            curve_value_pending_until_ = GetTime() + 2.;
        }
    };
    auto frame_all = [&] {
        commit_numeric();
        commit_tangent();
        cancel_position_graph();
        ClearActiveID();
        refit();
        // The mouse blur can have queued the edit on the preceding frame.
        // Fit once more when that published revision arrives, not old samples.
        curve_frame_after_value_ = curve_value_pending_;
    };
    if (matching && !curve_editing()) {
        const bool mode_changed =
            curve_angle_seeded_ && curve_angles_unwrapped_ != graph->unwrapped_rotation();
        for (unsigned i = 0; i < 2; ++i) {
            const double seed = graph->graph_keys().front().pose[4 + i];
            if (mode_changed || (curve_angle_seeded_ && !graph->unwrapped_rotation() &&
                                 std::round((seed - curve_angle_seed_[i]) / 360.) != 0))
                curve_scales_[4 + i].fitted = false;
            curve_angle_seed_[i] = seed;
        }
        curve_angles_unwrapped_ = graph->unwrapped_rotation();
        curve_angle_seeded_ = true;
    }
    draw_timeline_modes();
    curve_frame_all_rect_ = ImVec4();
    curve_graphs_rect_ = ImVec4();
    for (auto& rect : curve_axis_rects_)
        rect = ImVec4();
    for (auto& compact : curve_lane_compact_axes_)
        compact = false;
    const float item_gap = GetStyle().ItemSpacing.x;
    const float pad_x = GetStyle().FramePadding.x;
    char speed[20];
    std::snprintf(speed, sizeof(speed), "%gx", state.playback_speed);
    float speed_text_width = CalcTextSize(speed).x;
    for (const char* label : {"0.25x", "0.5x", "1x", "2x", "4x"})
        speed_text_width = std::max(speed_text_width, CalcTextSize(label).x);
    const float speed_width = std::ceil(speed_text_width + GetFrameHeight() + 2 * pad_x);
    char transport_time[64]{};
    std::snprintf(transport_time, sizeof(transport_time), "%.2f / %.2f s", lite_shot_time_,
                  state.duration);
    const float left_transport = 196 * s + CalcTextSize(transport_time).x + CalcTextSize("Undo").x +
                                 CalcTextSize("More").x + 4 * pad_x + 4 * item_gap;
    const float right_transport = speed_width + GetFrameHeight() + GetStyle().ItemInnerSpacing.x +
                                  CalcTextSize("Pause after capture").x + 74 * s + 2 * item_gap;
    const float speed_label = CalcTextSize("Speed").x + item_gap;
    const float available_transport = GetWindowContentRegionMax().x - GetWindowContentRegionMin().x;
    const bool show_speed_label =
        left_transport + right_transport + speed_label + item_gap <= available_transport;
    const bool narrow =
        left_transport + right_transport + (show_speed_label ? speed_label : 0) + item_gap >
        available_transport;
    const bool compact_header = GetWindowSize().x < 790 * s;
    auto join_header = [&](float width, float gap) {
        const float previous_right = GetCurrentWindow()->DC.CursorPosPrevLine.x - GetWindowPos().x;
        if (previous_right + gap + width <= GetWindowContentRegionMax().x)
            SameLine(0, gap);
    };
    auto axis_button = [&](unsigned axis) {
        const bool active = curve_axes_[axis < 3 ? 0 : axis < 6 ? 1 : 2] == int(axis);
        if (rail_drawing_) {
            namespace k = rail_kit;
            char label[64];
            std::snprintf(label, sizeof(label), "%s###curve-axis-%u", channels[axis], axis);
            k::BeginRegion(GetCursorScreenPos(), axis < 3 ? 34 : axis < 6 ? 60 : 94);
            if (k::Tab(label, active, axis < 3 ? 34 : axis < 6 ? 60 : 94) && !active) {
                cancel_position_graph();
                ClearActiveID();
                curve_axes_[axis < 3 ? 0 : axis < 6 ? 1 : 2] = int(axis);
            }
            const auto low = GetItemRectMin(), high = GetItemRectMax();
            curve_axis_rects_[axis] = ImVec4(low.x, low.y, high.x, high.y);
            return;
        }
        PushStyleColor(ImGuiCol_Button, active ? curve_color(ui::SELECTED) : ImVec4(0, 0, 0, 0));
        PushStyleColor(ImGuiCol_Border,
                       active ? curve_color(ui::SELECTED_EDGE) : ImVec4(0, 0, 0, 0));
        PushStyleColor(ImGuiCol_Text, curve_color(active ? ui::SELECTED_INK : ui::TEXT));
        PushStyleVar(ImGuiStyleVar_FrameBorderSize, active ? s : 0.f);
        char label[64];
        std::snprintf(label, sizeof(label), "%s###curve-axis-%u", channels[axis], axis);
        if (Button(label) && !active) {
            cancel_position_graph();
            ClearActiveID();
            curve_axes_[axis < 3 ? 0 : axis < 6 ? 1 : 2] = int(axis);
            save_layout();
        }
        const auto low = GetItemRectMin(), high = GetItemRectMax();
        curve_axis_rects_[axis] = ImVec4(low.x, low.y, high.x, high.y);
        PopStyleVar();
        PopStyleColor(3);
    };
    char camera_context[80]{};
    if (selected)
        std::snprintf(camera_context, sizeof(camera_context), "Camera %02u | %.2f s",
                      state.selected_camera + 1, graph->graph_keys()[state.selected_camera].time);
    else
        std::snprintf(camera_context, sizeof(camera_context), "Shot seconds");
    bool footer_camera_context = false;
    curve_group_controls_visible_ = !curve_compare_;
    if (rail_drawing_) {
        namespace k = rail_kit;
        namespace t = rail_theme;
        auto& d = *rail_design_;
        const auto o = GetWindowPos();
        auto* dl = GetWindowDrawList();
        dl->AddRectFilled(ImVec2(o.x + 252 * s, o.y + 22 * s), ImVec2(o.x + 253 * s, o.y + 42 * s),
                          t::U32(t::border_default));
        if (!curve_compare_) {
            const float x[]{265, 341, 419}, w[]{72, 74, 72};
            for (int i = 0; i < 3; ++i) {
                k::At(x[i], 17);
                if (k::Tab(groups[i], curve_group_ == i, w[i]) && curve_group_ != i) {
                    cancel_position_graph();
                    ClearActiveID();
                    curve_group_ = i;
                }
            }
        } else {
            k::At(265, 17);
            if (k::Button("Graphs", 72))
                OpenPopup("##curve-graphs");
            auto lo = GetItemRectMin(), hi = GetItemRectMax();
            curve_graphs_rect_ = ImVec4(lo.x, lo.y, hi.x, hi.y);
            SetNextWindowSize(ImVec2(180 * s, 133 * s));
            if (BeginPopup("##curve-graphs")) {
                k::BeginRegion(ImVec2(GetWindowPos().x + 12 * s, GetWindowPos().y + 12 * s), 156);
                k::Label("Visible graphs");
                k::VGap(6);
                k::Divider();
                k::VGap(10);
                for (unsigned i = 0; i < 3; ++i) {
                    bool on = (curve_lanes_ & (1u << i)) != 0;
                    if (k::Checkbox(groups[i], &on,
                                    on && (curve_lanes_ & (curve_lanes_ - 1)) == 0)) {
                        const auto proposed =
                            on ? curve_lanes_ | (1u << i) : curve_lanes_ & ~(1u << i);
                        if (proposed) {
                            curve_lanes_ = proposed;
                            cancel_position_graph();
                            ClearActiveID();
                        }
                    }
                    if (i < 2)
                        k::VGap(7);
                }
                k::EndRegion();
                EndPopup();
            }
            k::BeginRegion(o, 1120);
        }
        k::At(905, 23);
        if (k::Checkbox("Stack graphs", &curve_compare_)) {
            cancel_position_graph();
            ClearActiveID();
        }
        k::At(1009, 23);
        if (k::Checkbox("Show original", &curve_show_before_, !matching) && curve_show_before_ &&
            !curve_before_)
            curve_before_ = graph;
        const int first = curve_group_ == 0   ? 0
                          : curve_group_ == 1 ? 3
                                              : 6,
                  last = curve_group_ == 2 ? 6 : first + 2;
        const float axis_w = curve_group_ == 0 ? 34.f : curve_group_ == 1 ? 60.f : 94.f;
        if (!curve_compare_)
            for (int axis = first; axis <= last; ++axis) {
                k::At(17 + (axis - first) * (axis_w + 2), 57);
                if (k::Tab(channels[axis], curve_axes_[curve_group_] == axis, axis_w)) {
                    curve_axes_[curve_group_] = axis;
                    cancel_position_graph();
                    ClearActiveID();
                }
                auto lo = GetItemRectMin(), hi = GetItemRectMax();
                curve_axis_rects_[axis] = ImVec4(lo.x, lo.y, hi.x, hi.y);
            }
        const float context_x = curve_group_ == 1 && !curve_compare_ ? 213.f : 135.f;
        k::At(context_x, 64);
        k::Text(camera_context, t::text_muted);
        const float fit = context_x + 121;
        k::At(fit, 57);
        if (k::Button("Fit all###Fit", 31, k::Btn::Quiet, false, k::Align::Left))
            frame_all();
        auto lo = GetItemRectMin(), hi = GetItemRectMax();
        curve_frame_all_rect_ = ImVec4(lo.x, lo.y, hi.x, hi.y);
        k::At(fit + 43, 57);
        if (k::Button("Update original###Replace Before", 87, k::Btn::Quiet,
                      !matching || !curve_before_, k::Align::Left)) {
            curve_before_ = graph;
            curve_show_before_ = true;
        }
        SetCursorPos(ImVec2(930 * s, 57 * s));
    } else {
        curve_group_controls_visible_ = !curve_compare_;
        if (!curve_compare_ && compact_header) {
            SameLine();
            SetNextItemWidth(100 * s);
            if (BeginCombo("##curve-group-picker", groups[curve_group_],
                           ImGuiComboFlags_HeightSmall)) {
                for (int i = 0; i < 3; ++i)
                    if (Selectable(groups[i], curve_group_ == i) && curve_group_ != i) {
                        cancel_position_graph();
                        ClearActiveID();
                        curve_group_ = i;
                        save_layout();
                    }
                EndCombo();
            }
        } else if (!curve_compare_) {
            SameLine(0, 14 * s);
            for (int i = 0; i < 3; ++i) {
                if (i)
                    SameLine(0, 3 * s);
                char label[64];
                std::snprintf(label, sizeof(label), "%s###curve-group-%d", groups[i], i);
                const bool active = i == curve_group_;
                if (active)
                    PushStyleColor(ImGuiCol_Button, curve_color(ui::SELECTED));
                if (Button(label, ImVec2(76 * s, 0)) && curve_group_ != i) {
                    cancel_position_graph();
                    ClearActiveID();
                    curve_group_ = i;
                    save_layout();
                }
                if (active)
                    PopStyleColor();
            }
        }
        join_header(CalcTextSize("Stack graphs").x + GetFrameHeight() +
                        GetStyle().ItemInnerSpacing.x,
                    compact_header ? 6 * s : 14 * s);
        if (Checkbox("Stack graphs###Compare", &curve_compare_)) {
            cancel_position_graph();
            ClearActiveID();
            curve_resize_pending_ = true;
            save_layout();
        }
        if (IsItemHovered())
            SetTooltip("Show selected Position, Rotation and Framing graphs in aligned lanes.");
        if (curve_compare_) {
            const auto timeline_seed = GetCurrentWindow()->ID;
            join_header(CalcTextSize("Graphs").x + 2 * GetStyle().FramePadding.x, 8 * s);
            if (Button("Graphs"))
                OpenPopup("##curve-graphs");
            const auto low = GetItemRectMin(), high = GetItemRectMax();
            curve_graphs_rect_ = ImVec4(low.x, low.y, high.x, high.y);
            if (BeginPopup("##curve-graphs")) {
                TextDisabled("Visible graphs");
                Separator();
                PushOverrideID(timeline_seed);
                for (unsigned i = 0; i < 3; ++i) {
                    char label[64];
                    std::snprintf(label, sizeof(label), "%s###curve-lane-%u", groups[i], i);
                    bool active = (curve_lanes_ & (1u << i)) != 0;
                    BeginDisabled(active && (curve_lanes_ & (curve_lanes_ - 1)) == 0);
                    if (Checkbox(label, &active)) {
                        const unsigned proposed =
                            active ? curve_lanes_ | (1u << i) : curve_lanes_ & ~(1u << i);
                        if (proposed) {
                            curve_lanes_ = proposed;
                            curve_resize_pending_ = true;
                            cancel_position_graph();
                            ClearActiveID();
                            save_layout();
                        }
                    }
                    EndDisabled();
                }
                PopID();
                EndPopup();
            }
        }
        join_header(CalcTextSize("Show original").x + GetFrameHeight() +
                        GetStyle().ItemInnerSpacing.x,
                    8 * s);
        BeginDisabled(!matching);
        if (Checkbox("Show original###Show Before", &curve_show_before_) && curve_show_before_ &&
            !curve_before_)
            curve_before_ = graph;
        if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
            SetTooltip(
                "Show a read-only original snapshot. It stays fixed across edits and Undo until Update original.\nShortest-path yaw and roll share the current continuous angle branch; authored values stay unchanged.");
        EndDisabled();
        const float view_tools_width = CalcTextSize("Fit all").x +
                                       CalcTextSize("Update original").x +
                                       4 * GetStyle().FramePadding.x + GetStyle().ItemSpacing.x;
        const float header_right = GetCurrentWindow()->DC.CursorPosPrevLine.x - GetWindowPos().x;
        const bool inline_view_tools = curve_compare_ && !compact_header && !layout_custom_ &&
                                       !editor_custom_ && io.DisplaySize.y >= 650 * s &&
                                       header_right + GetStyle().ItemSpacing.x + view_tools_width <=
                                           GetWindowContentRegionMax().x;
        if (inline_view_tools) {
            SameLine();
            if (Button("Fit all###Fit"))
                frame_all();
            const auto low = GetItemRectMin(), high = GetItemRectMax();
            curve_frame_all_rect_ = ImVec4(low.x, low.y, high.x, high.y);
            if (IsItemHovered())
                SetTooltip("Show every camera and the full curves; resets zoom and pan.");
            SameLine();
            BeginDisabled(!matching || !curve_before_);
            if (Button("Update original###Replace Before")) {
                curve_before_ = graph;
                curve_show_before_ = true;
            }
            EndDisabled();
            if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
                SetTooltip("Use the current published curves as the read-only original snapshot.");
            const float right = GetCurrentWindow()->DC.CursorPosPrevLine.x - GetWindowPos().x;
            if (right + GetStyle().ItemSpacing.x + CalcTextSize(camera_context).x <=
                GetWindowContentRegionMax().x) {
                SameLine();
                AlignTextToFramePadding();
                TextDisabled("%s", camera_context);
            } else
                footer_camera_context = true;
        }
        if (compact_header) {
            join_header(CalcTextSize("View").x + 2 * GetStyle().FramePadding.x, 6 * s);
            if (Button("View"))
                OpenPopup("##curve-view-options");
            if (BeginPopup("##curve-view-options")) {
                if (Button("Fit all###Fit")) {
                    frame_all();
                    CloseCurrentPopup();
                }
                const auto fit_min = GetItemRectMin(), fit_max = GetItemRectMax();
                curve_frame_all_rect_ = ImVec4(fit_min.x, fit_min.y, fit_max.x, fit_max.y);
                if (IsItemHovered())
                    SetTooltip("Show every camera and the full curves; resets zoom and pan.");
                BeginDisabled(!matching || !curve_before_);
                if (Button("Update original###Replace Before")) {
                    curve_before_ = graph;
                    curve_show_before_ = true;
                    CloseCurrentPopup();
                }
                EndDisabled();
                EndPopup();
            }
        }
        Separator();

        if (!curve_compare_) {
            const int first = curve_group_ == 0 ? 0 : curve_group_ == 1 ? 3 : 6;
            const int last = curve_group_ == 2 ? 6 : first + 2;
            for (int axis = first; axis <= last; ++axis) {
                if (axis != first)
                    SameLine(0, 3 * s);
                axis_button(unsigned(axis));
            }
            SameLine();
        }
        if (!inline_view_tools)
            TextDisabled("%s", camera_context);
        if (!compact_header && !inline_view_tools) {
            SameLine();
            if (Button("Fit all###Fit"))
                frame_all();
            const auto fit_min = GetItemRectMin(), fit_max = GetItemRectMax();
            curve_frame_all_rect_ = ImVec4(fit_min.x, fit_min.y, fit_max.x, fit_max.y);
            if (IsItemHovered())
                SetTooltip("Show every camera and the full curves; resets zoom and pan.");
            SameLine();
            BeginDisabled(!matching || !curve_before_);
            if (Button("Update original###Replace Before")) {
                curve_before_ = graph;
                curve_show_before_ = true;
            }
            EndDisabled();
            if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
                SetTooltip("Use the current published curves as the read-only original snapshot.");
        }
    }

    curve_value_rect_ = ImVec4();
    if (!curve_compare_ && selected) {
        const int channel = curve_axes_[curve_group_];
        const char* units = channel < 3 ? "units" : channel < 6 ? "deg" : "ratio";
        // Keep precision editing on the same row when there is room. Smaller
        // windows get a separate row rather than overlapping the reference controls.
        const float value_width = CalcTextSize(channels[channel]).x + CalcTextSize(units).x +
                                  2 * GetStyle().ItemSpacing.x + 110 * s;
        const float previous_right = GetItemRectMax().x - GetWindowPos().x;
        if (!rail_drawing_ && previous_right + GetStyle().ItemSpacing.x + value_width <=
                                  GetWindowContentRegionMax().x)
            SameLine(GetWindowContentRegionMax().x - value_width);
        const auto& key = graph->graph_keys()[state.selected_camera];
        if (!position_graph_dragging_ && !position_value_editing_)
            position_graph_draft_ = key.authored_pose[channel];
        BeginDisabled(!editable || position_graph_dragging_ || right_cancelled ||
                      std::abs(key.authored_pose[channel]) > 1e8);
        TextDisabled("%s", channels[channel]);
        SameLine();
        SetNextItemWidth(110 * s);
        const double minimum = channel_min(channel), maximum = channel_max(channel);
        const auto value_id = GetID("##curve-value");
        const auto field_at = GetCursorScreenPos();
        const ImRect field(field_at, ImVec2(field_at.x + 110 * s, field_at.y + GetFrameHeight()));
        // TempInputScalar seeds its text from this format on the activation
        // frame, so choose full precision before either mouse or nav activation.
        const bool precise = GetActiveID() == value_id || TempInputIsActive(value_id) ||
                             GetCurrentContext()->NavActivateId == value_id ||
                             (IsMouseClicked(ImGuiMouseButton_Left) && IsWindowHovered() &&
                              field.Contains(io.MousePos));
        const char* value_format = precise                                   ? "%.17g"
                                   : std::abs(position_graph_draft_) < 10000 ? "%.3f"
                                                                             : "%.0f";
        DragScalar("##curve-value", ImGuiDataType_Double, &position_graph_draft_,
                   channel == 6 ? .002f : .25f, &minimum, &maximum, value_format,
                   ImGuiSliderFlags_AlwaysClamp | ImGuiSliderFlags_NoRoundToFormat);
        const auto field_low = GetItemRectMin(), field_high = GetItemRectMax();
        curve_value_rect_ = ImVec4(field_low.x, field_low.y, field_high.x, field_high.y);
        if (IsItemActivated()) {
            position_value_editing_ = true;
            position_graph_escape_ = false;
            position_graph_axis_ = channel;
            position_graph_revision_ = list.revision;
            position_graph_index_ = state.selected_camera;
            position_graph_time_ = key.time;
            position_graph_original_ = key.authored_pose[channel];
            curve_edit_session_ = graph->session_id();
            start_curve_preview(*graph, state.selected_camera, unsigned(channel));
        }
        if (IsItemDeactivated()) {
            if (IsItemDeactivatedAfterEdit())
                commit_numeric();
            position_value_editing_ = false;
            clear_curve_preview();
        }
        if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) {
            char title[64];
            std::snprintf(title, sizeof(title), "%s value", channels[channel]);
            curve_tooltip(
                s, title, nullptr,
                channel == 4 || channel == 5
                    ? "Drag to adjust; Ctrl+click to type.\nInput keeps the authored rotation angle.\nRight-click cancels an active adjustment."
                    : "Drag to adjust; Ctrl+click to type.\nRight-click cancels an active adjustment.");
        }
        SameLine();
        TextDisabled("%s", units);
        EndDisabled();
    }

    if (rail_drawing_)
        SetCursorPos(ImVec2(17 * s, 97 * s));
    tangent_mode_rect_ = ImVec4();
    for (auto& rect : tangent_value_rects_)
        rect = ImVec4();
    for (auto& point : tangent_points_)
        point = ImVec2();
    const bool position_lane = curve_compare_ ? (curve_lanes_ & 1u) != 0 : curve_group_ == 0;
    if (selected && position_lane && graph->smooth_position()) {
        const unsigned axis = unsigned(curve_axes_[0]);
        const auto& key = graph->graph_keys()[state.selected_camera];
        const auto& slope = key.tangents[axis];
        const char* modes[]{"Auto", "Flat", "Linked", "Broken"};
        const bool compact = GetContentRegionAvail().x < 440 * s;
        BeginDisabled(!editable || curve_editing() || right_cancelled);
        SetNextItemWidth((compact ? 78 : 92) * s);
        if (BeginCombo("##curve-tangent-mode", modes[slope.mode], ImGuiComboFlags_HeightSmall)) {
            for (unsigned mode = 0; mode < 4; ++mode)
                if (Selectable(modes[mode], slope.mode == mode) && mode != slope.mode) {
                    const double incoming = mode < 2                              ? 0
                                            : mode == 2 && !state.selected_camera ? slope.outgoing
                                                                                  : slope.incoming;
                    const double outgoing = mode < 2 ? 0 : mode == 2 ? incoming : slope.outgoing;
                    request_tangent(*graph, state.selected_camera, axis, mode, incoming, outgoing);
                }
            EndCombo();
        }
        const auto mode_low = GetItemRectMin(), mode_high = GetItemRectMax();
        tangent_mode_rect_ = ImVec4(mode_low.x, mode_low.y, mode_high.x, mode_high.y);
        EndDisabled();
        if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
            SetTooltip(
                "XYZ arrival/departure slopes in Smooth mode.\nAuto calculates; Flat stops; Linked shares a slope; Broken edits each side.");
        for (unsigned side = 0; side < 2; ++side) {
            SameLine();
            TextDisabled("%s", compact ? side ? "Out" : "In" : side ? "Departure" : "Arrival");
            SameLine();
            SetNextItemWidth((compact ? 64 : 86) * s);
            const bool active =
                tangent_value_editing_ && tangent_axis_ == axis && tangent_side_ == side;
            double value = active ? tangent_draft_[side] : side ? slope.outgoing : slope.incoming;
            BeginDisabled(!editable || tangent_dragging_ || position_graph_dragging_ ||
                          position_value_editing_ || (tangent_value_editing_ && !active) ||
                          right_cancelled || slope.mode < 2 || (!side && !state.selected_camera) ||
                          (side && state.selected_camera + 1 == graph->graph_keys().size()));
            PushID(int(side));
            const auto value_id = GetID("##curve-tangent-value");
            const auto field_at = GetCursorScreenPos();
            const ImRect field(field_at, ImVec2(field_at.x + (compact ? 64 : 86) * s,
                                                field_at.y + GetFrameHeight()));
            const bool precise = active || GetActiveID() == value_id ||
                                 TempInputIsActive(value_id) ||
                                 GetCurrentContext()->NavActivateId == value_id ||
                                 (IsMouseClicked(ImGuiMouseButton_Left) && IsWindowHovered() &&
                                  field.Contains(io.MousePos));
            DragScalar("##curve-tangent-value", ImGuiDataType_Double, &value, .25f, nullptr,
                       nullptr, precise ? "%.17g" : "%.3f", ImGuiSliderFlags_NoRoundToFormat);
            const auto low = GetItemRectMin(), high = GetItemRectMax();
            tangent_value_rects_[side] = ImVec4(low.x, low.y, high.x, high.y);
            if (IsItemActivated())
                start_tangent(axis, side, false);
            if (tangent_value_editing_ && tangent_axis_ == axis && tangent_side_ == side &&
                std::isfinite(value)) {
                tangent_draft_[side] = value;
                if (tangent_mode_ == 2)
                    tangent_draft_[1 - side] = value;
            }
            if (IsItemDeactivated()) {
                if (IsItemDeactivatedAfterEdit())
                    commit_tangent();
                tangent_value_editing_ = false;
                clear_curve_preview();
            }
            if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
                curve_tooltip(
                    s, side ? "Departure slope" : "Arrival slope", nullptr,
                    "World units per shot second.\nDrag to adjust; Ctrl+click to type.\nRight-click cancels an active adjustment.");
            PopID();
            EndDisabled();
        }
        if (!compact) {
            SameLine();
            TextDisabled("units/s");
        }
    }

    if (rail_drawing_)
        SetCursorPos(ImVec2(
            17 * s, (97 + (selected && position_lane && graph->smooth_position() ? 40 : 0)) * s));
    unsigned lanes[3]{}, count = 0;
    if (curve_compare_) {
        for (unsigned i = 0; i < 3; ++i)
            if (curve_lanes_ & (1u << i))
                lanes[count++] = i;
    } else
        lanes[count++] = unsigned(curve_group_);
    curve_lane_count_ = count;
    for (auto& rect : curve_plot_rects_)
        rect = ImVec4();
    for (auto& point : curve_selected_points_)
        point = ImVec2();
    curve_draft_point_ = ImVec2();
    curve_draft_outside_ = false;
    curve_snapped_neighbor_ = -1;
    for (auto& point : curve_before_points_)
        point = ImVec2();
    for (auto& offset : curve_before_offsets_)
        offset = 0;
    if (matching && curve_before_ && !graph->unwrapped_rotation() &&
        !curve_before_->unwrapped_rotation()) {
        // A periodic coordinate alignment, never an edit of the reference.
        // Unwrapped/full-turn authoring deliberately retains its distinct turns.
        for (unsigned channel = 4; channel <= 5; ++channel)
            curve_before_offsets_[channel] =
                360. * std::round((graph->graph_keys().front().pose[channel] -
                                   curve_before_->graph_keys().front().pose[channel]) /
                                  360.);
    }
    const unsigned footer_rows = narrow ? 2u : 1u;
    const float row_gap = GetStyle().ItemSpacing.y;
    const float transport_height = footer_rows * GetFrameHeight() + (footer_rows - 1) * row_gap;
    // EndChild and the hint each add a layout gap; the one-pixel separator
    // contributes its gap, but zero height, in this pinned ImGui version.
    const float footer_height =
        row_gap + GetTextLineHeightWithSpacing() + row_gap + transport_height + 2 * s;
    const float plot_space =
        rail_drawing_ ? 150 * s : std::max(60 * s, GetContentRegionAvail().y - footer_height);
    // The graph area alone can scroll at small physical sizes or high DPI;
    // never trade away transport controls or draw a ruler over their hit boxes.
    PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0, 0));
    BeginChild("##curve-plots", ImVec2(0, plot_space), ImGuiChildFlags_None,
               ImGuiWindowFlags_NoBackground | ImGuiWindowFlags_NoScrollWithMouse);
    if (!matching) {
        const auto at = GetCursorScreenPos();
        GetWindowDrawList()->AddRectFilled(
            at, ImVec2(at.x + GetContentRegionAvail().x, at.y + plot_space),
            GetColorU32(curve_color(ui::FIELD)), 3 * s);
        SetCursorScreenPos(ImVec2(at.x + 18 * s, at.y + 18 * s));
        TextDisabled(state.camera_count ? "Updating shot curves..."
                                        : "Capture a camera to start a curve.");
        SetCursorScreenPos(ImVec2(at.x, at.y + plot_space));
    } else {
        const double shot_start = graph->graph_keys().front().time;
        const double shot_end = graph->graph_keys().back().time;
        // An empty time span needs a fallback; a real subsecond shot does not.
        const double shot_span = shot_end > shot_start ? shot_end - shot_start : 1.;
        const bool keys_changed = curve_key_count_seen_ != graph->graph_keys().size() ||
                                  curve_start_seen_ != shot_start ||
                                  curve_duration_seen_ != shot_end;
        if (curve_fit_pending_ || keys_changed) {
            // Capture/removal or changed boundaries starts with the complete
            // path. Ordinary value revisions keep deliberate horizontal zoom.
            curve_panning_ = false;
            curve_view_start_ = shot_start;
            curve_view_end_ = shot_start + shot_span;
            curve_fit_pending_ = false;
            curve_duration_seen_ = shot_end;
            curve_start_seen_ = shot_start;
            curve_key_count_seen_ = graph->graph_keys().size();
            if (keys_changed)
                for (auto& scale : curve_scales_)
                    scale.fitted = false;
        }
        if (!curve_editing() && lite_shot_playhead_ != state.playhead) {
            lite_shot_time_ =
                float(std::clamp(state.playhead, shot_start, std::max(shot_start, shot_end)));
            lite_shot_playhead_ = state.playhead;
        }
        // Input updates the shared range for the next frame. All lanes in
        // this frame retain exactly the same mapping, even while zooming.
        const double frame_start = curve_view_start_, frame_end = curve_view_end_;
        const auto live_preview = visualization_preview_snapshot();
        const auto draw_graph =
            live_preview && live_preview->source == 2 && live_preview->token == curve_preview_token_
                ? live_preview->path
                : graph;
        auto fit_scale = [&](unsigned channel) {
            auto& scale = curve_scales_[channel];
            if (scale.fitted && scale.revision == graph->project_revision())
                return;
            double low = graph->graph_keys().front().pose[channel], high = low;
            const auto add = [&](const VisualizationPath& path, double offset) {
                for (const auto& pose : path.points()) {
                    low = std::min(low, pose[channel] + offset);
                    high = std::max(high, pose[channel] + offset);
                }
                for (const auto& key : path.graph_keys()) {
                    low = std::min(low, key.pose[channel] + offset);
                    high = std::max(high, key.pose[channel] + offset);
                }
            };
            add(*graph, 0);
            if (curve_show_before_ && curve_before_)
                add(*curve_before_, curve_before_offsets_[channel]);
            const double padding = std::max(channel == 6  ? .08
                                            : channel < 3 ? 10.
                                                          : 3.,
                                            (high - low) * .15);
            if (!scale.fitted) {
                scale.low = low - padding;
                scale.high = high + padding;
            } else {
                // Keep familiar bounds for in-range edits. Accepted drags,
                // numeric edits and Undo expand only the side that needs room.
                if (low < scale.low)
                    scale.low = low - padding;
                if (high > scale.high)
                    scale.high = high + padding;
            }
            scale.fitted = true;
            scale.revision = graph->project_revision();
        };
        const float ruler_extra = count == 3 && !rail_drawing_ ? 17 * s : 0;
        const float shared_lane_height = (plot_space - ruler_extra) / count;
        for (unsigned lane = 0; lane < count; ++lane) {
            const unsigned group = lanes[lane], channel = unsigned(curve_axes_[group]);
            fit_scale(channel);
            const bool last = lane + 1 == count;
            const float lane_start = GetCursorScreenPos().y;
            if (rail_drawing_ && curve_compare_)
                GetWindowDrawList()->AddRectFilled(
                    GetCursorScreenPos(),
                    ImVec2(GetCursorScreenPos().x + GetContentRegionAvail().x,
                           lane_start + shared_lane_height - 2 * s),
                    GetColorU32(curve_color(ui::FIELD)), 4 * s);
            if (curve_compare_) {
                const float title_width =
                    std::max({CalcTextSize(groups[0]).x, CalcTextSize(groups[1]).x,
                              CalcTextSize(groups[2]).x}) +
                    12 * s;
                const float left = GetCursorPosX();
                AlignTextToFramePadding();
                TextDisabled("%s", groups[group]);
                SameLine(left + title_width);
                char value[80]{};
                if (selected) {
                    char number[64];
                    format_value(number, sizeof(number),
                                 graph->graph_keys()[state.selected_camera].authored_pose[channel],
                                 channel);
                    std::snprintf(value, sizeof(value), "%s%s", number,
                                  channel < 3   ? " units"
                                  : channel < 6 ? " deg"
                                                : " ratio");
                }
                if (group < 2) {
                    const unsigned first = group == 0 ? 0 : 3;
                    float buttons_width = 6 * s;
                    for (unsigned axis = first; axis < first + 3; ++axis)
                        buttons_width +=
                            CalcTextSize(channels[axis]).x + 2 * GetStyle().FramePadding.x;
                    const float value_width = std::max(100 * s, CalcTextSize(value).x);
                    const bool compact_axes =
                        GetWindowSize().x < 530 * s ||
                        title_width + buttons_width + 2 * GetStyle().ItemSpacing.x + value_width >
                            GetWindowContentRegionMax().x - left;
                    curve_lane_compact_axes_[group] = compact_axes;
                    if (compact_axes) {
                        PushID(int(group));
                        SetNextItemWidth(90 * s);
                        if (BeginCombo("##lane-axis", channels[channel],
                                       ImGuiComboFlags_HeightSmall)) {
                            for (unsigned axis = first; axis < first + 3; ++axis)
                                if (Selectable(channels[axis], axis == channel)) {
                                    curve_axes_[group] = int(axis);
                                    cancel_position_graph();
                                    ClearActiveID();
                                    save_layout();
                                }
                            EndCombo();
                        }
                        PopID();
                    } else {
                        const auto* parent = GetCurrentWindow()->ParentWindow;
                        PushOverrideID(parent->ID);
                        for (unsigned axis = first; axis < first + 3; ++axis) {
                            if (axis != first)
                                SameLine(0, 3 * s);
                            axis_button(axis);
                        }
                        PopID();
                    }
                } else
                    TextUnformatted("Aspect ratio");
                if (selected) {
                    SameLine(GetWindowContentRegionMax().x - CalcTextSize(value).x);
                    AlignTextToFramePadding();
                    TextUnformatted(value);
                }
            }
            if (rail_drawing_ && curve_compare_)
                SetCursorScreenPos(ImVec2(GetCursorScreenPos().x, lane_start + 30 * s));
            const auto origin = GetCursorScreenPos();
            const float title_height = origin.y - lane_start;
            // Three lanes reserve the shared ruler once, instead of taking
            // its17px from the final plot. Keep every overview plot meaningful.
            const float minimum_extent =
                (rail_drawing_ ? 14.f : 42.f) * s + (count == 3 && last ? ruler_extra : 0);
            const float minimum_lane = curve_compare_
                                           ? title_height + minimum_extent +
                                                 (rail_drawing_ ? 2 * s : GetStyle().ItemSpacing.y)
                                           : 60 * s;
            const float lane_height =
                std::max(shared_lane_height + (last ? ruler_extra : 0), minimum_lane);
            const ImVec2 extent(
                std::max(1.f, GetContentRegionAvail().x),
                std::max(minimum_extent, lane_height - title_height - GetStyle().ItemSpacing.y));
            const ImVec2 a(origin.x + 62 * s, origin.y + 7 * s);
            const ImVec2 b(origin.x + extent.x - 12 * s, origin.y + extent.y -
                                                             (rail_drawing_ && curve_compare_ ? 4.f
                                                              : last                          ? 24.f
                                                                     : 7.f) *
                                                                 s);
            const float time_left = a.x + 6 * s, time_right = b.x - 6 * s;
            curve_plot_rects_[channel] = ImVec4(a.x, a.y, b.x, b.y);
            const auto& scale = curve_scales_[channel];
            const double view_span = std::max(1e-9, frame_end - frame_start);
            auto screen = [&](double time, double value) {
                return ImVec2(
                    time_left + float((time - frame_start) / view_span) * (time_right - time_left),
                    b.y - float((value - scale.low) / (scale.high - scale.low)) * (b.y - a.y));
            };
            PushID(int(channel));
            InvisibleButton("##curve-plot", extent,
                            ImGuiButtonFlags_MouseButtonLeft | ImGuiButtonFlags_MouseButtonRight);
            const bool hovered = IsItemHovered();
            auto* draw = GetWindowDrawList();
            draw->AddRectFilled(origin, ImVec2(origin.x + extent.x, origin.y + extent.y),
                                GetColorU32(curve_color(ui::FIELD)), 3 * s);
            for (int i = 0; i < 3; ++i) {
                const double value = scale.high - (scale.high - scale.low) * i * .5;
                const float y = a.y + (b.y - a.y) * i * .5f;
                draw->AddLine(ImVec2(a.x, y), ImVec2(b.x, y),
                              GetColorU32(curve_color(ui::EDGE, .65f)));
                char label[48];
                format_value(label, sizeof(label), value, channel);
                if (!(rail_drawing_ && curve_compare_))
                    draw->AddText(
                        ImVec2(a.x - 8 * s - CalcTextSize(label).x, y - GetFontSize() * .5f),
                        GetColorU32(ImGuiCol_TextDisabled), label);
            }
            const unsigned divisions = b.x - a.x > 600 * s ? 5 : 3;
            for (unsigned i = 0; i <= divisions; ++i) {
                const float x = time_left + (time_right - time_left) * float(i) / divisions;
                draw->AddLine(ImVec2(x, a.y), ImVec2(x, b.y),
                              GetColorU32(curve_color(ui::EDGE, .5f)));
                if (last && !(rail_drawing_ && curve_compare_)) {
                    char label[48];
                    std::snprintf(label, sizeof(label), "%.2f s",
                                  frame_start + view_span * i / divisions);
                    const float label_width = CalcTextSize(label).x;
                    draw->AddText(ImVec2(std::clamp(x - label_width * .5f, a.x, b.x - label_width),
                                         b.y + 6 * s),
                                  GetColorU32(ImGuiCol_TextDisabled), label);
                }
            }
            draw->PushClipRect(a, b, true);
            auto line = [&](const VisualizationPath& path, bool before) {
                const auto& times = path.point_times();
                const double offset = before ? curve_before_offsets_[channel] : 0;
                float phase = 0;
                for (std::size_t i = 1; i < times.size(); ++i) {
                    if (times[i] < frame_start || times[i - 1] > frame_end)
                        continue;
                    auto p = screen(times[i - 1], path.points()[i - 1][channel] + offset);
                    // Step channels hold to the arrival time, then jump. Do
                    // not leave a hole or draw a false diagonal at that edge.
                    const double end_value = path.channel_breaks(i, channel)
                                                 ? path.points()[i - 1][channel]
                                                 : path.points()[i][channel];
                    auto q = screen(times[i], end_value + offset);
                    if (!clip_segment(p, q, a, b))
                        continue;
                    if (before)
                        dashed_line(draw, p, q, GetColorU32(curve_color(ui::CURVE_BEFORE, .38f)), s,
                                    phase);
                    else
                        draw->AddLine(p, q, GetColorU32(curve_color(channel_color(channel))),
                                      1.7f * s);
                }
            };
            if (curve_show_before_ && curve_before_)
                line(*curve_before_, true);
            if (curve_show_before_ && curve_before_ &&
                state.selected_camera < curve_before_->graph_keys().size()) {
                const auto& old = curve_before_->graph_keys()[state.selected_camera];
                curve_before_points_[channel] =
                    screen(old.time, old.pose[channel] + curve_before_offsets_[channel]);
            }
            line(*draw_graph, false);
            const float playhead_x = screen(lite_shot_time_, scale.low).x;
            draw->AddLine(ImVec2(playhead_x, a.y), ImVec2(playhead_x, b.y),
                          GetColorU32(curve_color(ui::ACCENT, .45f)), s);
            unsigned hit = ~0u;
            float nearest = 9 * s;
            for (unsigned index = 0; index < graph->graph_keys().size(); ++index) {
                const auto& key = graph->graph_keys()[index];
                if (key.time < frame_start || key.time > frame_end)
                    continue;
                const auto p = screen(key.time, key.pose[channel]);
                const bool active = index == state.selected_camera;
                if (active) {
                    curve_selected_points_[channel] = p;
                    position_graph_selected_point_ = p;
                }
                if (p.y < a.y || p.y > b.y)
                    continue;
                const float r = (active ? 4.f : 3.f) * s;
                const ImVec2 diamond[]{ImVec2(p.x, p.y - r), ImVec2(p.x + r, p.y),
                                       ImVec2(p.x, p.y + r), ImVec2(p.x - r, p.y)};
                draw->AddConvexPolyFilled(diamond, 4,
                                          active ? GetColorU32(curve_color(ui::TEXT))
                                                 : GetColorU32(curve_color(ui::FIELD)));
                draw->AddPolyline(diamond, 4, GetColorU32(curve_color(channel_color(channel))),
                                  ImDrawFlags_Closed, s);
                const float distance = std::sqrt((io.MousePos.x - p.x) * (io.MousePos.x - p.x) +
                                                 (io.MousePos.y - p.y) * (io.MousePos.y - p.y));
                if (hovered && distance < nearest) {
                    nearest = distance;
                    hit = index;
                }
            }
            int handle_hit = -1;
            double handle_times[2]{};
            const bool handles = selected && free_key && channel < 3 && graph->smooth_position();
            if (handles) {
                const auto& key = graph->graph_keys()[state.selected_camera];
                const auto point = screen(key.time, key.pose[channel]);
                const auto& slopes = key.tangents[channel];
                const double pixels_per_second = (time_right - time_left) / view_span;
                const double pixels_per_unit = (b.y - a.y) / (scale.high - scale.low);
                const bool active = tangent_dragging_ && tangent_axis_ == channel;
                if (active) {
                    const double value =
                        tangent_original_[tangent_side_] +
                        (tangent_mouse_y_ - io.MousePos.y) * tangent_units_per_pixel_ / tangent_dt_;
                    if (std::isfinite(value)) {
                        tangent_draft_[tangent_side_] = value;
                        if (tangent_mode_ == 2)
                            tangent_draft_[1 - tangent_side_] = value;
                    }
                }
                for (unsigned side = 0; side < 2; ++side) {
                    if ((!side && !state.selected_camera) ||
                        (side && state.selected_camera + 1 >= graph->graph_keys().size()))
                        continue;
                    if (point.x < a.x || point.x > b.x || point.y < a.y + 7 * s ||
                        point.y > b.y - 7 * s)
                        continue;
                    const double slope = active ? tangent_draft_[side]
                                         : side ? slopes.outgoing
                                                : slopes.incoming;
                    const double direction = side ? 1. : -1.;
                    const auto& neighbor = graph->graph_keys()[side ? state.selected_camera + 1
                                                                    : state.selected_camera - 1];
                    const double raw_x =
                        std::abs(neighbor.time - key.time) * pixels_per_second / 3.;
                    const double ratio = slope * (pixels_per_unit / pixels_per_second);
                    const double norm = std::hypot(1., ratio);
                    if (!std::isfinite(norm))
                        continue;
                    const double length = std::clamp(raw_x * norm, 28. * s, 56. * s);
                    double dx = direction * length / norm,
                           dy = -direction * length * (ratio / norm);
                    double fraction = 1;
                    if (dx > 0)
                        fraction = std::min(fraction, (b.x - 7 * s - point.x) / dx);
                    else if (dx < 0)
                        fraction = std::min(fraction, (a.x + 7 * s - point.x) / dx);
                    if (dy > 0)
                        fraction = std::min(fraction, (b.y - 7 * s - point.y) / dy);
                    else if (dy < 0)
                        fraction = std::min(fraction, (a.y + 7 * s - point.y) / dy);
                    if (fraction <= 0)
                        continue;
                    dx *= fraction;
                    dy *= fraction;
                    double dt = dx / pixels_per_second;
                    if (active && side == tangent_side_) {
                        dt = tangent_dt_;
                        dx = dt * pixels_per_second;
                        dy = -slope * dt * pixels_per_unit;
                    }
                    if (!std::isfinite(dx) || !std::isfinite(dy) || !std::isfinite(dt) || dt == 0 ||
                        (!active && std::hypot(dx, dy) < 18 * s))
                        continue;
                    ImVec2 tip(point.x + float(dx), point.y + float(dy));
                    bool crowded = false;
                    if (!active)
                        for (unsigned index = 0; index < graph->graph_keys().size(); ++index) {
                            if (index == state.selected_camera)
                                continue;
                            const auto other = screen(graph->graph_keys()[index].time,
                                                      graph->graph_keys()[index].pose[channel]);
                            if (std::hypot(tip.x - other.x, tip.y - other.y) < 21 * s) {
                                crowded = true;
                                break;
                            }
                        }
                    if (crowded)
                        continue;
                    tip.y = std::clamp(tip.y, a.y + 4 * s, b.y - 4 * s);
                    tangent_points_[side] = tip;
                    handle_times[side] = dt;
                    const auto arm = GetColorU32(curve_color(channel_color(channel), .55f));
                    draw->AddLine(point, tip, arm, s);
                    draw->AddCircleFilled(tip, 3 * s,
                                          GetColorU32(curve_color(channel_color(channel))), 12);
                    if (hovered && std::hypot(io.MousePos.x - tip.x, io.MousePos.y - tip.y) < 9 * s)
                        handle_hit = int(side);
                }
                if (handle_hit >= 0 && !curve_editing()) {
                    hit = ~0u;
                    curve_tooltip(
                        s, handle_hit ? "Departure handle" : "Arrival handle", nullptr,
                        "Drag vertically to change the slope.\nRight-click cancels the adjustment.");
                    if (IsMouseClicked(ImGuiMouseButton_Left) && editable && !right_cancelled) {
                        start_tangent(channel, unsigned(handle_hit), true);
                        tangent_dt_ = handle_times[handle_hit];
                        tangent_units_per_pixel_ =
                            (scale.high - scale.low) / std::max(1.f, b.y - a.y);
                    }
                }
                if (tangent_dragging_ && tangent_axis_ == channel &&
                    IsMouseReleased(ImGuiMouseButton_Left)) {
                    commit_tangent();
                    tangent_dragging_ = false;
                    clear_curve_preview();
                }
            }
            if (hit != ~0u && !curve_editing()) {
                const auto& key = graph->graph_keys()[hit];
                char title[64], value[96];
                std::snprintf(title, sizeof(title), "Camera %02u | %.3f s", hit + 1, key.time);
                std::snprintf(value, sizeof(value), "%s: %.12g %s", channels[channel],
                              key.authored_pose[channel],
                              channel < 3   ? "units"
                              : channel < 6 ? "deg"
                                            : "ratio");
                curve_tooltip(
                    s, title, value,
                    key.source ? "Attached camera: edit attachment offsets."
                    : hit == state.selected_camera
                        ? "Drag vertically to adjust.\nAlt: align with a nearby camera value.\nRight-click: cancel the adjustment."
                        : "Click to select this camera.\nSelection keeps the replay in place.");
                if (IsMouseClicked(ImGuiMouseButton_Left) && can_select && !right_cancelled) {
                    if (hit != state.selected_camera) {
                        request_curve(EditorAction::SelectCurveCamera, *graph, hit);
                        lite_shot_time_ = float(key.time);
                    } else if (editable && !curve_editing() &&
                               std::abs(key.authored_pose[channel]) <= 1e8) {
                        position_graph_dragging_ = true;
                        position_graph_escape_ = false;
                        position_graph_axis_ = int(channel);
                        position_graph_revision_ = list.revision;
                        position_graph_index_ = hit;
                        position_graph_time_ = key.time;
                        position_graph_original_ = position_graph_draft_ =
                            key.authored_pose[channel];
                        position_graph_mouse_y_ = io.MousePos.y;
                        position_graph_units_per_pixel_ =
                            float((scale.high - scale.low) / std::max(1.f, b.y - a.y));
                        curve_edit_session_ = graph->session_id();
                        start_curve_preview(*graph, hit, channel);
                    }
                }
            }
            if (position_graph_dragging_ && position_graph_axis_ == int(channel)) {
                position_graph_draft_ = std::clamp(
                    position_graph_original_ + (double(position_graph_mouse_y_) - io.MousePos.y) *
                                                   position_graph_units_per_pixel_,
                    channel_min(channel), channel_max(channel));
                const auto& key = graph->graph_keys()[position_graph_index_];
                double displayed =
                    key.pose[channel] + position_graph_draft_ - position_graph_original_;
                if (io.KeyAlt) {
                    double nearest_value = 8.0 * s * position_graph_units_per_pixel_;
                    const auto consider = [&](unsigned index) {
                        const auto& neighbor = graph->graph_keys()[index];
                        double authored = neighbor.authored_pose[channel];
                        if (!std::isfinite(authored) || std::abs(authored) > 1e8)
                            return;
                        if ((channel == 4 || channel == 5) && !graph->unwrapped_rotation()) {
                            const double turns =
                                std::round((position_graph_original_ - key.pose[channel] -
                                            (authored - neighbor.pose[channel])) /
                                           360.);
                            authored += 360. * turns;
                        }
                        if (!std::isfinite(authored) || authored < channel_min(channel) ||
                            authored > channel_max(channel))
                            return;
                        const double distance = std::abs(neighbor.pose[channel] - displayed);
                        if (distance < nearest_value ||
                            (distance == nearest_value && curve_snapped_neighbor_ < 0)) {
                            nearest_value = distance;
                            curve_snapped_neighbor_ = int(index);
                            position_graph_draft_ = authored;
                        }
                    };
                    // Earlier camera wins an exact tie. Sources are read only;
                    // attached neighbors are valid alignment references too.
                    if (position_graph_index_)
                        consider(position_graph_index_ - 1);
                    if (position_graph_index_ + 1 < graph->graph_keys().size())
                        consider(position_graph_index_ + 1);
                }
                displayed = key.pose[channel] + position_graph_draft_ - position_graph_original_;
                const auto draft = screen(position_graph_time_, displayed);
                curve_draft_outside_ = draft.y < a.y || draft.y > b.y;
                curve_draft_point_ = ImVec2(draft.x, std::clamp(draft.y, a.y + 8 * s, b.y - 8 * s));
                const auto color = GetColorU32(curve_color(channel_color(channel)));
                if (curve_snapped_neighbor_ >= 0 && draft.y >= a.y && draft.y <= b.y)
                    draw->AddLine(ImVec2(a.x, draft.y), ImVec2(b.x, draft.y),
                                  GetColorU32(curve_color(channel_color(channel), .45f)), s);
                draw->AddCircle(curve_draft_point_, 6 * s, color, 16, 2 * s);
                if (curve_draft_outside_) {
                    const float direction = draft.y < a.y ? -1.f : 1.f;
                    const float edge = direction < 0 ? a.y : b.y;
                    draw->AddTriangleFilled(ImVec2(draft.x, edge - direction * s),
                                            ImVec2(draft.x - 4 * s, edge - direction * 7 * s),
                                            ImVec2(draft.x + 4 * s, edge - direction * 7 * s),
                                            color);
                }
                if (IsMouseReleased(ImGuiMouseButton_Left)) {
                    if (position_graph_draft_ != position_graph_original_)
                        request_curve(EditorAction::SetCameraCurveValue, *graph,
                                      position_graph_index_, channel, position_graph_draft_);
                    position_graph_dragging_ = false;
                    clear_curve_preview();
                }
            }
            draw->PopClipRect();
            if (hovered && !curve_editing() && !right_cancelled) {
                if (io.MouseWheel != 0) {
                    const double fraction = std::clamp(
                        double((io.MousePos.x - time_left) / std::max(1.f, time_right - time_left)),
                        0., 1.);
                    const double anchor = frame_start + view_span * fraction;
                    const double next = std::clamp(view_span * std::pow(.8, io.MouseWheel),
                                                   std::min(.01, shot_span), shot_span);
                    curve_view_start_ = std::clamp(anchor - next * fraction, shot_start,
                                                   std::max(shot_start, shot_end - next));
                    curve_view_end_ = curve_view_start_ + next;
                }
                if (IsMouseClicked(ImGuiMouseButton_Right)) {
                    curve_panning_ = true;
                    curve_pan_mouse_ = io.MousePos.x;
                    curve_pan_start_ = curve_view_start_;
                    curve_pan_end_ = curve_view_end_;
                }
            }
            if (curve_panning_ && IsMouseDown(ImGuiMouseButton_Right)) {
                const double width = curve_pan_end_ - curve_pan_start_;
                const double delta = (curve_pan_mouse_ - io.MousePos.x) * width /
                                     std::max(1.f, time_right - time_left);
                curve_view_start_ = std::clamp(curve_pan_start_ + delta, shot_start,
                                               std::max(shot_start, shot_end - width));
                curve_view_end_ = curve_view_start_ + width;
            }
            if (!IsMouseDown(ImGuiMouseButton_Right))
                curve_panning_ = false;
            PopID();
        }
    }
    EndChild();
    PopStyleVar();
    if (editable && curve_editing() && curve_preview_token_ &&
        GetTime() - curve_preview_sent_at_ >= .1) {
        CameraPose request = curve_preview_anchor_;
        const bool tangent = tangent_dragging_ || tangent_value_editing_;
        if (tangent) {
            request[1] = tangent_draft_[0];
            request[2] = tangent_draft_[1];
        } else {
            request[1] = position_graph_axis_;
            request[2] = position_graph_draft_;
        }
        const double target = tangent ? tangent_index_ * 12 + tangent_axis_ * 4 + tangent_mode_
                                      : position_graph_index_;
        if (editor_enqueue(tangent ? EditorAction::PreviewCameraPositionTangents
                                   : EditorAction::PreviewCameraCurveValue,
                           target, &request))
            curve_preview_sent_at_ = GetTime();
    }
    const bool outside_range =
        selected &&
        std::abs(
            graph->graph_keys()[state.selected_camera].authored_pose[curve_axes_[curve_group_]]) >
            1e8;
    char snap_hint[96]{};
    if (curve_snapped_neighbor_ >= 0)
        std::snprintf(snap_hint, sizeof(snap_hint),
                      "Aligned: Camera %02u | Release applies | Right-click cancels",
                      curve_snapped_neighbor_ + 1);
    const char* reason =
        tangent_dragging_        ? "Release applies slope | Right-click cancels"
        : tangent_value_editing_ ? "Finish editing slope to apply | Right-click cancels"
        : position_graph_dragging_ && curve_draft_outside_
            ? "Release to apply and reveal key | Right-click cancels"
        : position_graph_dragging_ && curve_snapped_neighbor_ >= 0 ? snap_hint
        : position_graph_dragging_ ? "Release to apply | Alt aligns | Right-click cancels"
        : position_value_editing_  ? "Finish editing to apply | Right-click cancels"
        : locked                   ? "Finish recording to edit curves"
        : selected && !free_key    ? "Attached camera | edit attachment offsets"
        : outside_range            ? "Value exceeds editor range | Existing authoring is preserved"
        : !state.paused || state.playing ? "Pause playback to edit curves"
        : curve_show_before_ && curve_before_
            ? "Solid: Current | Dashed: Original | Wheel: zoom | Right-drag: pan"
            : "Drag vertically | Alt aligns | Wheel zooms | Right-drag pans";
    if (rail_drawing_) {
        namespace k = rail_kit;
        namespace t = rail_theme;
        namespace f = rail_fields;
        auto& d = *rail_design_;
        const auto o = GetWindowPos();
        const float y = GetWindowHeight() / s - 47;
        k::BeginRegion(o, 1120);
        k::At(17, y - 37);
        k::Text(reason, t::text_dim);
        k::At(17, y - 11);
        const auto divider = k::Place(1086, 1);
        GetWindowDrawList()->AddRectFilled(divider.Min, divider.Max, t::U32(t::border_default));
        k::At(17, y);
        if (k::Button("Play cinematic", 108, k::Btn::Primary,
                      !state.ready || state.busy || locked || state.camera_count < 2 || !matching ||
                          state.playing || !state.paused || state.attach_preview ||
                          cinematic_preparing_)) {
            EditorCinematicStatus previous{};
            if (editor_cinematic_status(previous) &&
                (!cinematic_token_ || std::int32_t(previous.token - cinematic_token_) > 0))
                cinematic_token_ = previous.token;
            if (!++cinematic_token_)
                ++cinematic_token_;
            CameraPose p{};
            p[0] = list.revision;
            if (d.send(EditorAction::PrepareCinematic, cinematic_token_, &p)) {
                cinematic_revision_ = list.revision;
                cinematic_preparing_ = true;
            }
        }
        k::At(135, y);
        if (k::Button("Seek here", 83, k::Btn::Secondary,
                      !state.ready || state.busy || locked || state.playing ||
                          state.attach_preview || !matching))
            d.send(EditorAction::SeekShot, lite_shot_time_);
        char shot[64];
        std::snprintf(shot, sizeof(shot), "%.2f / %.2f s", lite_shot_time_, state.duration);
        k::At(228, y + 7);
        k::Text(shot, t::text_muted);
        CameraPose p{};
        p[0] = list.revision;
        k::At(315, y);
        if (k::Button("Undo", 57, k::Btn::Secondary, !can_select || !(list.flags & 1)))
            d.send(EditorAction::UndoShot, 0, &p);
        k::At(382, y);
        if (k::Button("More", 56))
            OpenPopup("##curve-transport-more");
        SetNextWindowSize(ImVec2(296 * s, 184 * s));
        if (BeginPopup("##curve-transport-more")) {
            k::BeginRegion(ImVec2(GetWindowPos().x + 12 * s, GetWindowPos().y + 12 * s), 272);
            k::Text("Playback and camera", t::text_primary, k::Role::BodyMedium);
            k::VGap(6);
            k::Divider();
            k::VGap(10);
            k::BeginRow(30, 8);
            if (k::Button("< Previous", 132, k::Btn::Secondary, !can_select || !state.camera_count))
                d.send(EditorAction::PreviousView);
            if (k::Button("Forward >", 132, k::Btn::Secondary, !can_select || !state.camera_count))
                d.send(EditorAction::NextView);
            k::EndRow();
            k::VGap(10);
            if (k::Button("Redo", 132, k::Btn::Secondary, !can_select || !(list.flags & 2)))
                d.send(EditorAction::RedoShot, 0, &p);
            k::VGap(10);
            k::BeginRow(30, 10);
            k::Label("FOV", 34);
            auto& n = d.number("##lite-fov", state.horizontal_fov);
            auto e = f::DragField("##lite-fov", &n.value, .1, 90, 100,
                                  !can_select || !state.manual_active || state.horizontal_fov <= 0);
            n.active = e.active;
            if (e.released && can_select && state.manual_active && state.horizontal_fov > 0) {
                auto factor = lite_fov_factor(state.horizontal_fov, n.value);
                if (std::isfinite(factor))
                    editor_request_framing_factor(factor);
            }
            k::EndRow();
            k::EndRegion();
            EndPopup();
        }
        k::BeginRegion(o, 1120);
        k::At(751, y + 7);
        k::Text("Speed", t::text_muted);
        const double speeds[]{.05, .1, .25, .5, 1, 2, 4};
        const char* labels[]{"0.05x", "0.1x", "0.25x", "0.5x", "1x", "2x", "4x"};
        int speed = 4;
        for (int i = 0; i < 7; ++i)
            if (std::abs(speeds[i] - state.playback_speed) < .0001)
                speed = i;
        k::At(796, y);
        if (k::Dropdown("##curve-speed", &speed, labels, 7, 80,
                        !state.ready || state.busy || locked))
            d.send(EditorAction::SetPlaybackSpeed, speeds[speed]);
        auto& pause = d.number("##pause-after-capture", state.pause_after_capture ? 1 : 0);
        bool value = pause.value != 0;
        k::At(886, y + 6);
        if (k::Checkbox("Pause after capture", &value) &&
            d.send(EditorAction::SetPauseAfterCapture, value ? 1 : 0)) {
            pause.value = value ? 1 : 0;
            pause.pending_until = GetTime() + 1;
        }
        k::At(1025, y);
        if (k::Button(state.paused ? "Resume" : "Pause", 78, k::Btn::Secondary,
                      !state.ready || state.busy || locked))
            d.send(EditorAction::PlayPause);
        k::EndRegion();
    } else {
        const bool ordinary_hint = !curve_editing() && !locked && (!selected || free_key) &&
                                   !outside_range && state.paused && !state.playing;
        const char* display_reason =
            compact_header && ordinary_hint && curve_show_before_ && curve_before_
                ? "Solid: Current | Dashed: Original | Wheel: zoom | Right-drag: pan"
                : reason;
        if (footer_camera_context)
            TextDisabled("%s | %s", camera_context, display_reason);
        else
            TextDisabled("%s", display_reason);
        if (compact_header && IsItemHovered())
            SetTooltip("%s", reason);
        const float footer_y = GetWindowContentRegionMax().y - row_gap - transport_height;
        SetCursorPosY(std::max(GetCursorPosY(), footer_y));
        Separator();
        BeginDisabled(!state.ready || state.busy || locked);
        BeginDisabled(state.camera_count < 2 || !matching || state.playing || !state.paused ||
                      state.attach_preview || cinematic_preparing_);
        PushStyleColor(ImGuiCol_Button, curve_color(ui::ACCENT));
        PushStyleColor(ImGuiCol_Text, curve_color(ui::ACCENT_INK));
        if (Button("Play cinematic", ImVec2(124 * s, 0))) {
            EditorCinematicStatus previous{};
            if (editor_cinematic_status(previous) &&
                (!cinematic_token_ || std::int32_t(previous.token - cinematic_token_) > 0))
                cinematic_token_ = previous.token;
            if (!++cinematic_token_)
                ++cinematic_token_;
            CameraPose request{};
            request[0] = list.revision;
            if (editor_enqueue(EditorAction::PrepareCinematic, cinematic_token_, &request)) {
                cinematic_revision_ = list.revision;
                cinematic_preparing_ = true;
            }
        }
        PopStyleColor(2);
        EndDisabled();
        SameLine();
        BeginDisabled(state.playing || state.attach_preview || !matching);
        action_button("Seek here", EditorAction::SeekShot, 72 * s, lite_shot_time_);
        EndDisabled();
        SameLine();
        TextDisabled("%.2f / %.2f s", lite_shot_time_, state.duration);
        SameLine();
        BeginDisabled(!matching || !state.paused || state.playing || state.attach_preview ||
                      !(list.flags & 1));
        CameraPose history_request{};
        history_request[0] = list.revision;
        if (Button("Undo"))
            editor_enqueue(EditorAction::UndoShot, 0, &history_request);
        EndDisabled();
        SameLine();
        if (Button("More"))
            OpenPopup("##curve-transport-more");
        if (BeginPopup("##curve-transport-more")) {
            TextUnformatted("Playback and camera");
            Separator();
            BeginDisabled(!state.paused || state.playing || state.attach_preview ||
                          !state.camera_count);
            action_button("< Previous", EditorAction::PreviousView, 100 * s);
            SameLine();
            action_button("Forward >", EditorAction::NextView, 100 * s);
            EndDisabled();
            BeginDisabled(!state.paused || state.playing || state.attach_preview ||
                          !(list.flags & 2));
            if (Button("Redo", ImVec2(100 * s, 0)))
                editor_enqueue(EditorAction::RedoShot, 0, &history_request);
            EndDisabled();
            BeginDisabled(!state.paused || state.playing || state.attach_preview ||
                          !state.manual_active || state.horizontal_fov <= 0);
            TextDisabled("FOV");
            SameLine();
            SetNextItemWidth(100 * s);
            if (!lite_fov_editing_)
                lite_fov_draft_ = float(state.horizontal_fov);
            InputFloat("##lite-fov", &lite_fov_draft_, 0, 0, "%.1f",
                       ImGuiInputTextFlags_AutoSelectAll);
            const bool fov_commit = IsItemDeactivatedAfterEdit();
            lite_fov_editing_ = IsItemActive();
            if (fov_commit) {
                const double factor = lite_fov_factor(state.horizontal_fov, lite_fov_draft_);
                if (std::isfinite(factor))
                    editor_request_framing_factor(factor);
            }
            EndDisabled();
            TextDisabled("Replay ticks, players and kills remain in Replay.");
            EndPopup();
        }
        if (!narrow)
            SameLine(std::max(GetCursorPosX(), GetWindowContentRegionMax().x - right_transport -
                                                   (show_speed_label ? speed_label : 0)));
        if (show_speed_label) {
            TextDisabled("Speed");
            SameLine();
        }
        SetNextItemWidth(speed_width);
        if (BeginCombo("##curve-speed", speed, ImGuiComboFlags_HeightSmall)) {
            for (double value : {.25, .5, 1., 2., 4.}) {
                char label[20];
                std::snprintf(label, sizeof(label), "%gx", value);
                if (Selectable(label, std::abs(state.playback_speed - value) < .0001))
                    editor_enqueue(EditorAction::SetPlaybackSpeed, value);
            }
            EndCombo();
        }
        if (!show_speed_label && IsItemHovered())
            SetTooltip("Playback speed");
        SameLine();
        const auto now_ms = GetTickCount64();
        if (state.pause_after_capture == pause_capture_pending_value_ ||
            now_ms >= pause_capture_pending_until_)
            pause_capture_pending_until_ = 0;
        bool pause_after =
            pause_capture_pending_until_ ? pause_capture_pending_value_ : state.pause_after_capture;
        if (Checkbox("Pause after capture", &pause_after) &&
            editor_enqueue(EditorAction::SetPauseAfterCapture, pause_after ? 1 : 0)) {
            pause_capture_pending_value_ = pause_after;
            pause_capture_pending_until_ = now_ms + 1000;
        }
        SameLine();
        action_button(state.paused ? "Resume" : "Pause", EditorAction::PlayPause, 74 * s);
        EndDisabled();
    }
    EditorCinematicStatus preparation{};
    const bool have_preparation = editor_cinematic_status(preparation) &&
                                  preparation.token == cinematic_token_ &&
                                  preparation.revision == cinematic_revision_;
    if (cinematic_preparing_) {
        if (!matching || list.revision != cinematic_revision_ ||
            IsKeyPressed(ImGuiKey_Escape, false))
            cinematic_preparing_ = false;
        else if (have_preparation) {
            if (preparation.state == 3)
                cinematic_preparing_ = false;
            else if (preparation.state == 2 && state.ready && !state.busy && state.paused &&
                     !state.playing && !state.attach_preview && !locked &&
                     state.camera_count >= 2 && editor_enqueue(EditorAction::PlayPath, 0))
                cinematic_preparing_ = false;
        }
    }
}
} // namespace dolly
