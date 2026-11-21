// DirectX 11 in-game editor. This is an overlay on the game's swapchain, not a
// second window, console-command renderer, or replacement for path evaluation.
#include "dolly_overlay.hpp"
#include "dolly_overlay_panel.hpp"
#include "dolly_overlay_assets.hpp"
#include "dolly_editor.hpp"
#include "dolly_bone_picker.hpp"
#include "dolly_visualization.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_video.hpp"
#include "dolly_depth_live.hpp"
#include "dolly_depth_scene.hpp"
#include "dolly_render_class.hpp"
#include "dolly_player_capture.hpp"
#include "dolly_media.hpp"
#include "dolly_stream.hpp"
#include "dolly_reshade.hpp"
#include "MinHook.h"
#include <d3d11_1.h>
#include <dxgi.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>
#include "imgui.h"
#include "imgui_impl_dx11.h"
#include "imgui_impl_win32.h"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

namespace dolly {
namespace {
using PresentFn = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT);
using ResizeFn = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT, UINT, DXGI_FORMAT, UINT);
PresentFn original_present = nullptr;
ResizeFn original_resize = nullptr;
std::atomic<bool> installed{false}, enabled{false}, resizing{false};
// Set by the renderer probe when the engine's vertex-buffer retirement queue
// nears its 16-bit capacity. Stops Dolly's optional draw submissions.
std::atomic<bool> renderer_pressure{false};
std::atomic<bool> rail_drawers_open{false}, rail_escape_requested{false};
bool rail_background_attempted = false; // render-lock-owned, reset with the device
std::atomic<std::uint64_t> diagnostic_present{0}, diagnostic_panel{0}, diagnostic_init_attempts{0},
    diagnostic_init_successes{0}, diagnostic_releases{0}, diagnostic_resizes{0};
std::atomic<std::uint64_t> diagnostic_draw{0}, diagnostic_guides{0}, diagnostic_overlay_last_us{0},
    diagnostic_overlay_max_us{0}, diagnostic_present_last_us{0}, diagnostic_present_max_us{0},
    diagnostic_lock_skips{0}, diagnostic_overlay_active_ms{0}, diagnostic_present_active_ms{0},
    diagnostic_guide_lines{0}, diagnostic_guide_labels{0}, diagnostic_grid{0};
std::atomic<const char*> last_error{"Waiting for the DirectX 11 game window."};
std::recursive_mutex render_mutex;
struct InputMessage {
    HWND window;
    UINT message = 0;
    WPARAM wparam = 0;
    LPARAM lparam = 0;
    unsigned raw_kind = 0;
    int button = 0;
    bool down = false;
    float vertical = 0, horizontal = 0;
};
std::mutex input_mutex;
std::deque<InputMessage> pending_input;
bool input_overflow = false;
IDXGISwapChain* swapchain = nullptr; // Identity only: do not retain a dead game's swapchain.
UINT target_width = 0, target_height = 0;
ID3D11Device* device = nullptr;
ID3D11DeviceContext* immediate = nullptr;
ID3D11DeviceContext1* context1 = nullptr;
ID3DDeviceContextState* overlay_state = nullptr;
ID3D11RenderTargetView* target = nullptr;
ID3D11Texture2D* picker_portrait_texture = nullptr;
ID3D11ShaderResourceView* picker_portrait_view = nullptr;
ID3D11ShaderResourceView* rail_background_view = nullptr;
DXGI_FORMAT picker_portrait_format = DXGI_FORMAT_UNKNOWN;
std::uint32_t picker_portrait_captured_request = 0;
ImGuiContext* imgui = nullptr;
OverlayPanel panel_ui;
std::atomic<HWND> game_window{nullptr};
bool win32_ready = false, dx11_ready = false, last_panel = false;
// Live depth stays default-off until the opt-in controls land. The state
// object only records ownership; a null tracker keeps any installed hooks
// inert.
depth::LiveDepth depth_live;
std::shared_ptr<depth::SceneTracker> depth_tracker;
bool media_suspended = false;
ULONGLONG next_initialization = 0;
constexpr wchar_t kWindowProperty[] = L"DeadlockDolly.Overlay.OriginalWindowProcedure";

// Serialize our ImGui context across Present and window-message callbacks,
// restoring the prior context around every call into our backends. Other DLLs
// embedding ImGui retain their own context and render state.
struct GuiContextScope {
    ImGuiContext* previous;
    explicit GuiContextScope(ImGuiContext* current) : previous(ImGui::GetCurrentContext()) {
        ImGui::SetCurrentContext(current);
    }
    ~GuiContextScope() { ImGui::SetCurrentContext(previous); }
};

// Distinguish work inside Dolly from a slow original Present. These counters
// are observational only: they never skip graphics work, flush the device, or
// change replay speed. The active timestamp also survives a stalled call.
struct DiagnosticTimer {
    std::atomic<std::uint64_t>& last;
    std::atomic<std::uint64_t>& maximum;
    std::atomic<std::uint64_t>& active_since;
    LARGE_INTEGER begin{};
    static LONGLONG frequency() noexcept {
        static const LONGLONG value = []() {
            LARGE_INTEGER f{};
            QueryPerformanceFrequency(&f);
            return f.QuadPart;
        }();
        return value;
    }
    DiagnosticTimer(std::atomic<std::uint64_t>& last_value,
                    std::atomic<std::uint64_t>& maximum_value,
                    std::atomic<std::uint64_t>& active_value) noexcept
        : last(last_value), maximum(maximum_value), active_since(active_value) {
        active_since.store(GetTickCount64(), std::memory_order_relaxed);
        QueryPerformanceCounter(&begin);
    }
    ~DiagnosticTimer() {
        LARGE_INTEGER end{};
        QueryPerformanceCounter(&end);
        const auto hz = frequency();
        const auto duration =
            hz > 0 && end.QuadPart >= begin.QuadPart
                ? std::uint64_t(double(end.QuadPart - begin.QuadPart) * 1000000.0 / double(hz))
                : 0;
        last.store(duration, std::memory_order_relaxed);
        auto prior = maximum.load(std::memory_order_relaxed);
        while (prior < duration &&
               !maximum.compare_exchange_weak(prior, duration, std::memory_order_relaxed)) {
        }
        active_since.store(0, std::memory_order_relaxed);
    }
};

