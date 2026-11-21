#include "dolly_overlay_panel.hpp"
#include "dolly_rail_design.hpp"
#include "dolly_lite.hpp"
#include "dolly_replay_events.hpp"
#include "dolly_visualization_runtime.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
namespace dolly {
void OverlayPanel::draw_kara_transport(const EditorSnapshot& state) {
    namespace k = rail_kit;
    namespace t = rail_theme;
    namespace f = rail_fields;
    auto& d = *rail_design_;
    auto& io = ImGui::GetIO();
    const float s = panel_scale;
    EditorCameraList list{};
    const bool have_list = editor_camera_list(list) && list.total == state.camera_count;
    EditorLiteReplayInfo info{};
    const bool have_info = editor_lite_replay_info(info);
    EditorReplayEvents events{};
    const bool have_events = editor_replay_events(events),
               kills_ready = have_events && events.status == 2;
    const auto recording = editor_recording_status();
    const bool locked = editor_recording_locks_cameras(recording.state);
    const auto graph = visualization_snapshot();
    const bool tangents = graph && graph->smooth_position() &&
                          state.selected_camera < graph->graph_keys().size() &&
                          (curve_compare_ ? (curve_lanes_ & 1u) != 0 : curve_group_ == 0);
    const float width = 1120 * s, height = curve_view_ ? (341 + (tangents ? 40 : 0)) * s : 188 * s;
    const float left = (io.DisplaySize.x - width) * .5f;
    rail_bar_offset_.x = std::clamp(rail_bar_offset_.x, -left, left);
    rail_bar_offset_.y =
        std::clamp(rail_bar_offset_.y, -(io.DisplaySize.y - height - 8 * s), 8 * s);
    ImGui::SetNextWindowPos(
        ImVec2(left + rail_bar_offset_.x, io.DisplaySize.y - height - 8 * s + rail_bar_offset_.y));
    ImGui::SetNextWindowSize(ImVec2(width, height));
    const auto flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoSavedSettings |
                       ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoBackground |
                       ImGuiWindowFlags_NoScrollWithMouse;
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,
                        curve_view_ ? ImVec2(17 * s, 17 * s) : ImVec2());
    ImGui::Begin("##lite-timeline", nullptr, flags);
    ImGui::SetWindowFontScale(s / rail_font_dpi_);
    const auto o = ImGui::GetWindowPos();
    auto* dl = ImGui::GetWindowDrawList();
    dl->AddRectFilled(o, ImVec2(o.x + width, o.y + height), t::U32(t::bg_surface), 6 * s);
    if (rail_background_)
        dl->AddImageRounded(rail_background_, o, ImVec2(o.x + width, o.y + height), ImVec2(),
                            ImVec2(1, 1), IM_COL32_WHITE, 6 * s);
    dl->AddRect(ImVec2(o.x + .5f * s, o.y + .5f * s),
                ImVec2(o.x + width - .5f * s, o.y + height - .5f * s), t::U32(t::timeline_stroke),
                6 * s, 0, s);
    if (curve_view_) {
        // The graph engine retains its revision/session-bound edits and preview cancellation.
        // The surrounding rail has no persisted Arrange geometry.
        ImGui::SetCursorPos(ImVec2(17 * s, 17 * s));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(17 * s, 17 * s));
        ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(10 * s, 7 * s));
        ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(8 * s, 10 * s));
        draw_curve_workspace(state, list);
        ImGui::PopStyleVar(3);
    } else {
        cancel_position_graph();
        k::BeginRegion(o, 1120);
        {
            f::Greyed dim(!state.ready || state.busy);
            auto tab = [&](const char* name, float x, float w, int mode) {
                k::At(x, 17);
                if (k::Tab(name,
                           mode == 0   ? !lite_campath_timeline_
                           : mode == 1 ? lite_campath_timeline_
                                       : false,
                           w)) {
                    curve_view_ = mode == 2;
                    lite_campath_timeline_ = mode == 1;
                    timeline_view_id_ = nullptr;
                }
            };
            tab("Replay", 17, 64, 0);
            tab("Campaths", 85, 84, 1);
            tab("Curves", 173, 67, 2);
            k::At(252, 17);
            if (k::Button("Play cinematic", 108, k::Btn::Primary,
                          state.camera_count < 2 || !have_list || state.playing || !state.paused ||
                              state.attach_preview || locked || cinematic_preparing_)) {
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
            if (cinematic_preparing_) {
                EditorCinematicStatus completed{};
                if (!have_list || list.revision != cinematic_revision_ ||
                    ImGui::IsKeyPressed(ImGuiKey_Escape, false))
                    cinematic_preparing_ = false;
                else if (editor_cinematic_status(completed) &&
                         completed.token == cinematic_token_ &&
                         completed.revision == cinematic_revision_) {
                    if (completed.state == 3)
                        cinematic_preparing_ = false;
                    else if (completed.state == 2 && state.ready && !state.busy && state.paused &&
                             !state.playing && !state.attach_preview && !locked &&
                             state.camera_count >= 2 && d.send(EditorAction::PlayPath))
                        cinematic_preparing_ = false;
                }
            }
            // Player identity is supplied by the replay index; no preview names or kills.
            std::vector<int> players;
            int player = -1;
            if (kills_ready)
                for (unsigned i = 0; i < events.player_count; ++i)
                    if (events.players[i].id) {
                        if (int(events.players[i].id) == replay_player_filter_)
                            player = int(players.size());
                        players.push_back(int(events.players[i].id));
                    }
            if (player < 0 && !players.empty()) {
                player = 0;
                replay_player_filter_ = players[0];
            }
            auto focus = [&]() {
                if (state.ready && !state.busy && state.paused && !state.playing &&
                    !state.attach_preview)
                    d.send(EditorAction::FocusReplayPlayer, replay_player_filter_);
            };
            k::At(503.5f, 17);
            if (k::ChevronButton("##previous-player", k::Dir::Left,
                                 players.empty() || state.playing || state.attach_preview)) {
                player = (player + int(players.size()) - 1) % int(players.size());
                replay_player_filter_ = players[player];
                focus();
            }
            const char* name =
                player >= 0 ? replay_player_name(events, replay_player_filter_) : "Players";
            const float name_width = std::min(300.f, std::ceil(k::TextWidth(name) + 26));
            k::At(538.5f, 17);
            if (k::Button(name, name_width, k::Btn::Field, players.empty(), k::Align::Left))
                focus();
            if (ImGui::IsItemHovered()) {
                char tooltip[512];
                unsigned kills = 0, deaths = 0;
                for (unsigned i = 0; i < events.count; ++i) {
                    auto kind = replay_event_kind(events.kills[i], replay_player_filter_);
                    kills += kind == ReplayEventKind::Kill;
                    deaths += kind == ReplayEventKind::Death;
                }
                std::snprintf(
                    tooltip, sizeof(tooltip),
                    "%s\n%u kills | %u deaths\nClick the name or arrows to follow this player.",
                    name, kills, deaths);
                k::ShowTooltip(tooltip);
            }
            k::At(538.5f + name_width + 5, 17);
            if (k::ChevronButton("##next-player", k::Dir::Right,
                                 players.empty() || state.playing || state.attach_preview)) {
                player = (player + 1) % int(players.size());
                replay_player_filter_ = players[player];
                focus();
            }
            k::At(743, 24);
            k::Text("Kills  |  Wheel: zoom", t::text_dim, k::Role::Body, 16, 360, k::Align::Right);
            const bool shot = lite_campath_timeline_;
            const float track_end = shot ? 835.f : 903.f;
            rail_track_rect_ =
                ImVec4(o.x + 71 * s, o.y + 94 * s, o.x + track_end * s, o.y + 94 * s);
            k::At(17, 85);
            k::Text(shot ? "Shot" : "Tick", t::text_muted);
            SliderRow track{};
            {
                f::Inert inert(state.playing || state.attach_preview ||
                               (shot ? !state.camera_count
                                     : !(have_info && (info.flags & 2) && info.total_ticks > 0)));
                if (shot) {
                    if (!ImGui::IsAnyItemActive() &&
                        (lite_shot_playhead_ != state.playhead ||
                         lite_shot_duration_ != state.duration ||
                         (have_list && lite_shot_revision_ != list.revision))) {
                        lite_shot_time_ = float(std::clamp(state.playhead, 0., state.duration));
                        lite_shot_playhead_ = state.playhead;
                        lite_shot_duration_ = state.duration;
                        lite_shot_revision_ = have_list ? list.revision : 0;
                    }
                    const auto guides = visualization_snapshot();
                    track = slider_row("##lite-shot-position", "Shot", &lite_shot_time_, 0,
                                       float(std::max(state.duration, .001)), nullptr, 42,
                                       guides.get(), true);
                    lite_shot_time_ = std::clamp(lite_shot_time_, 0.f, float(state.duration));
                } else {
                    const auto total = have_info && (info.flags & 2) ? info.total_ticks : -1;
                    if (total != lite_replay_total_seen_) {
                        timeline_view_id_ = nullptr;
                        lite_replay_total_seen_ = total;
                    }
                    if (!lite_replay_dragging_)
                        lite_replay_cursor_ = state.tick;
                    float cursor = float(lite_replay_cursor_);
                    track = slider_row(
                        "##lite-replay-position", "Tick", &cursor, 0,
                        float(have_info ? std::max<std::int64_t>(1, info.total_ticks - 1) : 1),
                        nullptr, 42, nullptr, true);
                    lite_replay_cursor_ = std::int64_t(std::nearbyint(cursor));
                    lite_replay_dragging_ = track.active;
                }
            }
            // Kill lane and ruler use the exact same zoom/pan window as the real slider.
            const float span = timeline_view_end_ - timeline_view_start_;
            int hovered = -1;
            float nearest = 8 * s;
            if (kills_ready && span > 0 && (!shot || (have_info && (info.flags & 1))))
                for (unsigned i = 0; i < events.count; ++i) {
                    const auto& event = events.kills[i];
                    const auto kind = replay_event_kind(event, replay_player_filter_);
                    if (kind == ReplayEventKind::Hidden)
                        continue;
                    const double time =
                        shot ? double(event.tick - info.start_tick) / info.tick_rate : event.tick;
                    if (time < timeline_view_start_ || time > timeline_view_end_)
                        continue;
                    const float x = timeline_x0_ + (timeline_x1_ - timeline_x0_) *
                                                       float((time - timeline_view_start_) / span);
                    const auto color = kind == ReplayEventKind::Kill ? IM_COL32(101, 181, 255, 255)
                                                                     : IM_COL32(255, 114, 122, 255);
                    dl->AddLine(ImVec2(x, timeline_y_ - 18 * s), ImVec2(x, timeline_y_ - 7 * s),
                                color, 3 * s);
                    if (ImGui::IsWindowHovered(ImGuiHoveredFlags_AllowWhenBlockedByActiveItem) &&
                        io.MousePos.y >= timeline_y_ - 28 * s &&
                        io.MousePos.y <= timeline_y_ - 7 * s &&
                        std::abs(io.MousePos.x - x) <= nearest) {
                        nearest = std::abs(io.MousePos.x - x);
                        hovered = int(i);
                    }
                }
            if (hovered >= 0 && !state.playing && !state.attach_preview) {
                const auto& event = events.kills[hovered];
                char tip[512];
                std::snprintf(
                    tip, sizeof(tip),
                    "T %d  %s killed %s\nBlue: kills. Red: deaths. Click to seek; zoom to separate markers.",
                    event.tick, replay_player_name(events, event.killer),
                    replay_player_name(events, event.victim));
                k::ShowTooltip(tip);
                if (ImGui::IsMouseClicked(0)) {
                    GImGui->ActiveIdHasBeenEditedBefore = GImGui->ActiveIdHasBeenEditedThisFrame =
                        false;
                    ImGui::ClearActiveID();
                    lite_replay_dragging_ = false;
                    track.committed = false;
                    d.send(EditorAction::SeekReplayTick, event.tick);
                }
            }
            if (!shot && track.committed &&
                valid_lite_seek_tick(double(lite_replay_cursor_), info.total_ticks))
                d.send(EditorAction::SeekReplayTick, double(lite_replay_cursor_));
            for (unsigned i = 0; i <= 4; ++i) {
                const float fraction = i / 4.f;
                char label[32];
                const float v = timeline_view_start_ + span * fraction;
                std::snprintf(label, sizeof(label), shot ? "%.2f s" : "%.0f", v);
                const float tw = k::TextWidth(label, k::Role::Small);
                k::At(71 + (track_end - 71) * fraction - tw * .5f, 108);
                k::Text(label, t::text_dim, k::Role::Small, 16);
            }
            char readout[64];
            if (shot) {
                std::snprintf(readout, sizeof(readout), "%.3f s", lite_shot_time_);
                k::At(853, 86);
                k::Text(readout, t::text_muted, k::Role::Body, 16, 56, k::Align::Right);
            }
            std::snprintf(readout, sizeof(readout), "/ %lld",
                          static_cast<long long>(have_info ? info.total_ticks : 0));
            if (!lite_tick_editing_)
                std::snprintf(lite_tick_draft_, sizeof(lite_tick_draft_), "%d", state.tick);
            k::At(934, 86);
            k::Text("Tick", t::text_muted, k::Role::Body, 16);
            {
                f::Inert off(state.playing || state.attach_preview);
                k::At(966, 79);
                k::InputField("##lite-tick", lite_tick_draft_, sizeof(lite_tick_draft_), 80);
                lite_tick_editing_ = ImGui::IsItemActive();
                if (ImGui::IsItemDeactivatedAfterEdit() ||
                    (lite_tick_editing_ && ImGui::IsKeyPressed(ImGuiKey_Enter))) {
                    char* end = nullptr;
                    auto tick = std::strtoll(lite_tick_draft_, &end, 10);
                    if (end != lite_tick_draft_ && !*end &&
                        valid_lite_seek_tick(double(tick), have_info ? info.total_ticks : -1))
                        d.send(EditorAction::SeekReplayTick, double(tick));
                }
                k::At(1054, 86);
                k::Text(readout, t::text_dim);
            }
            k::At(17, 141);
            if (k::Button(state.paused ? "Resume" : "Pause", 78))
                d.send(EditorAction::PlayPause);
            if (shot) {
                k::At(105, 141);
                if (k::Button("Seek here", 83, k::Btn::Secondary,
                              state.playing || state.attach_preview || !state.camera_count))
                    d.send(EditorAction::SeekShot, lite_shot_time_);
            }
            k::At(shot ? 198 : 105, 141);
            if (k::ChevronButton("##prev-camera", k::Dir::Left,
                                 !state.paused || state.playing || state.attach_preview ||
                                     !state.camera_count))
                d.send(EditorAction::PreviousView);
            k::At(shot ? 233 : 140, 141);
            if (k::ChevronButton("##next-camera", k::Dir::Right,
                                 !state.paused || state.playing || state.attach_preview ||
                                     !state.camera_count))
                d.send(EditorAction::NextView);
            auto& pause = d.number("##pause-after-capture", state.pause_after_capture ? 1 : 0);
            bool paused = pause.value != 0;
            k::At(717, 147);
            if (k::Checkbox("Pause after capture", &paused) &&
                d.send(EditorAction::SetPauseAfterCapture, paused ? 1 : 0)) {
                pause.value = paused ? 1 : 0;
                pause.pending_until = ImGui::GetTime() + 1;
            }
            k::At(864, 148);
            k::Text("Speed", t::text_muted);
            const double speeds[]{.05, .1, .25, .5, 1, 2, 4};
            const char* labels[]{"0.05x", "0.1x", "0.25x", "0.5x", "1x", "2x", "4x"};
            int speed = 4;
            for (int i = 0; i < 7; ++i)
                if (std::abs(state.playback_speed - speeds[i]) < .0001)
                    speed = i;
            k::At(909, 141);
            if (k::Dropdown("##rail-speed", &speed, labels, 7, 80))
                d.send(EditorAction::SetPlaybackSpeed, speeds[speed]);
            k::At(1007, 148);
            k::Text("FOV", t::text_muted);
            auto& fov = d.number("##lite-fov", state.horizontal_fov);
            f::Inert off(!state.paused || state.playing || state.attach_preview ||
                         !state.manual_active || state.horizontal_fov <= 0);
            k::At(1039, 141);
            auto e = f::DragField("##lite-fov", &fov.value, .1, 90, 64);
            fov.active = e.active;
            if (e.released && !(GImGui->CurrentItemFlags & ImGuiItemFlags_Disabled)) {
                const auto factor = lite_fov_factor(state.horizontal_fov, fov.value);
                if (std::isfinite(factor))
                    editor_request_framing_factor(factor);
            }
        }
        k::EndRegion();
    }
    // Kara's bar can be moved by its unused background; double-click docks it.
    if (ImGui::IsWindowHovered() && !ImGui::IsAnyItemHovered() && !ImGui::IsAnyItemActive() &&
        !ImGui::IsPopupOpen(nullptr, ImGuiPopupFlags_AnyPopupId)) {
        if (ImGui::IsMouseDragging(0)) {
            rail_bar_offset_.x += io.MouseDelta.x;
            rail_bar_offset_.y += io.MouseDelta.y;
        }
        if (ImGui::IsMouseDoubleClicked(0))
            rail_bar_offset_ = ImVec2();
    }
    ImGui::End();
    ImGui::PopStyleVar();
}
}
