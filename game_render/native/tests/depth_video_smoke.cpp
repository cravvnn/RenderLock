#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include "dolly_depth_scene.hpp"
#include "dolly_video.hpp"
#include <array>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <limits>
#include <stdexcept>
#include <cstdio>
#include <cwchar>

namespace {
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
template <class T> struct Com {
    T* p = nullptr;
    ~Com() {
        if (p)
            p->Release();
    }
};
using namespace dolly;
struct Test {
    HWND window = nullptr;
    Com<IDXGISwapChain> chain;
    Com<ID3D11Device> device;
    Com<ID3D11DeviceContext> context;
    Com<ID3D11Texture2D> color, depth, scaled_depth;
    Com<ID3D11RenderTargetView> rtv;
    Com<ID3D11DepthStencilView> dsv, scaled_dsv;
    Com<ID3D11DepthStencilState> state;
    Com<ID3D11Buffer> cb;
    std::unique_ptr<depth::SceneTracker> tracker;
    Test() {
        window = CreateWindowExW(0, L"STATIC", L"Dolly paired depth test", WS_OVERLAPPEDWINDOW, 0,
                                 0, 320, 240, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        require(window != nullptr, "Create test window failed");
        DXGI_SWAP_CHAIN_DESC sc{};
        sc.BufferDesc.Width = 320;
        sc.BufferDesc.Height = 240;
        sc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        sc.SampleDesc.Count = 1;
        sc.BufferCount = 1;
        sc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        sc.OutputWindow = window;
        sc.Windowed = TRUE;
        require(SUCCEEDED(D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
                                                        nullptr, 0, D3D11_SDK_VERSION, &sc,
                                                        &chain.p, &device.p, nullptr, &context.p)),
                "Create test swapchain failed");
        require(SUCCEEDED(chain.p->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                             reinterpret_cast<void**>(&color.p))),
                "Get color failed");
        require(SUCCEEDED(device.p->CreateRenderTargetView(color.p, nullptr, &rtv.p)),
                "Create color view failed");
        D3D11_TEXTURE2D_DESC td{};
        td.Width = 320;
        td.Height = 240;
        td.MipLevels = td.ArraySize = td.SampleDesc.Count = 1;
        td.Format = DXGI_FORMAT_R32_TYPELESS;
        td.BindFlags = D3D11_BIND_DEPTH_STENCIL;
        require(SUCCEEDED(device.p->CreateTexture2D(&td, nullptr, &depth.p)),
                "Create depth failed");
        const char* name = "scratchrendertarget_1118301577_320x240_17_1.vtex";
        require(SUCCEEDED(depth.p->SetPrivateData(WKPDID_D3DDebugObjectName,
                                                  static_cast<UINT>(std::strlen(name)), name)),
                "Name depth failed");
        D3D11_DEPTH_STENCIL_VIEW_DESC vd{};
        vd.Format = DXGI_FORMAT_D32_FLOAT;
        vd.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
        require(SUCCEEDED(device.p->CreateDepthStencilView(depth.p, &vd, &dsv.p)),
                "Create depth view failed");
        // Upscaling or resolution scaling renders the scene below the color
        // swapchain size; the paired depth is captured at the scene's own size.
        td.Width = 160;
        td.Height = 120;
        require(SUCCEEDED(device.p->CreateTexture2D(&td, nullptr, &scaled_depth.p)),
                "Create scaled depth failed");
        const char* scaled_name = "scratchrendertarget_1118301577_160x120_17_1.vtex";
        require(SUCCEEDED(scaled_depth.p->SetPrivateData(
                    WKPDID_D3DDebugObjectName, static_cast<UINT>(std::strlen(scaled_name)),
                    scaled_name)),
                "Name scaled depth failed");
        require(SUCCEEDED(device.p->CreateDepthStencilView(scaled_depth.p, &vd, &scaled_dsv.p)),
                "Create scaled depth view failed");
        D3D11_DEPTH_STENCIL_DESC ds{};
        ds.DepthEnable = true;
        ds.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        ds.DepthFunc = D3D11_COMPARISON_GREATER_EQUAL;
        require(SUCCEEDED(device.p->CreateDepthStencilState(&ds, &state.p)), "Create state failed");
        D3D11_BUFFER_DESC bd{};
        bd.ByteWidth = 640;
        bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        require(SUCCEEDED(device.p->CreateBuffer(&bd, nullptr, &cb.p)),
                "Create calibration failed");
        tracker = std::make_unique<depth::SceneTracker>(device.p, 320, 240);
    }
    ~Test() {
        video::stop(true);
        video::shutdown();
        video::reset_resources();
        if (window)
            DestroyWindow(window);
    }
    void present(unsigned id, bool missing = false, bool scaled = false,
                 bool invalid_calibration = false, const video::CameraSample* camera = nullptr,
                 bool wrong_phase = false) {
        std::array<unsigned char, 640> bytes{};
        const auto put = [&bytes](unsigned offset, float value) {
            std::memcpy(bytes.data() + offset, &value, 4);
        };
        for (unsigned offset : {128u, 148u, 168u, 188u, 192u, 212u, 372u, 436u})
            put(offset, 1);
        if (camera)
            put(212, 4.0f / 3.0f); // Match the color canvas for exporter cross-checks.
        const float near_clip = float(id + 7);
        put(236, near_clip);
        put(248, -1);
        put(260, -1);
        put(264, 1 / near_clip);
        // The per-view viewport must describe the target the depth frame is
        // captured at, or the readback calibration is rejected.
        const float scene_width = scaled ? 160.0f : 320.0f;
        const float scene_height = scaled ? 120.0f : 240.0f;
        put(328, scene_width);
        put(332, scene_height);
        put(336, 1.0f / scene_width);
        put(340, 1.0f / scene_height);
        if (scaled) {
            // Temporal AA shifts the sampled projection by a subpixel amount.
            // Alternate the shift so stale calibration cannot pass this take.
            put(200, (id % 2 ? 1.0f : -1.0f) / scene_width);
            put(216, (id % 2 ? -1.0f : 1.0f) / scene_height);
        }
        if (invalid_calibration)
            put(200, 123);
        put(376, near_clip);
        put(380, std::numeric_limits<float>::infinity());
        put(456, -1);
        context.p->UpdateSubresource(cb.p, 0, nullptr, bytes.data(), 0, 0);
        if (invalid_calibration) {
            // Other modes intentionally exercise repeated bindings. This
            // diagnostic fixture explicitly supplies exactly one candidate.
            std::array<ID3D11Buffer*, 14> empty{};
            context.p->VSSetConstantBuffers(0, static_cast<UINT>(empty.size()), empty.data());
        }
        context.p->VSSetConstantBuffers(id % 2, 1, &cb.p);
        context.p->OMSetDepthStencilState(state.p, 0);
        // The scene pass renders into its own (possibly smaller) color target;
        // binding the swapchain view beside a smaller depth view is invalid in
        // D3D11, so only the depth view is bound for the scaled fixture.
        context.p->OMSetRenderTargets(scaled ? 0 : 1, scaled ? nullptr : &rtv.p,
                                      scaled ? scaled_dsv.p : dsv.p);
        const D3D11_VIEWPORT viewport{0, 0, scene_width, scene_height, 0, 1};
        context.p->RSSetViewports(1, &viewport);
        const float rgba[]{float(id) / 255, 32.0f / 255, 64.0f / 255, 1};
        context.p->ClearRenderTargetView(rtv.p, rgba);
        context.p->ClearDepthStencilView(scaled ? scaled_dsv.p : dsv.p, D3D11_CLEAR_DEPTH, .5f, 0);
        // This test supplies a calibrated GPU fixture directly to the observer;
        // depth_scene_smoke separately verifies the real draw detours.
        tracker->draw(context.p);
        auto scene = tracker->consume(context.p);
        // A camera stands in for the bridge's view hook, which publishes the
        // phase and its applied camera before the frame is presented.
        if (camera)
            video::publish_path_replay_time(true, double(id) / 10 + (wrong_phase ? 1 : 0), false,
                                            camera);
        video::capture(chain.p, device.p, context.p, missing ? nullptr : &scene, double(id) / 10,
                       camera != nullptr);
        chain.p->Present(0, 0);
    }
    void wait() {
        const auto end = GetTickCount64() + 15000;
        while (GetTickCount64() < end) {
            const auto s = video::status();
            if (s.state == video::State::completed || s.state == video::State::failed ||
                s.state == video::State::cancelled)
                return;
            Sleep(5);
        }
        throw std::runtime_error("Recording did not finish without Present");
    }
};
}
int wmain(int argc, wchar_t** argv) {
    namespace fs = std::filesystem;
    try {
        require(argc == 3, "Usage: depth_video_smoke ffmpeg.exe existing-output-parent");
        const fs::path root =
            fs::path(argv[2]) / (L"depth-video-" + std::to_wstring(GetCurrentProcessId()));
        require(fs::create_directory(root), "Could not create unique output directory");
        Test t;
        for (unsigned mode = 0; mode < 14; ++mode) {
            // Each take gets its own folder: the depth layer lives beside the
            // color file as <parent>\depth and is never overwritten.
            const fs::path mode_root = root / std::to_wstring(mode);
            require(fs::create_directory(mode_root), "Could not create mode output directory");
            const auto path = mode_root / (std::to_wstring(mode) + (mode == 5 ? L".mp4" : L".mkv"));
            video::Options options;
            options.path = path.c_str();
            options.ffmpeg = argv[1];
            options.encoder = video::Encoder::ffmpeg;
            options.codec = video::Codec::lossless;
            options.fps = mode == 7 ? 120 : 30;
            options.bitrate = 2000000;
            options.fixed_step = mode != 1;
            options.depth = true;
            options.camera_track = mode == 0 || (mode >= 9 && mode <= 12);
            options.depth_exr = (mode == 0 || mode == 7);
            if (mode == 5) {
                // The paired depth master needs the external encoder; a native
                // Media Foundation request must be rejected.
                options.encoder = video::Encoder::media_foundation;
                options.codec = video::Codec::h264_mf;
                require(!video::start(options), "Depth take accepted a native encoder");
                video::shutdown();
                video::reset_resources();
                continue;
            }
            if (mode == 11) {
                std::ofstream foreign(path.wstring() + L".camera-track.json");
                foreign << "foreign camera";
            }
            require(video::start(options), "Paired recording start failed");
            const auto end = GetTickCount64() + 15000;
            bool enough = false;
            bool calibration_rejected = false;
            for (unsigned frame = 0; GetTickCount64() < end; ++frame) {
                // Mode 6 starts with transition frames that have no verified
                // scene; they must be skipped, not fail the take. Mode 7
                // renders the scene below the color size (upscaling), so the
                // paired depth keeps its own size and per-frame TAA jitter.
                // Mode 8 supplies an unrelated projection and must retain the
                // calibration error instead of reporting generic pairing failure.
                const video::CameraSample camera{{0, 0, 0}, {90, 0, -90}, 90};
                t.present(frame % 100 + 1, mode == 6 && frame < 3, mode == 7, mode == 8,
                          (mode == 0 || mode >= 10) ? &camera : nullptr, mode == 10);
                const auto status = video::status();
                if (status.state == video::State::failed) {
                    if (mode == 8) {
                        std::fwprintf(stderr, L"Rejected invalid calibration: 0x%08X %ls\n",
                                      status.error_code, status.error);
                        require(status.frames_written == 0 &&
                                    status.error_code == HRESULT_FROM_WIN32(ERROR_INVALID_DATA),
                                "Invalid calibration lost its readback HRESULT");
                        require(std::wcsstr(status.error, L"no valid camera calibration") &&
                                    std::wcsstr(status.error, L"calibration=0/1"),
                                "Invalid calibration lost its readback diagnosis");
                        calibration_rejected = true;
                        break;
                    }
                    std::fwprintf(stderr, L"%ls\n", status.error);
                    throw std::runtime_error("Paired recording failed");
                }
                if (status.frames_written >= 12) {
                    enough = true;
                    break;
                }
                Sleep(8);
            }
            if (mode == 8) {
                require(calibration_rejected,
                        "Invalid calibration was accepted or never diagnosed");
                t.wait();
                video::shutdown();
                video::reset_resources();
                require(!fs::exists(path) && !fs::exists(mode_root / L"depth"),
                        "Invalid calibration retained partial paired output");
                continue;
            }
            if (!enough) {
                const auto status = video::status();
                std::fwprintf(stderr,
                              L"mode %u state %u %ux%u frames %llu dropped %llu error %ls\n"
                              L"scene %hs\n",
                              mode, static_cast<unsigned>(status.state), status.width,
                              status.height, static_cast<unsigned long long>(status.frames_written),
                              static_cast<unsigned long long>(status.frames_dropped), status.error,
                              depth::scene_diagnostic());
            }
            require(enough, "Paired recording did not produce frames");
            if (mode == 3)
                t.present(77, true);
            else if (mode == 4)
                video::reset_resources();
            else
                video::stop(mode == 2 || mode == 12);
            t.wait();
            const auto status = video::status();
            const auto expected = (mode == 2 || mode == 12) ? video::State::cancelled
                                  : mode == 3               ? video::State::failed
                                                            : video::State::completed;
            require(status.state == expected, "Unexpected paired recorder final state");
            video::shutdown();
            video::reset_resources();
            if (expected == video::State::cancelled)
                require(!fs::exists(path.wstring() + L".camera-track.json"),
                        "Cancelled take retained camera metadata");
            if (expected == video::State::completed) {
                require(fs::exists(path) && fs::exists(mode_root / L"depth" / L"manifest.json"),
                        "Completed pair missing output");
                require(fs::exists(mode_root / L"depth" / L"camera.json"),
                        "Completed pair missing the camera bundle");
                std::ifstream camera(mode_root / L"depth" / L"camera.json");
                const std::string bundle((std::istreambuf_iterator<char>(camera)), {});
                require(bundle.find("\"format\": \"deadlock-dolly-camera\"") != std::string::npos &&
                            bundle.find("\"complete\": true") != std::string::npos &&
                            bundle.find("\"frames\": " + std::to_string(status.frames_written) +
                                        ",") != std::string::npos,
                        "Camera bundle header missing");
                require(bundle.find("\"state\": \"complete\"") != std::string::npos &&
                            bundle.find("\"view\": [") != std::string::npos,
                        "Camera bundle record state missing");
                if (mode == 0 || mode == 7) {
                    // The float EXR sequence: one file per color frame, named by
                    // the zero-based color index, and recorded in the manifest.
                    require(fs::exists(mode_root / L"depth" / L"exr"),
                            "EXR sequence directory missing");
                    std::size_t exr_files = 0;
                    for (const auto& entry : fs::directory_iterator(mode_root / L"depth" / L"exr"))
                        if (entry.path().extension() == L".exr")
                            ++exr_files;
                    require(exr_files == static_cast<std::size_t>(status.frames_written),
                            "EXR count differs from the color frames");
                    std::ifstream exr_manifest(mode_root / L"depth" / L"manifest.json");
                    const std::string exr_text((std::istreambuf_iterator<char>(exr_manifest)), {});
                    require(exr_text.find("\"exr\": \"exr/") != std::string::npos,
                            "EXR sequence missing from the manifest");
                }
                // This test stops with a GPU copy still pending. Sidecar range
                // must describe encoded output, excluding that unwritten tail.
                std::ifstream metadata(path.wstring() + L".shot.json");
                const std::string text((std::istreambuf_iterator<char>(metadata)), {});
                const auto last =
                    "\"last_frame\": " + std::to_string(status.frames_written - 1) + ",";
                require(text.find(last) != std::string::npos,
                        "Shot metadata included an unwritten tail frame");
                const auto clock = (mode == 0 || mode >= 10) ? "\"clock_native_frames\": "
                                                             : "\"clock_fallback_frames\": ";
                require(text.find(clock + std::to_string(status.frames_written) + ",") !=
                            std::string::npos,
                        "Clock metadata did not match the written frames");
                // Only the native view clock carries a rendered camera. Its
                // sidecar has exactly one row per encoded frame.
                const auto camera_path = fs::path(path.wstring() + L".camera-track.json");
                if (mode == 0) {
                    std::ifstream camera_file(camera_path);
                    const std::string camera((std::istreambuf_iterator<char>(camera_file)), {});
                    std::size_t rows = 0;
                    for (auto at = camera.find("\n    ["); at != std::string::npos;
                         at = camera.find("\n    [", at + 1))
                        ++rows;
                    require(camera.find("\"format\": \"deadlock-dolly-camera-track\"") !=
                                    std::string::npos &&
                                rows == status.frames_written &&
                                camera.find("\"complete\": true") != std::string::npos &&
                                camera.find("\"first_qpc\": " + std::to_string(status.first_qpc)) !=
                                    std::string::npos,
                            "Camera metadata did not match the written frames");
                    video::publish_path_replay_time(false, 0);
                } else if (mode >= 9 && mode <= 11) {
                    std::ifstream camera_file(camera_path);
                    const std::string camera((std::istreambuf_iterator<char>(camera_file)), {});
                    if (mode == 11)
                        require(camera == "foreign camera",
                                "Camera writer overwrote a foreign file");
                    else
                        require(camera.find("\"complete\": false") != std::string::npos &&
                                    camera.find("missing or invalid camera sample") !=
                                        std::string::npos,
                                "Missing/stale camera metadata appeared complete");
                } else {
                    require(!fs::exists(camera_path),
                            "Camera metadata collected when not requested");
                }
                for (const auto& entry : fs::directory_iterator(mode_root))
                    require(entry.path().wstring().find(L".pending-") == std::wstring::npos,
                            "Camera finalization leaked its temporary file");
                if (mode == 7) {
                    // The paired depth keeps the scene target's size and names
                    // the color recording it belongs to.
                    std::ifstream manifest(mode_root / L"depth" / L"manifest.json");
                    const std::string pair((std::istreambuf_iterator<char>(manifest)), {});
                    require(pair.find("\"width\": 160") != std::string::npos &&
                                pair.find("\"height\": 120") != std::string::npos,
                            "Scaled scene depth manifest size missing");
                    require(pair.find("\"color_width\": 320") != std::string::npos &&
                                pair.find("\"color_height\": 240") != std::string::npos,
                            "Color recording size missing from the scaled depth manifest");
                    require(fs::exists(mode_root / L"depth" / L"preview_80x60.raw"),
                            "Scaled depth preview size missing");
                    require(bundle.find("\"width\": 160") != std::string::npos &&
                                bundle.find("\"height\": 120") != std::string::npos,
                            "Scaled scene camera bundle size missing");
                }
                std::ofstream report(root / (std::to_wstring(mode) + L".status"));
                report << status.frames_written << " " << status.frames_dropped << "\n";
            } else
                require(!fs::exists(path) && !fs::exists(mode_root / L"depth"),
                        "Failed/cancelled pair retained output");
        }
        const fs::path collision_root = root / L"collision";
        require(fs::create_directory(collision_root), "Create collision folder failed");
        const auto collision = collision_root / L"collision.mkv";
        const fs::path collision_dir = collision_root / L"depth";
        require(fs::create_directory(collision_dir), "Create collision fixture failed");
        {
            std::ofstream foreign(collision_dir / "keep.txt");
            foreign << "keep";
        }
        video::Options options;
        options.path = collision.c_str();
        options.ffmpeg = argv[1];
        options.encoder = video::Encoder::ffmpeg;
        options.codec = video::Codec::lossless;
        options.depth = true;
        require(video::start(options), "Collision recording request failed");
        t.present(1);
        t.wait();
        require(video::status().state == video::State::failed, "Existing depth folder accepted");
        video::shutdown();
        video::reset_resources();
        require(!fs::exists(collision) && fs::exists(collision_dir / "keep.txt"),
                "Collision cleanup changed foreign output");
        std::wprintf(
            L"Paired depth/video recordings, cancellation, missing-source failure, transition-frame skipping and reset passed.\n%ls\n",
            root.wstring().c_str());
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
}