template <typename T> void release(T*& object) noexcept {
    if (object) {
        object->Release();
        object = nullptr;
    }
}
LRESULT CALLBACK window_proc(HWND, UINT, WPARAM, LPARAM);

void release_target() noexcept {
    release(target);
}
void clear_pending_input() {
    std::lock_guard<std::mutex> lock(input_mutex);
    pending_input.clear();
    input_overflow = false;
}
void feed_window_input(const InputMessage& message) {
    // The official Win32 mouse handler changes OS capture and cursor ownership.
    // Never replay those side effects from Present: the game's window thread
    // may be waiting for this frame. Dolly also must not release a mouse capture
    // that belongs to Deadlock's hero/replay UI. Mouse events only update ImGui.
    auto& io = ImGui::GetIO();
    const auto msg = message.message;
    // Tab is a game key in Lite, never a request to focus ImGui controls.
    // ImGui tabbing remains enabled even without NavEnableKeyboard.
    if (panel_ui.lite_mode() && message.wparam == VK_TAB &&
        (msg == WM_KEYDOWN || msg == WM_KEYUP || msg == WM_SYSKEYDOWN || msg == WM_SYSKEYUP))
        return;
    int button = -1;
    bool down = false;
    switch (msg) {
    case WM_LBUTTONDOWN:
    case WM_LBUTTONDBLCLK:
        button = 0;
        down = true;
        break;
    case WM_RBUTTONDOWN:
    case WM_RBUTTONDBLCLK:
        button = 1;
        down = true;
        break;
    case WM_MBUTTONDOWN:
    case WM_MBUTTONDBLCLK:
        button = 2;
        down = true;
        break;
    case WM_XBUTTONDOWN:
    case WM_XBUTTONDBLCLK:
        button = HIWORD(message.wparam) == XBUTTON1 ? 3 : 4;
        down = true;
        break;
    case WM_LBUTTONUP:
        button = 0;
        break;
    case WM_RBUTTONUP:
        button = 1;
        break;
    case WM_MBUTTONUP:
        button = 2;
        break;
    case WM_XBUTTONUP:
        button = HIWORD(message.wparam) == XBUTTON1 ? 3 : 4;
        break;
    case WM_MOUSEMOVE:
        io.AddMousePosEvent(float(static_cast<short>(LOWORD(message.lparam))),
                            float(static_cast<short>(HIWORD(message.lparam))));
        return;
    case WM_MOUSEWHEEL:
        io.AddMouseWheelEvent(0, float(static_cast<short>(HIWORD(message.wparam))) / WHEEL_DELTA);
        return;
    case WM_MOUSEHWHEEL:
        io.AddMouseWheelEvent(-float(static_cast<short>(HIWORD(message.wparam))) / WHEEL_DELTA, 0);
        return;
    case WM_SETCURSOR:
        return; // Native window-message handling owns the OS cursor.
    default:
        break;
    }
    if (button >= 0) {
        io.AddMouseButtonEvent(button, down);
        return;
    }
    // Keyboard, text and focus handling do not change Win32 mouse ownership.
    if ((msg >= WM_KEYFIRST && msg <= WM_KEYLAST) || msg == WM_SETFOCUS || msg == WM_KILLFOCUS)
        ImGui_ImplWin32_WndProcHandler(message.window, msg, message.wparam, message.lparam);
}
void feed_pending_input() {
    std::deque<InputMessage> messages;
    bool overflow = false;
    {
        std::lock_guard<std::mutex> lock(input_mutex);
        messages.swap(pending_input);
        overflow = input_overflow;
        input_overflow = false;
    }
    if (overflow) {
        ImGui::GetIO().ClearInputKeys();
        ImGui::GetIO().ClearInputMouse();
    }
    for (const auto& message : messages)
        if (message.window == game_window) {
            auto& io = ImGui::GetIO();
            if (message.raw_kind == 1)
                io.AddMouseButtonEvent(message.button, message.down);
            else if (message.raw_kind == 2)
                io.AddMouseWheelEvent(message.horizontal, message.vertical);
            else {
                feed_window_input(message);
                if (message.raw_kind == 3) {
                    io.AddKeyEvent(ImGuiMod_Ctrl, (GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0);
                    io.AddKeyEvent(ImGuiMod_Shift, (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0);
                    io.AddKeyEvent(ImGuiMod_Alt, (GetAsyncKeyState(VK_MENU) & 0x8000) != 0);
                    io.AddKeyEvent(ImGuiMod_Super, (GetAsyncKeyState(VK_LWIN) & 0x8000) ||
                                                       (GetAsyncKeyState(VK_RWIN) & 0x8000));
                }
            }
        }
}
void reset_depth_lifecycle() noexcept {
    depth_tracker.reset();
    depth::set_scene_tracker(nullptr);
    depth::set_white_clear(false);
    classify::probe(false);
    depth_live.clear_device();
}
void release_device() noexcept {
    rail_drawers_open.store(false);
    rail_escape_requested.store(false);
    rail_background_attempted = false;
    release(rail_background_view);
    reset_depth_lifecycle();
    video::reset_resources();
    dolly::stream::reset_resources();
    reshade_release_device();
    if (device || imgui)
        diagnostic_releases.fetch_add(1, std::memory_order_relaxed);
    editor_overlay_available(false);
    editor_text_input_active(false);
    clear_pending_input();
    release_target();
    release(picker_portrait_view);
    release(picker_portrait_texture);
    picker_portrait_format = DXGI_FORMAT_UNKNOWN;
    picker_portrait_captured_request = 0;
    if (imgui) {
        panel_ui.cancel_position_graph();
        auto* previous = ImGui::GetCurrentContext();
        ImGui::SetCurrentContext(imgui);
        panel_ui.flush_layout(true);
        if (dx11_ready)
            ImGui_ImplDX11_Shutdown();
        if (win32_ready)
            ImGui_ImplWin32_Shutdown();
        ImGui::DestroyContext(imgui);
        ImGui::SetCurrentContext(previous == imgui ? nullptr : previous);
    }
    imgui = nullptr;
    panel_ui.reset_fonts();
    dx11_ready = win32_ready = last_panel = false;
    release(overlay_state);
    release(context1);
    release(immediate);
    release(device);
    if (game_window && IsWindow(game_window)) {
        auto previous = reinterpret_cast<WNDPROC>(GetPropW(game_window, kWindowProperty));
        // Another overlay may have chained our callback. Never overwrite its hook.
        // In that case the property stays until WM_NCDESTROY and our callback simply
        // forwards. The module is pinned so that chained callback remains valid.
        if (previous && reinterpret_cast<WNDPROC>(GetWindowLongPtrW(game_window, GWLP_WNDPROC)) ==
                            window_proc) {
            SetWindowLongPtrW(game_window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(previous));
            RemovePropW(game_window, kWindowProperty);
        }
    }
    game_window = nullptr;
    swapchain = nullptr;
    editor_attach_window(nullptr);
}

bool suitable_window(HWND window) noexcept {
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    RECT bounds{};
    return pid == GetCurrentProcessId() && IsWindowVisible(window) &&
           GetAncestor(window, GA_ROOT) == window && GetClientRect(window, &bounds) &&
           bounds.right - bounds.left >= 320 && bounds.bottom - bounds.top >= 200;
}

bool create_target(IDXGISwapChain* chain) noexcept {
    ID3D11Texture2D* buffer = nullptr;
    if (FAILED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&buffer))))
        return false;
    D3D11_TEXTURE2D_DESC description{};
    buffer->GetDesc(&description);
    const HRESULT result = device->CreateRenderTargetView(buffer, nullptr, &target);
    buffer->Release();
    target_width = SUCCEEDED(result) ? description.Width : 0;
    target_height = SUCCEEDED(result) ? description.Height : 0;
    return SUCCEEDED(result);
}

