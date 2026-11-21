#include "dolly_rail_kit.hpp"
#include "dolly_rail_icons.h"
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace dolly::rail_kit {
namespace icons = dolly::rail_icons;
namespace {

struct Column {
    float x, w, y;
};
struct Row {
    float top, h, gap, x, end;
    bool active;
};
struct Cols {
    Column parent;
    float gap, leftW, top, bottomLeft;
};
struct TipEntry {
    std::string label, text;
};
struct ItemsOverride {
    std::string id, closedFormat, emptyText;
    std::vector<std::string> items;
    std::vector<const char*> ptrs;
};
} // namespace
struct ContextState {
    float scale = 1;
    ImVec2 origin, absPos, exact;
    std::vector<Column> cols;
    std::vector<Cols> colsStack;
    Row row{};
    bool abs = false;
    float maxY = 0;
    FooterSlotFn footer = nullptr;
    std::vector<TipEntry> tips;
    std::vector<ItemsOverride> overrides;
    ListLook list;
    TooltipLook tooltip;
    ImFont* fonts[5]{};
    float atlas_scale = 1;
};
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
// Frame scratch alias, only used under the overlay render lock and restored by Scope.
ContextState* active = nullptr;
#define g (*active)
float S() {
    return g.scale;
}

ImVec2 ToScreen(float x, float y) {
    return ImVec2(g.origin.x + std::round(x * g.scale), g.origin.y + std::round(y * g.scale));
}

ImDrawList* DL() {
    return ImGui::GetWindowDrawList();
}
ImU32 C(const ImVec4& c) {
    return theme::U32(c);
}

float WidthRange(const char* b, const char* e, Role r) {
    ImFont* f = fonts::Get(r);
    return f->CalcTextSizeA(fonts::PixelSize(r) * g.scale, FLT_MAX, 0.0f, b, e).x / g.scale;
}

void DrawRange(ImDrawList* dl, Role r, const char* b, const char* e, ImVec2 topLeft, float lh,
               ImU32 col) {
    ImFont* f = fonts::Get(r);
    float base = topLeft.y + fonts::BaselineOffset(r, lh) * g.scale;
    ImVec2 pos(std::floor(topLeft.x + 0.5f),
               std::round(base) -
                   std::round(f->Ascent * (fonts::PixelSize(r) * g.scale / f->FontSize)));
    dl->AddText(f, fonts::PixelSize(r) * g.scale, pos, col, b, e);
}

// Polyline in design px relative to `o` (screen px), stroked with round caps.
void Stroke(ImDrawList* dl, ImVec2 o, std::initializer_list<ImVec2> pts, float th, ImU32 col,
            bool caps = true) {
    const float s = g.scale;
    dl->PathClear();
    for (const ImVec2& p : pts)
        dl->PathLineTo(ImVec2(o.x + p.x * s, o.y + p.y * s));
    dl->PathStroke(col, 0, th * s);
    if (caps) {
        const ImVec2* first = pts.begin();
        const ImVec2* last = pts.end() - 1;
        dl->AddCircleFilled(ImVec2(o.x + first->x * s, o.y + first->y * s), th * s * 0.5f, col, 12);
        dl->AddCircleFilled(ImVec2(o.x + last->x * s, o.y + last->y * s), th * s * 0.5f, col, 12);
        for (const ImVec2* p = first + 1; p < last; ++p) // round joins
            dl->AddCircleFilled(ImVec2(o.x + p->x * s, o.y + p->y * s), th * s * 0.5f, col, 12);
    }
}

// Inside stroke of a rounded rect (Figma strokeAlign INSIDE), 1 design px.
void InsideStroke(ImDrawList* dl, const ImRect& r, float radius, ImU32 col) {
    const float s = g.scale, t = 1.0f * s;
    dl->AddRect(ImVec2(r.Min.x + t * 0.5f - 0.5f, r.Min.y + t * 0.5f - 0.5f),
                ImVec2(r.Max.x - t * 0.5f + 0.5f, r.Max.y - t * 0.5f + 0.5f), col,
                radius * s - t * 0.5f, 0, t);
}

struct AlphaScope {
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

// ---- hooks registered by scene files ----------------------------------------------------------

std::vector<TipEntry>& Tips() {
    return g.tips;
}

// Tooltip registered for the label [b, e), or null.
const char* TipFor(const char* b, const char* e) {
    if (Tips().empty() || !b)
        return nullptr;
    const size_t n = (size_t)(e - b);
    for (const TipEntry& t : Tips())
        if (t.label.size() == n && !memcmp(t.label.data(), b, n))
            return t.text.c_str();
    return nullptr;
}
const char* TipFor(const char* label) {
    return label ? TipFor(label, ImGui::FindRenderedTextEnd(label)) : nullptr;
}

bool HoverRect(const ImRect& r) {
    return ImGui::IsWindowHovered() && ImGui::IsMouseHoveringRect(r.Min, r.Max);
}

std::vector<ItemsOverride>& ItemOverrides() {
    return g.overrides;
}

const ItemsOverride* FindItems(const char* id) {
    for (const ItemsOverride& o : ItemOverrides())
        if (o.id == id)
            return &o;
    return nullptr;
}

// Width a widget of requested width `w` gets at the current layout position (Place's rules, no side effects).
float ResolveWidth(float w) {
    const Column& c = g.cols.back();
    if (g.abs)
        return w <= 0.0f ? c.w + w - g.absPos.x : w;
    if (g.row.active) {
        float avail = g.row.end - g.row.x;
        return w == 0.0f ? avail : (w < 0.0f ? avail + w : w);
    }
    return w == 0.0f ? c.w : (w < 0.0f ? c.w + w : w);
}

} // namespace

// ---- scale & regions ------------------------------------------------------------------------
Context::Context() : state(std::make_unique<ContextState>()) {}
Context::~Context() = default;
Scope::Scope(Context& context) : previous_(active) {
    active = context.state.get();
}
Scope::~Scope() {
    active = previous_;
}
void SetFonts(ImFont* body, ImFont* medium, ImFont* small, ImFont* chip, ImFont* title,
              float atlas_scale) {
    g.atlas_scale = atlas_scale;
    g.fonts[0] = body;
    g.fonts[1] = medium;
    g.fonts[2] = small;
    g.fonts[3] = chip;
    g.fonts[4] = title;
}
ImFont* ActiveFont(unsigned role) {
    return role < 5 && g.fonts[role] ? g.fonts[role] : ImGui::GetFont();
}
float AtlasScale() {
    return g.atlas_scale;
}
void SetScale(float s) {
    g.scale = s;
}
float Scale() {
    return g.scale;
}

void BeginRegion(ImVec2 o, float w) {
    g.origin = ImVec2(std::round(o.x), std::round(o.y));
    g.cols.assign(1, Column{0.0f, w, 0.0f});
    g.colsStack.clear();
    g.row = Row{};
    g.abs = false;
    g.maxY = 0.0f;
}

float EndRegion() {
    float h = g.maxY;
    if (!g.cols.empty() && g.cols[0].y > h)
        h = g.cols[0].y;
    return h;
}

// ---- layout ---------------------------------------------------------------------------------
void VGap(float h) {
    if (!g.cols.empty() && !g.row.active)
        g.cols.back().y += h;
}

void BeginRow(float height, float gap) {
    Column& c = g.cols.back();
    g.row = Row{c.y, height, gap, c.x, c.x + c.w, true};
}

void EndRow() {
    if (!g.row.active)
        return;
    Column& c = g.cols.back();
    c.y = g.row.top + g.row.h;
    if (c.y > g.maxY)
        g.maxY = c.y;
    g.row.active = false;
}

void HGap(float w) {
    if (g.row.active)
        g.row.x += w;
}
void Spring(float trailing) {
    if (g.row.active)
        g.row.x = g.row.end - trailing;
}

void BeginColumns(float gap, float leftW) {
    Column parent = g.cols.back();
    float lw = leftW > 0.0f ? leftW : (parent.w - gap) * 0.5f;
    g.colsStack.push_back(Cols{parent, gap, lw, parent.y, parent.y});
    g.cols.push_back(Column{parent.x, lw, parent.y});
}

void NextColumn() {
    if (g.colsStack.empty())
        return;
    Cols& cs = g.colsStack.back();
    cs.bottomLeft = g.cols.back().y;
    g.cols.back() =
        Column{cs.parent.x + cs.leftW + cs.gap, cs.parent.w - cs.leftW - cs.gap, cs.top};
}

void EndColumns() {
    if (g.colsStack.empty())
        return;
    Cols cs = g.colsStack.back();
    g.colsStack.pop_back();
    float bottom = g.cols.back().y > cs.bottomLeft ? g.cols.back().y : cs.bottomLeft;
    g.cols.pop_back();
    g.cols.back().y = bottom;
}

float Avail() {
    return g.row.active ? g.row.end - g.row.x : g.cols.back().w;
}

void At(float x, float y) {
    g.abs = true;
    g.absPos = ImVec2(x, y);
}

float CursorY() {
    return g.cols.empty() ? 0.0f : g.cols.back().y;
}

ImRect Place(float w, float h) {
    Column& c = g.cols.back();
    float x, y;
    if (g.abs) {
        if (w <= 0.0f)
            w = c.w + w - g.absPos.x;
        x = g.absPos.x;
        y = g.absPos.y;
        g.abs = false;
    } else if (g.row.active) {
        float avail = g.row.end - g.row.x;
        if (w == 0.0f)
            w = avail;
        else if (w < 0.0f)
            w = avail + w;
        x = g.row.x;
        y = g.row.top + (g.row.h - h) * 0.5f;
        g.row.x += w + g.row.gap;
    } else {
        if (w == 0.0f)
            w = c.w;
        else if (w < 0.0f)
            w = c.w + w;
        x = c.x;
        y = c.y;
        c.y += h;
    }
    if (y + h > g.maxY)
        g.maxY = y + h;
    g.exact = ImVec2(g.origin.x + x * g.scale, g.origin.y + y * g.scale);
    ImRect r(ToScreen(x, y), ToScreen(x + w, y + h));
    ImGui::SetCursorScreenPos(r.Min);
    ImGui::ItemSize(r.GetSize());
    return r;
}

// ---- text -----------------------------------------------------------------------------------
float TextWidth(const char* text, Role r) {
    return WidthRange(text, ImGui::FindRenderedTextEnd(text), r);
}

void PaintText(ImDrawList* dl, Role r, const char* text, ImVec2 topLeft, float lh, ImU32 col) {
    DrawRange(dl, r, text, ImGui::FindRenderedTextEnd(text), topLeft, lh, col);
}

void Text(const char* text, const ImVec4& col, Role r, float lh, float w, Align a) {
    const char* end = ImGui::FindRenderedTextEnd(text);
    float tw = WidthRange(text, end, r);
    if (w == HUG)
        w = std::ceil(tw);
    ImRect rr = Place(w, lh);
    ImGui::ItemAdd(rr, 0);
    float x = rr.Min.x;
    if (a == Align::Center)
        x = rr.Min.x + (rr.GetWidth() - tw * S()) * 0.5f;
    else if (a == Align::Right)
        x = rr.Max.x - tw * S();
    DrawRange(DL(), r, text, end, ImVec2(x, rr.Min.y), lh, C(col));
    if (const char* tip = TipFor(text, end))
        if (HoverRect(rr))
            ShowTooltip(tip);
}

// ---- composites -----------------------------------------------------------------------------
void SectionTitle(const char* title, const char* info, const char* detail) {
    BeginRow(18.0f, 6.0f);
    Text(title, theme::text_primary, Role::Title, 18.0f);
    if (info)
        InfoIcon(info);
    if (detail) {
        float dw = std::ceil(TextWidth(detail));
        Spring(dw);
        Text(detail, theme::text_dim, Role::Body, 18.0f, dw);
    }
    EndRow();
}

void Divider() {
    ImRect r = Place(0.0f, 1.0f);
    DL()->AddRectFilled(r.Min, r.Max, C(theme::border_default));
}

void FooterRow(const char* status) {
    // Figma Rail / Footer row [design reference] (widths from the component so the row matches exactly).
    constexpr float kTrailing = 29.0f + 8.0f + 47.0f; // F7 chip, gap, Console
    BeginRow(30.0f, 8.0f);
    Button("Stop / restore", 104.0f);
    ShortcutChip("Backspace", 74.0f);
    if (g.footer)
        g.footer(status, Avail() - kTrailing - 8.0f);
    else if (status)
        Text(status, theme::text_dim, Role::Body, 18.0f, 94.0f);
    Spring(kTrailing);
    ShortcutChip("F7", 29.0f);
    Text("Console", theme::text_muted, Role::Body, 18.0f, 47.0f);
    // Layout button removed (Kara: "remove layout since you cant use it anymore").
    EndRow();
}

void StaticInfoIcon(ImDrawList* dl, ImVec2 o, ImU32 col) {
    const float s = S();
    const ImVec2 c(o.x + 6.5f * s, o.y + 6.5f * s);
    dl->AddCircle(c, 6.0f * s + 0.5f, col, 32, 1.0f * s); // AddCircle insets the radius by 0.5
    const ImVec2 a(c.x, o.y + 5.6f * s), b(c.x, o.y + 9.2f * s);
    dl->AddLine(a, b, col, 1.1f * s);
    dl->AddCircleFilled(a, 0.55f * s, col, 12);
    dl->AddCircleFilled(b, 0.55f * s, col, 12);
    dl->AddCircleFilled(ImVec2(c.x, o.y + 4.0f * s), 0.7f * s, col, 12);
}

void Notice(const char* text, const ImVec4& textCol, float w) {
    // Same geometry as the Campaths notice (drawer_campaths.cpp): one line = 32 tall, text at (31, 8).
    const float s = S(), width = ResolveWidth(w);
    ImFont* f = fonts::Get(Role::Body);
    const char* end = text + strlen(text);
    struct Line {
        const char* b;
        const char* e;
    };
    std::vector<Line> lines;
    for (const char* p = text; p < end;) {
        const char* e = f->CalcWordWrapPositionA(fonts::PixelSize(Role::Body) * S() / f->FontSize,
                                                 p, end, (width - 41.0f) * s);
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
    ImRect r = Place(width, 16.0f * lines.size() + 16.0f);
    ImGui::ItemAdd(r, 0);
    ImDrawList* dl = DL();
    dl->AddRectFilled(r.Min, r.Max, C(theme::bg_field), 4.0f * s);
    StaticInfoIcon(dl, ImVec2(r.Min.x + 10.0f * s, r.Min.y + 9.5f * s), C(theme::text_dim));
    for (size_t i = 0; i < lines.size(); ++i)
        DrawRange(dl, Role::Body, lines[i].b, lines[i].e,
                  ImVec2(r.Min.x + 31.0f * s, r.Min.y + (8.0f + 16.0f * i) * s), 16.0f, C(textCol));
}

// ---- widgets --------------------------------------------------------------------------------
// A disabled widget still owns the mouse (like ImGui's disabled items): pressing it never drags the
// drawer behind it (drawers drag by their empty background).
static bool ClaimHover(const ImRect& r, ImGuiID id) {
    return ImGui::ItemHoverable(r, id, GImGui->CurrentItemFlags);
}

bool Button(const char* label, float w, Btn type, bool disabled, Align align) {
    const char* end = ImGui::FindRenderedTextEnd(label);
    float tw = WidthRange(label, end, Role::Body);
    if (w == HUG)
        w = std::ceil(type == Btn::Quiet ? tw : tw + 26.0f);
    ImRect r = Place(w, 30.0f);
    ImGuiID id = ImGui::GetID(label);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool hov = false, held = false, pressed = false;
    if (!disabled)
        pressed = SafeButtonBehavior(r, id, &hov, &held);
    else
        hov = ClaimHover(r, id); // the colours below test `disabled` first
    if (hov)
        if (const char* tip = TipFor(label, end))
            ShowTooltip(tip);
    ImVec4 fill(0, 0, 0, 0), text = theme::text_primary;
    switch (type) {
    case Btn::Secondary:
        fill = disabled ? theme::bg_field
               : held   ? theme::button_pressed
               : hov    ? theme::button_hover
                        : theme::button_default;
        text = disabled ? theme::text_dim : theme::text_primary;
        break;
    case Btn::Primary:
        fill = disabled ? theme::bg_field
               : held   ? theme::accent_pressed
               : hov    ? theme::accent_hover
                        : theme::accent_default;
        text = disabled ? theme::text_dim : theme::text_on_accent;
        break;
    case Btn::Quiet:
        text = disabled ? theme::text_dim : hov ? theme::text_primary : theme::text_muted;
        break;
    case Btn::Field:
        fill = theme::bg_field;
        text = theme::text_primary;
        break;
    }
    ImDrawList* dl = DL();
    if (fill.w > 0.0f)
        dl->AddRectFilled(r.Min, r.Max, C(fill), 4.0f * S());
    float x = r.Min.x + (r.GetWidth() - tw * S()) * 0.5f;
    if (align == Align::Left)
        x = r.Min.x + (type == Btn::Quiet ? 0.0f : 13.0f * S());
    else if (align == Align::Right)
        x = r.Max.x - 13.0f * S() - tw * S();
    DrawRange(dl, Role::Body, label, end, ImVec2(x, r.Min.y + 6.0f * S()), 18.0f, C(text));
    return pressed;
}

bool ChevronButton(const char* idStr, Dir dir, bool disabled, bool fieldFill) {
    ImRect r = Place(30.0f, 30.0f);
    ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool hov = false, held = false, pressed = false;
    if (!disabled)
        pressed = SafeButtonBehavior(r, id, &hov, &held);
    else
        ClaimHover(r, id);
    ImVec4 fill = disabled    ? theme::bg_field
                  : hov       ? theme::button_hover
                  : fieldFill ? theme::bg_field
                              : theme::button_default;
    ImDrawList* dl = DL();
    dl->AddRectFilled(r.Min, r.Max, C(fill), 4.0f * S());
    ImU32 col = C(disabled ? theme::text_dim : theme::text_primary);
    // Figma: 7x4 chevron "M0 0 L3.5 4 L7 0" rotated -90 / +90, stroke 1, round caps.
    if (dir == Dir::Left)
        Stroke(dl, r.Min, {ImVec2(17, 11.5f), ImVec2(13, 15), ImVec2(17, 18.5f)}, 1.0f, col);
    else
        Stroke(dl, r.Min, {ImVec2(13, 11.5f), ImVec2(17, 15), ImVec2(13, 18.5f)}, 1.0f, col);
    return pressed;
}

bool ArrowButton(const char* idStr, Dir dir, bool quiet, bool disabled) {
    ImRect r = Place(30.0f, 30.0f);
    ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool hov = false, held = false, pressed = false;
    if (!disabled)
        pressed = SafeButtonBehavior(r, id, &hov, &held);
    else
        ClaimHover(r, id);
    ImDrawList* dl = DL();
    if (!quiet)
        dl->AddRectFilled(r.Min, r.Max, C(disabled ? theme::bg_field : theme::button_default),
                          4.0f * S());
    ImU32 col = C(disabled ? theme::text_dim : quiet ? theme::text_muted : theme::text_primary);
    if (dir == Dir::Left)
        Stroke(dl, ImVec2(r.Min.x + 13.3f * S(), r.Min.y + 12 * S()),
               {ImVec2(3, 0), ImVec2(0, 3), ImVec2(3, 6)}, 1.4f, col);
    else
        Stroke(dl, ImVec2(r.Min.x + 13.8f * S(), r.Min.y + 12 * S()),
               {ImVec2(0, 0), ImVec2(3, 3), ImVec2(0, 6)}, 1.4f, col);
    return pressed;
}

static void FieldChrome(ImDrawList* dl, const ImRect& r) {
    dl->AddRectFilled(r.Min, r.Max, C(theme::bg_field), 4.0f * S());
}

bool Dropdown(const char* idStr, int* current, const char* const* items, int count, float w,
              bool disabled) {
    ImRect r = Place(w, 30.0f);
    ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool hov = false, held = false;
    char popupId[64];
    snprintf(popupId, sizeof(popupId), "##dd%08x", id);
    if (!disabled && SafeButtonBehavior(r, id, &hov, &held) && !ImGui::IsPopupOpen(popupId))
        ImGui::OpenPopup(popupId); // like ImGui::BeginCombo: clicking the open dropdown closes it
    if (disabled)
        hov = ClaimHover(r, id);
    if (hov && !ImGui::IsPopupOpen(popupId))
        if (const char* tip = TipFor(idStr))
            ShowTooltip(tip);

    // Figma data from a scene file (OverrideItems) replaces the drawer's own list.
    const ItemsOverride* ov = FindItems(idStr);
    const char* const* listItems = items;
    int listCount = count;
    const char* emptyText = nullptr;
    if (ov && !ov->ptrs.empty()) {
        listItems = ov->ptrs.data();
        listCount = (int)ov->ptrs.size();
    } else if (ov && !ov->emptyText.empty()) {
        listCount = 0;
        emptyText = ov->emptyText.c_str();
    }
    {
        AlphaScope dim(disabled);
        ImDrawList* dl = DL();
        FieldChrome(dl, r);
        const char* value = "";
        if (current && *current >= 0) {
            if (listItems != items && listCount > 0)
                value = listItems[*current < listCount ? *current : listCount - 1];
            else if (*current < count)
                value = items[*current];
        }
        char formatted[128];
        if (ov && !ov->closedFormat.empty()) {
            snprintf(formatted, sizeof(formatted), ov->closedFormat.c_str(), value);
            value = formatted;
        }
        dl->PushClipRect(r.Min, ImVec2(r.Max.x - 29.0f * S(), r.Max.y), true);
        PaintText(dl, Role::BodyMedium, value, ImVec2(r.Min.x + 13.0f * S(), r.Min.y + 6.0f * S()),
                  18.0f, C(theme::text_muted));
        dl->PopClipRect();
        // Figma chevron "M0 0 L4 4 L8 0", 8x4 at (w - 21, 13), stroke 1.4 round.
        Stroke(dl, ImVec2(r.Max.x - 21.0f * S(), r.Min.y + 13.0f * S()),
               {ImVec2(0, 0), ImVec2(4, 4), ImVec2(8, 0)}, 1.4f, C(theme::text_muted));
    }

    // Popup list: DropdownListLook() (default: Figma menu style, bg/surface, 1 px border/default, radius 6, rows 30).
    bool changed = false;
    const ListLook& L = DropdownListLook();
    const int rows = emptyText ? 1 : listCount;
    const float s = S(), pad = L.pad * s, rowH = L.rowH * s;
    const float pitch = rowH + L.rowGap * s; // row to row (rowGap: group D)
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
        ImVec2 p = ImGui::GetCursorScreenPos();
        ImRect box(p, ImVec2(p.x + popW, p.y + popH));
        if (L.shadow.w > 0.0f)
            DropShadow(dl, box, L.radius, L.shadow, L.shadowBlur, L.shadowY);
        dl->AddRectFilled(box.Min, box.Max, C(L.bg), L.radius * s);
        if (L.border.w > 0.0f)
            InsideStroke(dl, box, L.radius, C(L.border));
        const float textY = (L.rowH - 18.0f) * 0.5f * s;
        if (emptyText) {
            PaintText(dl, L.role, emptyText,
                      ImVec2(box.Min.x + pad + L.textX * s, box.Min.y + pad + textY), 18.0f,
                      C(L.textEmpty));
        }
        for (int i = 0; i < listCount; ++i) {
            ImRect ir(ImVec2(box.Min.x + pad, box.Min.y + pad + pitch * i),
                      ImVec2(box.Max.x - pad, box.Min.y + pad + pitch * i + rowH));
            ImGui::PushID(i);
            ImGuiID iid = ImGui::GetID("##item");
            ImGui::ItemAdd(ir, iid);
            bool ih = false, ihd = false;
            if (SafeButtonBehavior(ir, iid, &ih, &ihd)) {
                *current = i;
                changed = true;
                ImGui::CloseCurrentPopup();
            }
            ImGui::PopID();
            bool sel = current && *current == i;
            if (ih || sel)
                dl->AddRectFilled(ir.Min, ir.Max, C(ih ? L.rowHover : L.rowSelected),
                                  L.rowRadius * s);
            PaintText(dl, L.role, listItems[i], ImVec2(ir.Min.x + L.textX * s, ir.Min.y + textY),
                      18.0f, C(ih || sel ? L.textSelected : L.text));
        }
        ImGui::SetCursorScreenPos(p);
        ImGui::Dummy(ImVec2(popW, popH));
        ImGui::EndPopup();
    }
    ImGui::PopStyleVar(2);
    return changed;
}

void Field(const char* value, float w, bool disabled) {
    ImRect r = Place(w, 30.0f);
    ImGui::ItemAdd(r, 0);
    AlphaScope dim(disabled);
    ImDrawList* dl = DL();
    FieldChrome(dl, r);
    dl->PushClipRect(r.Min, r.Max, true);
    PaintText(dl, Role::BodyMedium, value, ImVec2(r.Min.x + 13.0f * S(), r.Min.y + 6.0f * S()),
              18.0f, C(theme::text_muted));
    dl->PopClipRect();
}

bool InputField(const char* idStr, char* buf, int bufSize, float w) {
    ImRect r = Place(w, 30.0f);
    FieldChrome(DL(), r);
    ImFont* f = fonts::Get(Role::BodyMedium);
    const float previous_scale = ImGui::GetCurrentWindow()->FontWindowScale;
    ImGui::SetWindowFontScale(fonts::PixelSize(Role::BodyMedium) * S() / f->FontSize);
    // Put the text on Field's Figma baseline (centring the font box would land it 1 px lower).
    float lineTop =
        std::round(r.Min.y + (6.0f + fonts::BaselineOffset(Role::BodyMedium, 18.0f)) * S()) -
        std::round(f->Ascent * (fonts::PixelSize(Role::BodyMedium) * S() / f->FontSize));
    ImGui::PushFont(f);
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(13.0f * S(), lineTop - r.Min.y));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 4.0f * S());
    ImGui::PushStyleColor(ImGuiCol_FrameBg, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleColor(ImGuiCol_FrameBgHovered, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleColor(ImGuiCol_FrameBgActive, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleColor(ImGuiCol_Text, theme::text_muted);
    ImGui::PushStyleColor(ImGuiCol_TextSelectedBg, theme::accent_default);
    ImGui::SetCursorScreenPos(r.Min);
    ImGui::SetNextItemWidth(r.GetWidth());
    bool changed = ImGui::InputText(idStr, buf, (size_t)bufSize);
    ImGui::PopStyleColor(5);
    ImGui::PopStyleVar(2);
    ImGui::PopFont();
    ImGui::SetWindowFontScale(previous_scale);
    return changed;
}

bool Slider(const char* idStr, float* v, float vmin, float vmax, float w, bool disabled) {
    ImRect r = Place(w, 12.0f);
    ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return false;
    const float s = S();
    bool changed = false;
    if (!disabled) {
        bool hov = false, held = false;
        SafeButtonBehavior(r, id, &hov, &held, ImGuiButtonFlags_PressedOnClick);
        if (held && vmax > vmin) {
            float t = (ImGui::GetIO().MousePos.x - r.Min.x - 6.0f * s) / (r.GetWidth() - 12.0f * s);
            t = t < 0 ? 0 : (t > 1 ? 1 : t);
            float nv = vmin + (vmax - vmin) * t;
            if (nv != *v) {
                *v = nv;
                changed = true;
            }
        }
    } else {
        ClaimHover(r, id);
    }
    AlphaScope dim(disabled);
    float t = vmax > vmin ? (*v - vmin) / (vmax - vmin) : 0.0f;
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    // Figma: 4 px track (radius 2) with accent fill up to a 12 px white thumb kept inside the track.
    // The thumb snaps to whole design px, like the Figma slider fills.
    float thumbX = r.Min.x + std::round(t * (r.GetWidth() / s - 12.0f)) * s;
    ImDrawList* dl = DL();
    ImVec2 a(r.Min.x, r.Min.y + 4.0f * s), b(r.Max.x, r.Min.y + 8.0f * s);
    dl->AddRectFilled(a, b, C(theme::button_default), 2.0f * s);
    if (thumbX + 6.0f * s > a.x)
        dl->AddRectFilled(a, ImVec2(thumbX + 6.0f * s, b.y), C(theme::accent_default), 2.0f * s,
                          ImDrawFlags_RoundCornersLeft);
    dl->AddCircleFilled(ImVec2(thumbX + 6.0f * s, r.Min.y + 6.0f * s), 6.0f * s,
                        C(theme::text_primary), 32);
    return changed;
}

bool SliderRow(const char* label, float* v, float vmin, float vmax, const char* fmt,
               bool disabled) {
    char value[32];
    snprintf(value, sizeof(value), fmt, *v);
    BeginRow(18.0f, 8.0f);
    Text(label, theme::text_muted);
    float vw = std::ceil(TextWidth(value));
    Spring(vw);
    Text(value, theme::text_primary, Role::Body, 18.0f, vw);
    EndRow();
    VGap(10.0f);
    ImGui::PushID(label);
    bool changed = Slider("##slider", v, vmin, vmax, 0.0f, disabled);
    ImGui::PopID();
    return changed;
}

bool Checkbox(const char* label, bool* v, bool disabled) {
    const char* end = ImGui::FindRenderedTextEnd(label);
    float tw = WidthRange(label, end, Role::Body);
    ImRect r = Place(std::ceil(17.0f + tw), 18.0f);
    ImGuiID id = ImGui::GetID(label);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool changed = false, hov = false;
    if (!disabled) {
        bool held = false;
        if (SafeButtonBehavior(r, id, &hov, &held)) {
            *v = !*v;
            changed = true;
        }
    } else {
        hov = ClaimHover(r, id);
    }
    if (hov)
        if (const char* tip = TipFor(label, end))
            ShowTooltip(tip);
    AlphaScope dim(disabled);
    const float s = S();
    ImDrawList* dl = DL();
    ImRect box(ImVec2(r.Min.x, r.Min.y + 3.0f * s),
               ImVec2(r.Min.x + 12.0f * s, r.Min.y + 15.0f * s));
    if (*v) {
        dl->AddRectFilled(box.Min, box.Max, C(theme::accent_default), 4.0f * s);
        // Figma check "M0 2 L2 4 L6 0" at (3, 4) inside the box, stroke 1, butt caps.
        Stroke(dl, ImVec2(box.Min.x + 3.0f * s, box.Min.y + 4.0f * s),
               {ImVec2(0, 2), ImVec2(2, 4), ImVec2(6, 0)}, 1.0f, C(theme::text_on_accent), false);
    } else {
        dl->AddRectFilled(box.Min, box.Max, C(theme::bg_field), 4.0f * s);
        InsideStroke(dl, box, 4.0f, C(theme::border_default));
    }
    DrawRange(dl, Role::Body, label, end, ImVec2(r.Min.x + 17.0f * s, r.Min.y), 18.0f,
              C(theme::text_primary));
    return changed;
}

bool Disclosure(const char* label, bool* open) {
    const char* end = ImGui::FindRenderedTextEnd(label);
    float tw = WidthRange(label, end, Role::Body);
    ImRect r = Place(std::ceil(14.0f + tw), 18.0f);
    ImGuiID id = ImGui::GetID(label);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool hov = false, held = false, toggled = false;
    if (SafeButtonBehavior(r, id, &hov, &held)) {
        *open = !*open;
        toggled = true;
    }
    if (hov)
        if (const char* tip = TipFor(label, end))
            ShowTooltip(tip);
    const float s = S();
    ImDrawList* dl = DL();
    ImU32 chev = C(theme::text_muted);
    ImVec2 box(r.Min.x, r.Min.y + 5.0f * s); // 8x8 chevron frame centred in the 18 px row
    if (*open)
        Stroke(dl, ImVec2(box.x + 1.5f * s, box.y + 3.0f * s),
               {ImVec2(0, 0), ImVec2(2.5f, 2.5f), ImVec2(5, 0)}, 1.4f, chev);
    else
        Stroke(dl, ImVec2(box.x + 3.0f * s, box.y + 1.5f * s),
               {ImVec2(0, 0), ImVec2(2.5f, 2.5f), ImVec2(0, 5)}, 1.4f, chev);
    DrawRange(dl, Role::Body, label, end, ImVec2(r.Min.x + 14.0f * s, r.Min.y), 18.0f,
              C(*open ? theme::text_primary : theme::text_muted));
    return toggled;
}

bool Tab(const char* label, bool selected, float w) {
    const char* end = ImGui::FindRenderedTextEnd(label);
    float tw = WidthRange(label, end, Role::Body);
    if (w == HUG)
        w = std::ceil(tw + 26.0f);
    ImRect r = Place(w, 30.0f);
    ImGuiID id = ImGui::GetID(label);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool hov = false, held = false;
    bool pressed = SafeButtonBehavior(r, id, &hov, &held);
    if (hov)
        if (const char* tip = TipFor(label, end))
            ShowTooltip(tip);
    ImDrawList* dl = DL();
    if (selected)
        dl->AddRectFilled(r.Min, r.Max, C(theme::button_default), 4.0f * S());
    DrawRange(dl, Role::Body, label, end, ImVec2(r.Min.x + 13.0f * S(), r.Min.y + 6.0f * S()),
              18.0f, C(selected ? theme::text_primary : theme::text_muted));
    return pressed;
}

void ShortcutChip(const char* keys, float w) {
    float tw = TextWidth(keys, Role::Chip);
    if (w == HUG)
        w = std::ceil(tw + 16.0f);
    ImRect r = Place(w, 20.0f);
    ImGui::ItemAdd(r, 0);
    ImDrawList* dl = DL();
    InsideStroke(dl, r, 4.0f, C(theme::border_default));
    float x = r.Min.x + (r.GetWidth() - tw * S()) * 0.5f;
    PaintText(dl, Role::Chip, keys, ImVec2(x, r.Min.y + 3.0f * S()), 14.0f, C(theme::text_muted));
    if (const char* tip = TipFor(keys))
        if (HoverRect(r))
            ShowTooltip(tip);
}

bool Keycap(const char* idStr, const char* keys, float w, KeycapState st) {
    ImRect r = Place(w, 30.0f);
    ImGuiID id = ImGui::GetID(idStr);
    if (!ImGui::ItemAdd(r, id))
        return false;
    bool hov = false, held = false;
    bool pressed = SafeButtonBehavior(r, id, &hov, &held);
    ImDrawList* dl = DL();
    FieldChrome(dl, r);
    if (st == KeycapState::Listening)
        InsideStroke(dl, r, 4.0f, C(theme::accent_default));
    float tw = TextWidth(keys, Role::BodyMedium);
    PaintText(dl, Role::BodyMedium, keys,
              ImVec2(r.Min.x + (r.GetWidth() - tw * S()) * 0.5f, r.Min.y + 6.0f * S()), 18.0f,
              C(st == KeycapState::Empty ? theme::text_dim : theme::text_primary));
    return pressed;
}

void InfoIcon(const char* tooltip) {
    ImRect r = Place(13.0f, 13.0f);
    ImGuiID id = ImGui::GetID(tooltip ? tooltip : "##info");
    if (!ImGui::ItemAdd(r, id))
        return;
    bool hov = ClaimHover(r, id); // pressing the icon never drags the drawer
    const float s = S();
    ImU32 col = C(hov ? theme::accent_default : theme::text_dim);
    ImDrawList* dl = DL();
    // Figma Dolly / Info icon: 12 px ring (centre stroke 1), 3.6 px stem (stroke 1.1, round), 1.4 px dot.
    // Drawn at the unrounded position: centred in an 18 px row the icon sits on a half pixel, as in Figma.
    // AddCircle insets the radius by 0.5, so add it back.
    const ImVec2 o = g.exact;
    dl->AddCircle(ImVec2(o.x + 6.5f * s, o.y + 6.5f * s), 6.0f * s + 0.5f, col, 32, 1.0f * s);
    Stroke(dl, o, {ImVec2(6.5f, 5.6f), ImVec2(6.5f, 9.2f)}, 1.1f, col);
    dl->AddCircleFilled(ImVec2(o.x + 6.5f * s, o.y + 4.0f * s), 0.7f * s, col, 12);
    if (hov && tooltip)
        ShowTooltip(tooltip);
}

void ItemTooltip(const char* text) {
    if (text && ImGui::IsItemHovered())
        ShowTooltip(text);
}

void ShowTooltip(const char* text) {
    // Figma Dolly / Info bubble: bg/field, radius 7, padding 6/10, Inter 12 / 16 line height, wraps at 300.
    const float s = S();
    ImFont* f = fonts::Get(Role::Body);
    const char* end = text + strlen(text);
    struct Line {
        const char* b;
        const char* e;
    };
    std::vector<Line> lines;
    float maxW = 0.0f;
    for (const char* p = text; p < end;) {
        const char* e = f->CalcWordWrapPositionA(fonts::PixelSize(Role::Body) * S() / f->FontSize,
                                                 p, end, 300.0f * s);
        if (e == p)
            e = p + 1;
        const char* le = e;
        while (le > p && (le[-1] == ' ' || le[-1] == '\n'))
            --le;
        lines.push_back({p, le});
        float lw = f->CalcTextSizeA(fonts::PixelSize(Role::Body) * g.scale, FLT_MAX, 0.0f, p, le).x;
        if (lw > maxW)
            maxW = lw;
        p = e;
        while (p < end && (*p == ' ' || *p == '\n'))
            ++p;
    }
    const TooltipLook& look = Tooltips();
    if (look.fullWrapWidth && lines.size() > 1)
        maxW = 300.0f * s;
    ImVec2 size(std::ceil(maxW + 20.0f * s), std::round((16.0f * lines.size() + 12.0f) * s));
    if (look.atOffset) { // group D: Kara's spot next to the software cursor, kept on screen (above the pointer near the bottom)
        const ImVec2 m = ImGui::GetIO().MousePos, disp = ImGui::GetIO().DisplaySize;
        ImVec2 at(std::round(m.x + look.offset.x * s), std::round(m.y + look.offset.y * s));
        if (at.x + size.x > disp.x)
            at.x = ImMax(0.0f, disp.x - size.x);
        if (at.y + size.y > disp.y)
            at.y = ImMax(0.0f, std::round(m.y - 4.0f * s - size.y));
        ImGui::SetNextWindowPos(at);
    }
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0, 0));
    ImGui::PushStyleVar(ImGuiStyleVar_PopupBorderSize, 0.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, 0.0f);
    ImGui::PushStyleColor(ImGuiCol_PopupBg, ImVec4(0, 0, 0, 0));
    if (ImGui::BeginTooltip()) {
        ImDrawList* dl = ImGui::GetWindowDrawList();
        ImVec2 p = ImGui::GetCursorScreenPos();
        if (look.shadow.w > 0.0f)
            DropShadow(dl, ImRect(p, ImVec2(p.x + size.x, p.y + size.y)), 7.0f, look.shadow,
                       look.shadowBlur, look.shadowY);
        dl->AddRectFilled(p, ImVec2(p.x + size.x, p.y + size.y), C(theme::bg_field), 7.0f * s);
        for (size_t i = 0; i < lines.size(); ++i)
            DrawRange(dl, Role::Body, lines[i].b, lines[i].e,
                      ImVec2(p.x + 10.0f * s, p.y + (6.0f + 16.0f * i) * s), 16.0f,
                      C(theme::text_primary));
        ImGui::Dummy(size);
        ImGui::EndTooltip();
    }
    ImGui::PopStyleColor();
    ImGui::PopStyleVar(3);
}

