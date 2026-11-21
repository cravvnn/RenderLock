#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cwchar>
#include <cstring>
#include <initializer_list>
#include <string>
#include <thread>
#include <fstream>
#include "dolly_video.hpp"

namespace {
template <class T> void release(T*& p) {
    if (p) {
        p->Release();
        p = nullptr;
    }
}
bool check(bool condition, const char* message) {
    if (!condition)
        std::fprintf(stderr, "FAIL: %s\n", message);
    return condition;
}
bool terminal(dolly::video::State state) {
    return state == dolly::video::State::completed || state == dolly::video::State::failed ||
           state == dolly::video::State::cancelled;
}
bool wait_terminal() {
    const auto until = GetTickCount64() + 15000;
    while (GetTickCount64() < until) {
        if (terminal(dolly::video::status().state))
            return true;
        Sleep(5);
    }
    return false;
}
bool inspect_mp4(const wchar_t* path, std::uint64_t expected_frames,
                 std::uint64_t expected_duration, std::uint32_t expected_fps = 30,
                 LONGLONG minimum_last_pts = 0) {
    // Read the muxed H.264 samples back without requiring a video decoder.
    const auto platform = LoadLibraryExW(L"mfplat.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    const auto readwrite =
        LoadLibraryExW(L"mfreadwrite.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!platform || !readwrite) {
        if (platform)
            FreeLibrary(platform);
        if (readwrite)
            FreeLibrary(readwrite);
        return false;
    }
    const auto address_of = [](HMODULE module, const char* name, auto& function) {
        const auto address = GetProcAddress(module, name);
        static_assert(sizeof(address) == sizeof(function), "Windows function pointer width");
        std::memcpy(&function, &address, sizeof(function));
    };
    decltype(&MFStartup) startup = nullptr;
    decltype(&MFShutdown) shutdown = nullptr;
    decltype(&MFCreateSourceReaderFromURL) create_reader = nullptr;
    address_of(platform, "MFStartup", startup);
    address_of(platform, "MFShutdown", shutdown);
    address_of(readwrite, "MFCreateSourceReaderFromURL", create_reader);
    const HRESULT com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    const bool started = startup && shutdown && create_reader && SUCCEEDED(com) &&
                         SUCCEEDED(startup(MF_VERSION, MFSTARTUP_FULL));
    IMFSourceReader* reader = nullptr;
    bool ok = started && SUCCEEDED(create_reader(path, nullptr, &reader));
    if (ok) {
        reader->SetStreamSelection(MF_SOURCE_READER_ALL_STREAMS, FALSE);
        reader->SetStreamSelection(MF_SOURCE_READER_FIRST_VIDEO_STREAM, TRUE);
        IMFMediaType* type = nullptr;
        UINT32 width = 0, height = 0, fps_n = 0, fps_d = 0;
        ok = SUCCEEDED(reader->GetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, &type)) &&
             SUCCEEDED(MFGetAttributeSize(type, MF_MT_FRAME_SIZE, &width, &height)) &&
             width == 320 && height == 240 &&
             SUCCEEDED(MFGetAttributeRatio(type, MF_MT_FRAME_RATE, &fps_n, &fps_d)) && fps_n > 0 &&
             fps_d > 0 &&
             // Media Foundation derives the container rate from the samples'
             // real timing: a real-time capture reports the achieved rate, not
             // the requested one (30 fps can appear as 30000/1001; a 120 fps
             // request lands far lower on WARP). Zero disables the rate band;
             // the sample-timestamp checks below remain authoritative.
             (expected_fps == 0 || std::abs(double(fps_n) / double(fps_d) - double(expected_fps)) <=
                                       double(expected_fps) * 0.05);
        if (!ok)
            std::fprintf(stderr, "Container metadata: %ux%u fps=%u/%u expected=%u\n", width, height,
                         fps_n, fps_d, expected_fps);
        release(type);
    }
    std::uint64_t frames = 0;
    LONGLONG last = -1, end = 0;
    while (ok && frames <= expected_frames) {
        DWORD flags = 0;
        LONGLONG timestamp = 0;
        IMFSample* sample = nullptr;
        const HRESULT hr = reader->ReadSample(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, nullptr,
                                              &flags, &timestamp, &sample);
        if (FAILED(hr)) {
            ok = false;
            break;
        }
        if (sample) {
            LONGLONG duration = 0;
            ok =
                SUCCEEDED(sample->GetSampleDuration(&duration)) && timestamp > last && duration > 0;
            if (!frames)
                ok = ok && timestamp == 0;
            last = timestamp;
            end = timestamp + duration;
            ++frames;
            release(sample);
        }
        if (flags & MF_SOURCE_READERF_ENDOFSTREAM)
            break;
    }
    ok = ok && frames == expected_frames && last >= minimum_last_pts && end > 0 &&
         std::uint64_t(end) + 10000 >= expected_duration &&
         std::uint64_t(end) <= expected_duration + 10000;
    if (!ok)
        std::fprintf(
            stderr,
            "Container samples: count=%llu expected=%llu last=%lld end=%lld expected_end=%llu\n",
            static_cast<unsigned long long>(frames),
            static_cast<unsigned long long>(expected_frames), last, end,
            static_cast<unsigned long long>(expected_duration));
    release(reader);
    if (started)
        shutdown();
    if (SUCCEEDED(com))
        CoUninitialize();
    FreeLibrary(readwrite);
    FreeLibrary(platform);
    return check(
        ok, "MP4 has the expected dimensions, monotonic sample timestamps and elapsed duration");
}
}

