// A capture failure that lands after the encoder's stop decision must not
// change the outcome: the take that was kept is reported completed.
// Built against a generated test copy of dolly_video_win.cpp that calls
// dolly_test_hooks::after_decision right after each worker's decide_cancel;
// the production source has no hook.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <cstdio>
#include <string>
#include "dolly_video.hpp"

namespace dolly_test_hooks {
void (*after_decision)() = nullptr;
}

namespace {
bool check(bool condition, const char* message) {
    if (!condition)
        std::fprintf(stderr, "FAIL: %s\n", message);
    return condition;
}
bool exists(const std::wstring& path) {
    return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}
bool wait_terminal() {
    using dolly::video::State;
    const auto until = GetTickCount64() + 15000;
    while (GetTickCount64() < until) {
        const auto state = dolly::video::status().state;
        if (state == State::completed || state == State::failed || state == State::cancelled)
            return true;
        Sleep(5);
    }
    return false;
}

// A swapchain whose backbuffer is gone, as after device loss.
struct LostSwapChain final : IDXGISwapChain {
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void** out) override {
        *out = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return 1; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE SetPrivateData(REFGUID, UINT, const void*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetPrivateDataInterface(REFGUID, const IUnknown*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetPrivateData(REFGUID, UINT*, void*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetParent(REFIID, void**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDevice(REFIID, void**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Present(UINT, UINT) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE GetBuffer(UINT, REFIID, void** out) override {
        ++calls;
        *out = nullptr;
        return DXGI_ERROR_DEVICE_REMOVED;
    }
    HRESULT STDMETHODCALLTYPE SetFullscreenState(BOOL, IDXGIOutput*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetFullscreenState(BOOL*, IDXGIOutput**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDesc(DXGI_SWAP_CHAIN_DESC*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ResizeBuffers(UINT, UINT, UINT, DXGI_FORMAT, UINT) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ResizeTarget(const DXGI_MODE_DESC*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetContainingOutput(IDXGIOutput**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetFrameStatistics(DXGI_FRAME_STATISTICS*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetLastPresentCount(UINT*) override { return E_NOTIMPL; }
    unsigned calls = 0;
};

LostSwapChain gLost;
ID3D11Device* gDevice = nullptr;
ID3D11DeviceContext* gContext = nullptr;
double gPhase = 0;
unsigned gHooks = 0;

// Runs on the encoder worker after its single stop decision, like a Present
// that arrives late and finds the backbuffer gone.
void late_capture() {
    ++gHooks;
    dolly::video::capture(&gLost, gDevice, gContext, nullptr, gPhase, true);
}
} // namespace

int main() {
    using namespace dolly::video;
    wchar_t temporary[MAX_PATH]{};
    if (!GetTempPathW(MAX_PATH, temporary))
        return 1;
    const auto base = std::wstring(temporary) + L"DollyLateFault-" + std::to_wstring(GetCurrentProcessId());
    HWND window = CreateWindowExW(0, L"STATIC", L"Dolly late fault", WS_OVERLAPPEDWINDOW, 0, 0, 320,
                                  240, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
    DXGI_SWAP_CHAIN_DESC description{};
    description.BufferDesc.Width = 320;
    description.BufferDesc.Height = 240;
    description.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    description.SampleDesc.Count = 1;
    description.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    description.BufferCount = 1;
    description.OutputWindow = window;
    description.Windowed = TRUE;
    description.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
    IDXGISwapChain* swapchain = nullptr;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr, 0,
                                               D3D11_SDK_VERSION, &description, &swapchain, &gDevice,
                                               nullptr, &gContext);
    if (!check(SUCCEEDED(hr), "WARP swapchain creation"))
        return 1;
    ID3D11Texture2D* buffer = nullptr;
    ID3D11RenderTargetView* view = nullptr;
    hr = swapchain->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&buffer));
    if (SUCCEEDED(hr))
        hr = gDevice->CreateRenderTargetView(buffer, nullptr, &view);
    bool ok = check(SUCCEEDED(hr), "backbuffer view");
    dolly_test_hooks::after_decision = late_capture;

    // Media Foundation MP4: record, stop, then lose the backbuffer late.
    const auto mp4 = base + L".mp4";
    ok = ok && check(!exists(mp4), "fresh MP4 path");
    ok = ok && check(start(mp4.c_str(), 30, 2000000), "MP4 recording starts");
    const auto until = GetTickCount64() + 15000;
    std::uint64_t started_at = 0;
    while (ok && GetTickCount64() < until) {
        const float color[] = {0.8f, 0.2f, 0.1f, 1.0f};
        gContext->ClearRenderTargetView(view, color);
        capture(swapchain, gDevice, gContext);
        swapchain->Present(0, 0);
        const auto snapshot = status();
        if (snapshot.state == State::failed) {
            ok = check(false, "MP4 recording failed before stop");
            break;
        }
        if (snapshot.state == State::recording && !started_at)
            started_at = GetTickCount64();
        if (started_at && GetTickCount64() - started_at >= 800)
            break;
        Sleep(8);
    }
    gHooks = 0;
    stop();
    ok = check(wait_terminal(), "MP4 recording reaches a terminal state") && ok;
    auto result = status();
    ok = check(gHooks == 1, "late capture ran after the MP4 decision") && ok;
    ok = check((result.state == State::completed) == exists(mp4),
               "MP4 terminal state agrees with the kept file") && ok;
    ok = check(result.state == State::completed && exists(mp4),
               "a late backbuffer loss keeps the finished MP4 completed") && ok;
    ok = check(gLost.calls == 0, "capture does not read the backbuffer after admission closed") && ok;
    shutdown();
    reset_resources();
    DeleteFileW(mp4.c_str()); // Exact fixture created by this test only.

    // Camera-only trace: three fixed-step frames, then the late loss. Checked
    // independently of the MP4 result.
    const bool mp4_ok = ok;
    ok = check(SUCCEEDED(hr), "backbuffer view");
    gLost.calls = 0;
    const auto trace = base + L"-camera.json";
    ok = ok && check(!exists(trace), "fresh trace path");
    Options options;
    options.path = trace.c_str();
    options.fps = 30;
    options.fixed_step = options.shot_only = options.camera_track = options.camera_only = true;
    options.max_frames = 3;
    ok = ok && check(start(options), "camera-only capture starts");
    const auto ready_until = GetTickCount64() + 5000;
    while (status().state == State::starting && GetTickCount64() < ready_until)
        Sleep(1);
    CameraSample camera{{10, 20, 30}, {0, 90, 0}, 90};
    gHooks = 0;
    for (unsigned i = 0; ok && i < 3; ++i) {
        gPhase = double(i) / 30;
        publish_path_replay_time(true, gPhase, false, &camera);
        capture(swapchain, gDevice, gContext, nullptr, gPhase, true);
    }
    ok = check(wait_terminal(), "camera-only capture reaches a terminal state") && ok;
    result = status();
    ok = check(gHooks == 1, "late capture ran after the camera-only decision") && ok;
    ok = check((result.state == State::completed) == exists(trace),
               "camera-only terminal state agrees with the kept trace") && ok;
    ok = check(result.state == State::completed, "a late backbuffer loss keeps the trace completed") && ok;
    ok = check(gLost.calls == 0, "camera-only capture does not read the backbuffer after admission closed") && ok;
    shutdown();
    reset_resources();
    DeleteFileW(trace.c_str());

    ok = ok && mp4_ok;
    dolly_test_hooks::after_decision = nullptr;
    view->Release();
    buffer->Release();
    gContext->Release();
    gDevice->Release();
    swapchain->Release();
    DestroyWindow(window);
    if (ok)
        std::puts("Late capture faults after the stop decision keep completed takes.");
    return ok ? 0 : 1;
}