// Upload the installed game's small hero icon once per picker request.
// The catalog worker has already read/validated pixels; no disk or engine calls here.
void update_picker_portrait(const PickerFrame& frame) noexcept {
    if (!frame.ready || !frame.catalog || !device ||
        picker_portrait_captured_request == frame.catalog->sequence)
        return;
    picker_portrait_captured_request = frame.catalog->sequence;
    const auto& portrait = frame.catalog->portrait;
    if (portrait.bgra.empty())
        return;
    D3D11_TEXTURE2D_DESC image{};
    image.Width = portrait.width;
    image.Height = portrait.height;
    image.MipLevels = image.ArraySize = 1;
    image.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    image.SampleDesc.Count = 1;
    image.Usage = D3D11_USAGE_IMMUTABLE;
    image.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA pixels{};
    pixels.pSysMem = portrait.bgra.data();
    pixels.SysMemPitch = portrait.width * 4;
    if (FAILED(device->CreateTexture2D(&image, &pixels, &picker_portrait_texture)) ||
        FAILED(device->CreateShaderResourceView(picker_portrait_texture, nullptr,
                                                &picker_portrait_view))) {
        release(picker_portrait_view);
        release(picker_portrait_texture);
    }
}

void reset_picker_portrait() noexcept {
    release(picker_portrait_view);
    release(picker_portrait_texture);
    picker_portrait_format = DXGI_FORMAT_UNKNOWN;
    picker_portrait_captured_request = 0;
}
ImTextureID picker_portrait_id() noexcept {
    return reinterpret_cast<ImTextureID>(picker_portrait_view);
}
const PickerPortrait picker_portrait{reset_picker_portrait, update_picker_portrait, picker_portrait_id};

// A complete state swap is preferable to assuming what the game or a ReShade
// effect left bound at Present. Restore before the original Present executes.
struct DeviceStateScope {
    ID3DDeviceContextState* saved = nullptr;
    DeviceStateScope() { context1->SwapDeviceContextState(overlay_state, &saved); }
    ~DeviceStateScope() {
        // Context-state objects retain bound targets too. Clear our target before
        // swapping away so ResizeBuffers can release the last backbuffer reference.
        immediate->OMSetRenderTargets(0, nullptr, nullptr);
        context1->SwapDeviceContextState(saved, nullptr);
        release(saved);
    }
};

// Diagnostic-only timing for the overlay startup path. Inert unless the game is
// launched with DOLLY_OVERLAY_TIMING=1; writes to %TEMP%\dolly_overlay_timing.log.
void overlay_timing(const char* message) {
    if (GetEnvironmentVariableW(L"DOLLY_OVERLAY_TIMING", nullptr, 0) == 0)
        return;
    wchar_t dir[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, dir) == 0)
        return;
    wchar_t path[MAX_PATH]{};
    _snwprintf_s(path, MAX_PATH, _TRUNCATE, L"%sdolly_overlay_timing.log", dir);
    FILE* file = nullptr;
    if (_wfopen_s(&file, path, L"a") == 0 && file) {
        std::fprintf(file, "%s\n", message);
        std::fclose(file);
    }
}