void SetFooterSlot(FooterSlotFn fn) {
    g.footer = fn;
}

void RegisterTooltip(const char* label, const char* text) {
    for (TipEntry& t : Tips())
        if (t.label == label) {
            t.text = text;
            return;
        }
    Tips().push_back(TipEntry{label, text});
}

ListLook& DropdownListLook() {
    return g.list;
}

TooltipLook& Tooltips() {
    return g.tooltip;
}

void DropShadow(ImDrawList* dl, const ImRect& r, float radius, const ImVec4& col, float blur,
                float offsetY) {
    // Gaussian blur (sigma = blur / 2, as Figma) approximated by stacked rounded rects, outermost first: each layer's
    // alpha makes the accumulated coverage at its edge follow 0.5 * erfc(d / (sigma * sqrt 2)) of the colour's alpha.
    const float s = S();
    if (col.w <= 0.0f || blur <= 0.0f)
        return;
    const float sigma = blur * 0.5f * s, step = ImMax(1.0f, sigma / 6.0f);
    const ImVec2 a(r.Min.x, r.Min.y + offsetY * s), b(r.Max.x, r.Max.y + offsetY * s);
    const float inner =
        -ImMin(2.0f * sigma, ImMax(0.0f, ImMin(b.x - a.x, b.y - a.y) * 0.5f - 1.0f));
    dl->PushClipRectFullScreen();
    float covered = 0.0f;
    for (float e = 3.0f * sigma; e >= inner; e -= step) {
        const float want = col.w * 0.5f * std::erfc(e / (sigma * 1.41421356f));
        const float k = 1.0f - (1.0f - want) / (1.0f - covered);
        covered = want;
        if (k <= 0.0f)
            continue;
        ImVec4 c = col;
        c.w = k;
        dl->AddRectFilled(ImVec2(a.x - e, a.y - e), ImVec2(b.x + e, b.y + e), C(c),
                          ImMax(0.0f, radius * s + e));
    }
    dl->PopClipRect();
}

