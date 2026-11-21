#include "dolly_rail_fields.hpp"
#include "imgui_internal.h"
#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>
namespace dolly::rail_fields {
namespace kit = rail_kit;
namespace theme = rail_theme;
namespace fonts = rail_fonts;
namespace {
bool SafeButtonBehavior(const ImRect& rect, ImGuiID id, bool* hovered, bool* held,
                        ImGuiButtonFlags flags = 0) {
    if (GImGui->CurrentItemFlags & ImGuiItemFlags_Disabled) {
        *hovered = *held = false;
        if (GImGui->ActiveId == id)
            ImGui::ClearActiveID();
        return false;
    }
    return ImGui::ButtonBehavior(rect, id, hovered, held, flags);
}

float S() {
    return kit::Scale();
}
ImU32 C(const ImVec4& c) {
    return theme::U32(c);
}

// Polyline in design px from `o` (screen px) with round caps and joins (the kit's chevrons).
void Stroke(ImDrawList* dl, ImVec2 o, std::initializer_list<ImVec2> pts, float th, ImU32 col) {
    const float s = S();
    dl->PathClear();
    for (const ImVec2& p : pts)
        dl->PathLineTo(ImVec2(o.x + p.x * s, o.y + p.y * s));
    dl->PathStroke(col, 0, th * s);
    for (const ImVec2& p : pts)
        dl->AddCircleFilled(ImVec2(o.x + p.x * s, o.y + p.y * s), th * s * 0.5f, col, 12);
}

struct AlphaScope { // the kit's own disabled look: 50 %
    bool on;
    explicit AlphaScope(bool dim) : on(dim) {
        if (on)
            ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
    }
    ~AlphaScope() {
        if (on)
            ImGui::PopStyleVar();
    }
};

// ---- value mapping of the sliders -------------------------------------------------------------------------------
float ToT(float v, float vmin, float vmax, Map m) {
    float t = 0.0f;
    switch (m) {
    case Map::Log:
        t = (vmin > 0.0f && vmax > vmin)
                ? std::log(std::max(v, vmin) / vmin) / std::log(vmax / vmin)
                : 0.0f;
        break;
    case Map::Log1p:
        t = vmax > 0.0f ? std::log1p(std::max(v, 0.0f)) / std::log1p(vmax) : 0.0f;
        break;
    default:
        t = vmax > vmin ? (v - vmin) / (vmax - vmin) : 0.0f;
        break;
    }
    return std::clamp(t, 0.0f, 1.0f);
}

float FromT(float t, float vmin, float vmax, Map m) {
    t = std::clamp(t, 0.0f, 1.0f);
    switch (m) {
    case Map::Log:
        return vmin * std::pow(vmax / vmin, t);
    case Map::Log1p:
        return std::expm1(t * std::log1p(vmax));
    default:
        return vmin + (vmax - vmin) * t;
    }
}

// ImGui sliders round a dragged value to the precision its format shows ("%.0f" -> whole numbers).
float RoundToFormat(float v, const char* fmt) {
    const char* dot = fmt ? strstr(fmt, "%.") : nullptr;
    if (!dot)
        return v;
    const int digits = std::clamp(dot[2] - '0', 0, 6);
    const double k = std::pow(10.0, digits);
    return (float)(std::round(v * k) / k);
}

// The inline number entry of a Ctrl+clicked slider / drag field: ImGui's own TempInput (the real overlay's sliders
// do the same), drawn in the Field look over `bb`.
void TypeIn(const ImRect& bb, ImGuiID id, const char* label, ImGuiDataType type, void* v,
            const char* fmt, const void* vmin, const void* vmax, float padX) {
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->AddRectFilled(bb.Min, bb.Max, C(theme::bg_field), 4.0f * S());
    dl->AddRect(bb.Min, bb.Max, C(theme::accent_default), 4.0f * S(), 0, 1.0f * S());
    ImFont* f = fonts::Get(kit::Role::BodyMedium);
    const float previous_scale = ImGui::GetCurrentWindow()->FontWindowScale;
    ImGui::SetWindowFontScale(fonts::PixelSize(kit::Role::BodyMedium) * S() / f->FontSize);
    ImGui::PushFont(f);
    ImGui::PushStyleVar(
        ImGuiStyleVar_FramePadding,
        ImVec2(
            padX * S(),
            std::floor((bb.GetHeight() - fonts::PixelSize(kit::Role::BodyMedium) * S()) * 0.5f)));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 4.0f * S());
    ImGui::PushStyleColor(ImGuiCol_FrameBg, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleColor(ImGuiCol_FrameBgHovered, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleColor(ImGuiCol_FrameBgActive, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleColor(ImGuiCol_Text, theme::text_primary);
    ImGui::PushStyleColor(ImGuiCol_TextSelectedBg, theme::accent_default);
    ImGui::TempInputScalar(bb, id, label, type, v, fmt, vmin, vmax);
    ImGui::PopStyleColor(5);
    ImGui::PopStyleVar(2);
    ImGui::PopFont();
    ImGui::SetWindowFontScale(previous_scale);
}

// Hover / press handling shared by the slider and the drag field. Returns true on a Ctrl+click (or a double-click
// when `doubleClickTypes`): the caller opens the number entry this same frame.
bool Press(const ImRect& r, ImGuiID id, bool disabled, bool doubleClickTypes, bool* rightClicked) {
    ImGuiContext& g = *GImGui;
    const bool hovered =
        ImGui::ItemHoverable(r, id, g.LastItemData.ItemFlags); // also claims hover when disabled
    *rightClicked = false;
    if (disabled || (g.CurrentItemFlags & ImGuiItemFlags_Disabled))
        return false;
    if (g.NavActivateId == id)
        return true;
    if (!hovered)
        return false;
    if (ImGui::IsMouseClicked(ImGuiMouseButton_Right)) {
        *rightClicked = true;
        return false;
    }
    if (!ImGui::IsMouseClicked(ImGuiMouseButton_Left))
        return false;
    if (g.IO.KeyCtrl || (doubleClickTypes && g.IO.MouseClickedCount[ImGuiMouseButton_Left] == 2))
        return true;
    ImGui::SetActiveID(id, ImGui::GetCurrentWindow());
    ImGui::SetFocusID(id, ImGui::GetCurrentWindow());
    ImGui::FocusWindow(ImGui::GetCurrentWindow());
    return false;
}

// Slider look of kit::Slider (Figma: 4 px track radius 2, accent fill up to a 12 px white thumb on whole px).
void DrawSlider(const ImRect& r, float t) {
    const float s = S();
    const float thumbX = r.Min.x + std::round(t * (r.GetWidth() / s - 12.0f)) * s;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    const ImVec2 a(r.Min.x, r.Min.y + 4.0f * s), b(r.Max.x, r.Min.y + 8.0f * s);
    dl->AddRectFilled(a, b, C(theme::button_default), 2.0f * s);
    if (thumbX + 6.0f * s > a.x)
        dl->AddRectFilled(a, ImVec2(thumbX + 6.0f * s, b.y), C(theme::accent_default), 2.0f * s,
                          ImDrawFlags_RoundCornersLeft);
    dl->AddCircleFilled(ImVec2(thumbX + 6.0f * s, r.Min.y + 6.0f * s), 6.0f * s,
                        C(theme::text_primary), 32);
}

// One slider at `r` (12 tall); the number entry opens over `typeBox`.
Edit SliderAt(const ImRect& r, const ImRect& typeBox, ImGuiID id, const char* label, float* v,
              float vmin, float vmax, const char* fmt, const SliderOpts& o) {
    ImGuiContext& g = *GImGui;
    Edit e;
    if (!ImGui::ItemAdd(r, id))
        return e;
    bool typing = ImGui::TempInputIsActive(id);
    bool right = false;
    if (!typing && Press(r, id, o.disabled, false, &right))
        typing = true;
    if (right && o.hasReset) {
        *v = o.reset;
        e.changed = e.released = true;
    }
    if (typing) {
        const float before = *v;
        TypeIn(typeBox, id, label, ImGuiDataType_Float, v, o.typeFmt ? o.typeFmt : fmt,
               o.unclamped_entry ? nullptr : &vmin, o.unclamped_entry ? nullptr : &vmax, 8.0f);
        e.active = true;
        e.changed = *v != before;
        if (ImGui::IsItemDeactivated()) {
            e.active = false;
            e.released = true;
        }
        return e;
    }
    if (g.ActiveId == id) {
        if (g.IO.MouseDown[ImGuiMouseButton_Left] && !o.disabled) {
            const float s = S();
            const float t =
                (g.IO.MousePos.x - r.Min.x - 6.0f * s) / std::max(1.0f, r.GetWidth() - 12.0f * s);
            const float nv =
                std::clamp(RoundToFormat(FromT(t, vmin, vmax, o.map), fmt), vmin, vmax);
            if (nv != *v) {
                *v = nv;
                e.changed = true;
            }
            e.active = true;
        } else {
            ImGui::ClearActiveID();
            e.released = true;
        }
    }
    AlphaScope dim(o.disabled);
    DrawSlider(r, ToT(*v, vmin, vmax, o.map));
    return e;
}

} // namespace
Greyed::Greyed(bool dim) : on(dim) {
    if (!on)
        return;
    ImGui::PushStyleVar(ImGuiStyleVar_DisabledAlpha, 0.45f);
    ImGui::BeginDisabled();
}

Greyed::~Greyed() {
    if (!on)
        return;
    ImGui::EndDisabled();
    ImGui::PopStyleVar();
}

Inert::Inert(bool disabled) : on(disabled) {
    if (!on)
        return;
    ImGui::PushStyleVar(ImGuiStyleVar_DisabledAlpha, 1.0f);
    ImGui::BeginDisabled();
}

Inert::~Inert() {
    if (!on)
        return;
    ImGui::EndDisabled();
    ImGui::PopStyleVar();
}

// ---- widgets ----------------------------------------------------------------------------------------------------
Edit Slider(const char* id, float* v, float vmin, float vmax, float w, const SliderOpts& o) {
    const ImRect r = kit::Place(w, 12.0f);
    const float s = S();
    const ImRect box(ImVec2(r.Min.x, r.Min.y - 5.0f * s), ImVec2(r.Max.x, r.Max.y + 5.0f * s));
    return SliderAt(r, box, ImGui::GetID(id), id, v, vmin, vmax, "%.2f", o);
}

Edit SliderRow(const char* label, float* v, float vmin, float vmax, const char* fmt,
               const SliderOpts& o) {
    kit::BeginRow(18.0f, 8.0f);
    kit::Text(label, o.disabled ? theme::text_dim : theme::text_muted);
    if (fmt) {
        char value[32];
        snprintf(value, sizeof(value), fmt, *v);
        const float vw = std::ceil(kit::TextWidth(value));
        kit::Spring(vw);
        kit::Text(value, theme::text_primary, kit::Role::Body, 18.0f, vw);
    }
    kit::EndRow();
    kit::VGap(10.0f);
    ImGui::PushID(label);
    const ImRect r = kit::Place(0.0f, 12.0f);
    const float s = S();
    const ImRect box(ImVec2(r.Min.x, r.Min.y - 5.0f * s), ImVec2(r.Max.x, r.Max.y + 5.0f * s));
    const Edit e = SliderAt(r, box, ImGui::GetID("##slider"), "##slider", v, vmin, vmax,
                            fmt ? fmt : "%.0f", o);
    ImGui::PopID();
    return e;
}

Edit SliderLine(const char* label, float* v, float vmin, float vmax, const char* fmt, float labelW,
                float indent, const SliderOpts& o) {
    char value[32];
    snprintf(value, sizeof(value), fmt, *v);
    if (!strncmp(value, "-0", 2) && std::atof(value) == 0.0)
        memmove(value, value + 1, strlen(value)); // no "-0.00"
    kit::BeginRow(18.0f, 10.0f);
    if (indent > 0.0f)
        kit::HGap(indent);
    kit::Text(label, o.disabled ? theme::text_dim : theme::text_muted, kit::Role::Body, 18.0f,
              labelW);
    ImGui::PushID(label);
    const ImRect r = kit::Place(275.0f, 12.0f);
    const float s = S();
    const ImRect box(ImVec2(r.Min.x, r.Min.y - 3.0f * s), ImVec2(r.Max.x, r.Max.y + 3.0f * s));
    const Edit e = SliderAt(r, box, ImGui::GetID("##slider"), "##slider", v, vmin, vmax, fmt, o);
    ImGui::PopID();
    kit::Text(value, o.disabled ? theme::text_dim : theme::text_primary, kit::Role::Body, 18.0f,
              44.0f, kit::Align::Right);
    kit::EndRow();
    return e;
}

Edit DragField(const char* idStr, double* v, double speed, double reset, float w, bool disabled) {
    ImGuiContext& g = *GImGui;
    Edit e;
    const ImRect r = kit::Place(w, 30.0f);
    const ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return e;
    bool typing = ImGui::TempInputIsActive(id);
    bool right = false;
    if (!typing && Press(r, id, disabled, true, &right))
        typing = true;
    if (right) {
        *v = reset;
        e.changed = e.released = true;
    }
    if (typing) {
        const double before = *v;
        TypeIn(r, id, idStr, ImGuiDataType_Double, v, "%.2f", nullptr, nullptr, 13.0f);
        e.active = true;
        e.changed = *v != before;
        if (ImGui::IsItemDeactivated()) {
            e.active = false;
            e.released = true;
        }
        return e;
    }
    // Drag: after the mouse moved past half the drag threshold, each screen px moves the value by `speed` (Alt
    // 100x finer, Shift 10x faster), rounded to the shown precision (ImGui DragScalar).
    auto* storage = ImGui::GetStateStorage();
    double s_start = storage->GetFloat(id ^ 0x51a1u, float(*v));
    double s_accum = storage->GetFloat(id ^ 0x51a2u, 0);
    if (g.ActiveId == id) {
        if (g.ActiveIdIsJustActivated) {
            s_start = *v;
            s_accum = 0.0;
        }
        if (g.IO.MouseDown[ImGuiMouseButton_Left] && !disabled) {
            if (ImGui::IsMouseDragPastThreshold(ImGuiMouseButton_Left,
                                                g.IO.MouseDragThreshold * 0.5f)) {
                double k = speed;
                if (g.IO.KeyAlt)
                    k *= 0.01;
                if (g.IO.KeyShift)
                    k *= 10.0;
                s_accum += g.IO.MouseDelta.x * k;
                const double nv = std::round((s_start + s_accum) * 100.0) / 100.0;
                if (nv != *v) {
                    *v = nv;
                    e.changed = true;
                }
            }
            e.active = true;
        } else {
            ImGui::ClearActiveID();
            e.released = *v != s_start;
        }
    }
    storage->SetFloat(id ^ 0x51a1u, float(s_start));
    storage->SetFloat(id ^ 0x51a2u, float(s_accum));
    AlphaScope dim(disabled);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->AddRectFilled(r.Min, r.Max, C(theme::bg_field), 4.0f * S());
    char text[32];
    snprintf(text, sizeof(text), "%.2f", *v);
    dl->PushClipRect(r.Min, r.Max, true);
    kit::PaintText(dl, kit::Role::BodyMedium, text,
                   ImVec2(r.Min.x + 13.0f * S(), r.Min.y + 6.0f * S()), 18.0f,
                   C(theme::text_muted));
    dl->PopClipRect();
    return e;
}

Edit DragCell(const char* idStr, const ImRect& r, float* v, float speed, float vmin, float vmax,
              const char* fmt, bool disabled) {
    ImGuiContext& g = *GImGui;
    Edit e;
    const ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return e;
    bool typing = ImGui::TempInputIsActive(id);
    bool right = false;
    if (!typing && Press(r, id, disabled, true, &right))
        typing = true;
    e.hovered = !disabled && g.HoveredId == id;
    if (typing) {
        const float before = *v;
        TypeIn(r, id, idStr, ImGuiDataType_Float, v, fmt, &vmin, &vmax, 4.0f);
        e.active = e.typing = true;
        e.changed = *v != before;
        if (ImGui::IsItemDeactivated()) {
            e.active = e.typing = false;
            e.released = true;
        }
        return e;
    }
    auto* storage = ImGui::GetStateStorage();
    float s_start = storage->GetFloat(id ^ 0x51a1u, *v),
          s_accum = storage->GetFloat(id ^ 0x51a2u, 0);
    if (g.ActiveId == id) {
        if (g.ActiveIdIsJustActivated) {
            s_start = *v;
            s_accum = 0.0f;
        }
        if (g.IO.MouseDown[ImGuiMouseButton_Left] && !disabled) {
            if (ImGui::IsMouseDragPastThreshold(ImGuiMouseButton_Left,
                                                g.IO.MouseDragThreshold * 0.5f)) {
                float k = speed;
                if (g.IO.KeyAlt)
                    k *= 0.01f;
                if (g.IO.KeyShift)
                    k *= 10.0f;
                s_accum += g.IO.MouseDelta.x * k;
                const float nv = std::clamp(RoundToFormat(s_start + s_accum, fmt), vmin, vmax);
                if (nv != *v) {
                    *v = nv;
                    e.changed = true;
                }
            }
            e.active = true;
        } else {
            ImGui::ClearActiveID();
            e.released = *v != s_start;
        }
    }
    storage->SetFloat(id ^ 0x51a1u, s_start);
    storage->SetFloat(id ^ 0x51a2u, s_accum);
    return e;
}

int Combo(const char* idStr, int current, const char* const* items, int count,
          const char* closedText, float w, bool disabled, const char* emptyText) {
    const ImRect r = kit::Place(w, 30.0f);
    const ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return -1;
    char popupId[64];
    snprintf(popupId, sizeof(popupId), "##dwc%08x", id);
    bool hov = false, held = false;
    if (!disabled && SafeButtonBehavior(r, id, &hov, &held) && !ImGui::IsPopupOpen(popupId))
        ImGui::OpenPopup(popupId);
    if (disabled)
        ImGui::ItemHoverable(r, id, GImGui->LastItemData.ItemFlags);
    const float s = S();
    {
        AlphaScope dim(disabled);
        ImDrawList* dl = ImGui::GetWindowDrawList();
        dl->AddRectFilled(r.Min, r.Max, C(theme::bg_field), 4.0f * s);
        dl->PushClipRect(r.Min, ImVec2(r.Max.x - 29.0f * s, r.Max.y), true);
        kit::PaintText(dl, kit::Role::BodyMedium, closedText ? closedText : "",
                       ImVec2(r.Min.x + 13.0f * s, r.Min.y + 6.0f * s), 18.0f,
                       C(theme::text_muted));
        dl->PopClipRect();
        Stroke(dl, ImVec2(r.Max.x - 21.0f * s, r.Min.y + 13.0f * s),
               {ImVec2(0, 0), ImVec2(4, 4), ImVec2(8, 0)}, 1.4f, C(theme::text_muted));
    }
    // Open list: kit::DropdownListLook() (Kara's list look when group D sets it).
    int picked = -1;
    const kit::ListLook& L = kit::DropdownListLook();
    const bool empty = count <= 0;
    const int rows = empty ? 1 : count;
    const float pad = L.pad * s, rowH = L.rowH * s, pitch = rowH + L.rowGap * s;
    const float popW = r.GetWidth(),
                popH = rowH * rows + (rows > 1 ? L.rowGap * s * (rows - 1) : 0.0f) + pad * 2.0f;
    ImVec2 pos(r.Min.x, r.Max.y + L.gap * s);
    if (pos.y + popH > ImGui::GetIO().DisplaySize.y)
        pos.y = r.Min.y - L.gap * s - popH;
    ImGui::SetNextWindowPos(pos);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0, 0));
    ImGui::PushStyleVar(ImGuiStyleVar_PopupBorderSize, 0.0f);
    if (ImGui::BeginPopup(popupId, ImGuiWindowFlags_NoBackground |
                                       ImGuiWindowFlags_NoScrollWithMouse |
                                       ImGuiWindowFlags_NoScrollbar)) {
        ImDrawList* dl = ImGui::GetWindowDrawList();
        const ImVec2 p = ImGui::GetCursorScreenPos();
        const ImRect box(p, ImVec2(p.x + popW, p.y + popH));
        if (L.shadow.w > 0.0f)
            kit::DropShadow(dl, box, L.radius, L.shadow, L.shadowBlur, L.shadowY);
        dl->AddRectFilled(box.Min, box.Max, C(L.bg), L.radius * s);
        if (L.border.w > 0.0f) {
            const float t = 1.0f * s;
            dl->AddRect(ImVec2(box.Min.x + t * 0.5f - 0.5f, box.Min.y + t * 0.5f - 0.5f),
                        ImVec2(box.Max.x - t * 0.5f + 0.5f, box.Max.y - t * 0.5f + 0.5f),
                        C(L.border), L.radius * s - t * 0.5f, 0, t);
        }
        const float textY = (L.rowH - 18.0f) * 0.5f * s;
        if (empty) {
            kit::PaintText(dl, L.role, emptyText ? emptyText : "",
                           ImVec2(box.Min.x + pad + L.textX * s, box.Min.y + pad + textY), 18.0f,
                           C(L.textEmpty));
        }
        for (int i = 0; i < count; ++i) {
            const ImRect ir(ImVec2(box.Min.x + pad, box.Min.y + pad + pitch * i),
                            ImVec2(box.Max.x - pad, box.Min.y + pad + pitch * i + rowH));
            ImGui::PushID(i);
            const ImGuiID iid = ImGui::GetID("##item");
            ImGui::ItemAdd(ir, iid);
            bool ih = false, ihd = false;
            if (SafeButtonBehavior(ir, iid, &ih, &ihd)) {
                picked = i;
                ImGui::CloseCurrentPopup();
            }
            ImGui::PopID();
            const bool sel = i == current;
            if (ih || sel)
                dl->AddRectFilled(ir.Min, ir.Max, C(ih ? L.rowHover : L.rowSelected),
                                  L.rowRadius * s);
            kit::PaintText(dl, L.role, items[i], ImVec2(ir.Min.x + L.textX * s, ir.Min.y + textY),
                           18.0f, C(ih || sel ? L.textSelected : L.text));
        }
        ImGui::SetCursorScreenPos(p);
        ImGui::Dummy(ImVec2(popW, popH));
        ImGui::EndPopup();
    }
    ImGui::PopStyleVar(2);
    return picked;
}

void Placeholder(const char* text, bool disabled) {
    const ImRect r(ImGui::GetItemRectMin(), ImGui::GetItemRectMax());
    if (r.GetWidth() <= 0.0f)
        return;
    const float s = S();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->PushClipRect(r.Min, ImVec2(r.Max.x - 29.0f * s, r.Max.y), true);
    ImVec4 col = theme::text_muted;
    if (disabled)
        col.w *= 0.5f;
    kit::PaintText(dl, kit::Role::BodyMedium, text, ImVec2(r.Min.x + 13.0f * s, r.Min.y + 6.0f * s),
                   18.0f, C(col));
    dl->PopClipRect();
}

void Truncated(const char* text, float w, const ImVec4& col, float lineHeight) {
    if (!text || w <= 0.0f)
        return;
    const ImRect r = kit::Place(w, lineHeight);
    ImGui::ItemAdd(r, 0);
    std::string shown = text;
    const bool cut = kit::TextWidth(text) > w;
    if (cut) {
        static const char* const kEllipsis = "\xE2\x80\xA6";
        const float ew = kit::TextWidth(kEllipsis);
        size_t n = shown.size();
        while (n > 0 && kit::TextWidth(shown.substr(0, n).c_str()) + ew > w) {
            --n;
            while (n > 0 && (shown[n] & 0xC0) == 0x80)
                --n; // whole UTF-8 characters
        }
        while (n > 0 && shown[n - 1] == ' ')
            --n;
        shown = shown.substr(0, n) + kEllipsis;
    }
    kit::PaintText(ImGui::GetWindowDrawList(), kit::Role::Body, shown.c_str(), r.Min, lineHeight,
                   C(col));
    if (cut && ImGui::IsWindowHovered() && ImGui::IsMouseHoveringRect(r.Min, r.Max))
        kit::ShowTooltip(text);
}

void Wrapped(const char* text, const ImVec4& col, kit::Role role, float lineHeight) {
    const float s = S();
    const float width = kit::Avail();
    ImFont* f = fonts::Get(role);
    const char* end = text + strlen(text);
    struct Line {
        const char* b;
        const char* e;
    };
    std::vector<Line> lines;
    for (const char* p = text; p < end;) {
        const char* e =
            f->CalcWordWrapPositionA(fonts::PixelSize(role) * s / f->FontSize, p, end, width * s);
        if (e == p)
            e = p + 1;
        const char* le = e;
        while (le > p && (le[-1] == ' ' || le[-1] == '\n'))
            --le;
        lines.push_back({p, le});
        p = e;
        while (p < end && (*p == ' ' || *p == '\n'))
            ++p;
    }
    if (lines.empty())
        lines.push_back({text, text});
    const ImRect r = kit::Place(0.0f, lineHeight * lines.size());
    ImGui::ItemAdd(r, 0);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    for (size_t i = 0; i < lines.size(); ++i) {
        const std::string line(lines[i].b, lines[i].e);
        kit::PaintText(dl, role, line.c_str(), ImVec2(r.Min.x, r.Min.y + lineHeight * i * s),
                       lineHeight, C(col));
    }
}

// ---- footer -----------------------------------------------------------------------------------------------------

} // namespace dolly::rail_fields
