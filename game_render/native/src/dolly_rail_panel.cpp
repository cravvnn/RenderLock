// Kara's top rail, bound to Dolly's existing editor actions and published state.
#include "dolly_overlay_panel.hpp"
#include "dolly_rail_icons.h"
#include "dolly_rail_design.hpp"
#include "dolly_ui_tokens_generated.hpp"
#include "dolly_rail_tokens_generated.hpp"
#include "dolly_video.hpp"
#include "imgui_internal.h"
#include <algorithm>
#include <cmath>
#include <cstdio>

namespace dolly {
void OverlayPanel::draw_bone_picker(const EditorSnapshot& state, const PickerPortrait& portrait) {
    if (!rail_enabled_ || !rail_font_) {
        draw_bone_picker_controls(state, portrait);
        return;
    }
    const ImGuiStyle previous = ImGui::GetStyle();
    if (!rail_design_)
        rail_design_ = std::make_shared<RailDesignState>();
    rail_kit::Scope scope(rail_design_->kit);
    rail_kit::SetFonts(rail_font_, rail_medium_, rail_small_, rail_chip_, rail_heading_,
                       rail_font_dpi_);
    const float old_scale = panel_scale;
    panel_scale =
        std::min(ImGui::GetIO().DisplaySize.x / 1920.f, ImGui::GetIO().DisplaySize.y / 1080.f);
    rail_kit::SetScale(panel_scale);
    const auto old_heading = heading_font, old_title = title_font;
    rail_drawing_ = true;
    auto& style = ImGui::GetStyle();
    style.FrameBorderSize = 0;
    style.FrameRounding = 4 * panel_scale;
    for (auto& color : style.Colors) {
        const unsigned rgb = (unsigned(std::round(color.x * 255)) << 16) |
                             (unsigned(std::round(color.y * 255)) << 8) |
                             unsigned(std::round(color.z * 255));
        color = panel_color(rgb, color.w);
    }
    if (rail_heading_)
        heading_font = title_font = rail_heading_;
    ImGui::PushFont(rail_font_);
    draw_bone_picker_controls(state, portrait);
    rail_bar_offset_.x += 160 * panel_scale;
    rail_picker_mode_ = true;
    draw_rail(state, rail_background_);
    rail_picker_mode_ = false;
    rail_bar_offset_.x -= 160 * panel_scale;
    ImGui::PopFont();
    heading_font = old_heading;
    title_font = old_title;
    rail_drawing_ = false;
    panel_scale = old_scale;
    style = previous;
}
void OverlayPanel::draw_object_picker(const EditorSnapshot& state) {
    if (!rail_enabled_ || !rail_font_) {
        draw_object_picker_controls(state);
        return;
    }
    const ImGuiStyle previous = ImGui::GetStyle();
    if (!rail_design_)
        rail_design_ = std::make_shared<RailDesignState>();
    rail_kit::Scope scope(rail_design_->kit);
    rail_kit::SetFonts(rail_font_, rail_medium_, rail_small_, rail_chip_, rail_heading_,
                       rail_font_dpi_);
    const float old_scale = panel_scale;
    panel_scale =
        std::min(ImGui::GetIO().DisplaySize.x / 1920.f, ImGui::GetIO().DisplaySize.y / 1080.f);
    rail_kit::SetScale(panel_scale);
    const auto old_heading = heading_font, old_title = title_font;
    rail_drawing_ = true;
    auto& style = ImGui::GetStyle();
    style.FrameBorderSize = 0;
    style.FrameRounding = 4 * panel_scale;
    for (auto& color : style.Colors) {
        const unsigned rgb = (unsigned(std::round(color.x * 255)) << 16) |
                             (unsigned(std::round(color.y * 255)) << 8) |
                             unsigned(std::round(color.z * 255));
        color = panel_color(rgb, color.w);
    }
    if (rail_heading_)
        heading_font = title_font = rail_heading_;
    ImGui::PushFont(rail_font_);
    draw_object_picker_controls(state);
    rail_bar_offset_.x += 40 * panel_scale;
    rail_picker_mode_ = true;
    draw_rail(state, rail_background_);
    rail_picker_mode_ = false;
    rail_bar_offset_.x -= 40 * panel_scale;
    ImGui::PopFont();
    heading_font = old_heading;
    title_font = old_title;
    rail_drawing_ = false;
    panel_scale = old_scale;
    style = previous;
}

void OverlayPanel::draw_rail(const EditorSnapshot& state, ImTextureID background) {
    namespace k = rail_kit;
    namespace t = rail_theme;
    if (!rail_design_)
        rail_design_ = std::make_shared<RailDesignState>();
    auto& d = *rail_design_;
    k::Scope scope(d.kit);
    k::SetFonts(rail_font_, rail_medium_, rail_small_, rail_chip_, rail_heading_, rail_font_dpi_);
    auto& io = ImGui::GetIO();
    const float old_scale = panel_scale;
    const auto old_font = panel_font, old_heading = heading_font;
    const float s = std::max(.25f, std::min(io.DisplaySize.x / 1920.f, io.DisplaySize.y / 1080.f));
    const float origin = (io.DisplaySize.x - 1920 * s) * .5f;
    panel_scale = s;
    rail_drawing_ = true;
    rail_background_ = background;
    if (rail_font_)
        panel_font = rail_font_;
    if (rail_heading_)
        heading_font = rail_heading_;
    k::SetScale(s);
    ImGui::PushFont(panel_font);
    const float font_scale = s / rail_font_dpi_;
    const auto previous = ImGui::GetStyle();
    auto& style = ImGui::GetStyle();
    for (auto& color : style.Colors) {
        const unsigned rgb = (unsigned(std::round(color.x * 255)) << 16) |
                             (unsigned(std::round(color.y * 255)) << 8) |
                             unsigned(std::round(color.z * 255));
        color = panel_color(rgb, color.w);
    }
    style.WindowPadding = ImVec2();
    style.WindowBorderSize = 0;
    style.FrameBorderSize = 0;
    style.FrameRounding = 4 * s;
    style.WindowRounding = 0;
    style.PopupRounding = 6 * s;
    style.PopupBorderSize = 0;
    style.ScrollbarSize = 8 * s;
    style.Colors[ImGuiCol_Text] = t::text_primary;
    style.Colors[ImGuiCol_PopupBg] = t::bg_surface;
    style.Colors[ImGuiCol_ModalWindowDimBg] =
        ImVec4(t::bg_window.x, t::bg_window.y, t::bg_window.z, .6f);
    constexpr float widths[]{444, 444, 897, 897, 897, 444};
    constexpr const char* names[]{"Camera", "Follow", "Bone Picker", "Look", "Export", "Campaths"};
    const auto flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                       ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoBackground |
                       ImGuiWindowFlags_NoScrollWithMouse;
    bool hovered_ui = false;
    float next_x = origin + 59 * s;
    for (unsigned tab = 0; tab < 6; ++tab) {
        if (rail_picker_mode_ && tab < 5)
            continue;
        bool selected = tab == 5 ? rail_paths_open_ : rail_selected_ == int(tab);
        if (tab == 5 && !selected)
            d.clear = false;
        auto& open = d.open[tab];
        open.set(selected);
        open.step(io.DeltaTime, .32f, reduced_motion_);
        d.hover[tab].step(io.DeltaTime, .22f, reduced_motion_);
        const float progress = open.value();
        const float hover = d.hover[tab].value();
        const float width = (65 + (widths[tab] - 65) * progress) * s;
        const float x = tab == 5 ? origin + 1861 * s - width : next_x;
        next_x = x + width + 5 * s;
        const float closed_height = (65 + 25 * hover) * s;
        const float pad = tab == 0 || tab == 1 || tab == 5 ? 21.f : 31.f;
        // Measure once before opening, so the card does not reveal a stale height
        // and start a second resize as soon as its contents become visible.
        const float content_width = widths[tab] - 2 * pad + 1;
        if (!d.measured[tab]) {
            char measure_id[40];
            std::snprintf(measure_id, sizeof(measure_id), "##rail-measure%u", tab);
            ImGui::SetNextWindowPos(ImVec2(-40000, -40000));
            ImGui::SetNextWindowSize(ImVec2(widths[tab] * s, 4000 * s));
            ImGui::Begin(measure_id, nullptr,
                         flags | ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoFocusOnAppearing |
                             ImGuiWindowFlags_NoBringToFrontOnFocus);
            ImGui::SetWindowFontScale(font_scale);
            const int previous_page = lite_page_;
            lite_page_ = std::min(int(tab), 4);
            ImGui::BeginDisabled();
            k::BeginRegion(ImGui::GetWindowPos(), content_width);
            draw_kara_drawer(state, tab, true);
            rail_body_height_[tab] = d.used_height;
            ImGui::EndDisabled();
            lite_page_ = previous_page;
            ImGui::End();
            d.measured[tab] = true;
        }
        const float wanted_height = rail_body_height_[tab] + pad + (pad - 1) + 64;
        auto& tween = d.heights[tab];
        const auto height_at = [&] {
            const float time = float(std::clamp((ImGui::GetTime() - tween.started) / .32, 0., 1.));
            return tween.from +
                   (tween.to - tween.from) * (1 - (1 - time) * (1 - time) * (1 - time));
        };
        if (!tween.to)
            tween.from = tween.to = wanted_height;
        if (std::abs(wanted_height - tween.to) > .01f) {
            tween.from = progress > 0 ? height_at() : wanted_height;
            tween.to = wanted_height;
            tween.started = ImGui::GetTime();
        }
        const float target_height = (reduced_motion_ ? wanted_height : height_at()) * s;
        const float max_height = io.DisplaySize.y - 210 * s;
        const float height =
            closed_height + (std::min(target_height, max_height) - closed_height) * progress;
        char id[40];
        std::snprintf(id, sizeof(id), "##rail-tab%u", tab);
        // Round both edges, as in Kara's preview. Rounding position and width
        // separately lets Campaths' fixed right edge wobble by a pixel.
        const float pixel_x = std::round(x);
        ImGui::SetNextWindowPos(ImVec2(pixel_x, 0));
        ImGui::SetNextWindowSize(ImVec2(std::round(x + width) - pixel_x, std::round(height)));
        ImGui::Begin(id, nullptr, flags);
        ImGui::SetWindowFontScale(font_scale);
        auto* dl = ImGui::GetWindowDrawList();
        const ImVec2 a = ImGui::GetWindowPos();
        const ImVec2 b(a.x + ImGui::GetWindowWidth(), a.y + ImGui::GetWindowHeight());
        const auto fill =
            t::Mix(t::Mix(t::bg_surface, t::button_hover, hover), t::bg_surface, progress);
        // The rail hangs from the top edge: only the bottom corners are rounded.
        dl->AddRectFilled(a, b, t::U32(t::border_soft), 31 * s, ImDrawFlags_RoundCornersBottom);
        dl->AddRectFilled(ImVec2(a.x + s, a.y), ImVec2(b.x - s, b.y - s), t::U32(fill), 30 * s,
                          ImDrawFlags_RoundCornersBottom);
        hovered_ui |= ImGui::IsWindowHovered(ImGuiHoveredFlags_ChildWindows);
        // Kara's fade starts after 60% of opening TIME, not eased geometry.
        // Closing decays at the same rate; reversing keeps the current opacity.
        const float opening_time = 1 - std::cbrt(std::max(0.f, 1 - progress));
        const float fade_target = std::clamp((opening_time - .6f) / .4f, 0.f, 1.f);
        const float alpha = d.alpha[tab] =
            reduced_motion_ ? (selected ? 1.f : 0.f)
                            : std::max(fade_target, d.alpha[tab] - io.DeltaTime / (.32f * .4f));
        if (alpha > .001f) {
            lite_page_ = std::min(int(tab), 4);
            const float card_x = tab == 5 ? origin + 1861 * s - widths[tab] * s : a.x;
            ImGui::PushStyleVar(ImGuiStyleVar_Alpha, previous.Alpha * alpha);
            ImGui::PushClipRect(a, ImVec2(b.x, std::max(0.f, b.y - 64 * s)), true);
            // Keep transitioning controls inert without adding a second dim/fade.
            ImGui::PushStyleVar(ImGuiStyleVar_DisabledAlpha, 1.f);
            ImGui::BeginDisabled(!selected || progress < .98f);
            // A bounded scroller only appears when the viewport cannot hold an expanded drawer.
            ImGui::SetCursorScreenPos(ImVec2(card_x + pad * s, pad * s));
            const float body_height = std::max(1.f, height - (pad + 64) * s);
            const bool scroll = target_height > max_height;
            if (scroll) {
                // Put the scrollbar in the drawer's existing right inset. Shrinking
                // the design region cuts fixed-width rows, and placing text flush
                // against the child scissor can shave off the last glyph at a
                // fractional UI scale. Keep the full row width plus an 8px gutter.
                const float scroll_width = content_width * s + style.ScrollbarSize + 8 * s;
                ImGui::BeginChild("##drawer-scroll", ImVec2(scroll_width, body_height),
                                  ImGuiChildFlags_None, ImGuiWindowFlags_NoBackground);
            }
            k::BeginRegion(ImGui::GetCursorScreenPos(), content_width);
            draw_kara_drawer(state, tab);
            if (std::abs(rail_body_height_[tab] - d.used_height) > .01f)
                rail_body_height_[tab] = d.used_height;
            if (scroll) {
                ImGui::SetCursorPosY(d.used_height * s);
                ImGui::Dummy(ImVec2(1, 1));
                ImGui::EndChild();
            }
            ImGui::EndDisabled();
            ImGui::PopStyleVar();
            ImGui::PopClipRect();
            ImGui::PopStyleVar();
        }
        const ImVec2 icon((a.x + b.x) * .5f, b.y - (32.5f - .5f * progress) * s);
        rail_icons::DrawRail(dl, rail_icons::Rail(tab), icon, s,
                             t::U32(t::Mix(t::text_muted, t::text_primary, progress)));
        const ImRect hit(ImVec2(a.x, selected ? b.y - 64 * s : a.y), b);
        ImGui::SetCursorScreenPos(hit.Min);
        if (ImGui::InvisibleButton("##rail-button", hit.GetSize(),
                                   selected ? ImGuiButtonFlags_None
                                            : ImGuiButtonFlags_PressedOnClick)) {
            if (tab == 5) {
                rail_paths_open_ = !rail_paths_open_;
                d.clear = d.remove = false;
            } else {
                rail_selected_ = selected ? -1 : int(tab);
                lite_page_ = int(tab);
            }
        }
        rail_tab_rects_[tab] = ImVec4(hit.Min.x, hit.Min.y, hit.Max.x, hit.Max.y);
        if (ImGui::IsItemHovered())
            k::ShowTooltip(names[tab]);
        d.hover[tab].set(ImGui::IsWindowHovered(ImGuiHoveredFlags_ChildWindows) &&
                         ImGui::IsMouseHoveringRect(a, b));
        ImGui::End();
    }
    draw_kara_transport(state);
    EditorCameraList history{};
    if (state.ready && state.paused && !state.busy && !state.playing && !state.attach_preview &&
        !state.bone_picker && !editor_recording_locks_cameras(editor_recording_status().state) &&
        !io.WantTextInput && !ImGui::IsAnyItemActive() && editor_camera_list(history) &&
        history.total == state.camera_count) {
        CameraPose p{};
        p[0] = history.revision;
        if ((history.flags & 1u) &&
            ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiKey_Z, ImGuiInputFlags_RouteAlways))
            d.send(EditorAction::UndoShot, 0, &p);
        if ((history.flags & 2u) &&
            (ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiKey_Y, ImGuiInputFlags_RouteAlways) ||
             ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiMod_Shift | ImGuiKey_Z,
                             ImGuiInputFlags_RouteAlways)))
            d.send(EditorAction::RedoShot, 0, &p);
    }
    const char* status = !state.ready    ? "Waiting for replay"
                         : state.busy    ? "Working"
                         : state.playing ? "Playing shot"
                         : state.paused  ? "Paused"
                                         : "Playing";
    char tick[48];
    std::snprintf(tick, sizeof(tick), "Replay tick %d", state.tick);
    const float pill_width = 29 + std::ceil(k::TextWidth(status, k::Role::BodyMedium));
    const float status_width = 34 + pill_width + 12 + std::ceil(k::TextWidth(tick));
    ImGui::SetNextWindowPos(ImVec2(origin + 59 * s, io.DisplaySize.y - 42 * s));
    ImGui::SetNextWindowSize(ImVec2(status_width * s, 42 * s));
    ImGui::Begin("##rail-status", nullptr, flags);
    ImGui::SetWindowFontScale(font_scale);
    auto* status_draw = ImGui::GetWindowDrawList();
    const auto a = ImGui::GetWindowPos(), b = ImVec2(a.x + status_width * s, io.DisplaySize.y);
    status_draw->AddRectFilled(a, b, t::U32(t::border_soft), 10 * s, ImDrawFlags_RoundCornersTop);
    status_draw->AddRectFilled(ImVec2(a.x + s, a.y + s), ImVec2(b.x - s, b.y),
                               t::U32(t::bg_surface), 9 * s, ImDrawFlags_RoundCornersTop);
    auto pill = t::button_default, dot = t::text_muted;
    if (state.ready && (state.busy || state.playing || !state.paused)) {
        pill = t::accent_default;
        pill.w = .22f;
        dot = state.busy ? t::status_busy : t::accent_hover;
    }
    const ImVec2 pa(a.x + 17 * s, a.y + 11 * s), pb(pa.x + pill_width * s, pa.y + 21 * s);
    status_draw->AddRectFilled(pa, pb, t::U32(pill), 10.5f * s);
    status_draw->AddCircleFilled(ImVec2(pa.x + 11 * s, pa.y + 10.5f * s), 3 * s, t::U32(dot), 24);
    k::PaintText(status_draw, k::Role::BodyMedium, status, ImVec2(pa.x + 20 * s, pa.y + 3 * s), 15,
                 t::U32(t::text_primary));
    k::PaintText(status_draw, k::Role::Body, tick,
                 ImVec2(pa.x + (pill_width + 12) * s, a.y + 13.5f * s), 16, t::U32(t::text_muted));
    if (ImGui::IsWindowHovered()) {
        hovered_ui = true;
        ImGui::SetTooltip("%s\n%s\nF7 Console | F8 Editor | Escape closes drawers, then flies",
                          state.shot_name[0] ? state.shot_name : "Untitled shot", state.message);
    }
    ImGui::End();
    if (!hovered_ui && !ImGui::IsAnyItemHovered() && !ImGui::IsAnyItemActive() &&
        !ImGui::IsPopupOpen(nullptr, ImGuiPopupFlags_AnyPopupId) && ImGui::IsMouseClicked(0) &&
        !ImGui::IsWindowHovered(ImGuiHoveredFlags_AnyWindow))
        close_rail_drawers();
    style = previous;
    ImGui::PopFont();
    panel_scale = old_scale;
    panel_font = old_font;
    heading_font = old_heading;
    rail_drawing_ = false;
}
}
