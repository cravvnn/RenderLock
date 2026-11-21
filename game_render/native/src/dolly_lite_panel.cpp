#include "dolly_overlay_panel.hpp"
#include "dolly_rail_design.hpp"
#include "dolly_lite.hpp"
#include "dolly_replay_events.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_ui_tokens_generated.hpp"
#include "imgui_internal.h"
#include <shellapi.h>
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <climits>
namespace dolly {
bool lite_mode_requested() noexcept {
    // Historical internal name: there is now one standard Dolly interface.
    // Keep the camera transport ABI and authored data compatible.
    return true;
}
static bool camera_request(EditorAction action, double value, const EditorCameraList& list) {
    CameraPose request{};
    request[0] = list.revision;
    return editor_enqueue(action, value, &request);
}
bool OverlayPanel::shader_notice_visible(const EditorSnapshot& state) const noexcept {
    EditorLiteReplayInfo info{};
    return state.focused && editor_lite_replay_info(info) && (info.flags & 4);
}
void OverlayPanel::draw_shader_notice() {
    if(rail_enabled_ && rail_font_) {
        namespace k=rail_kit;namespace t=rail_theme;
        if(!rail_design_)rail_design_=std::make_shared<RailDesignState>();
        k::Scope scope(rail_design_->kit);k::SetFonts(rail_font_,rail_medium_,rail_small_,rail_chip_,rail_heading_,rail_font_dpi_);
        const auto display=ImGui::GetIO().DisplaySize;const float scale=std::min(display.x/1920.f,display.y/1080.f);k::SetScale(scale);
        const ImVec2 a((display.x-420*scale)*.5f,display.y*.75f-110*scale),b(a.x+420*scale,a.y+110*scale);
        ImGui::SetNextWindowPos(a);ImGui::SetNextWindowSize(ImVec2(420*scale,110*scale));
        ImGui::Begin("##shader-loading",nullptr,ImGuiWindowFlags_NoDecoration|ImGuiWindowFlags_NoInputs|ImGuiWindowFlags_NoSavedSettings|ImGuiWindowFlags_NoBackground);
        auto* dl=ImGui::GetWindowDrawList();k::DropShadow(dl,ImRect(a,b),6,ImVec4(0,0,0,.45f),24,8);
        dl->AddRectFilled(a,b,t::U32(t::bg_surface),6*scale);dl->AddRect(a,b,t::U32(t::border_default),6*scale,0,scale);
        dl->AddRectFilled(ImVec2(a.x+188*scale,a.y+6*scale),ImVec2(a.x+232*scale,a.y+8*scale),t::U32(t::accent_default),scale);
        k::BeginRegion(a,420);k::At(21,21);k::Text("Deadlock Dolly",t::text_muted,k::Role::BodyMedium,16);
        auto* title=rail_loading_title_?rail_loading_title_:rail_heading_;
        const float size=title->FontSize/rail_font_dpi_*scale,base=a.y+(43+26*.5f)*scale+(title->Ascent+title->Descent)*size/title->FontSize*.5f;
        dl->AddText(title,size,ImVec2(a.x+21*scale,base-title->Ascent*size/title->FontSize),t::U32(t::text_primary),"Loading shaders");
        k::At(21,75);k::Text("Your replay will open automatically.",t::text_muted,k::Role::Body,16);k::EndRegion();ImGui::End();return;
    }
    const auto size = ImGui::GetIO().DisplaySize;
    const float s = panel_scale;
    const float inset = 24 * s;
    ImGui::SetNextWindowPos(ImVec2(size.x * .5f, size.y * .75f), ImGuiCond_Always, ImVec2(.5f, 1));
    ImGui::SetNextWindowSize(ImVec2(std::min(420 * s, std::max(1.f, size.x - 2 * inset)), 0));
    ImGui::SetNextWindowBgAlpha(1.f);
    ImGui::PushStyleColor(ImGuiCol_WindowBg, lite_color(ui::PANEL));
    ImGui::PushStyleColor(ImGuiCol_Border, lite_color(ui::EDGE));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(18 * s, 16 * s));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 6 * s);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, s);
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(8 * s, 8 * s));
    ImGui::Begin("##shader-loading", nullptr, ImGuiWindowFlags_NoDecoration |
        ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoInputs |
        ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing);
    const auto position = ImGui::GetWindowPos();
    const float middle = position.x + ImGui::GetWindowSize().x * .5f;
    ImGui::GetWindowDrawList()->AddRectFilled(
        ImVec2(middle - 22 * s, position.y + 5 * s),
        ImVec2(middle + 22 * s, position.y + 7 * s),
        ImGui::GetColorU32(lite_color(ui::ACCENT)), s);
    ImGui::PushFont(heading_font);
    ImGui::PushStyleColor(ImGuiCol_Text, lite_color(ui::ACCENT));
    ImGui::TextUnformatted("Deadlock Dolly");
    ImGui::PopStyleColor();
    ImGui::PopFont();
    ImGui::PushFont(title_font);
    ImGui::TextUnformatted("Loading shaders");
    ImGui::PopFont();
    ImGui::PushStyleColor(ImGuiCol_Text, lite_color(ui::MUTED));
    ImGui::PushTextWrapPos(0);
    ImGui::TextUnformatted("Your replay will open automatically.");
    ImGui::PopTextWrapPos();
    ImGui::PopStyleColor();
    ImGui::End();
    ImGui::PopStyleVar(4);
    ImGui::PopStyleColor(2);
}
bool OverlayPanel::lite_campaths_visible(const EditorSnapshot& state) const noexcept {
    return lite_mode_ && state.enabled && state.focused && state.owner == EditorOwner::Panel;
}
void OverlayPanel::cancel_position_graph() noexcept {
    clear_curve_preview();
    position_graph_dragging_ = position_value_editing_ = false;
    tangent_dragging_ = tangent_value_editing_ = false;
    position_graph_escape_ = true;
    curve_panning_ = false;
}
void OverlayPanel::draw_lite_campaths(const EditorSnapshot& state) {
    using namespace ImGui;
    auto& io = GetIO();
    float s = panel_scale;
    if (!lite_campaths_visible(state)) {
        cancel_position_graph();
        return;
    }
    const auto recording = editor_recording_status();
    const bool recording_locked = editor_recording_locks_cameras(recording.state);
    const bool compact = io.DisplaySize.x < 900 * s || io.DisplaySize.y < 650 * s;
    // The compact camera list can be closed while the independent Curves dock
    // remains visible; hiding the list must not cancel a dock gesture.
    if (!rail_drawing_ && compact && state.owner == EditorOwner::Panel && !compact_paths_) return;
    EditorCameraList list{};
    EditorLiteReplayInfo info{};
    bool have_list = editor_camera_list(list) && list.total == state.camera_count;
    bool have_info = editor_lite_replay_info(info);
    bool matching = have_info && have_list && info.revision == list.revision &&
                    info.first == list.first && info.count == list.count;
    auto flags = ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse;
    if (!layout_editing_) flags |= ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove;
    if (state.owner != EditorOwner::Panel)
        flags |= ImGuiWindowFlags_NoInputs;
    if (have_list) {
        bool just_added = false;
        const bool changed_block = lite_arrive_first_ != list.first;
        if (lite_arrive_revision_ != list.revision || lite_arrive_first_ != list.first) {
            if (lite_arrive_first_ != list.first) {
                std::fill(std::begin(lite_arrive_editing_), std::end(lite_arrive_editing_), false);
                std::fill(std::begin(lite_bank_editing_),std::end(lite_bank_editing_),false);
                bank_release_pending_=false;
            }
            lite_arrive_revision_ = list.revision;
            lite_arrive_first_ = list.first;
            for (unsigned i = 0; i < list.count; ++i) {
                if (!lite_arrive_editing_[i])
                    lite_arrive_drafts_[i] = float(list.rows[i].time);
                if (!lite_bank_editing_[i]) lite_bank_drafts_[i] = float(list.rows[i].roll);
            }
        }
        const float usable_bottom = io.DisplaySize.y - lite_timeline_height() - 32 * s;
        const bool default_sidebar=!layout_custom_ || (layout_reset_&2);
        const float maximum_height=default_sidebar ? std::max(1.f,usable_bottom-layout_top_inset()) :
            curve_view_ ? std::max(1.f,usable_bottom-16*s) : std::max(220*s,usable_bottom-16*s);
        const bool full_width = compact && state.owner == EditorOwner::Panel;
        const float camera_width=CalcTextSize("Camera 64").x+6*s;
        const float arrive_width=std::max(CalcTextSize("Arrive / s").x,CalcTextSize("9999.99").x)+6*s;
        const float aspect_width=CalcTextSize("Aspect").x+6*s;
        const float bank_width=std::max(CalcTextSize("Bank").x,CalcTextSize("-180.0").x)+6*s;
        const float minimum_width=32*s+camera_width+arrive_width+aspect_width+bank_width;
        const float panel_width = full_width ? io.DisplaySize.x - 32 * s : std::max(252*s,minimum_width);
        const float controls_height = 4 * (GetFrameHeight() + 6 * s) + 6 * s + 1;
        const float header_height = GetTextLineHeight() + 4 * s;
        const float row_height = GetTextLineHeight() + 8 * s;
        const float minimum_height = 280*s;
        const float panel_height = full_width ? maximum_height :
            std::min(maximum_height, list.total ? std::clamp(64 * s + controls_height + header_height + row_height * list.count,
                                                           minimum_height, 510 * s) : 320 * s);
        // Anchor before Begin: moving an auto-sized window after Begin shifts
        // its content, but leaves the background and clip rectangles behind.
        const auto* previous_sidebar=FindWindowByName("##lite-campaths");
        const bool graph_resize=false;
        const ImVec2 position(16*s,layout_top_inset());
        const bool focus_layout = !layout_custom_;
        SetNextWindowPos(outer_position_pending_[0] ? outer_position_[0] : position, (outer_position_pending_[0] || focus_layout || graph_resize || (layout_reset_ & 2)) ? ImGuiCond_Always : ImGuiCond_FirstUseEver);
        outer_position_pending_[0]=false;
        // Adopt the narrower default once for older layouts, preserving their
        // position and height. Subsequent user widths remain authoritative.
        if(!sidebar_width_current_ && !full_width && !layout_custom_) {sidebar_width_pending_=panel_width;sidebar_width_current_=true;MarkIniSettingsDirty();}
        SetNextWindowSize(ImVec2(sidebar_width_pending_>=0 ? sidebar_width_pending_ : graph_resize && layout_custom_ ? previous_sidebar->Size.x : panel_width,
                                graph_resize && layout_custom_ ? std::min(minimum_height,maximum_height) :
                                sidebar_width_pending_>=0 && previous_sidebar ? previous_sidebar->Size.y : panel_height),
                         (sidebar_width_pending_>=0 || focus_layout || graph_resize || (layout_reset_ & 2)) ? ImGuiCond_Always : ImGuiCond_FirstUseEver);
        sidebar_width_pending_=-1;
        layout_reset_ &= ~2u;
        SetNextWindowSizeConstraints(ImVec2(std::min(minimum_width, panel_width), std::min(280 * s, maximum_height)),
                                    ImVec2(full_width ? panel_width : std::min(360*s,io.DisplaySize.x - 32 * s), maximum_height));
        PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(8 * s, 12 * s));
        PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(8 * s, 6 * s));
        PushStyleVar(ImGuiStyleVar_WindowRounding, 6 * s);
        PushStyleVar(ImGuiStyleVar_FrameRounding, 4 * s);
        PushStyleVar(ImGuiStyleVar_FrameBorderSize, 0);
        if (rail_drawing_) {
            SetNextWindowPos(rail_paths_position_,ImGuiCond_Always);
            SetNextWindowSize(rail_paths_size_,ImGuiCond_Always);
            SetNextWindowSizeConstraints(ImVec2(1,1),rail_paths_size_);
        }
        Begin("##lite-campaths", nullptr, flags);
        if (rail_drawing_) SetWindowFontScale(12*s/ImGui::GetFont()->FontSize);
        else { constrain_panel(); panel_handle("Drag campaths"); }
        if (have_camera_total_ && list.total > previous_camera_total_) {
            animated_camera_ = state.selected_camera < list.total ? state.selected_camera : list.total - 1;
            camera_added_at_ = GetTime();
            just_added = true;
            if (animated_camera_ >= list.first + list.count)
                camera_request(EditorAction::CameraPage, std::min((animated_camera_ / kEditorCameraListCount) * kEditorCameraListCount,
                    list.total > kEditorCameraListCount ? list.total - kEditorCameraListCount : 0u), list);
        }
        previous_camera_total_ = list.total; have_camera_total_ = true;
        // Keep the original live bank-preview behavior, with one outstanding
        // revision-bound change. A release waits for the preceding revision.
        if (bank_release_pending_ && (recording_locked || state.busy || state.playing || !state.paused || state.attach_preview ||
            bank_release_index_<list.first || bank_release_index_>=list.first+list.count ||
            list.rows[bank_release_index_-list.first].time!=bank_anchor_time_))bank_release_pending_=false;
        if (bank_release_pending_ && list.revision != bank_sent_revision_) {
            CameraPose request{};request[0]=list.revision;request[1]=bank_release_value_;
            editor_enqueue(EditorAction::SetCameraRoll,bank_release_index_,&request);
            bank_release_pending_=false;
        }
        TextColored(lite_color(rail_drawing_ ? ui::STRONG : ui::ACCENT), rail_drawing_ ? "Campaths" : "CAMPATHS");
        SameLine();
        char count[32];
        std::snprintf(count, sizeof(count), "%u PLACED", list.total);
        SetCursorPosX(GetWindowContentRegionMax().x - CalcTextSize(count).x);
        TextDisabled("%s", count);
        if (compact && state.owner == EditorOwner::Panel) {
            if (Button("Tools")) { compact_paths_ = false; save_layout(); }
            SameLine(); layout_controls();
        }
        Separator();
        const auto sidebar_id=GetCurrentWindow()->ID;
        const float footer = 4 * GetFrameHeightWithSpacing() + GetStyle().ItemSpacing.y + 1;
        const float minimum_body=footer+header_height+row_height+24*s+
            (recording_locked ? 2*GetTextLineHeightWithSpacing() : 0);
        const bool scroll_body=default_sidebar && GetContentRegionAvail().y<minimum_body;
        if(scroll_body) {
            SetNextWindowContentSize(ImVec2(0,minimum_body));
            BeginChild("##campaths-content",ImVec2(0,0),ImGuiChildFlags_None,ImGuiWindowFlags_NoBackground);
            // Preserve every historical camera/control ID inside the scroller.
            PushOverrideID(sidebar_id);
        }
        if (recording_locked)
            TextWrapped(recording.state == video::State::finalizing ?
                        "Saving video. Please wait." :
                        "Finish recording before editing cameras.");
        const float body_height=scroll_body ? std::max(GetWindowSize().y,minimum_body) : GetWindowSize().y;
        const float row_area = std::max(row_height, (scroll_body ? minimum_body : GetContentRegionAvail().y) - footer - header_height);
        const unsigned rows = std::max(1u, unsigned(row_area / row_height));
        if (camera_pending_first_ != ~0u && (list.revision != camera_pending_revision_ ||
                                            GetTime() - camera_pending_at_ > 2.))
            camera_pending_first_ = ~0u;
        const bool requested_page = camera_pending_first_ == list.first;
        if (requested_page) {
            camera_row_page_ = camera_pending_page_;
            camera_pending_first_ = ~0u;
        } else if (changed_block) camera_pending_first_ = ~0u;
        if (!requested_page && (just_added || changed_block) && state.selected_camera >= list.first &&
            state.selected_camera < list.first + list.count)
            camera_row_page_ = (state.selected_camera - list.first) / rows;
        camera_row_page_ = std::min(camera_row_page_, (std::max(1u, list.count) - 1) / rows);
        // Fill the tail from the preceding rows rather than leaving one camera
        // stranded above an empty sidebar after capture selects the last page.
        const unsigned visible_begin = std::min(camera_row_page_ * rows, list.count > rows ? list.count - rows : 0u);
        const unsigned visible_end = std::min(list.count, visible_begin + rows);
        if (list.count) {
            PushStyleVar(ImGuiStyleVar_CellPadding,ImVec2(2*s,2*s));
            PushStyleColor(ImGuiCol_TableHeaderBg,lite_color(ui::BUTTON));
            const auto row_seed=sidebar_id;
            PushStyleVar(ImGuiStyleVar_CellPadding,ImVec2(2*s,2*s));
            if (BeginTable("##lite-campath-details",4,ImGuiTableFlags_BordersInnerV |
                           ImGuiTableFlags_RowBg | ImGuiTableFlags_SizingStretchProp)) {
                TableSetupColumn("Camera",ImGuiTableColumnFlags_WidthStretch,camera_width);
                TableSetupColumn("Arrive / s",ImGuiTableColumnFlags_WidthStretch,arrive_width);
                TableSetupColumn("Aspect",ImGuiTableColumnFlags_WidthStretch,aspect_width);
                TableSetupColumn("Bank",ImGuiTableColumnFlags_WidthStretch,bank_width);
                TableHeadersRow();
                for (unsigned i = visible_begin; i < visible_end; ++i) {
                    const unsigned index=list.first+i;
                    const auto& camera=list.rows[i];
                    const bool selected=index==state.selected_camera;
                    TableNextRow(ImGuiTableRowFlags_None,row_height);
                    if(selected)TableSetBgColor(ImGuiTableBgTarget_RowBg0,ImColor(lite_color(ui::SELECTED)));
                    TableSetColumnIndex(0);
                    // Keep camera/field IDs stable when changing their presentation.
                    PushOverrideID(row_seed);PushID(int(index));
                    const auto cell=GetCursorScreenPos();
                    SetNextItemAllowOverlap();
                    const bool clicked=Selectable("##camera",false,ImGuiSelectableFlags_AllowDoubleClick,
                                                   ImVec2(0,row_height-4*s));
                    if(clicked && state.ready && !state.busy && !state.playing && !recording_locked)
                        camera_request(IsMouseDoubleClicked(ImGuiMouseButton_Left) ? EditorAction::ViewCamera : EditorAction::SelectCamera,index,list);
                    const float slide=index==animated_camera_ ? 4*s*(1-motion_progress(camera_added_at_,.28)) : 0;
                    SetCursorScreenPos(ImVec2(cell.x+slide,cell.y+2*s));
                    Text("Camera %02u",index+1);
                    if(IsItemHovered() && matching && (info.flags&1))SetTooltip("Replay tick %lld",static_cast<long long>(info.ticks[i]));
                    TableSetColumnIndex(1);
                    BeginDisabled(state.owner != EditorOwner::Panel || !state.ready || !state.paused ||
                                  state.busy || state.playing || state.attach_preview || recording_locked);
                    PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(2 * s, 2 * s));
                    PushStyleVar(ImGuiStyleVar_FrameBorderSize, 0);
                    PushStyleColor(ImGuiCol_FrameBg, ImVec4(0, 0, 0, 0));
                    PushStyleColor(ImGuiCol_FrameBgHovered, lite_color(ui::FIELD));
                    PushStyleColor(ImGuiCol_FrameBgActive, lite_color(ui::FIELD));
                    SetNextItemWidth(-1);
                    DragFloat("##arrive", &lite_arrive_drafts_[i], .01f, 0.f, 1e9f, "%.2f",
                              ImGuiSliderFlags_AlwaysClamp);
                    if (IsItemActivated())
                        lite_arrive_edit_revisions_[i] = list.revision;
                    lite_arrive_editing_[i] = IsItemActive();
                    const bool arrival_edited = IsItemDeactivatedAfterEdit();
                    if (IsItemHovered())
                        SetTooltip("Camera %02u arrival. Drag to adjust; Ctrl+click to type.",
                                   index + 1);
                    if (arrival_edited && !recording_locked) {
                        CameraPose request{};
                        request[0] = lite_arrive_edit_revisions_[i];
                        request[1] = lite_arrive_drafts_[i];
                        request[2] = 1; // Authoritative dispatch snaps to the replay clock.
                        if (!editor_enqueue(EditorAction::SetCameraTime, index, &request))
                            lite_arrive_drafts_[i] = float(camera.time);
                    }
                    PopStyleColor(3);
                    PopStyleVar(2);
                    EndDisabled();
                    TableSetColumnIndex(2);Text("%.2f",camera.aspect);
                    TableSetColumnIndex(3);
                    BeginDisabled(state.owner != EditorOwner::Panel || !state.ready || !state.paused ||
                                  state.busy || state.playing || state.attach_preview || recording_locked);
                    SetNextItemWidth(-1);
                    PushStyleVar(ImGuiStyleVar_FramePadding,ImVec2(2*s,2*s));
                    PushStyleColor(ImGuiCol_FrameBg,ImVec4(0,0,0,0));
                    DragFloat("##bank",&lite_bank_drafts_[i],.25f,-180,180,"%.1f",ImGuiSliderFlags_AlwaysClamp);
                    bool bank_active=IsItemActive(), bank_commit=IsItemDeactivatedAfterEdit();
                    lite_bank_editing_[i]=bank_active;
                    if (recording_locked) {
                        bank_active = bank_commit = false;
                        lite_bank_editing_[i] = false;
                        bank_release_pending_ = false;
                        lite_bank_drafts_[i] = float(camera.roll);
                    }
                    if (IsItemActivated()) {bank_sent_revision_=0;bank_release_pending_=false;bank_anchor_time_=camera.time;}
                    if ((bank_active || bank_commit) && camera.time != bank_anchor_time_) {
                        ClearActiveID();
                        bank_active = bank_commit = false;
                        lite_bank_editing_[i] = false;
                        bank_release_pending_ = false;
                        lite_bank_drafts_[i] = float(camera.roll);
                    }
                    if ((bank_commit || (bank_active && std::abs(lite_bank_drafts_[i]-camera.roll)>.001)) && list.revision != bank_sent_revision_) {
                        CameraPose request{};request[0]=list.revision;request[1]=lite_bank_drafts_[i];
                        if (editor_enqueue(EditorAction::SetCameraRoll,index,&request)) {
                            bank_sent_revision_=list.revision;bank_sent_value_=lite_bank_drafts_[i];
                        } else lite_bank_drafts_[i]=float(camera.roll);
                    } else if (bank_commit && lite_bank_drafts_[i]!=bank_sent_value_) {
                        bank_release_pending_=true;bank_release_index_=index;bank_release_value_=lite_bank_drafts_[i];
                    }
                    if (IsItemHovered())SetTooltip("Camera %02u bank. Drag to rotate; Ctrl+click to type.",index+1);
                    PopStyleColor();PopStyleVar();EndDisabled();
                    PopID();PopID();
                }
                EndTable();
            }
            PopStyleVar();
            PopStyleColor();PopStyleVar();
        } else {
            Spacing(); Spacing();
            TextColored(lite_color(ui::TEXT), "Your first camera starts here");
            TextWrapped("Frame a view, then capture it. Add another view to create a cinematic path.");
            Spacing();
            BeginDisabled(!state.ready || !state.paused || state.busy || state.playing || state.attach_preview || recording_locked);
            action_button("Capture camera", EditorAction::Capture, -1, 0, true);
            EndDisabled();
        }
        SetCursorPosY(std::max(GetCursorPosY(),body_height-footer-12*s));
        BeginDisabled(!state.ready || !state.paused || state.busy || state.playing ||
                      state.attach_preview || recording_locked);
        const unsigned row_first = list.first + visible_begin;
        BeginDisabled(row_first == 0 || camera_pending_first_ != ~0u);
        if (ArrowButton("##previous-cameras", ImGuiDir_Left)) {
            if (camera_row_page_) --camera_row_page_;
            else if (list.first) {
                camera_pending_first_ = ((list.first - 1) / kEditorCameraListCount) * kEditorCameraListCount;
                camera_pending_page_ = (list.first - camera_pending_first_ - 1) / rows;
                camera_pending_revision_ = list.revision;
                camera_pending_at_ = GetTime();
                if (!camera_request(EditorAction::CameraPage, camera_pending_first_, list)) camera_pending_first_ = ~0u;
            }
        }
        EndDisabled(); SameLine();
        if (list.total) TextDisabled("%u-%u of %u", row_first + 1,
                                    std::min({list.total, list.first + list.count, row_first + rows}), list.total);
        else TextDisabled("0 cameras");
        SameLine(GetWindowContentRegionMax().x - GetFrameHeight());
        BeginDisabled(row_first + rows >= list.total || camera_pending_first_ != ~0u);
        if (ArrowButton("##next-cameras", ImGuiDir_Right)) {
            if ((camera_row_page_ + 1) * rows < list.count) ++camera_row_page_;
            else {
                camera_pending_first_ = std::min(((list.first / kEditorCameraListCount) + 1) * kEditorCameraListCount,
                    list.total > kEditorCameraListCount ? list.total - kEditorCameraListCount : 0u);
                camera_pending_page_ = (list.first + list.count - camera_pending_first_) / rows;
                camera_pending_revision_ = list.revision;
                camera_pending_at_ = GetTime();
                if (!camera_request(EditorAction::CameraPage,camera_pending_first_,list)) camera_pending_first_ = ~0u;
            }
        }
        EndDisabled();
        EndDisabled();
        Separator();
        BeginDisabled(!state.ready || !state.paused || state.busy || state.playing || state.attach_preview || recording_locked);
        float history_half = (GetContentRegionAvail().x - 8*s)*.5f;
        BeginDisabled(!(list.flags&1));
        if(Button("Undo",ImVec2(history_half,0)))camera_request(EditorAction::UndoShot,0,list);
        EndDisabled();SameLine();BeginDisabled(!(list.flags&2));
        if(Button("Redo",ImVec2(history_half,0)))camera_request(EditorAction::RedoShot,0,list);
        EndDisabled();
        action_button("Save Path", EditorAction::SaveShot, GetContentRegionAvail().x, 0, true);
        float half = (GetContentRegionAvail().x - 8 * s) * .5f;
        BeginDisabled(state.selected_camera >= list.total);
        if (Button("Delete", ImVec2(half, 0)))
            camera_request(EditorAction::DeleteCamera, state.selected_camera, list);
        EndDisabled();
        SameLine();
        BeginDisabled(!list.total);
        if (Button("Clear All", ImVec2(half, 0))) {
            lite_clear_revision_ = list.revision;
            OpenPopup("Clear cameras?");
        }
        EndDisabled();
        EndDisabled();
        if (BeginPopupModal("Clear cameras?", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
            TextUnformatted("Remove every camera from this shot?");
            const bool changed = list.revision != lite_clear_revision_;
            if (changed)
                TextUnformatted("Path changed. Cancel and review it again.");
            const bool can_clear = !changed && state.owner == EditorOwner::Panel && state.ready &&
                                   state.paused && !state.busy && !state.playing && !state.attach_preview && !recording_locked;
            BeginDisabled(!can_clear);
            // Keep dispatch bound to the path that opened this confirmation,
            // including programmatic keyboard activation of a disabled item.
            if (Button("Clear") && can_clear && camera_request(EditorAction::ClearCameraPath, 0, list))
                CloseCurrentPopup();
            EndDisabled();
            SameLine();
            if (Button("Cancel"))
                CloseCurrentPopup();
            EndPopup();
        }
        if(scroll_body) { PopID(); EndChild(); }
        if(layout_editing_ && !full_width) {
            const auto cursor=GetCursorScreenPos(),pos=GetWindowPos();
            SetCursorScreenPos(ImVec2(pos.x+GetWindowSize().x-6*s,pos.y+30*s));
            InvisibleButton("##campath-width",ImVec2(6*s,std::max(20*s,GetWindowSize().y-60*s)));
            if(IsItemHovered() || IsItemActive())SetMouseCursor(ImGuiMouseCursor_ResizeEW);
            if(IsItemHovered())SetTooltip("Drag to adjust sidebar width");
            if(IsItemActivated()) {sidebar_resize_width_=GetWindowSize().x;sidebar_resize_mouse_=io.MousePos;}
            if(IsItemActive())sidebar_width_pending_=std::clamp(sidebar_resize_width_+io.MousePos.x-sidebar_resize_mouse_.x,minimum_width,std::min(360*s,io.DisplaySize.x-32*s));
            if(IsItemDeactivated())save_layout();
            GetWindowDrawList()->AddLine(ImVec2(pos.x+GetWindowSize().x-3*s,pos.y+GetWindowSize().y*.5f-14*s),ImVec2(pos.x+GetWindowSize().x-3*s,pos.y+GetWindowSize().y*.5f+14*s),GetColorU32(ImGuiCol_SliderGrab),s);
            SetCursorScreenPos(cursor);
        }
        End();
        PopStyleVar(5);
    }
}
void OverlayPanel::draw_lite_timeline(const EditorSnapshot& state) {
    using namespace ImGui;
    auto& io = GetIO();
    const float s = panel_scale;
    if (!lite_campaths_visible(state)) { cancel_position_graph(); return; }
    EditorCameraList list{};
    EditorLiteReplayInfo info{};
    EditorReplayEvents events{};
    const bool have_list = editor_camera_list(list) && list.total == state.camera_count;
    const bool have_info = editor_lite_replay_info(info);
    const bool have_events = editor_replay_events(events);
    const bool kills_ready = have_events && events.status == 2;
    const bool replay_ready = state.ready && have_info && (info.flags & 2) && info.total_ticks > 0;
    const unsigned event_status = have_events ? events.status : 0;
    if (replay_ready != lite_replay_ready_seen_ ||
        (event_status != lite_replay_events_status_seen_ && event_status == 2))
        lite_replay_fit_pending_ = true;
    lite_replay_ready_seen_ = replay_ready;
    lite_replay_events_status_seen_ = event_status;
    if (replay_player_filter_ >= 0 && kills_ready) {
        bool found = false;
        for (unsigned i=0;i<events.player_count;++i) found |= events.players[i].id == unsigned(replay_player_filter_);
        if (!found) replay_player_filter_ = -1;
    }
    auto flags = ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse;
    if (!layout_editing_) flags |= ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove;
    if (state.owner != EditorOwner::Panel) flags |= ImGuiWindowFlags_NoInputs;
    const float width = std::min(1120 * s, io.DisplaySize.x - 32 * s);
    const float height = lite_timeline_height();
    const auto* previous_timeline=FindWindowByName("##lite-timeline");
    const auto* saved_timeline=layout_custom_ && !previous_timeline ?
        FindWindowSettingsByID(ImHashStr("##lite-timeline")) : nullptr;
    const bool have_custom_geometry=previous_timeline ||
        (saved_timeline && saved_timeline->Size.x>0 && saved_timeline->Size.y>0);
    // SizeFull includes a pending restored/programmatic size before Begin.
    const ImVec2 previous_size=previous_timeline ? previous_timeline->SizeFull :
        saved_timeline ? ImVec2(float(saved_timeline->Size.x),float(saved_timeline->Size.y)) : ImVec2();
    const ImVec2 previous_position=previous_timeline ? previous_timeline->Pos :
        saved_timeline ? ImVec2(float(saved_timeline->Pos.x),float(saved_timeline->Pos.y)) : ImVec2();
    const bool fitting_noncurve=!curve_view_ && layout_custom_ && have_custom_geometry &&
        std::abs(previous_size.y-height)>.5f &&
        !(layout_editing_ && IsMouseDown(ImGuiMouseButton_Left));
    const bool curve_resize = curve_resize_pending_ || fitting_noncurve;
    curve_resize_pending_ = false;
    const bool custom_geometry=layout_custom_ && have_custom_geometry && !(layout_reset_&4);
    const float resized_width=custom_geometry ? std::clamp(previous_size.x,std::min(560*s,width),io.DisplaySize.x-32*s) : width;
    const float resized_height=height;
    ImVec2 timeline_position((io.DisplaySize.x-width)*.5f,io.DisplaySize.y-height-16*s);
    if(custom_geometry) {
        // Keep custom horizontal placement. Arrange may temporarily float the
        // timeline while its required content height changes.
        const float margin=12*s;
        timeline_position=ImVec2(
            std::clamp(previous_position.x,margin,std::max(margin,io.DisplaySize.x-resized_width-margin)),
            std::clamp(previous_position.y+previous_size.y-resized_height,
                       margin,std::max(margin,io.DisplaySize.y-resized_height-margin)));
    }
    if(outer_position_pending_[1]) timeline_position=outer_position_[1];
    if(!layout_editing_) {
        const float margin=12*s;
        timeline_position.x=std::clamp(timeline_position.x,margin,std::max(margin,io.DisplaySize.x-resized_width-margin));
        // Apply the dock after pending Arrange geometry so an old saved or
        // dragged Y never leaves a gap on normal frames or viewport changes.
        timeline_position.y=io.DisplaySize.y-resized_height-16*s;
    }
    SetNextWindowPos(timeline_position,
                     (!layout_editing_ || outer_position_pending_[1] || !layout_custom_ || curve_resize || (layout_reset_ & 4)) ? ImGuiCond_Always : ImGuiCond_FirstUseEver);
    outer_position_pending_[1]=false;
    SetNextWindowSize(ImVec2(resized_width,resized_height), (!layout_custom_ || curve_resize || (layout_reset_ & 4)) ? ImGuiCond_Always : ImGuiCond_FirstUseEver);
    layout_reset_ &= ~4u;
    SetNextWindowSizeConstraints(ImVec2(std::min(560 * s, width),height),
                                 ImVec2(io.DisplaySize.x - 32 * s,height));
    const bool compact_curve_frames=curve_view_ && io.DisplaySize.y<650*s;
    if(compact_curve_frames)
        PushStyleVar(ImGuiStyleVar_FramePadding,ImVec2(GetStyle().FramePadding.x,3*s));
    PushStyleColor(ImGuiCol_WindowBg, lite_color(ui::BG));
    if (rail_drawing_) {
        SetNextWindowPos(ImVec2((io.DisplaySize.x-width)*.5f+rail_bar_offset_.x,
            std::clamp(io.DisplaySize.y-height-8*s+rail_bar_offset_.y,0.f,std::max(0.f,io.DisplaySize.y-height))),ImGuiCond_Always);
        SetNextWindowSize(ImVec2(width,height),ImGuiCond_Always);
        flags |= ImGuiWindowFlags_NoSavedSettings;
    }
    Begin("##lite-timeline",nullptr,flags);
    if (rail_drawing_) {
        SetWindowFontScale(12*s/ImGui::GetFont()->FontSize);
        const auto pos=GetWindowPos(),size=GetWindowSize();
        if (rail_background_)
            GetWindowDrawList()->AddImage(rail_background_,pos,ImVec2(pos.x+size.x,pos.y+size.y));
        if (IsWindowHovered() && !IsAnyItemHovered() && IsMouseDragging(0) &&
            !IsAnyItemActive() && !IsPopupOpen(nullptr,ImGuiPopupFlags_AnyPopupId)) {
            rail_bar_offset_.x=std::clamp(rail_bar_offset_.x+io.MouseDelta.x,-(io.DisplaySize.x-width)*.5f,(io.DisplaySize.x-width)*.5f);
            rail_bar_offset_.y+=io.MouseDelta.y;
        }
        if (IsWindowHovered() && IsMouseDoubleClicked(0) && !IsAnyItemHovered()) rail_bar_offset_=ImVec2();
    }
    const bool compact=lite_timeline_compact(GetWindowSize().x) || (!curve_view_ && GetWindowSize().y<150*s);
    if (!rail_drawing_) { constrain_panel(); panel_handle("Drag timeline"); }
    if (curve_view_) {
        draw_curve_workspace(state, list);
        End(); PopStyleColor();
        if(compact_curve_frames) PopStyleVar();
        return;
    }
    cancel_position_graph();
    auto cinematic_button = [&] {
        BeginDisabled(state.camera_count < 2 || !have_list || state.playing || !state.paused || state.attach_preview || cinematic_preparing_);
        PushStyleColor(ImGuiCol_Button, lite_color(ui::ACCENT));
        PushStyleColor(ImGuiCol_Text, lite_color(rail_drawing_ ? ui::ACCENT_INK : ui::KEYCAP_INK));
        if (Button("Play cinematic", ImVec2(132 * s, 0))) {
            EditorCinematicStatus previous{};
            if (editor_cinematic_status(previous) && (!cinematic_token_ ||
                std::int32_t(previous.token - cinematic_token_) > 0)) cinematic_token_ = previous.token;
            if (!++cinematic_token_) ++cinematic_token_;
            CameraPose request{}; request[0] = list.revision;
            if (editor_enqueue(EditorAction::PrepareCinematic, cinematic_token_, &request)) {
                cinematic_revision_ = list.revision;
                cinematic_preparing_ = true;
            }
        }
        PopStyleColor(2);
        if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
            SetTooltip(state.camera_count < 2 ? "Place at least two cameras to play a cinematic." : "Prepare the replay and play the camera path.");
        EndDisabled();
    };
    auto tick_editor = [&] {
        BeginGroup();
        TextDisabled("Tick"); SameLine();
        if (!lite_tick_editing_) std::snprintf(lite_tick_draft_,sizeof(lite_tick_draft_),"%d",state.tick);
        SetNextItemWidth(90*s);
        BeginDisabled(state.playing || state.attach_preview);
        if (InputText("##lite-tick",lite_tick_draft_,sizeof(lite_tick_draft_),ImGuiInputTextFlags_CharsDecimal |
                      ImGuiInputTextFlags_AutoSelectAll | ImGuiInputTextFlags_EnterReturnsTrue)) {
            char* end=nullptr; const auto tick=std::strtoll(lite_tick_draft_,&end,10);
            const auto total=have_info && (info.flags&2) ? info.total_ticks : -1;
            if(end!=lite_tick_draft_ && !*end && valid_lite_seek_tick(double(tick),total))
                editor_enqueue(EditorAction::SeekReplayTick,double(tick));
        }
        lite_tick_editing_=IsItemActive();
        if(IsItemHovered())SetTooltip("Type a replay tick and press Enter to seek.");
        EndDisabled();SameLine();
        if(have_info && (info.flags&2))TextDisabled("/ %lld",static_cast<long long>(info.total_ticks));
        else TextDisabled("/ --");
        EndGroup();
    };
    auto player_filter = [&] {
        if (replay_player_filter_ < 0 && kills_ready)
            for (unsigned i=0;i<events.player_count;++i) if (events.players[i].id) {replay_player_filter_=int(events.players[i].id);break;}
        auto focus = [&] {
            if (state.ready && !state.busy && state.paused && !state.playing && !state.attach_preview)
                editor_enqueue(EditorAction::FocusReplayPlayer, replay_player_filter_);
        };
        auto change = [&](bool forward) {
            int ids[kReplayPlayers]{}; unsigned count=0,current=0;
            for (unsigned i=0;i<events.player_count;++i) if (events.players[i].id) {
                ids[count]=int(events.players[i].id); if(ids[count]==replay_player_filter_)current=count; ++count;
            }
            if (count) { replay_player_filter_=ids[forward ? (current+1)%count : (current+count-1)%count]; focus(); }
        };
        BeginDisabled(!kills_ready || !events.player_count || state.playing || state.attach_preview);
        PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(3*s,3*s));
        PushStyleVar(ImGuiStyleVar_FrameBorderSize,0);
        PushStyleColor(ImGuiCol_Button,ImVec4(0,0,0,0));
        if (ArrowButton("##previous-player",ImGuiDir_Left)) change(false);
        SameLine(0,4*s);
        const char* name = kills_ready && replay_player_filter_>=0 ? replay_player_name(events,replay_player_filter_) : "Players";
        const auto text_size=CalcTextSize(name);
        const float name_width=(compact ? 128.f : 176.f)*s;
        const auto pos=GetCursorScreenPos();
        if (InvisibleButton("##player-name",ImVec2(name_width,GetFrameHeight()))) focus();
        const ImVec2 text_pos(pos.x+std::max(0.f,(name_width-text_size.x)*.5f),pos.y+GetStyle().FramePadding.y);
        RenderTextEllipsis(GetWindowDrawList(),text_pos,ImVec2(pos.x+name_width,pos.y+GetFrameHeight()),
                           pos.x+name_width,pos.x+name_width,name,nullptr,&text_size);
        if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) {
            const char* camera_hint = state.ready ? "Click the name or arrows to follow this player through playback and seeks." : "Camera selection is unavailable at this tick.";
            if (kills_ready && replay_player_filter_ >= 0) {
                unsigned kills = 0, deaths = 0;
                for (unsigned i = 0; i < events.count; ++i) {
                    const auto kind = replay_event_kind(events.kills[i],replay_player_filter_);
                    kills += kind == ReplayEventKind::Kill;
                    deaths += kind == ReplayEventKind::Death;
                }
                SetTooltip("%s\n%u kills | %u deaths (full replay)\nBlue: kills. Red: deaths.\n%s",name,kills,deaths,camera_hint);
            } else
                SetTooltip("%s\n%s",have_events ? events.message : "Reading replay kills...",camera_hint);
        }
        SameLine(0,4*s);
        if (ArrowButton("##next-player",ImGuiDir_Right)) change(true);
        PopStyleColor(); PopStyleVar(2); EndDisabled();
    };
    BeginDisabled(!state.ready || state.busy);
    draw_timeline_modes();
    SameLine();cinematic_button();
    const float player_width = (compact ? 128.f : 176.f)*s + 2*(GetFontSize()+6*s) + 8*s;
    if (compact) SameLine();
    else {
        const float left_end=GetCurrentWindow()->DC.CursorPosPrevLine.x-GetWindowPos().x;
        SameLine(std::max(left_end+GetStyle().ItemSpacing.x,(GetWindowSize().x-player_width)*.5f));
    }
    player_filter();
    if (compact) {
        SameLine();
        if (Button("More")) OpenPopup("##compact-transport");
    } else {
        SameLine(GetWindowContentRegionMax().x - 185 * s);
        TextDisabled("Kills   |   Wheel: zoom");
    }
    char total_label[32];
    if(have_info && (info.flags&2))std::snprintf(total_label,sizeof(total_label),"/ %lld",static_cast<long long>(info.total_ticks));
    else std::snprintf(total_label,sizeof(total_label),"/ --");
    const float tick_width=CalcTextSize("Tick").x+90*s+CalcTextSize(total_label).x+2*GetStyle().ItemSpacing.x;
    const float tick_x=GetWindowContentRegionMax().x-tick_width;
    const float tick_screen_x=GetWindowPos().x+tick_x;
    const float track_width = std::max(80 * s, GetContentRegionAvail().x);
    const auto tick_seed=GetCurrentWindow()->ID;
    PushStyleVar(ImGuiStyleVar_WindowPadding,ImVec2(0,0));
    BeginChild("##lite-shot-track",ImVec2(compact ? std::max(80*s,track_width-tick_width-8*s) : 0,
               (compact ? 42.f : 70.f)*s),ImGuiChildFlags_None,
               ImGuiWindowFlags_NoBackground | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    BeginDisabled(state.playing || state.attach_preview ||
                  (lite_campath_timeline_ ? !state.camera_count : !(have_info && (info.flags&2) && info.total_ticks>0)));
    SetCursorPosY(GetCursorPosY() + (compact ? 20.f : 26.f) * s);
    SliderRow track{};
    if (lite_campath_timeline_) {
        if (!IsAnyItemActive() && (lite_shot_playhead_ != state.playhead || lite_shot_duration_ != state.duration ||
            (have_list && lite_shot_revision_ != list.revision))) {
            lite_shot_time_ = float(std::clamp(state.playhead,0.,state.duration));
            lite_shot_playhead_ = state.playhead;
            lite_shot_duration_ = state.duration;
            lite_shot_revision_ = have_list ? list.revision : 0;
        }
        const auto guides = visualization_snapshot();
        track = slider_row("##lite-shot-position","Shot",&lite_shot_time_,0,
                           float(std::max(state.duration,.001)),"%.3f s",42,guides.get(),true);
        lite_shot_time_ = std::clamp(lite_shot_time_,0.f,float(state.duration));
    } else {
        const auto known_total=have_info && (info.flags&2) ? info.total_ticks : -1;
        if(known_total!=lite_replay_total_seen_ || (replay_ready && lite_replay_fit_pending_)) {
            timeline_view_id_=nullptr;
            lite_replay_total_seen_=known_total;
            if (replay_ready) lite_replay_fit_pending_ = false;
        }
        if (!lite_replay_dragging_) lite_replay_cursor_ = state.tick;
        float cursor = float(lite_replay_cursor_);
        track = slider_row("##lite-replay-position","Tick",&cursor,0,
                           float(have_info ? std::max<std::int64_t>(1,info.total_ticks-1) : 1),nullptr,42,nullptr,true);
        lite_replay_cursor_ = std::int64_t(std::nearbyint(cursor));
        lite_replay_dragging_ = track.active;
    }
    // Replay kills use the same visible track as camera keyframes. Hovering a
    // dense group lists the selected player's kills/deaths; a click seeks one tick.
    int hovered_kill = -1;
    float closest = 8*s;
    const auto mouse = io.MousePos;
    const float span = timeline_view_end_ - timeline_view_start_;
    if (kills_ready && span>0 && (!lite_campath_timeline_ || (have_info && (info.flags&1)))) {
        auto* draw = GetWindowDrawList();
        int last_label_tick = -1;
        for (unsigned i=0;i<events.count;++i) {
            const auto& kill = events.kills[i];
            const auto kind = replay_event_kind(kill,replay_player_filter_);
            if (kind == ReplayEventKind::Hidden) continue;
            const double time = lite_campath_timeline_ ? double(kill.tick-info.start_tick)/info.tick_rate : kill.tick;
            if (time < timeline_view_start_ || time > timeline_view_end_) continue;
            const float x = timeline_x0_ + (timeline_x1_-timeline_x0_)*float((time-timeline_view_start_)/span);
            const auto tint = lite_color(kind == ReplayEventKind::Kill ? 0x65b5ff : 0xff727a);
            // Markers have a separate lane and an outlined stem, so the
            // playhead at the same tick cannot cover the event.
            draw->AddLine(ImVec2(x,timeline_y_-18*s),ImVec2(x,timeline_y_-7*s),IM_COL32(12,16,19,255),5*s);
            draw->AddLine(ImVec2(x,timeline_y_-18*s),ImVec2(x,timeline_y_-7*s),ImColor(tint),3*s);
            if (!compact && span <= 512 && (last_label_tick < 0 || kill.tick - last_label_tick >= span * 70*s/std::max(1.f,timeline_x1_-timeline_x0_))) {
                char tick_label[24]; std::snprintf(tick_label,sizeof(tick_label),"T %d",kill.tick);
                const float tw = CalcTextSize(tick_label).x;
                draw->AddText(ImVec2(std::clamp(x-tw*.5f,timeline_x0_,std::max(timeline_x0_,timeline_x1_-tw)),timeline_y_-39*s),ImColor(tint),tick_label);
                last_label_tick = kill.tick;
            }
            if (state.owner==EditorOwner::Panel && state.ready && !state.busy &&
                // The slider may acquire the click first; a marker then
                // deliberately replaces that gesture. Popups still block it.
                IsWindowHovered(ImGuiHoveredFlags_RootAndChildWindows |
                                ImGuiHoveredFlags_AllowWhenBlockedByActiveItem) &&
                !(GetCurrentContext()->CurrentItemFlags & ImGuiItemFlags_Disabled) &&
                mouse.y>=timeline_y_-28*s && mouse.y<=timeline_y_-7*s && std::abs(mouse.x-x)<=closest) {
                closest=std::abs(mouse.x-x); hovered_kill=int(i);
            }
        }
        if (hovered_kill>=0 && !state.playing && !state.attach_preview) {
            const auto& kill=events.kills[hovered_kill];
            SetMouseCursor(ImGuiMouseCursor_Hand);
            BeginTooltip();
            for (unsigned i=0;i<events.count;++i) {
                const auto& nearby=events.kills[i];
                if (!kill_matches(nearby,replay_player_filter_)) continue;
                const double time = lite_campath_timeline_ ? double(nearby.tick-info.start_tick)/info.tick_rate : nearby.tick;
                const float x=timeline_x0_+(timeline_x1_-timeline_x0_)*float((time-timeline_view_start_)/span);
                if (std::abs(mouse.x-x)<=8*s)
                    Text("T %d  %s killed %s",nearby.tick,replay_player_name(events,nearby.killer),replay_player_name(events,nearby.victim));
            }
            TextDisabled("Blue: kills. Red: deaths. Click to seek; zoom to separate markers.");
            EndTooltip();
            if (IsMouseClicked(ImGuiMouseButton_Left)) {
                // Replace the slider gesture completely. Otherwise its edited
                // deactivation emits a second scrub seek on the next frame.
                GetCurrentContext()->ActiveIdHasBeenEditedBefore = false;
                GetCurrentContext()->ActiveIdHasBeenEditedThisFrame = false;
                ClearActiveID(); lite_replay_dragging_=false; track.committed=false;
                editor_enqueue(EditorAction::SeekReplayTick,double(kill.tick));
            }
        }
    }
    if (!lite_campath_timeline_ && track.committed && valid_lite_seek_tick(double(lite_replay_cursor_),info.total_ticks))
        editor_enqueue(EditorAction::SeekReplayTick,double(lite_replay_cursor_));
    EndDisabled();
    // A quiet ruler makes the visible zoom range explicit in both modes.
    if (!compact && span > 0) {
        auto* draw = GetWindowDrawList();
        const unsigned divisions = timeline_x1_ - timeline_x0_ > 420 * s ? 4 : 2;
        for (unsigned i = 0; i <= divisions; ++i) {
            const float fraction = float(i) / divisions;
            const double value = timeline_view_start_ + span * fraction;
            const float x = timeline_x0_ + (timeline_x1_ - timeline_x0_) * fraction;
            char label[32];
            std::snprintf(label, sizeof(label), lite_campath_timeline_ ? "%.2f s" : "%.0f", value);
            const float text_width = CalcTextSize(label).x;
            const float label_x = std::clamp(x - text_width * .5f, timeline_x0_,
                                            std::max(timeline_x0_, timeline_x1_ - text_width));
            if(i<divisions && label_x+text_width<tick_screen_x-8*s)
                draw->AddText(ImVec2(label_x, timeline_y_ + 10 * s), GetColorU32(ImGuiCol_TextDisabled), label);
        }
    }
    if (!compact) {
        // Share the ruler's window so it cannot intercept mouse input meant for
        // the tick field. Preserve the existing ID while changing ownership.
        SetCursorScreenPos(ImVec2(tick_screen_x,timeline_y_+6*s));
        PushOverrideID(tick_seed);tick_editor();PopID();
    }
    EndChild(); PopStyleVar();
    auto after_track=GetCursorScreenPos();
    if (compact) {
        SetCursorScreenPos(ImVec2(tick_screen_x,timeline_y_-GetFrameHeight()*.5f));
        tick_editor();
    } else after_track.y-=6*s; // Keep the transport spacing below the taller ruler.
    SetCursorScreenPos(after_track);
    if (rail_drawing_) {
        SetCursorPos(ImVec2(16*s,GetWindowHeight()-46*s));
        action_button(state.paused ? "Resume" : "Pause",EditorAction::PlayPause,78*s);
        SameLine();
        BeginDisabled(state.playing || state.attach_preview || !state.camera_count || !have_list ||
                      (!lite_campath_timeline_ && (!state.paused ||
                       editor_recording_locks_cameras(editor_recording_status().state))));
        if (lite_campath_timeline_) action_button("Seek here",EditorAction::SeekShot,95*s,double(lite_shot_time_));
        else if (Button("Seek camera",ImVec2(95*s,0))) {
            CameraPose request{}; request[0]=double(list.revision);
            camera_request(EditorAction::SeekCamera,state.selected_camera,list);
        }
        SameLine();action_button("<",EditorAction::PreviousView,32*s);
        SameLine();action_button(">",EditorAction::NextView,32*s);
        EndDisabled();
        SameLine(std::max(GetCursorPosX(),GetWindowWidth()-420*s));
        const auto now_ms=GetTickCount64();
        if (state.pause_after_capture==pause_capture_pending_value_ || now_ms>=pause_capture_pending_until_)
            pause_capture_pending_until_=0;
        bool pause_capture=pause_capture_pending_until_ ? pause_capture_pending_value_ : state.pause_after_capture;
        if (Checkbox("Pause after capture",&pause_capture) &&
            editor_enqueue(EditorAction::SetPauseAfterCapture,pause_capture ? 1 : 0)) {
            pause_capture_pending_value_=pause_capture;pause_capture_pending_until_=now_ms+1000;
        }
        SameLine();TextDisabled("Speed");SameLine();SetNextItemWidth(80*s);
        char speed[24];std::snprintf(speed,sizeof(speed),"%.3gx",state.playback_speed);
        if (BeginCombo("##rail-speed",speed)) {
            for(double value:{.05,.1,.25,.5,1.,2.,4.}) {
                char label[24];std::snprintf(label,sizeof(label),"%.3gx",value);
                if(Selectable(label,value==state.playback_speed))editor_enqueue(EditorAction::SetPlaybackSpeed,value);
            }
            EndCombo();
        }
        SameLine();TextDisabled("FOV");SameLine();SetNextItemWidth(64*s);
        BeginDisabled(!state.paused || state.playing || state.attach_preview || !state.manual_active || state.horizontal_fov<=0);
        if(!lite_fov_editing_)lite_fov_draft_=float(state.horizontal_fov);
        InputFloat("##lite-fov",&lite_fov_draft_,0,0,"%.1f",ImGuiInputTextFlags_AutoSelectAll);
        const bool committed=IsItemDeactivatedAfterEdit();lite_fov_editing_=IsItemActive();
        if(committed) {
            const auto factor=lite_fov_factor(state.horizontal_fov,lite_fov_draft_);
            if(std::isfinite(factor))editor_request_framing_factor(factor);
        }
        EndDisabled();
    } else {
    const bool compact_popup = compact && BeginPopup("##compact-transport");
    if (!compact || compact_popup) {
        const float seek_width = (lite_campath_timeline_ ? 76.f : 112.f) * s;
        if (!compact)
            SetCursorPosX(GetWindowSize().x * .5f - seek_width * .5f - GetStyle().ItemSpacing.x -
                          100 * s);
        BeginGroup();
        BeginDisabled(!state.paused || state.playing || state.attach_preview ||
                      !state.camera_count);
        action_button("< Previous", EditorAction::PreviousView, 100 * s);
        if (IsItemHovered())
            SetTooltip("Select and view the previous saved camera.");
        EndDisabled();
        SameLine();
        const bool selected_camera_available = have_list && state.selected_camera < list.total;
        BeginDisabled(state.playing || state.attach_preview || !state.camera_count ||
                      (!lite_campath_timeline_ &&
                       (!state.paused || !selected_camera_available ||
                        editor_recording_locks_cameras(editor_recording_status().state))));
        if (lite_campath_timeline_)
            action_button("Seek here", EditorAction::SeekShot, seek_width, double(lite_shot_time_));
        else if (Button("Seek camera", ImVec2(seek_width, 0)))
            camera_request(EditorAction::SeekCamera, state.selected_camera, list);
        if (IsItemHovered())
            SetTooltip(lite_campath_timeline_
                           ? "Apply the selected point on the campath shot."
                           : "Seek to the selected saved camera's replay time and view.");
        EndDisabled();
        SameLine();
        BeginDisabled(!state.paused || state.playing || state.attach_preview ||
                      !state.camera_count);
        action_button("Forward >", EditorAction::NextView, 100 * s);
        if (IsItemHovered())
            SetTooltip("Select and view the next saved camera.");
        EndDisabled();
        EndGroup();
        if (!compact)
            transport_width_ = GetItemRectSize().x;
        if (!compact)
            SetCursorPosX(
                std::max(GetStyle().WindowPadding.x, (GetWindowSize().x - speed_width_) * .5f));
        BeginGroup();
        action_button(state.paused ? "Resume" : "Pause", EditorAction::PlayPause, 83 * s);
        SameLine();
        // Match the desktop's acknowledged preference without flashing the previous
        // value back while the configuration makes its normal round trip.
        const auto now_ms = GetTickCount64();
        if (state.pause_after_capture == pause_capture_pending_value_ ||
            now_ms >= pause_capture_pending_until_)
            pause_capture_pending_until_ = 0;
        bool pause_after_capture =
            pause_capture_pending_until_ ? pause_capture_pending_value_ : state.pause_after_capture;
        if (Checkbox("Pause after capture", &pause_after_capture) &&
            editor_enqueue(EditorAction::SetPauseAfterCapture, pause_after_capture ? 1 : 0)) {
            pause_capture_pending_value_ = pause_after_capture;
            pause_capture_pending_until_ = now_ms + 1000;
    }
    if (IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
        SetTooltip("Pause the replay after capturing a camera. Also applies to the capture shortcut.");
    // Compact transport lives in a popup. Keep its preference row distinct so
    // the popup still fits smaller viewports with every speed and FOV control.
    if (!compact_popup) SameLine();
    TextDisabled("Speed");SameLine();
    for(double value : {.25,.5,1.,2.,4.}) {
        char label[16];std::snprintf(label,sizeof(label),"%gx",value);
        const bool selected=std::abs(state.playback_speed-value)<.0001;
        if(selected) {PushStyleColor(ImGuiCol_Button,lite_color(ui::SELECTED_BLUE));PushStyleColor(ImGuiCol_Text,lite_color(ui::STRONG));}
        if(Button(label))editor_enqueue(EditorAction::SetPlaybackSpeed,value);
        if(selected)PopStyleColor(2);SameLine();
    }
    if (GetContentRegionAvail().x < 120 * s) NewLine();
    TextDisabled("FOV");SameLine();SetNextItemWidth(74*s);
    BeginDisabled(!state.paused || state.playing || state.attach_preview || !state.manual_active || state.horizontal_fov<=0);
    if(!lite_fov_editing_)lite_fov_draft_=float(state.horizontal_fov);
    InputFloat("##lite-fov",&lite_fov_draft_,0,0,"%.1f",ImGuiInputTextFlags_AutoSelectAll);
    const bool fov_commit=IsItemDeactivatedAfterEdit();lite_fov_editing_=IsItemActive();
    if(fov_commit) {const auto factor=lite_fov_factor(state.horizontal_fov,lite_fov_draft_);if(std::isfinite(factor))editor_request_framing_factor(factor);}
    EndDisabled();
    EndGroup();
    if (!compact) speed_width_ = GetItemRectSize().x;
    }
    if (compact_popup) EndPopup();
    }
    EndDisabled();
    EditorCinematicStatus preparation{};
    const bool have_preparation = editor_cinematic_status(preparation) &&
        preparation.token == cinematic_token_ && preparation.revision == cinematic_revision_;
    const bool canceled_preparation = have_preparation && preparation.state == 3;
    if (canceled_preparation) cinematic_preparing_ = false;
    if (cinematic_preparing_) {
        if (!have_list || list.revision != cinematic_revision_ || IsKeyPressed(ImGuiKey_Escape, false))
            cinematic_preparing_ = false;
        else if (have_preparation) {
            if (preparation.state == 3) cinematic_preparing_ = false;
            else if (preparation.state == 2 && state.ready && !state.busy && state.paused &&
                     !state.playing && !state.attach_preview && state.camera_count >= 2 &&
                     editor_enqueue(EditorAction::PlayPath,0)) {
                cinematic_preparing_ = false;
            }
        }
    }
    End(); PopStyleColor();
    if(compact_curve_frames) PopStyleVar();
}
}
