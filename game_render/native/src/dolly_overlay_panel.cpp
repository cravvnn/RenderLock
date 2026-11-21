// ImGui layout and actions, independent of DX11 hooks and resource ownership.
#include "dolly_overlay_panel.hpp"
#include "dolly_overlay_assets.hpp"
#include <cstdlib>
#include "imgui_internal.h"
#include "dolly_lite.hpp"
#include "dolly_attach.hpp"
#include "dolly_ui_tokens_generated.hpp"
#include "dolly_rail_tokens_generated.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_video.hpp"
#include "dolly_reshade.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace dolly {
static ImVec4 panel_color(unsigned rgb, float alpha = 1.0f) {
    return ImVec4(float((rgb >> 16) & 255) / 255.f, float((rgb >> 8) & 255) / 255.f,
                  float(rgb & 255) / 255.f, alpha);
}
ImVec4 OverlayPanel::panel_color(unsigned rgb, float alpha) const {
    if (rail_drawing_) rgb = ui::rail_color(rgb);
    return ImVec4(float((rgb >> 16) & 255) / 255.0f, float((rgb >> 8) & 255) / 255.0f,
                  float(rgb & 255) / 255.0f, alpha);
}
void OverlayPanel::style_panel() {
    lite_mode_ = lite_mode_requested();
    wchar_t mode[32]{};
    const DWORD mode_length = GetEnvironmentVariableW(L"DOLLY_OVERLAY_UI",mode,32);
    rail_enabled_ = mode_length != 7 || std::wcscmp(mode,L"classic") != 0;
    ImGui::StyleColorsDark();
    auto& style = ImGui::GetStyle();
    // Dolly palette, shared with the desktop Tk UI. The values live in
    // dolly/ui_theme.py and reach here through dolly_ui_tokens_generated.hpp
    // (regenerate with tools/generate_ui_tokens.py; use --check to verify).
    namespace ui = dolly::ui;
    style.WindowPadding = ImVec2(16, 14);
    style.FramePadding = ImVec2(10, 5);
    style.ItemSpacing = ImVec2(8, 6);
    style.ItemInnerSpacing = ImVec2(8, 6);
    style.WindowRounding = 6;
    style.ChildRounding = 4;
    style.FrameRounding = 4;
    style.PopupRounding = 4;
    style.GrabRounding = 4;
    style.ScrollbarRounding = 6;
    style.WindowBorderSize = 1;
    style.ChildBorderSize = 1;
    style.FrameBorderSize = 1;
    style.ScrollbarSize = 10;
    style.GrabMinSize = 14;
    style.DisabledAlpha = .45f;
    // Native-feeling tabs: soft corners and a slim accent overline on the active
    // tab, no heavy tab-bar border.
    style.TabRounding = 4;
    style.TabBarBorderSize = 0;
    style.TabBarOverlineSize = 2;
    // Opaque matte surfaces keep the same contrast over every replay scene.
    style.Colors[ImGuiCol_WindowBg] = panel_color(ui::BG);
    style.Colors[ImGuiCol_ChildBg] = panel_color(ui::PANEL);
    style.Colors[ImGuiCol_PopupBg] = panel_color(ui::PANEL);
    style.Colors[ImGuiCol_Border] = panel_color(ui::EDGE);
    style.Colors[ImGuiCol_BorderShadow] = ImVec4(0, 0, 0, 0);
    style.Colors[ImGuiCol_Text] = panel_color(ui::TEXT);
    style.Colors[ImGuiCol_TextDisabled] = panel_color(ui::MUTED);
    style.Colors[ImGuiCol_Button] = panel_color(ui::BUTTON);
    style.Colors[ImGuiCol_ButtonHovered] = panel_color(ui::BUTTON_HOVER);
    style.Colors[ImGuiCol_ButtonActive] = panel_color(ui::BUTTON_ACTIVE);
    // Recessed field surface so checkboxes, dropdowns and inputs read as
    // interactive controls against the panel background.
    style.Colors[ImGuiCol_FrameBg] = panel_color(ui::FIELD);
    style.Colors[ImGuiCol_FrameBgHovered] = panel_color(ui::PANEL_ALT);
    style.Colors[ImGuiCol_FrameBgActive] = panel_color(ui::BUTTON);
    style.Colors[ImGuiCol_SliderGrab] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_SliderGrabActive] = panel_color(ui::ACCENT_HOVER);
    style.Colors[ImGuiCol_CheckMark] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_Header] = panel_color(ui::SELECTED);
    style.Colors[ImGuiCol_HeaderHovered] = style.Colors[ImGuiCol_ButtonHovered];
    style.Colors[ImGuiCol_HeaderActive] = style.Colors[ImGuiCol_ButtonActive];
    style.Colors[ImGuiCol_Separator] = panel_color(ui::EDGE);
    style.Colors[ImGuiCol_ScrollbarBg] = panel_color(ui::BG, 0);
    style.Colors[ImGuiCol_ScrollbarGrab] = panel_color(ui::EDGE);
    style.Colors[ImGuiCol_ScrollbarGrabHovered] = panel_color(ui::SCROLL_HOVER);
    style.Colors[ImGuiCol_ScrollbarGrabActive] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_ResizeGrip] = panel_color(ui::ACCENT, .15f);
    style.Colors[ImGuiCol_ResizeGripHovered] = panel_color(ui::ACCENT, .45f);
    style.Colors[ImGuiCol_ResizeGripActive] = panel_color(ui::ACCENT, .75f);
    style.Colors[ImGuiCol_NavCursor] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_TextSelectedBg] = panel_color(ui::SELECTED);
    style.Colors[ImGuiCol_PlotHistogram] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_Tab] = panel_color(ui::PANEL);
    style.Colors[ImGuiCol_TabHovered] = panel_color(ui::BUTTON);
    style.Colors[ImGuiCol_TabSelected] = panel_color(ui::SELECTED);
    style.Colors[ImGuiCol_TabSelectedOverline] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_TabDimmed] = panel_color(ui::TAB_DIM);
    style.Colors[ImGuiCol_TabDimmedSelected] = panel_color(ui::TAB_DIM_SELECTED);
    style.Colors[ImGuiCol_TabDimmedSelectedOverline] = panel_color(ui::TAB_DIM_OVERLINE);
}
static bool compact_checkbox(const char* label, bool* value, float scale) {
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(4 * scale, 2 * scale));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 2 * scale);
    const bool changed = ImGui::Checkbox(label, value);
    ImGui::PopStyleVar(2);
    return changed;
}
static ImFont* installed_font(const char* filename, float size) {
    char windows[MAX_PATH]{}, path[MAX_PATH]{};
    const UINT length = GetWindowsDirectoryA(windows, MAX_PATH);
    if (!length || length >= MAX_PATH)
        return nullptr;
    const int written = std::snprintf(path, sizeof(path), "%s\\Fonts\\%s", windows, filename);
    if (written < 0 || written >= int(sizeof(path)))
        return nullptr;
    const DWORD attributes = GetFileAttributesA(path);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY))
        return nullptr;
    ImFontConfig config;
    config.OversampleH = 2;
    config.OversampleV = 2;
    return ImGui::GetIO().Fonts->AddFontFromFileTTF(path, size, &config);
}
void OverlayPanel::load_panel_fonts(float scale) {
    rail_font_dpi_=scale;
    panel_scale = scale;
    // Read the fonts already installed with Windows; no system fonts are
    // distributed with Dolly. Keep a working fallback on minimal Windows images.
    panel_font = installed_font("segoeui.ttf", 16.0f * scale);
    if (!panel_font)
        panel_font = installed_font("arial.ttf", 16.0f * scale);
    if (!panel_font) {
        ImFontConfig config;
        config.SizePixels = 16.0f * scale;
        panel_font = ImGui::GetIO().Fonts->AddFontDefault(&config);
    }
    heading_font = installed_font("seguisb.ttf", 16.0f * scale);
    if (!heading_font)
        heading_font = panel_font;
    title_font = installed_font("seguisb.ttf", 22.0f * scale);
    if (!title_font)
        title_font = heading_font;
    rail_font_ = load_rail_font(12.0f * scale);
    rail_medium_ = load_rail_medium(12.0f * scale);
    rail_small_ = load_rail_font(11.0f * scale);
    rail_chip_ = load_rail_medium(11.0f * scale);
    rail_heading_ = load_rail_title(18.0f * scale);
    rail_loading_title_ = load_rail_font(21.0f * scale,true);
    ImGui::GetIO().FontDefault = panel_font;
}

void OverlayPanel::action_button(const char* label, EditorAction action, float width, double value,
                                 bool primary) {
    if (primary) {
        ImGui::PushStyleColor(ImGuiCol_Button, panel_color(dolly::ui::ACCENT));
        ImGui::PushStyleColor(ImGuiCol_Border, panel_color(dolly::ui::ACCENT));
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, panel_color(dolly::ui::ACCENT_HOVER));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive, panel_color(dolly::ui::ACCENT_ACTIVE));
        ImGui::PushStyleColor(ImGuiCol_Text, panel_color(dolly::ui::ACCENT_INK));
        ImGui::PushFont(rail_drawing_ ? panel_font : heading_font);
    }
    if (ImGui::Button(label, ImVec2(width, 0)) && editor_enqueue(action, value)) {
        if (action == EditorAction::Stop) cinematic_preparing_ = false;
    }
    if (primary) {
        ImGui::PopFont();
        ImGui::PopStyleColor(5);
    }
}
// Stock key-cap glyph: a light cap with a dark label for the bound action,
// rendered from the live editor binding. key_cap_width() lets a caller reserve
// room so the chip never spills past the panel edge; key_cap() draws it.
bool key_cap_label(std::uint16_t vk, std::uint32_t modifiers, char* out,
                   std::size_t capacity) noexcept {
    if (!vk || capacity == 0)
        return false;
    std::size_t n = 0;
    auto append = [&](const char* s) {
        while (*s && n < capacity - 1)
            out[n++] = *s++;
    };
    if (modifiers & 1)
        append("CTRL+");
    if (modifiers & 2)
        append("ALT+");
    if (modifiers & 4)
        append("SHIFT+");
    // Function keys and single characters read cleanly; named keys use short forms.
    if (vk >= 0x70 && vk <= 0x87) {
        char fn[4]{};
        std::snprintf(fn, sizeof(fn), "F%u", unsigned(vk - 0x6f));
        append(fn);
    } else if ((vk >= '0' && vk <= '9') || (vk >= 'A' && vk <= 'Z')) {
        char ch[2] = {char(vk), 0};
        append(ch);
    } else if (vk == 0x20) {
        append("SPACE");
    } else if (vk == 0x08) {
        append("BACKSPACE");
    } else if (vk == 0x21) {
        append("PGUP");
    } else if (vk == 0x22) {
        append("PGDN");
    } else if (vk == 0xBC) {
        append(",");
    } else if (vk == 0xBE) {
        append(".");
    } else {
        return false; // Unnamed key: skip the chip rather than guess.
    }
    out[n] = 0;
    return n != 0;
}
static bool key_cap_text(EditorAction action, char* out, std::size_t capacity) {
    const EditorBinding binding = editor_binding_snapshot(action);
    return key_cap_label(binding.vk, binding.modifiers, out, capacity);
}
// Total horizontal room the chip needs, including the gap after it (0 if none).
static float key_cap_width(EditorAction action, float scale) {
    char text[24]{};
    if (!key_cap_text(action, text, sizeof(text)))
        return 0;
    return ImGui::CalcTextSize(text).x + 18 * scale;
}
// Draw the chip at the current cursor and advance it. Returns the width used.
static float key_cap(EditorAction action, float scale, bool rail = false) {
    char text[24]{};
    if (!key_cap_text(action, text, sizeof(text)))
        return 0;
    const ImVec2 size = ImGui::CalcTextSize(text);
    const float pad_x = 6 * scale, pad_y = 3 * scale;
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    const float height = size.y + pad_y * 2;
    const float width = size.x + pad_x * 2;
    auto* draw = ImGui::GetWindowDrawList();
    draw->AddRectFilled(origin, ImVec2(origin.x + width, origin.y + height),
                        ImGui::GetColorU32(panel_color(rail ? ui::rail::FIELD : ui::KEYCAP)), 4 * scale);
    if (rail) draw->AddRect(origin, ImVec2(origin.x+width, origin.y+height),
        ImGui::GetColorU32(panel_color(ui::rail::EDGE)),4*scale);
    draw->AddText(ImVec2(origin.x + pad_x, origin.y + pad_y),
                  ImGui::GetColorU32(panel_color(rail ? ui::rail::MUTED : ui::KEYCAP_INK)), text);
    ImGui::Dummy(ImVec2(width, height));
    return width;
}