bool initialize_device(IDXGISwapChain* chain) {
    const ULONGLONG t_start = GetTickCount64();
    ULONGLONG t_phase = t_start;
    auto mark = [&](const char* label) {
        const ULONGLONG now = GetTickCount64();
        char text[160]{};
        std::snprintf(text, sizeof(text), "init %-28s %5llu ms", label, now - t_phase);
        overlay_timing(text);
        t_phase = now;
    };
    diagnostic_init_attempts.fetch_add(1, std::memory_order_relaxed);
    DXGI_SWAP_CHAIN_DESC description{};
    if (FAILED(chain->GetDesc(&description)) || !suitable_window(description.OutputWindow))
        return false;
    if (FAILED(chain->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&device)))) {
        last_error = "The presenting game window is not using DirectX 11.";
        return false;
    }
    device->GetImmediateContext(&immediate);
    ID3D11Device1* device1 = nullptr;
    if (!immediate ||
        FAILED(
            device->QueryInterface(__uuidof(ID3D11Device1), reinterpret_cast<void**>(&device1))) ||
        FAILED(immediate->QueryInterface(__uuidof(ID3D11DeviceContext1),
                                         reinterpret_cast<void**>(&context1)))) {
        last_error = "The DirectX 11.1 context API is unavailable; in-game editor disabled.";
        release(device1);
        release_device();
        return false;
    }
    const D3D_FEATURE_LEVEL feature = device->GetFeatureLevel();
    D3D_FEATURE_LEVEL chosen{};
    const HRESULT state_result = device1->CreateDeviceContextState(
        0, &feature, 1, D3D11_SDK_VERSION, __uuidof(ID3D11Device), &chosen, &overlay_state);
    device1->Release();
    if (FAILED(state_result)) {
        last_error = "Could not create an isolated DirectX state for the editor.";
        release_device();
        return false;
    }
    // Save ALL D3D state with the Windows 10 D3D11.1 context API. The upstream
    // renderer additionally saves its draw state, but does not restore HS/DS/CS
    // shaders or output UAVs. Context swapping covers those and other overlays.
    game_window = description.OutputWindow;
    swapchain = chain;
    mark("device_state");
    if (!create_target(chain)) {
        last_error = "Could not create the editor render target.";
        release_device();
        return false;
    }
    mark("create_target");
    IMGUI_CHECKVERSION();
    auto* previous = ImGui::GetCurrentContext();
    imgui = ImGui::CreateContext();
    {
        GuiContextScope scope(imgui);
        auto& io = ImGui::GetIO();
        io.IniFilename = nullptr;
        io.LogFilename = nullptr;
        io.ConfigFlags = ImGuiConfigFlags_NoMouseCursorChange;
        panel_ui.style_panel();
        panel_ui.initialize_layout();
        const float panel_scale = std::clamp(ImGui_ImplWin32_GetDpiScaleForHwnd(game_window), 1.0f, 2.5f);
        panel_ui.load_panel_fonts(panel_scale);
        ImGui::GetStyle().ScaleAllSizes(panel_scale);
        win32_ready = ImGui_ImplWin32_Init(game_window);
        if (win32_ready)
            dx11_ready = ImGui_ImplDX11_Init(device, immediate);
    }
    ImGui::SetCurrentContext(previous);
    if (!win32_ready || !dx11_ready) {
        last_error = "Could not initialize the DirectX 11 editor UI.";
        release_device();
        return false;
    }
    mark("imgui_fonts");
    bool gpu_ready = false;
    {
        GuiContextScope scope(imgui);
        DeviceStateScope graphics;
        gpu_ready = ImGui_ImplDX11_CreateDeviceObjects();
    }
    if (!gpu_ready) {
        last_error =
            "Could not compile or create the editor shaders; in-game controls remain disabled.";
        release_device();
        return false;
    }
    mark("device_objects");
    // Install exactly once on the actual swapchain window. A property retains
    // its original callback even if another overlay chains us during a resize.
    auto prior = reinterpret_cast<WNDPROC>(GetWindowLongPtrW(game_window, GWLP_WNDPROC));
    auto retained = reinterpret_cast<WNDPROC>(GetPropW(game_window, kWindowProperty));
    if (retained && prior == retained) {
        RemovePropW(game_window, kWindowProperty);
        retained = nullptr;
    }
    // If a second overlay chained our window procedure, it remains in that
    // chain after device loss. Reuse it instead of wrapping the chain twice.
    if (!retained) {
        if (!prior || !SetPropW(game_window, kWindowProperty, reinterpret_cast<HANDLE>(prior))) {
            last_error = "Could not attach the editor to the game window.";
            release_device();
            return false;
        }
        SetLastError(0);
        auto replaced =
            SetWindowLongPtrW(game_window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(window_proc));
        if (!replaced && GetLastError() != 0) {
            last_error = "Could not attach editor input to the game window.";
            RemovePropW(game_window, kWindowProperty);
            release_device();
            return false;
        }
        // Preserve the actual previous callback if another hook raced the read.
        if (replaced)
            SetPropW(game_window, kWindowProperty, reinterpret_cast<HANDLE>(replaced));
    }
    last_error = "";
    editor_attach_window(game_window);
    editor_overlay_available(true);
    diagnostic_init_successes.fetch_add(1, std::memory_order_relaxed);
    mark("window_attach");
    {
        char text[160]{};
        std::snprintf(text, sizeof(text), "init TOTAL                     %5llu ms",
                      GetTickCount64() - t_start);
        overlay_timing(text);
    }
    return true;
}

