// ImGui presentation only. Called with the overlay context current and its render lock held.
#pragma once
#include "dolly_editor.hpp"
#include "dolly_bone_picker.hpp"
#include "dolly_visualization.hpp"
#include "imgui.h"
#include <memory>
#include <string>
struct ImGuiSettingsHandler;

namespace dolly {
struct RailDesignState;
// Key-cap chip text for a binding (modifier bits 1 Ctrl, 2 Alt, 4 Shift).
// False for an unnamed key; the chip is then skipped.
bool key_cap_label(std::uint16_t vk, std::uint32_t modifiers, char* out,
                   std::size_t capacity) noexcept;
// Borrowed renderer operations; the panel never creates/releases a GPU resource.
struct PickerPortrait {
    void (*reset)() noexcept;
    void (*update)(const PickerFrame&) noexcept;
    ImTextureID (*texture)() noexcept;
};

// One presentation owner per overlay context; existing per-widget static edit
// caches remain process-lived to preserve device-loss behavior.
class OverlayPanel {
public:
    bool lite_mode() const noexcept { return lite_mode_; }
    void style_panel();
    void initialize_layout();
    // Commit completed layout gestures after Render, never through ImGui's
    // truncate-in-place file writer. force also flushes pending shutdown state.
    bool flush_layout(bool force = false) noexcept;
    void load_panel_fonts(float scale);
    // Font pointers borrow the current ImGui atlas. Guide preference and widget
    // edit caches deliberately survive device recreation, as they did before.
    void reset_fonts() noexcept {
        panel_font = heading_font = title_font = rail_font_ = rail_heading_ = nullptr;
        rail_medium_ = rail_small_ = rail_chip_ = nullptr;
        rail_loading_title_ = nullptr;
        panel_scale = 1.0f;
    }
    bool guides_visible(const EditorSnapshot& state, bool desktop_preview = false) noexcept;
    bool preview_guides_visible(const EditorSnapshot&, const VisualizationPreview&) noexcept;
    bool framing_grid_visible(const EditorSnapshot& state) noexcept;
    void draw_path_guides(const EditorSnapshot& state,
                          const std::shared_ptr<const VisualizationPath>& path, bool desktop_preview = false);
    void draw_framing_grid(const EditorSnapshot& state);
    bool shader_notice_visible(const EditorSnapshot& state) const noexcept;
    void draw_shader_notice();
    bool lite_campaths_visible(const EditorSnapshot& state) const noexcept;
    void draw_lite_campaths(const EditorSnapshot& state);
    void draw_lite_timeline(const EditorSnapshot& state);
    void cancel_position_graph() noexcept;
    void draw_panel(const EditorSnapshot& state);
    // The rail uses the same control bodies and revision-bound actions as classic.
    void draw_rail(const EditorSnapshot& state, ImTextureID background = 0);
    bool rail_enabled() const noexcept { return rail_enabled_; }
    bool rail_drawers_open() const noexcept { return rail_selected_ >= 0 || rail_paths_open_; }
    void close_rail_drawers() noexcept { rail_selected_ = -1; rail_paths_open_ = false; }
    ImVec4 rail_tab_rect(unsigned tab) const noexcept { return tab < 6 ? rail_tab_rects_[tab] : ImVec4(); }
    ImVec4 main_tab_rect(unsigned page) const noexcept { return page<5 ? main_tab_rects_[page] : ImVec4(); }
    ImVec4 main_card_heights(unsigned index) const noexcept {
        return index<4 ? ImVec4(section_heights_[lite_page_][index],section_draw_heights_[index],
                                section_minimum_[lite_page_][index],section_extra_draw_[index]) : ImVec4();
    }
    void draw_bone_picker(const EditorSnapshot& state, const PickerPortrait& portrait);
    // The Object Picker is its own mode and its own right-side window, entered
    // only by its bindable key. It needs no GPU resource (proxy wireframes).
    void draw_object_picker(const EditorSnapshot& state);
    void reset_guides() noexcept { guide_geometry.line_count = guide_geometry.label_count = 0; }
    std::size_t guide_lines() const noexcept { return guide_geometry.line_count; }
    std::size_t guide_labels() const noexcept { return guide_geometry.label_count; }
    // Last drawn Shot-timeline rect (screen coordinates) and committed tick
    // drags; the smoke test reads these through overlay diagnostics.
    float timeline_x0() const noexcept { return timeline_x0_; }
    float timeline_x1() const noexcept { return timeline_x1_; }
    float timeline_y() const noexcept { return timeline_y_; }
    std::uint64_t timeline_drags() const noexcept { return timeline_drags_; }
    float timeline_view_start() const noexcept { return timeline_view_start_; }
    float timeline_view_end() const noexcept { return timeline_view_end_; }
    unsigned timeline_mode() const noexcept { return curve_view_ ? 2u : lite_campath_timeline_ ? 1u : 0u; }
    ImVec2 position_graph_selected_point() const noexcept { return position_graph_selected_point_; }
    int curve_group() const noexcept { return curve_group_; }
    bool curve_compare() const noexcept { return curve_compare_; }
    unsigned curve_lane_count() const noexcept { return curve_lane_count_; }
    double curve_view_start() const noexcept { return curve_view_start_; }
    double curve_view_end() const noexcept { return curve_view_end_; }
    bool curve_editing() const noexcept { return position_graph_dragging_ || position_value_editing_ || tangent_dragging_ || tangent_value_editing_; }
    std::uint32_t curve_before_revision() const noexcept { return curve_before_ ? curve_before_->project_revision() : 0; }
    std::uint64_t curve_before_session() const noexcept { return curve_before_ ? curve_before_->session_id() : 0; }
    ImVec4 curve_plot_rect(unsigned channel) const noexcept { return channel < 7 ? curve_plot_rects_[channel] : ImVec4(); }
    ImVec4 curve_value_rect() const noexcept { return curve_value_rect_; }
    ImVec4 curve_frame_all_rect() const noexcept { return curve_frame_all_rect_; }
    ImVec4 curve_axis_rect(unsigned channel) const noexcept { return channel<7 ? curve_axis_rects_[channel] : ImVec4(); }
    ImVec4 curve_graphs_rect() const noexcept { return curve_graphs_rect_; }
    bool curve_group_controls_visible() const noexcept { return curve_group_controls_visible_; }
    bool curve_lane_compact_axes(unsigned group) const noexcept { return group<3 && curve_lane_compact_axes_[group]; }
    ImVec2 curve_selected_point(unsigned channel) const noexcept { return channel < 7 ? curve_selected_points_[channel] : ImVec2(); }
    ImVec2 curve_draft_point() const noexcept { return curve_draft_point_; }
    bool curve_draft_outside() const noexcept { return curve_draft_outside_; }
    int curve_snapped_neighbor() const noexcept { return curve_snapped_neighbor_; }
    double curve_draft_value() const noexcept { return position_graph_draft_; }
    double curve_scale_low(unsigned channel) const noexcept { return channel<7 ? curve_scales_[channel].low : 0; }
    double curve_scale_high(unsigned channel) const noexcept { return channel<7 ? curve_scales_[channel].high : 0; }
    ImVec2 curve_before_selected_point(unsigned channel) const noexcept { return channel < 7 ? curve_before_points_[channel] : ImVec2(); }
    double curve_before_display_offset(unsigned channel) const noexcept { return channel < 7 ? curve_before_offsets_[channel] : 0; }
    double curve_before_value(unsigned channel, unsigned index) const noexcept {
        return curve_before_ && channel < 7 && index < curve_before_->graph_keys().size()
            ? curve_before_->graph_keys()[index].authored_pose[channel] : 0;
    }
    ImVec2 curve_tangent_point(unsigned side) const noexcept { return side<2 ? tangent_points_[side] : ImVec2(); }
    ImVec4 curve_tangent_mode_rect() const noexcept { return tangent_mode_rect_; }
    ImVec4 curve_tangent_value_rect(unsigned side) const noexcept { return side<2 ? tangent_value_rects_[side] : ImVec4(); }

private:
    bool rail_enabled_ = true, rail_drawing_ = false, rail_paths_open_ = false, rail_picker_mode_ = false;
    int rail_selected_ = -1;
    float rail_body_height_[6]{654,390,314,536,399,480};
    ImVec4 rail_tab_rects_[6]{};
    ImVec2 rail_paths_position_{}, rail_paths_size_{};
    ImVec2 rail_bar_offset_{};
    ImVec4 rail_track_rect_{};
    ImTextureID rail_background_ = 0;
    ImFont* rail_font_ = nullptr;
    ImFont* rail_heading_ = nullptr;
    ImFont* rail_medium_ = nullptr;
    ImFont* rail_small_ = nullptr;
    ImFont* rail_chip_ = nullptr;
    ImFont* rail_loading_title_ = nullptr;
    float rail_font_dpi_=1;
    std::shared_ptr<RailDesignState> rail_design_;
    void draw_kara_drawer(const EditorSnapshot& state, unsigned tab, bool measuring = false);
    void draw_kara_footer(const EditorSnapshot& state, bool record_action = true);
    void draw_kara_transport(const EditorSnapshot& state);
    void draw_controls(const EditorSnapshot& state);
    void draw_controls_footer(const EditorSnapshot& state);
    void draw_bone_picker_controls(const EditorSnapshot& state, const PickerPortrait& portrait);
    void draw_object_picker_controls(const EditorSnapshot& state);
    ImVec4 panel_color(unsigned rgb, float alpha = 1.0f) const;
    ImVec4 lite_color(unsigned rgb) const { return panel_color(rgb); }
    bool layout_editing_ = false, reduced_motion_ = false, classic_layout_ = false, layout_custom_ = false;
    bool compact_controls_ = false, compact_paths_ = false, card_open_ = false;
    bool editor_custom_ = false, editor_position_pending_ = false, editor_dragging_ = false;
    ImVec2 editor_position_{};
    ImVec2 drag_start_[3]{}, drag_mouse_[3]{}, outer_position_[2]{};
    bool outer_position_pending_[2]{};
    unsigned section_slots_[5][4]={{0,1,2,3},{0,1,2,3},{0,1,2,3},{0,1,2,3},{0,1,2,3}};
    float section_heights_[5][4]={{230,160,0,0},{170,200,0,0},{150,330,0,0},{290,140,140,160},{140,230,150,120}};
    ImVec2 section_positions_[4]{};
    unsigned section_columns_[4]{}, card_index_ = 0;
    int section_column_pref_[5][4]={{-1,-1,-1,-1},{-1,-1,-1,-1},{-1,-1,-1,-1},{-1,-1,-1,-1},{-1,-1,-1,-1}};
    float column_weights_[5][3]={{1,1,1},{1,1,1},{1,1,1},{1,1,1},{1,1,1}};
    // Natural content measurements never include the padding used to align a
    // visual row. Only an explicit resize changes section_minimum_.
    float section_minimum_[5][4]{}, section_extra_draw_[4]{}, section_draw_heights_[4]{};
    float section_widths_[4]{}, column_widths_[3]{}, column_x_[3]{};
    ImVec2 section_canvas_{}, section_ghost_{};
    const char* section_names_[4]{};
    bool section_ghost_active_ = false;
    int section_resize_ = -1;
    ImVec2 resize_mouse_{}, resize_size_{};
    float sidebar_resize_width_ = 0;
    float sidebar_width_pending_ = -1;
    bool sidebar_width_current_ = false;
    ImVec2 sidebar_resize_mouse_{};
    float card_target_y_ = 0;
    float card_vertical_scale_ = 1;
    double sections_changed_at_[5]={-10,-10,-10,-10,-10};
    struct SectionPayload { int page; unsigned index; };
    static unsigned section_count(int page);
    void prepare_sections();
    void finish_section_layout();
    void save_layout();
    unsigned layout_reset_ = 0, layout_columns_ = 2, card_column_ = 0;
    float card_origin_y_ = 0, card_column_y_[3]{};
    float transport_width_ = 500, speed_width_ = 500;
    double tab_changed_at_ = -10, camera_added_at_ = -10;
    int tab_direction_ = 1;
    unsigned previous_camera_total_ = 0, animated_camera_ = ~0u, camera_row_page_ = 0;
    unsigned camera_pending_first_ = ~0u, camera_pending_page_ = 0;
    std::uint32_t camera_pending_revision_ = 0;
    double camera_pending_at_ = -10;
    bool have_camera_total_ = false, cinematic_preparing_ = false;
    std::uint32_t cinematic_revision_ = 0, cinematic_token_ = 0;
    std::wstring layout_file_;
    std::string layout_last_saved_;
    bool layout_save_requested_ = false;
    bool layout_save_failed_ = false;
    double layout_save_retry_at_ = 0;
    void layout_controls();
    void panel_handle(const char* label, bool editor = false);
    void constrain_panel();
    float motion_progress(double started, double duration = .22) const;
    float lite_timeline_height() const;
    bool lite_timeline_compact(float width) const;
    float layout_top_inset() const;
    float layout_main_minimum_ = 200.f;
    bool layout_scroll_content_ = false;
    bool layout_single_header_ = false;
    ImVec4 main_tab_rects_[5]{};
    static void* read_layout_open(ImGuiContext*, ImGuiSettingsHandler*, const char*);
    static void read_layout_line(ImGuiContext*, ImGuiSettingsHandler*, void*, const char*);
    static void write_layout(ImGuiContext*, ImGuiSettingsHandler*, ImGuiTextBuffer*);
    bool lite_mode_ = false;
    int lite_page_ = 0, lite_pending_page_ = -1;
    int lite_tick_step_ = 1;
    bool lite_campath_timeline_ = false, lite_replay_dragging_ = false;
    std::int64_t lite_replay_cursor_ = 0;
    bool lite_tick_editing_ = false, lite_fov_editing_ = false;
    char lite_tick_draft_[32]{};
    float lite_fov_draft_ = 90;
    float lite_shot_time_ = 0;
    double lite_shot_playhead_ = -1, lite_shot_duration_ = -1;
    std::int64_t lite_replay_total_seen_ = -1;
    bool lite_replay_ready_seen_ = false, lite_replay_fit_pending_ = true;
    unsigned lite_replay_events_status_seen_ = 0;
    std::uint32_t lite_shot_revision_ = 0;
    std::uint32_t lite_arrive_revision_ = 0, lite_arrive_first_ = 0;
    float lite_arrive_drafts_[kEditorCameraListCount]{};
    bool lite_arrive_editing_[kEditorCameraListCount]{};
    std::uint32_t lite_arrive_edit_revisions_[kEditorCameraListCount]{};
    std::uint32_t lite_clear_revision_ = 0;
    float lite_bank_drafts_[kEditorCameraListCount]{};
    bool lite_bank_editing_[kEditorCameraListCount]{};
    bool bank_release_pending_ = false;
    std::uint32_t bank_sent_revision_ = 0, bank_release_index_ = 0;
    float bank_sent_value_ = 0, bank_release_value_ = 0;
    double bank_anchor_time_ = 0;
    bool pause_capture_pending_value_ = false;
    std::uint64_t pause_capture_pending_until_ = 0;
    bool position_graph_dragging_ = false;
    bool position_value_editing_ = false, position_graph_escape_ = false;
    int position_graph_axis_ = 0;
    std::uint32_t position_graph_revision_ = 0, position_graph_index_ = 0;
    double position_graph_time_ = 0, position_graph_original_ = 0, position_graph_draft_ = 0;
    float position_graph_mouse_y_ = 0, position_graph_units_per_pixel_ = 1;
    ImVec2 position_graph_selected_point_{};
    bool curve_view_ = false, curve_compare_ = false, curve_show_before_ = false;
    std::uint32_t curve_preview_token_ = 0, curve_preview_index_ = 0;
    CameraPose curve_preview_anchor_{};
    double curve_preview_sent_at_ = -1e30;
    void start_curve_preview(const VisualizationPath&,unsigned index,unsigned channel);
    void clear_curve_preview() noexcept;
    bool curve_fit_pending_ = true, curve_resize_pending_ = false, curve_panning_ = false;
    int curve_group_ = 0, curve_axes_[3]{0, 4, 6};
    unsigned curve_lanes_ = 3, curve_lane_count_ = 1;
    std::uint64_t curve_session_ = 0, curve_edit_session_ = 0;
    bool tangent_dragging_ = false, tangent_value_editing_ = false;
    unsigned tangent_side_ = 0, tangent_axis_ = 0, tangent_mode_ = 0;
    std::uint32_t tangent_revision_ = 0, tangent_index_ = 0;
    std::uint64_t tangent_session_ = 0;
    double tangent_time_ = 0, tangent_draft_[2]{}, tangent_original_[2]{};
    double tangent_dt_ = 0, tangent_units_per_pixel_ = 0;
    float tangent_mouse_y_ = 0;
    ImVec2 tangent_points_[2]{};
    ImVec4 tangent_mode_rect_{}, tangent_value_rects_[2]{};
    double curve_view_start_ = 0, curve_view_end_ = 1, curve_duration_seen_ = -1, curve_start_seen_ = 0;
    std::size_t curve_key_count_seen_ = 0;
    double curve_pan_mouse_ = 0, curve_pan_start_ = 0, curve_pan_end_ = 1;
    double curve_angle_seed_[2]{}, curve_before_offsets_[7]{};
    bool curve_angle_seeded_ = false, curve_angles_unwrapped_ = false;
    struct CurveScale { double low = 0, high = 1; bool fitted = false; std::uint32_t revision = 0; };
    CurveScale curve_scales_[7]{};
    ImVec4 curve_plot_rects_[7]{};
    ImVec4 curve_value_rect_{};
    ImVec4 curve_frame_all_rect_{};
    ImVec4 curve_axis_rects_[7]{}, curve_graphs_rect_{};
    bool curve_group_controls_visible_ = true, curve_lane_compact_axes_[3]{};
    bool curve_value_pending_ = false, curve_frame_after_value_ = false;
    std::uint32_t curve_value_pending_revision_ = 0;
    std::uint64_t curve_value_pending_session_ = 0;
    double curve_value_pending_until_ = 0;
    ImVec2 curve_selected_points_[7]{};
    ImVec2 curve_draft_point_{};
    bool curve_draft_outside_ = false;
    int curve_snapped_neighbor_ = -1;
    ImVec2 curve_before_points_[7]{};
    std::shared_ptr<const VisualizationPath> curve_before_;
    void draw_curve_workspace(const EditorSnapshot& state, const EditorCameraList& list);
    bool draw_timeline_modes();
    unsigned layout_card_index_ = 0;
    int replay_player_filter_ = -1;
    static void roster_label(const EditorRosterEntry& entry, char* out, std::size_t capacity);
    struct SliderRow {
        bool committed = false, active = false;
    };
    void action_button(const char* label, EditorAction action, float width = 0, double value = 0,
                       bool primary = false);
    SliderRow slider_row(const char* id, const char* label, float* value, float minimum,
                         float maximum, const char* format, float label_width = 58.0f,
                         const VisualizationPath* timeline = nullptr, bool replay_track = false,
                         bool unclamped_entry = false);
    void section_title(const char* title, const char* detail = nullptr);
    bool begin_panel_card(const char* name);
    bool inline_controls(const char* label, const char* display = nullptr);
    void end_panel_card();
    void replay_badge(const EditorSnapshot& state);
    ImFont* panel_font = nullptr;
    ImFont* heading_font = nullptr;
    ImFont* title_font = nullptr;
    float panel_scale = 1.0f;
    bool show_path_guides = true;
    VisualizationGeometry guide_geometry;
    float timeline_x0_ = 0, timeline_x1_ = 0, timeline_y_ = 0;
    std::uint64_t timeline_drags_ = 0;
    // Classic reset confirmation, opened outside the busy-disabled cards.
    bool reset_path_confirm_ = false;
    const char* timeline_view_id_ = nullptr;
    float timeline_view_start_ = 0, timeline_view_end_ = 0;
    // Camera range the Shot timeline was last fitted to.
    std::size_t timeline_fit_count_ = 0;
    double timeline_fit_first_ = 0, timeline_fit_last_ = 0;
    bool timeline_panning_ = false;
    float timeline_pan_anchor_ = 0, timeline_pan_start_ = 0, timeline_pan_end_ = 0;
};
} // namespace dolly
