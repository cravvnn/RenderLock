#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <codecapi.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <cstdio>
#include <io.h>
#include <fcntl.h>
#include <cwchar>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "dolly_video.hpp"
#include "dolly_video_math.hpp"
#include "dolly_encoder_error.hpp"
#include "dolly_capture_timing.hpp"
#include "dolly_depth_scene.hpp"
#include "dolly_depth_sequence.hpp"
#include "dolly_sound_capture.hpp"
#include <cmath>

namespace dolly::video {
namespace {
constexpr unsigned kSlots = 3;
constexpr std::size_t kCameraFrameLimit = 262144;
// Sized for an 8K backbuffer (high-resolution stills); 4K takes are unchanged.
constexpr std::uint64_t kMemoryLimit = 2048ULL * 1024 * 1024;
constexpr UINT kMaxDimension = 8192;
template <class T> struct Com {
    T* p = nullptr;
    ~Com() { reset(); }
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
    T** put() {
        reset();
        return &p;
    }
    void reset() {
        if (p) {
            p->Release();
            p = nullptr;
        }
    }
    T* operator->() const { return p; }
};
template <class T> bool load_function(HMODULE module, const char* name, T& result) noexcept {
    const FARPROC address = GetProcAddress(module, name);
    static_assert(sizeof(address) == sizeof(result), "Windows function pointer width");
    std::memcpy(&result, &address, sizeof(result));
    return result != nullptr;
}
struct MediaApi {
    HMODULE platform = nullptr, readwrite = nullptr;
    decltype(&::MFStartup) MFStartup = nullptr;
    decltype(&::MFShutdown) MFShutdown = nullptr;
    decltype(&::MFCreateFile) MFCreateFile = nullptr;
    decltype(&::MFCreateAttributes) MFCreateAttributes = nullptr;
    decltype(&::MFCreateMediaType) MFCreateMediaType = nullptr;
    decltype(&::MFCreateMemoryBuffer) MFCreateMemoryBuffer = nullptr;
    decltype(&::MFCreateSample) MFCreateSample = nullptr;
    decltype(&::MFCreateSinkWriterFromURL) MFCreateSinkWriterFromURL = nullptr;
    bool load() noexcept {
        platform = LoadLibraryExW(L"mfplat.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
        readwrite = LoadLibraryExW(L"mfreadwrite.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!platform || !readwrite)
            return false;
        return load_function(platform, "MFStartup", MFStartup) &&
               load_function(platform, "MFShutdown", MFShutdown) &&
               load_function(platform, "MFCreateFile", MFCreateFile) &&
               load_function(platform, "MFCreateAttributes", MFCreateAttributes) &&
               load_function(platform, "MFCreateMediaType", MFCreateMediaType) &&
               load_function(platform, "MFCreateMemoryBuffer", MFCreateMemoryBuffer) &&
               load_function(platform, "MFCreateSample", MFCreateSample) &&
               load_function(readwrite, "MFCreateSinkWriterFromURL", MFCreateSinkWriterFromURL);
    }
    void unload() noexcept {
        if (readwrite) {
            FreeLibrary(readwrite);
            readwrite = nullptr;
        }
        if (platform) {
            FreeLibrary(platform);
            platform = nullptr;
        }
    }
    ~MediaApi() { unload(); }
};
struct FrameStamp {
    double phase = -1;
    bool native = false;
    bool has_camera = false;
    CameraSample camera{};
    double capture_time = 0;
    MovieAudioSample audio{};
};
struct CameraFrame {
    std::uint64_t frame = 0;
    double video_time = 0, phase = 0;
    CameraSample camera{};
    double capture_time = 0;
};
struct CpuFrame {
    FrameStamp stamp;
    std::vector<std::uint8_t> pixels;
    std::uint64_t pts = 0;
    depth::RawFrame depth;
};
struct Session {
    std::wstring path;
    std::uint32_t fps = 30, bitrate = 20000000;
    Encoder encoder = Encoder::media_foundation;
    Codec codec = Codec::auto_select;
    std::uint32_t quality = 0, preset = 0;
    bool fixed_step = false;
    bool depth_enabled = false;
    bool depth_exr = false;
    bool shot_only = false;
    bool white_clear = false;
    bool camera_enabled = false;
    bool camera_only = false;
    std::uint64_t camera_view_serial = 0; // Present owner only.
    const char* camera_partial = nullptr;
    std::uint32_t max_frames = 0;
    // Depth layer folder and its ProRes master, derived from the color path's
    // parent when depth is enabled: <color parent>\depth\depth.mov.
    std::wstring depth_directory, depth_video_path;
    ShotRange shot;
    ShotClockTrace shot_clock;
    std::vector<CameraFrame> camera_trace;     // Touched only by the encoder thread.
    std::vector<MovieAudioSample> audio_trace; // Only successfully written frames.
    const char* audio_partial = nullptr;
    CaptureTrace capture_trace;
    depth::Sequence depth_output; // Touched only by the encoder thread.
    std::wstring ffmpeg;
    // Published once by the render callback before configured=true.
    std::uint32_t width = 0, height = 0;
    bool rgba = false;
    std::atomic<bool> configured{false}, ready{false}, stopping{false}, cancel{false}, done{false};
    std::atomic<unsigned> producer_active{0};
    std::atomic<State> state{State::starting};
    std::atomic<HRESULT> error{S_OK};
    std::atomic<const wchar_t*> error_text{L""};
    // Backing store for a dynamically composed error (FFmpeg stderr). Filled
    // once before error_text is published; never mutated afterwards.
    wchar_t error_buffer[256]{};
    std::atomic<std::uint64_t> written{0}, dropped{0}, duration{0};
    // Depth-only diagnostics: pre-play frames are skipped because depth needs
    // an exact replay time. Zero color frames plus non-zero skips identifies a
    // shot that never started, instead of a silent empty take.
    std::atomic<std::uint64_t> depth_skips{0}, depth_skip_since{0}, scene_skip_since{0};
    std::atomic<double> depth_last_replay{-2.0};
    // Last authored-path time submitted. The path clamps at its end, so
    // duplicate clamped frames are dropped to keep every take the same length.
    std::atomic<double> shot_last_replay{-1.0};
    // Capture-path accounting for zero-frame diagnosis.
    std::atomic<std::uint64_t> capture_calls{0}, capture_not_ready{0}, capture_preplay{0},
        capture_scene_skips{0}, capture_dropped{0}, capture_submitted{0}, capture_produced{0};
    // The render callback is the sole producer, the encoder the sole consumer.
    CpuFrame cpu[kSlots];
    std::atomic<std::uint64_t> produced{0}, consumed{0}, gpu_pending{0};
    std::atomic<std::uint64_t> first_qpc{0}, stop_qpc{0};
    std::uint64_t frequency = 1;
    std::mutex wait_mutex;
    std::condition_variable wake;
};
struct GpuSlot {
    FrameStamp stamp;
    Com<ID3D11Texture2D> staging;
    std::uint64_t pts = 0;
    depth::RawFrame depth;
    bool depth_ready = false;
};
struct RenderResources {
    // These objects are touched only under the caller's Present/Resize lock.
    std::shared_ptr<Session> owner;
    IDXGISwapChain* swapchain = nullptr; // Identity only; never dereferenced later.
    ID3D11Device* device = nullptr;
    GpuSlot slots[kSlots];
    Com<ID3D11Texture2D> resolved;
    D3D11_TEXTURE2D_DESC description{};
    std::uint64_t submitted = 0, drained = 0;
    Cadence cadence;
    depth::Readback depth_queue;
    void clear() noexcept {
        for (auto& slot : slots) {
            slot.staging.reset();
            slot.depth = {};
            slot.depth_ready = false;
        }
        depth_queue.reset();
        resolved.reset();
        if (owner)
            owner->gpu_pending.store(0, std::memory_order_release);
        owner.reset();
        swapchain = nullptr;
        device = nullptr;
        description = {};
        submitted = drained = 0;
        cadence = {};
    }
};
std::mutex control_mutex;
std::shared_ptr<Session> current;
// The native DLL stays resident until process exit. A process-lifetime thread
// holder avoids std::thread's terminating destructor during abrupt ExitProcess;
// normal session cleanup still explicitly joins through shutdown().
std::thread* encoder_thread = nullptr;
std::atomic<unsigned> deferred_stop{0};
std::atomic<std::uint64_t> deferred_stop_qpc{0};
std::atomic<std::uint64_t> gCaptureAttempts{0}, gCaptureNoLock{0};
struct CachedStatus {
    std::atomic<State> state{State::idle};
    std::atomic<std::uint32_t> width{0}, height{0}, fps{0}, error_code{0};
    std::atomic<std::uint64_t> written{0}, dropped{0}, duration{0}, first_qpc{0}, frequency{0};
    std::atomic<bool> depth{false};
    std::atomic<bool> white_clear{false};
    std::atomic<bool> shot_only{false};
} cached;
// The take whose error text the cached snapshot reports. Holding it keeps a
// composed error_buffer alive for lock-free readers after start() replaces it.
std::shared_ptr<const Session> cached_owner;
RenderResources gpu;
// Published by the bridge for every successful Mode::Play path evaluation.
// The recorder pairs frames with the native authored path directly instead of
// the editor config round trip, which keeps separate layer takes aligned.
std::atomic<bool> gPathPlaying{false};
std::atomic<bool> gPathCompleted{false};
std::atomic<double> gPathPhase{0};
// The camera applied with gPathPhase. Seven doubles cannot be one atomic, so a
// short mutex keeps a capture from pairing one frame's position with another's.
std::mutex gCameraMutex;
CameraSample gCamera{};
bool gCameraValid = false;
double gCameraPhase = -1;
std::uint64_t gCameraSerial = 0; // Protected by gCameraMutex.

std::uint64_t now_qpc() noexcept {
    LARGE_INTEGER result{};
    QueryPerformanceCounter(&result);
    return std::uint64_t(result.QuadPart);
}
void request_stop(Session& s, bool cancel) noexcept {
    if (cancel)
        s.cancel.store(true, std::memory_order_release);
    std::uint64_t unset = 0;
    s.stop_qpc.compare_exchange_strong(unset, now_qpc());
    s.stopping.store(true, std::memory_order_release);
    s.wake.notify_one();
}
void apply_deferred_stop(Session& s) noexcept {
    const auto request = deferred_stop.exchange(0, std::memory_order_acq_rel);
    if (!request)
        return;
    auto timestamp = deferred_stop_qpc.exchange(0);
    if (!timestamp)
        timestamp = now_qpc();
    std::uint64_t unset = 0;
    s.stop_qpc.compare_exchange_strong(unset, timestamp);
    request_stop(s, request == 2);
}
// Stop barrier with capture()'s ProducerScope (VideoCompleteness.tla). This is
// a store/load handshake on both sides (ready, then producer_active; and
// producer_active, then ready), so all four operations are seq_cst: with
// release/acquire each side may read the other's stale value and a producer
// could publish a frame after the encoder's drain and gpu_pending exchange.
void quiesce_producers(Session& s) noexcept {
    s.ready.store(false, std::memory_order_seq_cst);
    while (s.producer_active.load(std::memory_order_seq_cst))
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
}
// The one cancel decision for finalization, taken after quiesce_producers().
// stop(true) or a Present's deferred stop can still set cancel until done is
// published; re-reading it could delete the color file but keep depth, or keep
// the files and sidecars while reporting the take cancelled. Cancel is
// monotonic, so earlier reads that saw it agree with this snapshot; a cancel
// after this point is too late and the take completes consistently.
bool decide_cancel(Session& s) noexcept {
    apply_deferred_stop(s);
    return s.cancel.load(std::memory_order_acquire);
}
// The published error text must stay valid after the caller's frame returns:
// several failure paths format into a local buffer, and the Python side reads
// the message later on its poll cadence.
void publish_error(Session& s, HRESULT hr, const wchar_t* message, std::size_t length) noexcept {
    HRESULT empty = S_OK;
    if (s.error.compare_exchange_strong(empty, FAILED(hr) ? hr : E_FAIL)) {
        const auto count = std::min<std::size_t>(message ? length : 0, 255);
        if (count)
            std::wmemcpy(s.error_buffer, message, count);
        s.error_buffer[count] = 0;
        s.error_text.store(s.error_buffer, std::memory_order_release);
    }
    request_stop(s, false);
}
void fail(Session& s, HRESULT hr, const wchar_t* message) noexcept {
    publish_error(s, hr, message, message ? wcsnlen(message, 255) : 0);
}
bool active(State state) noexcept {
    return state == State::starting || state == State::recording || state == State::finalizing;
}
// Optional metadata is written to a CREATE_NEW temporary, flushed to disk and
// renamed without replacing anything, so a failure never leaves a partial file
// under the final name or overwrites a foreign one. False means not published.
template <class Write>
bool publish_sidecar(const Session& s, const std::wstring& destination, Write write) noexcept {
    std::wstring temporary;
    bool owned = false;
    try {
        temporary = destination + L".pending-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
                    std::to_wstring(s.first_qpc.load());
        HANDLE handle = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                    FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
            return false;
        owned = true;
        const int fd = _open_osfhandle(reinterpret_cast<intptr_t>(handle), _O_BINARY);
        if (fd < 0) {
            CloseHandle(handle);
            DeleteFileW(temporary.c_str());
            return false;
        }
        std::FILE* file = _fdopen(fd, "wb");
        if (!file) {
            _close(fd);
            DeleteFileW(temporary.c_str());
            return false;
        }
        write(file);
        const bool flushed = std::fflush(file) == 0 && std::ferror(file) == 0 && _commit(fd) == 0;
        const bool closed = std::fclose(file) == 0;
        // No MOVEFILE_REPLACE_EXISTING: an existing destination is never ours.
        const bool published =
            flushed && closed &&
            MoveFileExW(temporary.c_str(), destination.c_str(), MOVEFILE_WRITE_THROUGH) != 0;
        if (!published)
            DeleteFileW(temporary.c_str());
        return published;
    } catch (...) {
        if (owned)
            DeleteFileW(temporary.c_str());
        return false;
    }
}
// Sidecar metadata is additive; a failed write never invalidates the video.
bool write_shot_sidecar(const Session& s) noexcept {
    std::string leaf;
    try {
        const auto slash = s.path.find_last_of(L"\\/");
        const auto* base =
            slash == std::wstring::npos ? s.path.c_str() : s.path.c_str() + slash + 1;
        const int size = WideCharToMultiByte(CP_UTF8, 0, base, -1, nullptr, 0, nullptr, nullptr);
        if (size > 1) {
            leaf.resize(std::size_t(size - 1));
            WideCharToMultiByte(CP_UTF8, 0, base, -1, leaf.data(), size, nullptr, nullptr);
        }
        return publish_sidecar(s, s.path + L".shot.json", [&](std::FILE* file) {
            const auto frames = static_cast<unsigned long long>(s.written.load(std::memory_order_acquire));
            const auto duration = double(s.duration.load(std::memory_order_acquire)) / 10000000.0;
            const auto first = static_cast<unsigned long long>(s.shot.first);
            const auto last = static_cast<unsigned long long>(s.shot.last);
            std::fprintf(file,
                         "{\n  \"format\": \"deadlock-dolly-shot\",\n  \"version\": 1,\n"
                         "  \"video_file\": \"%s\",\n  \"fps\": %u,\n  \"fixed_step\": %s,\n"
                         "  \"frames_written\": %llu,\n  \"duration_seconds\": %.7f,\n"
                         "  \"first_frame\": %llu,\n  \"last_frame\": %llu,\n"
                         "  \"first_replay_time\": %.9f,\n  \"last_replay_time\": %.9f,\n",
                         leaf.c_str(), s.fps, s.fixed_step ? "true" : "false", frames, duration, first,
                         last, s.shot.first_time, s.shot.last_time);
            std::fprintf(file,
                         "  \"clock_native_frames\": %llu,\n  \"clock_fallback_frames\": %llu,\n"
                         "  \"clock_samples_limit\": %zu,\n  \"clock_samples\": [",
                         static_cast<unsigned long long>(s.shot_clock.native_frames),
                         static_cast<unsigned long long>(s.shot_clock.fallback_frames),
                         s.shot_clock.samples.size());
            for (std::size_t i = 0; i < s.shot_clock.count; ++i) {
                const auto& sample = s.shot_clock.samples[i];
                std::fprintf(file, "%s\n    {\"frame\": %llu, \"phase\": %.9f, \"source\": \"%s\"}",
                             i ? "," : "", static_cast<unsigned long long>(sample.frame), sample.phase,
                             sample.native ? "native" : "editor");
            }
            std::fprintf(file, "\n  ],\n  \"capture_qpc_frequency\": %llu,\n  \"capture_samples\": [",
                         static_cast<unsigned long long>(s.frequency));
            for (std::size_t i = 0; i < s.capture_trace.size; ++i) {
                const auto& t = s.capture_trace.samples[i];
                std::fprintf(file,
                             "%s\n    {\"entry\": %llu, \"readback\": %llu, \"end\": %llu, "
                             "\"phase\": %.9f, \"pending\": %u, \"outcome\": %u, \"missed\": %llu}",
                             i ? "," : "", static_cast<unsigned long long>(t.entry),
                             static_cast<unsigned long long>(t.readback),
                             static_cast<unsigned long long>(t.end), t.phase, t.pending,
                             static_cast<unsigned>(t.outcome), static_cast<unsigned long long>(t.missed));
            }
            std::fprintf(file,
                         "\n  ],\n  \"movie_audio\": {\"version\": 1, \"complete\": %s, \"frames\": [",
                         !s.audio_partial && s.audio_trace.size() == frames && frames ? "true" : "false");
            for (std::size_t i = 0; i < s.audio_trace.size(); ++i) {
                const auto& a = s.audio_trace[i];
                std::fprintf(file, "%s\n    [%llu, %llu, %llu, %llu, %u]", i ? "," : "",
                             static_cast<unsigned long long>(a.take),
                             static_cast<unsigned long long>(a.first),
                             static_cast<unsigned long long>(a.end),
                             static_cast<unsigned long long>(a.movie_frame), a.rate);
            }
            std::fprintf(file, "\n  ]}\n}\n");
        });
    } catch (...) {
        return false;
    }
}
// Optional metadata has independent ownership and finalization: failure never
// removes a valid video, overwrites a foreign sidecar, or masquerades as complete.
bool write_camera_sidecar(const Session& s) noexcept {
    if (!s.camera_enabled)
        return true;
    try {
        const auto destination = s.camera_only ? s.path : s.path + L".camera-track.json";
        return publish_sidecar(s, destination, [&](std::FILE* file) {
            const auto count = s.written.load(std::memory_order_acquire);
            const bool complete = !s.camera_partial && count > 0 && s.camera_trace.size() == count &&
                                  (!s.camera_only || count == s.max_frames);
            const char* reason = s.camera_partial ? s.camera_partial : "missing camera frames";
            std::fprintf(
                file,
                "{\n  \"format\": \"deadlock-dolly-camera-track\",\n  \"version\": 2,\n"
                "  \"fps\": %u,\n  \"fixed_step\": %s,\n  \"frames_written\": %llu,\n"
                "  \"first_qpc\": %llu,\n  \"qpc_frequency\": %llu,\n"
                "  \"width\": %u,\n  \"height\": %u,\n  \"depth\": %s,\n"
                "  \"complete\": %s,\n  \"partial_reason\": \"%s\",\n"
                "  \"source\": \"native-view-hook\",\n"
                "  \"fields\": [\"frame\", \"video_time\", \"phase\", \"x\", \"y\", \"z\", "
                "\"pitch\", \"yaw\", \"roll\", \"horizontal_fov\", \"capture_time\"],\n  \"frames\": [",
                s.fps, s.fixed_step ? "true" : "false", static_cast<unsigned long long>(count),
                static_cast<unsigned long long>(s.first_qpc.load()),
                static_cast<unsigned long long>(s.frequency), s.width, s.height,
                s.depth_enabled ? "true" : "false", complete ? "true" : "false",
                complete ? "" : reason);
            for (std::size_t i = 0; i < s.camera_trace.size(); ++i) {
                const auto& f = s.camera_trace[i];
                const auto& c = f.camera;
                std::fprintf(
                    file,
                    "%s\n    [%llu, %.9f, %.9f, %.17g, %.17g, %.17g, %.17g, %.17g, %.17g, %.17g, %.9f]",
                    i ? "," : "", static_cast<unsigned long long>(f.frame), f.video_time, f.phase,
                    c.position[0], c.position[1], c.position[2], c.angles[0], c.angles[1], c.angles[2],
                    c.horizontal_fov, f.capture_time);
            }
            std::fprintf(file, "\n  ]\n}\n");
        });
    } catch (...) {
        return false;
    }
}

HRESULT media_type(IMFMediaType* type, const GUID& subtype, const Session& s) {
    HRESULT hr = type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    if (SUCCEEDED(hr))
        hr = type->SetGUID(MF_MT_SUBTYPE, subtype);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
    if (SUCCEEDED(hr))
        hr = MFSetAttributeSize(type, MF_MT_FRAME_SIZE, s.width, s.height);
    if (SUCCEEDED(hr))
        hr = MFSetAttributeRatio(type, MF_MT_FRAME_RATE, s.fps, 1);
    if (SUCCEEDED(hr))
        hr = MFSetAttributeRatio(type, MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT709);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE, MFNominalRange_16_235);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_VIDEO_PRIMARIES, MFVideoPrimaries_BT709);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_TRANSFER_FUNCTION, MFVideoTransFunc_709);
    return hr;
}

HRESULT begin_writer(MediaApi& api, Session& s, Com<IMFByteStream>& file,
                     Com<IMFSinkWriter>& writer, DWORD& stream, bool& created) {
    // This operation atomically refuses existing files, including races after
    // a file picker/launcher existence check. It never truncates another file.
    HRESULT hr = api.MFCreateFile(MF_ACCESSMODE_WRITE, MF_OPENMODE_FAIL_IF_EXIST, MF_FILEFLAGS_NONE,
                                  s.path.c_str(), file.put());
    if (FAILED(hr))
        return hr;
    created = true;
    Com<IMFAttributes> attributes;
    hr = api.MFCreateAttributes(attributes.put(), 2);
    if (SUCCEEDED(hr))
        hr = attributes->SetGUID(MF_TRANSCODE_CONTAINERTYPE, MFTranscodeContainerType_MPEG4);
    if (SUCCEEDED(hr))
        hr = attributes->SetUINT32(MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, TRUE);
    if (SUCCEEDED(hr))
        hr = api.MFCreateSinkWriterFromURL(nullptr, file.p, attributes.p, writer.put());
    Com<IMFMediaType> output, input;
    if (SUCCEEDED(hr))
        hr = api.MFCreateMediaType(output.put());
    if (SUCCEEDED(hr))
        hr = media_type(output.p, MFVideoFormat_H264, s);
    if (SUCCEEDED(hr))
        hr = output->SetUINT32(MF_MT_AVG_BITRATE, s.bitrate);
    if (SUCCEEDED(hr))
        hr = output->SetUINT32(MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Base);
    if (SUCCEEDED(hr))
        hr = writer->AddStream(output.p, &stream);
    if (SUCCEEDED(hr))
        hr = api.MFCreateMediaType(input.put());
    if (SUCCEEDED(hr))
        hr = media_type(input.p, MFVideoFormat_NV12, s);
    if (SUCCEEDED(hr))
        hr = input->SetUINT32(MF_MT_DEFAULT_STRIDE, s.width);
    if (SUCCEEDED(hr))
        hr = writer->SetInputMediaType(stream, input.p, nullptr);
    // The sink writer's default throttling stays enabled: any encoder
    // backpressure waits here on this worker, never on the render thread.
    if (SUCCEEDED(hr))
        hr = writer->BeginWriting();
    return hr;
}

HRESULT make_sample(MediaApi& api, const Session& s, const CpuFrame& frame,
                    Com<IMFSample>& sample) {
    const DWORD bytes = s.width * s.height * 3 / 2;
    Com<IMFMediaBuffer> buffer;
    HRESULT hr = api.MFCreateMemoryBuffer(bytes, buffer.put());
    BYTE* destination = nullptr;
    if (SUCCEEDED(hr))
        hr = buffer->Lock(&destination, nullptr, nullptr);
    if (SUCCEEDED(hr)) {
        rgb_to_nv12(frame.pixels.data(), std::size_t(s.width) * 4, destination, s.width, s.height,
                    s.rgba);
        hr = buffer->Unlock();
    }
    if (SUCCEEDED(hr))
        hr = buffer->SetCurrentLength(bytes);
    if (SUCCEEDED(hr))
        hr = api.MFCreateSample(sample.put());
    if (SUCCEEDED(hr))
        hr = sample->AddBuffer(buffer.p);
    if (SUCCEEDED(hr))
        hr = sample->SetSampleTime(LONGLONG(frame.pts));
    return hr;
}
// Metadata follows successful output writes, not GPU submissions that may still
// be pending at stop. Only the encoder worker mutates the written-frame range.
void observe_written_frame(Session& s, const FrameStamp& stamp, double video_time) noexcept {
    const auto index = s.written.fetch_add(1, std::memory_order_relaxed);
    s.shot.observe(index, stamp.phase);
    s.shot_clock.observe(index, stamp.phase, stamp.native);
    if (s.fixed_step && !s.audio_partial) {
        if (!stamp.audio.take || stamp.audio.end <= stamp.audio.first)
            s.audio_partial = "missing movie PCM sample";
        else if (s.audio_trace.size() >= kCameraFrameLimit)
            s.audio_partial = "movie audio frame limit reached";
        else {
            try {
                s.audio_trace.push_back(stamp.audio);
            } catch (...) {
                s.audio_partial = "movie audio allocation failed";
            }
        }
    }
    if (s.camera_enabled && !s.camera_partial) {
        bool valid = stamp.has_camera && std::isfinite(stamp.phase) && stamp.phase >= 0 &&
                     std::isfinite(video_time) && video_time >= 0 &&
                     std::isfinite(stamp.camera.horizontal_fov) &&
                     stamp.camera.horizontal_fov > 1 && stamp.camera.horizontal_fov < 179;
        for (unsigned i = 0; i < 3; ++i)
            valid = valid && std::isfinite(stamp.camera.position[i]) &&
                    std::isfinite(stamp.camera.angles[i]);
        if (!valid)
            s.camera_partial = "missing or invalid camera sample";
        else if (s.camera_trace.size() >= kCameraFrameLimit)
            s.camera_partial = "camera frame limit reached";
        else {
            try {
                s.camera_trace.push_back(
                    {index, video_time, stamp.phase, stamp.camera, stamp.capture_time});
            } catch (...) {
                s.camera_partial = "camera allocation failed";
            }
        }
    }
}
HRESULT write_sample(Session& s, IMFSinkWriter* writer, DWORD stream, IMFSample* sample,
                     std::uint64_t start_pts, std::uint64_t end_pts, const FrameStamp& stamp) {
    const auto duration = std::max<std::uint64_t>(1, end_pts > start_pts ? end_pts - start_pts : 1);
    HRESULT hr = sample->SetSampleDuration(LONGLONG(duration));
    if (SUCCEEDED(hr))
        hr = writer->WriteSample(stream, sample);
    if (SUCCEEDED(hr)) {
        observe_written_frame(s, stamp, double(start_pts) / 10000000.0);
        s.duration.store(start_pts + duration, std::memory_order_release);
    }
    return hr;
}
HRESULT consume_frame(MediaApi& api, Session& s, IMFSinkWriter* writer, DWORD stream,
                      Com<IMFSample>& previous, std::uint64_t& previous_pts,
                      FrameStamp& previous_stamp) {
    const auto read = s.consumed.load(std::memory_order_relaxed);
    auto& frame = s.cpu[read % kSlots];
    Com<IMFSample> next;
    HRESULT hr = make_sample(api, s, frame, next);
    if (SUCCEEDED(hr) && s.depth_enabled && !s.depth_output.write(frame.depth, frame.pts)) {
        hr = s.depth_output.error();
        fail(s, hr, L"Depth frame could not be written; paired recording stopped.");
    }
    const auto pts = frame.pts;
    const auto stamp = frame.stamp;
    s.consumed.store(read + 1, std::memory_order_release);
    s.wake.notify_all();
    if (SUCCEEDED(hr) && previous.p)
        hr = write_sample(s, writer, stream, previous.p, previous_pts, pts, previous_stamp);
    if (FAILED(hr))
        return hr;
    previous.reset();
    previous.p = next.p;
    next.p = nullptr;
    previous_pts = pts;
    previous_stamp = stamp;
    return S_OK;
}
// ---- FFmpeg external-encoder backend -------------------------------------
// The capture path above is unchanged: this backend only replaces the sink
// that consumes owned CPU frames, so a slow encoder can never stall rendering.
void fail_text(Session& s, HRESULT hr, const std::wstring& message) noexcept {
    publish_error(s, hr, message.c_str(), message.size());
}
std::wstring capture_diagnostic(const Session& s) {
    wchar_t text[448]{};
    std::swprintf(
        text, 448,
        L"capture attempts=%llu no-lock=%llu calls=%llu not-ready=%llu preplay=%llu "
        L"scene-skip=%llu submitted=%llu dropped=%llu produced=%llu",
        static_cast<unsigned long long>(gCaptureAttempts.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(gCaptureNoLock.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(s.capture_calls.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(s.capture_not_ready.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(s.capture_preplay.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(s.capture_scene_skips.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(s.capture_submitted.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(s.capture_dropped.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(s.capture_produced.load(std::memory_order_relaxed)));
    return text;
}
std::wstring narrow_to_wide(const char* text) {
    if (!text || !*text)
        return L"";
    const int wide = MultiByteToWideChar(CP_UTF8, 0, text, -1, nullptr, 0);
    if (wide <= 1)
        return L"";
    std::wstring converted(static_cast<std::size_t>(wide), L'\0');
    if (MultiByteToWideChar(CP_UTF8, 0, text, -1, converted.data(), wide) != wide)
        return L"";
    converted.resize(static_cast<std::size_t>(wide) - 1);
    return converted;
}
// A depth take never opens without a verified scene frame. When the whole
// take was skipped by the scene gate, lead with that and the scene observer's
// last reason (observed target, size, recording size): the published error is
// capped, so the cause must come before any counters.
std::wstring zero_frame_message(const Session& s) {
    const auto skips = s.capture_scene_skips.load(std::memory_order_relaxed);
    if (s.depth_enabled && skips) {
        wchar_t lead[192]{};
        std::swprintf(
            lead, 192,
            L"No depth frame could be verified after %llu rendered frames, so the take was "
            L"discarded. ",
            static_cast<unsigned long long>(skips));
        return std::wstring(lead) + narrow_to_wide(depth::scene_diagnostic());
    }
    return L"Recording ended before any game frame was captured. (" + capture_diagnostic(s) + L")";
}
const wchar_t* codec_token(Codec codec) noexcept {
    switch (codec) {
    case Codec::h264_nvenc:
        return L"h264_nvenc";
    case Codec::hevc_nvenc:
        return L"hevc_nvenc";
    case Codec::h264_mf:
        return L"h264_mf";
    case Codec::libx264:
        return L"libx264";
    case Codec::libx265:
        return L"libx265";
    case Codec::h264_qsv:
        return L"h264_qsv";
    case Codec::hevc_qsv:
        return L"hevc_qsv";
    case Codec::h264_amf:
        return L"h264_amf";
    case Codec::hevc_amf:
        return L"hevc_amf";
    case Codec::lossless:
        return L"ffv1";
    default:
        return L"h264_nvenc";
    }
}
std::uint32_t default_quality(Codec codec) noexcept {
    switch (codec) {
    case Codec::h264_mf:
    case Codec::lossless:
        return 0; // bitrate / lossless: no constant-quality value
    default:
        return 20;
    }
}
std::wstring preset_token(Codec codec, std::uint32_t index) {
    if (!index)
        index = 5;
    switch (codec) {
    case Codec::h264_nvenc:
    case Codec::hevc_nvenc:
        return L"p" + std::to_wstring(std::min<std::uint32_t>(index, 7));
    case Codec::libx264:
    case Codec::libx265: {
        static const wchar_t* names[] = {L"ultrafast", L"superfast", L"veryfast", L"faster",
                                         L"fast",      L"medium",    L"slow"};
        return names[std::min<std::uint32_t>(index, 7) - 1];
    }
    default:
        return L"";
    }
}
std::wstring quote_arg(const std::wstring& value) {
    std::wstring out;
    out.reserve(value.size() + 2);
    out.push_back(L'"');
    for (wchar_t c : value) {
        if (c == L'"')
            out.push_back(L'\\');
        out.push_back(c);
    }
    out.push_back(L'"');
    return out;
}
std::wstring build_ffmpeg_command(const Session& s) {
    const bool mp4 =
        s.path.size() >= 4 && _wcsicmp(s.path.c_str() + s.path.size() - 4, L".mp4") == 0;
    const bool mov =
        s.path.size() >= 4 && _wcsicmp(s.path.c_str() + s.path.size() - 4, L".mov") == 0;
    const Codec codec = s.codec == Codec::auto_select ? Codec::h264_nvenc : s.codec;
    std::wstring cmd = quote_arg(s.ffmpeg);
    cmd += L" -hide_banner -loglevel error -nostdin -n";
    cmd += L" -f rawvideo -pixel_format ";
    cmd += s.rgba ? L"rgba" : L"bgra";
    cmd += L" -video_size " + std::to_wstring(s.width) + L"x" + std::to_wstring(s.height);
    cmd += L" -framerate " + std::to_wstring(s.fps);
    cmd += L" -i pipe:0 -an -c:v ";
    cmd += codec_token(codec);
    const auto preset = preset_token(codec, s.preset);
    if (!preset.empty())
        cmd += L" -preset " + preset;
    const auto quality = s.quality ? s.quality : default_quality(codec);
    switch (codec) {
    case Codec::h264_nvenc:
    case Codec::hevc_nvenc:
        cmd += L" -rc vbr -cq " + std::to_wstring(quality) + L" -b:v 0";
        break;
    case Codec::libx264:
    case Codec::libx265:
        cmd += L" -crf " + std::to_wstring(quality) + L" -b:v 0";
        break;
    case Codec::h264_qsv:
    case Codec::hevc_qsv:
        cmd += L" -global_quality " + std::to_wstring(quality);
        break;
    case Codec::h264_amf:
    case Codec::hevc_amf:
        cmd +=
            L" -rc cqp -qp_i " + std::to_wstring(quality) + L" -qp_p " + std::to_wstring(quality);
        break;
    case Codec::lossless:
        cmd += L" -pix_fmt bgra";
        break;
    default:
        cmd += L" -b:v " + std::to_wstring(s.bitrate);
        break;
    }
    if (codec != Codec::lossless) {
        // Raw game RGB has no color metadata. Set both the actual conversion
        // and the frame/output tags; output options alone can leave primaries
        // and transfer characteristics unspecified in the encoded file.
        cmd += L" -vf scale=out_color_matrix=bt709:out_range=tv,"
               L"setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
               L" -pix_fmt yuv420p -colorspace bt709 -color_primaries bt709"
               L" -color_trc bt709 -color_range tv";
    }
    if (mov && codec == Codec::h264_mf) {
        // The MF H.264 encoder can omit the range flag, and QuickTime MOV
        // does not otherwise preserve it. Signal the actual limited range in
        // the H.264 stream itself so editors do not have to guess.
        cmd += L" -bsf:v h264_metadata=video_full_range_flag=0";
    }
    if (mp4)
        cmd += L" -movflags +faststart";
    cmd += L" ";
    cmd += quote_arg(s.path);
    return cmd;
}
// The paired depth master is a 10-bit ProRes 4444 .mov fed with linear
// gray16 samples. The 0..kDepthUnitMax mapping is documented in the layer's
// manifest.json; the float EXR sequence stays an optional precision master.
// The stream size is the verified scene target's size, which can be below the
// color recording when the game renders the scene at an internal resolution.
std::wstring build_depth_command(const Session& s, std::uint32_t width, std::uint32_t height) {
    std::wstring cmd = quote_arg(s.ffmpeg);
    cmd += L" -hide_banner -loglevel error -nostdin -n";
    cmd += L" -f rawvideo -pixel_format gray16le";
    cmd += L" -video_size " + std::to_wstring(width) + L"x" + std::to_wstring(height);
    cmd += L" -framerate " + std::to_wstring(s.fps);
    cmd += L" -i pipe:0 -an -c:v prores_ks -profile:v 4444 -pix_fmt yuv444p10le";
    cmd += L" ";
    cmd += quote_arg(s.depth_video_path);
    return cmd;
}
HRESULT write_all(HANDLE pipe, const void* data, std::size_t bytes) noexcept {
    const auto* cursor = static_cast<const unsigned char*>(data);
    while (bytes) {
        DWORD written = 0;
        const DWORD chunk = static_cast<DWORD>(std::min<std::size_t>(bytes, 1u << 24));
        if (!WriteFile(pipe, cursor, chunk, &written, nullptr) || !written)
            return HRESULT_FROM_WIN32(GetLastError() ? GetLastError() : ERROR_BROKEN_PIPE);
        cursor += written;
        bytes -= written;
    }
    return S_OK;
}
std::wstring read_log_error(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return L"";
    LARGE_INTEGER size{};
    std::string data;
    if (GetFileSizeEx(file, &size) && size.QuadPart > 0 && size.QuadPart < (1 << 20)) {
        data.resize(static_cast<std::size_t>(size.QuadPart));
        DWORD read = 0;
        ReadFile(file, data.data(), static_cast<DWORD>(data.size()), &read, nullptr);
        data.resize(read);
    }
    CloseHandle(file);
    if (data.empty())
        return L"";
    const int wide =
        MultiByteToWideChar(CP_UTF8, 0, data.data(), static_cast<int>(data.size()), nullptr, 0);
    std::wstring out(static_cast<std::size_t>(std::max(0, wide)), L'\0');
    if (wide > 0)
        MultiByteToWideChar(CP_UTF8, 0, data.data(), static_cast<int>(data.size()), out.data(),
                            wide);
    return encoder_error_summary(out);
}
struct Child {
    HANDLE process = nullptr;
    HANDLE input = nullptr;
    std::wstring log;
    void close_input() noexcept {
        if (input) {
            CloseHandle(input);
            input = nullptr;
        }
    }
    void close_process() noexcept {
        if (process) {
            CloseHandle(process);
            process = nullptr;
        }
    }
};
// Names the first failing write stage and appends the first diagnostic from that
// child's stderr, so a stopped take keeps its cause in the media error even
// though the temporary FFmpeg logs are removed during cleanup.
enum class WriteStage { color_pipe, depth_sequence, depth_pipe };
const wchar_t* write_stage_name(WriteStage stage) noexcept {
    switch (stage) {
    case WriteStage::color_pipe:
        return L"the color FFmpeg pipe";
    case WriteStage::depth_sequence:
        return L"the depth sequence writer";
    case WriteStage::depth_pipe:
        return L"the depth FFmpeg pipe";
    }
    return L"the encoder";
}
std::wstring write_failure_detail(WriteStage stage, HRESULT hr, const Child& color,
                                  const Child& depth) {
    std::wstring detail = write_stage_name(stage);
    if (hr == HRESULT_FROM_WIN32(ERROR_BROKEN_PIPE))
        detail += L" closed its input";
    else
        detail += L" failed";
    const Child* failed = stage == WriteStage::color_pipe   ? &color
                          : stage == WriteStage::depth_pipe ? &depth
                                                            : nullptr;
    const auto tail = failed ? read_log_error(failed->log) : std::wstring();
    if (!tail.empty()) {
        detail += L": ";
        detail += tail;
    }
    return detail;
}
bool spawn_encoder(Session& s, Child& child, const std::wstring& command,
                   const std::wstring& output, const wchar_t* label, std::wstring& error) {
    if (GetFileAttributesW(output.c_str()) != INVALID_FILE_ATTRIBUTES) {
        error = L"The output file already exists. Choose a new filename.";
        return false;
    }
    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    HANDLE read_end = nullptr, write_end = nullptr;
    if (!CreatePipe(&read_end, &write_end, &sa, 0)) {
        error = L"Could not create the FFmpeg input pipe.";
        return false;
    }
    SetHandleInformation(write_end, HANDLE_FLAG_INHERIT, 0);
    HANDLE nul = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                             OPEN_EXISTING, 0, nullptr);
    wchar_t temp[MAX_PATH]{};
    GetTempPathW(MAX_PATH, temp);
    child.log = std::wstring(temp) + L"DollyFFmpeg-" + std::to_wstring(GetCurrentProcessId()) +
                L"-" + label + L"-" + std::to_wstring(GetTickCount64()) + L".log";
    HANDLE log = CreateFileW(child.log.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, nullptr);
    if (!nul || nul == INVALID_HANDLE_VALUE || !log || log == INVALID_HANDLE_VALUE) {
        if (nul && nul != INVALID_HANDLE_VALUE)
            CloseHandle(nul);
        if (log && log != INVALID_HANDLE_VALUE)
            CloseHandle(log);
        CloseHandle(read_end);
        CloseHandle(write_end);
        error = L"Could not prepare the FFmpeg output handles.";
        return false;
    }
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = read_end;
    si.hStdOutput = nul;
    si.hStdError = log;
    PROCESS_INFORMATION pi{};
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    const BOOL started = CreateProcessW(s.ffmpeg.c_str(), mutable_command.data(), nullptr, nullptr,
                                        TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
    CloseHandle(read_end);
    CloseHandle(nul);
    CloseHandle(log);
    if (!started) {
        CloseHandle(write_end);
        error = L"FFmpeg could not start. Check the selected ffmpeg.exe path and codec support.";
        return false;
    }
    CloseHandle(pi.hThread);
    child.process = pi.hProcess;
    child.input = write_end;
    return true;
}
bool spawn_ffmpeg(Session& s, Child& child, std::wstring& error) {
    return spawn_encoder(s, child, build_ffmpeg_command(s), s.path, L"color", error);
}
bool spawn_depth_ffmpeg(Session& s, Child& child, std::uint32_t width, std::uint32_t height,
                        std::wstring& error) {
    return spawn_encoder(s, child, build_depth_command(s, width, height), s.depth_video_path,
                         L"depth", error);
}
// Called only after the render producer is quiescent and CPU draining has ended.
// FFmpeg rawvideo cannot represent a hole in its constant-rate input. Fixed-step
// exports promise every captured frame regardless of the selected encoder.
void reject_incomplete_take(Session& s, bool& finalized, bool cancelled) noexcept {
    if (!finalized || cancelled || FAILED(s.error.load()) ||
        !(s.fixed_step || s.encoder == Encoder::ffmpeg) || !s.dropped.load())
        return;
    fail(s, HRESULT_FROM_WIN32(ERROR_INVALID_DATA),
         L"Export lost frames and was discarded to prevent a shortened or incomplete video. "
         L"Use fixed-step export; if already enabled, lower the export FPS or rendering load.");
    finalized = false;
}

void encode_ffmpeg(std::shared_ptr<Session> s) noexcept {
    bool created = false, finalized = false, wrote_any = false;
    std::uint64_t frames = 0;
    Child child, depth_child;
    std::vector<std::uint16_t> depth_gray;
    try {
        const auto deadline = GetTickCount64() + 10000;
        while (!s->configured.load(std::memory_order_acquire) &&
               !s->stopping.load(std::memory_order_acquire)) {
            std::unique_lock<std::mutex> lock(s->wait_mutex);
            s->wake.wait_for(lock, std::chrono::milliseconds(20));
            apply_deferred_stop(*s);
            if (GetTickCount64() > deadline)
                fail(*s, HRESULT_FROM_WIN32(ERROR_TIMEOUT),
                     L"Recording did not receive a game frame within 10 seconds. Restore the "
                     L"Deadlock window and retry. If it persists, export diagnostics.");
        }
        if (!s->stopping.load()) {
            std::wstring error;
            if (s->ffmpeg.empty() || !spawn_ffmpeg(*s, child, error)) {
                fail_text(*s, HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND),
                          error.empty() ? std::wstring(L"FFmpeg is unavailable.") : error);
            } else {
                created = true;
                // The depth encoder starts with the first verified depth frame:
                // only then is the scene target's size (and therefore the raw
                // stream size) known, which can be below the color size when
                // the game renders the scene at an internal resolution.
                if (s->depth_enabled &&
                    !s->depth_output.begin(s->depth_directory.c_str(), s->depth_exr, true, s->width,
                                           s->height))
                    fail(
                        *s, s->depth_output.error(),
                        L"Could not create a new depth layer folder. Existing folders are never overwritten.");
                for (auto& frame : s->cpu)
                    frame.pixels.resize(std::size_t(s->width) * s->height * 4);
                if (SUCCEEDED(s->error.load())) {
                    s->state.store(State::recording, std::memory_order_release);
                    s->ready.store(true, std::memory_order_release);
                }
            }
        }
        // One paired write: color frame to the main encoder, linear depth to
        // the ProRes depth.mov and the optional EXR/preview sequence. Pairing
        // is by frame index so a slow depth writer fails the take instead of
        // silently diverging the two masters. The first failure records which
        // stage failed so the media error can carry its FFmpeg stderr tail.
        WriteStage write_stage = WriteStage::color_pipe;
        const auto write_frame = [&](const CpuFrame& frame) -> HRESULT {
            write_stage = WriteStage::color_pipe;
            HRESULT hr = write_all(child.input, frame.pixels.data(), frame.pixels.size());
            if (FAILED(hr))
                return hr;
            if (!s->depth_enabled)
                return S_OK;
            write_stage = WriteStage::depth_sequence;
            if (!s->depth_output.write(frame.depth, frame.pts))
                return s->depth_output.error();
            if (!depth_child.process) {
                // The raw stream size is the verified scene target's size,
                // known once the first depth frame exists.
                write_stage = WriteStage::depth_pipe;
                std::wstring error;
                if (!spawn_depth_ffmpeg(*s, depth_child, frame.depth.frame.width,
                                        frame.depth.frame.height, error)) {
                    fail_text(*s, HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND),
                              error.empty() ? std::wstring(L"The depth encoder could not start.")
                                            : error);
                    return E_FAIL;
                }
            }
            if (depth_child.process && depth_child.input) {
                write_stage = WriteStage::depth_pipe;
                const auto* linear = s->depth_output.linear_data();
                const auto count = s->depth_output.linear_count();
                depth_gray.resize(count);
                for (std::size_t i = 0; i < count; ++i)
                    depth_gray[i] = depth_gray16(linear[i], kDepthUnitMax);
                return write_all(depth_child.input, depth_gray.data(),
                                 depth_gray.size() * sizeof(std::uint16_t));
            }
            return S_OK;
        };
        std::uint64_t stop_deadline = 0;
        while (child.process && SUCCEEDED(s->error.load()) && !s->cancel.load()) {
            apply_deferred_stop(*s);
            const bool stopping = s->stopping.load(std::memory_order_acquire);
            if (stopping && !stop_deadline) {
                s->state.store(State::finalizing, std::memory_order_release);
                stop_deadline = GetTickCount64() + 250;
            }
            const auto read = s->consumed.load(std::memory_order_relaxed);
            if (read != s->produced.load(std::memory_order_acquire)) {
                const auto& frame = s->cpu[read % kSlots];
                const auto stamp = frame.stamp;
                const HRESULT hr = write_frame(frame);
                s->consumed.store(read + 1, std::memory_order_release);
                s->wake.notify_all();
                if (FAILED(hr)) {
                    std::wstring message =
                        s->depth_enabled
                            ? L"Paired color/depth recording stopped."
                            : L"FFmpeg stopped accepting frames. Check the encoder and "
                              L"output path.";
                    message +=
                        L" (" + write_failure_detail(write_stage, hr, child, depth_child) + L")";
                    fail_text(*s, hr, message);
                    break;
                }
                wrote_any = true;
                ++frames;
                // Raw frames are piped at a constant rate: frame N is at N / fps.
                observe_written_frame(*s, stamp,
                                      double(frames - 1) / std::max<std::uint32_t>(1, s->fps));
                s->duration.store(frames * 10000000ull / std::max<std::uint32_t>(1, s->fps),
                                  std::memory_order_release);
                continue;
            }
            if (stopping && (!s->gpu_pending.load(std::memory_order_acquire) ||
                             GetTickCount64() >= stop_deadline))
                break;
            std::unique_lock<std::mutex> lock(s->wait_mutex);
            s->wake.wait_for(lock, std::chrono::milliseconds(5));
        }
        quiesce_producers(*s);
        s->dropped.fetch_add(s->gpu_pending.exchange(0), std::memory_order_relaxed);
        while (child.process && !s->cancel.load() && SUCCEEDED(s->error.load()) &&
               s->consumed.load() != s->produced.load(std::memory_order_acquire)) {
            const auto read = s->consumed.load(std::memory_order_relaxed);
            const auto& frame = s->cpu[read % kSlots];
            const auto stamp = frame.stamp;
            const HRESULT hr = write_frame(frame);
            s->consumed.store(read + 1, std::memory_order_release);
            s->wake.notify_all();
            if (FAILED(hr)) {
                std::wstring message =
                    s->depth_enabled ? L"Paired color/depth recording failed while finishing."
                                     : L"FFmpeg stopped accepting frames while finishing.";
                message += L" (" + write_failure_detail(write_stage, hr, child, depth_child) + L")";
                fail_text(*s, hr, message);
                break;
            }
            wrote_any = true;
            ++frames;
            observe_written_frame(*s, stamp,
                                  double(frames - 1) / std::max<std::uint32_t>(1, s->fps));
            s->duration.store(frames * 10000000ull / std::max<std::uint32_t>(1, s->fps),
                              std::memory_order_release);
        }
        child.close_input();
        depth_child.close_input();
        if (child.process && !s->cancel.load() && SUCCEEDED(s->error.load())) {
            if (!wrote_any) {
                fail_text(*s, E_FAIL, zero_frame_message(*s));
            } else {
                const DWORD wait = WaitForSingleObject(child.process, 120000);
                DWORD code = 1;
                GetExitCodeProcess(child.process, &code);
                if (wait != WAIT_OBJECT_0 || code != 0) {
                    const auto tail = read_log_error(child.log);
                    fail_text(
                        *s, E_FAIL,
                        tail.empty()
                            ? std::wstring(
                                  L"FFmpeg could not encode the recording. This build may lack the selected encoder.")
                            : tail);
                } else {
                    finalized = true;
                }
            }
        }
        if (finalized && depth_child.process && !s->cancel.load() && SUCCEEDED(s->error.load())) {
            const DWORD wait = WaitForSingleObject(depth_child.process, 120000);
            DWORD code = 1;
            GetExitCodeProcess(depth_child.process, &code);
            if (wait != WAIT_OBJECT_0 || code != 0) {
                const auto tail = read_log_error(depth_child.log);
                fail_text(
                    *s, E_FAIL,
                    tail.empty()
                        ? std::wstring(
                              L"The depth master could not be encoded. This FFmpeg build must support ProRes 4444.")
                        : tail);
                finalized = false;
            }
        }
    } catch (...) {
        fail(*s, E_OUTOFMEMORY,
             L"Video recording ran out of memory or the encoder could not continue.");
    }
    quiesce_producers(*s);
    const bool cancelled = decide_cancel(*s);
    if (child.process && (cancelled || FAILED(s->error.load()))) {
        TerminateProcess(child.process, 1);
        WaitForSingleObject(child.process, 5000);
    }
    if (depth_child.process && (cancelled || FAILED(s->error.load()))) {
        TerminateProcess(depth_child.process, 1);
        WaitForSingleObject(depth_child.process, 5000);
    }
    child.close_input();
    child.close_process();
    depth_child.close_input();
    depth_child.close_process();
    if (cancelled || FAILED(s->error.load()))
        s->dropped.fetch_add(s->produced.load() - s->consumed.load(), std::memory_order_relaxed);
    if (!child.log.empty())
        DeleteFileW(child.log.c_str());
    if (!depth_child.log.empty())
        DeleteFileW(depth_child.log.c_str());
    reject_incomplete_take(*s, finalized, cancelled);
    if (s->depth_enabled) {
        if (finalized && !cancelled && !s->depth_output.finish(s->written.load())) {
            wchar_t message[256]{};
            std::swprintf(
                message, 256,
                L"Depth sequence could not be finalized with the encoded color frame count (color frames %llu, depth skips %llu).",
                static_cast<unsigned long long>(s->written.load()),
                static_cast<unsigned long long>(s->depth_skips.load()));
            fail(*s, s->depth_output.error(), message);
            finalized = false;
        }
        if (!finalized || cancelled)
            s->depth_output.discard();
    }
    if (created && (!finalized || cancelled))
        DeleteFileW(s->path.c_str());
    if (finalized && !cancelled && SUCCEEDED(s->error.load())) {
        if (s->shot.open())
            write_shot_sidecar(*s);
        write_camera_sidecar(*s);
    }
    s->state.store(FAILED(s->error.load())  ? State::failed
                   : (cancelled || !created) ? State::cancelled
                                             : State::completed,
                   std::memory_order_release);
    s->done.store(true, std::memory_order_release);
}

// Numerical-only worker. The Present callback is the sole vector writer until
// ready is closed and admitted callbacks drain. No GPU/codec/audio is initialized.
// See CameraOnlyCapture.tla; this publishes a raw trace, never a final .crav.
void finish_camera_only(std::shared_ptr<Session> s) noexcept {
    s->ready.store(true, std::memory_order_release);
    s->state.store(State::recording, std::memory_order_release);
    auto progress = s->written.load(std::memory_order_acquire);
    auto changed = GetTickCount64();
    while (!s->stopping.load(std::memory_order_acquire)) {
        std::unique_lock<std::mutex> lock(s->wait_mutex);
        s->wake.wait_for(lock, std::chrono::milliseconds(20));
        apply_deferred_stop(*s);
        const auto count = s->written.load(std::memory_order_acquire);
        if (count != progress) {
            progress = count;
            changed = GetTickCount64();
        }
        if (GetTickCount64() - changed > 10000)
            fail(*s, HRESULT_FROM_WIN32(ERROR_TIMEOUT),
                 L"Camera-only capture made no progress for 10 seconds.");
    }
    s->state.store(State::finalizing, std::memory_order_release);
    quiesce_producers(*s);
    const bool cancelled = decide_cancel(*s);
    if (!cancelled && SUCCEEDED(s->error.load())) {
        if (s->written.load() != s->max_frames || s->camera_partial ||
            s->camera_trace.size() != s->max_frames)
            fail(*s, E_FAIL, L"Camera-only capture ended before its complete frame budget.");
        else if (!write_camera_sidecar(*s))
            fail(*s, E_FAIL, L"Camera-only trace could not be published without overwrite.");
    }
    s->state.store(FAILED(s->error.load()) ? State::failed
                   : cancelled ? State::cancelled : State::completed,
                   std::memory_order_release);
    s->done.store(true, std::memory_order_release);
    s->wake.notify_all();
}
void encode(std::shared_ptr<Session> s) noexcept {
    if (s->camera_only) {
        finish_camera_only(s);
        return;
    }
    if (s->encoder == Encoder::ffmpeg) {
        encode_ffmpeg(s);
        return;
    }
    bool com_started = false, mf_started = false, created = false, finalized = false;
    MediaApi api;
    Com<IMFByteStream> file;
    Com<IMFSinkWriter> writer;
    Com<IMFSample> previous;
    DWORD stream = 0;
    std::uint64_t previous_pts = 0;
    FrameStamp previous_stamp;
    try {
        const auto deadline = GetTickCount64() + 10000;
        while (!s->configured.load(std::memory_order_acquire) &&
               !s->stopping.load(std::memory_order_acquire)) {
            std::unique_lock<std::mutex> lock(s->wait_mutex);
            s->wake.wait_for(lock, std::chrono::milliseconds(20));
            apply_deferred_stop(*s);
            if (GetTickCount64() > deadline)
                fail(*s, HRESULT_FROM_WIN32(ERROR_TIMEOUT),
                     L"Recording did not receive a game frame within 10 seconds. Restore the "
                     L"Deadlock window and retry. If it persists, export diagnostics.");
        }
        if (!s->stopping.load()) {
            HRESULT hr = api.load() ? S_OK : HRESULT_FROM_WIN32(ERROR_MOD_NOT_FOUND);
            if (SUCCEEDED(hr))
                hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
            com_started = SUCCEEDED(hr);
            if (SUCCEEDED(hr)) {
                hr = api.MFStartup(MF_VERSION, MFSTARTUP_FULL);
                mf_started = SUCCEEDED(hr);
            }
            if (SUCCEEDED(hr))
                hr = begin_writer(api, *s, file, writer, stream, created);
            if (FAILED(hr)) {
                fail(
                    *s, hr,
                    L"MP4 encoder could not start. Check the output path, Windows Media Feature Pack and H.264 encoder support.");
            } else {
                if (s->depth_enabled &&
                    !s->depth_output.begin(s->depth_directory.c_str(), s->depth_exr, false))
                    fail(
                        *s, s->depth_output.error(),
                        L"Could not create a new depth layer folder. Existing folders are never overwritten.");
                for (auto& frame : s->cpu)
                    frame.pixels.resize(std::size_t(s->width) * s->height * 4);
                if (SUCCEEDED(s->error.load())) {
                    s->state.store(State::recording, std::memory_order_release);
                    s->ready.store(true, std::memory_order_release);
                }
            }
        }
        std::uint64_t stop_deadline = 0;
        while (writer.p && SUCCEEDED(s->error.load()) && !s->cancel.load()) {
            apply_deferred_stop(*s);
            const bool stopping = s->stopping.load(std::memory_order_acquire);
            if (stopping && !stop_deadline) {
                s->state.store(State::finalizing, std::memory_order_release);
                stop_deadline = GetTickCount64() + 250;
            }
            const auto read = s->consumed.load(std::memory_order_relaxed);
            if (read != s->produced.load(std::memory_order_acquire)) {
                const HRESULT hr = consume_frame(api, *s, writer.p, stream, previous, previous_pts,
                                                 previous_stamp);
                if (FAILED(hr)) {
                    fail(*s, hr,
                         L"Video encoding failed. Check available disk space and encoder support.");
                    break;
                }
                continue;
            }
            if (stopping && (!s->gpu_pending.load(std::memory_order_acquire) ||
                             GetTickCount64() >= stop_deadline))
                break;
            std::unique_lock<std::mutex> lock(s->wait_mutex);
            s->wake.wait_for(lock, std::chrono::milliseconds(5));
        }
        quiesce_producers(*s);
        // No Present after alt-tab/shutdown is required to finish the file.
        // Pending GPU copies are counted as lost, then released at the next
        // serialized render/reset boundary, never by the encoder thread.
        s->dropped.fetch_add(s->gpu_pending.exchange(0), std::memory_order_relaxed);
        // A Present may have published one last CPU frame while the worker
        // observed the stop deadline. With the producer now quiescent, drain
        // the bounded CPU queue before finalizing instead of silently losing it.
        while (writer.p && !s->cancel.load() && SUCCEEDED(s->error.load()) &&
               s->consumed.load() != s->produced.load(std::memory_order_acquire)) {
            const HRESULT hr =
                consume_frame(api, *s, writer.p, stream, previous, previous_pts, previous_stamp);
            if (FAILED(hr))
                fail(*s, hr, L"Video encoding failed while finishing the last frames.");
        }
        if (writer.p && !s->cancel.load() && SUCCEEDED(s->error.load()) && previous.p) {
            const auto first = s->first_qpc.load(std::memory_order_acquire);
            const auto last = s->stop_qpc.load(std::memory_order_acquire);
            const auto elapsed =
                last > first ? clock_units(last - first, s->frequency, 10000000) : 0;
            // A hard cap closes admission at the last copy, so wall-time stop
            // can coincide with that sample's PTS. Give it a full nominal
            // duration rather than a sub-tick duration the MP4 muxer rounds to0.
            const auto end = final_sample_end(
                previous_pts, (s->fixed_step || s->max_frames) ? s->fps : 0, elapsed);
            HRESULT hr =
                write_sample(*s, writer.p, stream, previous.p, previous_pts, end, previous_stamp);
            if (SUCCEEDED(hr))
                hr = writer->Finalize();
            finalized = SUCCEEDED(hr);
            if (FAILED(hr))
                fail(*s, hr, L"The MP4 file could not be finalized. Check available disk space.");
        } else if (writer.p && !s->cancel.load() && SUCCEEDED(s->error.load())) {
            fail_text(*s, E_FAIL, zero_frame_message(*s));
        }
    } catch (...) {
        fail(*s, E_OUTOFMEMORY,
             L"Video recording ran out of memory or the encoder could not continue.");
    }
    quiesce_producers(*s);
    const bool cancelled = decide_cancel(*s);
    if (cancelled || FAILED(s->error.load()))
        s->dropped.fetch_add(s->produced.load() - s->consumed.load() + (previous.p ? 1 : 0));
    previous.reset();
    writer.reset();
    if (file.p)
        file->Close();
    file.reset();
    if (mf_started)
        api.MFShutdown();
    if (com_started)
        CoUninitialize();
    api.unload();
    reject_incomplete_take(*s, finalized, cancelled);
    if (s->depth_enabled) {
        if (finalized && !cancelled && !s->depth_output.finish(s->written.load())) {
            wchar_t message[256]{};
            std::swprintf(
                message, 256,
                L"Depth sequence could not be finalized with the encoded color frame count (color frames %llu, depth skips %llu).",
                static_cast<unsigned long long>(s->written.load()),
                static_cast<unsigned long long>(s->depth_skips.load()));
            fail(*s, s->depth_output.error(), message);
            finalized = false;
        }
        if (!finalized || cancelled)
            s->depth_output.discard();
    }
    // Delete only an output atomically created by this recording, never an
    // existing destination rejected by MF_OPENMODE_FAIL_IF_EXIST.
    if (created && (!finalized || cancelled))
        DeleteFileW(s->path.c_str());
    if (finalized && !cancelled && SUCCEEDED(s->error.load())) {
        if (s->shot.open())
            write_shot_sidecar(*s);
        write_camera_sidecar(*s);
    }
    s->state.store(FAILED(s->error.load())  ? State::failed
                   : (cancelled || !created) ? State::cancelled
                                             : State::completed,
                   std::memory_order_release);
    s->done.store(true, std::memory_order_release);
}

bool initialize_resources(Session& s, IDXGISwapChain* swapchain, ID3D11Device* device,
                          ID3D11Texture2D* backbuffer, const D3D11_TEXTURE2D_DESC& desc) noexcept {
    const bool rgba =
        desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM || desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
    const bool bgra =
        desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM || desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
    const auto bytes = std::uint64_t(desc.Width) * desc.Height * 4;
    // Three GPU textures, three CPU frames, one optional resolve texture and
    // two NV12 samples: a fixed bound independent of recording length.
    if ((!rgba && !bgra) || !desc.Width || !desc.Height || (desc.Width & 1) || (desc.Height & 1) ||
        desc.Width > kMaxDimension || desc.Height > kMaxDimension ||
        (s.depth_enabled ? bytes * 16 > kMemoryLimit * 2 : bytes * 8 > kMemoryLimit) ||
        desc.ArraySize != 1 || desc.MipLevels != 1) {
        fail(s, E_INVALIDARG,
             L"Recording requires an even-sized SDR BGRA/RGBA backbuffer, up to 8192 x 8192.");
        return false;
    }
    auto staging_desc = desc;
    staging_desc.SampleDesc = {1, 0};
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staging_desc.MiscFlags = 0;
    HRESULT hr = S_OK;
    for (auto& slot : gpu.slots) {
        hr = device->CreateTexture2D(&staging_desc, nullptr, slot.staging.put());
        if (FAILED(hr))
            break;
    }
    if (SUCCEEDED(hr) && desc.SampleDesc.Count > 1) {
        UINT support = 0;
        hr = device->CheckFormatSupport(desc.Format, &support);
        if (SUCCEEDED(hr) && !(support & D3D11_FORMAT_SUPPORT_MULTISAMPLE_RESOLVE))
            hr = E_NOTIMPL;
        auto resolved_desc = staging_desc;
        resolved_desc.Usage = D3D11_USAGE_DEFAULT;
        resolved_desc.CPUAccessFlags = 0;
        if (SUCCEEDED(hr))
            hr = device->CreateTexture2D(&resolved_desc, nullptr, gpu.resolved.put());
    }
    if (FAILED(hr)) {
        fail(s, hr, L"DirectX 11 could not allocate the bounded video readback buffers.");
        return false;
    }
    (void)backbuffer;
    gpu.swapchain = swapchain;
    gpu.device = device;
    gpu.description = desc;
    gpu.cadence.frequency = s.frequency;
    gpu.cadence.fps = s.fps;
    s.width = desc.Width;
    s.height = desc.Height;
    s.rgba = rgba;
    s.configured.store(true, std::memory_order_release);
    s.wake.notify_one();
    return true;
}
}

namespace {
bool absolute_path(const wchar_t* path, std::size_t length) noexcept {
    const bool drive = length > 3 && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/');
    const bool unc = length > 3 && path[0] == L'\\' && path[1] == L'\\';
    return drive || unc;
}
}
bool start(const Options& options) noexcept {
    try {
        const wchar_t* path = options.path;
        if (!path || !*path || std::wcslen(path) > 1023 ||
            (options.fps != 30 && options.fps != 60 && options.fps != 120 && options.fps != 300 &&
             options.fps != 600) ||
            options.bitrate < 1000000 || options.bitrate > 100000000 || options.max_frames > 65535)
            return false;
        // Refuse relative paths and URLs. The picker passes an absolute local
        // or UNC destination. FFmpeg lossless output uses Matroska (.mkv).
        const auto length = std::wcslen(path);
        const bool mp4 = length >= 4 && _wcsicmp(path + length - 4, L".mp4") == 0;
        const bool mkv = length >= 4 && _wcsicmp(path + length - 4, L".mkv") == 0;
        const bool mov = length >= 4 && _wcsicmp(path + length - 4, L".mov") == 0;
        const bool json = length >= 5 && _wcsicmp(path + length - 5, L".json") == 0;
        if (options.camera_only &&
            (!json || !options.fixed_step || !options.shot_only || !options.camera_track ||
             !options.max_frames || options.depth || options.depth_exr || options.white_clear ||
             options.encoder != Encoder::media_foundation || options.codec != Codec::auto_select))
            return false;
        if (!absolute_path(path, length) || length < 4 ||
            (options.camera_only ? !json : (!mp4 && !mkv && !mov)))
            return false;
        if (options.camera_only && GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES)
            return false;
        if (mov && options.encoder != Encoder::ffmpeg)
            return false;
        if (options.encoder == Encoder::ffmpeg) {
            const wchar_t* ffmpeg = options.ffmpeg;
            if (!ffmpeg || !*ffmpeg)
                return false;
            const auto ffmpeg_length = std::wcslen(ffmpeg);
            if (ffmpeg_length > 32767 || !absolute_path(ffmpeg, ffmpeg_length) || ffmpeg_length < 4)
                return false;
            if (options.codec == Codec::lossless && !mkv)
                return false;
        }
        std::lock_guard<std::mutex> lock(control_mutex);
        if (current && !current->done.load(std::memory_order_acquire))
            return false;
        if (!encoder_thread)
            encoder_thread = new std::thread;
        if (encoder_thread->joinable())
            encoder_thread->join();
        deferred_stop.store(0);
        deferred_stop_qpc.store(0);
        // Path state belongs to one take. A completed path from an earlier take
        // must not make a later free-camera take drop its repeated frames.
        gPathPlaying.store(false, std::memory_order_release);
        gPathCompleted.store(false, std::memory_order_release);
        gPathPhase.store(0, std::memory_order_relaxed);
        {
            std::lock_guard<std::mutex> camera_lock(gCameraMutex);
            gCameraValid = false;
            gCameraPhase = -1;
        }
        auto next = std::make_shared<Session>();
        next->path = path;
        next->fps = options.fps;
        next->bitrate = options.bitrate;
        next->encoder = options.encoder;
        next->codec = options.codec;
        next->quality = options.quality;
        next->preset = options.preset;
        next->fixed_step = options.fixed_step;
        next->depth_enabled = options.depth;
        next->depth_exr = options.depth_exr;
        next->shot_only = options.shot_only;
        next->white_clear = options.white_clear;
        next->camera_enabled = options.camera_track;
        next->camera_only = options.camera_only;
        next->max_frames = options.max_frames;
        if (options.camera_only)
            next->camera_trace.reserve(options.max_frames);
        if (options.ffmpeg)
            next->ffmpeg = options.ffmpeg;
        if (next->depth_enabled) {
            // The depth master is a ProRes .mov from the external encoder; the
            // built-in Media Foundation sink cannot write it.
            if (next->encoder != Encoder::ffmpeg)
                return false;
            const auto slash = next->path.find_last_of(L"\\/");
            if (slash == std::wstring::npos || slash + 1 >= next->path.size())
                return false;
            next->depth_directory = next->path.substr(0, slash + 1) + L"depth";
            next->depth_video_path = next->depth_directory + L"\\depth.mov";
        }
        LARGE_INTEGER frequency{};
        if (!QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0)
            return false;
        next->frequency = std::uint64_t(frequency.QuadPart);
        *encoder_thread = std::thread(encode, next);
        cached.depth.store(options.depth, std::memory_order_release);
        // A lock-free status() reader must see this take, never the previous
        // take's terminal state or counters.
        cached.width.store(0);
        cached.height.store(0);
        cached.fps.store(next->fps);
        cached.error_code.store(0);
        cached.written.store(0);
        cached.dropped.store(0);
        cached.duration.store(0);
        cached.first_qpc.store(0);
        cached.frequency.store(next->frequency);
        std::atomic_store(&cached_owner, std::shared_ptr<const Session>(next));
        cached.state.store(State::starting, std::memory_order_release);
        current = std::move(next);
        return true;
    } catch (...) {
        return false;
    }
}
bool start(const wchar_t* path, std::uint32_t fps, std::uint32_t bitrate) noexcept {
    Options options;
    options.path = path;
    options.fps = fps;
    options.bitrate = bitrate;
    options.encoder = Encoder::media_foundation;
    options.codec = Codec::auto_select;
    return start(options);
}
void stop(bool cancel) noexcept {
    std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
    if (lock.owns_lock()) {
        if (current && !current->done.load(std::memory_order_acquire))
            request_stop(*current, cancel);
        return;
    }
    std::uint64_t unset = 0;
    deferred_stop_qpc.compare_exchange_strong(unset, now_qpc());
    unsigned request = deferred_stop.load(std::memory_order_relaxed);
    const unsigned wanted = cancel ? 2 : 1;
    while (request < wanted && !deferred_stop.compare_exchange_weak(request, wanted)) {
    }
}
Status status() noexcept {
    std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
    Status result;
    if (!lock.owns_lock()) {
        result.state = cached.state.load(std::memory_order_acquire);
        result.width = cached.width.load();
        result.height = cached.height.load();
        result.fps = cached.fps.load();
        result.error_code = cached.error_code.load();
        result.frames_written = cached.written.load();
        result.frames_dropped = cached.dropped.load();
        result.duration_100ns = cached.duration.load();
        result.first_qpc = cached.first_qpc.load();
        result.qpc_frequency = cached.frequency.load();
        if (const auto owner = std::atomic_load(&cached_owner))
            std::wcsncpy(result.error, owner->error_text.load(std::memory_order_acquire), 255);
        return result;
    }
    if (!current)
        return result;
    const auto& s = *current;
    result.state = s.state.load(std::memory_order_acquire);
    if (s.configured.load(std::memory_order_acquire)) {
        result.width = s.width;
        result.height = s.height;
    }
    result.fps = s.fps;
    result.error_code = std::uint32_t(s.error.load());
    result.frames_written = s.written.load();
    result.frames_dropped = s.dropped.load();
    result.duration_100ns = s.duration.load();
    result.first_qpc = s.first_qpc.load();
    result.qpc_frequency = s.frequency;
    const auto* message = s.error_text.load(std::memory_order_acquire);
    std::wcsncpy(result.error, message, 255);
    cached.width.store(result.width);
    cached.height.store(result.height);
    cached.fps.store(result.fps);
    cached.error_code.store(result.error_code);
    cached.written.store(result.frames_written);
    cached.dropped.store(result.frames_dropped);
    cached.duration.store(result.duration_100ns);
    cached.first_qpc.store(result.first_qpc);
    cached.frequency.store(result.qpc_frequency);
    cached.state.store(result.state, std::memory_order_release);
    return result;
}
bool wants_depth() noexcept {
    std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
    if (lock.owns_lock()) {
        const bool wanted = current && current->depth_enabled;
        cached.depth.store(wanted, std::memory_order_release);
        return wanted;
    }
    return cached.depth.load(std::memory_order_acquire);
}
bool wants_white_clear() noexcept {
    std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
    if (lock.owns_lock()) {
        const bool wanted = current && current->white_clear;
        cached.white_clear.store(wanted, std::memory_order_release);
        return wanted;
    }
    return cached.white_clear.load(std::memory_order_acquire);
}
bool wants_shot_only() noexcept {
    std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
    if (lock.owns_lock()) {
        const bool wanted = current && current->shot_only;
        cached.shot_only.store(wanted, std::memory_order_release);
        return wanted;
    }
    return cached.shot_only.load(std::memory_order_acquire);
}
void publish_path_replay_time(bool playing, double phase, bool completed,
                              const CameraSample* camera) noexcept {
    gPathCompleted.store(completed, std::memory_order_relaxed);
    {
        std::lock_guard<std::mutex> lock(gCameraMutex);
        gCameraValid = playing && std::isfinite(phase) && camera != nullptr;
        gCameraPhase = phase;
        if (gCameraValid) {
            gCamera = *camera;
            ++gCameraSerial;
        }
    }
    if (playing && std::isfinite(phase)) {
        gPathPhase.store(phase, std::memory_order_relaxed);
        gPathPlaying.store(true, std::memory_order_release);
        return;
    }
    gPathPlaying.store(false, std::memory_order_release);
}
bool path_replay_time(double& seconds) noexcept {
    if (!gPathPlaying.load(std::memory_order_acquire))
        return false;
    const auto phase = gPathPhase.load(std::memory_order_relaxed);
    if (!std::isfinite(phase) || phase < 0)
        return false;
    seconds = phase;
    return true;
}
// Finalizing still needs Presents: the copy submitted on the last recording
// Present is read back by the next capture(), which admits no new frame once
// the take is stopping. Without it that frame is counted as lost.
bool present_should_capture(State state, bool editor_focused) noexcept {
    return editor_focused || active(state);
}
void capture(IDXGISwapChain* swapchain, ID3D11Device* device, ID3D11DeviceContext* context,
             const depth::SceneFrame* scene, double replay_time, bool native_clock) noexcept {
    gCaptureAttempts.fetch_add(1, std::memory_order_relaxed);
    if (!swapchain || !device || !context)
        return;
    std::shared_ptr<Session> session;
    {
        std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
        if (!lock.owns_lock()) {
            gCaptureNoLock.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        session = current;
    }
    if (!session || session->done.load(std::memory_order_acquire)) {
        gpu.clear();
        return;
    }
    auto& s = *session;
    const auto capture_entry = s.max_frames ? now_qpc() : 0;
    s.capture_calls.fetch_add(1, std::memory_order_relaxed);
    apply_deferred_stop(s);
    if (gpu.owner != session) {
        if (s.stopping.load(std::memory_order_acquire))
            return;
        gpu.clear();
        gpu.owner = session;
    }
    if (FAILED(s.error.load()) || s.cancel.load())
        return;
    const auto read_backbuffer = [&](Com<ID3D11Texture2D>& texture,
                                     D3D11_TEXTURE2D_DESC& texture_desc) {
        HRESULT hr = swapchain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                          reinterpret_cast<void**>(texture.put()));
        if (FAILED(hr)) {
            fail(s, hr, L"The game's DirectX 11 backbuffer became unavailable.");
            return false;
        }
        texture->GetDesc(&texture_desc);
        return true;
    };
    Com<ID3D11Texture2D> backbuffer;
    D3D11_TEXTURE2D_DESC desc{};
    HRESULT hr = S_OK;
    if (!s.camera_only && !s.configured.load(std::memory_order_acquire)) {
        // Startup configuration: the worker waits for it before creating any
        // output, so a failure here cannot disagree with kept files.
        if (s.stopping.load() || !read_backbuffer(backbuffer, desc))
            return;
        initialize_resources(s, swapchain, device, backbuffer.p, desc);
        return;
    }
    // Every later failure point runs inside the producer gate. Increment
    // before testing ready: the worker closes ready, then waits for admitted
    // callbacks before its single stop decision, so a failure is either
    // counted before that decision or never observed (the backbuffer is not
    // read once admission has closed). Both sides are a store/load handshake,
    // so the increment and ready load are seq_cst to match quiesce_producers().
    struct ProducerScope {
        Session& session;
        explicit ProducerScope(Session& value) : session(value) {
            session.producer_active.fetch_add(1, std::memory_order_seq_cst);
        }
        ~ProducerScope() { session.producer_active.fetch_sub(1, std::memory_order_release); }
    } producer(s);
    if (!s.ready.load(std::memory_order_seq_cst)) {
        if (!s.camera_only)
            s.capture_not_ready.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (s.camera_only && s.stopping.load())
        return;
    if (!read_backbuffer(backbuffer, desc))
        return;
    if (s.camera_only) {
        if (!desc.Width || !desc.Height || desc.Width > 32768 || desc.Height > 32768) {
            fail(s, E_FAIL, L"Camera-only capture received invalid output dimensions.");
            return;
        }
        if (!s.configured.load(std::memory_order_acquire)) {
            s.width = desc.Width;
            s.height = desc.Height;
            s.configured.store(true, std::memory_order_release);
        } else if (s.width != desc.Width || s.height != desc.Height) {
            fail(s, E_FAIL, L"Camera-only capture output dimensions changed.");
            return;
        }
        if (!native_clock) {
            if (!s.camera_trace.empty())
                fail(s, E_FAIL, L"Camera-only capture lost its verified native path clock.");
            return; // Prepared before playback; never manufacture a frame.
        }
        CameraSample camera{};
        std::uint64_t serial = 0;
        {
            std::lock_guard<std::mutex> lock(gCameraMutex);
            if (!gCameraValid || std::abs(gCameraPhase - replay_time) >= 1e-9) {
                fail(s, E_FAIL, L"Camera-only capture has no camera for this rendered phase.");
                return;
            }
            serial = gCameraSerial;
            camera = gCamera;
        }
        if (serial == s.camera_view_serial)
            return; // Another Present without a new verified main view.
        s.camera_view_serial = serial;
        const auto count = s.camera_trace.size();
        if (count && std::abs(replay_time - s.camera_trace.back().phase) < 1e-9) {
            if (gPathCompleted.load(std::memory_order_acquire))
                request_stop(s, false);
            return; // A paused/clamped view is not another simulation frame.
        }
        bool valid = std::isfinite(replay_time) &&
                     std::abs(replay_time - double(count) / s.fps) <= 1e-6 &&
                     std::isfinite(camera.horizontal_fov) && camera.horizontal_fov > 1 &&
                     camera.horizontal_fov < 179 && count < s.max_frames;
        for (unsigned i = 0; i < 3; ++i)
            valid = valid && std::isfinite(camera.position[i]) && std::isfinite(camera.angles[i]);
        if (!valid) {
            wchar_t detail[256]{};
            std::swprintf(detail, 256,
                L"Camera-only frame %llu: phase %.9f s, expected %.9f s, FOV %.6f. Missing or invalid fixed-step sample.",
                static_cast<unsigned long long>(count), replay_time, double(count) / s.fps,
                camera.horizontal_fov);
            fail(s, E_FAIL, detail);
            return;
        }
        const double time = double(count) / s.fps;
        s.camera_trace.push_back({count, time, replay_time, camera, time}); // Capacity reserved at start.
        if (!count)
            s.first_qpc.store(now_qpc(), std::memory_order_release);
        s.duration.store((count + 1) * 10000000ull / s.fps, std::memory_order_relaxed);
        s.written.store(count + 1, std::memory_order_release);
        if (count + 1 == s.max_frames)
            request_stop(s, false);
        s.wake.notify_one();
        return;
    }
    if (gpu.swapchain != swapchain || gpu.device != device || desc.Width != gpu.description.Width ||
        desc.Height != gpu.description.Height || desc.Format != gpu.description.Format ||
        desc.SampleDesc.Count != gpu.description.SampleDesc.Count) {
        fail(s, E_FAIL,
             L"The game resolution, swapchain or color format changed during recording.");
        return;
    }
    auto* timing = s.capture_trace.begin(s.max_frames != 0, replay_time, capture_entry,
                                         static_cast<unsigned>(gpu.submitted - gpu.drained));
    struct TimingScope {
        CaptureTrace::Sample* sample;
        ~TimingScope() {
            if (sample)
                sample->end = now_qpc();
        }
    } timing_scope{timing};
    // Map only already-submitted copies, never the copy issued this Present.
    // In real time DO_NOT_WAIT makes a busy GPU a skipped opportunity, not a
    // render stall. In fixed-step export the producer instead applies
    // backpressure: a blocking readback plus a wait for CPU queue room keeps the
    // simulation from outrunning capture, so every authored frame is encoded.
    const bool fixed_backpressure = s.fixed_step;
    for (unsigned copies = 0; copies < 1 && gpu.drained != gpu.submitted; ++copies) {
        const auto write = s.produced.load(std::memory_order_relaxed);
        if (write - s.consumed.load(std::memory_order_acquire) >= kSlots) {
            if (!fixed_backpressure)
                break;
            // Hold the simulation until the encoder frees queue space. The wait
            // is bounded (under the desktop "not responding" threshold) so a
            // wedged encoder, stop, or teardown can never pin the render thread;
            // after the budget we degrade to the real-time drop path.
            const auto deadline = GetTickCount64() + 3000;
            bool healthy = true;
            while (s.produced.load(std::memory_order_relaxed) -
                       s.consumed.load(std::memory_order_acquire) >=
                   kSlots) {
                if (s.stopping.load(std::memory_order_acquire) ||
                    !s.ready.load(std::memory_order_acquire) ||
                    FAILED(s.error.load(std::memory_order_relaxed)) ||
                    GetTickCount64() >= deadline) {
                    healthy = false;
                    break;
                }
                std::unique_lock<std::mutex> lock(s.wait_mutex);
                s.wake.wait_for(lock, std::chrono::milliseconds(2));
            }
            if (!healthy) {
                // Count a genuine timeout as a drop; stop/failure/teardown end
                // the recording instead.
                if (!s.stopping.load(std::memory_order_acquire) &&
                    s.ready.load(std::memory_order_acquire) &&
                    !FAILED(s.error.load(std::memory_order_relaxed))) {
                    fail(s, HRESULT_FROM_WIN32(ERROR_TIMEOUT),
                         L"Frame-accurate export writer exceeded the capture wait budget. "
                         L"Recording stopped without skipping the frame.");
                }
                return;
            }
            if (s.produced.load(std::memory_order_relaxed) -
                    s.consumed.load(std::memory_order_acquire) >=
                kSlots)
                break;
        }
        auto& slot = gpu.slots[gpu.drained % kSlots];
        if (s.depth_enabled && !slot.depth_ready) {
            auto result = gpu.depth_queue.poll(context, slot.depth);
            if (fixed_backpressure && result == depth::ReadbackResult::pending) {
                const auto deadline = GetTickCount64() + 3000;
                while (result == depth::ReadbackResult::pending && !s.stopping.load() &&
                       !s.cancel.load() && SUCCEEDED(s.error.load()) &&
                       GetTickCount64() < deadline) {
                    Sleep(1);
                    result = gpu.depth_queue.poll(context, slot.depth);
                }
            }
            if (result == depth::ReadbackResult::pending)
                break;
            if (result != depth::ReadbackResult::ready) {
                const auto readback_error = static_cast<HRESULT>(gpu.depth_queue.error());
                const auto error = FAILED(readback_error) ? readback_error : E_FAIL;
                wchar_t detail[192]{}, message[256]{};
                MultiByteToWideChar(CP_UTF8, 0, gpu.depth_queue.diagnostic(), -1, detail, 192);
                std::swprintf(
                    message, 256,
                    L"Depth readback failed: %ls (result=%d, HRESULT=0x%08lX). Recording stopped.",
                    detail, static_cast<int>(result), static_cast<unsigned long>(error));
                fail(s, error, message);
                return;
            }
            if (slot.depth.frame.sample != gpu.drained) {
                wchar_t message[192]{};
                std::swprintf(
                    message, 192,
                    L"Depth/color frame mismatch: expected sample %llu, received %llu. Recording stopped.",
                    static_cast<unsigned long long>(gpu.drained),
                    static_cast<unsigned long long>(slot.depth.frame.sample));
                fail(s, HRESULT_FROM_WIN32(ERROR_INVALID_DATA), message);
                return;
            }
            slot.depth_ready = true;
        }
        D3D11_MAPPED_SUBRESOURCE mapped{};
        hr = context->Map(slot.staging.p, 0, D3D11_MAP_READ,
                          fixed_backpressure ? 0 : D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (hr == DXGI_ERROR_WAS_STILL_DRAWING)
            break;
        if (FAILED(hr)) {
            fail(s, hr, L"DirectX 11 video readback failed; recording stopped.");
            return;
        }
        auto& frame = s.cpu[write % kSlots];
        const auto row_bytes = std::size_t(s.width) * 4;
        if (mapped.RowPitch < row_bytes || !mapped.pData) {
            context->Unmap(slot.staging.p, 0);
            fail(s, E_FAIL, L"DirectX 11 returned an invalid video row stride.");
            return;
        }
        for (std::uint32_t row = 0; row < s.height; ++row)
            std::memcpy(frame.pixels.data() + row * row_bytes,
                        static_cast<const std::uint8_t*>(mapped.pData) +
                            std::size_t(row) * mapped.RowPitch,
                        row_bytes);
        context->Unmap(slot.staging.p, 0);
        frame.pts = slot.pts;
        frame.stamp = slot.stamp;
        if (s.depth_enabled) {
            frame.depth = std::move(slot.depth);
            slot.depth_ready = false;
        }
        s.produced.store(write + 1, std::memory_order_release);
        s.capture_produced.fetch_add(1, std::memory_order_relaxed);
        ++gpu.drained;
        s.gpu_pending.store(gpu.submitted - gpu.drained, std::memory_order_release);
        s.wake.notify_one();
    }
    if (s.stopping.load(std::memory_order_acquire))
        return;
    if (timing)
        timing->readback = now_qpc();
    // Count admissions, not asynchronously written frames. Drain previous
    // copies above, but never enqueue color OR depth beyond the same hard cap.
    if (!frame_admission_open(s.capture_submitted.load(std::memory_order_relaxed), s.max_frames)) {
        request_stop(s, false);
        return;
    }
    // Admission applies only to new copies. Even a paused/clamped endpoint
    // must drain the copy submitted on the preceding Present before returning.
    if ((s.depth_enabled || s.shot_only) && (!std::isfinite(replay_time) || replay_time < 0)) {
        // A prepared recording starts before its shot plays. Pre-roll frames
        // have no replay time and cannot be paired with depth or aligned with
        // the other layer takes; skip them instead of failing the take. The
        // first encoded frame is the first authored one.
        s.capture_preplay.fetch_add(1, std::memory_order_relaxed);
        const auto skips = s.depth_skips.fetch_add(1, std::memory_order_relaxed) + 1;
        s.depth_last_replay.store(replay_time, std::memory_order_relaxed);
        if (skips == 1)
            s.depth_skip_since.store(GetTickCount64(), std::memory_order_relaxed);
        const auto since = s.depth_skip_since.load(std::memory_order_relaxed);
        if (since && GetTickCount64() - since > 10000 &&
            !s.written.load(std::memory_order_relaxed)) {
            wchar_t message[256]{};
            std::swprintf(
                message, 256,
                L"The shot never started; %llu pre-play frames were skipped (last replay time %.3f).",
                static_cast<unsigned long long>(skips), double(replay_time));
            fail(s, HRESULT_FROM_WIN32(ERROR_TIMEOUT), message);
        }
        return;
    }
    // A take that plays an authored path holds the camera at that path's end
    // while the recorder is still open. Those clamped frames are the same
    // authored instant, so they must be dropped for every path take, not only
    // for the paired depth and POV/bone takes: otherwise each exported video
    // ends with however many seconds of still frame the hold lasted.
    const bool path_take = gPathPlaying.load(std::memory_order_relaxed) ||
                           gPathCompleted.load(std::memory_order_relaxed);
    if (s.depth_enabled || s.shot_only || path_take) {
        // The authored path clamps at its end. Dropping repeated clamped
        // frames keeps the color, depth and layer takes the same length.
        const auto previous = s.shot_last_replay.load(std::memory_order_relaxed);
        const bool completed = gPathCompleted.load(std::memory_order_relaxed);
        // Nothing after the completed endpoint belongs to the shot. Admitting the
        // clamped frame produced exactly one extra held frame whose encoded
        // duration was the real hold -- a freeze of up to seconds at the end of
        // an otherwise correct export.
        if (completed ||
            !capture_video_phase(previous, replay_time, s.fixed_step, native_clock, completed)) {
            if (timing)
                timing->outcome = CaptureTrace::repeated_phase;
            return;
        }
        s.shot_last_replay.store(replay_time, std::memory_order_relaxed);
    }

    const auto now = now_qpc();
    std::uint64_t pts = 0, missed = 0;
    if (s.fixed_step) {
        // One captured frame per Present. The frame index, not wall-clock
        // time, defines the timestamp, so the export is deterministic.
        pts = std::uint64_t(gpu.submitted) * 10000000ull / std::max<std::uint32_t>(1, s.fps);
        std::uint64_t zero = 0;
        s.first_qpc.compare_exchange_strong(zero, now, std::memory_order_release);
    } else {
        if (!gpu.cadence.sample(now, pts, missed)) {
            if (timing)
                timing->outcome = CaptureTrace::cadence_wait;
            return;
        }
        s.first_qpc.store(gpu.cadence.start, std::memory_order_release);
    }
    if (timing)
        timing->missed = missed;
    s.dropped.fetch_add(missed, std::memory_order_relaxed);
    if (gpu.submitted - gpu.drained >= kSlots) {
        if (timing)
            timing->outcome = CaptureTrace::queue_full;
        if (s.fixed_step) {
            fail(s, HRESULT_FROM_WIN32(ERROR_TIMEOUT),
                 L"Frame-accurate export could not capture every frame. Recording stopped.");
            return;
        }
        s.capture_dropped.fetch_add(1, std::memory_order_relaxed);
        s.dropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    auto& slot = gpu.slots[gpu.submitted % kSlots];
    if (s.depth_enabled) {
        depth::ProjectionBuffer calibration[14]{};
        const auto count = scene ? scene->calibration(calibration, 14) : 0;
        const bool scene_ready = scene && scene->result == depth::SceneResult::ready && count > 0 &&
                                 scene->width && scene->height;
        const auto fail_scene = [&]() {
            wchar_t detail[560]{};
            if (MultiByteToWideChar(CP_UTF8, 0, depth::scene_diagnostic(), -1, detail, 559) <= 0)
                detail[0] = 0;
            std::wstring message =
                L"A verified scene depth and replay time were not available for this color frame. (";
            message += detail;
            message += L")";
            fail_text(s, E_FAIL, message);
        };
        if (!scene_ready) {
            // A prepared take can present transition frames first: the scene
            // target may be cleared or overwritten by an unsupported pass
            // (for example a full-viewport ALWAYS write) before the world pass
            // renders. Nothing has been captured yet, so skip the color+depth
            // pair instead of failing; a bounded budget still ends a take that
            // never produces a verified frame.
            if (gpu.submitted == 0) {
                const auto skips =
                    s.capture_scene_skips.fetch_add(1, std::memory_order_relaxed) + 1;
                if (skips == 1)
                    s.scene_skip_since.store(GetTickCount64(), std::memory_order_relaxed);
                const auto since = s.scene_skip_since.load(std::memory_order_relaxed);
                if (since && GetTickCount64() - since > 10000) {
                    wchar_t detail[560]{};
                    if (MultiByteToWideChar(CP_UTF8, 0, depth::scene_diagnostic(), -1, detail,
                                            559) <= 0)
                        detail[0] = 0;
                    wchar_t message[768]{};
                    std::swprintf(
                        message, 768,
                        L"The shot never produced a verified scene frame; %llu rendered frames were skipped. (%s)",
                        static_cast<unsigned long long>(skips), detail);
                    fail_text(s, E_FAIL, message);
                }
                return;
            }
            fail_scene();
            return;
        }
        if (!std::isfinite(replay_time) || replay_time < 0) {
            // The pre-play skip above normally prevents this; keep the guard
            // so an unpaired frame can never be captured.
            fail_scene();
            return;
        }
        // The depth data is captured at the scene target's own size. It can be
        // below the color recording when the game renders the scene at an
        // internal resolution with upscaling or resolution scaling.
        const depth::Frame metadata{scene->width, scene->height, gpu.submitted, replay_time, {}};
        if (gpu.depth_queue.enqueue(device, context, scene->texture(), metadata, calibration,
                                    count) != depth::ReadbackResult::ready) {
            fail(s, E_FAIL, L"Could not queue depth with the current color frame.");
            return;
        }
    }
    if (gpu.resolved.p) {
        context->ResolveSubresource(gpu.resolved.p, 0, backbuffer.p, 0, desc.Format);
        context->CopyResource(slot.staging.p, gpu.resolved.p);
    } else {
        context->CopyResource(slot.staging.p, backbuffer.p);
    }
    if (timing)
        timing->outcome = CaptureTrace::admitted;
    slot.stamp = {replay_time, native_clock};
    if (s.fixed_step)
        movie_audio_sample(slot.stamp.audio);
    slot.stamp.capture_time = double(pts) / 10000000.0;
    if (s.camera_enabled && native_clock) {
        // The native clock and this camera come from the same view evaluation.
        std::lock_guard<std::mutex> lock(gCameraMutex);
        slot.stamp.has_camera = gCameraValid && std::abs(gCameraPhase - replay_time) < 1e-9;
        slot.stamp.camera = gCamera;
    }
    slot.pts = pts;
    ++gpu.submitted;
    const auto admitted = s.capture_submitted.fetch_add(1, std::memory_order_relaxed) + 1;
    s.gpu_pending.store(gpu.submitted - gpu.drained, std::memory_order_release);
    // Publish the last pending copy BEFORE asking the encoder to finalize.
    // Later Presents provide completion-only drain; no extra copy is admitted.
    if (!frame_admission_open(admitted, s.max_frames))
        request_stop(s, false);
}
void reset_resources() noexcept {
    if (gpu.owner && !gpu.owner->done.load(std::memory_order_acquire) &&
        active(gpu.owner->state.load())) {
        // Resize is a normal user operation: keep any completed frames as a
        // playable MP4 instead of deleting an otherwise valid recording.
        request_stop(*gpu.owner, false);
        gpu.owner->dropped.fetch_add(gpu.owner->gpu_pending.exchange(0));
    }
    gpu.clear();
}
void shutdown() noexcept {
    std::thread worker;
    {
        std::lock_guard<std::mutex> lock(control_mutex);
        if (current && !current->done.load())
            request_stop(*current, false);
        if (encoder_thread)
            worker = std::move(*encoder_thread);
    }
    if (worker.joinable())
        worker.join();
}
}
