// Bone Picker ImGui presentation; GPU resources belong to the overlay renderer.
#include "dolly_overlay_panel.hpp"
#include "dolly_ui_tokens_generated.hpp"
#include "dolly_rail_design.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdio>

namespace dolly {
// Picker presentation uses the renderer's borrowed portrait interface.
void OverlayPanel::draw_bone_picker_controls(const EditorSnapshot& state,
                                             const PickerPortrait& portrait) {
    static PickerFrame snapshot;
    static char search[96]{};
    static bool common_only = true;
    static std::uint32_t request = 0;
    static std::array<int, kPickerMaxBones> candidates{};
    static unsigned candidate_count = 0;
    static std::array<ImVec2, kPickerMaxBones> stable_markers{};
    static std::array<bool, kPickerMaxBones> stable_marker_ready{};
    static CameraPose marker_view{};
    static ImVec2 marker_display{};
    static double marker_fov = 0;
    // A contended camera sample must not disable an active text field for one
    // frame: ImGui drops keyboard focus when that happens. Keep the last
    // bounded snapshot; all mutations independently validate the live session.
    if (!picker_snapshot(snapshot) && (!snapshot.stamp || picker_now() - snapshot.stamp > 250))
        snapshot.ready = false;
    const auto current = snapshot.catalog ? snapshot.catalog->sequence : 0;
    if (request != current) {
        request = current;
        search[0] = 0;
        common_only = true;
        candidate_count = 0;
        stable_marker_ready.fill(false);
        portrait.reset();
    }
    if (snapshot.ready && snapshot.selected < 0 && state.attach_bone[0] && snapshot.catalog) {
        for (std::size_t i = 0; i < snapshot.catalog->bones.size(); ++i)
            if (std::strcmp(snapshot.catalog->bones[i].name, state.attach_bone) == 0) {
                picker_select(request, int(i));
                break;
            }
    }
    auto& io = ImGui::GetIO();
    // Screen-space hysteresis must never resist an intentional camera move.
    // Reproject the held world pose using the view actually applied by Camera.
    if (marker_view != snapshot.view || marker_fov != snapshot.fov ||
        marker_display.x != io.DisplaySize.x || marker_display.y != io.DisplaySize.y) {
        stable_marker_ready.fill(false);
        marker_view = snapshot.view;
        marker_fov = snapshot.fov;
        marker_display = io.DisplaySize;
    }
    if (snapshot.ready && !snapshot.preview)
        portrait.update(snapshot);
    const float margin = rail_drawing_ ? 0 : 24 * panel_scale;
    const float width =
        rail_drawing_ ? 444 * panel_scale : std::min(350 * panel_scale, io.DisplaySize.x * .42f);
    const float left = rail_drawing_
                           ? (io.DisplaySize.x - 1920 * panel_scale) * .5f + 59 * panel_scale
                           : io.DisplaySize.x - width - margin;
    unsigned visible_count = 0;
    if (snapshot.catalog)
        for (const auto& bone : snapshot.catalog->bones)
            visible_count += picker_matches(bone, search, common_only) ? 1u : 0u;
    const float row_height = ImGui::GetTextLineHeight() + 10 * panel_scale;
    const float height =
        std::min(io.DisplaySize.y - margin * 2,
                 std::max(440 * panel_scale,
                          425 * panel_scale +
                              visible_count * (row_height + ImGui::GetStyle().ItemSpacing.y)));
    if (rail_drawing_) {
        namespace k = rail_kit;
        namespace f = rail_fields;
        namespace t = rail_theme;
        auto& d = *rail_design_;
        const float scale = panel_scale;
        const bool selected = snapshot.catalog && snapshot.selected >= 0 &&
                              std::size_t(snapshot.selected) < snapshot.catalog->bones.size();
        const float list_height = std::max(64.f, 27.f * std::min(20u, visible_count) + 6);
        const float max_height = io.DisplaySize.y - 20 * scale;
        ImGui::SetNextWindowPos(ImVec2(left, 0));
        ImGui::SetNextWindowSize(ImVec2(width, std::min((452 + list_height) * scale, max_height)));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(22 * scale, 22 * scale));
        ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 2 * scale));
        ImGui::Begin("Bone Picker", nullptr,
                     ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                         ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoBackground);
        ImGui::SetWindowFontScale(scale / rail_font_dpi_);
        auto* dl = ImGui::GetWindowDrawList();
        auto o = ImGui::GetWindowPos(), sz = ImGui::GetWindowSize();
        dl->AddRectFilled(o, ImVec2(o.x + sz.x, o.y + sz.y), t::U32(t::border_soft), 16 * scale,
                          ImDrawFlags_RoundCornersBottom);
        dl->AddRectFilled(ImVec2(o.x + scale, o.y), ImVec2(o.x + sz.x - scale, o.y + sz.y - scale),
                          t::U32(t::bg_surface), 15 * scale, ImDrawFlags_RoundCornersBottom);
        k::BeginRegion(ImVec2(left + 22 * scale, 22 * scale), 401);
        k::Text("Dolly / Bone Picker", t::text_muted, k::Role::Small, 15);
        k::VGap(6);
        k::Text("Choose a joint", t::text_primary, k::Role::Title);
        k::VGap(8);
        char player[112] = "Replay hero";
        EditorRoster roster{};
        if (editor_roster_snapshot(roster))
            for (unsigned i = 0; i < roster.count; ++i)
                if (snapshot.catalog && roster.players[i].handle == snapshot.catalog->handle) {
                    roster_label(roster.players[i], player, sizeof(player));
                    break;
                }
        k::Text(player, t::text_primary, k::Role::Body, 15);
        k::VGap(3);
        k::Text("Native replay / Paused", t::text_muted, k::Role::Body, 15);
        const ImVec2 pa(left + 367 * scale, 22 * scale), pb(pa.x + 56 * scale, pa.y + 56 * scale);
        dl->AddRectFilled(pa, pb, t::U32(t::bg_field), 8 * scale);
        if (auto texture = portrait.texture())
            dl->AddImageRounded(texture, pa, pb, ImVec2(), ImVec2(1, 1), IM_COL32_WHITE, 8 * scale);
        dl->AddRect(pa, pb, t::U32(t::border_default), 8 * scale);
        k::VGap(15);
        k::Divider();
        k::VGap(16);
        k::Text("View", t::text_muted, k::Role::BodyMedium, 15);
        k::VGap(8);
        {
            f::Greyed off(!snapshot.ready || snapshot.finishing);
            k::BeginRow(30, 4);
            if (k::Tab("Hero overview", !snapshot.preview, 198.5))
                picker_preview(request, false);
            {
                f::Inert off(snapshot.selected < 0);
                if (k::Tab("Attached preview", snapshot.preview, 198.5))
                    picker_preview(request, true);
            }
            k::EndRow();
        }
        k::VGap(15);
        k::Divider();
        k::VGap(16);
        k::Text("Body joints", t::text_muted, k::Role::BodyMedium, 15);
        k::VGap(8);
        k::InputField("##bone-search", search, sizeof(search));
        if (!search[0] && !ImGui::IsItemActive())
            f::Placeholder("Search the full rig...");
        k::VGap(8);
        k::Checkbox("Common body joints", &common_only);
        k::VGap(8);
        if (search[0]) {
            k::Text("Searching all named bones", t::text_muted, k::Role::Body, 15);
            k::VGap(8);
        } else if (!common_only) {
            char all[64];
            std::snprintf(all, sizeof(all), "All %u named bones",
                          snapshot.catalog ? unsigned(snapshot.catalog->bones.size()) : 0);
            k::Text(all, t::text_muted, k::Role::Body, 15);
            k::VGap(8);
        }
        if (snapshot.error[0]) {
            k::Notice(snapshot.error);
            k::VGap(8);
        }
        // Keep selection and actions reachable on short displays; the long rig list scrolls.
        const float list_y = k::CursorY();
        const float available = std::max(64.f, (max_height / scale) - (list_y + 22 + 174));
        auto box = k::Place(401, std::min(list_height, available));
        dl->AddRectFilled(box.Min, box.Max, t::U32(t::bg_field), 6 * scale);
        ImGui::SetCursorScreenPos(ImVec2(box.Min.x + 4 * scale, box.Min.y + 3 * scale));
        ImGui::BeginChild("##picker-list", ImVec2(393 * scale, box.GetHeight() - 6 * scale),
                          ImGuiChildFlags_None, ImGuiWindowFlags_NoBackground);
        ImGui::BeginDisabled(!snapshot.ready || snapshot.finishing);
        unsigned matches = 0;
        if (snapshot.catalog)
            for (std::size_t index = 0; index < snapshot.catalog->bones.size(); ++index) {
                const auto& bone = snapshot.catalog->bones[index];
                if (!picker_matches(bone, search, common_only))
                    continue;
                ++matches;
                const char* friendly = picker_friendly_name(bone.name);
                std::array<double, 3> position{};
                const bool valid = picker_position(snapshot.sample, bone, position);
                ImGui::PushID(int(index));
                ImGui::BeginDisabled(!valid);
                if (ImGui::InvisibleButton("##bone-row", ImVec2(385 * scale, 25 * scale)))
                    picker_select(request, int(index));
                auto lo = ImGui::GetItemRectMin(), hi = ImGui::GetItemRectMax();
                bool on = int(index) == snapshot.selected;
                if (on)
                    dl = ImGui::GetWindowDrawList();
                else
                    dl = ImGui::GetWindowDrawList();
                if (on)
                    dl->AddRectFilled(lo, hi, t::U32(t::bg_rail_selected), 4 * scale);
                ImVec2 dot(lo.x + 13 * scale, lo.y + 12.5f * scale);
                if (on)
                    dl->AddCircleFilled(dot, 5 * scale, t::U32(t::accent_hover));
                dl->AddCircle(dot, 4.5f * scale, t::U32(on ? t::text_primary : t::text_muted), 0,
                              scale);
                k::PaintText(dl, k::Role::Body, friendly ? friendly : bone.name,
                             ImVec2(lo.x + 26 * scale, lo.y + 5 * scale), 15,
                             t::U32(t::text_primary));
                ImGui::EndDisabled();
                ImGui::PopID();
            }
        if (!matches)
            ImGui::TextWrapped("No matching bones. Try another search or show all joints.");
        ImGui::EndDisabled();
        ImGui::EndChild();
        k::VGap(15);
        k::Divider();
        k::VGap(16);
        k::Text("Selection", t::text_muted, k::Role::BodyMedium, 15);
        k::VGap(6);
        if (selected) {
            const auto& bone = snapshot.catalog->bones[snapshot.selected];
            const auto friendly = picker_friendly_name(bone.name);
            k::Text(friendly ? friendly : bone.name, t::text_primary, k::Role::BodyMedium, 15);
            k::VGap(6);
            if (friendly) {
                k::Text(bone.name, t::text_muted, k::Role::Body, 15);
                k::VGap(6);
            }
        } else {
            k::Text("Click a joint or choose a bone in the list.", t::text_muted, k::Role::Body,
                    15);
            k::VGap(6);
        }
        k::VGap(15);
        k::BeginRow(30, 8);
        if (k::Button("Cancel", 196.5, k::Btn::Secondary, false, k::Align::Left) ||
            ImGui::IsKeyPressed(ImGuiKey_Escape, false))
            d.send(EditorAction::CancelBonePicker);
        if (k::Button("Use this bone", 196.5, k::Btn::Primary,
                      !selected || !snapshot.ready || snapshot.finishing, k::Align::Left)) {
            CameraPose token{};
            token[0] = request;
            d.send(EditorAction::FinishBonePicker, double(snapshot.selected), &token);
        }
        k::EndRow();
        const float used = k::EndRegion() + 43;
        ImGui::SetWindowSize(ImVec2(width, std::min(max_height, used * scale)));
        ImGui::End();
        ImGui::PopStyleVar(2);
        if (!snapshot.preview) {
            auto* guide = ImGui::GetBackgroundDrawList();
            const ImVec2 a((io.DisplaySize.x - 280 * scale) * .5f, 0),
                b(a.x + 280 * scale, 59 * scale);
            guide->AddRectFilled(a, b, t::U32(t::bg_surface), 12 * scale,
                                 ImDrawFlags_RoundCornersBottom);
            k::PaintText(guide, k::Role::Small, "Live hero / Bone Picker",
                         ImVec2(a.x + 45 * scale, 13 * scale), 15, t::U32(t::text_muted));
            k::PaintText(guide, k::Role::Body, "Select a joint / Middle mouse to orbit",
                         ImVec2(a.x + 27 * scale, 33 * scale), 15, t::U32(t::text_primary));
        }
    } else {
        ImGui::SetNextWindowPos(ImVec2(left, margin), ImGuiCond_Always);
        ImGui::SetNextWindowSize(ImVec2(width, height), ImGuiCond_Always);
        ImGui::SetNextWindowBgAlpha(.94f);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 12 * panel_scale);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,
                            ImVec2(20 * panel_scale, 18 * panel_scale));
        ImGui::Begin("Bone Picker", nullptr,
                     ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                         ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoSavedSettings |
                         ImGuiWindowFlags_NoTitleBar);
        const ImVec2 portrait_min(left + width - 76 * panel_scale, margin + 16 * panel_scale);
        const ImVec2 portrait_max(portrait_min.x + 56 * panel_scale,
                                  portrait_min.y + 56 * panel_scale);
        auto* panel_draw = ImGui::GetWindowDrawList();
        panel_draw->AddRectFilled(portrait_min, portrait_max, IM_COL32(37, 55, 57, 255),
                                  7 * panel_scale);
        const auto portrait_texture = portrait.texture();
        if (portrait_texture)
            panel_draw->AddImage(portrait_texture, portrait_min, portrait_max);
        panel_draw->AddRect(portrait_min, portrait_max, IM_COL32(114, 177, 164, 255),
                            7 * panel_scale);
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "DOLLY / BONE PICKER");
        ImGui::PushFont(heading_font);
        ImGui::TextUnformatted("Choose a joint");
        ImGui::PopFont();
        EditorRoster roster{};
        if (editor_roster_snapshot(roster)) {
            char player[112]{};
            for (unsigned i = 0; i < roster.count; ++i)
                if (snapshot.catalog && roster.players[i].handle == snapshot.catalog->handle) {
                    roster_label(roster.players[i], player, sizeof(player));
                    ImGui::TextWrapped("%s", player);
                    break;
                }
        }
        ImGui::TextDisabled("Native replay / Paused");
        ImGui::Separator();
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "VIEW");
        ImGui::BeginDisabled(!snapshot.ready || snapshot.finishing);
        if (ImGui::RadioButton("Hero overview", !snapshot.preview))
            picker_preview(request, false);
        ImGui::SameLine();
        ImGui::BeginDisabled(snapshot.selected < 0);
        if (ImGui::RadioButton("Attached preview", snapshot.preview))
            picker_preview(request, true);
        ImGui::EndDisabled();
        ImGui::EndDisabled();
        ImGui::Spacing();
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "BODY JOINTS");
        ImGui::SetNextItemWidth(-1);
        ImGui::InputTextWithHint("##bone-search", "Search the full rig...", search, sizeof(search));
        ImGui::Checkbox("Common body joints", &common_only);
        if (*search)
            ImGui::TextDisabled("Searching all named bones");
        else if (!common_only)
            ImGui::TextDisabled("All %u named bones",
                                snapshot.catalog ? unsigned(snapshot.catalog->bones.size()) : 0u);
        if (snapshot.error[0])
            ImGui::TextWrapped("%s", snapshot.error);
        ImGui::BeginDisabled(!snapshot.ready || snapshot.finishing);
        const float footer = 108 * panel_scale;
        ImGui::BeginChild("##picker-list",
                          ImVec2(0, std::max(65.0f, ImGui::GetContentRegionAvail().y - footer)),
                          false, ImGuiWindowFlags_HorizontalScrollbar);
        // ImGui's navigation cursor looks like a stray left-edge selection line.
        // The filled joint circle is the sole persistent selection indicator.
        ImGui::PushStyleColor(ImGuiCol_NavCursor, IM_COL32(0, 0, 0, 0));
        unsigned matches = 0;
        if (snapshot.catalog) {
            for (std::size_t index = 0; index < snapshot.catalog->bones.size(); ++index) {
                const auto& bone = snapshot.catalog->bones[index];
                if (!picker_matches(bone, search, common_only))
                    continue;
                ++matches;
                const char* friendly = picker_friendly_name(bone.name);
                ImGui::PushID(int(index));
                std::array<double, 3> position{};
                const bool valid = picker_position(snapshot.sample, bone, position);
                ImGui::BeginDisabled(!valid);
                const float row_width = ImGui::GetContentRegionAvail().x;
                if (ImGui::InvisibleButton("##bone-row", ImVec2(row_width, row_height)))
                    picker_select(request, int(index));
                const auto row = ImGui::GetItemRectMin();
                const auto row_end = ImGui::GetItemRectMax();
                const bool hovered = ImGui::IsItemHovered();
                const auto text_y = row.y + (row_height - ImGui::GetTextLineHeight()) * .5f;
                const ImVec2 dot(row.x + 11 * panel_scale, row.y + row_height * .5f);
                auto* draw = ImGui::GetWindowDrawList();
                draw->AddRectFilled(row, row_end,
                                    rail_drawing_ ? ImGui::GetColorU32(panel_color(
                                                        hovered ? ui::BUTTON_HOVER : ui::FIELD))
                                    : hovered     ? IM_COL32(41, 70, 70, 245)
                                                  : IM_COL32(29, 45, 50, 225),
                                    5 * panel_scale);
                draw->AddRect(row, row_end,
                              rail_drawing_ ? ImGui::GetColorU32(panel_color(ui::EDGE))
                                            : IM_COL32(69, 99, 101, 255),
                              5 * panel_scale);
                draw->AddCircle(dot, 4 * panel_scale,
                                rail_drawing_ ? ImGui::GetColorU32(panel_color(ui::MUTED))
                                              : IM_COL32(146, 204, 192, 255));
                if (int(index) == snapshot.selected)
                    draw->AddCircleFilled(dot, 2.5f * panel_scale,
                                          rail_drawing_
                                              ? ImGui::GetColorU32(panel_color(ui::ACCENT))
                                              : IM_COL32(153, 247, 216, 255));
                draw->AddText(ImVec2(row.x + 25 * panel_scale, text_y),
                              valid ? IM_COL32(227, 239, 236, 255) : IM_COL32(125, 140, 140, 255),
                              friendly ? friendly : bone.name);
                if (hovered)
                    ImGui::SetTooltip("%s%s", bone.name, valid ? "" : " (pose unavailable)");
                ImGui::EndDisabled();
                ImGui::PopID();
            }
        }
        if (!matches)
            ImGui::TextWrapped("No matching bones. Try another search or show all joints.");
        ImGui::PopStyleColor();
        ImGui::EndChild();
        ImGui::Separator();
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "SELECTION");
        const bool selected = snapshot.catalog && snapshot.selected >= 0 &&
                              std::size_t(snapshot.selected) < snapshot.catalog->bones.size();
        if (selected) {
            const auto& bone = snapshot.catalog->bones[snapshot.selected];
            const auto friendly = picker_friendly_name(bone.name);
            ImGui::TextWrapped("%s", friendly ? friendly : bone.name);
            if (friendly)
                ImGui::TextDisabled("%s", bone.name);
        } else
            ImGui::TextWrapped("Click a joint or choose a bone in the list.");
        ImGui::EndDisabled();
        const float half =
            (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) * .5f;
        if (ImGui::Button("Cancel", ImVec2(half, 0)) || ImGui::IsKeyPressed(ImGuiKey_Escape, false))
            editor_enqueue(EditorAction::CancelBonePicker);
        ImGui::SameLine();
        ImGui::BeginDisabled(!selected || !snapshot.ready || snapshot.finishing);
        if (ImGui::Button("Use this bone", ImVec2(-1, 0))) {
            CameraPose token{};
            token[0] = request;
            editor_enqueue(EditorAction::FinishBonePicker, double(snapshot.selected), &token);
        }
        ImGui::EndDisabled();
        ImGui::End();
        ImGui::PopStyleVar(2);

        if (!snapshot.preview) {
            auto* guide = ImGui::GetBackgroundDrawList();
            const ImVec2 from(28 * panel_scale, 27 * panel_scale);
            const ImVec2 to(std::min(left - 20 * panel_scale, 395 * panel_scale), 88 * panel_scale);
            guide->AddRectFilled(from, to, IM_COL32(14, 25, 30, 215), 9 * panel_scale);
            guide->AddText(ImVec2(from.x + 14 * panel_scale, from.y + 11 * panel_scale),
                           IM_COL32(148, 243, 211, 255), "LIVE HERO  /  BONE PICKER");
            guide->AddText(ImVec2(from.x + 14 * panel_scale, from.y + 35 * panel_scale),
                           IM_COL32(224, 240, 238, 255),
                           "Select a joint / Hold middle mouse to orbit");
        }
    }

    if (!snapshot.ready || snapshot.preview || !snapshot.catalog || snapshot.finishing)
        return;
    // A real transparent ImGui hit surface owns clicks in the scene. Merely
    // inspecting global mouse transitions outside every window loses clicks
    // when ImGui considers their initial press to belong to the game.
    const float scene_left = rail_drawing_ ? left + width + 8 : 0;
    const float scene_right = rail_drawing_ ? io.DisplaySize.x : left - 8;
    ImGui::SetNextWindowPos(ImVec2(scene_left, 0), ImGuiCond_Always);
    ImGui::SetNextWindowSize(ImVec2(std::max(1.0f, scene_right - scene_left), io.DisplaySize.y),
                             ImGuiCond_Always);
    ImGui::Begin("##picker-canvas", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoBackground |
                     ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings |
                     ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoNavFocus |
                     ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::SetCursorPos(ImVec2(0, 0));
    const bool scene_clicked = ImGui::InvisibleButton("##joints", ImGui::GetWindowSize(),
                                                      ImGuiButtonFlags_MouseButtonLeft |
                                                          ImGuiButtonFlags_MouseButtonMiddle);
    const bool orbiting = ImGui::IsItemActive() && ImGui::IsMouseDown(ImGuiMouseButton_Middle);
    if (orbiting && !ImGui::IsPopupOpen("##overlapping-bones", ImGuiPopupFlags_AnyPopupId))
        picker_orbit(request, -io.MouseDelta.x * .25 / panel_scale,
                     io.MouseDelta.y * .25 / panel_scale);
    VisualizationView view{snapshot.view, snapshot.fov, double(io.DisplaySize.x),
                           double(io.DisplaySize.y), 1};
    auto* draw = ImGui::GetBackgroundDrawList();
    const float radius = 5 * panel_scale, hit_radius = std::max(12.0f, 12 * panel_scale);
    const bool clicking = scene_clicked && ImGui::IsMouseReleased(ImGuiMouseButton_Left) &&
                          !orbiting && io.MousePos.x >= scene_left && io.MousePos.x < scene_right &&
                          !ImGui::IsPopupOpen("##overlapping-bones", ImGuiPopupFlags_AnyPopupId);
    unsigned hits = 0;
    for (std::size_t index = 0; index < snapshot.catalog->bones.size(); ++index) {
        const auto& bone = snapshot.catalog->bones[index];
        if (!picker_matches(bone, search, common_only))
            continue;
        std::array<double, 3> point{};
        VisualizationPoint screen{};
        if (!picker_position(snapshot.sample, bone, point) ||
            !project_visualization_point(view, point, screen) || screen.x < scene_left ||
            screen.x >= scene_right)
            continue;
        auto& marker = stable_markers[index];
        picker_stabilize_marker(screen.x, screen.y, panel_scale, marker.x, marker.y,
                                stable_marker_ready[index]);
        const ImVec2 center(marker.x, marker.y);
        const float dx = center.x - io.MousePos.x, dy = center.y - io.MousePos.y;
        const bool hover = dx * dx + dy * dy <= hit_radius * hit_radius;
        const bool chosen = int(index) == snapshot.selected;
        namespace t = rail_theme;
        draw->AddCircleFilled(center, radius + 2 * panel_scale,
                              rail_drawing_ ? t::U32(t::bg_window) : IM_COL32(12, 20, 25, 240));
        draw->AddCircleFilled(center, radius,
                              rail_drawing_ ? t::U32(t::accent_default)
                                            : IM_COL32(142, 243, 215, 255));
        if (rail_drawing_)
            draw->AddCircle(center, radius + panel_scale, t::U32(t::text_primary), 24,
                            1.25f * panel_scale);
        if (chosen || hover)
            draw->AddCircle(center, radius + 6 * panel_scale,
                            rail_drawing_ ? t::U32(chosen ? t::text_primary : t::accent_hover)
                                          : IM_COL32(180, 255, 232, 255),
                            24, 2 * panel_scale);
        if (hover && !ImGui::IsPopupOpen("##overlapping-bones")) {
            const char* label = picker_friendly_name(bone.name);
            ImGui::SetTooltip("%s\n%s", label ? label : bone.name, bone.name);
        }
        if (chosen) {
            const char* label = picker_friendly_name(bone.name);
            draw->AddText(ImVec2(center.x + 14, center.y - 10), IM_COL32(240, 255, 250, 255),
                          label ? label : bone.name);
        }
        if (clicking && hover && hits < candidates.size())
            candidates[hits++] = int(index);
    }
    if (clicking) {
        candidate_count = hits;
        if (hits == 1)
            picker_select(request, candidates[0]);
        else if (hits > 1)
            ImGui::OpenPopup("##overlapping-bones");
    }
    if (ImGui::BeginPopup("##overlapping-bones")) {
        ImGui::TextUnformatted("Choose the joint at this point");
        ImGui::Separator();
        for (unsigned i = 0; i < candidate_count; ++i) {
            const auto index = candidates[i];
            if (index < 0 || std::size_t(index) >= snapshot.catalog->bones.size())
                continue;
            const auto& bone = snapshot.catalog->bones[index];
            if (ImGui::Selectable(bone.name))
                picker_select(request, index);
        }
        ImGui::EndPopup();
    }
    ImGui::End();
}

} // namespace dolly
