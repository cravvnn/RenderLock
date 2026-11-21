#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include "dolly_protocol.hpp"
namespace dolly {
// Optional observational block; camera ABI 3 and all existing offsets stay fixed.
constexpr std::size_t kRendererDiagnosticsOffset = kControlBytes + 1024;
constexpr std::uint32_t kRendererDiagnosticsAbi = 3;
constexpr char kRendererDiagnosticsHash[] =
    "00ee98d8f2b87b4a8b81ce185549b863f1c9b61e40000f81f6fcfc46a8c18c37";
constexpr std::uint32_t kRendererDiagnosticsImageSize = 0x4b7000;
struct RendererDiagnosticLayout {
    const char* hash;
    std::uint32_t image_size, timestamp;
    std::uintptr_t system_pointer, system_object, queue, execution_context, buffer_table;
    std::uintptr_t table_begin, table_end;
};
inline constexpr RendererDiagnosticLayout kRendererDiagnosticLayouts[] = {
    {"386bdc4adfc8b8a0db67520b98b391f872a214e07077cc17a02f10bf94e3b2d8", 0x4d6000, 0x6aa18aa8,
     0x430010, 0x492410, 0x201c8, 0x1edd8, 0x3f79f0, 0x1e6000, 0x425a40},
    {kRendererDiagnosticsHash, kRendererDiagnosticsImageSize, 0x6abb2e1e, 0x436580, 0x498980,
     0x1288, 0xfb8, 0x3fdb40, 0x1ec000, 0x42c1e4},
    {"d0b561df9ca1f02e8e78999e7828a03cd20b95d53de0d95215993e58efe0b59c", 0x4b7000, 0x6abd8832,
     0x436580, 0x498980, 0x1288, 0xfb8, 0x3fdb40, 0x1ec000, 0x42c204},
    // Build 6739/6745 renderer (unchanged through 6745): instruction-verified
    // against the reviewed 00ee98d8 dump; only the table end moved.
    {"ca0cd37c078fd070d9b8f4708fc892971d2a35f4c637624b366395e205723889", 0x4b7000, 0x6ac0017d,
     0x436580, 0x498980, 0x1288, 0xfb8, 0x3fdb40, 0x1ec000, 0x42c204},
};
inline const RendererDiagnosticLayout*
renderer_diagnostic_layout(const char* hash, std::uint32_t image_size,
                           std::uint32_t timestamp) noexcept {
    if (!hash)
        return nullptr;
    for (const auto& candidate : kRendererDiagnosticLayouts)
        if (candidate.image_size == image_size && candidate.timestamp == timestamp &&
            std::strcmp(candidate.hash, hash) == 0)
            return &candidate;
    return nullptr;
}
enum class RendererProbeState : std::uint32_t {
    Waiting = 0,
    Supported = 1,
    Unsupported = 2,
    Unreadable = 3,
    Racing = 4
};
// Flags: header read1, header/node samples stable2, retirement frame4,
// execution frame8, head stamp16, tail stamp32, main-view sample stable64.
#pragma pack(push, 1)
struct RendererDiagnostics {
    char magic[8];
    std::uint32_t sequence, abi, state, flags;
    std::uint64_t sample, uptime_ms, main_view_frames;
    std::uint64_t present_calls, panel_frames, init_attempts, init_successes, release_calls,
        resize_calls;
    std::uint32_t pending_count, capacity, allocated_slots, head_index, tail_index;
    std::uint32_t retirement_frame, execution_frame, head_buffer_frame, tail_buffer_frame,
        image_size;
    char renderer_hash[65];
    unsigned char hash_padding[7];
    char message[192];
    // ABI 2 occupies previously reserved bytes; all older counter offsets stay fixed.
    std::uint64_t overlay_draw_frames, guide_frames, overlay_last_us, overlay_max_us;
    std::uint64_t present_last_us, present_max_us, overlay_lock_skips;
    std::uint64_t overlay_active_since_ms, present_active_since_ms, guide_lines, guide_labels;
    unsigned char padding[24];
    // ABI 3: draw-classification probe text used to design layer filters.
    char layers[512];
};
#pragma pack(pop)
static_assert(sizeof(RendererDiagnostics) == 1024, "Optional graphics diagnostic layout");
static_assert(offsetof(RendererDiagnostics, pending_count) == 96,
              "Optional graphics counter offset");
static_assert(offsetof(RendererDiagnostics, renderer_hash) == 136, "Optional graphics hash offset");
static_assert(offsetof(RendererDiagnostics, message) == 208, "Optional graphics message offset");
static_assert(offsetof(RendererDiagnostics, overlay_draw_frames) == 400,
              "Optional overlay timing offset");
static_assert(kRendererDiagnosticsOffset >= kControlBytes + sizeof(Status),
              "No overlap with camera status");
static_assert(kRendererDiagnosticsOffset + sizeof(RendererDiagnostics) <= kControlBytes + 2048,
              "No overlap with editor status");
// Both functions run only on the native control worker. Probe once after the
// module loads; the hash is calculated off the render/input threads.
void renderer_diagnostics_probe(std::uintptr_t module, const char* file_sha256) noexcept;
void renderer_diagnostics_tick(unsigned char* mapping) noexcept;
}
