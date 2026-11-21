#pragma once
#include <cstddef>
#include <cstdint>

namespace dolly {
constexpr std::uint32_t kBridgeAbi = 4;
constexpr std::size_t kControlBytes = 2 * 1024 * 1024;
// The editor blocks occupy the first +4096 tail; the attach roster
// (native -> editor) follows at +4096 inside a second 4 KiB page. The Object
// Picker block occupies one appended 4 KiB page after the legacy tail. Keep
// this in step with dolly/native_bridge.py MAPPING_BYTES and
// dolly/object_wire.OBJECT_MAPPING_BYTES. Replay kill events occupy the final
// optional 32 KiB block; all prior offsets and the camera payload are unchanged.
constexpr std::size_t kMappingBytes = kControlBytes + 24576 + 4096 + 32768;
constexpr std::size_t kPayloadOffset = 1024;
constexpr std::size_t kMaxPayloadBytes = kControlBytes - kPayloadOffset;
constexpr char kControlMagic[8] = {'D', 'L', 'Y', 'C', 'A', 'M', '0', '1'};
constexpr char kStatusMagic[8] = {'D', 'L', 'Y', 'S', 'T', 'A', 'T', '1'};
enum class Mode : std::uint32_t { Release = 0, Hold = 1, Play = 2, HoldCurrent = 3, Manual = 4 };
enum class State : std::uint32_t {
    Starting = 0,
    Probe = 1,
    Armed = 2,
    Playing = 3,
    Completed = 4,
    Stopped = 5,
    Fault = 6,
    Unsupported = 7
};
constexpr std::uint32_t kFrozen = 1;
constexpr std::uint32_t kAspect = 2;
// Time a segment without replacing the game's spectator view or lens.
constexpr std::uint32_t kGamePov = 16;
// Optional per-command opt-out of the seek render relief. Absent means the
// relief is allowed, so older editors keep the safer default.
constexpr std::uint32_t kNoSeekRelief = 4;
// Particle controls occupy previously unused flag bits.
constexpr std::uint32_t kConfetti = 8;
constexpr std::uint32_t kConfettiDespawnOnGround = 32;
constexpr std::uint32_t kConfettiHeightShift = 16;
constexpr std::uint32_t kConfettiHeightMask = 0xffff;
// Particle preset id (bits 6-9) and quantised intensity (bits 10-15). Absent
// (zero) means the original confetti preset at intensity 1.0, so every older
// editor keeps its exact behaviour.
constexpr std::uint32_t kParticlePresetShift = 6;
constexpr std::uint32_t kParticlePresetMask = 0xf;
constexpr std::uint32_t kParticleIntensityShift = 10;
constexpr std::uint32_t kParticleIntensityMask = 0x3f;
constexpr std::uint32_t kParticleIntensityDefault = 20;  // 20/20 = 1.0x
constexpr std::uint32_t kConfettiControlMask =
    kConfetti | kConfettiDespawnOnGround | (kConfettiHeightMask << kConfettiHeightShift) |
    (kParticlePresetMask << kParticlePresetShift) | (kParticleIntensityMask << kParticleIntensityShift);
#pragma pack(push, 1)
struct ControlHeader {
    char magic[8];
    std::uint32_t abi;
    std::uint32_t sequence;
    std::uint32_t command;
    std::uint32_t mode;
    std::uint32_t editor_pid;
    std::uint32_t game_pid;
    std::uint64_t heartbeat;
    std::uint32_t flags;
    std::uint32_t payload_bytes;
    double start_phase;
    double speed;
    char demo_name[512];
};
struct Status {
    char magic[8];
    std::uint32_t sequence;
    std::uint32_t abi;
    std::uint32_t state;
    std::uint32_t game_pid;
    std::uint32_t ack_command;
    std::uint32_t error;
    std::uint64_t frame_count;
    double real_time;
    double engine_time;
    double phase;
    std::int32_t tick;
    std::uint32_t paused;
    double original_pose[7];
    double applied_pose[7];
    double original_fov;
    double applied_fov;
    std::uint64_t hook_calls;
    char message[256];
    char demo_name[512];
    double max_frame_interval_ms;
    double frame_interval_ms;
    std::uint32_t effect_count;
    std::uint32_t effect_error;
    std::uint64_t effect_frames;
    double effect_phase;
};
// Optional confetti diagnostics, published after the editor bone picker. The
// block reports Dolly-side control flow only, so a visually missing rain can
// be separated from an engine-side effect retirement. Older helpers leave it
// zeroed and every reader must treat that as "not published".
constexpr std::size_t kConfettiDiagnosticsOffset = kControlBytes + 22440;
constexpr std::uint32_t kConfettiDiagnosticsAbi = 1;
constexpr char kConfettiDiagnosticsMagic[8] = {'D', 'L', 'Y', 'C', 'F', 'T', '0', '1'};
struct ConfettiDiagnostics {
    char magic[8];
    std::uint32_t sequence, abi;
    std::uint32_t state, handles;
    std::uint32_t starts, start_failures;
    std::uint32_t frames, running_frames, resets;
    std::uint32_t stop_disabled, stop_reconfigured, stop_seek, stop_backward;
    std::uint32_t stop_invalid, stop_shutdown, stop_create_failed;
    std::uint32_t state_changes;
    double max_backward_delta;
};
#pragma pack(pop)
static_assert(sizeof(ConfettiDiagnostics) == 84, "Python confetti diagnostic layout");
static_assert(kConfettiDiagnosticsOffset + sizeof(ConfettiDiagnostics) <= kMappingBytes,
              "Confetti diagnostics fit the mapping");
static_assert(sizeof(ControlHeader) == 576, "Python control layout must match");
static_assert(offsetof(ControlHeader, heartbeat) == 32, "Heartbeat requires aligned atomic access");
static_assert(offsetof(Status, original_pose) == 72, "Python status layout must match");
static_assert(offsetof(Status, message) == 208, "Python message layout must match");
static_assert(sizeof(Status) == 1016, "Python status size must match");
}