void OverlayPanel::roster_label(const EditorRosterEntry& entry, char* out, std::size_t capacity) {
    const char* hero = attach_hero_name(entry.model_path);
    if (hero) {
        std::snprintf(out, capacity, "%s (%u)", hero, entry.entity_index);
        return;
    }
    const char* stem = entry.model_path;
    for (const char* p = entry.model_path; *p; ++p)
        if (*p == '/' || *p == '\\')
            stem = p + 1;
    char readable[64]{};
    std::size_t length = 0;
    while (stem[length] && stem[length] != '.' && length < sizeof(readable) - 1) {
        readable[length] = stem[length] == '_' ? ' ' : stem[length];
        ++length;
    }
    readable[length] = 0;
    std::snprintf(out, capacity, "%s (%u)", readable[0] ? readable : "Unknown", entry.entity_index);
}
// Label, ImGui slider and a right-aligned readout. Ctrl+click types an exact
// value (ImGui built-in), which keeps numeric entry available in game. With a
// timeline, the wheel zooms the visible time window and right-drag pans it so
// a crowded track stays workable. Alt+drag moves a camera tick (the playhead
// stays pinned while a tick is dragged and the new arrival commits on release
// with the revision captured at press, so the list re-sorts safely). A plain
// drag always scrubs the slider within the visible window.
OverlayPanel::SliderRow OverlayPanel::slider_row(const char* id, const char* label, float* value,
                                                 float minimum, float maximum, const char* format,
                                                 float label_width,
                                                 const VisualizationPath* timeline, bool replay_track,
                                                 bool unclamped_entry) {
    SliderRow result;
    const bool timeline_mode = (timeline != nullptr || replay_track) && maximum > minimum;
    if (timeline_mode) {
        const float full = maximum - minimum;
        const bool stale = timeline_view_id_ == nullptr
                           || std::strcmp(timeline_view_id_, id) != 0
                           || timeline_view_end_ - timeline_view_start_ <= 0.0f;
        // A camera added, removed or moved at either end changes the range
        // the view was fitted to; fit again so no camera is left out of
        // sight. Other edits keep a deliberate zoom.
        bool range_changed = false;
        if (timeline) {
            const auto& keys = timeline->graph_keys();
            const double first = keys.empty() ? 0 : keys.front().time;
            const double last = keys.empty() ? 0 : keys.back().time;
            range_changed = keys.size() != timeline_fit_count_ ||
                            first != timeline_fit_first_ || last != timeline_fit_last_;
            timeline_fit_count_ = keys.size();
            timeline_fit_first_ = first;
            timeline_fit_last_ = last;
        }
        if (stale || range_changed || timeline_view_end_ > maximum || timeline_view_start_ < minimum
                || timeline_view_end_ - timeline_view_start_ > full) {
            timeline_view_id_ = id;
            // Campath timelines fit the authored camera range, not the longer
            // effect-track duration. A shot with a 5 s DOF track and cameras
            // at 0-2.1 s otherwise opens with an empty tail that reads as a
            // phantom fourth camera after playback.
            if (timeline && !timeline->graph_keys().empty()) {
                timeline_view_start_ = std::max(
                    minimum, float(timeline->graph_keys().front().time));
                timeline_view_end_ = std::min(
                    maximum, float(timeline->graph_keys().back().time));
                if (timeline_view_end_ <= timeline_view_start_) {
                    timeline_view_start_ = minimum;
                    timeline_view_end_ = maximum;
                }
            } else {
                timeline_view_start_ = minimum;
                timeline_view_end_ = maximum;
            }
        } else {
            timeline_view_start_ = std::max(timeline_view_start_, minimum);
            timeline_view_end_ = std::min(timeline_view_end_, maximum);
        }
    }
    const float slider_min = timeline_mode ? timeline_view_start_ : minimum;
    const float slider_max = timeline_mode ? timeline_view_end_ : maximum;
    const bool rail_track=rail_drawing_ && timeline_mode && rail_track_rect_.z>rail_track_rect_.x;
    if(!rail_track) {
    ImGui::AlignTextToFramePadding();
    ImGui::TextDisabled("%s", label);
    const float right = ImGui::GetWindowContentRegionMax().x;
    const float value_width = format ? 55 * panel_scale : 0;
    const float start = label_width * panel_scale;
    ImGui::SameLine(start);
    }
    const float right = ImGui::GetWindowContentRegionMax().x;
    const float value_width = format ? 55 * panel_scale : 0;
    const float start = label_width * panel_scale;
    const float width = std::max(24.0f, right - start - value_width - 10 * panel_scale);
    if(rail_track)ImGui::SetCursorScreenPos(ImVec2(rail_track_rect_.x-4*panel_scale,rail_track_rect_.y-(ImGui::GetFontSize()+4*panel_scale)*.5f));
    ImGui::SetNextItemWidth(rail_track?rail_track_rect_.z-rail_track_rect_.x+8*panel_scale:width);
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(0, 2 * panel_scale * (card_open_ ? card_vertical_scale_ : 1)));
    ImGui::PushStyleVar(ImGuiStyleVar_GrabMinSize, 5 * panel_scale);
    for (const auto color : {ImGuiCol_FrameBg, ImGuiCol_FrameBgHovered, ImGuiCol_FrameBgActive,
                             ImGuiCol_SliderGrab, ImGuiCol_SliderGrabActive, ImGuiCol_Border})
        ImGui::PushStyleColor(color, ImVec4(0, 0, 0, 0));
    ImGui::SliderFloat(id, value, slider_min, slider_max, "",
                       unclamped_entry ? ImGuiSliderFlags_None : ImGuiSliderFlags_AlwaysClamp);
    result.committed = ImGui::IsItemDeactivatedAfterEdit();
    result.active = ImGui::IsItemActive();
    const bool deactivated = ImGui::IsItemDeactivated();
    // Hovering the Shot timeline claims the wheel so the panel's scroll region
    // does not swallow it; the wheel zooms the timeline instead.
    if (timeline_mode)
        ImGui::SetItemKeyOwner(ImGuiKey_MouseWheelY);
    ImGui::PopStyleColor(6);
    ImGui::PopStyleVar(2);
    // Process-lived like the other per-widget edit caches.
    static int dragged_tick = -1;
    static std::uint32_t dragged_revision = 0;
    static EditorLiteReplayInfo dragged_clock{};
    static float pinned_value = 0, dragged_time = 0;
    const auto& io = ImGui::GetIO();
    if (!(result.active && io.WantTextInput)) {
        const auto lo = ImGui::GetItemRectMin(), hi = ImGui::GetItemRectMax();
        const float y = (lo.y + hi.y) * .5f;
        const float x0 = lo.x + 4 * panel_scale, x1 = hi.x - 4 * panel_scale;
        const float t = std::clamp((*value - slider_min) / (slider_max - slider_min), 0.0f, 1.0f);
        const float x = x0 + (x1 - x0) * t;
        auto* draw = ImGui::GetWindowDrawList();
        draw->AddLine(ImVec2(x0, y), ImVec2(x1, y),
                      ImGui::GetColorU32(panel_color(dolly::ui::TRACK)), (rail_track?4:6) * panel_scale);
        draw->AddLine(ImVec2(x0, y), ImVec2(x, y),
                      ImGui::GetColorU32(panel_color(dolly::ui::ACCENT_ACTIVE)), (rail_track?4:6) * panel_scale);
        int hovered_tick = -1;
        double hovered_time = 0;
        if (timeline_mode) {
            timeline_x0_ = x0;
            timeline_x1_ = x1;
            timeline_y_ = y;
            const float span = timeline_view_end_ - timeline_view_start_;
            const unsigned dragged_color = 0xe8c879;
            const auto in_view = [&](double time) {
                return time >= timeline_view_start_ && time <= timeline_view_end_;
            };
            const auto marker_x = [&](double time) {
                const float current = timeline_view_end_ - timeline_view_start_;
                return x0 + (x1 - x0) *
                                std::clamp(float((time - timeline_view_start_) / current), 0.0f, 1.0f);
            };
            // Wheel zooms around the pointer; right-drag pans the window. The
            // seek preview follows the visible window so its handle stays put.
            if (ImGui::IsItemHovered() && io.MouseWheel != 0.0f) {
                const float full = maximum - minimum;
                const float fraction =
                    std::clamp((io.MousePos.x - x0) / std::max(1.0f, x1 - x0), 0.0f, 1.0f);
                // Preserve the exact playhead tick when zooming over its
                // handle. Pixel rounding at a full-replay scale otherwise
                // sends a one-tick view dozens of ticks away from the kill.
                const float anchor = std::abs(io.MousePos.x - x) <= 8 * panel_scale
                                         ? *value : timeline_view_start_ + fraction * span;
                const float factor = std::pow(.85f, io.MouseWheel);
                const float min_span = std::min(full, timeline ? .1f : 1.f);
                const float next_span = std::clamp(span * factor, min_span, full);
                const float next_start =
                    std::clamp(anchor - fraction * next_span, minimum, maximum - next_span);
                timeline_view_start_ = next_start;
                timeline_view_end_ = next_start + next_span;
                *value = std::clamp(*value, timeline_view_start_, timeline_view_end_);
            }
            if (ImGui::IsItemHovered() && ImGui::IsMouseClicked(ImGuiMouseButton_Right)
                    && span < maximum - minimum) {
                timeline_panning_ = true;
                timeline_pan_anchor_ = io.MousePos.x;
                timeline_pan_start_ = timeline_view_start_;
                timeline_pan_end_ = timeline_view_end_;
            }
            if (timeline_panning_) {
                if (ImGui::IsMouseDragging(ImGuiMouseButton_Right)) {
                    const float pan_span = timeline_pan_end_ - timeline_pan_start_;
                    const float shift = -(io.MousePos.x - timeline_pan_anchor_) /
                                        std::max(1.0f, x1 - x0) * pan_span;
                    const float next_start = std::clamp(timeline_pan_start_ + shift,
                                                        minimum, maximum - pan_span);
                    timeline_view_start_ = next_start;
                    timeline_view_end_ = next_start + pan_span;
                    *value = std::clamp(*value, timeline_view_start_, timeline_view_end_);
                }
                if (!ImGui::IsMouseDown(ImGuiMouseButton_Right))
                    timeline_panning_ = false;
            }
            if (timeline) {
            // An abandoned drag (panel hidden mid-drag) must never leak into
            // the next time the timeline is drawn.
            if (dragged_tick >= 0 && !result.active && !deactivated)
                dragged_tick = -1;
            if (!result.active && io.KeyAlt && ImGui::IsItemHovered()) {
                float best = 8 * panel_scale;
                for (const auto& camera : timeline->cameras()) {
                    if (!in_view(camera.time))
                        continue;
                    const float distance = std::abs(io.MousePos.x - marker_x(camera.time));
                    if (distance <= best) {
                        best = distance;
                        hovered_tick = int(camera.index);
                        hovered_time = camera.time;
                    }
                }
                if (hovered_tick >= 0) {
                    ImGui::SetTooltip(
                        "Camera %02u arrives at %.2f s. Alt+drag to change it.",
                        hovered_tick + 1, hovered_time);
                    ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
                }
            }
            if (ImGui::IsItemActivated() && io.KeyAlt) {
                const ImVec2 press = io.MouseClickedPos[0];
                if (press.y >= lo.y - 10 * panel_scale && press.y <= hi.y + 10 * panel_scale) {
                    float best = 8 * panel_scale;
                    for (const auto& camera : timeline->cameras()) {
                        if (!in_view(camera.time))
                            continue;
                        const float distance = std::abs(press.x - marker_x(camera.time));
                        if (distance <= best) {
                            best = distance;
                            dragged_tick = int(camera.index);
                            dragged_time = float(camera.time);
                        }
                    }
                    // The index comes from the guide snapshot; it names the
                    // same camera only when the guides match the camera list.
                    EditorCameraList list{};
                    if (dragged_tick >= 0 && editor_camera_list(list) &&
                        timeline->project_revision() == list.revision &&
                        editor_lite_replay_info(dragged_clock) &&
                        dragged_clock.revision == list.revision && (dragged_clock.flags & 1)) {
                        dragged_revision = list.revision;
                        pinned_value = *value;
                    } else dragged_tick = -1;
                }
            }
            if (dragged_tick >= 0) {
                // Keep the playhead exactly where it was for the whole drag.
                *value = pinned_value;
                const float fraction = std::clamp((io.MousePos.x - x0) / (x1 - x0), 0.0f, 1.0f);
                dragged_time = timeline_view_start_ +
                               fraction * (timeline_view_end_ - timeline_view_start_);
                double snapped = dragged_time; std::int64_t tick = -1;
                snap_camera_time(dragged_time, dragged_clock, snapped, tick);
                dragged_time = float(snapped);
                const float ghost = marker_x(snapped);
                draw->AddLine(ImVec2(ghost, y - 11 * panel_scale),
                              ImVec2(ghost, y + 11 * panel_scale),
                              ImGui::GetColorU32(panel_color(dragged_color)), 2 * panel_scale);
                char text[64]{};
                std::snprintf(text, sizeof(text), "T %lld  %.3f s", static_cast<long long>(tick), dragged_time);
                draw->AddText(ImVec2(ghost + 5 * panel_scale, y - 20 * panel_scale),
                              ImGui::GetColorU32(panel_color(dragged_color)), text);
                if (deactivated) {
                    EditorCameraList list{};
                    if (editor_camera_list(list) && list.revision == dragged_revision) {
                        CameraPose request{};
                        request[0] = double(list.revision);
                        request[1] = double(dragged_time);
                        request[2] = 1;
                        if (editor_enqueue(EditorAction::SetCameraTime, double(dragged_tick),
                                           &request))
                            ++timeline_drags_;
                    }
                    dragged_tick = -1;
                }
            }
            for (const auto& camera : timeline->cameras()) {
                if (!in_view(camera.time))
                    continue;
                const float marker = marker_x(camera.time);
                const bool selected = camera.index == timeline->selected_camera();
                const bool highlighted = int(camera.index) == hovered_tick;
                const auto color = selected || highlighted ? 0xe8c879 : 0x9caeb8;
                const float tick_width = highlighted ? 2 * panel_scale : panel_scale;
                draw->AddLine(ImVec2(marker, y - 8 * panel_scale),
                              ImVec2(marker, y + 8 * panel_scale),
                              ImGui::GetColorU32(panel_color(color)), tick_width);
            }
            }
        }
        if (rail_drawing_)
            draw->AddCircleFilled(ImVec2(x,y),6*panel_scale,ImGui::GetColorU32(panel_color(ui::STRONG)));
        else {
        draw->AddRectFilled(ImVec2(x - 2.5f * panel_scale, y - 6 * panel_scale),
                            ImVec2(x + 2.5f * panel_scale, y + 6 * panel_scale),
                            ImGui::GetColorU32(panel_color(dolly::ui::ACCENT)), 2.5f * panel_scale);
        }
    }
    if (format) {
        char readout[32]{};
        std::snprintf(readout, sizeof(readout), format, *value);
        ImGui::SameLine(right - ImGui::CalcTextSize(readout).x);
        ImGui::TextDisabled("%s", readout);
    }
    return result;
}
void OverlayPanel::section_title(const char* title, const char* detail) {
    // Deadlock draws section headers in caps; uppercase the title for a native
    // look without shipping the game font.
    char upper[96]{};
    std::size_t n = 0;
    for (; title[n] && n < sizeof(upper) - 1; ++n)
        upper[n] = (title[n] >= 'a' && title[n] <= 'z') ? char(title[n] - 32) : title[n];
    upper[n] = 0;
    if (rail_drawing_) std::snprintf(upper, sizeof(upper), "%s", title);
    const auto position=ImGui::GetCursorScreenPos();
    const float heading_size = rail_drawing_ ? 18*panel_scale : ImGui::GetFontSize();
    section_names_[card_index_]=title;
    const float width=ImGui::GetContentRegionAvail().x;
    ImGui::PushItemFlag(ImGuiItemFlags_Disabled,false);
    const float old_max_x=ImGui::GetCurrentWindow()->DC.CursorMaxPos.x;
    if(layout_editing_) {
        ImGui::InvisibleButton("##section-handle",ImVec2(width+6*panel_scale,ImGui::GetTextLineHeight()));
        ImGui::GetCurrentWindow()->DC.CursorMaxPos.x=std::max(old_max_x,position.x+width);
    }
    else ImGui::Dummy(ImVec2(width,rail_drawing_ ? 26*panel_scale : ImGui::GetTextLineHeight()));
    const bool hovered=ImGui::IsItemHovered(),active=ImGui::IsItemActive();
    ImGui::GetWindowDrawList()->AddText(heading_font,heading_size,position,
                                      ImGui::GetColorU32(panel_color(rail_drawing_ ? ui::STRONG : ui::MUTED)),upper);
    const float right=position.x+width+3*panel_scale;
    if(layout_editing_)for(int row=0;row<3;++row)for(int column=0;column<2;++column)
        ImGui::GetWindowDrawList()->AddCircleFilled(ImVec2(right-column*4*panel_scale,position.y+(4+row*4)*panel_scale),
            .8f*panel_scale,ImGui::GetColorU32(panel_color(ui::MUTED,(hovered||active) ? .9f : .55f)));
    if(layout_editing_ && (hovered||active))ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeAll);
    if(layout_editing_ && hovered)ImGui::SetTooltip("Drag to swap, insert between sections, or place in empty column space.");
    if(layout_editing_ && ImGui::BeginDragDropSource(ImGuiDragDropFlags_SourceNoPreviewTooltip)) {
        const SectionPayload payload{lite_page_,card_index_};
        ImGui::SetDragDropPayload("DOLLY_SECTION",&payload,sizeof(payload));
        ImGui::EndDragDropSource();
    }
    ImGui::PopItemFlag();
    if (detail && !compact_controls_) {
        const float width = ImGui::CalcTextSize(detail).x;
        const float right = ImGui::GetWindowContentRegionMax().x;
        if (ImGui::CalcTextSize(upper).x + width + 36 * panel_scale <
            ImGui::GetContentRegionAvail().x) {
            ImGui::SameLine(right - width - 12 * panel_scale);
            ImGui::TextDisabled("%s", detail);
        }
    }
}
bool OverlayPanel::begin_panel_card(const char* name) {
    const unsigned index = layout_card_index_++;
    card_open_ = true;card_index_=index;
    card_column_ = section_columns_[index];
    card_target_y_=section_positions_[index].y;
    const float gap = 12 * panel_scale;
    const float available = ImGui::GetWindowContentRegionMax().x - ImGui::GetWindowContentRegionMin().x;
    const float width = section_widths_[index];
    // Spread controls only into space explicitly added by the user. Bound the
    // density adjustment so text and default/expanded layouts remain readable.
    card_vertical_scale_=section_extra_draw_[index]>0 ? std::min(1.8f,
        1+section_extra_draw_[index]/std::max(1.f,section_heights_[lite_page_][index]*panel_scale)) : 1;
    const float progress = motion_progress(tab_changed_at_);
    const float rearranged=motion_progress(sections_changed_at_[lite_page_],.16);
    const float spare=std::max(0.f,ImGui::GetWindowContentRegionMax().y-card_target_y_-section_heights_[lite_page_][index]*panel_scale);
    ImGui::SetCursorPos(ImVec2(section_positions_[index].x+tab_direction_*20*panel_scale*(1-progress),
                              card_target_y_+std::min(6*panel_scale,spare)*(1-rearranged)));
    float lifted=1;
    if(const auto* payload=ImGui::GetDragDropPayload();payload && payload->IsDataType("DOLLY_SECTION") && payload->DataSize==sizeof(SectionPayload)) {
        const auto& source=*static_cast<const SectionPayload*>(payload->Data);
        if(source.page==lite_page_ && source.index==index)lifted=.45f;
    }
    ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * (.65f + .35f * progress) * (.85f + .15f * rearranged)*lifted);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2((rail_drawing_ ? 0.f : 12.f) * panel_scale, (rail_drawing_ ? 0.f : compact_controls_ ? 5.f : 10.f) * panel_scale));
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(8 * panel_scale, (compact_controls_ ? 2.f : 6.f) * panel_scale * card_vertical_scale_));
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(10 * panel_scale, (compact_controls_ ? 2.f : 5.f) * panel_scale * card_vertical_scale_));
    ImGui::PushStyleColor(ImGuiCol_ChildBg, panel_color(dolly::ui::PANEL));
    if(layout_columns_>1 || section_extra_draw_[index]>0)
        ImGui::SetNextWindowSizeConstraints(ImVec2(0,section_draw_heights_[index]),ImVec2(FLT_MAX,FLT_MAX));
    return ImGui::BeginChild(name, ImVec2(width - 20 * panel_scale * (1 - progress), 0),
                             (rail_drawing_ ? 0 : ImGuiChildFlags_Borders) | ImGuiChildFlags_AutoResizeY |
                                 ImGuiChildFlags_AlwaysUseWindowPadding | ImGuiChildFlags_AlwaysAutoResize,
                             ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
}
void OverlayPanel::end_panel_card() {
    if (!card_open_) return;
    auto* window = ImGui::GetCurrentWindow();
    // Match ImGui's natural AutoResizeY content/ideal measurement. Neither
    // cursor extent includes the minimum added for neighboring cards.
    const float content_height=std::trunc(std::max(window->DC.CursorMaxPos.y,window->DC.IdealMaxPos.y)-
                                          window->DC.CursorStartPos.y);
    const float height=content_height+2*window->WindowPadding.y+window->DecoOuterSizeY1+
                       window->DecoOuterSizeY2-window->ScrollbarSizes.y;
    section_heights_[lite_page_][card_index_]=height/(panel_scale*card_vertical_scale_);
    card_column_y_[card_column_]=std::max(card_column_y_[card_column_],card_target_y_+std::max(height,window->Size.y)+12*panel_scale);
    const ImRect bounds(window->Pos,ImVec2(window->Pos.x+window->Size.x,window->Pos.y+window->Size.y));
    if(layout_editing_ && ImGui::BeginDragDropTargetCustom(bounds,window->GetID("##section-target"))) {
        if(const auto* accepted=ImGui::AcceptDragDropPayload("DOLLY_SECTION",ImGuiDragDropFlags_AcceptBeforeDelivery | ImGuiDragDropFlags_AcceptNoDrawDefaultRect)) {
            if(accepted->DataSize==sizeof(SectionPayload)) {
                const auto payload=*static_cast<const SectionPayload*>(accepted->Data);
                if(payload.page==lite_page_ && payload.index<section_count(lite_page_) && payload.index!=card_index_) {
                    window->DrawList->AddRect(bounds.Min,bounds.Max,ImGui::GetColorU32(panel_color(ui::ACCENT,.65f)),4*panel_scale,0,1*panel_scale);
                    const bool before=ImGui::GetIO().MousePos.y<bounds.Min.y+8*panel_scale;
                    const bool after=ImGui::GetIO().MousePos.y>bounds.Max.y-18*panel_scale;
                    if(before || after)window->DrawList->AddLine(ImVec2(bounds.Min.x,before ? bounds.Min.y : bounds.Max.y),ImVec2(bounds.Max.x,before ? bounds.Min.y : bounds.Max.y),ImGui::GetColorU32(panel_color(ui::ACCENT)),3*panel_scale);
                    if(accepted->IsDelivery()) {
                        if(before || after) {
                            for(unsigned i=0;i<section_count(lite_page_);++i)section_column_pref_[lite_page_][i]=int(section_columns_[i]);
                            unsigned order[4]{},n=0;
                            for(unsigned slot=0;slot<section_count(lite_page_);++slot)for(unsigned i=0;i<section_count(lite_page_);++i)
                                if(i!=payload.index && section_slots_[lite_page_][i]==slot)order[n++]=i;
                            unsigned at=0;while(at<n && order[at]!=card_index_)++at;if(after)++at;
                            for(unsigned j=n;j>at;--j)order[j]=order[j-1];order[at]=payload.index;
                            for(unsigned j=0;j<=n;++j)section_slots_[lite_page_][order[j]]=j;
                            section_column_pref_[lite_page_][payload.index]=int(card_column_);
                        } else {
                        std::swap(section_slots_[lite_page_][payload.index],section_slots_[lite_page_][card_index_]);
                        const auto source_column=section_columns_[payload.index];
                        section_column_pref_[lite_page_][payload.index]=int(card_column_);
                        section_column_pref_[lite_page_][card_index_]=int(source_column);
                        }
                        sections_changed_at_[lite_page_]=ImGui::GetTime();save_layout();
                    }
                }
            }
        }
        ImGui::EndDragDropTarget();
    }
    if(layout_editing_) {
        const auto cursor=ImGui::GetCursorScreenPos();const auto cursor_max=ImGui::GetCurrentWindow()->DC.CursorMaxPos;
        const auto ideal_max=window->DC.IdealMaxPos;const bool cursor_was_set=window->DC.IsSetPos;
        ImGui::PushID(int(card_index_));ImGui::PushItemFlag(ImGuiItemFlags_Disabled,false);
        ImGui::SetCursorScreenPos(ImVec2(bounds.Max.x-16*panel_scale,bounds.Max.y-16*panel_scale));
        ImGui::InvisibleButton("##section-resize",ImVec2(16*panel_scale,16*panel_scale),ImGuiButtonFlags_FlattenChildren);
        for(int i=0;i<3;++i)ImGui::GetWindowDrawList()->AddLine(ImVec2(bounds.Max.x-(5+i*4)*panel_scale,bounds.Max.y-3*panel_scale),ImVec2(bounds.Max.x-3*panel_scale,bounds.Max.y-(5+i*4)*panel_scale),ImGui::GetColorU32(panel_color(ui::ACCENT,.6f)));
        if(ImGui::IsItemHovered() || ImGui::IsItemActive())ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeNWSE);
        if(ImGui::IsItemHovered())ImGui::SetTooltip("Resize section. Column widths stay aligned; text stays the same size.");
        if(ImGui::IsItemActivated()) {section_resize_=int(card_index_);resize_mouse_=ImGui::GetIO().MousePos;resize_size_=ImVec2(bounds.Max.x-bounds.Min.x,(bounds.Max.y-bounds.Min.y)/panel_scale);}
        if(ImGui::IsItemActive()) {
            const auto mouse=ImGui::GetIO().MousePos;
            section_minimum_[lite_page_][card_index_]=std::clamp(resize_size_.y+(mouse.y-resize_mouse_.y)/panel_scale,0.f,900.f);
            if(layout_columns_>1) {
                const float total=section_canvas_.x-12*panel_scale*(layout_columns_-1);
                const float minimum=std::min(total/layout_columns_,(layout_columns_==3 ? 160.f : 240.f)*panel_scale);
                const float flexible=total-minimum*layout_columns_;
                const unsigned other=(card_column_+1)%layout_columns_;
                if(flexible>1) {
                    float sum=0;for(unsigned i=0;i<layout_columns_;++i)sum+=column_weights_[lite_page_][i];
                    const float pair=column_weights_[lite_page_][card_column_]+column_weights_[lite_page_][other];
                    const float desired=(resize_size_.x+mouse.x-resize_mouse_.x-minimum)/flexible*sum;
                    column_weights_[lite_page_][card_column_]=std::clamp(desired,.2f,pair-.2f);
                    column_weights_[lite_page_][other]=pair-column_weights_[lite_page_][card_column_];
                }
            }
        }
        if(ImGui::IsItemDeactivated()) {section_resize_=-1;save_layout();}
        ImGui::PopItemFlag();ImGui::PopID();ImGui::SetCursorScreenPos(cursor);
        window->DC.CursorMaxPos=cursor_max;window->DC.IdealMaxPos=ideal_max;
        // Restoring a cursor is decoration, not a content-size request. If
        // IsSetPos leaks from this helper, End() adds trailing item spacing
        // back into natural content and makes the resize grip enlarge cards.
        window->DC.IsSetPos=cursor_was_set;
    }
    ImGui::EndChild();
    ImGui::PopStyleColor();
    ImGui::PopStyleVar(4);
    card_open_ = false;
}
bool OverlayPanel::inline_controls(const char* label, const char* display) {
    if (rail_drawing_) {
        ImGui::PushStyleColor(ImGuiCol_Header, ImVec4());
        ImGui::PushStyleColor(ImGuiCol_HeaderHovered, panel_color(ui::BUTTON_HOVER,.5f));
        ImGui::PushStyleColor(ImGuiCol_Text, panel_color(ui::MUTED));
    }
    const bool open = display ? ImGui::TreeNodeBehavior(ImGui::GetID(label),ImGuiTreeNodeFlags_CollapsingHeader,display) : ImGui::CollapsingHeader(label);
    if (display && ImGui::IsItemHovered()) ImGui::SetTooltip("%s",label);
    if (rail_drawing_) ImGui::PopStyleColor(3);
    return open;
}
void OverlayPanel::replay_badge(const EditorSnapshot& state) {
    const char* text = state.busy      ? "Working"
                       : !state.ready  ? "Waiting for replay"
                       : state.playing ? "Playing shot"
                       : state.paused  ? "Paused"
                                       : "Playing";
    const ImVec2 start = ImGui::GetCursorScreenPos();
    const ImVec2 text_size = ImGui::CalcTextSize(text);
    const ImVec2 size(text_size.x + 30 * panel_scale, text_size.y + 10 * panel_scale);
    ImGui::GetWindowDrawList()->AddRectFilled(start, ImVec2(start.x + size.x, start.y + size.y),
                                              ImGui::GetColorU32(panel_color(dolly::ui::SELECTED)),
                                              5 * panel_scale);
    ImGui::GetWindowDrawList()->AddCircleFilled(
        ImVec2(start.x + 11 * panel_scale, start.y + size.y / 2), 3 * panel_scale,
        ImGui::GetColorU32(panel_color(dolly::ui::ACCENT)));
    ImGui::GetWindowDrawList()->AddText(
        ImVec2(start.x + 20 * panel_scale, start.y + 5 * panel_scale),
        ImGui::GetColorU32(panel_color(dolly::ui::ACCENT_HOVER)), text);
    ImGui::Dummy(size);
    if (state.tick > 0 && ImGui::GetIO().DisplaySize.x >= 900 * panel_scale &&
        ImGui::GetIO().DisplaySize.y >= 650 * panel_scale) {
        char tick[48]{};
        std::snprintf(tick, sizeof(tick), "Replay tick %d", state.tick);
        const float width = ImGui::CalcTextSize(tick).x;
        if (size.x + width + 12 * panel_scale < ImGui::GetContentRegionAvail().x) {
            ImGui::SameLine(ImGui::GetWindowContentRegionMax().x - width);
            ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 5 * panel_scale);
            ImGui::TextDisabled("%s", tick);
        }
    }
}
bool OverlayPanel::guides_visible(const EditorSnapshot& state,bool) noexcept {
    return show_path_guides && state.enabled && state.ready && state.paused &&
           state.manual_active && !state.playing && !state.busy && state.view_width > 0 &&
           state.view_height > 0 && !state.attach_preview && !state.bone_picker && !state.object_picker &&
           !editor_recording_locks_cameras(editor_recording_status().state) &&
           (state.owner == EditorOwner::Flight || state.owner == EditorOwner::Panel || state.owner==EditorOwner::Unfocused);
}
bool OverlayPanel::preview_guides_visible(const EditorSnapshot& state,const VisualizationPreview& preview) noexcept {
    return preview.path && guides_visible(state,preview.source==1) && !state.attach_preview &&
        !state.bone_picker && !state.object_picker &&
        !editor_recording_locks_cameras(editor_recording_status().state) &&
        (preview.source==1 || (preview.source==2 && state.focused && state.owner==EditorOwner::Panel));
}
bool OverlayPanel::framing_grid_visible(const EditorSnapshot& state) noexcept {
    // Same paused-editing policy as the path guides, but independent of the
    // path-guides checkbox and available with the panel open or closed.
    return editor_framing_grid_enabled() && state.enabled && state.focused && state.ready &&
           state.paused && state.manual_active && !state.playing && !state.busy &&
           state.view_width > 0 && state.view_height > 0 &&
           (state.owner == EditorOwner::Flight || state.owner == EditorOwner::Panel);
}
void OverlayPanel::draw_framing_grid(const EditorSnapshot& state) {
    if (!framing_grid_visible(state))
        return;
    const auto size = ImGui::GetIO().DisplaySize;
    // Fixed After Effects-style standard guide: rule of thirds plus a centre
    // cross, one thin light line at the panel's guide opacity.
    const unsigned grid_color = 0xffffff;
    const ImU32 color = ImGui::GetColorU32(panel_color(grid_color, .35f));
    const float width = panel_scale;
    auto* draw = ImGui::GetBackgroundDrawList();
    draw->PushClipRect(ImVec2(0, 0), size, true);
    for (const float fraction : {1.0f / 3.0f, 2.0f / 3.0f}) {
        draw->AddLine(ImVec2(size.x * fraction, 0), ImVec2(size.x * fraction, size.y), color,
                      width);
        draw->AddLine(ImVec2(0, size.y * fraction), ImVec2(size.x, size.y * fraction), color,
                      width);
    }
    const float arm = 12 * panel_scale;
    const ImVec2 center(size.x * .5f, size.y * .5f);
    draw->AddLine(ImVec2(center.x - arm, center.y), ImVec2(center.x + arm, center.y), color,
                  width);
    draw->AddLine(ImVec2(center.x, center.y - arm), ImVec2(center.x, center.y + arm), color,
                  width);
    draw->PopClipRect();
}
void OverlayPanel::draw_path_guides(const EditorSnapshot& state,
                                    const std::shared_ptr<const VisualizationPath>& path,bool desktop_preview) {
    if (!path || !path->enabled() || !guides_visible(state,desktop_preview))
        return;
    const auto size = ImGui::GetIO().DisplaySize;
    VisualizationView view{state.pose, state.horizontal_fov, double(size.x), double(size.y), 1};
    if (!project_visualization(*path, view, guide_geometry))
        return;
    auto* draw = ImGui::GetBackgroundDrawList();
    draw->PushClipRect(ImVec2(0, 0), size, true);
    for (std::size_t i = 0; i < guide_geometry.line_count; ++i) {
        const auto& line = guide_geometry.lines[i];
        const bool selected = line.kind == VisualizationKind::SelectedCamera;
        const auto color = selected                               ? IM_COL32(255, 205, 115, 245)
                           : line.kind == VisualizationKind::Path ? IM_COL32(100, 214, 195, 205)
                                                                  : IM_COL32(153, 185, 226, 220);
        const ImVec2 a(line.a.x, line.a.y), b(line.b.x, line.b.y);
        const float width = (selected ? 2.0f : 1.3f) * panel_scale;
        draw->AddLine(a, b, IM_COL32(8, 12, 17, 170), width + 2 * panel_scale);
        draw->AddLine(a, b, color, width);
    }
    for (std::size_t i = 0; i < guide_geometry.label_count; ++i) {
        const auto& label = guide_geometry.labels[i];
        char text[32]{};
        std::snprintf(text, sizeof(text), "%u", label.camera_index + 1);
        const ImVec2 point(label.point.x + 5 * panel_scale, label.point.y + 5 * panel_scale);
        const auto measured = ImGui::CalcTextSize(text);
        draw->AddRectFilled(
            ImVec2(point.x - 3 * panel_scale, point.y - 2 * panel_scale),
            ImVec2(point.x + measured.x + 3 * panel_scale, point.y + measured.y + 2 * panel_scale),
            IM_COL32(14, 19, 26, 210), 3 * panel_scale);
        draw->AddText(point,
                      label.selected ? IM_COL32(255, 205, 115, 255) : IM_COL32(208, 223, 243, 255),
                      text);
    }
    draw->PopClipRect();
}
// Export encoder labels match the desktop Export tab's codec list; the numeric
// IDs are the same dolly::video::Codec values sent over the editor wire.
static const char* video_codec_label(std::uint32_t id) noexcept {
    switch (id) {
    case 1:
        return "NVIDIA H.264 (NVENC)";
    case 2:
        return "NVIDIA HEVC (NVENC)";
    case 3:
        return "H.264 (Media Foundation)";
    case 4:
        return "Software H.264 (x264)";
    case 5:
        return "Software HEVC (x265)";
    case 6:
        return "Intel H.264 (Quick Sync)";
    case 7:
        return "Intel HEVC (Quick Sync)";
    case 8:
        return "AMD H.264 (AMF)";
    case 9:
        return "AMD HEVC (AMF)";
    case 10:
        return "Lossless FFV1 (.mkv)";
    default:
        return "Auto (hardware when available)";
    }
}
static constexpr char export_sources[] = "Camera path / Bone camera\0Player POV\0";
void OverlayPanel::draw_panel(const EditorSnapshot& state) {
    for(auto& rect:main_tab_rects_) rect=ImVec4();
    auto& io = ImGui::GetIO();
    const float margin =
        std::min(20.0f * panel_scale, std::min(io.DisplaySize.x, io.DisplaySize.y) * .04f);
    const bool compact = io.DisplaySize.x < 900 * panel_scale || io.DisplaySize.y < 650 * panel_scale;
    const bool compressed=io.DisplaySize.y<650*panel_scale;
    if(compressed) {
        ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,ImVec2(4*panel_scale,3*panel_scale));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,ImVec2(ImGui::GetStyle().WindowPadding.x,8*panel_scale));
    }
    const auto export_recording = editor_recording_status();
    const bool recording_locked = editor_recording_locks_cameras(export_recording.state);
    const bool editor_arranged=layout_custom_ || editor_custom_;
    const float left = compact || editor_arranged ? margin : 284 * panel_scale;
    const float maximum_width=std::max(1.f,io.DisplaySize.x-left-margin);
    const float desired_width=std::min(940*panel_scale,compact ? maximum_width : std::min(maximum_width,io.DisplaySize.x-500*panel_scale));
    const char* pages[] = {"Camera###CAMERA", "Follow###FOLLOW", "Bone Picker###BONE PICKER", "Look###LOOK", "Export###EXPORT"};
    const char* status=state.busy ? "Working" : !state.ready ? "Waiting for replay" : state.playing ? "Playing shot" : state.paused ? "Paused" : "Playing";
    float header_width=ImGui::CalcTextSize("Deadlock Dolly").x+ImGui::CalcTextSize(status).x+30*panel_scale;
    for(const auto* page:pages) header_width+=ImGui::CalcTextSize(page,nullptr,true).x+2*ImGui::GetStyle().FramePadding.x;
    header_width+=ImGui::CalcTextSize("Layout").x+2*ImGui::GetStyle().FramePadding.x;
    if(compact) header_width+=ImGui::CalcTextSize("Paths").x+2*ImGui::GetStyle().FramePadding.x;
    header_width+=(compact ? 8.f : 7.f)*ImGui::GetStyle().ItemSpacing.x;
    float header_available_width=desired_width;
    if(editor_arranged && !(layout_reset_&1)) {
        if(const auto* previous=ImGui::FindWindowByName("Deadlock Dolly")) header_available_width=previous->Size.x;
        else if(const auto* saved=ImGui::FindWindowSettingsByID(ImHashStr("Deadlock Dolly")); saved && saved->Size.x>0)
            header_available_width=float(saved->Size.x);
        header_available_width=std::clamp(header_available_width,std::min(640*panel_scale,maximum_width),maximum_width);
    }
    const bool single_header=compressed && header_width<=header_available_width-2*ImGui::GetStyle().WindowPadding.x;
    layout_single_header_=single_header;
    const float header_height=std::max(ImGui::GetFrameHeight(),ImGui::GetTextLineHeight()+10*panel_scale)+
        (single_header ? 0.f : ImGui::GetFrameHeightWithSpacing());
    layout_main_minimum_=2*ImGui::GetStyle().WindowPadding.y+header_height+40*panel_scale+
        ImGui::GetFrameHeightWithSpacing()+3*ImGui::GetStyle().ItemSpacing.y+14*panel_scale;
    if(compact && compact_paths_) { if(compressed) ImGui::PopStyleVar(2); return; }
    const float top=editor_arranged && !(layout_reset_&1) ? margin : layout_top_inset();
    const ImVec2 maximum(std::max(1.0f, io.DisplaySize.x - left - margin),
                         std::max(1.0f, io.DisplaySize.y-lite_timeline_height()-
                             (editor_arranged && !(layout_reset_&1) ? 2*margin+12*panel_scale : top+28*panel_scale)));
    const ImVec2 size(desired_width, std::min(520*panel_scale,maximum.y));
    ImGui::SetNextWindowPos(editor_position_pending_ ? editor_position_ : ImVec2(io.DisplaySize.x-size.x-margin,top),
                              editor_position_pending_ || !editor_arranged || (layout_reset_ & 1) ? ImGuiCond_Always : ImGuiCond_FirstUseEver);
    editor_position_pending_=false;
    // Keep all tabs at one height, and constrain older tall saved layouts.
    ImGui::SetNextWindowSize(size,
                             !editor_arranged || (layout_reset_ & 1) ? ImGuiCond_Always : ImGuiCond_FirstUseEver);
    layout_reset_ &= ~1u;
    // Keep resized and restored menus wide enough for readable control columns.
    ImGui::SetNextWindowSizeConstraints(ImVec2(std::min(640 * panel_scale,maximum.x),
                                               std::min(520 * panel_scale,maximum.y)), maximum);
    if (ImGui::Begin("Deadlock Dolly", nullptr,
                     ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoCollapse |
                         ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse |
                         ImGuiWindowFlags_NoMove | (layout_editing_ ? 0 : ImGuiWindowFlags_NoResize))) {
        constrain_panel();
        panel_handle("Drag main menu",true);
        EditorCameraList history{};
        if (state.ready && state.paused && !state.busy && !state.playing && !state.attach_preview &&
            !state.bone_picker && !recording_locked && !io.WantTextInput && !ImGui::IsAnyItemActive() &&
            editor_camera_list(history) && history.total == state.camera_count) {
            CameraPose request{};
            request[0] = double(history.revision);
            if ((history.flags & 1u) && ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiKey_Z,ImGuiInputFlags_RouteAlways))
                editor_enqueue(EditorAction::UndoShot, 0, &request);
            if ((history.flags & 2u) &&
                (ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiKey_Y,ImGuiInputFlags_RouteAlways) ||
                 ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiMod_Shift | ImGuiKey_Z,ImGuiInputFlags_RouteAlways)))
                editor_enqueue(EditorAction::RedoShot, 0, &request);
        }
        ImGui::PushFont(single_header ? panel_font : title_font);
        ImGui::TextUnformatted("Deadlock Dolly");
        ImGui::PopFont();
        if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenOverlappedByItem)) ImGui::SetTooltip("%s",state.shot_name[0] ? state.shot_name : "Untitled shot");
        ImGui::SameLine();
        replay_badge(state);
        if (compact && !single_header) {
            ImGui::SameLine(ImGui::GetWindowContentRegionMax().x - 62 * panel_scale);
            layout_controls();
        }
        for (int page = 0; page < 5; ++page) {
            if (page || single_header) ImGui::SameLine();
            if (page == lite_page_) ImGui::PushStyleColor(ImGuiCol_Button, panel_color(ui::SELECTED));
            if (ImGui::Button(pages[page])) lite_pending_page_ = page;
            const auto tab_min=ImGui::GetItemRectMin(),tab_max=ImGui::GetItemRectMax();
            main_tab_rects_[page]=ImVec4(tab_min.x,tab_min.y,tab_max.x,tab_max.y);
            if (page == lite_page_) ImGui::PopStyleColor();
        }
        if (compact) {
            ImGui::SameLine();
            if (ImGui::Button("Paths")) { compact_paths_ = true; save_layout(); }
        }
        if(!compact || single_header) {
            ImGui::SameLine(ImGui::GetWindowContentRegionMax().x - 62 * panel_scale);
            layout_controls();
        }
        if (lite_pending_page_ >= 0) {
            if (lite_pending_page_ != lite_page_) {
                tab_direction_ = lite_pending_page_ > lite_page_ ? 1 : -1;
                tab_changed_at_ = ImGui::GetTime();
            }
            lite_page_ = lite_pending_page_; lite_pending_page_ = -1;
            save_layout();
        }
        ImGui::Separator();
        ImGui::Spacing();
        // All cards share one continuous tab. Fit the presentation to the
        // available workspace; no section pager or alternate detail screens.
        const float footer_height = ImGui::GetFrameHeightWithSpacing() + 10 * panel_scale;
        // Very small/high-DPI viewports may not have forty logical pixels
        // left. Use the actual remainder rather than pushing the fixed
        // Stop/Finish footer outside the window; the body remains scrollable.
        const float body_height = std::max(1.f,ImGui::GetContentRegionAvail().y - footer_height);
        compact_controls_ = body_height < 440 * panel_scale;
        const float content_width = ImGui::GetContentRegionAvail().x;
        layout_columns_ = content_width >= 400 * panel_scale && lite_page_ >= 3 ? 3 :
                          content_width >= 460 * panel_scale ? 2 : 1;
        const float base_scale = panel_scale;
        // One workspace scale for every tab. Inline expansion never changes it.
        // Curves can deliberately reserve a larger bottom workspace. Keep the
        // existing controls readable and scroll their content instead of
        // shrinking all camera options into the remaining few pixels.
        layout_scroll_content_=curve_view_ || (!editor_arranged && body_height<320*base_scale);
        const float content_scale = layout_scroll_content_ ? 1.f :
            std::min({1.f,body_height / (320 * base_scale),content_width / (550 * base_scale)});
        ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0, 0, 0, 0));
        // Aligned rows can be taller than the former independent columns.
        // Keep their final controls reachable without changing the font scale.
        if (ImGui::BeginChild("##editor-content", ImVec2(0, body_height), ImGuiChildFlags_None)) {
            ImGui::SetWindowFontScale(content_scale);
            panel_scale *= content_scale;
            if (lite_page_ == 4) {
                // A full source name needs room for both its text and the
                // combo arrow. Reflow Export before its cards are placed,
                // including custom column weights and the tab-entry inset.
                // Reserve the existing maximum vertical card expansion too:
                // ImGui sizes a combo's arrow from its whole frame height.
                const float source_width = ImGui::CalcTextSize(export_sources).x +
                    ImGui::GetTextLineHeight() +
                    2 * (compact_controls_ ? 2.f : 5.f) * panel_scale * 1.8f +
                    2 * 10 * panel_scale;
                const float minimum_card = std::ceil(source_width +
                    (2 * 12 + 20) * panel_scale + 1.f);
                const float gap = 12 * panel_scale;
                const float available = ImGui::GetContentRegionAvail().x;
                while (layout_columns_ > 1) {
                    const float total = available - gap * (layout_columns_ - 1);
                    const float minimum = std::min(total / layout_columns_,
                        (layout_columns_ == 3 ? 160.f : 240.f) * panel_scale);
                    const float flexible = std::max(0.f, total - minimum * layout_columns_);
                    float weight = 0;
                    for (unsigned column = 0; column < layout_columns_; ++column)
                        weight += column_weights_[lite_page_][column];
                    float used = 0;
                    bool fits = true;
                    for (unsigned column = 0; column < layout_columns_; ++column) {
                        const float width = column + 1 == layout_columns_ ? total - used :
                            std::floor(minimum + flexible *
                                column_weights_[lite_page_][column] / weight + .5f);
                        fits &= width >= minimum_card;
                        used += width;
                    }
                    if (fits) break;
                    --layout_columns_;
                }
            }
            draw_controls(state);
            panel_scale = base_scale;
            ImGui::SetWindowFontScale(1);
        }
        ImGui::EndChild();
        ImGui::PopStyleColor();
        ImGui::Spacing();
        draw_controls_footer(state);
    }
    ImGui::End();
    if(compressed) ImGui::PopStyleVar(2);
}