bool try_initialize_device(IDXGISwapChain* chain) {
    if (GetTickCount64() < next_initialization)
        return false;
    if (initialize_device(chain)) {
        next_initialization = 0;
        return true;
    }
    next_initialization = GetTickCount64() + 2000;
    return false;
}
void render_overlay(IDXGISwapChain* chain) {
    const auto state = editor_snapshot();
    if (!state.enabled || !state.focused || state.owner != EditorOwner::Panel ||
        state.bone_picker || state.object_picker || renderer_pressure.load())
        rail_drawers_open.store(false, std::memory_order_release);
    if (resizing.load(std::memory_order_acquire))
        return;
    const bool media_live = media_session_active() || dolly::stream::active();
    if (!media_live)
        depth::set_white_clear(false);
    if (!state.enabled && !media_live && !panel_ui.shader_notice_visible(state)) {
        panel_ui.cancel_position_graph();
        // Playback may disable manual editor input. Only a lost session retires
        // media here; control ownership and camera readiness are transient.
        if (!media_suspended && chain == swapchain && immediate) {
            reset_depth_lifecycle();
            video::reset_resources();
            dolly::stream::reset_resources();
            reshade_set_enabled(false);
            if (context1 && overlay_state) {
                DeviceStateScope graphics_scope;
                reshade_release_device();
            } else
                reshade_release_device();
            media_suspended = true;
        }
        return;
    }
    if (renderer_pressure.load(std::memory_order_acquire)) {
        panel_ui.cancel_position_graph();
        // The engine's DirectX 11 vertex-buffer retirement queue is near its
        // 16-bit capacity. Stop all optional Dolly rendering (effects, capture
        // and the editor) so the engine can retire queued buffers and avoid the
        // fatal "EnsureCapacity allocation count overflow". The editor itself
        // stays alive and resumes automatically once pressure clears.
        if (!media_suspended && chain == swapchain && immediate) {
            reset_depth_lifecycle();
            video::reset_resources();
            dolly::stream::reset_resources();
            reshade_set_enabled(false);
            if (context1 && overlay_state) {
                DeviceStateScope graphics_scope;
                reshade_release_device();
            } else {
                reshade_release_device();
            }
            media_suspended = true;
        }
        return;
    }
    media_suspended = false;
    if (!swapchain) {
        if (!try_initialize_device(chain))
            return;
    }
    if (chain != swapchain) {
        DXGI_SWAP_CHAIN_DESC replacement{};
        if (FAILED(chain->GetDesc(&replacement)) || replacement.OutputWindow != game_window ||
            !suitable_window(replacement.OutputWindow))
            return;
        release_device();
        if (!try_initialize_device(chain))
            return;
    }
    if (!target && !create_target(chain))
        return;
    player_capture::present_boundary(immediate);
    // Metadata hooks start after verified startup, before replay shaders are
    // created. Scene capture still requires its separately bounded request.
    if (player_capture::layout_hook_requested())
        player_capture::install_layout_hook(device);
    if (player_capture::draw_hooks_requested())
        player_capture::draw_hooks_result(depth::install_scene_hooks(device, immediate));
    if (player_capture::producer_hook_requested())
        player_capture::install_producer_hook();
    // Scene depth is consumed once per Present here, outside ReShade's add-on
    // event callbacks: ReShade gates those events while it detects high
    // non-local network traffic (its online depth protection), but Dolly
    // publishes its own verified scene depth through the runtime API, which is
    // not gated. Recording capture still uses the post-effects callback below.
    depth::SceneFrame scene_frame{};
    const depth::SceneFrame* scene_sample = nullptr;
    {
        const auto editor = editor_snapshot();
        const auto snapshot = video::status();
        // A requested take may start while desktop controls own focus. The
        // validated game swapchain supplies frames independently of input focus;
        // do not block initialization while the controller waits for recording.
        if (media_session_active() &&
            (snapshot.state == video::State::starting ||
             snapshot.state == video::State::recording || editor.focused)) {
            // The recorder reports the size only once a take exists. ReShade
            // also needs depth while merely editing, so fall back to the
            // current backbuffer dimensions.
            UINT depth_width = snapshot.width, depth_height = snapshot.height;
            if ((!depth_width || !depth_height) && chain) {
                ID3D11Texture2D* buffer = nullptr;
                if (SUCCEEDED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                               reinterpret_cast<void**>(&buffer)))) {
                    D3D11_TEXTURE2D_DESC desc{};
                    buffer->GetDesc(&desc);
                    depth_width = desc.Width;
                    depth_height = desc.Height;
                    buffer->Release();
                }
            }
            if (depth_width && depth_height) {
                depth_live.publish(reinterpret_cast<std::uintptr_t>(device),
                                   reinterpret_cast<std::uintptr_t>(immediate), depth_width,
                                   depth_height);
                // Depth observation also feeds ReShade's DEPTH semantic while
                // its runtime is active, so depth-dependent effects work
                // without ReShade's disabled automatic graphics hooks.
                depth_live.request(video::wants_depth() || reshade_available());
                depth::set_white_clear(video::wants_white_clear());
                const bool matte_hooks =
                    (video::wants_white_clear() || video::wants_shot_only()) && !depth_live.hooks();
                if ((depth_live.needs_hooks() || matte_hooks) &&
                    depth::install_scene_hooks(device, immediate))
                    depth_live.note_hooks(true);
                depth::scene_note_hooks(depth_live.hooks());
                if (depth_live.needs_tracker()) {
                    auto tracker = std::make_shared<depth::SceneTracker>(device, depth_live.width(),
                                                                         depth_live.height());
                    depth_tracker = tracker;
                    depth::set_scene_tracker(tracker);
                    depth_live.note_tracker(tracker != nullptr);
                } else if (depth_live.needs_drop()) {
                    depth_tracker.reset();
                    depth::set_scene_tracker(nullptr);
                    reshade_set_scene_depth(nullptr);
                    depth_live.note_tracker(false);
                }
                // The depth take is also the draw-classification probe window
                // used to design layer filters. ReShade-only depth does not
                // need that probe.
                classify::probe(depth_live.active() && video::wants_depth());
            }
            // Consume the verified scene sample for this exact Present before
            // recording. The recorder owns color/depth pairing and fails closed
            // when a requested depth frame is missing or ambiguous.
            if (depth_live.active() && depth_tracker) {
                scene_frame = depth_tracker->consume(immediate);
                scene_sample = &scene_frame;
                if (scene_frame.result == depth::SceneResult::ready)
                    reshade_set_scene_depth(scene_frame.texture());
                char depth_debug[176]{};
                std::snprintf(depth_debug, sizeof(depth_debug),
                              "consume: result=%d tex=%p live=%d tracker=%d",
                              static_cast<int>(scene_frame.result),
                              static_cast<const void*>(scene_frame.texture()),
                              depth_live.active() ? 1 : 0, depth_tracker ? 1 : 0);
                reshade_depth_debug(depth_debug);
            } else {
                char depth_debug[176]{};
                std::snprintf(depth_debug, sizeof(depth_debug),
                              "consume: skipped live=%d tracker=%d requested=%d hooks=%d",
                              depth_live.active() ? 1 : 0, depth_tracker ? 1 : 0,
                              depth_live.requested() ? 1 : 0, depth_live.hooks() ? 1 : 0);
                reshade_depth_debug(depth_debug);
            }
        }
    }
    // Both optional effects and capture share this one real game Present.
    // Capture runs after effects but before either editor's UI or path guides.
    const auto clean_frame = [](IDXGISwapChain* capture_chain, ID3D11Device* capture_device,
                                ID3D11DeviceContext* capture_context, void* user) {
        dolly::stream::capture(capture_chain, capture_device, capture_context);
        const auto editor = editor_snapshot();
        const auto snapshot = video::status();
        if (!media_session_active() ||
            !video::present_should_capture(snapshot.state, editor.focused))
            return;
        const auto* sample = static_cast<const depth::SceneFrame*>(user);
        // Prefer the native path clock this view just evaluated: it is the
        // authored frame time and not gated by the editor config round trip,
        // so separate layer takes start on the same frame.
        double replay_time = -1.0;
        const bool native_clock = video::path_replay_time(replay_time);
        if (!native_clock)
            replay_time = editor.playing ? editor.phase : -1.0;
        video::capture(capture_chain, capture_device, capture_context, sample, replay_time,
                       native_clock);
    };
    auto* frame_user = const_cast<depth::SceneFrame*>(scene_sample);
    bool effects_handled = false;
    if (reshade_enabled() || reshade_overlay_pending()) {
        // The manual ReShade API changes graphics state. Restore the exact
        // engine context before Dolly draws or the real Present continues.
        DeviceStateScope effects_scope;
        effects_handled = reshade_render(chain, device, immediate, clean_frame, frame_user);
    } else {
        // Applies pending disable/teardown without adding a state swap to the
        // ordinary camera path when ReShade has never been configured.
        effects_handled = reshade_render(chain, device, immediate, clean_frame, frame_user);
    }
    if (!effects_handled)
        clean_frame(chain, device, immediate, frame_user);
    if (dolly::stream::active()) {
        // RenderLock drives the session from Blender: the game window shows only
        // the game, without Dolly's in-game panel, guides or notices.
        panel_ui.cancel_position_graph();
        return;
    }
    if (reshade_overlay_open() || reshade_overlay_pending()) {
        panel_ui.cancel_position_graph();
        clear_pending_input();
        editor_text_input_active(false);
        return;
    }
    const bool draw_loading = panel_ui.shader_notice_visible(state);
    if (!state.enabled && !draw_loading) {
        panel_ui.cancel_position_graph();
        clear_pending_input();
        editor_text_input_active(false);
        return;
    }
    GuiContextScope gui_scope(imgui);
    const bool panel = editor_panel_visible() && state.focused;
    const auto preview=visualization_preview_snapshot();
    const bool use_preview=preview && panel_ui.preview_guides_visible(state,*preview);
    const bool desktop_preview=use_preview && preview->source==1;
    const auto guides = panel_ui.guides_visible(state,desktop_preview) ?
        use_preview ? preview->path : visualization_snapshot() : nullptr;
    const bool draw_guides = guides && guides->enabled();
    const bool draw_grid = panel_ui.framing_grid_visible(state);
    const bool draw_campaths = panel_ui.lite_campaths_visible(state);
    if (!draw_campaths) panel_ui.cancel_position_graph();
    auto& io = ImGui::GetIO();
    if (panel != last_panel) {
        io.ClearInputKeys();
        io.ClearInputMouse();
        last_panel = panel;
    }
    // Present must never perform OS cursor updates on the game's behalf.
    io.ConfigFlags =
        ImGuiConfigFlags_NoMouseCursorChange |
        (panel && !panel_ui.lite_mode() ? ImGuiConfigFlags_NavEnableKeyboard : 0);
    io.ConfigNavMoveSetMousePos = false;
    io.WantSetMousePos = false;
    io.MouseDrawCursor = panel;
    // Paused editing keeps render-only guides while desktop controls own
    // focus. Playback, game UI, console and capture still hide them.
    if (!panel) {
        clear_pending_input();
        io.ClearEventsQueue();
        editor_text_input_active(false);
        if (!draw_guides && !draw_grid && !draw_campaths && !draw_loading)
            return;
    } else
        feed_pending_input();
    DeviceStateScope graphics_scope;
    ImGui_ImplDX11_NewFrame();
    ImGui_ImplWin32_NewFrame();
    // Window/input coordinates need not match the swapchain (resolution changes,
    // borderless scaling). Keep layout and mouse input in client coordinates;
    // scale only the renderer's viewport and scissor rectangles to the buffer.
    io.DisplayFramebufferScale = io.DisplaySize.x > 0 && io.DisplaySize.y > 0
                                     ? ImVec2(float(target_width) / io.DisplaySize.x,
                                              float(target_height) / io.DisplaySize.y)
                                     : ImVec2(1, 1);
    ImGui::NewFrame();
    panel_ui.reset_guides();
    // The Object Picker is its own mode: it replaces the normal panel and
    // suppresses the editing guides so the scene reads cleanly.
    if (draw_guides && !state.bone_picker && !state.object_picker)
        panel_ui.draw_path_guides(state, guides,desktop_preview);
    if (draw_grid && !state.bone_picker && !state.object_picker) {
        panel_ui.draw_framing_grid(state);
        diagnostic_grid.fetch_add(1, std::memory_order_relaxed);
    }
    if (panel) {
        if (state.object_picker)
            panel_ui.draw_object_picker(state);
        else if (state.bone_picker)
            panel_ui.draw_bone_picker(state, picker_portrait);
        else if (panel_ui.rail_enabled()) {
            if (rail_escape_requested.exchange(false)) panel_ui.close_rail_drawers();
            if (!rail_background_attempted) {
                rail_background_attempted = true;
                rail_background_view = load_rail_background(device);
            }
            panel_ui.draw_rail(state, reinterpret_cast<ImTextureID>(rail_background_view));
            rail_drawers_open.store(panel_ui.rail_drawers_open(), std::memory_order_release);
        } else
            panel_ui.draw_panel(state);
    }
    if (!panel && panel_ui.rail_enabled()) panel_ui.cancel_position_graph();
    if (draw_campaths && !panel_ui.rail_enabled()) {
        panel_ui.draw_lite_campaths(state);
        panel_ui.draw_lite_timeline(state);
    }
    if (draw_loading)
        panel_ui.draw_shader_notice();
    editor_text_input_active(panel && (ImGui::GetIO().WantTextInput ||
        ImGui::IsPopupOpen(nullptr, ImGuiPopupFlags_AnyPopupId)));
    ImGui::Render();
    panel_ui.flush_layout();
    immediate->OMSetRenderTargets(1, &target, nullptr);
    ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
    diagnostic_draw.fetch_add(1, std::memory_order_relaxed);
    if (draw_guides)
        diagnostic_guides.fetch_add(1, std::memory_order_relaxed);
    diagnostic_guide_lines.store(panel_ui.guide_lines(), std::memory_order_relaxed);
    diagnostic_guide_labels.store(panel_ui.guide_labels(), std::memory_order_relaxed);
    if (panel)
        diagnostic_panel.fetch_add(1, std::memory_order_relaxed);
}

