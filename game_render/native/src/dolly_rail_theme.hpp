#pragma once
// Colour tokens: Figma variable collection "Bone" (VariableCollectionId:1:2), resolved in mode
// "Dolly Blue" ([design reference]) of file design source. Names mirror the Figma variable names.
#include "imgui.h"
#include "dolly_rail_tokens_generated.hpp"

namespace dolly::rail_theme {

constexpr ImVec4 Rgb(unsigned hex, float a = 1.0f) {
    return ImVec4(((hex >> 16) & 0xFF) / 255.0f, ((hex >> 8) & 0xFF) / 255.0f,
                  (hex & 0xFF) / 255.0f, a);
}

// bg/*
constexpr ImVec4 bg_window = Rgb(ui::rail::BG);
constexpr ImVec4 bg_surface = Rgb(ui::rail::PANEL);
constexpr ImVec4 bg_rail = Rgb(ui::rail::RAIL);
constexpr ImVec4 bg_rail_selected = Rgb(ui::rail::SELECTED);
constexpr ImVec4 bg_rail_hover = Rgb(ui::rail::RAIL_HOVER);
constexpr ImVec4 bg_field = Rgb(ui::rail::FIELD);
// text/*
constexpr ImVec4 text_primary = Rgb(ui::rail::TEXT);
constexpr ImVec4 text_muted = Rgb(ui::rail::MUTED);
constexpr ImVec4 text_dim = Rgb(ui::rail::DIM);
constexpr ImVec4 text_on_accent = Rgb(ui::rail::ACCENT_INK);
// border/*
constexpr ImVec4 border_default = Rgb(ui::rail::EDGE);
constexpr ImVec4 border_soft = Rgb(ui::rail::CARD_EDGE);
// accent/*
constexpr ImVec4 accent_default = Rgb(ui::rail::ACCENT);
constexpr ImVec4 accent_hover = Rgb(ui::rail::ACCENT_HOVER);
constexpr ImVec4 accent_pressed = Rgb(ui::rail::ACCENT_ACTIVE);
// button/*
constexpr ImVec4 button_default = Rgb(ui::rail::BUTTON);
constexpr ImVec4 button_hover = Rgb(ui::rail::BUTTON_HOVER);
constexpr ImVec4 button_pressed = Rgb(ui::rail::BUTTON_ACTIVE);
// status/*
constexpr ImVec4 status_ready = Rgb(ui::rail::STATUS_READY);
constexpr ImVec4 status_busy = Rgb(ui::rail::STATUS_BUSY);
constexpr ImVec4 status_error = Rgb(ui::rail::STATUS_ERROR);

// Raw (unbound) colours that appear in the Figma frames.
constexpr ImVec4 timeline_stroke = Rgb(ui::rail::CARD_EDGE_PINNED); // replay bar outline
constexpr ImVec4 camera_marker_selected =
    Rgb(ui::rail::CAMERA_MARKER_SELECTED); // selected camera tick on the replay bar

inline ImVec4 Mix(const ImVec4& a, const ImVec4& b, float t) {
    return ImVec4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t,
                  a.w + (b.w - a.w) * t);
}

// Packs a token for ImDrawList, applying the current ImGui style alpha (used for content fades).
inline ImU32 U32(const ImVec4& c) {
    return ImGui::GetColorU32(c);
}

} // namespace theme