void OverrideItems(const char* id, const char* const* items, int count, const char* closedFormat,
                   const char* emptyText) {
    ItemsOverride o;
    o.id = id;
    for (int i = 0; i < count; ++i)
        o.items.push_back(items[i]);
    if (closedFormat)
        o.closedFormat = closedFormat;
    if (emptyText)
        o.emptyText = emptyText;
    std::vector<ItemsOverride>& all = ItemOverrides();
    bool replaced = false;
    for (ItemsOverride& e : all)
        if (!replaced && e.id == o.id) {
            e = std::move(o);
            replaced = true;
        }
    if (!replaced)
        all.push_back(std::move(o));
    for (ItemsOverride& e :
         all) { // pointers into the stored strings (the vector may have moved them)
        e.ptrs.clear();
        for (const std::string& s : e.items)
            e.ptrs.push_back(s.c_str());
    }
}

void Placeholder(const char* name, float height) {
    ImRect r = Place(0.0f, height);
    ImGui::ItemAdd(r, 0);
    ImDrawList* dl = DL();
    InsideStroke(dl, r, 4.0f, C(theme::border_default));
    char label[96];
    snprintf(label, sizeof(label), "%s drawer (stub)", name);
    float tw = TextWidth(label);
    PaintText(dl, Role::Body, label,
              ImVec2(r.Min.x + (r.GetWidth() - tw * S()) * 0.5f,
                     r.Min.y + (r.GetHeight() - 18.0f * S()) * 0.5f),
              18.0f, C(theme::text_dim));
}

} // namespace kit

namespace dolly::rail_fonts {
ImFont* Get(Role role) {
    return rail_kit::ActiveFont(unsigned(role));
}
float PixelSize(Role role) {
    return Get(role)->FontSize / rail_kit::AtlasScale();
}
float BaselineOffset(Role role, float line) {
    const float size = role == Role::Title                         ? 18.f
                       : role == Role::Small || role == Role::Chip ? 11.f
                                                                   : 12.f;
    // Inter's design metrics; Oracle title uses its own ascent/descent ratios.
    const auto* font = Get(role);
    const float asc = role == Role::Title ? font->Ascent / rail_kit::AtlasScale() / size : .96875f;
    const float desc =
        role == Role::Title ? -font->Descent / rail_kit::AtlasScale() / size : .2412f;
    return (line - (asc + desc) * size) * .5f + asc * size;
}
}
