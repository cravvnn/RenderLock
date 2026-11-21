#pragma once
#include <cstdint>

namespace dolly {
struct OverlayDiagnostics {
    std::uint64_t present_calls = 0, panel_frames = 0, init_attempts = 0, init_successes = 0,
                  release_calls = 0, resize_calls = 0;
    std::uint64_t draw_frames = 0, guide_frames = 0, overlay_last_us = 0, overlay_max_us = 0;
    std::uint64_t present_last_us = 0, present_max_us = 0, lock_skips = 0;
    std::uint64_t overlay_active_since_ms = 0, present_active_since_ms = 0, guide_lines = 0,
                  guide_labels = 0;
    // Last drawn Shot-timeline rect (screen coordinates) and committed camera
    // tick drags. Native-internal diagnostics; not part of any wire ABI.
    float timeline_x0 = 0, timeline_x1 = 0, timeline_y = 0;
    std::uint64_t timeline_drags = 0;
    float timeline_view_start = 0, timeline_view_end = 0;
    std::uint64_t grid_frames = 0;
};
// Independent monotonic counters; sampled off the render thread.
OverlayDiagnostics overlay_diagnostics() noexcept;
// Called by the native worker only after game fingerprints, development flags,
// the camera guard and MinHook initialization have succeeded. Never DllMain.
// Failure leaves the camera bridge and external editor available.
bool install_overlay_hooks() noexcept;

// Suspends the overlay and releases its device resources. Hook code remains
// resident until process exit, like the native camera callback, to avoid
// unhook/unload races with another renderer or overlay.
void shutdown_overlay() noexcept;
const char* overlay_last_error() noexcept;

// Renderer pressure guard. The read-only renderer probe sets this when the
// game's DirectX 11 vertex-buffer retirement queue approaches its 16-bit
// capacity (an engine-side limit that otherwise fatals with
// "EnsureCapacity allocation count overflow"). While set, the overlay releases
// its optional effect device objects and stops submitting draw data so the
// engine has the best chance to drain the queue. It clears automatically once
// the queue falls well below the threshold. Camera control is unaffected.
void overlay_set_renderer_pressure(bool high) noexcept;
bool overlay_renderer_pressure() noexcept;
// Presentation-only Escape handoff; never changes the shared-memory action ABI.
bool overlay_close_drawers_on_escape() noexcept;

// Raw-input games may suppress legacy window mouse/key messages. Input hooks
// enqueue these events; only the render/window callback accesses ImGui.
// Mouse indices: left 0, right 1, middle 2, side 1 3, side 2 4.
void overlay_raw_mouse_button(int button, bool down) noexcept;
void overlay_raw_mouse_wheel(float vertical, float horizontal = 0) noexcept;
void overlay_raw_key(unsigned virtual_key, unsigned scan_code, bool down,
                     bool extended = false) noexcept;
}
