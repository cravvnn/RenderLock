#pragma once
// Shared widget kit matching the Figma "Overlay · rail" kit and the Dolly Redesign components.
//
// Units: every size/position argument is in Figma design px; the kit multiplies by the UI scale.
// Layout mirrors Figma auto-layout: content flows top-down inside a region (a drawer's content area),
// rows lay children out left-to-right and centre them vertically, gaps are explicit (no implicit spacing).
//
// Width arguments:  > 0 fixed width,  0 fill the rest of the row/column,  < 0 fill minus |w|,  HUG = fit content.
#include "imgui.h"
#include <memory>
#include "imgui_internal.h" // ImRect
#include "dolly_rail_theme.hpp"
#include "dolly_rail_fonts.hpp"

namespace dolly::rail_kit {
namespace theme = dolly::rail_theme;
namespace fonts = dolly::rail_fonts;

using fonts::Role;

struct ContextState;
class Context {
public:
    Context();
    ~Context();
    std::unique_ptr<ContextState> state;
};
// One binding per draw call; persistent edit/layout state belongs to OverlayPanel.
class Scope {
    ContextState* previous_;

public:
    explicit Scope(Context& context);
    ~Scope();
};
void SetFonts(ImFont* body, ImFont* medium, ImFont* small, ImFont* chip, ImFont* title,
              float atlas_scale = 1);

constexpr float HUG = -1.0e9f;

enum class Align { Left, Center, Right };
enum class Btn {
    Secondary,
    Primary,
    Quiet,
    Field
}; // Field = bg/field fill, white label (replay bar "Ivy")
enum class Dir { Left, Right };
enum class KeycapState { Default, Listening, Empty };

// ---- scale & regions (the shell sets these up; drawers normally don't call them) -------------
void SetScale(float s);
float Scale();
inline float Px(float design) {
    return design * Scale();
}
void BeginRegion(ImVec2 originScreen, float widthDesign); // origin snapped to whole pixels
float EndRegion();                                        // returns the used height (design px)

// ---- layout ---------------------------------------------------------------------------------
void VGap(float h);                                    // vertical gap between stacked items
void BeginRow(float height = 30.0f, float gap = 8.0f); // children centred vertically in `height`
void EndRow();
void HGap(float w);               // extra horizontal space inside a row
void Spring(float trailingWidth); // next items end flush right; pass their total width incl. gaps
void BeginColumns(float gap = 30.0f, float leftWidth = 0.0f); // two columns (0 = equal split)
void NextColumn();
void EndColumns();              // continues below the taller column
float Avail();                  // design px left in the current row / column
void At(float x, float y);      // place only the next item at (x, y) from the region origin
ImRect Place(float w, float h); // reserve an item rect (screen px); used by custom widgets
float CursorY();                // design px from the region top

// ---- text -----------------------------------------------------------------------------------
float TextWidth(const char* text, Role r = Role::Body); // design px, text up to "##"
// Draws text in a Figma text box whose top-left is `boxTopLeft` (screen px) with the given line height.
void PaintText(ImDrawList* dl, Role r, const char* text, ImVec2 boxTopLeft, float lineHeight,
               ImU32 col);
void Text(const char* text, const ImVec4& col = theme::text_primary, Role r = Role::Body,
          float lineHeight = 18.0f, float w = HUG, Align a = Align::Left);
inline void Label(const char* text, float w = HUG) {
    Text(text, theme::text_muted, Role::Body, 18.0f, w);
}

// ---- composites -----------------------------------------------------------------------------
// Title row (18 tall, full width): VALVE Oracle 15 title, optional info icon 6 px after it (hover shows
// `info` as a tooltip), optional dim detail text flush right ("5 saved").
void SectionTitle(const char* title, const char* info = nullptr, const char* detail = nullptr);
void Divider(); // 1 px border/default line, full width
// Rail / Footer row [design reference]: Stop / restore + Backspace chip, status slot ... F7 chip + Console (30 tall, full width).
// `status`: dim text 94 wide in the status slot, 8 px after the chip (Look: "ReShade not set"). The Layout
// button is gone (Kara removed it).
void FooterRow(const char* status = nullptr);
// Kara's notice (Campaths "Finish recording before editing cameras."): bg/field, radius 4, padding 8 / 10,
// 13 px info icon, 8 px, Inter 12 / 16 text that wraps; `w` as for other widgets (0 = full width).
void Notice(const char* text, const ImVec4& textCol = theme::text_primary, float w = 0.0f);
void StaticInfoIcon(ImDrawList* dl, ImVec2 topLeft,
                    ImU32 col); // Dolly / Info icon 13x13, no tooltip

// ---- widgets (return true when clicked / changed) --------------------------------------------
bool Button(
    const char* label, float w = HUG, Btn type = Btn::Secondary, bool disabled = false,
    Align align = Align::Center); // Figma buttons centre the label; a few use Left (13 px padding)
bool ChevronButton(const char* id, Dir dir, bool disabled = false,
                   bool fieldFill = false); // Button / Chevron 30x30
bool ArrowButton(const char* id, Dir dir, bool quiet = false,
                 bool disabled = false); // Overlay / Arrow button 30x30
bool Dropdown(const char* id, int* current, const char* const* items, int count, float w = 0,
              bool disabled = false);
void Field(const char* value, float w = 0, bool disabled = false); // Dolly / Field (read-only look)
bool InputField(const char* id, char* buf, int bufSize, float w = 0); // same look, editable
bool Slider(const char* id, float* v, float vmin, float vmax, float w = 0,
            bool disabled = false); // 12 tall
// Label + value row (18 tall) with a slider 10 px below: "Movement speed ... 400".
bool SliderRow(const char* label, float* v, float vmin, float vmax, const char* fmt = "%.0f",
               bool disabled = false);
bool Checkbox(const char* label, bool* v, bool disabled = false); // 18 tall
// Disclosure row (18 tall): 8 px chevron + 6 px gap + label. Closed: ">" and muted label; open: "v" and white label.
bool Disclosure(const char* label, bool* open);
bool Tab(const char* label, bool selected, float w = HUG); // Dolly / Tab 30 tall
void ShortcutChip(const char* keys, float w = HUG);        // Overlay / Shortcut 20 tall
bool Keycap(const char* id, const char* keys, float w = 140.0f,
            KeycapState s = KeycapState::Default);
void InfoIcon(const char* tooltip); // 13x13, tooltip on hover
void ItemTooltip(const char* text); // call right after any item: Figma info-bubble tooltip on hover
void ShowTooltip(const char* text); // shows the info-bubble tooltip now (at the mouse)

// Placeholder card used by the drawer stubs (dim outline + name) of the given content height.
void Placeholder(const char* name, float height);

// ---- hooks for scene files (register once, e.g. from a static initialiser; they apply everywhere) -----
// Footer status slot: when set, FooterRow calls it inside its row right after the Backspace chip (row gap 8)
// instead of drawing `status`. `width` = design px left before the F7 chip. Draw with kit widgets.
using FooterSlotFn = void (*)(const char* status, float width);
void SetFooterSlot(FooterSlotFn fn);

// Hover tooltip (Info bubble) for an existing widget, matched by its label (text before "##"): Button,
// Checkbox, Tab, Disclosure, ShortcutChip, Text, and Dropdown by its id ("##cam-step").
void RegisterTooltip(const char* label, const char* text);

// Look of every open dropdown list. Defaults = the look the preview had (bg/surface, border/default, radius 6,
// 4 px padding, 30 px rows, selected row button/default). Change the fields to restyle all lists.
struct ListLook {
    ImVec4 bg = theme::bg_surface;
    ImVec4 border = theme::border_default; // alpha 0 = no border
    float radius = 6.0f, pad = 4.0f, rowH = 30.0f, rowRadius = 4.0f,
          gap = 4.0f;   // gap: field -> list
    float textX = 9.0f; // label inset inside a row
    ImVec4 rowSelected = theme::button_default, rowHover = theme::button_hover;
    ImVec4 text = theme::text_muted, textSelected = theme::text_primary,
           textEmpty = theme::text_dim;
    Role role = Role::Body;
    // Added for group D (Kara's lists [design reference]): space between rows, and a Figma DROP_SHADOW under the list
    // (alpha 0 = none; blur and y offset in design px). The defaults keep the look above.
    float rowGap = 0.0f;
    ImVec4 shadow = ImVec4(0, 0, 0, 0);
    float shadowBlur = 0.0f, shadowY = 0.0f;
};
ListLook& DropdownListLook();

// Added for group D: placement and shadow of the hover tooltips (ShowTooltip, Kara's Info bubble [design reference]). The
// defaults keep the preview's look (ImGui places the bubble, no shadow, the bubble hugs its longest line).
struct TooltipLook {
    bool atOffset = false; // true: bubble top-left at pointer + offset (design px), kept on screen
    ImVec2 offset = ImVec2(0, 0);
    bool fullWrapWidth =
        false; // wrapped text: bubble = wrap width + padding (Figma's fixed 300 px text box)
    ImVec4 shadow =
        ImVec4(0, 0, 0, 0); // Figma DROP_SHADOW (alpha 0 = none); blur and y offset in design px
    float shadowBlur = 0.0f, shadowY = 0.0f;
};
TooltipLook& Tooltips();

// Soft drop shadow of a rounded rect `r` (screen px; Figma DROP_SHADOW with spread 0: colour, blur, y offset in
// design px), drawn on `dl` even outside its window. Draw it before the shape it belongs to.
void DropShadow(ImDrawList* dl, const ImRect& r, float radius, const ImVec4& col, float blur,
                float offsetY);

// Replaces a dropdown's items (matched by its id string, e.g. "##follow-hero") with Figma's data. The drawer's
// index keeps working (clamped). `closedFormat` formats the closed field ("Point: %s"). count 0 with
// `emptyText` keeps the closed value and shows one dim, unselectable row ("Waiting for replay players...").
void OverrideItems(const char* id, const char* const* items, int count,
                   const char* closedFormat = nullptr, const char* emptyText = nullptr);

} // namespace kit