void OverlayPanel::draw_controls(const EditorSnapshot& state) {
    const auto export_recording = editor_recording_status();
    const bool recording_locked = editor_recording_locks_cameras(export_recording.state);
    layout_card_index_ = 0;
    card_origin_y_ = ImGui::GetCursorPosY();
    std::fill(std::begin(card_column_y_), std::end(card_column_y_), card_origin_y_);
    prepare_sections();
    if (lite_page_ == 0) {
        ImGui::BeginDisabled(!state.ready || state.busy);
        if (begin_panel_card("##cameras-card")) {
            char count[32]{};
            std::snprintf(count, sizeof(count), "%u saved", state.camera_count);
            section_title("Replay and shot", count);
            const auto guides = visualization_snapshot();
            {
            char tick_label[64]{};
            std::snprintf(tick_label, sizeof(tick_label), "Replay tick %d", state.tick);
            if (!compact_controls_) ImGui::TextDisabled("%s", tick_label);
            ImGui::BeginDisabled(state.playing || state.attach_preview);
            static int tick_step = 1;
            const float step_width = 88 * panel_scale;
            const float arrow_width =
                std::max(1.0f, (ImGui::GetContentRegionAvail().x - step_width -
                                2 * ImGui::GetStyle().ItemSpacing.x) /
                                   2);
            action_button("< Back", EditorAction::StepReplayTicks, arrow_width, -tick_step);
            ImGui::SameLine();
            ImGui::SetNextItemWidth(step_width);
            char step_label[24]{};
            std::snprintf(step_label, sizeof(step_label), "%d tick%s", tick_step,
                          tick_step == 1 ? "" : "s");
            if (ImGui::BeginCombo("##tick-step", step_label, ImGuiComboFlags_HeightLargest)) {
                for (int step : {1, 2, 5, 10, 25}) {
                    char label[24]{};
                    std::snprintf(label, sizeof(label), "%d tick%s", step,
                                  step == 1 ? "" : "s");
                    if (ImGui::Selectable(label, step == tick_step))
                        tick_step = step;
                }
                ImGui::EndCombo();
            }
            ImGui::SameLine();
            action_button("Forward >", EditorAction::StepReplayTicks, arrow_width,
                          tick_step);
            if (!compact_controls_) ImGui::TextDisabled(state.attach_preview ? "Detach to step with a fixed camera."
                                                     : "Tick steps keep the camera fixed.");
            ImGui::EndDisabled();
            char speed_label[24]{};
            std::snprintf(speed_label, sizeof(speed_label), "%.3g x", state.playback_speed);
            const float speed_width = std::max(ImGui::CalcTextSize(speed_label).x,
                ImGui::CalcTextSize("0.05 x").x) + ImGui::GetFrameHeight() +
                2 * ImGui::GetStyle().FramePadding.x;
            const bool inline_speed = 58 * panel_scale - ImGui::GetCursorPosX() +
                speed_width <= ImGui::GetContentRegionAvail().x;
            ImGui::AlignTextToFramePadding();
            ImGui::TextDisabled("Speed");
            if (inline_speed) ImGui::SameLine(58 * panel_scale);
            ImGui::SetNextItemWidth(-1);
            if (ImGui::BeginCombo("##playback-speed", speed_label, ImGuiComboFlags_HeightLargest)) {
                for (double speed : {.05, .1, .25, .5, 1.0, 2.0, 4.0}) {
                    char label[24]{};
                    std::snprintf(label, sizeof(label), "%.3g x", speed);
                    if (ImGui::Selectable(label, speed == state.playback_speed))
                        editor_enqueue(EditorAction::SetPlaybackSpeed, speed);
                }
                ImGui::EndCombo();
            }
            ImGui::Separator();
            ImGui::BeginDisabled(recording_locked);
            const float capture_hint = key_cap_width(EditorAction::Capture, panel_scale);
            action_button(
                "Capture camera here", EditorAction::Capture,
                ImGui::GetContentRegionAvail().x -
                    (capture_hint > 0 ? capture_hint + ImGui::GetStyle().ItemSpacing.x : 0),
                0, true);
            if (capture_hint > 0) {
                ImGui::SameLine();
                ImGui::AlignTextToFramePadding();
                key_cap(EditorAction::Capture, panel_scale, rail_drawing_);
            }
            ImGui::EndDisabled();
            }
            {
            ImGui::BeginDisabled(!state.ready || state.busy || state.playing ||
                                 !state.camera_count || recording_locked);
            if (ImGui::Button("Reset camera path",
                              ImVec2(ImGui::GetContentRegionAvail().x, 0)))
                reset_path_confirm_ = true;
            ImGui::EndDisabled();
            ImGui::Separator();
            ImGui::BeginDisabled(!state.camera_count || state.playing);
            ImGui::BeginDisabled(recording_locked);
            action_button("Replace selected", EditorAction::Replace,
                          ImGui::GetContentRegionAvail().x);
            ImGui::EndDisabled();
            const float half =
                (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
            action_button("Previous view", EditorAction::PreviousView, half);
            ImGui::SameLine();
            action_button("Next view", EditorAction::NextView, half);
            ImGui::EndDisabled();
            ImGui::Spacing();
            }
            {
            compact_checkbox("Show path guides", &show_path_guides, panel_scale);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Camera positions and spline while editing a paused replay. The selected camera is gold. Guides show through walls and hide during playback.");
            if (show_path_guides && state.camera_count && !guides) {
                const auto viewer_state = visualization_runtime_state();
                if (viewer_state == VisualizationRuntimeState::Invalid ||
                    viewer_state == VisualizationRuntimeState::Unavailable)
                    ImGui::TextWrapped(
                        "Path guides unavailable. Export diagnostics from the desktop editor.");
            }
            if (show_path_guides && guides &&
                guides->camera_count() > guides->cameras().size())
                ImGui::TextDisabled("%u of %u camera markers; selected included",
                                    unsigned(guides->cameras().size()),
                                    unsigned(guides->camera_count()));
            ImGui::Spacing();
            ImGui::AlignTextToFramePadding();
            ImGui::TextDisabled("Framing guide");
            const float grid_hint = key_cap_width(EditorAction::FramingGrid, panel_scale);
            if (grid_hint > 0) {
                ImGui::SameLine();
                key_cap(EditorAction::FramingGrid, panel_scale, rail_drawing_);
            }
            ImGui::SameLine();
            bool framing_grid = editor_framing_grid_enabled();
            if (compact_checkbox("Rule of thirds", &framing_grid, panel_scale))
                editor_enqueue(EditorAction::FramingGrid);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Thirds and centre cross while editing a paused replay. Toggle with the bound key.");
            }
        }
        end_panel_card();
        if (begin_panel_card("##flight-card")) {
            // Send one change at the end of a drag, not a settings write per frame.
            static float speed_draft = 400.0f;
            static bool speed_editing = false;
            if (!speed_editing)
                speed_draft = std::clamp(static_cast<float>(state.speed), 1.0f, 10000.0f);
            section_title("Free camera");
            if (!compact_controls_) ImGui::TextWrapped("Set movement speed. Mouse wheel adjusts framing while flying.");
            {
            ImGui::TextDisabled("Movement speed");
            char speed_label[32]{};
            std::snprintf(speed_label, sizeof(speed_label), "%.0f", speed_draft);
            ImGui::SameLine(ImGui::GetWindowContentRegionMax().x -
                            ImGui::CalcTextSize(speed_label).x);
            ImGui::TextUnformatted(speed_label);
            ImGui::SetNextItemWidth(-1);
            ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,
                                ImVec2(12 * panel_scale, 3 * panel_scale * card_vertical_scale_));
            ImGui::SliderFloat("##flight-speed", &speed_draft, 1.0f, 10000.0f, "",
                               ImGuiSliderFlags_Logarithmic | ImGuiSliderFlags_AlwaysClamp);
            ImGui::PopStyleVar();
            const bool speed_committed = ImGui::IsItemDeactivatedAfterEdit();
            speed_editing = ImGui::IsItemActive();
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Movement speed in world units per second. Ctrl+click to enter a value.");
            if (speed_committed)
                editor_enqueue(EditorAction::SetSpeed, double(speed_draft));
            ImGui::Spacing();
            // Fly camera is used after F8 closes Dolly, so no key cap for
            // it; Heroes / game UI keeps its cap.
            const float gameui_hint = key_cap_width(EditorAction::GameUI, panel_scale);
            const float half =
                (ImGui::GetContentRegionAvail().x - 2 * ImGui::GetStyle().ItemSpacing.x -
                 (gameui_hint > 0 ? gameui_hint : 0)) /
                2;
            action_button("Fly camera", EditorAction::Flight, half);
            ImGui::SameLine();
            action_button("Heroes / game UI", EditorAction::GameUI, half, 1);
            if (gameui_hint > 0) {
                ImGui::SameLine();
                ImGui::AlignTextToFramePadding();
                key_cap(EditorAction::GameUI, panel_scale, rail_drawing_);
            }
            }
            ImGui::Spacing();
            {
            ImGui::BeginDisabled(state.playing);
            ImGui::TextUnformatted("Updates / s");
            ImGui::SetNextItemWidth(-1);
            char playback_rate[32]{};
            std::snprintf(playback_rate, sizeof(playback_rate), "%u", state.playback_rate);
            if (ImGui::BeginCombo("##playback-rate", playback_rate, ImGuiComboFlags_HeightLargest)) {
                for (unsigned value : {30u, 60u, 120u}) {
                    char label[32]{};
                    std::snprintf(label, sizeof(label), "%u", value);
                    if (ImGui::Selectable(label, value == state.playback_rate))
                        editor_enqueue(EditorAction::SetPlaybackRate, double(value));
                }
                ImGui::EndCombo();
            }
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Native monitoring frequency. Camera and supported effects follow each rendered frame; this is not an output FPS setting.");
            ImGui::EndDisabled();
            }
            bool replay_hud = state.replay_show_hud;
            if (compact_checkbox("Show game HUD during replay", &replay_hud, panel_scale))
                editor_enqueue(EditorAction::SetReplayHud, replay_hud ? 1 : 0);
        }
        end_panel_card();
        ImGui::EndDisabled();
        // Outside the busy-disabled cards: Cancel must stay available if the
        // replay turns busy while the confirmation is open.
        if (reset_path_confirm_) {
            reset_path_confirm_ = false;
            ImGui::OpenPopup("Reset camera path?");
        }
        if (ImGui::BeginPopupModal("Reset camera path?", nullptr,
                                   ImGuiWindowFlags_AlwaysAutoResize)) {
            ImGui::TextUnformatted("Replace all camera views with the current view?");
            ImGui::TextUnformatted("Lens and depth-of-field tracks will be kept.");
            ImGui::BeginDisabled(!state.ready || state.busy || state.playing ||
                                 !state.camera_count || recording_locked);
            if (ImGui::Button("Reset here") && editor_enqueue(EditorAction::ResetCameraPath))
                ImGui::CloseCurrentPopup();
            ImGui::EndDisabled();
            ImGui::SameLine();
            if (ImGui::Button("Cancel"))
                ImGui::CloseCurrentPopup();
            ImGui::EndPopup();
        }
    }
    if (lite_page_ == 1) {
        ImGui::BeginDisabled(!state.ready || state.busy);
        EditorRoster roster{};
        const bool roster_ok = editor_roster_snapshot(roster);
        static std::uint32_t follow_handle = 0;
        int follow_index = -1;
        if (roster_ok)
            for (std::uint32_t i = 0; i < roster.count && i < kEditorRosterPlayers; ++i)
                if (roster.players[i].handle == follow_handle)
                    follow_index = int(i);
        char hero_label[112]{};
        if (follow_index >= 0)
            roster_label(roster.players[follow_index], hero_label, sizeof(hero_label));
        else
            std::snprintf(hero_label, sizeof(hero_label), "Select a hero...");
        static float follow_draft[3] = {135, 34, 0};
        static bool follow_dirty = false;
        static double follow_applied[3] = {-1, 0, 0};
        const double published[3] = {state.follow_distance, state.follow_shoulder,
                                     state.follow_height};
        if (published[0] != follow_applied[0] || published[1] != follow_applied[1] ||
            published[2] != follow_applied[2]) {
            for (int i = 0; i < 3; ++i) {
                follow_applied[i] = published[i];
                follow_draft[i] = float(published[i]);
            }
            follow_dirty = false;
        }
        if (begin_panel_card("##follow-card")) {
            section_title("Follow a player", state.follow_active ? "Active" : "Choose a target");
            if (!compact_controls_) ImGui::TextWrapped("Follow a hero's aim and movement with the game's camera.");
            ImGui::TextDisabled("Replay hero");
            ImGui::BeginDisabled(!state.follow_available || state.playing ||
                                 !state.paused || state.attach_preview);
            ImGui::SetNextItemWidth(-1);
            if (ImGui::BeginCombo("##follow-hero", hero_label, ImGuiComboFlags_HeightLargest)) {
                if (!roster_ok || !roster.count)
                    ImGui::TextDisabled("Waiting for replay players...");
                else
                    for (std::uint32_t i = 0; i < roster.count && i < kEditorRosterPlayers;
                         ++i) {
                        char label[112]{};
                        roster_label(roster.players[i], label, sizeof(label));
                        ImGui::PushID(int(i));
                        if (ImGui::Selectable(label, int(i) == follow_index))
                            follow_handle = roster.players[i].handle;
                        ImGui::PopID();
                    }
                ImGui::EndCombo();
            }
            ImGui::EndDisabled();
            ImGui::BeginDisabled(!state.follow_pending);
            action_button("Restore rig", EditorAction::StopGameFollow, -1);
            ImGui::EndDisabled();
            if (!state.follow_available)
                ImGui::TextWrapped("Follow unavailable for this game build.");
            else if (state.attach_preview)
                ImGui::TextWrapped("Detach the bone preview to use Follow.");
            else if (follow_dirty)
                ImGui::TextDisabled("Changes ready to apply");
            if (!compact_controls_) ImGui::TextWrapped(
                "Restore rig resets camera settings and keeps the selected hero.");
            bool show_hud = state.replay_show_hud;
            if (compact_checkbox("Show game HUD during replay", &show_hud, panel_scale))
                editor_enqueue(EditorAction::SetReplayHud, show_hud ? 1 : 0);
            if (!compact_controls_) ImGui::TextWrapped(
                "Applies to Follow and ordinary replay playback. Pause restores the previous HUD.");
        }
        end_panel_card();
        if (begin_panel_card("##follow-framing")) {
            section_title("Camera position", "Follow rig");
            ImGui::BeginDisabled(!state.follow_available || state.playing ||
                                 !state.paused || state.attach_preview);
            const char* names[] = {"Distance", "Shoulder", "Height"};
            for (int i = 0; i < 3; ++i) {
                ImGui::PushID(i);
                if (compact_controls_) {
                    const auto row = slider_row("##follow-offset", names[i], &follow_draft[i],
                                               i == 0 ? 0.f : -150.f, i == 0 ? 400.f : 150.f, "%.0f", 76);
                    follow_dirty |= row.active || row.committed;
                } else {
                    ImGui::TextUnformatted(names[i]);
                    ImGui::SetNextItemWidth(-1);
                    if (ImGui::SliderFloat("##follow-offset", &follow_draft[i],
                                       i == 0 ? 0.f : -150.f, i == 0 ? 400.f : 150.f,
                                       "%.0f", ImGuiSliderFlags_AlwaysClamp))
                        follow_dirty = true;
                }
                if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                    const float defaults[] = {135, 34, 0};
                    follow_draft[i] = defaults[i];
                    follow_dirty = true;
                }
                ImGui::PopID();
                if (ImGui::IsItemHovered())
                    ImGui::SetTooltip(
                        i == 0
                            ? "Distance behind the hero. Ctrl+click to type. Right-click resets to 135."
                        : i == 1
                            ? "Negative: left. Positive: right. Right-click resets to 34."
                            : "Height above or below the pivot. Right-click resets to 0.");
            }
            ImGui::BeginDisabled(follow_index < 0);
            if (ImGui::Button(state.follow_active ? "Apply Follow" : "Preview Follow",
                              ImVec2(-1, 0)) &&
                follow_index >= 0) {
                CameraPose request{};
                request[0] = follow_draft[1];
                request[1] = follow_draft[2];
                request[2] = follow_index;
                request[3] = roster.players[follow_index].handle;
                request[4] = roster.players[follow_index].entity_index;
                request[5] = std::uint32_t(roster.players[follow_index].model);
                request[6] = std::uint32_t(roster.players[follow_index].model >> 32);
                editor_enqueue(EditorAction::StartGameFollow, follow_draft[0], &request);
            }
            ImGui::EndDisabled();
            ImGui::EndDisabled();
        }
        end_panel_card();
        ImGui::EndDisabled();
    }
    if (lite_page_ == 2) {
        ImGui::BeginDisabled(!state.ready || state.busy);
        if (begin_panel_card("##attach-card")) {
            section_title("Bone camera", "Player or weapon");
            if (!compact_controls_) ImGui::TextWrapped("Choose a player, pick an attachment point, then adjust the camera around it.");
            if (!compact_controls_ && !state.camera_count)
                ImGui::TextWrapped(
                    "Choose a player to start a bone camera. No camera setup needed.");
            ImGui::BeginDisabled(!state.attach_available);
            EditorRoster roster{};
            const bool roster_ok = editor_roster_snapshot(roster);
            char player_label[112]{};
            if (state.attach_selected && roster_ok && roster.count &&
                state.attach_target_index < roster.count) {
                roster_label(roster.players[state.attach_target_index], player_label,
                             sizeof(player_label));
            } else {
                std::snprintf(player_label, sizeof(player_label), "Select a player...");
            }
            ImGui::SetNextItemWidth(-1);
            if (ImGui::BeginCombo("##attach-player", player_label, ImGuiComboFlags_HeightLargest)) {
                if (ImGui::Selectable("Free path", !state.attach_selected))
                    editor_enqueue(EditorAction::AttachReset);
                ImGui::Separator();
                if (!roster_ok || !roster.count) {
                    ImGui::TextDisabled("No players in the loaded replay");
                } else {
                    const std::uint32_t count = std::min<std::uint32_t>(
                        roster.count, std::uint32_t(kEditorRosterPlayers));
                    for (std::uint32_t index = 0; index < count; ++index) {
                        char name[112]{};
                        roster_label(roster.players[index], name, sizeof(name));
                        if (ImGui::Selectable(name, index == state.attach_target_index))
                            editor_enqueue(EditorAction::SetAttachTarget, double(index));
                        if (index == state.attach_target_index)
                            ImGui::SetItemDefaultFocus();
                    }
                }
                ImGui::EndCombo();
            }
            ImGui::SetNextItemWidth(-1);
            if (ImGui::BeginCombo("##attach-point", state.attach_point == 2 ? "Point: Bone"
                                                    : state.attach_point == 1
                                                        ? "Point: Weapon"
                                                        : "Point: Eyes")) {
                if (ImGui::Selectable("Eyes", state.attach_point == 0))
                    editor_enqueue(EditorAction::SetAttachPoint, 0);
                if (ImGui::Selectable("Weapon", state.attach_point == 1))
                    editor_enqueue(EditorAction::SetAttachPoint, 1);
                if (ImGui::Selectable("Bone Picker...", state.attach_point == 2))
                    editor_enqueue(EditorAction::OpenBonePicker);
                ImGui::EndCombo();
            }
            if (state.attach_point == 2 && !compact_controls_) {
                if (ImGui::Button("Change bone...", ImVec2(-1, 0)))
                    editor_enqueue(EditorAction::OpenBonePicker);
                ImGui::TextWrapped("Selected joint: %s", state.attach_bone[0]
                                                             ? state.attach_bone
                                                             : "Choose a bone");
            }
            ImGui::Separator();
            const float attach_half =
                (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
            const bool attach_ready = state.attach_selected &&
                                      (state.manual_active || state.follow_active) &&
                                      state.paused && !state.playing;
            ImGui::BeginDisabled(!attach_ready);
            action_button(state.attach_preview ? "Detach" : "Attach here",
                          EditorAction::AttachPreview, attach_half,
                          state.attach_preview ? 0 : 1, !state.attach_preview);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Live preview: the camera follows this player. Transfers from Follow automatically; WASD and the mouse edit the offsets.");
            ImGui::SameLine();
            ImGui::BeginDisabled(!state.manual_active || state.attach_preview);
            action_button("Snap", EditorAction::AttachSnap, attach_half);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Store offsets that reproduce the current free camera pose. Fly to frame, then Snap.");
            ImGui::EndDisabled();
            ImGui::EndDisabled();
            if (!compact_controls_) {
            if (!state.attach_selected)
                ImGui::TextDisabled("Choose a player to preview or snap.");
            else if (!state.manual_active && !state.follow_active)
                ImGui::TextDisabled("Start Fly camera to preview or snap.");
            else if (state.attach_preview)
                ImGui::TextDisabled("Preview active; detach to snap a free-camera view.");
            else
                ImGui::TextDisabled("Fly to frame, then Snap, then Attach here.");
            }
            ImGui::Spacing();
            action_button("Cycle target", EditorAction::AttachCycleTarget, attach_half, 1);
            ImGui::SameLine();
            action_button("Reset", EditorAction::AttachReset, attach_half);
            ImGui::EndDisabled();
            if (!compact_controls_ && !state.attach_available)
                ImGui::TextDisabled("Load a supported replay to choose a camera target.");
        }
        end_panel_card();
        if (begin_panel_card("##attach-framing")) {
            section_title("Frame the camera", "Offsets");
            ImGui::BeginDisabled(!state.attach_available);
            EditorCameraList aim_list{};
            const bool aim_list_ok = editor_camera_list(aim_list) &&
                                     aim_list.total == state.camera_count &&
                                     state.selected_camera < aim_list.total;
            const bool aim_disabled = !state.attach_selected || state.attach_point == 0 ||
                                 !state.paused || state.playing || state.attach_preview ||
                                 state.bone_picker || recording_locked || !aim_list_ok;
            ImGui::BeginDisabled(aim_disabled);
            bool aim_lock = state.attach_lock_vertical_aim;
            if (compact_checkbox("Lock vertical aim", &aim_lock, panel_scale) && !aim_disabled) {
                CameraPose request{};
                request[0] = double(aim_list.revision);
                request[1] = double(state.selected_camera);
                editor_enqueue(EditorAction::SetAttachVerticalAimLock, aim_lock ? 1 : 0, &request);
            }
            ImGui::EndDisabled();
            if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
                ImGui::SetTooltip("Bone/weapon follows the player without tilting with aim. "
                                  "Detach, change this setting, then Snap again to keep your view.");
            {
                // Slider rows commit at the end of an edit, not per frame.
                static float attach_draft[6]{};
                static bool attach_editing = false;
                if (!attach_editing) {
                    for (int index = 0; index < 6; ++index)
                        attach_draft[index] = float(state.attach_offsets[index]);
                }
                static const char* const kAttachAxis[6] = {"Forward", "L/R", "Up",
                                                           "Pitch",   "Yaw", "Roll"};
                bool attach_commit = false, attach_active = false;
                for (int index = 0; index < 4; ++index) {
                    char id[32]{};
                    std::snprintf(id, sizeof(id), "##attach-offset-%d", index);
                    const bool position = index < 3;
                    // Snap may author distances much larger than the
                    // normal fine-adjustment range. Keep those values
                    // visible on the slider; exact input can use the
                    // full range accepted by the attach action.
                    const float position_range = position
                        ? std::min(float(kAttachOffsetLimit),
                                   std::max(10.0f, std::abs(float(state.attach_offsets[index])) + 10.0f))
                        : 180.0f;
                    const SliderRow row =
                        slider_row(id, kAttachAxis[index], &attach_draft[index],
                                   -position_range, position_range,
                                   position ? "%.2f" : "%.1f", 74.0f,
                                   nullptr, false, position);
                    if (position)
                        attach_draft[index] = attach_offset_entry(
                            attach_draft[index], state.attach_offsets[index]);
                    attach_active = attach_active || row.active;
                    attach_commit = attach_commit || row.committed;
                }
                if (inline_controls("Rotation & transition")) {
                    for (int index = 4; index < 6; ++index) {
                        char id[32]{};
                        std::snprintf(id, sizeof(id), "##attach-offset-%d", index);
                        const auto row =
                            slider_row(id, kAttachAxis[index], &attach_draft[index], -180,
                                       180, "%.1f", 74.0f);
                        attach_active |= row.active;
                        attach_commit |= row.committed;
                    }
                    static float source_blend = 0;
                    static bool blend_editing = false;
                    if (!blend_editing)
                        source_blend = float(state.source_blend);
                    {
                    const auto blend_row = slider_row("##source-blend", "Blend in",
                                                      &source_blend, 0.0f, 10.0f, "%.2f s");
                    blend_editing = blend_row.active;
                    if (blend_row.committed)
                        editor_enqueue(EditorAction::SetSourceBlend, source_blend);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "0 keeps a cut. An eased blend finishes at this view's arrival time, starting no earlier than the previous view. Try 0.5 seconds.");
                    }
                }
                attach_editing = attach_active;
                if (attach_commit) {
                    CameraPose offsets{};
                    for (int index = 0; index < 6; ++index)
                        offsets[index] = attach_draft[index];
                    editor_enqueue(EditorAction::SetAttachOffsets, 0, &offsets);
                }
                static float attach_smoothing = 0.0f;
                static bool smoothing_editing = false;
                if (!smoothing_editing)
                    attach_smoothing =
                        std::clamp(float(state.attach_smoothing), 0.0f, 5.0f);
                {
                const SliderRow smoothing_row =
                    slider_row("##attach-smoothing", "Smooth", &attach_smoothing, 0.0f,
                               1.0f, "%.2f s");
                smoothing_editing = smoothing_row.active;
                if (smoothing_row.committed)
                    editor_enqueue(EditorAction::SetAttachSmoothing,
                                   double(attach_smoothing));
                }
                if (!compact_controls_) ImGui::TextDisabled("Drag to adjust; Ctrl+click a slider to type.");
            }
            if (inline_controls("Model visibility")) {
                bool hide = state.attach_hide;
                if (compact_checkbox("Hide this hero", &hide, panel_scale))
                    editor_enqueue(EditorAction::SetAttachHide,
                                   (hide ? 1 : 0) | (state.attach_auto_clearance ? 2 : 0));
                if (ImGui::IsItemHovered())
                    ImGui::SetTooltip(
                        "Hides this hero's identified body draws in preview and recordings.");
                ImGui::TextDisabled("Visible-model spacing");
                ImGui::SetNextItemWidth(-1);
                if (ImGui::BeginCombo("##attach-clearance", state.attach_auto_clearance
                                                                ? "Automatic clearance"
                                                                : "Exact offset")) {
                    if (ImGui::Selectable("Automatic clearance (experimental)",
                                          state.attach_auto_clearance))
                        editor_enqueue(EditorAction::SetAttachHide,
                                       (state.attach_hide ? 1 : 0) | 2);
                    if (ImGui::Selectable("Exact offset", !state.attach_auto_clearance))
                        editor_enqueue(EditorAction::SetAttachHide, state.attach_hide ? 1 : 0);
                    ImGui::EndCombo();
                }
                if (compact_controls_ && ImGui::IsItemHovered())
                    ImGui::SetTooltip(state.attach_auto_clearance ?
                        "Automatic clearance keeps the camera near the selected bone; animated arms or clothing may cross the view." :
                        "Exact offset can let the visible hero cross or block the camera.");
                if (!compact_controls_ && state.attach_auto_clearance)
                    ImGui::TextWrapped(
                        "Head POV stays close and slightly forward; other bones use a wider spacing guide. Animated arms and clothing can still cross the view.");
                else if (!compact_controls_ && !state.attach_hide)
                    ImGui::TextWrapped(
                        "Exact offset: the visible hero may cross or block the camera during motion.");
            }
            ImGui::EndDisabled();
        }
        end_panel_card();
        ImGui::EndDisabled();
    }
    if (lite_page_ == 3) {
        ImGui::BeginDisabled(!state.ready || state.busy);
        if (begin_panel_card("##dof-card")) {
            section_title("Depth of field", "Dolly lens");
            ImGui::BeginDisabled(!state.dof_available || !state.paused || state.playing ||
                                 !state.camera_count);
            bool enabled = state.dof[0] != 0 && state.dof[1] != 0 &&
                           std::any_of(state.dof.begin() + 2, state.dof.begin() + 6,
                                       [](double value) { return value != 0; });
            if (compact_checkbox("Enable DOF", &enabled, panel_scale))
                editor_enqueue(EditorAction::SetDofEnabled, enabled ? 1 : 0);
            static std::array<double, 11> dof_draft{};
            static std::array<bool, 11> dof_editing{};
            auto field = [&](unsigned index, const char* label, float step,
                             double defaults) {
                if (!dof_editing[index])
                    dof_draft[index] = state.dof[index];
                ImGui::PushID(int(index));
                const char* display=label;
                if (ImGui::GetContentRegionAvail().x<ImGui::CalcTextSize(label).x) {
                    display=(index==2 || index==6) ? "N. blurry" : (index==4 || index==8) ? "F. crisp" :
                            (index==3 || index==7) ? "N. crisp" : (index==5 || index==9) ? "F. blurry" : "Tilt";
                }
                ImGui::TextDisabled("%s", display);
                if (display!=label && ImGui::IsItemHovered()) ImGui::SetTooltip("%s",label);
                ImGui::SetNextItemWidth(-1);
                ImGui::DragScalar("##value", ImGuiDataType_Double, &dof_draft[index], step,
                                  nullptr, nullptr, "%.2f");
                bool committed = ImGui::IsItemDeactivatedAfterEdit();
                dof_editing[index] = ImGui::IsItemActive();
                if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                    dof_draft[index] = defaults;
                    dof_editing[index] = false;
                    committed = true;
                }
                if (ImGui::IsItemHovered())
                    ImGui::SetTooltip(
                        "Drag to adjust. Alt: 100x finer; Shift: 10x faster. Ctrl+click to type an exact value. Right-click to reset to Dolly's default. Applies when released.");
                if (committed)
                    editor_enqueue(
                        EditorAction(unsigned(EditorAction::SetDofEnabled) + index),
                        dof_draft[index]);
                ImGui::PopID();
            };
            ImGui::BeginDisabled(!enabled);
            ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,
                                ImVec2((compact_controls_ ? 2.f : 6.f) * panel_scale, (compact_controls_ ? 2.f : 3.f) * panel_scale * card_vertical_scale_));
            ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing,
                                ImVec2(10 * panel_scale, (compact_controls_ ? 2.f : 4.f) * panel_scale * card_vertical_scale_));
            if (!compact_controls_) ImGui::TextDisabled("Focus ranges");
            if (ImGui::BeginTable("##focus-ranges", 2, ImGuiTableFlags_SizingStretchSame)) {
                ImGui::TableNextColumn();
                field(2, "Near blurry", 1, -100);
                ImGui::TableNextColumn();
                field(4, "Far crisp", 1, 180);
                ImGui::TableNextColumn();
                field(3, "Near crisp", 1, 0);
                ImGui::TableNextColumn();
                field(5, "Far blurry", 1, 2000);
                ImGui::TableNextColumn();
                field(10, "Ground tilt", .01f, .5);
                ImGui::EndTable();
            }
            if (inline_controls("Individual focus overrides","Focus overrides")) {
                if (ImGui::BeginTable("##individual-focus", 2,
                                      ImGuiTableFlags_SizingStretchSame)) {
                    ImGui::TableNextColumn();
                    field(6, "Near blurry", 1, -100);
                    ImGui::TableNextColumn();
                    field(8, "Far crisp", 1, 180);
                    ImGui::TableNextColumn();
                    field(7, "Near crisp", 1, 0);
                    ImGui::TableNextColumn();
                    field(9, "Far blurry", 1, 2000);
                    ImGui::EndTable();
                }
            }
            ImGui::PopStyleVar(2);
            ImGui::EndDisabled();
            ImGui::EndDisabled();
            if (!compact_controls_ && !state.camera_count)
                ImGui::TextWrapped("Capture a camera to author DOF settings.");
            else if (!compact_controls_)
                ImGui::TextWrapped(
                    "Alt: fine adjust | Ctrl+click: type.\nChanges save to Effects at the playhead.");
        }
        end_panel_card();
        if (begin_panel_card("##citadel-dof-card")) {
            section_title("Citadel DOF", "Game engine");
            ImGui::BeginDisabled(!state.citadel_dof_available || !state.paused ||
                                 state.playing || !state.camera_count);
            bool citadel_enabled = state.citadel_dof_enabled;
            if (compact_checkbox("Enable DOF", &citadel_enabled, panel_scale))
                editor_enqueue(EditorAction::SetCitadelDofEnabled, citadel_enabled ? 1 : 0);
            ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,
                                ImVec2(6 * panel_scale, 3 * panel_scale * card_vertical_scale_));
            ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing,
                                ImVec2(10 * panel_scale, 4 * panel_scale * card_vertical_scale_));
            static float sensor_draft = 0;
            static bool sensor_editing = false;
            if (!sensor_editing)
                sensor_draft = float(state.citadel_dof_sensor);
            ImGui::TextDisabled("Sensor size");
            if (compact_controls_ && layout_columns_ < 3) ImGui::SameLine(0,8*panel_scale);
            ImGui::SetNextItemWidth(-1);
            ImGui::SliderFloat("##citadel-sensor", &sensor_draft, .5f, 3.0f, "%.2f",
                               ImGuiSliderFlags_AlwaysClamp);
            bool sensor_committed = ImGui::IsItemDeactivatedAfterEdit();
            sensor_editing = ImGui::IsItemActive();
            if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                sensor_draft = 1.0f;
                sensor_editing = false;
                sensor_committed = true;
            }
            if (sensor_committed)
                editor_enqueue(EditorAction::SetCitadelDofSensorSize, double(sensor_draft));
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Lens sensor size in inches, 0.5 to 3.0. Right-click to reset to 1.");
            constexpr double kFocusMaximum = 10000.0;
            const auto focus_to_slider = [kFocusMaximum](double value) {
                return float(std::log(1.0 + std::max(0.0, value)) /
                             std::log(1.0 + kFocusMaximum));
            };
            const auto slider_to_focus = [kFocusMaximum](double value) {
                return std::exp(std::clamp(value, 0.0, 1.0) *
                                std::log(1.0 + kFocusMaximum)) -
                       1.0;
            };
            static float focus_draft = 0;
            static bool focus_editing = false;
            if (!focus_editing)
                focus_draft = focus_to_slider(state.citadel_dof_focus);
            char focus_label[64]{};
            std::snprintf(focus_label, sizeof(focus_label), "Focus distance · %.0f in",
                          slider_to_focus(focus_draft));
            ImGui::TextDisabled("%s", focus_label);
            if (compact_controls_ && layout_columns_ < 3) ImGui::SameLine(0,8*panel_scale);
            ImGui::SetNextItemWidth(-1);
            ImGui::SliderFloat("##citadel-focus", &focus_draft, 0.0f, 1.0f, "",
                               ImGuiSliderFlags_AlwaysClamp);
            const bool focus_committed = ImGui::IsItemDeactivatedAfterEdit();
            focus_editing = ImGui::IsItemActive();
            if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                focus_draft = focus_to_slider(200.0);
                focus_editing = false;
                editor_enqueue(EditorAction::SetCitadelDofFocusDistance, 200.0);
            } else if (focus_committed)
                editor_enqueue(EditorAction::SetCitadelDofFocusDistance,
                               slider_to_focus(focus_draft));
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Focus distance in inches, 0-10000 on a log scale. Right-click to reset to 200.");
            ImGui::PopStyleVar(2);
            ImGui::EndDisabled();
            if (!compact_controls_ && !state.camera_count)
                ImGui::TextWrapped("Capture a camera to author DOF settings.");
            else if (!compact_controls_)
                ImGui::TextWrapped("Changes save to Effects at the playhead.");
        }
        end_panel_card();
        if (begin_panel_card("##appearance-card")) {
            section_title("Scene appearance", "Replay view");
            ImGui::BeginDisabled(state.playing);
            action_button("Clear stuck effects", EditorAction::DestroyRagdolls,
                          ImGui::GetContentRegionAvail().x);
            ImGui::EndDisabled();
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Clear ragdolls, decals, screen post-processing and Violet ink left "
                    "behind after seeking or repeated shot playback.");
            ImGui::BeginDisabled(state.playing);
            bool hide_glow = (state.scene_appearance_flags & 1) != 0;
            if (compact_checkbox("Hide Citadel glow", &hide_glow, panel_scale))
                editor_enqueue(EditorAction::ToggleCitadelGlow);
            ImGui::EndDisabled();
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Toggle hero, trooper, boss and health-bar glow together.");
            ImGui::BeginDisabled(state.playing);
            bool hide_bars = (state.scene_appearance_flags & 2) != 0;
            if (compact_checkbox("Hide floating health bars", &hide_bars, panel_scale))
                editor_enqueue(EditorAction::ToggleHealthbars);
            ImGui::EndDisabled();
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Hide or restore floating unit bars. Replay HUD controls the hero health panel. Bar glow stays with Toggle Citadel glow.");
            ImGui::BeginDisabled(state.playing);
            bool opacity_fix = (state.scene_appearance_flags & 4) != 0;
            if (compact_checkbox("Near player opacity fix", &opacity_fix, panel_scale))
                editor_enqueue(EditorAction::NearPlayerOpacityFix);
            ImGui::EndDisabled();
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Force full opacity on the near-player camera fades used while editing.");
        }
        end_panel_card();
        if (begin_panel_card("##confetti-card")) {
            section_title("Particles", "Native effect");
            bool enabled = state.confetti_enabled;
            if (compact_checkbox("Enable particles", &enabled, panel_scale))
                editor_enqueue(EditorAction::SetConfettiEnabled, enabled ? 1 : 0);
            static float height_draft = 250.0f;
            static bool height_editing = false;
            if (!height_editing)
                height_draft = float(state.confetti_spawn_height);
            if (layout_columns_ >= 3) ImGui::TextDisabled("Spawn height");
            const auto height_row =
                slider_row("##confetti-height", layout_columns_ >= 3 ? "" : "Spawn height", &height_draft, 100.0f,
                           1500.0f, "%.0f", layout_columns_ >= 3 ? 0.f : 92.f);
            height_editing = height_row.active;
            if (height_row.committed)
                editor_enqueue(EditorAction::SetConfettiSpawnHeight,
                               double(std::round(height_draft)));
            bool despawn = state.confetti_despawn_on_ground;
            if (compact_checkbox("Despawn on ground", &despawn, panel_scale))
                editor_enqueue(EditorAction::SetConfettiDespawnOnGround, despawn ? 1 : 0);
            ImGui::PushStyleColor(ImGuiCol_Text,
                                  ImGui::GetStyleColorVec4(ImGuiCol_TextDisabled));
            ImGui::TextWrapped(
                "Preset is chosen in Dolly; it follows the rendered camera and game time.");
            ImGui::PopStyleColor();
        }
        end_panel_card();
        ImGui::EndDisabled();
    }
    if (lite_page_ == 4) {
        // Standardizing RTT for these settings
        struct Pending {
            double value = 0;
            std::uint64_t until = 0;
        };
        static std::array<Pending, 15> pending;
        const auto now_ms = GetTickCount64();
        const auto shown = [&](int id, double current) {
            auto& edit = pending[id];
            if (edit.value == current || now_ms >= edit.until)
                edit.until = 0;
            else if (edit.until)
                return edit.value;
            return current;
        };
        const auto commit = [&](int id, double value, EditorAction action) {
            if (editor_enqueue(action, value))
                pending[id] = {value, now_ms + 1000};
        };

        if (begin_panel_card("##export-camera")) {
            section_title("Camera source", "Export");
            const auto capture_state = export_recording.state;
            ImGui::BeginDisabled(state.busy || state.playing ||
                                 capture_state == video::State::starting ||
                                 capture_state == video::State::recording ||
                                 capture_state == video::State::finalizing);
            int source = int(shown(12, state.video_pov ? 1.0 : 0.0));
            ImGui::TextUnformatted("Source");
            ImGui::SetNextItemWidth(-1);
            if (ImGui::Combo("##export-source", &source, "Camera path / Bone camera\0Player POV\0"))
                commit(12, double(source), EditorAction::SetVideoSource);
            const bool pov = source != 0;
            const bool single_attach = state.camera_count == 1 && state.attach_selected;
            if (pov || single_attach) {
                const double duration = shown(13, state.pov_duration);
                char duration_label[32];
                std::snprintf(duration_label, sizeof(duration_label), "%.3g seconds",
                              duration);
                ImGui::TextUnformatted("Replay duration");
                ImGui::SetNextItemWidth(-1);
                if (ImGui::BeginCombo("##export-duration", duration_label, ImGuiComboFlags_HeightLargest)) {
                    for (const double seconds : {1., 2., 5., 10., 15., 30., 60., 120.}) {
                        char label[32];
                        std::snprintf(label, sizeof(label), "%.0f seconds", seconds);
                        if (ImGui::Selectable(label, seconds == duration))
                            commit(13, seconds, EditorAction::SetPovDuration);
                    }
                    ImGui::EndCombo();
                }
                if (!compact_controls_ && single_attach && !pov)
                    ImGui::TextWrapped(
                        "Record uses this duration for the bone camera take. Your saved camera, offsets and smoothing are preserved.");
                else if (!compact_controls_)
                    ImGui::TextWrapped(
                        "Select a hero with Game Follow or F9, then pause at the start. F8: return here. Record POV hides the HUD and stops after this segment.");
            }
            // Below the replay duration: camera path and Player POV takes both record their camera.
            bool ae_camera = shown(11, state.video_ae_camera ? 1.0 : 0.0) != 0.0;
            if (ImGui::Checkbox("Export After Effects camera (.jsx)", &ae_camera))
                commit(11, ae_camera ? 1.0 : 0.0, EditorAction::SetVideoAeCamera);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Save the recorded camera as an After Effects script beside the finished video.");
            bool usd_camera = shown(14, state.video_usd_camera ? 1.0 : 0.0) != 0.0;
            if (ImGui::Checkbox("Export USD camera (.usda)", &usd_camera))
                commit(14, usd_camera ? 1.0 : 0.0, EditorAction::SetVideoUsdCamera);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Save the recorded camera as a USD layer beside the finished video, for Blender, Davinci Resolve and other 3D apps.");
            ImGui::EndDisabled();
            if (!pov && !state.camera_count) {
                ImGui::TextWrapped("Capture at least two camera views to record a shot.");
            } else if (!compact_controls_ && !pov && state.attach_selected) {
                EditorRoster roster{};
                char target[112] = "Saved player";
                if (editor_roster_snapshot(roster) &&
                    state.attach_target_index < roster.count)
                    roster_label(roster.players[state.attach_target_index], target,
                                 sizeof(target));
                ImGui::TextWrapped("View %u: Attach - %s", state.selected_camera + 1,
                                   target);
                if (state.attach_point == 2)
                    ImGui::TextWrapped("Bone: %s", state.attach_bone);
                else
                    ImGui::TextUnformatted(state.attach_point == 1 ? "Point: Weapon"
                                                                   : "Point: Eyes");
                ImGui::TextDisabled("%u of %u views use attachment", state.attach_keys,
                                    state.shot_keys);
            } else if (!compact_controls_ && !pov) {
                ImGui::TextWrapped("View %u: Free path", state.selected_camera + 1);
            }
            if (!compact_controls_ && !pov)
                ImGui::TextWrapped(
                    "Export uses saved sources and offsets, including edits made while previewing.");
        }
        end_panel_card();
        if (begin_panel_card("##export-settings")) {
            char bitrate[64]{};
            std::snprintf(bitrate, sizeof(bitrate), "%u Mbps", state.video_bitrate_mbps);
            section_title("Capture", bitrate);
            {
            ImGui::TextUnformatted("Video FPS");
            if (compact_controls_ && layout_columns_ < 3) ImGui::SameLine(110*panel_scale);
            ImGui::SetNextItemWidth(-1);
            const unsigned shown_fps = unsigned(shown(1, state.video_fps));
            char fps_label[16]{};
            std::snprintf(fps_label, sizeof(fps_label), "%u", shown_fps);
            if (ImGui::BeginCombo("##export-fps", fps_label, ImGuiComboFlags_HeightLargest)) {
                for (unsigned value : {30u, 60u, 120u, 300u, 600u}) {
                    char label[16]{};
                    std::snprintf(label, sizeof(label), "%u", value);
                    if (ImGui::Selectable(label, value == shown_fps))
                        commit(1, double(value), EditorAction::SetVideoFps);
                }
                ImGui::EndCombo();
            }
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "30-120 record in real time; 300 and 600 need Fixed-step export.");
            }
            if (!compact_controls_) ImGui::Spacing();
            if (inline_controls("Encoder & quality")) {
                ImGui::TextUnformatted("Bitrate");
                if (compact_controls_ && layout_columns_ < 3) ImGui::SameLine(110*panel_scale);
                ImGui::SetNextItemWidth(-1);
                const unsigned shown_bitrate = unsigned(shown(2, state.video_bitrate_mbps));
                char bitrate_label[24]{};
                std::snprintf(bitrate_label, sizeof(bitrate_label), "%u Mbps",
                              shown_bitrate);
                if (ImGui::BeginCombo("##export-bitrate", bitrate_label, ImGuiComboFlags_HeightLargest)) {
                    for (unsigned value : {10u, 20u, 40u}) {
                        char label[24]{};
                        std::snprintf(label, sizeof(label), "%u Mbps", value);
                        if (ImGui::Selectable(label, value == shown_bitrate))
                            commit(2, double(value), EditorAction::SetVideoBitrate);
                    }
                    ImGui::EndCombo();
                }
                ImGui::TextUnformatted("Encoder");
                if (compact_controls_ && layout_columns_ < 3) ImGui::SameLine(110*panel_scale);
                ImGui::SetNextItemWidth(-1);
                const unsigned shown_codec = unsigned(shown(3, state.video_codec));
                static constexpr const char* encoder_previews[]={"Auto (hardware)","NVIDIA H.264","NVIDIA HEVC","Windows H.264","CPU H.264","CPU HEVC","Intel H.264","Intel HEVC","AMD H.264","AMD HEVC","Lossless FFV1"};
                const char* encoder_preview=encoder_previews[shown_codec<=10 ? shown_codec : 0];
                if (ImGui::BeginCombo("##export-encoder", encoder_preview, ImGuiComboFlags_HeightLargest)) {
                    for (std::uint32_t id = 0; id <= 10; ++id) {
                        if (ImGui::Selectable(video_codec_label(id), id == shown_codec))
                            commit(3, double(id), EditorAction::SetVideoEncoder);
                    }
                    ImGui::EndCombo();
                }
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s",video_codec_label(shown_codec));
            }
            {
            if (!compact_controls_) ImGui::Spacing();
            bool fixed_step = shown(4, state.video_fixed_step ? 1.0 : 0.0) != 0.0;
            if (ImGui::Checkbox("Fixed-step export", &fixed_step))
                commit(4, fixed_step ? 1.0 : 0.0, EditorAction::SetVideoFixedStep);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Render exactly one frame per output frame. Required for 300/600 FPS and removes real-time encoder hitching.");
            if (!compact_controls_) ImGui::Spacing();
            ImGui::TextUnformatted("Export speed");
            if (compact_controls_ && layout_columns_ < 3) ImGui::SameLine(110*panel_scale);
            ImGui::SetNextItemWidth(-1);
            const double shown_speed = shown(6, state.video_speed);
            char export_speed[32]{};
            std::snprintf(export_speed, sizeof(export_speed), "%.3g x", shown_speed);
            if (ImGui::BeginCombo("##export-speed", export_speed, ImGuiComboFlags_HeightLargest)) {
                for (double value : {.05, .1, .25, .5, 1.0, 2.0, 4.0}) {
                    char label[32]{};
                    std::snprintf(label, sizeof(label), "%.3g x", value);
                    if (ImGui::Selectable(label, value == shown_speed))
                        commit(6, value, EditorAction::SetVideoSpeed);
                }
                ImGui::EndCombo();
            }
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Slow-motion playback rate for fixed-step export. Shared with the desktop Export tab.");
            }
        }
        end_panel_card();
        if (begin_panel_card("##export-passes")) {
            section_title("Capture layers", "Color included");
            bool depth_master = shown(5, state.video_depth ? 1.0 : 0.0) != 0.0;
            if (ImGui::Checkbox("Depth master (.mov)", &depth_master))
                commit(5, depth_master ? 1.0 : 0.0, EditorAction::SetVideoDepth);
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip(
                    "Write a matching ProRes depth.mov and preview video in the take's depth folder. Requires a verified scene depth.");
            bool depth_exr = shown(7, state.video_depth_exr ? 1.0 : 0.0) != 0.0;
            // The EXR sequence requires the depth master; the desktop
            // gates it the same way, so the dependency stays visible
            // instead of silently dropping a tick.
            ImGui::BeginDisabled(!state.video_depth);
            if (ImGui::Checkbox("EXR sequence (float)", &depth_exr))
                commit(7, depth_exr ? 1.0 : 0.0, EditorAction::SetVideoDepthExr);
            ImGui::EndDisabled();
            if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
                ImGui::SetTooltip(
                    "Also write the float EXR precision master under the depth folder's exr/ subfolder. Requires the depth master.");
            bool layer_world = shown(8, state.video_layer_world ? 1.0 : 0.0) != 0.0;
            if (ImGui::Checkbox("World layer", &layer_world))
                commit(8, layer_world ? 1.0 : 0.0, EditorAction::SetVideoLayerWorld);
            bool layer_players = shown(9, state.video_layer_players ? 1.0 : 0.0) != 0.0;
            if (ImGui::Checkbox("Players layer", &layer_players))
                commit(9, layer_players ? 1.0 : 0.0, EditorAction::SetVideoLayerPlayers);
            bool layer_effects = shown(10, state.video_layer_effects ? 1.0 : 0.0) != 0.0;
            if (ImGui::Checkbox("Effects layer", &layer_effects))
                commit(10, layer_effects ? 1.0 : 0.0, EditorAction::SetVideoLayerEffects);
        }
        end_panel_card();
        if (begin_panel_card("##video-card")) {
            section_title("Preview and stills", "Video");
            const auto& recording = export_recording;
            const bool active = recording.state == video::State::starting ||
                                recording.state == video::State::recording;
            ImGui::BeginDisabled(!state.ready || state.busy);
            ImGui::BeginDisabled(active || recording.state == video::State::finalizing ||
                                 state.camera_count < 2 || state.playing ||
                                 state.video_pov);
            action_button("Play shot", EditorAction::PlayPath,
                          ImGui::GetContentRegionAvail().x);
            ImGui::EndDisabled();
            if (active) {
                ImGui::Text("%.1f s  |  %llu frames",
                            double(recording.duration_100ns) / 1e7,
                            static_cast<unsigned long long>(recording.frames_written));
            } else {
                ImGui::BeginDisabled(recording.state == video::State::finalizing ||
                                     state.playing || state.video_pov);
                action_button("Screenshot (hero + plate)", EditorAction::TakeScreenshot,
                              ImGui::GetContentRegionAvail().x);
                ImGui::EndDisabled();
                if (ImGui::IsItemHovered())
                    ImGui::SetTooltip("High-res still of the current paused view: plate, "
                                      "players matte and depth. Desktop Export tab.");
            }
            ImGui::EndDisabled();
            if (recording.frames_dropped)
                ImGui::Text("Missed capture slots: %llu",
                            static_cast<unsigned long long>(recording.frames_dropped));
            ImGui::PushStyleColor(ImGuiCol_Text, ImGui::GetStyleColorVec4(ImGuiCol_TextDisabled));
            ImGui::TextWrapped("Output folder and FFmpeg runtime: desktop Export tab.");
            ImGui::PopStyleColor();
        }
        end_panel_card();
    }
    finish_section_layout();
    const float bottom = *std::max_element(card_column_y_, card_column_y_ + layout_columns_) - 12 * panel_scale;
    ImGui::SetCursorPosY(bottom);
    ImGui::Dummy(ImVec2(0, 0));
}