HRESULT STDMETHODCALLTYPE present_hook(IDXGISwapChain* chain, UINT interval, UINT flags) {
    static thread_local bool entered = false;
    if (entered)
        return original_present(chain, interval, flags);
    entered = true;
    if (enabled.load(std::memory_order_acquire) && !(flags & DXGI_PRESENT_TEST)) {
        diagnostic_present.fetch_add(1, std::memory_order_relaxed);
        std::unique_lock<std::recursive_mutex> lock(render_mutex, std::try_to_lock);
        if (!lock.owns_lock()) {
            // The game's message thread briefly holds this lock while feeding a
            // UI event. Retry instead of skipping the frame: a skipped Present
            // draws no overlay at all, which reads as the panel blinking off
            // during fast mouse movement. The wait is bounded so a stuck thread
            // cannot stall the game's Present.
            const auto deadline =
                std::chrono::steady_clock::now() + std::chrono::milliseconds(2);
            while (!lock.try_lock()) {
                if (std::chrono::steady_clock::now() >= deadline)
                    break;
                std::this_thread::sleep_for(std::chrono::microseconds(50));
            }
        }
        if (lock.owns_lock()) {
            DiagnosticTimer timer(diagnostic_overlay_last_us, diagnostic_overlay_max_us,
                                  diagnostic_overlay_active_ms);
            try {
                render_overlay(chain);
            } catch (...) {
                last_error =
                    "DirectX editor stopped after a rendering error; external controls remain available.";
                release_device();
                enabled = false;
            }
        } else
            diagnostic_lock_skips.fetch_add(1, std::memory_order_relaxed);
    }
    HRESULT result;
    {
        DiagnosticTimer timer(diagnostic_present_last_us, diagnostic_present_max_us,
                              diagnostic_present_active_ms);
        result = original_present(chain, interval, flags);
    }
    if (result == DXGI_ERROR_DEVICE_REMOVED || result == DXGI_ERROR_DEVICE_RESET) {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (chain == swapchain) {
            last_error = "DirectX device reset; waiting for the game renderer to recover.";
            release_device();
        }
    }
    entered = false;
    return result;
}
HRESULT STDMETHODCALLTYPE resize_hook(IDXGISwapChain* chain, UINT count, UINT width, UINT height,
                                      DXGI_FORMAT format, UINT flags) {
    // ResizeBuffers requires ALL references to the backbuffer to be gone first.
    // Do not hold our mutex while the game/other overlays process the resize.
    bool game_resize = false;
    {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (chain == swapchain) {
            diagnostic_resizes.fetch_add(1, std::memory_order_relaxed);
            game_resize = true;
            resizing = true;
            reset_depth_lifecycle();
            video::reset_resources();
            dolly::stream::reset_resources();
            reshade_release_device();
            release_target();
        }
    }
    const HRESULT result = original_resize(chain, count, width, height, format, flags);
    if (game_resize) {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (chain == swapchain &&
            (result == DXGI_ERROR_DEVICE_REMOVED || result == DXGI_ERROR_DEVICE_RESET))
            release_device();
        resizing = false;
    }
    return result; // Recreate lazily at the next Present, including failed resizes.
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    auto previous = reinterpret_cast<WNDPROC>(GetPropW(window, kWindowProperty));
    bool consumed = false;
    LRESULT result = 0;
    // Preserve UI input when the render thread is busy without blocking the
    // game's message thread (which a graphics driver may synchronously need).
    // Raw mouse data and editor bindings are handled immediately below.
    const bool ui_message = (message >= WM_KEYFIRST && message <= WM_KEYLAST) ||
                            (message >= WM_MOUSEFIRST && message <= WM_MOUSELAST) ||
                            message == WM_SETFOCUS || message == WM_KILLFOCUS ||
                            message == WM_SETCURSOR;
    if (window == game_window && ui_message && editor_panel_visible()) {
        std::unique_lock<std::recursive_mutex> lock(render_mutex, std::try_to_lock);
        if (lock.owns_lock() && imgui) {
            GuiContextScope scope(imgui);
            feed_pending_input();
            feed_window_input({window, message, wparam, lparam});
        } else {
            std::lock_guard<std::mutex> input_lock(input_mutex);
            if (pending_input.size() >= 512) {
                pending_input.clear();
                input_overflow = true;
            }
            pending_input.push_back({window, message, wparam, lparam});
        }
    }
    if (enabled.load(std::memory_order_acquire) && window == game_window)
        consumed = editor_window_message(window, message, wparam, lparam, result);
    if (message == WM_NCDESTROY) {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (window == game_window)
            release_device();
        RemovePropW(window, kWindowProperty);
    }
    if (consumed)
        return result;
    return previous ? CallWindowProcW(previous, window, message, wparam, lparam)
                    : DefWindowProcW(window, message, wparam, lparam);
}

