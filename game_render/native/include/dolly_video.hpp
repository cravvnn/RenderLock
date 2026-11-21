#pragma once
#include <cstdint>

struct IDXGISwapChain;
struct ID3D11Device;
struct ID3D11DeviceContext;
namespace dolly::depth {
struct SceneFrame;
}

namespace dolly::video {
enum class State : std::uint32_t {
    idle = 0,
    starting = 1,
    recording = 2,
    finalizing = 3,
    completed = 4,
    cancelled = 5,
    failed = 6
};
// Media Foundation is the no-dependency fallback. FFmpeg is an external
// encoder (spawned child process) used when the caller supplies its path.
enum class Encoder : std::uint32_t { media_foundation = 0, ffmpeg = 1 };
// Codec identifiers are part of the Python<->native wire protocol; keep the
// numeric values stable. auto_select resolves to hardware H.264 when possible.
enum class Codec : std::uint32_t {
    auto_select = 0,
    h264_nvenc = 1,
    hevc_nvenc = 2,
    h264_mf = 3,
    libx264 = 4,
    libx265 = 5,
    h264_qsv = 6,
    hevc_qsv = 7,
    h264_amf = 8,
    hevc_amf = 9,
    lossless = 10
};
struct Options {
    const wchar_t* path = nullptr;   // absolute .mp4/.mkv destination
    const wchar_t* ffmpeg = nullptr; // absolute ffmpeg.exe when encoder==ffmpeg
    std::uint32_t fps = 60;
    std::uint32_t bitrate = 20000000;
    // Rate-control quality (CRF/CQ/QP). 0 selects the codec's default.
    std::uint32_t quality = 0;
    // Encoder preset index (see preset table); 0 selects the default.
    std::uint32_t preset = 0;
    Encoder encoder = Encoder::media_foundation;
    Codec codec = Codec::auto_select;
    // Fixed-step export: capture exactly one frame per rendered Present and
    // timestamp it at the requested rate, instead of sampling wall-clock slots.
    // The host is expected to run the engine at a fixed frame rate (set by the
    // caller) so each captured frame is a distinct simulation step.
    bool fixed_step = false;
    // Paired numerical depth master. Off by default. The caller must supply a
    // verified scene sample and exact replay time. The master is a ProRes 4444
    // depth.mov inside the recording's depth layer folder; depth_exr adds the
    // float EXR sequence beside it under exr/.
    bool depth = false;
    // Also write the float EXR precision master (exr/NNNNNNNN.exr). Off by
    // default: the depth.mov is the deliverable, the EXRs are for VFX.
    bool depth_exr = false;
    // Capture only frames that carry a replay time. Layered recordings use
    // this so the color and every layer video contain exactly the authored
    // shot range and stay aligned frame for frame. The depth master already
    // skips frames without a replay time.
    bool shot_only = false;
    // Matte pass: force every color clear to white so the recorded layer can
    // be combined with its black pass into an alpha channel.
    bool white_clear = false;
    // Optional per-encoded-frame camera metadata; no collection when off.
    bool camera_track = false;
    // Dedicated numerical capture: no pixel copies, codec, movie or audio.
    // Requires fixed_step, shot_only, camera_track and a nonzero frame budget.
    // path is the actual .json camera trace destination, created without overwrite.
    bool camera_only = false;
    // Hard cap on admitted GPU copies, including paired depth (0 unlimited).
    // Reaching it closes admission and finalizes through the normal drain.
    std::uint32_t max_frames = 0;
};
struct Status {
    State state = State::idle;
    std::uint32_t width = 0, height = 0, fps = 0, error_code = 0;
    std::uint64_t frames_written = 0, frames_dropped = 0, duration_100ns = 0;
    std::uint64_t first_qpc = 0, qpc_frequency = 0;
    wchar_t error[256]{};
};
// Starts a video-only, real-time recording. The encoder initializes
// asynchronously after the next Present supplies dimensions. 30/60/120/300/600 FPS,
// current even-sized SDR game resolution, up to 8192 x 8192. Existing
// destinations are refused. Calls return promptly; disk/codec failures are
// reported through status().
bool start(const Options& options) noexcept;
// Media Foundation convenience overload retained for the native smoke test.
bool start(const wchar_t* path, std::uint32_t fps, std::uint32_t bitrate) noexcept;
void stop(bool cancel = false) noexcept;
Status status() noexcept;
// Whether the active recording requested the paired depth master. Used by the
// overlay to decide when live scene observation is armed.
bool wants_depth() noexcept;
// Whether the active recording requested the white matte background. The
// overlay arms the clear override for those frames only.
bool wants_white_clear() noexcept;
// Whether the active recording captures only authored shot frames. Layer
// takes learn the matte scene target during their black passes so a later
// sparse pass (particles-only effects) can reuse it.
bool wants_shot_only() noexcept;

// Call once for the game swapchain before Dolly's UI/guide rendering, under
// the same serialization as ResizeBuffers/reset_resources. Real-time capture
// does not wait for GPU/encoder readiness; fixed-step uses bounded backpressure.
// The render callback never writes an output file.
void capture(IDXGISwapChain* swapchain, ID3D11Device* device, ID3D11DeviceContext* context,
             const depth::SceneFrame* scene = nullptr, double replay_time = -1,
             bool native_clock = false) noexcept;
// Whether the overlay's clean-frame Present should call capture() for a
// recorder in `state`; the focused editor always feeds it.
bool present_should_capture(State state, bool editor_focused) noexcept;
// Rendered camera: Position, pitch/yaw/roll in degrees and horizontal FOV
// Recorded per encoded frame into <video>.camera-track.json.
struct CameraSample {
    double position[3]{}, angles[3]{};
    double horizontal_fov = 0;
};
// Latest authored-path replay time from the native view callback. Returns false
// when no native path evaluated a playing frame on the current view. Layer
// takes use it so every take starts at the same authored frame.
bool path_replay_time(double& seconds) noexcept;
// Bridge-side publication of that clock (called from the native view hook),
// with the camera the hook applied for the same phase when one is known.
void publish_path_replay_time(bool playing, double phase, bool completed = false,
                              const CameraSample* camera = nullptr) noexcept;
// Call at a serialized render/resize boundary before releasing the device.
// Stops an active recording and releases only recorder-owned GPU resources.
void reset_resources() noexcept;
// Off-render-thread only; finishes/cancels the worker before DLL shutdown.
// GPU resources are separately released by reset_resources().
void shutdown() noexcept;
}
