#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include "dolly_video.hpp"
#include <array>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
// NVENC does not support tiny video dimensions on all GPUs.
constexpr unsigned width = 320, height = 240;
template <class T> struct Com {
    T* p = nullptr;
    ~Com() { if (p) p->Release(); }
};
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

void record(const wchar_t* ffmpeg, const std::filesystem::path& output,
            bool rgba, dolly::video::Codec codec, bool stalled = false, bool fixed = true) {
    using namespace dolly::video;
    const HWND window = CreateWindowExW(0, L"STATIC", L"Dolly color smoke", WS_OVERLAPPEDWINDOW,
                                        0, 0, width, height, nullptr, nullptr,
                                        GetModuleHandleW(nullptr), nullptr);
    require(window != nullptr, "Could not create hidden test window");
    Com<IDXGISwapChain> swapchain;
    Com<ID3D11Device> device;
    Com<ID3D11DeviceContext> context;
    Com<ID3D11Texture2D> backbuffer;
    DXGI_SWAP_CHAIN_DESC desc{};
    desc.BufferDesc.Width = width;
    desc.BufferDesc.Height = height;
    desc.BufferDesc.Format = rgba ? DXGI_FORMAT_R8G8B8A8_UNORM : DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = desc.BufferCount = 1;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.OutputWindow = window;
    desc.Windowed = TRUE;
    require(SUCCEEDED(D3D11CreateDeviceAndSwapChain(
                nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
                &desc, &swapchain.p, &device.p, nullptr, &context.p)),
            "Could not create WARP swapchain");
    require(SUCCEEDED(swapchain.p->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                             reinterpret_cast<void**>(&backbuffer.p))),
            "Could not get backbuffer");

    const std::array<std::array<std::uint8_t, 3>, 4> colors{{
        {{255, 0, 0}}, {{0, 255, 0}}, {{0, 0, 255}}, {{255, 255, 255}},
    }};
    std::vector<std::uint8_t> pixels(width * height * 4);
    for (unsigned y = 0; y < height; ++y) {
        for (unsigned x = 0; x < width; ++x) {
            const auto& color = colors[(y / (height / 2)) * 2 + x / (width / 2)];
            const auto i = (y * width + x) * 4;
            pixels[i] = color[rgba ? 0 : 2];
            pixels[i + 1] = color[1];
            pixels[i + 2] = color[rgba ? 2 : 0];
            pixels[i + 3] = 255;
        }
    }
    Options options;
    options.path = output.c_str();
    options.ffmpeg = ffmpeg;
    options.encoder = Encoder::ffmpeg;
    options.codec = codec;
    options.fps = 30;
    options.bitrate = 20000000;
    options.fixed_step = fixed;
    options.max_frames = stalled ? 12 : 2;
    require(start(options), "Recorder refused color test");
    const auto deadline = GetTickCount64() + 15000;
    Status result;
    bool delayed = false;
    do {
        context.p->UpdateSubresource(backbuffer.p, 0, nullptr, pixels.data(), width * 4, 0);
        capture(swapchain.p, device.p, context.p);
        swapchain.p->Present(0, 0);
        Sleep(2);
        result = status();
        if (stalled && !delayed && result.frames_written >= 1) {
            Sleep(350); // A slow render must lengthen export time, not omit frames.
            delayed = true;
        }
    } while (result.state != State::completed && result.state != State::failed &&
             GetTickCount64() < deadline);
    const bool rejected = stalled && !fixed;
    if (!rejected && result.state != State::completed)
        std::fwprintf(stderr, L"Recorder state %u: 0x%08x %ls\n",
                      static_cast<unsigned>(result.state), result.error_code, result.error);
    stop(result.state != State::completed);
    shutdown();
    reset_resources();
    DestroyWindow(window);
    if (rejected) {
        require(result.state == State::failed && result.frames_dropped > 0 &&
                    std::wstring(result.error).find(L"lost frames") != std::wstring::npos,
                "Missing real-time frames were not rejected explicitly");
        require(!std::filesystem::exists(output), "Incomplete take was published as a video");
        return;
    }
    require(result.frames_dropped == 0, "Frame-accurate capture lost frames");
    require(result.duration_100ns == (stalled ? 12ull : 2ull) * 10000000 / 30,
            "A render stall changed the encoded duration");
    require(result.state == State::completed && result.frames_written == (stalled ? 12 : 2),
            "Recorder did not finish its two-frame test");
    require(std::filesystem::is_regular_file(output), "Recorder output is missing");
}
}

int wmain(int argc, wchar_t** argv) {
    try {
        require(argc == 3 || (argc == 4 && std::wstring(argv[3]) == L"--nvenc"),
                "Usage: video_color_smoke ffmpeg.exe empty-output-directory [--nvenc]");
        const std::filesystem::path root(argv[2]);
        require(std::filesystem::is_directory(root) && std::filesystem::is_empty(root),
                "Output directory must exist and be empty");
        const auto rejected_mov = root / L"rejected.mov";
        dolly::video::Options invalid;
        invalid.path = rejected_mov.c_str();
        require(!dolly::video::start(invalid), "Built-in encoder accepted MOV");
        invalid.encoder = dolly::video::Encoder::ffmpeg;
        invalid.ffmpeg = argv[1];
        invalid.codec = dolly::video::Codec::lossless;
        require(!dolly::video::start(invalid), "Lossless encoder accepted MOV");
        for (bool rgba : {true, false}) {
            for (bool lossless : {false, true}) {
                const std::wstring name = (lossless ? L"lossless-" : L"h264-") +
                                          std::wstring(rgba ? L"rgba" : L"bgra") +
                                          (lossless ? L".mkv" : L".mp4");
                record(argv[1], root / name, rgba,
                       lossless ? dolly::video::Codec::lossless : dolly::video::Codec::h264_mf);
            }
        }
        record(argv[1], root / L"h264-mf-rgba.mov", true, dolly::video::Codec::h264_mf);
        record(argv[1], root / L"fixed-stall.mkv", true, dolly::video::Codec::lossless, true, true);
        record(argv[1], root / L"realtime-stall.mkv", true, dolly::video::Codec::lossless, true, false);
        if (argc == 4) {
            record(argv[1], root / L"h264-auto-rgba.mp4", true, dolly::video::Codec::auto_select);
            record(argv[1], root / L"hevc-nvenc-rgba.mp4", true, dolly::video::Codec::hevc_nvenc);
        }
        std::puts("WARP color fixtures recorded successfully");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL: %s\n", error.what());
        return 1;
    }
}