// Build a temporary hidden swapchain solely to discover public DXGI method
// addresses; never create graphics resources under the loader lock. Hardware
// first, WARP fallback supports headless/CI and systems without an adapter.
bool discover_swapchain_methods(void*& present, void*& resize) noexcept {
    const wchar_t class_name[] = L"DeadlockDolly.DX11.Discovery";
    const auto instance = GetModuleHandleW(nullptr);
    WNDCLASSW wc{};
    wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = instance;
    wc.lpszClassName = class_name;
    const ATOM atom = RegisterClassW(&wc);
    if (!atom)
        return false;
    HWND window = CreateWindowExW(0, class_name, L"", WS_OVERLAPPEDWINDOW, 0, 0, 64, 64, nullptr,
                                  nullptr, instance, nullptr);
    if (!window) {
        UnregisterClassW(class_name, instance);
        return false;
    }
    DXGI_SWAP_CHAIN_DESC desc{};
    desc.BufferCount = 1;
    desc.BufferDesc.Width = 64;
    desc.BufferDesc.Height = 64;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.OutputWindow = window;
    desc.SampleDesc.Count = 1;
    desc.Windowed = TRUE;
    desc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
    IDXGISwapChain* dummy = nullptr;
    ID3D11Device* dummy_device = nullptr;
    ID3D11DeviceContext* dummy_context = nullptr;
    const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1,
                                        D3D_FEATURE_LEVEL_10_0};
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
                                               levels, 3, D3D11_SDK_VERSION, &desc, &dummy,
                                               &dummy_device, nullptr, &dummy_context);
    if (FAILED(hr)) {
        release(dummy_context);
        release(dummy_device);
        release(dummy);
        hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, levels, 3,
                                           D3D11_SDK_VERSION, &desc, &dummy, &dummy_device, nullptr,
                                           &dummy_context);
    }
    if (SUCCEEDED(hr) && dummy) {
        auto table = *reinterpret_cast<void***>(dummy);
        present = table[8];
        resize = table[13];
        // The reviewed startup worker has already configured Players support.
        // Observe shader creation now: waiting for the game's first Present
        // misses shaders loaded beforehand, leaving valid player draws unknown.
        // This only records resource metadata; no capture or game draw is armed.
        if (player_capture::layout_hook_requested())
            player_capture::install_layout_hook(dummy_device);
    }
    release(dummy_context);
    release(dummy_device);
    release(dummy);
    DestroyWindow(window);
    UnregisterClassW(class_name, instance);
    return present && resize;
}
} // namespace