int main(int argc, char** argv) {
    using namespace dolly::video;
    if (!check(!start(L"relative.mp4", 30, 10000000), "relative paths are refused") ||
        !check(!start(L"C:\\unused.mp4", 24, 10000000), "unsupported FPS is refused"))
        return 1;
    wchar_t temporary[MAX_PATH]{};
    if (!GetTempPathW(MAX_PATH, temporary))
        return 1;
    const auto path = std::wstring(temporary) + L"DollyVideoSmoke-" +
                      std::to_wstring(GetCurrentProcessId()) + L".mp4";
    // A unique temp name avoids deleting a file from any earlier test run.
    if (GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES)
        return 1;
    if (argc == 2 && std::strcmp(argv[1], "--status-cache") == 0) {
        // status() returns its cached snapshot when another caller holds the
        // recorder lock. A new take must never report the previous take's
        // terminal state through that cache.
        std::atomic<bool> run{true};
        std::thread hammer([&] {
            while (run.load())
                (void)status();
        });
        unsigned stale = 0, takes = 0;
        for (unsigned round = 0; round < 150; ++round) {
            // Never configured (no Present), so no output file is created.
            const auto take_path = path + L"-cache-" + std::to_wstring(round) + L".mp4";
            if (!start(take_path.c_str(), 30, 2000000))
                continue;
            ++takes;
            for (int i = 0; i < 50; ++i) {
                if (terminal(status().state)) {
                    ++stale;
                    break;
                }
            }
            stop(true);
            for (int i = 0; i < 2000 && !terminal(status().state); ++i)
                Sleep(1);
        }
        run = false;
        hammer.join();
        shutdown();
        if (stale)
            std::fprintf(stderr, "%u of %u fresh takes reported a terminal state\n", stale, takes);
        const bool ok = check(takes > 0 && stale == 0, "a new take reports its own state");
        if (ok)
            std::puts("Recorder status cache follows the current take.");
        return ok ? 0 : 1;
    }
    HWND window = CreateWindowExW(0, L"STATIC", L"Dolly video smoke", WS_OVERLAPPEDWINDOW, 0, 0,
                                  320, 240, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
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
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr,
                                               0, D3D11_SDK_VERSION, &description, &swapchain,
                                               &device, nullptr, &context);
    if (!check(SUCCEEDED(hr), "WARP swapchain creation"))
        return 1;
    ID3D11Texture2D* buffer = nullptr;
    ID3D11RenderTargetView* view = nullptr;
    hr = swapchain->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&buffer));
    if (SUCCEEDED(hr))
        hr = device->CreateRenderTargetView(buffer, nullptr, &view);
    if (argc == 2 && std::strcmp(argv[1], "--camera-only") == 0) {
        bool ok = check(SUCCEEDED(hr), "camera-only backbuffer view");
        for (unsigned variant = 0; variant < 6 && ok; ++variant) {
            const auto trace_path = path + L"-camera-" + std::to_wstring(variant) + L".json";
            if (GetFileAttributesW(trace_path.c_str()) != INVALID_FILE_ATTRIBUTES)
                return 1;
            Options options;
            options.path = trace_path.c_str();
            options.fps = 30;
            options.fixed_step = options.shot_only = options.camera_track = options.camera_only = true;
            options.max_frames = 3;
            ok = check(start(options), "start bounded numerical capture");
            const auto until = GetTickCount64() + 5000;
            while (status().state == State::starting && GetTickCount64() < until)
                Sleep(1);
            ok = ok && check(status().state == State::recording, "camera-only ready without codec");
            CameraSample camera{{10, 20, 30}, {0, 90, 0}, 90};
            const auto frame = [&](double phase, bool pose = true) {
                publish_path_replay_time(true, phase, false, pose ? &camera : nullptr);
                capture(swapchain, device, context, nullptr, phase, true);
            };
            frame(0);
            if (variant == 0) {
                capture(swapchain, device, context, nullptr, 0, true); // No fresh view.
                frame(0); // A paused main view is not another frame.
                ok = ok && check(status().frames_written == 1, "duplicate views do not invent frames");
                frame(1.0 / 30);
                frame(2.0 / 30);
            } else if (variant == 1) {
                frame(2.0 / 30); // Missing fixed-step frame.
            } else if (variant == 2) {
                frame(1.0 / 30, false); // Clock without its same-view pose.
            } else if (variant == 3) {
                stop(true);
            } else if (variant == 4) {
                stop(false); // A short take cannot publish success.
            } else {
                // A foreign destination appearing after start must be preserved.
                HANDLE foreign = CreateFileW(trace_path.c_str(), GENERIC_WRITE, 0, nullptr,
                                             CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
                ok = ok && check(foreign != INVALID_HANDLE_VALUE, "late foreign trace fixture");
                if (foreign != INVALID_HANDLE_VALUE) {
                    DWORD written = 0;
                    WriteFile(foreign, "foreign", 7, &written, nullptr);
                    CloseHandle(foreign);
                }
                frame(1.0 / 30);
                frame(2.0 / 30);
            }
            ok = ok && check(wait_terminal(), "camera-only terminal without encoder drain");
            const auto result = status();
            const auto expected = variant == 0 ? State::completed
                                : variant == 3 ? State::cancelled : State::failed;
            ok = ok && check(result.state == expected, "honest camera-only terminal state");
            if (variant == 1)
                ok = ok && check(std::wcsstr(result.error, L"frame 1") &&
                                 std::wcsstr(result.error, L"0.066666667") &&
                                 std::wcsstr(result.error, L"0.033333333"),
                                 "cadence refusal retains measured and expected phase");
            if (variant == 0 || variant == 5) {
                std::ifstream input(trace_path, std::ios::binary);
                const std::string text((std::istreambuf_iterator<char>(input)), {});
                ok = ok && check(variant == 0 ? text.find("\"complete\": true") != std::string::npos
                                              && text.find("\"frames_written\": 3") != std::string::npos
                                              && result.frames_written == 3
                                              && result.width == 320 && result.height == 240
                                            : text == "foreign", "trace completeness/foreign preservation");
                input.close();
                DeleteFileW(trace_path.c_str()); // Exact fixture created by this test only.
            } else {
                ok = ok && check(GetFileAttributesW(trace_path.c_str()) == INVALID_FILE_ATTRIBUTES,
                                 "failed/cancelled takes publish no trace");
            }
            reset_resources();
        }
        shutdown();
        release(view);
        release(buffer);
        release(context);
        release(device);
        release(swapchain);
        DestroyWindow(window);
        return ok ? 0 : 1;
    }
    if (argc == 2 && std::strcmp(argv[1], "--cap-only") == 0) {
        bool ok = check(SUCCEEDED(hr), "bounded backbuffer view");
        for (unsigned variant = 0; variant < 4; ++variant) {
            const bool fixed = (variant & 1) != 0;
            const bool shot_only = (variant & 2) != 0;
            if (!ok)
                break;
            const auto capped_path = path + std::to_wstring(variant) + L"-cap.mp4";
            Options options;
            options.path = capped_path.c_str();
            options.fps = 60;
            options.bitrate = 2000000;
            options.fixed_step = fixed;
            options.shot_only = shot_only;
            options.max_frames = 2;
            options.camera_track = true;
            const auto shot_path = capped_path + L".shot.json";
            if (variant == 0) {
                // A foreign shot sidecar is never truncated or replaced.
                std::ofstream foreign(shot_path, std::ios::binary);
                foreign << "foreign";
            }
            ok = check(start(options), "bounded recording starts") && ok;
            const auto deadline = GetTickCount64() + 15000;
            double phase = 0;
            while (ok && !terminal(status().state) && GetTickCount64() < deadline) {
                const float color[] = {0.2f, 0.4f, 0.7f, 1.0f};
                context->ClearRenderTargetView(view, color);
                const CameraSample camera{{phase, 2, 3}, {4, 5, 6}, 90};
                publish_path_replay_time(true, phase, false, &camera);
                capture(swapchain, device, context, nullptr, phase, true);
                phase += 1.0 / 60;
                swapchain->Present(0, 0);
                Sleep(shot_only && !fixed ? 20 : 2);
            }
            const auto result = status();
            ok = check(result.state == State::completed && result.frames_written == 2,
                       "hard admission cap auto-finalizes exactly two frames") &&
                 ok;
            for (unsigned i = 0; i < 20; ++i) {
                capture(swapchain, device, context);
                swapchain->Present(0, 0);
            }
            ok = check(status().frames_written == 2, "extra Presents cannot overshoot cap") && ok;
            shutdown();
            reset_resources();
            if (ok)
                ok = inspect_mp4(capped_path.c_str(), result.frames_written, result.duration_100ns,
                                 fixed ? 60 : 0, shot_only && !fixed ? 190000 : 0);
            {
                std::ifstream metadata(capped_path + L".camera-track.json");
                const std::string camera((std::istreambuf_iterator<char>(metadata)), {});
                ok = check(camera.find("\"complete\": true") != std::string::npos &&
                               camera.find("\"frames_written\": 2") != std::string::npos &&
                               camera.find("\"capture_time\"") != std::string::npos,
                           "bounded Media Foundation camera matches encoded output") &&
                     ok;
            }
            {
                std::ifstream sidecar(shot_path, std::ios::binary);
                const std::string shot((std::istreambuf_iterator<char>(sidecar)), {});
                ok = check(variant == 0 ? shot == "foreign"
                                        : shot.find("\"deadlock-dolly-shot\"") != std::string::npos &&
                                              shot.size() > 2 &&
                                              shot.compare(shot.size() - 2, 2, "}\n") == 0,
                           "shot sidecar is complete and never replaces a foreign file") &&
                     ok;
                WIN32_FIND_DATAW found{};
                const HANDLE leftover =
                    FindFirstFileW((shot_path + L".pending-*").c_str(), &found);
                ok = check(leftover == INVALID_HANDLE_VALUE, "no partial shot sidecar is left") &&
                     ok;
                if (leftover != INVALID_HANDLE_VALUE)
                    FindClose(leftover);
            }
            DeleteFileW((capped_path + L".camera-track.json").c_str());
            DeleteFileW(shot_path.c_str());
            DeleteFileW(capped_path.c_str());
        }
        // A take that plays an authored path must not record the hold after that
        // path ends. The endpoint clamps, so those frames repeat one authored
        // instant; only depth and POV/bone takes used to drop them, which left
        // every other exported video with seconds of still frame at its end.
        if (ok) {
            const auto held_path = path + L"-held.mp4";
            Options options;
            options.path = held_path.c_str();
            options.fps = 60;
            options.bitrate = 2000000;
            options.fixed_step = true;
            options.max_frames = 400;
            ok = check(start(options), "held-endpoint recording starts") && ok;
            const auto hold_deadline = GetTickCount64() + 15000;
            double phase = 0;
            // Advance the authored path until the encoder has definitely written
            // frames, then let it settle so the held-endpoint comparison below
            // cannot race the encoder worker.
            while (ok && status().frames_written < 2 && GetTickCount64() < hold_deadline) {
                publish_path_replay_time(true, phase, false);
                const float color[] = {0.2f, 0.4f, 0.7f, 1.0f};
                context->ClearRenderTargetView(view, color);
                capture(swapchain, device, context, nullptr, phase, true);
                phase += 1.0 / 60;
                swapchain->Present(0, 0);
                Sleep(2);
            }
            ok = check(status().frames_written >= 2, "advancing path frames were captured") && ok;
            std::uint64_t advancing = 0;
            for (unsigned stable = 0; ok && stable < 20 && GetTickCount64() < hold_deadline; ) {
                publish_path_replay_time(true, phase, false);
                const float color[] = {0.2f, 0.4f, 0.7f, 1.0f};
                context->ClearRenderTargetView(view, color);
                capture(swapchain, device, context, nullptr, phase, true);
                phase += 1.0 / 60;
                swapchain->Present(0, 0);
                Sleep(10);
                const auto written = status().frames_written;
                stable = written == advancing && written >= 2 ? stable + 1 : 0;
                advancing = written;
            }
            ok = check(advancing >= 2, "the encoded path frames settled") && ok;
            // Hold the endpoint exactly as the bridge does once the shot ends.
            // The step after the completed endpoint must not be recorded either:
            // admitting it once produced a single held frame whose encoded
            // duration was however long the hold lasted.
            const auto endpoint = phase - 1.0 / 60;
            for (unsigned i = 0; ok && i < 40 && GetTickCount64() < hold_deadline; ++i) {
                const bool past = (i % 2) == 1;
                const double held_phase = past ? endpoint + 1.0 / 60 : endpoint;
                publish_path_replay_time(true, held_phase, true);
                const float color[] = {0.6f, 0.1f, 0.1f, 1.0f};
                context->ClearRenderTargetView(view, color);
                capture(swapchain, device, context, nullptr, held_phase, true);
                swapchain->Present(0, 0);
                Sleep(5);
            }
            Sleep(300);
            const auto held = status();
            ok = check(held.frames_written == advancing,
                       "a completed path endpoint recorded still frames") && ok;
            stop();
            ok = check(wait_terminal(), "held-endpoint recording stops") && ok;
            shutdown();
            reset_resources();
            DeleteFileW(held_path.c_str());
        }
        release(view);
        release(buffer);
        release(context);
        release(device);
        release(swapchain);
        DestroyWindow(window);
        if (ok)
            std::puts(
                "WARP real-time/fixed-step two-frame admission and auto-finalization passed.");
        return ok ? 0 : 1;
    }
    bool ok = check(SUCCEEDED(hr), "backbuffer view") &&
              check(start(path.c_str(), 30, 2000000), "recording starts");
    const auto until = GetTickCount64() + 15000;
    std::uint64_t started_at = 0;
    while (ok && GetTickCount64() < until) {
        const float color[] = {0.8f, 0.2f, 0.1f, 1.0f};
        context->ClearRenderTargetView(view, color);
        capture(swapchain, device, context);
        swapchain->Present(0, 0);
        const auto snapshot = status();
        if (snapshot.state == State::failed) {
            std::fwprintf(stderr, L"Recorder failure 0x%08x: %ls\n", snapshot.error_code,
                          snapshot.error);
            ok = false;
            break;
        }
        if (snapshot.state == State::recording && !started_at)
            started_at = GetTickCount64();
        if (started_at && GetTickCount64() - started_at >= 1200)
            break;
        Sleep(8);
    }
    stop();
    // Deliberately provide no more Presents: finalization must still finish
    // without blocking or requiring the game to regain foreground focus.
    ok = check(wait_terminal(), "asynchronous finalization without Present") && ok;
    const auto result = status();
    ok = check(result.state == State::completed && result.frames_written >= 4,
               "real frames were written to a completed recording") &&
         ok;
    shutdown();
    reset_resources();
    if (ok)
        ok = inspect_mp4(path.c_str(), result.frames_written, result.duration_100ns);
    WIN32_FILE_ATTRIBUTE_DATA before{}, after{};
    if (ok) {
        ok = GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &before) != FALSE;
        ok = check(start(path.c_str(), 30, 2000000),
                   "existing-path request is handled asynchronously") &&
             ok;
        capture(swapchain, device, context);
        ok = check(wait_terminal() && status().state == State::failed, "existing MP4 is refused") &&
             ok;
        shutdown();
        reset_resources();
        ok = GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &after) &&
             before.nFileSizeHigh == after.nFileSizeHigh &&
             before.nFileSizeLow == after.nFileSizeLow && ok;
    }
    const auto high_path = path + L"-120.mp4";
    if (ok) {
        ok = check(GetFileAttributesW(high_path.c_str()) == INVALID_FILE_ATTRIBUTES,
                   "120 FPS output must not exist") &&
             check(start(high_path.c_str(), 120, 2000000), "120 FPS job starts");
        const auto high_deadline = GetTickCount64() + 10000;
        while (ok && status().frames_written < 6 && GetTickCount64() < high_deadline) {
            const float color[] = {0.1f, 0.6f, 0.3f, 1.0f};
            context->ClearRenderTargetView(view, color);
            capture(swapchain, device, context);
            swapchain->Present(0, 0);
            ok = check(!terminal(status().state), "120 FPS encoder remains active");
            Sleep(2);
        }
        ok = check(status().frames_written >= 6, "120 FPS encoder produces real samples") && ok;
        stop();
        ok = check(wait_terminal() && status().state == State::completed,
                   "120 FPS finalization completes") &&
             ok;
        const auto high_result = status();
        shutdown();
        reset_resources();
        if (ok)
            // The 120 FPS request is a ceiling on WARP; the achieved rate is
            // read back through the sample timestamps, so skip the rate band.
            ok = inspect_mp4(high_path.c_str(), high_result.frames_written,
                             high_result.duration_100ns, 0);
        DeleteFileW(high_path.c_str());
    }
    const auto cancel_path = path + L"-cancel.mp4";
    if (ok) {
        ok = check(start(cancel_path.c_str(), 60, 2000000), "60 FPS cancellation job starts");
        const auto cancel_deadline = GetTickCount64() + 10000;
        while (GetTickCount64() < cancel_deadline && status().state == State::starting) {
            capture(swapchain, device, context);
            swapchain->Present(0, 0);
            Sleep(5);
        }
        ok = check(status().state == State::recording, "60 FPS encoder initializes") && ok;
        capture(swapchain, device, context);
        swapchain->Present(0, 0);
        stop(true);
        ok = check(wait_terminal() && status().state == State::cancelled,
                   "cancel finishes asynchronously") &&
             ok;
        shutdown();
        reset_resources();
        ok = check(GetFileAttributesW(cancel_path.c_str()) == INVALID_FILE_ATTRIBUTES,
                   "cancel removes only its incomplete output") &&
             ok;
    }
    // Stop clicked in the desktop app while the game is not focused: the
    // overlay keeps feeding finalizing Presents through its gate, so the copy
    // submitted on the last recording Present is read back instead of being
    // counted as lost (which discards a fixed-step take).
    const auto stop_path = path + L"-stop.mp4";
    if (ok) {
        Options options;
        options.path = stop_path.c_str();
        options.fps = 30;
        options.bitrate = 2000000;
        options.fixed_step = true;
        ok = check(start(options), "fixed-step stop job starts");
        const auto present = [&] {
            const float color[] = {0.3f, 0.3f, 0.6f, 1.0f};
            context->ClearRenderTargetView(view, color);
            if (present_should_capture(status().state, false))
                capture(swapchain, device, context);
            swapchain->Present(0, 0);
            Sleep(16);
        };
        const auto stop_deadline = GetTickCount64() + 15000;
        while (ok && status().state == State::starting && GetTickCount64() < stop_deadline)
            present();
        for (int i = 0; ok && i < 20; ++i)
            present();
        ok = check(status().state == State::recording, "fixed-step stop job records") && ok;
        stop(false);
        Sleep(8); // The stop lands between two Presents.
        while (ok && !terminal(status().state) && GetTickCount64() < stop_deadline)
            present();
        const auto stopped = status();
        if (stopped.state != State::completed || stopped.frames_dropped)
            std::fwprintf(stderr, L"Stopped take: state=%u written=%llu dropped=%llu %ls\n",
                          unsigned(stopped.state),
                          static_cast<unsigned long long>(stopped.frames_written),
                          static_cast<unsigned long long>(stopped.frames_dropped),
                          stopped.error);
        ok = check(stopped.state == State::completed && stopped.frames_dropped == 0 &&
                       stopped.frames_written >= 10,
                   "a stop keeps the last submitted frame of a fixed-step take") &&
             ok;
        shutdown();
        reset_resources();
        DeleteFileW((stop_path + L".shot.json").c_str());
        DeleteFileW(stop_path.c_str());
    }
    release(view);
    release(buffer);
    release(context);
    release(device);
    release(swapchain);
    DestroyWindow(window);
    DeleteFileW(path.c_str());
    if (ok)
        std::puts("Windows DX11 real-time MP4 capture/finalization/no-overwrite smoke passed.");
    return ok ? 0 : 1;
}