void OverlayPanel::draw_controls_footer(const EditorSnapshot& state) {
    const auto export_recording = editor_recording_status();
    const bool recording_locked = editor_recording_locks_cameras(export_recording.state);
    const float stop_cap = key_cap_width(EditorAction::Stop, panel_scale);
    action_button("Stop / restore", EditorAction::Stop,
                  std::min(132 * panel_scale, ImGui::GetContentRegionAvail().x -
                      (stop_cap > 0 ? stop_cap + ImGui::GetStyle().ItemSpacing.x : 0)));
    if (stop_cap > 0) {
        ImGui::SameLine();
        ImGui::AlignTextToFramePadding();
        key_cap(EditorAction::Stop, panel_scale, rail_drawing_);
    }
    if (lite_page_ == 4 || recording_locked) {
        ImGui::SameLine();
        const auto& recording = export_recording;
        const bool active = recording.state == video::State::starting ||
                            recording.state == video::State::recording;
        ImGui::BeginDisabled(state.busy || (!active && !state.ready));
        if (active) {
            action_button("Finish recording", EditorAction::StopVideo,
                          150 * panel_scale, 0, true);
        } else if (recording.state == video::State::finalizing) {
            ImGui::BeginDisabled();
            ImGui::Button("Finalizing video...", ImVec2(150 * panel_scale, 0));
            ImGui::EndDisabled();
        } else {
            action_button(state.video_pov ? "Record POV" : "Record video",
                          EditorAction::StartVideo, 150 * panel_scale, 0, true);
        }
        ImGui::EndDisabled();
    }
    if (lite_page_ == 3) {
        ImGui::SameLine();
        if (reshade_available()) {
            if (ImGui::SmallButton("ReShade menu")) editor_enqueue(EditorAction::ReShade);
        } else {
            ImGui::AlignTextToFramePadding();
            ImGui::TextDisabled("ReShade not set");
            if (ImGui::IsItemHovered())
                ImGui::SetTooltip("Select the ReShade runtime in the desktop Settings to enable effects.");
        }
    }
    const float console_x=ImGui::GetWindowContentRegionMax().x-ImGui::CalcTextSize("F7  Console").x;
    ImGui::SameLine();
    const auto message_pos=ImGui::GetCursorScreenPos();
    const float message_width=console_x-ImGui::GetCursorPosX()-8*panel_scale;
    if (message_width>20*panel_scale) {
        ImGui::Dummy(ImVec2(message_width,ImGui::GetFrameHeight()));
        const ImVec2 text_pos(message_pos.x,message_pos.y+ImGui::GetStyle().FramePadding.y);
        ImGui::RenderTextEllipsis(ImGui::GetWindowDrawList(),text_pos,
            ImVec2(text_pos.x+message_width,text_pos.y+ImGui::GetTextLineHeight()),
            text_pos.x+message_width,text_pos.x+message_width,state.message,nullptr,nullptr);
        if (state.message[0] && ImGui::IsItemHovered()) ImGui::SetTooltip("%s",state.message);
    }
    ImGui::SameLine(console_x);
    ImGui::AlignTextToFramePadding();
    ImGui::TextDisabled("F7  Console");
    if (ImGui::IsItemHovered())
        ImGui::SetTooltip("Open Deadlock's console. Customize editor shortcuts in the launcher.");
}

} // namespace dolly