bool install_overlay_hooks() noexcept {
    if (installed.load(std::memory_order_acquire)) {
        enabled = true;
        return true;
    }
    try {
        void* present = nullptr;
        void* resize = nullptr;
        if (!discover_swapchain_methods(present, resize)) {
            last_error =
                "Could not discover DirectX 11 presentation; external controls remain available.";
            return false;
        }
        if (MH_CreateHook(present, reinterpret_cast<void*>(present_hook),
                          reinterpret_cast<void**>(&original_present)) != MH_OK) {
            last_error = "Could not attach the DirectX 11 presentation callback.";
            return false;
        }
        if (MH_CreateHook(resize, reinterpret_cast<void*>(resize_hook),
                          reinterpret_cast<void**>(&original_resize)) != MH_OK) {
            last_error = "Could not attach the DirectX 11 resize callback.";
            MH_RemoveHook(present);
            return false;
        }
        if (MH_EnableHook(resize) != MH_OK) {
            last_error = "Could not enable the DirectX 11 resize callback.";
            MH_RemoveHook(resize);
            MH_RemoveHook(present);
            return false;
        }
        if (MH_EnableHook(present) != MH_OK) {
            last_error = "Could not enable the DirectX 11 presentation callback.";
            MH_DisableHook(resize);
            MH_RemoveHook(present);
            return false;
        }
        installed = true;
        enabled = true;
        return true;
    } catch (...) {
        last_error = "DirectX 11 overlay initialization failed.";
        enabled = false;
        return false;
    }
}
OverlayDiagnostics overlay_diagnostics() noexcept {
    OverlayDiagnostics result{diagnostic_present.load(std::memory_order_relaxed),
                              diagnostic_panel.load(std::memory_order_relaxed),
                              diagnostic_init_attempts.load(std::memory_order_relaxed),
                              diagnostic_init_successes.load(std::memory_order_relaxed),
                              diagnostic_releases.load(std::memory_order_relaxed),
                              diagnostic_resizes.load(std::memory_order_relaxed),
                              diagnostic_draw.load(std::memory_order_relaxed),
                              diagnostic_guides.load(std::memory_order_relaxed),
                              diagnostic_overlay_last_us.load(std::memory_order_relaxed),
                              diagnostic_overlay_max_us.load(std::memory_order_relaxed),
                              diagnostic_present_last_us.load(std::memory_order_relaxed),
                              diagnostic_present_max_us.load(std::memory_order_relaxed),
                              diagnostic_lock_skips.load(std::memory_order_relaxed),
                              diagnostic_overlay_active_ms.load(std::memory_order_relaxed),
                              diagnostic_present_active_ms.load(std::memory_order_relaxed),
                              diagnostic_guide_lines.load(std::memory_order_relaxed),
                              diagnostic_guide_labels.load(std::memory_order_relaxed)};
    result.timeline_x0 = panel_ui.timeline_x0();
    result.timeline_x1 = panel_ui.timeline_x1();
    result.timeline_y = panel_ui.timeline_y();
    result.timeline_drags = panel_ui.timeline_drags();
    result.timeline_view_start = panel_ui.timeline_view_start();
    result.timeline_view_end = panel_ui.timeline_view_end();
    result.grid_frames = diagnostic_grid.load(std::memory_order_relaxed);
    return result;
}
const char* overlay_last_error() noexcept {
    return last_error.load(std::memory_order_acquire);
}
void overlay_set_renderer_pressure(bool high) noexcept {
    renderer_pressure.store(high, std::memory_order_release);
}
bool overlay_renderer_pressure() noexcept {
    return renderer_pressure.load(std::memory_order_acquire);
}
bool overlay_close_drawers_on_escape() noexcept {
    if (!rail_drawers_open.exchange(false, std::memory_order_acq_rel)) return false;
    rail_escape_requested.store(true, std::memory_order_release);
    return true;
}
void shutdown_overlay() noexcept {
    enabled = false;
    last_error = "In-game editor stopped.";
    try {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        release_device();
    } catch (...) {
    }
}
namespace {
void queue_raw_input(InputMessage message) noexcept {
    if (!enabled.load(std::memory_order_acquire) || !editor_panel_visible())
        return;
    message.window = game_window.load();
    if (!message.window)
        return;
    try {
        std::lock_guard<std::mutex> lock(input_mutex);
        if (pending_input.size() >= 512) {
            pending_input.clear();
            input_overflow = true;
        }
        pending_input.push_back(message);
    } catch (...) {
    } // An input hook must never throw into the game's event pump.
}
}
void overlay_raw_mouse_button(int button, bool down) noexcept {
    if (button < 0 || button > 4)
        return;
    InputMessage message{};
    message.raw_kind = 1;
    message.button = button;
    message.down = down;
    queue_raw_input(message);
}
void overlay_raw_mouse_wheel(float vertical, float horizontal) noexcept {
    InputMessage message{};
    message.raw_kind = 2;
    message.vertical = vertical;
    message.horizontal = horizontal;
    queue_raw_input(message);
}
void overlay_raw_key(unsigned virtual_key, unsigned scan_code, bool down, bool extended) noexcept {
    if (virtual_key > 255)
        return;
    InputMessage message{};
    message.raw_kind = 3;
    message.message = down ? WM_KEYDOWN : WM_KEYUP;
    message.wparam = virtual_key;
    message.lparam = LPARAM(1u | ((scan_code & 255u) << 16) | (extended ? 1u << 24 : 0u) |
                            (down ? 0u : 3u << 30));
    queue_raw_input(message);
}
} // namespace dolly
