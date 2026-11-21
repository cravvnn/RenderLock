#pragma once
#include "dolly_rail_kit.hpp"
namespace dolly::rail_fields {
namespace kit = rail_kit;
namespace theme = rail_theme;
namespace fonts = rail_fonts;
// ---- layout scopes -----------------------------------------------------------------------------------------------
// Kara's greyed group (a section at 45 %, inert): Waiting for replay / Working, depth of field without cameras.
struct Greyed {
    bool on;
    explicit Greyed(bool dim);
    ~Greyed();
    Greyed(const Greyed&) = delete;
    Greyed& operator=(const Greyed&) = delete;
};
// Inert without a look of its own: the widgets inside draw their own disabled look (the real BeginDisabled).
struct Inert {
    bool on;
    explicit Inert(bool disabled);
    ~Inert();
    Inert(const Inert&) = delete;
    Inert& operator=(const Inert&) = delete;
};

// ---- widgets -------------------------------------------------------------------------------------------------------
// One edit of a slider / drag field this frame.
struct Edit {
    bool changed = false; // the value moved this frame (dragging, typing, right-click reset)
    bool active = false;  // being dragged or typed in
    bool released =
        false; // the edit ended this frame: commit (release, Enter, click away, right-click reset)
    bool typing = false; // the number entry is open (the caller skips drawing its own value text)
    bool hovered = false;
};

enum class Map {
    Linear,
    Log,
    Log1p
}; // Log: ImGui's logarithmic slider (min > 0); Log1p: Citadel focus distance

struct SliderOpts {
    Map map = Map::Linear;
    bool hasReset = false; // right-click resets to `reset`
    float reset = 0.0f;
    bool disabled = false;         // the slider's own disabled look (50 %)
    const char* typeFmt = nullptr; // format of the Ctrl+click entry (default: the value format)
    bool unclamped_entry = false;  // Snap can author offsets beyond the fine slider range.
};
inline SliderOpts Opts(Map m = Map::Linear) {
    SliderOpts o;
    o.map = m;
    return o;
}
inline SliderOpts Resets(float v, bool disabled = false) {
    SliderOpts o;
    o.hasReset = true;
    o.reset = v;
    o.disabled = disabled;
    return o;
}

// Kit slider (Figma 4 px track, 12 px thumb) with the real slider's behaviour: drag, Ctrl+click to type a value
// (Enter or a click elsewhere applies, Esc cancels), right-click reset. 12 tall, `w` as kit widths.
Edit Slider(const char* id, float* v, float vmin, float vmax, float w,
            const SliderOpts& o = SliderOpts{});
// "Movement speed ... 400": label + value row, the slider 10 px below (kit::SliderRow's layout). fmt == nullptr:
// no value text (the label carries it: "Focus distance · 200 in").
Edit SliderRow(const char* label, float* v, float vmin, float vmax, const char* fmt,
               const SliderOpts& o = SliderOpts{});
// "Distance | slider | 135": label (labelW), 10, slider 275, 10, value right-aligned in 44 (Follow / Bone rows).
Edit SliderLine(const char* label, float* v, float vmin, float vmax, const char* fmt,
                float labelW = 64.0f, float indent = 0.0f, const SliderOpts& o = SliderOpts{});

// Field look (Dolly / Field) with the real DragScalar: drag sideways (Alt 100x finer, Shift 10x faster), Ctrl+click or
// double-click to type, right-click resets to `reset`, applies when released. 30 tall.
Edit DragField(const char* id, double* v, double speed, double reset, float w = 0.0f,
               bool disabled = false);

// The real overlay's chrome-less DragFloat cells (Campaths Arrive / Bank) over the screen rect `r`: drag sideways
// (Alt finer, Shift faster), Ctrl+click or double-click to type in a field over the cell, clamped to vmin .. vmax.
// The caller draws the value text unless e.typing. Call ImGui::SetNextItemAllowOverlap() on the row item first.
Edit DragCell(const char* id, const ImRect& r, float* v, float speed, float vmin, float vmax,
              const char* fmt, bool disabled);

// Dropdown with its closed text chosen by the caller (kit::Dropdown shows the item): "Point: Bone" over the items
// "Eyes", "Weapon", "Bone Picker...". Same look as kit::Dropdown and kit::DropdownListLook(). Returns the picked
// index (also when it is the current one: "Bone Picker..." reopens the picker), -1 when nothing was picked.
int Combo(const char* id, int current, const char* const* items, int count, const char* closedText,
          float w = 0.0f, bool disabled = false, const char* emptyText = nullptr);
// Placeholder in the last kit::Dropdown's closed field ("Select a hero..." while nothing is picked).
void Placeholder(const char* text, bool disabled = false);

void Wrapped(const char* text, const ImVec4& color, kit::Role role = kit::Role::Body,
             float line_height = 16);
void Truncated(const char* text, float width, const ImVec4& color, float line_height = 16);
} // namespace dolly::rail_fields
