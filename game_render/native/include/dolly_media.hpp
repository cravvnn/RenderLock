#pragma once
#include <cstddef>
#include <cstdint>

namespace dolly {
constexpr std::uint32_t kMediaAbi = 3;
constexpr std::size_t kMediaMappingBytes = 12288, kMediaStatusOffset = 8192;
#pragma pack(push, 1)
struct MediaCommand {
    char magic[8];
    // reserved: flags in bits0..5, hard video frame limit in bits16..31 (0
    // unlimited). Bit5 requests camera metadata; bit6 selects camera-only
    // capture (no video). Bits7..15 remain invalid; old consumers reject bit6.
    // encoder/codec/quality/preset select the FFmpeg backend/rate control.
    std::uint32_t sequence, abi, command, fps, bitrate, reserved;
    std::uint32_t encoder, codec, quality, preset;
    char16_t path[1024], config_path[1024], ffmpeg_path[1024];
};
struct MediaStatus {
    char magic[8];
    std::uint32_t sequence, abi, ack, command_error, video_state, fps, width, height, reshade_state,
        reshade_open;
    std::uint64_t frames_written, frames_dropped, duration_100ns;
    std::uint32_t video_error, reshade_error;
    char16_t video_message[384], reshade_message[384], command_message[384];
    std::uint64_t video_first_qpc, qpc_frequency;
};
#pragma pack(pop)
static_assert(sizeof(MediaCommand) == 6192, "Python media command layout");
static_assert(sizeof(MediaStatus) == 2400, "Python media status layout");
static_assert(offsetof(MediaStatus, frames_written) == 48, "Python media counter offset");
static_assert(kMediaStatusOffset + sizeof(MediaStatus) <= kMediaMappingBytes,
              "Media status fits mapping");
// Optional transport derived from the verified, private native session name.
// Only the existing native worker calls this. No rendering thread waits for it.
// Independent of manual editor ownership; expires with the verified session heartbeat.
bool media_session_active() noexcept;
void media_worker_tick(const wchar_t* session_name, bool connected) noexcept;
}
