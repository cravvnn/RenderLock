// Object Picker ImGui presentation. Its own right-side window, separate from
// the Bone Picker and the main Dolly panel. The scene surface projects the
// proxy wireframe and sends placement/selection as editor events; it never
// mutates the project or calls an engine function.
#include "dolly_overlay_panel.hpp"
#include "dolly_rail_design.hpp"
#include "dolly_object_picker.hpp"
#include "dolly_object_runtime.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdio>

namespace dolly {
namespace {
// Drag-and-drop payload type for a library row dropped onto the scene canvas.
constexpr const char* kObjectPayload = "DLY_OBJECT";
// Live gizmo drag state, owned by this presentation unit. The transform is
// committed to the project once on mouse release (one ObjectTransform event).
struct GizmoDrag {
    bool active = false;
    int index = -1;
    int mode = 0; // 0 move, 1 rotate, 2 scale
    std::array<double, 3> base_position{};
    std::array<double, 3> base_angles{};
    double base_scale = 1.0;
    // Live (dragged) transform; drawn directly so the project is never mutated
    // mid-drag and no const_cast is needed on the snapshot.
    std::array<double, 3> live_position{};
    std::array<double, 3> live_angles{};
    double live_scale = 1.0;
    ImVec2 start_mouse{};
};
GizmoDrag g_gizmo;
int g_gizmo_mode = 0; // selected tool, persisted across frames

// Client-side drop animation. A paused replay does not step the engine's
// physics, so a just-placed object falls under Dolly's own gravity to a floor
// and settles. Keyed by object index; the in-flight state is presentation-only
// and never enters the project. The floor is the placed Z (where it was
// dropped) minus the drop height, so the object lands at the aim point.
struct DropAnim {
    bool active = false;
    int index = -1;
    DropState state{};
    double floor_z = 0;
};
constexpr int kMaxDrops = 16;
std::array<DropAnim, kMaxDrops> g_drops{};
int g_last_object_count = 0;  // detects a new placement to start a drop
bool g_drop_on_place = true;  // panel switch
float g_drop_height = 400.0f; // world units above the aim point
constexpr double kDropGravity = 1600.0;
constexpr double kDropRestitution = 0.35;

// Placement controls, presentation-only. Distance is a fixed distance ahead of
// the camera; Ground intersects the aim ray with the horizontal plane at world
// Z ``g_ground_z`` (falling back to Distance when the ray misses the plane).
// Snap is an optional grid applied when a point is chosen.
float g_place_distance = 600.0f;
int g_place_mode = 0;    // 0 distance, 1 ground
float g_ground_z = 0.0f; // world-Z plane for ground placement
bool g_snap_enabled = false;
float g_snap_size = 16.0f; // world units per grid cell
bool g_snap_angle_enabled = false;
float g_snap_angle = 15.0f; // degrees per step on rotate

// A scene click resolves through object_place_view, then optional grid snap.
bool resolve_place(const VisualizationView& view, const ImVec2& mouse, double fallback_distance,
                   std::array<double, 3>& point) {
    const PlaceMode mode = g_place_mode == 1 ? PlaceMode::Ground : PlaceMode::Distance;
    const double distance = g_place_distance > 0 ? double(g_place_distance) : fallback_distance;
    if (!object_place_view(view, mouse.x, mouse.y, mode, distance, double(g_ground_z), point))
        return false;
    if (g_snap_enabled && g_snap_size > 0)
        object_snap_point(point, double(g_snap_size));
    return true;
}

void start_drop(int index, double floor_z, double drop_height) {
    for (auto& drop : g_drops) {
        if (!drop.active) {
            drop.active = true;
            drop.index = index;
            drop.floor_z = floor_z;
            drop.state = DropState{floor_z + std::max(0.0, drop_height), 0.0, false};
            return;
        }
    }
}

void cancel_drops() {
    for (auto& drop : g_drops)
        drop.active = false;
}

// Current world-Z offset from the stored (resting) position for an index, or 0.
double drop_z_offset(int index) {
    for (const auto& drop : g_drops)
        if (drop.active && drop.index == index)
            return drop.state.z - drop.floor_z;
    return 0.0;
}
// Library mirror of dolly/object_library.py (id order is the wire index).
// Keep this label table in sync and ids/labels/categories in append-only order.
struct ObjectEntry {
    const char* label;
    const char* category;
};
constexpr std::array<ObjectEntry, 9> kLibrary{{
    {"Marker", "Guides"},
    {"Arrow", "Guides"},
    {"Sphere", "Guides"},
    {"Crate", "Props"},
    {"Barrel", "Props"},
    {"Pillar", "Props"},
    {"Poster", "Props"},
    {"Flat light", "Lights"},
    {"Point light", "Lights"},
}};
constexpr std::array<const char*, 3> kCategories{{"Guides", "Props", "Lights"}};

VisualizationView object_view(const EditorSnapshot& state, const ImVec2& display) {
    // Match the guide projection: ImGui coordinates and mouse positions live
    // in DisplaySize space, so placement and drawing must use the same view.
    VisualizationView view{};
    view.pose = state.pose;
    view.horizontal_fov = state.horizontal_fov;
    view.width = double(display.x);
    view.height = double(display.y);
    view.near_plane = 1;
    return view;
}
} // namespace

void OverlayPanel::draw_object_picker_controls(const EditorSnapshot& state) {
    static int active_library = 0;
    auto& io = ImGui::GetIO();
    // Same window geometry and chrome as the Bone Picker so the two right-side
    // modes read identically (panel_scale, rounding, padding, background).
    const float margin = rail_drawing_ ? 0 : 24 * panel_scale;
    const float width = std::min(350 * panel_scale, io.DisplaySize.x * .42f);
    const float left = rail_drawing_
                           ? (io.DisplaySize.x - 1920 * panel_scale) * .5f + 59 * panel_scale
                           : io.DisplaySize.x - width - margin;
    // The library child takes its exact content height so the controls below it
    // always have room. The window is first opened at full available height
    // (so no child is clamped while laying out), then shrunk to the measured
    // content height before End, which never clips a control.
    const float row_height = ImGui::GetTextLineHeight() + 10 * panel_scale;
    constexpr int kLibraryRows = 9;   // entries in kLibrary
    constexpr int kCategoryCount = 3; // Guides, Props, Lights
    const float spacing = ImGui::GetStyle().ItemSpacing.y;
    const float line = ImGui::GetTextLineHeight() + spacing;
    const float natural_library =
        line * kCategoryCount + (row_height + spacing) * kLibraryRows + spacing * kCategoryCount;
    const float max_height = io.DisplaySize.y - margin * 2;
    // Reserve space for the header above the list and every control below it
    // (placed list, tool, place, snap, drop, buttons). When the display is too
    // short for the natural list, the list shrinks and scrolls instead, so a
    // control is never pushed off the panel.
    const float reserved = line * 10 + 470 * panel_scale;
    const float library_height = std::max(60.0f, std::min(natural_library, max_height - reserved));

    if (rail_drawing_) {
        namespace k = rail_kit;
        namespace t = rail_theme;
        namespace f = rail_fields;
        auto& d = *rail_design_;
        const float scale = panel_scale;
        ImGui::SetNextWindowPos(ImVec2(left, 0));
        ImGui::SetNextWindowSize(ImVec2(width, std::min(max_height, d.object_height * scale)));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(19 * scale, 19 * scale));
        ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 2 * scale));
        ImGui::PushStyleColor(ImGuiCol_WindowBg, t::bg_surface);
        ImGui::Begin("##object-picker", nullptr,
                     ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                         ImGuiWindowFlags_NoSavedSettings);
        ImGui::SetWindowFontScale(scale / rail_font_dpi_);
        k::BeginRegion(ImGui::GetCursorScreenPos(), 312);
        k::Text("DOLLY / OBJECT PICKER", t::text_dim, k::Role::Chip, 14);
        k::VGap(4);
        k::Text("Place an object", t::text_primary, k::Role::Title, 26);
        k::VGap(4);
        k::Text("Click to place; drag a row to drop.", t::text_muted, k::Role::Body, 16);
        k::VGap(14);
        k::Divider();
        k::VGap(14);
        k::Text("Selected", t::text_primary, k::Role::BodyMedium, 16);
        k::VGap(6);
        k::Field(kLibrary[active_library].label);
        k::VGap(14);
        k::Text("Library", t::text_primary, k::Role::BodyMedium, 16);
        k::VGap(6);
        auto row = [&](const char* label, bool selected, const char* id, bool* del = nullptr) {
            auto r = k::Place(312, 28);
            auto* dl = ImGui::GetWindowDrawList();
            dl->AddRectFilled(r.Min, r.Max, t::U32(selected ? t::bg_rail_selected : t::bg_field),
                              4 * scale);
            ImGui::SetCursorScreenPos(r.Min);
            bool chosen = ImGui::InvisibleButton(id, ImVec2((del ? 285 : 312) * scale, 28 * scale));
            ImVec2 dot(r.Min.x + 16 * scale, r.Min.y + 14 * scale);
            if (selected)
                dl->AddCircleFilled(dot, 5 * scale, t::U32(t::accent_hover));
            dl->AddCircle(dot, 4.5f * scale, t::U32(selected ? t::text_primary : t::text_muted), 0,
                          scale);
            k::PaintText(dl, k::Role::BodyMedium, label,
                         ImVec2(r.Min.x + 32 * scale, r.Min.y + 6 * scale), 16,
                         t::U32(selected ? t::text_primary : t::text_muted));
            if (del) {
                ImGui::SetCursorScreenPos(ImVec2(r.Max.x - 27 * scale, r.Min.y));
                *del = ImGui::InvisibleButton("##delete", ImVec2(27 * scale, 28 * scale));
                k::PaintText(dl, k::Role::Body, "Del",
                             ImVec2(r.Max.x - 23 * scale, r.Min.y + 6 * scale), 16,
                             t::U32(t::text_muted));
            }
            return chosen;
        };
        // List scrolling preserves every placement control below on shorter displays.
        auto region = k::Place(312, std::min(340.f, std::max(100.f, max_height / scale - 590)));
        ImGui::SetCursorScreenPos(region.Min);
        ImGui::BeginChild("##object-library", region.GetSize(), ImGuiChildFlags_None,
                          ImGuiWindowFlags_NoBackground);
        k::BeginRegion(ImGui::GetCursorScreenPos(), 312);
        for (const char* category : kCategories) {
            k::Text(category, t::text_dim, k::Role::Small, 14);
            k::VGap(2);
            for (std::size_t index = 0; index < kLibrary.size(); ++index) {
                if (std::strcmp(kLibrary[index].category, category))
                    continue;
                ImGui::PushID(int(index));
                if (row(kLibrary[index].label, int(index) == active_library, "##object-row"))
                    active_library = int(index);
                if (ImGui::BeginDragDropSource(ImGuiDragDropFlags_SourceAllowNullID)) {
                    const int payload = int(index);
                    ImGui::SetDragDropPayload(kObjectPayload, &payload, sizeof(payload));
                    ImGui::TextUnformatted(kLibrary[index].label);
                    ImGui::EndDragDropSource();
                }
                ImGui::PopID();
                k::VGap(2);
            }
            k::VGap(6);
        }
        auto used = k::EndRegion();
        ImGui::SetCursorPosY(used * scale);
        ImGui::Dummy(ImVec2(1, 1));
        ImGui::EndChild();
        k::BeginRegion(ImVec2(region.Min.x, region.Max.y + 10 * scale), 312);
        char placed[48];
        std::snprintf(placed, sizeof(placed), "Placed (%u)", state.object_count);
        k::Text(placed, t::text_primary, k::Role::BodyMedium, 16);
        k::VGap(6);
        if (state.object_count) {
            auto box = k::Place(312, 30.f * std::min(3u, state.object_count));
            ImGui::SetCursorScreenPos(box.Min);
            ImGui::BeginChild("##placed-objects", box.GetSize(), ImGuiChildFlags_None,
                              ImGuiWindowFlags_NoBackground);
            k::BeginRegion(ImGui::GetCursorScreenPos(), 312);
            for (unsigned i = 0; i < state.object_count && i < kEditorObjectCount; ++i) {
                ImGui::PushID(int(i));
                char label[80];
                std::snprintf(label, sizeof(label), "%s %u",
                              kLibrary[std::min<std::size_t>(state.object_items[i].shape,
                                                             kLibrary.size() - 1)]
                                  .label,
                              i + 1);
                bool del = false;
                if (row(label, int(i) == state.object_selected, "##placed-row", &del))
                    d.send(EditorAction::ObjectSelect, i);
                if (del)
                    d.send(EditorAction::ObjectDelete, i);
                ImGui::PopID();
                k::VGap(2);
            }
            used = k::EndRegion();
            ImGui::SetCursorPosY(used * scale);
            ImGui::Dummy(ImVec2(1, 1));
            ImGui::EndChild();
            k::BeginRegion(ImVec2(box.Min.x, box.Max.y + 10 * scale), 312);
        } else {
            k::Text("No objects placed", t::text_muted);
            k::VGap(10);
        }
        auto tabs = [&](const char* label, const char* const* names, int count, int& current,
                        bool off = false) {
            k::BeginRow(30, 4);
            k::Label(label, 64);
            f::Inert inert(off);
            for (int i = 0; i < count; ++i) {
                if (k::Tab(names[i], i == current, (312 - 64 - 4 * count) / count))
                    current = i;
            }
            k::EndRow();
            k::VGap(10);
        };
        const char* tools[]{"Move", "Rotate", "Scale"};
        tabs("Tool", tools, 3, g_gizmo_mode, state.object_selected < 0);
        const char* places[]{"Ahead", "On ground"};
        tabs("Place", places, 2, g_place_mode);
        k::BeginRow(18, 10);
        k::Label(g_place_mode ? "Floor Z" : "Distance", 58);
        auto& distance = g_place_mode ? g_ground_z : g_place_distance;
        f::Slider("##place-distance", &distance, g_place_mode ? -2048.f : 50.f,
                  g_place_mode ? 2048.f : 4000.f, 162);
        char value[40];
        std::snprintf(value, sizeof(value), "%.0f", distance);
        k::Text(value, t::text_muted, k::Role::Body, 18, 72, k::Align::Right);
        k::EndRow();
        k::VGap(10);
        auto snap = [&](const char* label, bool& on, float& value, float low, float high,
                        const char* fmt) {
            k::BeginRow(18, 10);
            k::Label(label == std::string("Grid") ? "Snap" : "", 58);
            k::Checkbox(label, &on);
            k::EndRow();
            if (on) {
                k::VGap(8);
                k::BeginRow(18, 10);
                k::HGap(68);
                f::Slider(label, &value, low, high, 162);
                char text[40];
                std::snprintf(text, sizeof(text), fmt, value);
                k::Text(text, t::text_muted, k::Role::Body, 18, 72, k::Align::Right);
                k::EndRow();
            }
            k::VGap(10);
        };
        snap("Grid", g_snap_enabled, g_snap_size, 1, 256, "%.0f units");
        snap("Angle", g_snap_angle_enabled, g_snap_angle, 1, 90, "%.0f deg");
        snap("Drop on place", g_drop_on_place, g_drop_height, 0, 1500, "%.0f up");
        k::BeginRow(30, 8);
        if (k::Button("Close", 152) || ImGui::IsKeyPressed(ImGuiKey_Escape, false))
            d.send(EditorAction::ObjectPickerCancel);
        if (k::Button("Delete selected", 152, k::Btn::Secondary,
                      !state.object_count || state.object_selected < 0))
            d.send(EditorAction::ObjectDelete, state.object_selected);
        k::EndRow();
        used = k::EndRegion();
        const float measured =
            (ImGui::GetCursorScreenPos().y - ImGui::GetWindowPos().y) + 19 * scale;
        d.object_height = measured / scale;
        ImGui::SetWindowSize(ImVec2(width, std::min(max_height, measured)));
        ImGui::End();
        ImGui::PopStyleColor();
        ImGui::PopStyleVar(2);
    } else {
        ImGui::SetNextWindowPos(ImVec2(left, margin), ImGuiCond_Always);
        ImGui::SetNextWindowSize(ImVec2(width, max_height), ImGuiCond_Always);
        ImGui::SetNextWindowBgAlpha(.94f);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 12 * panel_scale);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,
                            ImVec2(20 * panel_scale, 18 * panel_scale));
        ImGui::Begin("##object-picker", nullptr,
                     ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                         ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoSavedSettings |
                         ImGuiWindowFlags_NoTitleBar);

        // Header block matches the Bone Picker header exactly.
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "DOLLY / OBJECT PICKER");
        ImGui::PushFont(heading_font);
        ImGui::TextUnformatted("Place an object");
        ImGui::PopFont();
        ImGui::TextDisabled("Click to place; drag a row to drop.");
        ImGui::Separator();

        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "SELECTED");
        ImGui::TextUnformatted(kLibrary[active_library].label);
        ImGui::TextDisabled("%s", kLibrary[active_library].category);
        ImGui::Spacing();
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "LIBRARY");

        // The library child is sized to its content, or smaller on a short display
        // (then it scrolls) so the controls below always keep their room.
        ImGui::BeginChild("##object-library", ImVec2(0, library_height), false,
                          ImGuiWindowFlags_HorizontalScrollbar);
        // The stray left-edge navigation cursor line is suppressed, matching the
        // Bone Picker list. The ring is the sole persistent selection indicator.
        ImGui::PushStyleColor(ImGuiCol_NavCursor, IM_COL32(0, 0, 0, 0));
        for (const char* category : kCategories) {
            ImGui::TextDisabled("%s", category);
            for (std::size_t index = 0; index < kLibrary.size(); ++index) {
                if (std::strcmp(kLibrary[index].category, category) != 0)
                    continue;
                ImGui::PushID(int(index));
                const bool selected = int(index) == active_library;
                const float row_width = ImGui::GetContentRegionAvail().x;
                if (ImGui::InvisibleButton("##object-row", ImVec2(row_width, row_height)))
                    active_library = int(index);
                // Drag this row into the scene to place that object where you drop.
                if (ImGui::BeginDragDropSource(ImGuiDragDropFlags_SourceAllowNullID)) {
                    const int payload = int(index);
                    ImGui::SetDragDropPayload(kObjectPayload, &payload, sizeof(payload));
                    ImGui::TextUnformatted(kLibrary[index].label);
                    ImGui::EndDragDropSource();
                }
                const auto row = ImGui::GetItemRectMin();
                const auto row_end = ImGui::GetItemRectMax();
                const bool hovered = ImGui::IsItemHovered();
                const auto text_y = row.y + (row_height - ImGui::GetTextLineHeight()) * .5f;
                const ImVec2 dot(row.x + 11 * panel_scale, row.y + row_height * .5f);
                auto* draw = ImGui::GetWindowDrawList();
                draw->AddRectFilled(row, row_end,
                                    hovered ? IM_COL32(41, 70, 70, 245) : IM_COL32(29, 45, 50, 225),
                                    5 * panel_scale);
                draw->AddRect(row, row_end, IM_COL32(69, 99, 101, 255), 5 * panel_scale);
                draw->AddCircle(dot, 4 * panel_scale, IM_COL32(146, 204, 192, 255));
                if (selected)
                    draw->AddCircleFilled(dot, 2.5f * panel_scale, IM_COL32(153, 247, 216, 255));
                draw->AddText(ImVec2(row.x + 25 * panel_scale, text_y),
                              IM_COL32(227, 239, 236, 255), kLibrary[index].label);
                ImGui::PopID();
            }
            ImGui::Spacing();
        }
        ImGui::PopStyleColor();
        ImGui::EndChild();
        ImGui::Separator();

        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "PLACED (%u)", state.object_count);
        const float list_height = row_height * 3.4f;
        ImGui::BeginChild("##object-placed", ImVec2(0, list_height), false,
                          ImGuiWindowFlags_NoScrollbar);
        if (state.object_count == 0) {
            ImGui::TextDisabled("Nothing placed yet.");
        } else {
            ImGui::PushStyleColor(ImGuiCol_NavCursor, IM_COL32(0, 0, 0, 0));
            for (std::uint32_t index = 0; index < state.object_count; ++index) {
                const auto& item = state.object_items[index];
                const char* label =
                    item.shape < kLibrary.size() ? kLibrary[item.shape].label : "Object";
                ImGui::PushID(int(1000 + index));
                const bool selected = int(index) == state.object_selected;
                const float item_width = ImGui::GetContentRegionAvail().x;
                if (ImGui::InvisibleButton("##placed-row", ImVec2(item_width, row_height)))
                    editor_enqueue(EditorAction::ObjectSelect, double(index));
                const auto row = ImGui::GetItemRectMin();
                const auto row_end = ImGui::GetItemRectMax();
                const bool hovered = ImGui::IsItemHovered();
                const auto text_y = row.y + (row_height - ImGui::GetTextLineHeight()) * .5f;
                const ImVec2 dot(row.x + 11 * panel_scale, row.y + row_height * .5f);
                auto* draw = ImGui::GetWindowDrawList();
                draw->AddRectFilled(row, row_end,
                                    selected  ? IM_COL32(41, 78, 70, 245)
                                    : hovered ? IM_COL32(41, 70, 70, 245)
                                              : IM_COL32(29, 45, 50, 225),
                                    5 * panel_scale);
                draw->AddRect(row, row_end,
                              selected ? IM_COL32(153, 247, 216, 255) : IM_COL32(69, 99, 101, 255),
                              5 * panel_scale);
                draw->AddCircle(dot, 4 * panel_scale, IM_COL32(146, 204, 192, 255));
                if (selected)
                    draw->AddCircleFilled(dot, 2.5f * panel_scale, IM_COL32(153, 247, 216, 255));
                draw->AddText(ImVec2(row.x + 25 * panel_scale, text_y),
                              IM_COL32(227, 239, 236, 255), label);
                // Delete is a real button overlapped on the row's right edge.
                ImGui::SameLine(row_end.x - row.x - 46 * panel_scale);
                if (ImGui::SmallButton("Del"))
                    editor_enqueue(EditorAction::ObjectDelete, double(index));
                ImGui::PopID();
            }
            ImGui::PopStyleColor();
        }
        ImGui::EndChild();

        // Gizmo tool for the selected object. Move is the default.
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "TOOL");
        const bool has_selection = state.object_selected >= 0;
        ImGui::BeginDisabled(!has_selection);
        const char* tools[] = {"Move", "Rotate", "Scale"};
        for (int t = 0; t < 3; ++t) {
            if (t)
                ImGui::SameLine();
            if (ImGui::RadioButton(tools[t], g_gizmo_mode == t))
                g_gizmo_mode = t;
        }
        ImGui::EndDisabled();
        ImGui::Spacing();

        // Placement: how a scene click resolves to a world point.
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "PLACE");
        if (ImGui::RadioButton("Ahead", g_place_mode == 0))
            g_place_mode = 0;
        ImGui::SameLine();
        if (ImGui::RadioButton("On ground", g_place_mode == 1))
            g_place_mode = 1;
        if (g_place_mode == 0) {
            ImGui::SetNextItemWidth(-1);
            ImGui::SliderFloat("##place-distance", &g_place_distance, 50.0f, 4000.0f, "%.0f ahead");
        } else {
            ImGui::SetNextItemWidth(-1);
            ImGui::SliderFloat("##ground-z", &g_ground_z, -2048.0f, 2048.0f, "%.0f floor Z");
        }
        ImGui::Spacing();

        // Snapping: an optional grid on placement and move, and an angle step on
        // rotate. Off by default so free placement is unchanged.
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "SNAP");
        ImGui::Checkbox("Grid", &g_snap_enabled);
        if (g_snap_enabled) {
            ImGui::SameLine();
            ImGui::SetNextItemWidth(-1);
            ImGui::SliderFloat("##snap-size", &g_snap_size, 1.0f, 256.0f, "%.0f units");
        }
        ImGui::Checkbox("Angle", &g_snap_angle_enabled);
        if (g_snap_angle_enabled) {
            ImGui::SameLine();
            ImGui::SetNextItemWidth(-1);
            ImGui::SliderFloat("##snap-angle", &g_snap_angle, 1.0f, 90.0f, "%.0f deg");
        }
        ImGui::Spacing();

        // Drop physics: a client-side gravity animation on placement (the paused
        // replay does not step the engine's physics).
        ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "DROP");
        ImGui::Checkbox("Drop on place", &g_drop_on_place);
        if (g_drop_on_place) {
            ImGui::SameLine();
            ImGui::SetNextItemWidth(-1);
            ImGui::SliderFloat("##drop-height", &g_drop_height, 0.0f, 1500.0f, "%.0f up");
        }
        ImGui::Spacing();

        const float half =
            (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) * .5f;
        if (ImGui::Button("Close", ImVec2(half, 0)) || ImGui::IsKeyPressed(ImGuiKey_Escape, false))
            editor_enqueue(EditorAction::ObjectPickerCancel);
        ImGui::SameLine();
        ImGui::BeginDisabled(state.object_count == 0 || state.object_selected < 0);
        if (ImGui::Button("Delete selected", ImVec2(-1, 0)))
            editor_enqueue(EditorAction::ObjectDelete, double(state.object_selected));
        ImGui::EndDisabled();
        // Shrink to the measured content height now that every row has been laid
        // out, so the panel fits its contents exactly and never clips the buttons.
        const float measured = ImGui::GetCursorPosY() + ImGui::GetStyle().WindowPadding.y;
        if (measured > 0 && measured < max_height)
            ImGui::SetWindowSize(ImVec2(width, measured));
        ImGui::End();
        ImGui::PopStyleVar(2);
    }

    // Scene surface: project the proxy wireframe and own scene clicks.
    const bool view_ready =
        state.ready && state.horizontal_fov > 1 && io.DisplaySize.x > 0 && io.DisplaySize.y > 0;
    if (!view_ready)
        return;
    // A new object appears at the end of the list; start its drop.
    if (state.object_count > g_last_object_count && g_drop_on_place && state.object_count > 0) {
        const int index = int(state.object_count) - 1;
        const auto& item = state.object_items[index];
        start_drop(index, double(item.position[2]), double(g_drop_height));
    }
    g_last_object_count = int(state.object_count);
    // Step every in-flight drop once per rendered frame.
    for (auto& drop : g_drops) {
        if (!drop.active)
            continue;
        if (drop.index >= int(state.object_count)) {
            drop.active = false; // object deleted mid-flight
            continue;
        }
        if (!object_drop_step(drop.state, double(io.DeltaTime), kDropGravity, drop.floor_z,
                              kDropRestitution))
            drop.active = false;
    }

    const VisualizationView view = object_view(state, io.DisplaySize);
    const float scene_left = rail_drawing_ ? left + width + 8 : 0;
    ImGui::SetNextWindowPos(ImVec2(scene_left, 0), ImGuiCond_Always);
    ImGui::SetNextWindowSize(
        ImVec2(std::max(1.0f, rail_drawing_ ? io.DisplaySize.x - scene_left : left - 8),
               io.DisplaySize.y),
        ImGuiCond_Always);
    ImGui::Begin("##object-canvas", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoBackground |
                     ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings |
                     ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoNavFocus |
                     ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::SetCursorPos(ImVec2(0, 0));
    ImGui::InvisibleButton("##object-drop", ImGui::GetWindowSize(),
                           ImGuiButtonFlags_MouseButtonLeft);
    const bool canvas_hovered = ImGui::IsItemHovered();
    const bool in_scene =
        rail_drawing_ ? io.MousePos.x > left + width + 8 : io.MousePos.x < left - 8;

    // Drop a library row onto the scene to place it at the drop point.
    if (ImGui::BeginDragDropTarget()) {
        if (const ImGuiPayload* payload = ImGui::AcceptDragDropPayload(kObjectPayload)) {
            const int library = *static_cast<const int*>(payload->Data);
            if (library >= 0 && library < int(kLibrary.size()) && in_scene) {
                std::array<double, 3> point{};
                if (resolve_place(view, io.MousePos, state.object_distance, point)) {
                    CameraPose token{};
                    token[0] = point[0];
                    token[1] = point[1];
                    token[2] = point[2];
                    editor_enqueue(EditorAction::ObjectPlace, double(library), &token);
                }
            }
        }
        ImGui::EndDragDropTarget();
    }

    // Screen positions of each object's centre, for picking and gizmos.
    std::array<VisualizationPoint, kEditorObjectCount> centres{};
    std::array<bool, kEditorObjectCount> on_screen{};
    for (std::uint32_t index = 0; index < state.object_count; ++index) {
        const auto& item = state.object_items[index];
        const std::array<double, 3> origin{double(item.position[0]), double(item.position[1]),
                                           double(item.position[2])};
        on_screen[index] = project_visualization_point(view, origin, centres[index]);
    }

    // Gizmo interaction (drag the selected object). Move uses the X axis by
    // default; rotate/scale use the screen delta directly. The transform is
    // previewed live and committed once on release.
    const bool selected_valid =
        state.object_selected >= 0 && state.object_selected < int(state.object_count);
    if (g_gizmo.active) {
        if (ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            const double dx = io.MousePos.x - g_gizmo.start_mouse.x;
            const double dy = io.MousePos.y - g_gizmo.start_mouse.y;
            CameraPose token{};
            token[0] = g_gizmo.base_position[0];
            token[1] = g_gizmo.base_position[1];
            token[2] = g_gizmo.base_position[2];
            token[3] = g_gizmo.base_angles[0];
            token[4] = g_gizmo.base_angles[1];
            token[5] = g_gizmo.base_angles[2];
            token[6] = g_gizmo.base_scale;
            if (g_gizmo.mode == 0) {
                std::array<double, 3> delta{};
                if (object_move_in_view_plane(view, g_gizmo.base_position, dx, dy, delta)) {
                    token[0] += delta[0];
                    token[1] += delta[1];
                    token[2] += delta[2];
                }
                if (g_snap_enabled && g_snap_size > 0) {
                    std::array<double, 3> snapped{token[0], token[1], token[2]};
                    if (object_snap_point(snapped, double(g_snap_size))) {
                        token[0] = snapped[0];
                        token[1] = snapped[1];
                        token[2] = snapped[2];
                    }
                }
            } else if (g_gizmo.mode == 1) {
                token[4] = object_rotate_from_drag(g_gizmo.base_angles[1], dx);
                if (g_snap_angle_enabled && g_snap_angle > 0)
                    token[4] = object_snap_angle(token[4], double(g_snap_angle));
            } else {
                token[6] = object_scale_from_drag(g_gizmo.base_scale, dy, 0.05, 20.0);
            }
            // Keep the live transform in the gizmo state; the project is not
            // touched until release, and the draw loop reads the live values.
            g_gizmo.live_position = {token[0], token[1], token[2]};
            g_gizmo.live_angles = {token[3], token[4], token[5]};
            g_gizmo.live_scale = token[6];
        } else {
            // Release: commit the final transform once through the editor.
            CameraPose token{};
            token[0] = g_gizmo.live_position[0];
            token[1] = g_gizmo.live_position[1];
            token[2] = g_gizmo.live_position[2];
            token[3] = g_gizmo.live_angles[0];
            token[4] = g_gizmo.live_angles[1];
            token[5] = g_gizmo.live_angles[2];
            token[6] = g_gizmo.live_scale;
            editor_enqueue(EditorAction::ObjectTransform, double(g_gizmo.index), &token);
            g_gizmo.active = false;
        }
    } else if (selected_valid && canvas_hovered && in_scene &&
               ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
        // Start a gizmo drag only when the click lands on the selected object's
        // centre; otherwise the click is a new placement.
        const auto& centre = centres[state.object_selected];
        const double px = io.MousePos.x - centre.x, py = io.MousePos.y - centre.y;
        const double hit = std::max(16.0, 14.0 * double(panel_scale));
        if (on_screen[state.object_selected] && px * px + py * py <= hit * hit) {
            const auto& item = state.object_items[state.object_selected];
            g_gizmo.active = true;
            g_gizmo.index = state.object_selected;
            g_gizmo.mode = g_gizmo_mode;
            g_gizmo.base_position = {double(item.position[0]), double(item.position[1]),
                                     double(item.position[2])};
            g_gizmo.base_angles = {double(item.angles[0]), double(item.angles[1]),
                                   double(item.angles[2])};
            g_gizmo.base_scale = double(item.scale);
            g_gizmo.live_position = g_gizmo.base_position;
            g_gizmo.live_angles = g_gizmo.base_angles;
            g_gizmo.live_scale = g_gizmo.base_scale;
            g_gizmo.start_mouse = io.MousePos;
        } else {
            std::array<double, 3> point{};
            if (resolve_place(view, io.MousePos, state.object_distance, point)) {
                CameraPose token{};
                token[0] = point[0];
                token[1] = point[1];
                token[2] = point[2];
                editor_enqueue(EditorAction::ObjectPlace, double(active_library), &token);
            }
        }
    }

    auto* draw = ImGui::GetBackgroundDrawList();
    for (std::uint32_t index = 0; index < state.object_count; ++index) {
        // While dragging the selected object, draw its live (uncommitted)
        // transform instead of the stored one.
        EditorObjectItem item = state.object_items[index];
        if (g_gizmo.active && g_gizmo.index == int(index)) {
            item.position[0] = float(g_gizmo.live_position[0]);
            item.position[1] = float(g_gizmo.live_position[1]);
            item.position[2] = float(g_gizmo.live_position[2]);
            item.angles[0] = float(g_gizmo.live_angles[0]);
            item.angles[1] = float(g_gizmo.live_angles[1]);
            item.angles[2] = float(g_gizmo.live_angles[2]);
            item.scale = float(g_gizmo.live_scale);
        } else {
            // Drop animation: raise the object while it is still falling.
            const double offset = drop_z_offset(int(index));
            if (offset != 0.0)
                item.position[2] = float(double(item.position[2]) + offset);
        }
        object_runtime::ObjectSegment segments[object_runtime::kMaxSegmentsPerObject];
        std::size_t written = 0;
        if (!object_runtime::build_proxy(item, segments, object_runtime::kMaxSegmentsPerObject,
                                         written))
            continue;
        const bool selected = int(index) == state.object_selected;
        const ImU32 color = selected ? IM_COL32(153, 247, 216, 255) : IM_COL32(146, 204, 192, 200);
        for (std::size_t s = 0; s < written; ++s) {
            VisualizationPoint a{}, b{};
            if (project_visualization_line(view, segments[s].a, segments[s].b, a, b))
                draw->AddLine(ImVec2(a.x, a.y), ImVec2(b.x, b.y), color,
                              selected ? 2.0f * panel_scale : 1.4f * panel_scale);
        }
        // Selection ring at the object centre (also the gizmo hit target).
        if (selected && on_screen[index]) {
            const ImVec2 c(centres[index].x, centres[index].y);
            draw->AddCircle(c, 12 * panel_scale, IM_COL32(153, 247, 216, 255), 24, 2 * panel_scale);
        }
    }
    ImGui::End();
}

} // namespace dolly
