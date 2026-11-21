// Real rail controls and WARP screenshots; synthetic data, no game access.
#include "../src/dolly_overlay_assets.hpp"
#include "../vendor/imgui/backends/imgui_impl_dx11.h"
// The expected timings come from Kara's supplied rail.cpp (EaseAnim and Frame),
// checked against the actual window geometry and emitted text, not a test tween.
static void rail_motion_regressions() {
    auto* previous = ImGui::GetCurrentContext();
    const auto saved = snapshot;
    SetEnvironmentVariableA("DOLLY_UI_LAYOUT_FILE", "none");
    for (const auto size :
         {ImVec2(1280, 720), ImVec2(1920, 1080), ImVec2(2560, 1440), ImVec2(3840, 2160)}) {
        auto* ctx = ImGui::CreateContext();
        auto& io = ImGui::GetIO();
        io.DisplaySize = size;
        io.DeltaTime = .01f;
        io.IniFilename = nullptr;
        io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
        dolly::OverlayPanel panel;
        panel.style_panel();
        // Reserve non-white atlas UVs for glyphs in the opacity assertions;
        // ImGui can otherwise texture anti-aliased icon strokes from this atlas.
        ImGui::GetStyle().AntiAliasedLinesUseTex = false;
        panel.initialize_layout();
        panel.load_panel_fonts(1.5f);
        io.Fonts->Build();
        snapshot = {};
        snapshot.enabled = snapshot.focused = snapshot.ready = snapshot.paused = true;
        snapshot.manual_active = snapshot.attach_available = true;
        snapshot.owner = dolly::EditorOwner::Panel;
        snapshot.camera_count = 2;
        snapshot.horizontal_fov = 90;
        snapshot.duration = 3;
        snapshot.speed = 400;
        auto frame = [&] {
            ImGui::NewFrame();
            panel.draw_rail(snapshot);
            ImGui::Render();
        };
        auto frames = [&](int count) {
            while (count--)
                frame();
        };
        auto window = [&](unsigned tab) {
            char name[32];
            std::snprintf(name, sizeof(name), "##rail-tab%u", tab);
            return ImGui::FindWindowByName(name);
        };
        auto choose = [&](unsigned tab) {
            ctx->NavNextActivateId = window(tab)->GetID("##rail-button");
            frame();
        };
        auto text_alpha = [&](ImGuiWindow* w) {
            unsigned maximum = 0;
            for (const auto& v : w->DrawList->VtxBuffer)
                if (v.uv.x != io.Fonts->TexUvWhitePixel.x || v.uv.y != io.Fonts->TexUvWhitePixel.y)
                    maximum = std::max(maximum, (v.col >> IM_COL32_A_SHIFT) & 255u);
            return maximum;
        };
        const float scale = size.y / 1080;
        io.AddMousePosEvent(-100, -100);
        frames(3);
        io.AddMousePosEvent(80 * scale, 20 * scale);
        frame(); // hover is resolved after drawing, as in the supplied preview
        frames(6);
        require(std::abs(window(0)->Size.y - 80.383f * scale) < 1.1f,
                "Rail hover no longer follows Kara's 220ms cubic easing");
        io.AddMousePosEvent(-100, -100);
        frames(60);
        choose(0);
        frames(10);
        require(std::abs(window(0)->Size.x - 320.8435f * scale) < 1.1f,
                "Rail opening does not follow Kara's 320ms geometry");
        const float early_height = window(0)->Size.y;
        require(text_alpha(window(0)) == 0,
                "Drawer text appears before the final 40 percent of opening time");
        frames(10);
        require(std::abs(int(text_alpha(window(0))) - 16) <= 2,
                "Drawer fade is early or gains a separate disabled-control brightness jump");
        frames(20);
        const float first_open = 1 - std::pow(1 - .1f / .32f, 3);
        require(std::abs(early_height -
                         (65 * scale + (window(0)->Size.y - 65 * scale) * first_open)) < 1.5f,
                "First opening resizes again when initially hidden content is measured late");
        panel.close_rail_drawers();
        frames(10);
        require(std::abs(window(0)->Size.x - 188.1565f * scale) < 1.1f &&
                    std::abs(int(text_alpha(window(0))) - 56) <= 2,
                "Closing drawer lost Kara's geometry or linear content fade-out");
        choose(0); // reverse a closing drawer without restarting a full transition
        const float progress = (window(0)->Size.x / scale - 65) / 379;
        const float phase = 1 - std::cbrt(std::max(0.f, 1 - progress));
        frames(10);
        const float remaining = std::max(0.f, 1 - phase - .1f / .32f);
        const float expected = 65 + 379 * (1 - remaining * remaining * remaining);
        require(std::abs(window(0)->Size.x - expected * scale) < 2,
                "Reversing a drawer restarts its duration instead of preserving the motion phase");
        frames(50);
        choose(1);
        frames(16);
        require(std::abs(window(0)->Size.x - 112.375f * scale) < 1.1f &&
                    std::abs(window(1)->Size.x - 396.625f * scale) < 1.1f,
                "Switching tabs does not animate both drawers at Kara's pace");
        frames(50);
        require(observed_actions.empty(),
                "Motion or hidden measurement dispatched an editor action");
        for (unsigned direction = 0; direction < 2; ++direction) {
            choose(5);
            for (unsigned tick = 0; tick < 20; ++tick) {
                frame();
                const float edge = std::round((size.x - 1920 * scale) * .5f + 1861 * scale);
                require(std::abs(window(5)->Pos.x + window(5)->Size.x - edge) < .1f,
                        "Right-anchored Campaths edge wobbles during the animation");
            }
            frames(20);
        }
        // Use the real saved preference, not a test-only animation switch.
        ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nReducedMotion=1\n");
        choose(0);
        frame();
        require(std::abs(window(0)->Size.x - 444 * scale) < 1.1f &&
                    std::abs(window(1)->Size.x - 65 * scale) < 1.1f && text_alpha(window(0)) == 255,
                "Reduce motion does not snap drawer geometry and text together");
        // A short viewport exercises real scrolling without the parked tripod feature.
        io.DisplaySize = ImVec2(480, 200);
        frames(4);
        ImGuiWindow* child = nullptr;
        for (auto* w : ctx->Windows)
            if (w->Active && std::strstr(w->Name, "##drawer-scroll"))
                child = w;
        require(child && child->ScrollbarY, "Short viewport did not exercise drawer scrolling");
        for (unsigned position = 0; position < 2; ++position) {
            if (position) {
                ImGui::SetScrollY(child, child->ScrollMax.y);
                frames(2);
            }
            require(child->DC.CursorMaxPos.x <= child->InnerClipRect.Max.x,
                    "Scrollbar clips fixed-width controls");
            unsigned glyphs = 0;
            for (const auto& cmd : child->DrawList->CmdBuffer) {
                if (std::abs(cmd.ClipRect.z - child->InnerClipRect.Max.x) > .1f)
                    continue;
                for (unsigned i = cmd.IdxOffset; i < cmd.IdxOffset + cmd.ElemCount; ++i) {
                    const auto& v =
                        child->DrawList->VtxBuffer[cmd.VtxOffset + child->DrawList->IdxBuffer[i]];
                    if (v.uv.x == io.Fonts->TexUvWhitePixel.x &&
                        v.uv.y == io.Fonts->TexUvWhitePixel.y)
                        continue;
                    ++glyphs;
                    require(v.pos.x <= cmd.ClipRect.z, "Scrollbar cuts a rendered letter");
                }
            }
            require(glyphs > 20, "Scroll regression did not inspect rendered glyphs");
        }
        io.DisplaySize = size;
        ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nReducedMotion=0\n");
        for (const int rate : {30, 60, 144}) {
            io.DeltaTime = 1.f / rate;
            panel.close_rail_drawers();
            frames(rate);
            choose(0);
            const int count = int(std::round(.2 * rate));
            frames(count);
            const float elapsed = float(count) / rate;
            const float p = 1 - std::pow(1 - elapsed / .32f, 3);
            const float alpha = std::clamp((elapsed / .32f - .6f) / .4f, 0.f, 1.f);
            require(std::abs(window(0)->Size.x - (65 + 379 * p) * scale) < 1.1f &&
                        std::abs(int(text_alpha(window(0))) - int(std::round(255 * alpha))) <= 2,
                    "Rail geometry or fade changes with frame rate");
        }
        ImGui::DestroyContext(ctx);
        std::printf(
            "Rail motion %.0fx%.0f: reference hover/open/close/fade/reverse/switch, reduced motion and scrolled glyphs passed.\n",
            size.x, size.y);
    }
    ImGui::SetCurrentContext(previous);
    snapshot = saved;
}
static void rail_regressions(const char* output, bool motion = false) {
    rail_motion_regressions();
    SyntheticViewer viewer;
    SetEnvironmentVariableA("DOLLY_OVERLAY_UI", nullptr);
    auto* previous = ImGui::GetCurrentContext();
    const auto old_snapshot = snapshot;
    for (const auto size :
         {ImVec2(1280, 720), ImVec2(1920, 1080), ImVec2(2560, 1440), ImVec2(3840, 2160)}) {
        auto* ctx = ImGui::CreateContext();
        ImGui::SetCurrentContext(ctx);
        auto& io = ImGui::GetIO();
        io.DisplaySize = size;
        io.DeltaTime = 1.f / 60;
        io.IniFilename = nullptr;
        io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
        dolly::OverlayPanel panel;
        panel.style_panel();
        panel.load_panel_fonts(1.5f);
        io.Fonts->Build();
        snapshot = {};
        snapshot.enabled = snapshot.focused = snapshot.ready = snapshot.paused =
            snapshot.manual_active = true;
        snapshot.owner = dolly::EditorOwner::Panel;
        snapshot.camera_count = 2;
        snapshot.selected_camera = 0;
        snapshot.horizontal_fov = 90;
        snapshot.duration = 3;
        snapshot.tick = 3470;
        snapshot.playback_speed = .25;
        snapshot.attach_available = snapshot.attach_selected = true;
        snapshot.attach_point = 1;
        snapshot.follow_available = snapshot.dof_available = snapshot.citadel_dof_available = true;
        std::snprintf(snapshot.shot_name, sizeof(snapshot.shot_name), "Rail integration fixture");
        auto frame = [&] {
            ImGui::NewFrame();
            panel.draw_rail(snapshot);
            ImGui::Render();
        };
        auto settle = [&] {
            for (int n = 0; n < 55; ++n)
                frame();
        };
        auto find = [&](const char* part) -> ImGuiWindow* {
            for (auto* w : ctx->Windows)
                if (w->Active && std::strstr(w->Name, part))
                    return w;
            return nullptr;
        };
        frame();
        frame();
        require(panel.rail_enabled() && !panel.rail_drawers_open(),
                "Rail must default to closed drawers");
        require(find("##lite-timeline") && find("##rail-status"),
                "Closed rail lost transport or status");
        require(!find("Deadlock Dolly"), "Rail drew the classic panel as well");
        auto activate_tab = [&](unsigned tab) {
            panel.close_rail_drawers();
            settle();
            char name[32];
            std::snprintf(name, sizeof(name), "##rail-tab%u", tab);
            auto* button = find(name);
            require(button != nullptr, "Rail tab missing");
            ctx->NavNextActivateId = button->GetID("##rail-button");
            frame();
            settle();
        };
        constexpr const char* cards[] = {"##rail-tab0", "##rail-tab1", "##rail-tab2",
                                         "##rail-tab3", "##rail-tab4", "##rail-tab5"};
        observed_actions.clear();
        for (unsigned tab = 0; tab < 6; ++tab) {
            activate_tab(tab);
            require(panel.rail_drawers_open(), "Keyboard could not open a rail drawer");
            if (!find(cards[tab])) {
                for (auto* w : ctx->Windows)
                    if (w->Active)
                        std::fprintf(stderr, "Rail active: %s\n", w->Name);
                throw std::runtime_error(std::string("Rail shared control missing: ") + cards[tab]);
            }
            const auto rect = panel.rail_tab_rect(tab);
            require(rect.x >= -1 && rect.z <= size.x + 1, "Rail tab escaped the viewport");
            auto* bar = find("##lite-timeline");
            require(bar && bar->Pos.y >= 0 && bar->Pos.y + bar->Size.y <= size.y + 1,
                    "Rail transport clipped");
            require(observed_actions.empty(),
                    "Drawing or opening a rail drawer emitted an editor action");
            const float expected =
                (tab == 0 || tab == 1 || tab == 5 ? 444.f : 897.f) * size.y / 1080;
            require(std::abs(find(cards[tab])->Size.x - expected) < 1,
                    "Rail drawer no longer uses Kara's one/two-column width");
        }
        {
            activate_tab(3);
            auto* look = find("##rail-tab3");
            const dolly::EditorAction actions[] = {dolly::EditorAction::ToggleCitadelGlow,
                dolly::EditorAction::ToggleHealthbars, dolly::EditorAction::NearPlayerOpacityFix};
            const char* labels[] = {"Hide Citadel glow", "Hide floating health bars", "Near player opacity fix"};
            for (unsigned state : {0u, 7u}) {
                snapshot.scene_appearance_flags = static_cast<std::uint8_t>(state);
                for (unsigned i = 0; i < 3; ++i) {
                    observed_actions.clear();
                    ctx->NavNextActivateId = look->GetID(labels[i]);
                    frame();
                    require(observed_actions.size() == 1 && observed_actions[0].action == actions[i] && observed_actions[0].value == 0,
                            "Scene checkbox lost its toggle dispatch");
                    auto* nav = ctx->NavWindow;
                    require(nav != nullptr, "Scene checkbox is not keyboard accessible");
                    auto rect = nav->NavRectRel[ImGuiNavLayer_Main];
                    rect.Translate(nav->Pos);
                    require(rect.Min.x >= look->Pos.x && rect.Max.x <= look->Pos.x + look->Size.x,
                            "Scene checkbox escapes the existing drawer");
                }
            }
            observed_actions.clear();
            snapshot.playing = true;
            for (const auto* label : labels) {
                ctx->NavNextActivateId = look->GetID(label);
                frame();
            }
            require(observed_actions.empty(), "Scene checkboxes bypass playback guard");
            snapshot.playing = false;
            snapshot.scene_appearance_flags = 0;
        }
        {
            // RenderLock camera: Kara's section pattern in the Export drawer's right column.
            activate_tab(4);
            auto* exporter = find("##rail-tab4");
            const auto saved = snapshot;
            snapshot.camera_count = 2;
            snapshot.busy = snapshot.playing = snapshot.video_pov = false;
            snapshot.renderlock_exporting = false;
            frame();
            observed_actions.clear();
            ctx->NavNextActivateId = exporter->GetID("Export .crav");
            frame();
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::ExportRenderLock,
                    "Export .crav lost its RenderLock export dispatch");
            auto* nav = ctx->NavWindow;
            require(nav != nullptr, "Export .crav is not keyboard accessible");
            auto rect = nav->NavRectRel[ImGuiNavLayer_Main];
            rect.Translate(nav->Pos);
            require(rect.Min.x >= exporter->Pos.x && rect.Max.x <= exporter->Pos.x + exporter->Size.x &&
                        rect.Min.x > exporter->Pos.x + exporter->Size.x * .4f,
                    "Export .crav escapes the drawer or left the right-hand column");
            for (unsigned guard = 0; guard < 3; ++guard) {
                snapshot.playing = guard == 0;
                snapshot.video_pov = guard == 1;
                snapshot.camera_count = guard == 2 ? 1 : 2;
                frame();
                observed_actions.clear();
                ctx->NavNextActivateId = exporter->GetID("Export .crav");
                frame();
                require(observed_actions.empty(), "Export .crav bypasses its playback/POV/camera guard");
            }
            snapshot.playing = snapshot.video_pov = false;
            snapshot.camera_count = 2;
            snapshot.busy = snapshot.renderlock_exporting = true;
            frame();
            observed_actions.clear();
            ctx->NavNextActivateId = exporter->GetID("Cancel camera export");
            frame();
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::CancelRenderLock,
                    "A running RenderLock export cannot be cancelled from the drawer");
            snapshot = saved;
            frame();
            observed_actions.clear();
        }
        {
            // Relocated camera actions keep their real dispatch and use the same
            // full-width rows, drawer insets and gaps as Save Path at every scale.
            const float scale = size.y / 1080;
            activate_tab(0);
            ctx->NavNextActivateId = find("##rail-tab0")->GetID("Replace selected");
            frame();
            require(observed_actions.empty(), "Camera drawer still exposes Replace selected");
            activate_tab(5);
            auto* paths = find("##rail-tab5");
            auto activate = [&](ImGuiWindow* window, const char* label) {
                ctx->NavNextActivateId = window->GetID(label);
                frame();
                auto* nav = ctx->NavWindow;
                require(nav != nullptr, "Relocated button lost keyboard focus");
                auto rect = nav->NavRectRel[ImGuiNavLayer_Main];
                rect.Translate(nav->Pos);
                return rect;
            };
            const auto seek = activate(paths, "Seek camera");
            require(observed_actions.size() == 1 &&
                        observed_actions.back().action == dolly::EditorAction::SeekCamera &&
                        observed_actions.back().value == snapshot.selected_camera &&
                        observed_actions.back().revision == camera_revision_stub,
                    "Campaths Seek camera lost its selected-camera/revision request");
            const auto replace = activate(paths, "Replace selected");
            require(observed_actions.size() == 2 &&
                        observed_actions.back().action == dolly::EditorAction::Replace,
                    "Campaths Replace selected is disconnected");
            const auto save = activate(paths, "Save Path");
            require(observed_actions.size() == 3 &&
                        observed_actions.back().action == dolly::EditorAction::SaveShot,
                    "Campaths Save Path changed its action");
            observed_actions.clear();
            snapshot.bone_picker = true;
            activate(paths, "Delete");
            activate(paths, "Clear All");
            require(observed_actions.empty(), "Campaths offers deletion during Bone Picker inspection");
            activate(paths, "Cancel Bone Picker");
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::CancelBonePicker,
                    "Campaths offers no way to finish inspecting before editing cameras");
            snapshot.bone_picker = false;
            observed_actions.clear();
            snapshot.attach_preview = true;
            activate(paths, "Delete");
            activate(paths, "Clear All");
            require(observed_actions.empty(), "Attached preview permits camera deletion before detaching");
            const auto detach = activate(paths, "Detach preview");
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::AttachPreview &&
                        observed_actions[0].value == 0,
                    "Campaths offers no way to detach after finishing Bone Picker");
            observed_actions.clear();
            io.AddMousePosEvent(detach.GetCenter().x, detach.GetCenter().y);
            frame(); frame();
            io.AddMouseButtonEvent(0, true); frame();
            io.AddMouseButtonEvent(0, false); frame();
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::AttachPreview &&
                        observed_actions[0].value == 0,
                    "Campaths mouse detach is disconnected");
            observed_actions.clear();
            snapshot.busy = true;
            activate(paths, "Detach preview");
            snapshot.busy = false;
            snapshot.paused = false;
            activate(paths, "Detach preview");
            snapshot.paused = true;
            snapshot.playing = true;
            activate(paths, "Detach preview");
            snapshot.playing = false;
            dolly::recording_status_stub.state = dolly::video::State::recording;
            activate(paths, "Detach preview");
            require(observed_actions.empty(), "Campaths detach bypasses operation/recording guards");
            dolly::recording_status_stub = {};
            snapshot.attach_preview = false; // Existing detach operation has acknowledged free view.
            activate(paths, "Delete");
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::DeleteCamera,
                    "Campaths Delete remains unavailable after detaching");
            observed_actions.clear();
            activate(paths, "Clear All");
            settle();
            require(observed_actions.empty(), "Campaths Clear All skipped confirmation after detaching");
            activate(paths, "Clear");
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::ClearCameraPath,
                    "Campaths Clear remains unavailable after detaching");
            observed_actions.clear();
            settle();
            for (const auto r : {seek, replace, save}) {
                require(std::abs(r.Min.x - paths->Pos.x - 21 * scale) <= 1.1f &&
                            std::abs(paths->Pos.x + paths->Size.x - r.Max.x - 20 * scale) <= 1.1f &&
                            std::abs(r.GetHeight() - 30 * scale) <= 1.1f,
                        "Campaths action row does not match drawer padding and button height");
            }
            require(std::abs(replace.Min.y - seek.Max.y - 10 * scale) <= 1.1f &&
                        std::abs(save.Min.y - replace.Max.y - 10 * scale) <= 1.1f,
                    "Relocated Campaths actions do not have consistent 10px gaps");
            observed_actions.clear();
            snapshot.paused = false;
            activate(paths, "Seek camera");
            snapshot.paused = true;
            snapshot.attach_preview = true;
            activate(paths, "Seek camera");
            snapshot.attach_preview = false;
            snapshot.playing = true;
            activate(paths, "Seek camera");
            snapshot.playing = false;
            dolly::recording_status_stub.state = dolly::video::State::recording;
            activate(paths, "Seek camera");
            activate(paths, "Replace selected");
            require(observed_actions.empty(),
                    "Relocated actions bypassed their editing/recording guards");
            dolly::recording_status_stub = {};
            auto* bar = find("##lite-timeline");
            activate(bar, "Seek camera");
            require(observed_actions.empty(), "Replay bar still exposes Seek camera");
            const auto resume = activate(bar, "Resume");
            const auto previous = activate(bar, "##prev-camera");
            const auto next = activate(bar, "##next-camera");
            require(std::abs(previous.Min.x - resume.Max.x - 10 * scale) <= 1.1f &&
                        std::abs(next.Min.x - previous.Max.x - 5 * scale) <= 1.1f &&
                        std::abs(previous.Min.y - resume.Min.y) <= 1.1f &&
                        std::abs(next.Min.y - resume.Min.y) <= 1.1f,
                    "Replay arrows left a gap or lost alignment after removing Seek camera");
            observed_actions.clear();
            activate(bar, "Campaths");
            frame();
            activate(bar, "Seek here");
            require(observed_actions.size() == 1 &&
                        observed_actions.back().action == dolly::EditorAction::SeekShot,
                    "Moving Seek camera also removed Campaths timeline Seek here");
            observed_actions.clear();
            activate(bar, "Replay");
            frame();
            std::printf(
                "Moved buttons %.0fx%.0f: Campaths 21/20px insets, 30px rows, 10px gaps; Replay arrows 10/5px gaps; actions and guards passed.\n",
                size.x, size.y);
        }
        activate_tab(2);
        auto* card = find("##rail-tab2");
        require(card != nullptr, "Bone framing missing");
        ctx->NavNextActivateId = card->GetID("Lock vertical aim");
        frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action ==
                        dolly::EditorAction::SetAttachVerticalAimLock &&
                    observed_actions.back().revision == camera_revision_stub &&
                    observed_actions.back().time == 0,
                "Rail vertical aim toggle lost its real revision-bound action");
        observed_actions.clear();
        snapshot.attach_point = 0;
        frame();
        ctx->NavNextActivateId = card->GetID("Lock vertical aim");
        frame();
        for (const auto& action : observed_actions)
            std::fprintf(stderr, "Eyes action %u value %.0f\n", unsigned(action.action),
                         action.value);
        require(observed_actions.empty(), "Eyes incorrectly enabled the vertical aim toggle");
        snapshot.attach_point = 1;
        // Real numeric input must preserve Snap's large offsets and untouched axes.
        snapshot.attach_offsets[0] = 80.5;
        snapshot.attach_offsets[1] = -31.25;
        snapshot.attach_offsets[2] = 8.75;
        frame();
        ctx->NavNextActivateId = ImHashStr("##slider", 0, ImHashStr("Forward", 0, card->ID));
        ctx->NavNextActivateFlags = ImGuiActivateFlags_PreferInput;
        frame();
        frame();
        require(ctx->InputTextState.ID == ctx->ActiveId && ctx->ActiveId,
                "Kara's slider lost exact keyboard entry for a snapped offset");
        io.AddInputCharactersUTF8("75.25");
        frame();
        io.AddKeyEvent(ImGuiKey_Enter, true);
        frame();
        io.AddKeyEvent(ImGuiKey_Enter, false);
        frame();
        frame();
        require(observed_actions.size() == 1 &&
                    observed_actions[0].action == dolly::EditorAction::SetAttachOffsets &&
                    observed_actions[0].pose[0] == 75.25 && observed_actions[0].pose[1] == -31.25 &&
                    observed_actions[0].pose[2] == 8.75,
                "Kara's offset field lost the released value or changed another axis");
        observed_actions.clear();
        // Exercise the actual kit buttons, including a disabled group and a live safety footer.
        activate_tab(0);
        auto* camera = find("##rail-tab0");
        ctx->NavNextActivateId = camera->GetID("Capture camera here");
        frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action == dolly::EditorAction::Capture,
                "Kara's Capture button does not call Dolly");
        observed_actions.clear();
        dolly::recording_status_stub.state = dolly::video::State::recording;
        ctx->NavNextActivateId = camera->GetID("Capture camera here");
        frame();
        require(observed_actions.empty(), "Recording did not lock Kara's Capture button");
        ctx->NavNextActivateId = camera->GetID("Finish recording");
        frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action == dolly::EditorAction::StopVideo,
                "Kara's footer cannot finish recording");
        observed_actions.clear();
        dolly::recording_status_stub = {};
        snapshot.busy = true;
        ctx->NavNextActivateId = camera->GetID("Capture camera here");
        frame();
        require(observed_actions.empty(), "Waiting/working grey group emitted a camera action");
        ctx->NavNextActivateId = camera->GetID("Stop / restore");
        frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action == dolly::EditorAction::Stop,
                "Working state disabled Stop / restore");
        observed_actions.clear();
        snapshot.busy = false;
        activate_tab(4);
        auto* exports = find("##rail-tab4");
        ctx->NavNextActivateId = exports->GetID("Export USD camera (.usda)");
        frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action == dolly::EditorAction::SetVideoUsdCamera,
                "Kara's export format checkbox is disconnected");
        observed_actions.clear();
        // The custom table must own its numeric cells, preserve live Bank preview,
        // and deliver the final release after the outstanding revision is acknowledged.
        activate_tab(5);
        auto* paths = find("##rail-tab5");
        const float scale = size.y / 1080;
        const ImVec2 bank(paths->Pos.x + 334 * scale, 99 * scale);
        io.AddMousePosEvent(bank.x, bank.y);
        frame();
        frame();
        io.AddMouseButtonEvent(0, true);
        frame();
        io.AddMousePosEvent(bank.x + 16, bank.y);
        frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action == dolly::EditorAction::SetCameraRoll &&
                    observed_actions.back().revision == camera_revision_stub &&
                    observed_actions.back().time == 4,
                "Kara's Bank cell did not retain the real revision-bound live preview");
        io.AddMousePosEvent(bank.x + 24, bank.y);
        frame();
        io.AddMouseButtonEvent(0, false);
        frame();
        require(observed_actions.size() == 1, "Bank release bypassed the outstanding revision");
        ++camera_revision_stub;
        frame();
        require(observed_actions.size() == 2 &&
                    observed_actions.back().action == dolly::EditorAction::SetCameraRoll &&
                    observed_actions.back().revision == camera_revision_stub &&
                    observed_actions.back().time == 6,
                "Bank lost the final value while waiting for its preview acknowledgement");
        --camera_revision_stub;
        observed_actions.clear();
        io.AddMousePosEvent(-100, -100);
        frame();
        // The first step of a Bank drag selects its camera. The drag must
        // carry on from there, not stop when the selection changes.
        {
            require(snapshot.camera_count >= 2 && snapshot.selected_camera == 0,
                    "Bank drag fixture needs an unselected second camera");
            const ImVec2 other(bank.x, bank.y + 35 * scale);
            io.AddMousePosEvent(other.x, other.y);
            frame();
            frame();
            io.AddMouseButtonEvent(0, true);
            frame();
            io.AddMousePosEvent(other.x + 16, other.y);
            frame();
            require(observed_actions.size() == 1 &&
                        observed_actions.back().action == dolly::EditorAction::SetCameraRoll &&
                        observed_actions.back().value == 1,
                    "Bank drag on an unselected camera did not start");
            snapshot.selected_camera = 1;
            ++camera_revision_stub;
            frame();
            io.AddMousePosEvent(other.x + 32, other.y);
            frame();
            require(observed_actions.size() == 2 &&
                        observed_actions.back().action == dolly::EditorAction::SetCameraRoll &&
                        observed_actions.back().value == 1 &&
                        observed_actions.back().revision == camera_revision_stub,
                    "Bank drag stopped when its camera became the selected one");
            io.AddMouseButtonEvent(0, false);
            frame();
            ++camera_revision_stub;
            frame();
            frame();
            camera_revision_stub -= 2;
            snapshot.selected_camera = 0;
            observed_actions.clear();
            io.AddMousePosEvent(-100, -100);
            frame();
            frame();
        }
        // Curves is the live editor, with the redesigned mode tabs and bounded chrome.
        auto* bar = find("##lite-timeline");
        ctx->NavNextActivateId = bar->GetID("Curves");
        frame();
        frame();
        require(panel.timeline_mode() == 2 &&
                    find("##lite-timeline")->Size.y <= 381 * size.y / 1080 + 1,
                "Curves did not use Kara's compact graph bar");
        ctx->NavNextActivateId = bar->GetID("Replay");
        frame();
        frame();
        require(panel.timeline_mode() == 0, "Curves could not return to Replay");
        const dolly::PickerPortrait portrait{[]() noexcept {},
                                             [](const dolly::PickerFrame&) noexcept {},
                                             []() noexcept -> ImTextureID { return 0; }};
        dolly::CameraPose picker_pose{200, 300, 400, 0, 0, 0, double(size.x / size.y)};
        dolly::picker_camera(0, 0, false, false, picker_pose, 90, {}, nullptr, nullptr);
        auto catalog = std::make_shared<dolly::PickerCatalog>();
        catalog->sequence = unsigned(size.y);
        catalog->total = 4;
        dolly::PickerSample bone_sample{};
        bone_sample.count = 4;
        const char* bone_names[] = {"head", "pelvis", "hand_R", "ankle_L"};
        for (unsigned i = 0; i < 4; ++i) {
            dolly::PickerBone bone{};
            bone.source_index = i;
            std::strcpy(bone.name, bone_names[i]);
            catalog->bones.push_back(bone);
            bone_sample.transforms[i][2] = 90 - 25 * float(i);
        }
        bone_sample.transforms[2][1] = -35;
        dolly::picker_publish(catalog);
        snapshot.bone_picker = true;
        auto sample_bones = [&] {
            dolly::picker_camera(
                catalog->sequence, 10, true, true, picker_pose, 90, {},
                [](void* p, dolly::PickerSample& out, const char*&) noexcept {
                    out = *static_cast<dolly::PickerSample*>(p);
                    return true;
                },
                &bone_sample);
        };
        auto bone_frame = [&] {
            sample_bones();
            ImGui::NewFrame();
            panel.draw_bone_picker(snapshot, portrait);
            ImGui::Render();
        };
        bone_frame();
        bone_frame();
        auto* picker = find("Bone Picker");
        require(picker && std::abs(picker->Pos.x - 59 * size.y / 1080) < 1 &&
                    std::abs(picker->Size.x - 444 * size.y / 1080) < 1,
                "Bone Picker retained the old right-side window");
        dolly::PickerFrame bone_state{};
        require(dolly::picker_snapshot(bone_state) && bone_state.ready,
                "Rail picker synthetic pose is not ready");
        std::array<double, 3> bone_point{};
        dolly::VisualizationPoint hand{};
        require(dolly::picker_position(bone_state.sample, catalog->bones[2], bone_point) &&
                    dolly::project_visualization_point(
                        {bone_state.view, bone_state.fov, size.x, size.y, 1}, bone_point, hand),
                "Rail picker hand projection failed");
        auto marker_vertices = [&](ImU32 color) {
            unsigned count = 0;
            const float radius = 14 * size.y / 1080;
            for (const auto& v : ImGui::GetBackgroundDrawList()->VtxBuffer)
                if (std::abs(v.pos.x - float(hand.x)) < radius &&
                    std::abs(v.pos.y - float(hand.y)) < radius && (!color || v.col == color))
                    ++count;
            return count;
        };
        require(marker_vertices(0) > 12,
                "Rail picker clipped the scene's hand marker behind its left drawer");
        require(marker_vertices(IM_COL32(47, 92, 183, 255)) > 8 &&
                    marker_vertices(IM_COL32(255, 255, 255, 255)) > 8,
                "Rail picker lost Kara blue marker fill or its contrasting ring");
        io.AddMousePosEvent(float(hand.x), float(hand.y));
        bone_frame();
        bone_frame();
        io.AddMouseButtonEvent(0, true);
        bone_frame();
        io.AddMouseButtonEvent(0, false);
        bone_frame();
        bone_frame();
        require(dolly::picker_snapshot(bone_state) && bone_state.selected == 2,
                "Clicking the rail scene hand marker did not select its source bone");
        // The upper switcher remains the sole preview control.
        ctx->NavNextActivateId = picker->GetID("Preview attached view");
        bone_frame();
        bone_frame();
        require(dolly::picker_snapshot(bone_state) && !bone_state.preview,
                "Duplicate footer preview action is still present");
        ctx->NavNextActivateId = picker->GetID("Attached preview");
        bone_frame();
        bone_frame();
        require(dolly::picker_snapshot(bone_state) && bone_state.preview &&
                    !find("##picker-canvas"),
                "Upper attached-preview switch did not hide overview markers/canvas");
        ctx->NavNextActivateId = picker->GetID("Return to hero overview");
        bone_frame();
        bone_frame();
        require(dolly::picker_snapshot(bone_state) && bone_state.preview,
                "Duplicate footer return action is still present");
        ctx->NavNextActivateId = picker->GetID("Hero overview");
        bone_frame();
        bone_frame();
        require(dolly::picker_snapshot(bone_state) && !bone_state.preview &&
                    marker_vertices(0) > 12,
                "Returning to hero overview did not restore bone markers");
        io.AddMousePosEvent(-100, -100);
        bone_frame();
        std::printf(
            "Bone Picker %.0fx%.0f: blue circles/rings, scene click, top view switcher and removed footer passed.\n",
            size.x, size.y);
        ctx->NavNextActivateId = picker->GetID("Cancel");
        bone_frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action == dolly::EditorAction::CancelBonePicker,
                "Redesigned Bone Picker lost Cancel");
        observed_actions.clear();
        dolly::picker_camera(catalog->sequence, 10, false, true, picker_pose, 90, {}, nullptr,
                             nullptr);
        snapshot.bone_picker = false;
        snapshot.object_picker = true;
        auto object_frame = [&] {
            ImGui::NewFrame();
            panel.draw_object_picker(snapshot);
            ImGui::Render();
        };
        object_frame();
        object_frame();
        picker = find("##object-picker");
        require(picker && std::abs(picker->Pos.x - 59 * size.y / 1080) < 1,
                "Object Picker retained the old right-side window");
        ctx->NavNextActivateId = picker->GetID("Close");
        object_frame();
        require(observed_actions.size() == 1 &&
                    observed_actions.back().action == dolly::EditorAction::ObjectPickerCancel,
                "Redesigned Object Picker lost Close");
        observed_actions.clear();
        snapshot.object_picker = false;
        panel.close_rail_drawers();
        settle();
        require(!panel.rail_drawers_open() && panel.rail_tab_rect(0).w < 100 * size.y / 1080,
                "Closing drawers left a live drawer");
        if (output && size.x == (motion ? 1280 : 1920)) {
            ID3D11Device* device = nullptr;
            ID3D11DeviceContext* context = nullptr;
            D3D_FEATURE_LEVEL level{};
            require(SUCCEEDED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr,
                                                0, D3D11_SDK_VERSION, &device, &level, &context)),
                    "Rail screenshot WARP device failed");
            require(ImGui_ImplDX11_Init(device, context), "Rail screenshot renderer failed");
            ImGui_ImplDX11_NewFrame();
            auto* background = dolly::load_rail_background(device);
            require(background != nullptr, "Rail background resource missing");
            D3D11_TEXTURE2D_DESC desc{};
            desc.Width = unsigned(size.x);
            desc.Height = unsigned(size.y);
            desc.MipLevels = desc.ArraySize = 1;
            desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
            desc.SampleDesc.Count = 1;
            desc.BindFlags = D3D11_BIND_RENDER_TARGET;
            ID3D11Texture2D* target = nullptr;
            ID3D11RenderTargetView* view = nullptr;
            require(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &target)),
                    "Rail screenshot target failed");
            require(SUCCEEDED(device->CreateRenderTargetView(target, nullptr, &view)),
                    "Rail screenshot view failed");
            if (motion) {
                panel.close_rail_drawers();
                io.DeltaTime = 1.f / 60;
                settle();
            }
            for (unsigned shot = 0; shot < (motion ? 180u : 13u); ++shot) {
                const unsigned tab = motion ? 0 : shot;
                if (motion) {
                    const float scale = size.y / 1080;
                    io.AddMousePosEvent(shot < 12 ? 80 * scale : -100,
                                        shot < 12 ? 20 * scale : -100);
                    int change = -1;
                    if (shot == 20 || shot == 55)
                        change = 0;
                    if (shot == 45)
                        change = 1;
                    if (shot == 62)
                        change = 3;
                    if (shot == 90 || shot == 130)
                        change = 5;
                    if (shot == 120 || shot == 150)
                        panel.close_rail_drawers();
                    if (change >= 0) {
                        char name[32];
                        std::snprintf(name, sizeof(name), "##rail-tab%d", change);
                        ctx->NavNextActivateId = find(name)->GetID("##rail-button");
                    }
                } else if (tab < 6)
                    activate_tab(tab);
                else if (tab == 6) {
                    panel.close_rail_drawers();
                    settle();
                    viewer.publish(curve_packet());
                    viewer.tick();
                    snapshot.camera_count = 10;
                    snapshot.duration = 27;
                    snapshot.selected_camera = 0;
                    auto* bar = find("##lite-timeline");
                    ctx->NavNextActivateId = bar->GetID("Curves");
                    frame();
                    settle();
                    require(panel.curve_plot_rect(0).z > panel.curve_plot_rect(0).x,
                            "Redesigned Curves did not draw the real published graph");
                } else if (tab == 7) {
                    auto* bar = find("##lite-timeline");
                    ctx->NavNextActivateId = bar->GetID("Stack graphs");
                    frame();
                    frame();
                    require(panel.curve_lane_count() == 2,
                            "Stack graphs lost its published graph lanes");
                } else if (tab == 8)
                    snapshot.bone_picker = true;
                else if (tab == 9) {
                    snapshot.bone_picker = false;
                    snapshot.object_picker = true;
                }
                if (tab == 11) {
                    snapshot.bone_picker = snapshot.object_picker = false;
                    snapshot.scene_appearance_flags = 7;
                    activate_tab(3);
                }
                if (tab == 12) {
                    // Export drawer while a RenderLock .crav export runs (Dolly busy).
                    snapshot.scene_appearance_flags = 0;
                    snapshot.busy = snapshot.renderlock_exporting = true;
                    std::snprintf(snapshot.message, sizeof(snapshot.message), "%s",
                                  "Exporting camera: Dolly plays the shot once without recording video...");
                    activate_tab(4);
                }
                if (tab == 5) {
                    ctx->NavNextActivateId = find("##rail-tab0")->GetID("##rail-button");
                    frame();
                    settle();
                }
                ImGui_ImplDX11_NewFrame();
                if (tab == 8) {
                    sample_bones();
                    dolly::picker_select(catalog->sequence, 2);
                    sample_bones();
                }
                ImGui::NewFrame();
                if (tab == 8)
                    panel.draw_bone_picker(snapshot, portrait);
                else if (tab == 9)
                    panel.draw_object_picker(snapshot);
                else if (tab == 10)
                    panel.draw_shader_notice();
                else
                    panel.draw_rail(snapshot, reinterpret_cast<ImTextureID>(background));
                ImGui::Render();
                const float clear[] = {.08f, .09f, .1f, 1};
                context->ClearRenderTargetView(view, clear);
                context->OMSetRenderTargets(1, &view, nullptr);
                ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
                desc.BindFlags = 0;
                desc.Usage = D3D11_USAGE_STAGING;
                desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
                ID3D11Texture2D* readback = nullptr;
                require(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &readback)),
                        "Rail readback failed");
                context->CopyResource(readback, target);
                D3D11_MAPPED_SUBRESOURCE pixels{};
                require(SUCCEEDED(context->Map(readback, 0, D3D11_MAP_READ, 0, &pixels)),
                        "Rail map failed");
                std::ofstream file(std::string(output) + "-" + std::to_string(shot) + ".ppm",
                                   std::ios::binary);
                file << "P6\n" << unsigned(size.x) << " " << unsigned(size.y) << "\n255\n";
                for (unsigned y = 0; y < unsigned(size.y); ++y)
                    for (unsigned x = 0; x < unsigned(size.x); ++x)
                        file.write(static_cast<const char*>(pixels.pData) + y * pixels.RowPitch +
                                       x * 4,
                                   3);
                context->Unmap(readback, 0);
                readback->Release();
            }
            background->Release();
            view->Release();
            target->Release();
            ImGui_ImplDX11_Shutdown();
            context->ClearState();
            context->Release();
            device->Release();
        }
        dolly::picker_camera(catalog->sequence, 10, false, true, picker_pose, 90, {}, nullptr,
                             nullptr);
        ImGui::DestroyContext(ctx);
        std::printf(
            "Rail %.0fx%.0f: six drawers, live transport, keyboard, state guards, vertical aim revision and close passed.\n",
            size.x, size.y);
    }
    ImGui::SetCurrentContext(previous);
    snapshot = old_snapshot;
    observed_actions.clear();
}
