#pragma once
// Live frame stream for RenderLock's Blender render engine.
//
// The controller (Blender) creates "<session>.frames", a named mapping laid out
// as below. Each Present copies the clean game frame (before Dolly's own UI) to
// a staging ring and publishes finished frames into the mapping's slots.
//
// Depth (version 2): when the controller sets `depth` and the mapping holds a
// second set of slots after the colour slots, each frame also carries the
// game's scene depth as linear camera-axis distance (float32, game units,
// +infinity for the sky), read back with the same Present as its colour.
#include <cstdint>

struct IDXGISwapChain;
struct ID3D11Device;
struct ID3D11DeviceContext;
namespace dolly::depth {
struct SceneFrame;
}

namespace dolly::stream {

constexpr char kMagic[8] = {'D', 'L', 'Y', 'F', 'R', 'M', '0', '1'};
constexpr std::uint32_t kVersion = 2;
constexpr std::uint32_t kSlots = 3;
constexpr std::uint64_t kHeaderBytes = 4096;
constexpr std::uint64_t kSlotHeaderOffset = 256;
constexpr std::uint64_t kSlotHeaderBytes = 256;
// Camera block written by the controller: seq is odd while it writes.
constexpr std::uint64_t kCameraOffset = 1024;
struct CameraBlock {
    std::uint64_t seq;
    std::uint32_t enabled;
    std::uint32_t reserved;
    double pose[8];  // x, y, z, pitch, yaw, roll, horizontal fov (deg), aspect
};

#pragma pack(push, 1)
// Written by the controller once when it creates the mapping; `enabled` and
// `max_width/height` may change at any time.
struct Header {
    char magic[8];
    std::uint32_t version;
    std::uint32_t slots;
    std::uint64_t slot_bytes;    // pixel capacity per slot
    std::uint32_t enabled;       // controller: 1 = stream frames
    std::uint32_t depth;         // controller: 1 = also stream scene depth
    std::uint64_t latest;        // native: sequence of the newest complete slot (0 = none)
    std::uint64_t presents;      // native: Presents seen while enabled
    std::uint64_t dropped;       // native: frames not published (format, size, busy)
    std::uint32_t error;         // native: last error code (0 none)
    std::uint32_t reserved1;
    char message[160];           // native: last error text
};
// One per slot at kSlotHeaderOffset + i * kSlotHeaderBytes. `seq` is 0 while the
// slot is being written and the frame's sequence when complete.
struct SlotHeader {
    std::uint64_t seq;
    std::uint32_t width, height, row_bytes, dxgi_format;
    std::uint64_t present;       // Present index the frame was copied at
    std::uint64_t view_serial;   // view-hook evaluations seen when copied
    std::int32_t tick;           // replay tick of the last evaluated view
    std::uint32_t paused;
    double phase;                // native shot phase of the last view
    double pose[7];              // applied x, y, z, pitch, yaw, roll, fov
    std::uint32_t camera_ack;    // control command acknowledged by that view
    std::uint32_t reserved;
    std::int64_t qpc;            // QueryPerformanceCounter at copy
    // Depth of the same Present, in the depth slot with the same index.
    std::uint32_t depth_width, depth_height;
    std::uint32_t depth_state;   // DepthState
    std::uint32_t depth_reserved;
    double depth_near, depth_far;  // clip range of that view (game units)
};
enum DepthState : std::uint32_t {
    depth_off = 0,          // not requested or the mapping has no depth slots
    depth_ok = 1,           // depth slot holds this frame's linear depth
    depth_missing = 2,      // no verified scene depth for this Present
    depth_failed = 3,       // readback or conversion failed
};
#pragma pack(pop)
static_assert(sizeof(Header) <= kSlotHeaderOffset, "stream header too large");
static_assert(sizeof(SlotHeader) <= kSlotHeaderBytes, "stream slot header too large");

// Bridge worker: open/close the mapping by session name.
void worker_tick(const wchar_t* session_name, bool connected) noexcept;
// View hook: remember the camera/time the next frames show.
void note_view(std::int32_t tick, bool paused, double phase, const double pose[6], double fov,
               std::uint32_t camera_ack) noexcept;
// Present (render mutex held): copy and publish the clean frame.
// `scene` is this Present's verified scene depth sample, if it was observed.
void capture(IDXGISwapChain* chain, ID3D11Device* device, ID3D11DeviceContext* context,
             const depth::SceneFrame* scene) noexcept;
// The controller asked for depth with the frames (render thread arms the tracker).
bool wants_depth() noexcept;
// Latest controller camera, if it streams one (manual-flight view only).
bool camera(double out[8]) noexcept;
// Mapping open and streaming requested.
bool active() noexcept;
// Swapchain resize or device loss: drop GPU resources.
void reset_resources() noexcept;

}  // namespace dolly::stream
