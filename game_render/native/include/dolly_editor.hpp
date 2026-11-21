#pragma once
#include <cstdint>
#include <array>
#include <algorithm>
#include <cmath>
#include <cstring>
#include "dolly_path.hpp"
#include "dolly_video.hpp"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
namespace dolly {
constexpr std::size_t kEditorConfigOffset = 576;
constexpr std::size_t kEditorStatusOffset = 2 * 1024 * 1024 + 2048;
constexpr std::size_t kEditorInputDiagnosticsOffset = 2 * 1024 * 1024 + 3584;
constexpr std::uint32_t kEditorAbi = 2;
constexpr std::uint32_t kEditorPlaybackStateMask = 3;
constexpr std::uint32_t kEditorPauseAfterCapture = 4;
constexpr std::size_t kEditorBindingCount = 26, kEditorEventCount = 16;
enum class EditorOwner : std::uint32_t {
    Disabled = 0,
    Flight = 1,
    Panel = 2,
    GameUI = 3,
    Console = 4,
    Unfocused = 5,
    ReShade = 6
};
enum class EditorAction : std::uint32_t {
    Capture = 0,
    Replace,
    PlayPause,
    PlayPath,
    Stop,
    PreviousView,
    NextView,
    SeekBack,
    SeekForward,
    Panel,
    GameUI,
    Flight,
    MoveForward,
    MoveBack,
    MoveLeft,
    MoveRight,
    MoveUp,
    MoveDown,
    MoveFast,
    MoveSlow,
    LookLeft,
    LookRight,
    LookUp,
    LookDown,
    RollLeft,
    RollRight,
    Console,
    SetSpeed,
    SelectView,
    SetPlaybackSpeed,
    SetPlaybackRate,
    ReShade,
    StartVideo,
    StopVideo,
    SetVideoFps,
    SetVideoBitrate,
    SetVideoEncoder,
    SetVideoFixedStep,
    SetVideoSpeed,
    DestroyRagdolls,
    SetFraming,
    SetDofEnabled,
    SetDofOverride,
    SetDofRangeNearBlurry,
    SetDofRangeNearCrisp,
    SetDofRangeFarCrisp,
    SetDofRangeFarBlurry,
    SetDofNearBlurry,
    SetDofNearCrisp,
    SetDofFarCrisp,
    SetDofFarBlurry,
    SetDofTilt,
    SetVideoDepth,
    SetVideoDepthExr,
    SetVideoLayerWorld,
    SetVideoLayerPlayers,
    SetVideoLayerEffects,
    ToggleCitadelGlow,
    ToggleHealthbars,
    NearPlayerOpacityFix,
    SetCitadelDofEnabled,
    SetCitadelDofSensorSize,
    SetCitadelDofFocusDistance,
    SetAttachTarget,
    SetAttachPoint,
    SetAttachOffsets,
    SetAttachSmoothing,
    SetAttachHide,
    AttachCycleTarget,
    AttachCyclePoint,
    AttachReset,
    AttachPreview,
    AttachSnap,
    SetAttachBone,
    SetSourceBlend,
    SeekShot,
    StepReplayTicks,
    ResetCameraPath,
    SetVideoSource,
    SetPovDuration,
    SetConfettiEnabled,
    SetConfettiSpawnHeight,
    SetConfettiDespawnOnGround,
    OpenBonePicker,
    CancelBonePicker,
    FinishBonePicker,
    StartGameFollow,
    StopGameFollow,
    SetReplayHud,
    SelectCamera,
    ViewCamera,
    DeleteCamera,
    UndoShot,
    RedoShot,
    CameraPage,
    SetCameraTime,
    SetCameraRoll,
    FramingGrid,
    TakeScreenshot,
    // Object Picker (its own feature/mode). Appended so every existing action ID
    // is unchanged; 99+ are reserved for this feature.
    ObjectPickerOpen,
    ObjectPickerCancel,
    ObjectPlace,
    ObjectSelect,
    ObjectDelete,
    ObjectTransform,
    SaveShot,
    SeekReplayTick,
    ClearCameraPath,
    FocusReplayPlayer,
    PrepareCinematic,
    SetPauseAfterCapture,
    SetCameraPosition,
    SetCameraCurveValue,
    SelectCurveCamera,
    SetCameraPositionTangents,
    PreviewCameraCurveValue,
    PreviewCameraPositionTangents,
    ClearCameraCurvePreview,
    // Live level-view reset. Appended so every existing action ID is unchanged.
    LevelView,
    // Selected authored camera seek; existing replay/shot seek IDs stay fixed.
    SeekCamera,
    SetVideoAeCamera,
    SetVideoUsdCamera,
    SetAttachVerticalAimLock,
    // RenderLock .crav camera export from the in-game Export drawer.
    ExportRenderLock,
    CancelRenderLock
};
static_assert(static_cast<std::uint32_t>(EditorAction::SeekCamera) == 119,
              "Stable selected-camera seek action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::LevelView)==118,
              "Stable level-view action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::PreviewCameraCurveValue)==115 &&
              static_cast<std::uint32_t>(EditorAction::PreviewCameraPositionTangents)==116 &&
              static_cast<std::uint32_t>(EditorAction::ClearCameraCurvePreview)==117,
              "Stable render-only curve preview action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SetCameraPositionTangents) == 114,
              "Stable XYZ arrival/departure action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetCameraCurveValue) == 112 &&
              static_cast<std::uint32_t>(EditorAction::SelectCurveCamera) == 113,
              "Stable unified curve action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SetCameraPosition) == 111,
              "Stable XYZ value-graph action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetPauseAfterCapture) == 110,
              "Stable pause-after-capture preference action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoAeCamera) == 120,
              "Stable After Effects camera export action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoUsdCamera) == 121,
              "Stable USD camera export action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetAttachVerticalAimLock) == 122,
              "Stable per-camera vertical aim lock action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::ExportRenderLock) == 123 &&
                  static_cast<std::uint32_t>(EditorAction::CancelRenderLock) == 124,
              "Stable RenderLock camera export action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::TakeScreenshot) == 98,
              "Stable screenshot action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::ObjectPickerOpen) == 99 &&
                  static_cast<std::uint32_t>(EditorAction::ObjectDelete) == 103 &&
                  static_cast<std::uint32_t>(EditorAction::ObjectTransform) == 104,
              "Stable Object Picker action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::ResetCameraPath) == 77,
              "Stable camera reset action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::ReShade) == 31, "Stable editor action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::StopVideo) == 33, "Stable media action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoSpeed) == 38,
              "Stable video action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::DestroyRagdolls) == 39,
              "Stable replay cleanup action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoDepth) == 52,
              "Stable depth master action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoDepthExr) == 53,
              "Stable depth EXR action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::ToggleCitadelGlow) == 57 &&
                  static_cast<std::uint32_t>(EditorAction::SetCitadelDofFocusDistance) == 62,
              "Stable Citadel action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SetAttachTarget) == 63 &&
                  static_cast<std::uint32_t>(EditorAction::AttachSnap) == 72,
              "Stable attach action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SeekShot) == 75,
              "Stable shot seek action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::StepReplayTicks) == 76,
              "Stable tick-step action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetConfettiDespawnOnGround) == 82,
              "Stable confetti action IDs");
#pragma pack(push, 1)
struct EditorBinding {
    std::uint16_t vk, modifiers;
};
struct EditorConfig {
    char magic[8];
    std::uint32_t sequence, abi, enabled, owner, owner_sequence, selected_camera, camera_count,
        ack_event, flags, reserved;
    double speed, sensitivity;
    EditorBinding bindings[kEditorBindingCount];
    char shot_name[96], message[128];
    double duration, playhead;
    std::int32_t replay_tick;
    std::uint32_t playback_flags; // playing, busy, pause-after-capture preference
    double playback_speed;
    std::uint32_t playback_rate;
    EditorBinding reshade_binding;
    // Export (recording) settings, published by the desktop Export tab so the
    // in-game Export page can display and change them. These reuse the previous
    // 16 reserved bytes, so the editor ABI and struct size are unchanged.
    std::uint16_t video_fps, video_bitrate_mbps;
    std::uint8_t video_encoder, video_flags; // flags bit 0: fixed-step export
    float video_speed;
    float pov_duration;
    // Camera export formats beyond the full video_flags byte. Bit 0: USD (.usda).
    std::uint8_t camera_export_flags;
    std::uint8_t scene_appearance_flags; // bit 0: glow hidden, 1: bars hidden, 2: opacity fix
};
struct EditorEvent {
    std::uint32_t sequence, action;
    double value;
    double pose[7];
    std::int32_t tick;
    std::uint32_t paused;
};
struct EditorStatus {
    char magic[8];
    std::uint32_t sequence, abi, owner, flags, selected_camera, camera_count, last_event,
        dropped_events;
    double speed;
    double applied_pose[7];
    char message[128];
    double phase;
    std::int32_t tick;
    std::uint32_t reserved;
    std::uint64_t frame_count;
    EditorEvent events[kEditorEventCount];
};
// Optional observational block. Existing editor/camera ABI and offsets stay
// unchanged; older editors ignore these bytes.
struct EditorInputDiagnostics {
    char magic[8];
    std::uint32_t sequence, abi, flags, registration_flags;
    std::uint64_t raw_mouse_packets, relative_mouse_packets, accepted_motion_packets;
    std::uint64_t legacy_mouse_moves, consumed_motion_frames, cursor_syncs, cursor_failures;
    std::uint64_t registration_target;
    std::uint32_t last_cursor_error;
    unsigned char padding[36];
};
constexpr std::size_t kEditorDofOffset = 2 * 1024 * 1024 + 3712;
struct EditorDofConfig {
    char magic[8];
    std::uint32_t sequence, abi, enabled, reserved;
    double values[11];
};
// Citadel DOF follows the native DOF block. `available` means a camera exists
// to author against; `enabled` is the authored game switch itself.
constexpr std::size_t kEditorCitadelDofOffset = kEditorDofOffset + sizeof(EditorDofConfig);
struct EditorCitadelDofConfig {
    char magic[8];
    std::uint32_t sequence, abi, available, enabled;
    double sensor_size, focus_distance;
};
// Attach-camera runtime offsets follow the Citadel DOF block. The desktop
// editor queries the live schema once per session and republishes it; the
// native attach provider refuses attach keys while this block is absent.
constexpr std::size_t kEditorAttachOffset =
    kEditorCitadelDofOffset + sizeof(EditorCitadelDofConfig);
struct EditorAttachConfig {
    char magic[8];
    std::uint32_t sequence, abi, flags, reserved;
    std::uint64_t model;
    // scene_node, owner, origin, angles, view_offset, eye_angles, child, sibling
    std::uint32_t offsets[8];
    // ABI 3 uses reserved2 for source blend milliseconds (0..10000).
    std::uint32_t handle, entity_id, target_index, point, hide, reserved2;
    // ABI 5: hide bit 0 hides the model; bit 1 enables approximate clearance.
    // ABI 6: bit 2 uses composed orientation; bit 3 locks vertical aim.
    double offset[6];
    double smoothing;
    std::uint32_t attached_keys, key_count;
    // Monotonic snap requests from the editor; the preview callback consumes
    // changes while the free camera is active.
    std::uint32_t snap_request;
    std::uint64_t bone_hash;
    char bone_name[64]; // ASCII, full-width names need not have a terminator.
};
// Native -> editor live player roster for the attach target picker. The bridge
// worker writes it with the same seqlock discipline as the status block; the
// mapping is 4 KiB larger than the editor blocks it follows.
constexpr std::size_t kEditorRosterOffset = 2 * 1024 * 1024 + 4096;
constexpr std::uint32_t kEditorRosterAbi = 1;
constexpr std::size_t kEditorRosterPlayers = 16;
struct EditorRosterEntry {
    std::uint32_t handle, entity_index;
    std::uint64_t model;
    char model_path[96];
};
struct EditorRoster {
    char magic[8];
    std::uint32_t sequence, abi, count, flags; // flags bit 0: offsets available
    EditorRosterEntry players[kEditorRosterPlayers];
};
// Native -> editor attach result: authored offsets computed by the snap action
// or edited by attached fly. Written by the bridge with the status seqlock.
constexpr std::size_t kEditorAttachResultOffset = kEditorRosterOffset + sizeof(EditorRoster);
constexpr std::uint32_t kEditorAttachResultAbi = 1;
struct EditorAttachResult {
    char magic[8];
    std::uint32_t sequence, abi, flags, reserved; // flags bit 0: valid offsets
    std::uint32_t handle, entity_index, point, reserved2;
    std::uint64_t model;
    double offset[6];
    double smoothing;
};
constexpr std::size_t kEditorBonesOffset = kEditorAttachResultOffset + sizeof(EditorAttachResult);
constexpr std::size_t kEditorBoneCount = 256;
struct EditorBones {
    char magic[8];
    std::uint32_t sequence, abi, handle, entity_index;
    std::uint64_t model;
    std::uint32_t count, total;
    char names[kEditorBoneCount][64];
};
constexpr std::size_t kEditorFollowOffset = 2 * 1024 * 1024 + 23040;
struct EditorFollowConfig {
    char magic[8];
    std::uint32_t sequence, abi, flags, reserved;
    double distance, shoulder, height;
};
constexpr std::size_t kEditorCameraListOffset = 2 * 1024 * 1024 + 23360;
constexpr std::size_t kEditorCinematicOffset = 2 * 1024 * 1024 + 60000;
struct EditorCinematicStatus {
    char magic[8];
    std::uint32_t sequence, abi, token, revision, state;
};
static_assert(sizeof(EditorCinematicStatus) == 28, "Python cinematic completion layout");
static_assert(kEditorCinematicOffset + sizeof(EditorCinematicStatus) <= 2 * 1024 * 1024 + 61440,
              "Cinematic completion fits the existing mapping");
bool editor_cinematic_status(EditorCinematicStatus& out) noexcept;
constexpr std::size_t kEditorCameraListCount = 32;
struct EditorCameraRow {
    double time, aspect, roll;
    std::uint32_t source, reserved;
};
struct EditorCameraList {
    char magic[8];
    std::uint32_t sequence, abi, revision, total, first, count, flags, reserved;
    EditorCameraRow rows[kEditorCameraListCount];
};
static_assert(sizeof(EditorCameraList) == 1064, "Python camera list layout");
static_assert(kEditorCameraListOffset + sizeof(EditorCameraList) <= 2 * 1024 * 1024 + 24576,
              "Camera list fits existing shared mapping");
static_assert(static_cast<std::uint32_t>(EditorAction::SelectCamera) == 89,
              "Stable camera list action ID");
// Optional framing-guide block after the camera list. The reserved field
// carries the bindable action that is not part of the 26-entry wire table:
// vk in the low 16 bits, modifier bits in the high 16 bits (0 = unbound).
constexpr std::size_t kEditorFramingGridOffset = 2 * 1024 * 1024 + 24424;
struct EditorFramingGridConfig {
    char magic[8];
    std::uint32_t sequence, abi, flags, reserved;
};
static_assert(sizeof(EditorFramingGridConfig) == 24, "Framing guide config layout");
static_assert(kEditorFramingGridOffset + sizeof(EditorFramingGridConfig) <=
                  2 * 1024 * 1024 + 24576,
              "Framing guide config fits the mapping");
static_assert(static_cast<std::uint32_t>(EditorAction::SetCameraTime) == 95 &&
                  static_cast<std::uint32_t>(EditorAction::SetCameraRoll) == 96,
              "Stable inline camera edit action IDs");
// Optional level-view block after the framing guide. Carries the bindable
// action (same packed vk/modifier convention) and the project's normal aspect
// ratio. The reset itself is live-only: it levels the paused camera's roll and
// restores this framing without touching a saved camera, the project or Undo.
constexpr std::size_t kEditorLevelViewOffset = 2 * 1024 * 1024 + 24448;
struct EditorLevelViewConfig {
    char magic[8];
    std::uint32_t sequence, abi, flags, reserved;
    double standard_aspect;
};
static_assert(sizeof(EditorLevelViewConfig) == 32, "Level view config layout");
static_assert(kEditorLevelViewOffset + sizeof(EditorLevelViewConfig) <=
                  2 * 1024 * 1024 + 24576,
              "Level view config fits the mapping");
static_assert(static_cast<std::uint32_t>(EditorAction::FramingGrid) == 97,
              "Stable framing guide action ID");
// Object Picker config: its own 4 KiB page appended after the legacy tail
// (2 MiB + 24576), so every existing offset is unchanged. This block owns the
// wire layout and validation, exactly like the other editor blocks; the desktop
// publishes the placed-object list and the native Object Picker window reads it.
// Proxy geometry for each item is generated by dolly_object_runtime.
constexpr std::size_t kEditorObjectOffset = 2 * 1024 * 1024 + 24576;
constexpr std::size_t kEditorObjectCount = 64;
constexpr std::uint32_t kEditorObjectAbi = 1;
#pragma pack(push, 1)
struct EditorObjectItem {
    std::uint32_t shape;      // library index 0..8
    std::uint64_t model;      // FNV token, 0 while proxy-only
    float position[3];
    float angles[3];
    float scale;
};
struct EditorObjectConfig {
    char magic[8];
    std::uint32_t sequence, abi, flags, count;
    std::int32_t selected;
    // Bindable action, like the framing guide: vk low 16, modifiers high 16.
    std::uint32_t reserved;
    double distance;
    EditorObjectItem items[kEditorObjectCount];
};
#pragma pack(pop)
static_assert(sizeof(EditorObjectItem) == 40, "Object item layout must match Python");
static_assert(sizeof(EditorObjectConfig) == 32 + 8 + 64 * 40, "Object config layout");
// Native -> editor object status, written after the config in the same page.
// Reports that the runtime accepted the list and how many objects it holds.
constexpr std::size_t kEditorObjectStatusOffset = kEditorObjectOffset + sizeof(EditorObjectConfig);
struct EditorObjectStatus {
    char magic[8];
    std::uint32_t sequence, abi, flags, active_count;
    std::uint32_t selected;
    std::uint32_t error;
};
static_assert(sizeof(EditorObjectStatus) == 32, "Object status layout");
static_assert(kEditorObjectStatusOffset + sizeof(EditorObjectStatus) <=
                  2 * 1024 * 1024 + 24576 + 4096,
              "Object status fits the appended 4 KiB page");
// Experimental prop-persistence probe. Appended after the object status in the
// same page. The editor writes a request sequence; the render thread consumes
// it to create ONE model-less client prop (or re-count). The result carries the
// entity counts before/after so a tick/seek can be tested. This is a probe, not
// a feature: it exists only to answer whether a client-created entity survives
// replay playback.
constexpr std::size_t kEditorPropProbeOffset = kEditorObjectStatusOffset + sizeof(EditorObjectStatus);
constexpr std::uint32_t kEditorPropProbeAbi = 1;
#pragma pack(push, 1)
struct EditorPropProbeConfig {
    char magic[8];                 // "DLYPRP01"
    std::uint32_t sequence, abi, request, reserved;  // request: 1 create, 2 recount
};
struct EditorPropProbeStatus {
    char magic[8];                 // "DLYPRS01"
    std::uint32_t sequence, abi, state, resolved;
    std::uint32_t before_count, after_count, entity_handle, calls;
    std::uint32_t error;
    std::uint32_t alive_after_create, alive_after_seek, entity_index;
    std::uint64_t entity_ptr;
    std::uint64_t identity_ptr;
    char entity_class[32];
    std::uint32_t reserved1[6];
};
#pragma pack(pop)
static_assert(sizeof(EditorPropProbeConfig) == 24, "Prop probe config layout");
static_assert(sizeof(EditorPropProbeStatus) == 128, "Prop probe status layout");
static_assert(kEditorPropProbeOffset + sizeof(EditorPropProbeConfig) + sizeof(EditorPropProbeStatus) <=
                  2 * 1024 * 1024 + 24576 + 4096,
              "Prop probe blocks fit the appended 4 KiB page");
inline bool valid_editor_object_config(const EditorObjectConfig& c) noexcept {
    if (std::memcmp(c.magic, "DLYOBJ01", 8) || c.abi != kEditorObjectAbi ||
        (c.flags & ~3u) || c.count > kEditorObjectCount ||
        c.selected < -1 || c.selected >= static_cast<std::int32_t>(std::max<std::uint32_t>(c.count, 1)) ||
        !std::isfinite(c.distance) || c.distance < 10 || c.distance > 20000)
        return false;
    for (std::uint32_t i = 0; i < c.count; ++i) {
        const auto& it = c.items[i];
        if (it.shape >= 9)
            return false;
        if (!std::isfinite(it.scale) || it.scale < 0.05f || it.scale > 20.0f)
            return false;
        for (float v : it.position)
            if (!std::isfinite(v) || std::abs(v) > 1.0e7f)
                return false;
        for (float v : it.angles)
            if (!std::isfinite(v) || std::abs(v) > 1.0e5f)
                return false;
    }
    return true;
}
inline bool valid_editor_camera_list(const EditorCameraList& list) noexcept {
    if (std::memcmp(list.magic, "DLYCAMS1", 8) || list.abi != 1 || !list.revision ||
        list.reserved || (list.flags & ~3u) || list.count > kEditorCameraListCount ||
        (list.first % kEditorCameraListCount && list.first !=
            (list.total > kEditorCameraListCount ? list.total - kEditorCameraListCount : 0)) || list.first > list.total ||
        (list.total && list.first == list.total) ||
        list.count != std::min<std::uint32_t>(list.total - list.first, kEditorCameraListCount))
        return false;
    for (std::uint32_t i = 0; i < list.count; ++i) {
        const auto& row = list.rows[i];
        if (!std::isfinite(row.time) || row.time < 0 || !std::isfinite(row.aspect) ||
            row.aspect < .5 || row.aspect > 4 || !std::isfinite(row.roll) || row.source > 1 ||
            row.reserved || (i && row.time <= list.rows[i - 1].time))
            return false;
    }
    return true;
}
inline bool valid_editor_camera_action(const EditorCameraList& list, EditorAction action,
                                       double value, double revision) noexcept {
    if (!std::isfinite(value) || value != std::floor(value) || revision != list.revision)
        return false;
    if (action == EditorAction::UndoShot || action == EditorAction::RedoShot)
        return value == 0 && (list.flags & (action == EditorAction::UndoShot ? 1u : 2u));
    if (action == EditorAction::SeekCamera)
        return value >= 0 && value < list.total;
    if (action == EditorAction::CameraPage)
        return value >= 0 && value < list.total &&
               (std::fmod(value, double(kEditorCameraListCount)) == 0 ||
                value == (list.total > kEditorCameraListCount ? list.total - kEditorCameraListCount : 0));
    if (action == EditorAction::SetCameraTime || action == EditorAction::SetCameraRoll)
        return value >= list.first && value < list.first + list.count;
    return (action == EditorAction::SelectCamera || action == EditorAction::ViewCamera ||
            action == EditorAction::DeleteCamera) &&
           value >= list.first && value < list.first + list.count;
}
inline bool valid_editor_position_action(const EditorCameraList& list, double value,
                                         const CameraPose& pose) noexcept {
    if (!valid_editor_camera_action(list, EditorAction::SelectCamera, value, pose[0]))
        return false;
    for (double component : pose)
        if (!std::isfinite(component)) return false;
    const auto& row = list.rows[unsigned(value) - list.first];
    return !row.source && pose[1] >= 0 && pose[1] <= 2 && pose[1] == std::floor(pose[1]) &&
           std::abs(pose[2]) <= 1e8 && pose[3] == row.time;
}
#pragma pack(pop)
static_assert(sizeof(EditorFollowConfig) == 48, "Python Follow config layout");
static_assert(kEditorFollowOffset + sizeof(EditorFollowConfig) <= 2 * 1024 * 1024 + 24576,
              "Follow config fits the mapping");
static_assert(sizeof(EditorDofConfig) == 112, "Python optional DOF config layout");
static_assert(sizeof(EditorCitadelDofConfig) == 40, "Python optional Citadel DOF config layout");
static_assert(sizeof(EditorAttachConfig) == 228, "Python optional attach config layout");
static_assert(kEditorAttachOffset + sizeof(EditorAttachConfig) <= 2 * 1024 * 1024 + 4096,
              "Attach config fits the mapping");
static_assert(sizeof(EditorRosterEntry) == 112, "Python attach roster entry layout");
static_assert(sizeof(EditorRoster) == 1816, "Python attach roster layout");
static_assert(sizeof(EditorAttachResult) == 104, "Python attach result layout");
static_assert(sizeof(EditorBones) == 16424, "Python bone picker layout");
static_assert(kEditorBonesOffset + sizeof(EditorBones) <= 2 * 1024 * 1024 + 24576,
              "Bone picker fits the mapping");
static_assert(kEditorRosterOffset + sizeof(EditorRoster) <= 2 * 1024 * 1024 + 8192,
              "Attach roster fits the enlarged mapping");
static_assert(kEditorAttachResultOffset + sizeof(EditorAttachResult) <= 2 * 1024 * 1024 + 8192,
              "Attach result fits the enlarged mapping");
static_assert(kEditorCitadelDofOffset + sizeof(EditorCitadelDofConfig) <= 2 * 1024 * 1024 + 4096,
              "Citadel DOF config fits the mapping");
static_assert(kEditorInputDiagnosticsOffset + sizeof(EditorInputDiagnostics) == kEditorDofOffset,
              "DOF config follows diagnostics");
static_assert(kEditorDofOffset + sizeof(EditorDofConfig) <= 2 * 1024 * 1024 + 4096,
              "DOF config fits the mapping");
static_assert(static_cast<std::uint32_t>(EditorAction::SetFraming) == 40 &&
                  static_cast<std::uint32_t>(EditorAction::SetDofTilt) == 51,
              "Stable edit actions");
static_assert(sizeof(EditorConfig) == 448, "Python editor configuration layout");
static_assert(offsetof(EditorConfig, playback_speed) == 416, "Python playback speed offset");
static_assert(offsetof(EditorConfig, playback_rate) == 424, "Python playback rate offset");
static_assert(offsetof(EditorConfig, reshade_binding) == 428, "Python ReShade binding offset");
static_assert(offsetof(EditorStatus, events) == 256, "Python editor events offset");
static_assert(sizeof(EditorStatus) == 1536, "Python editor status layout");
static_assert(sizeof(EditorInputDiagnostics) == 128, "Optional input diagnostic layout");
static_assert(offsetof(EditorInputDiagnostics, raw_mouse_packets) == 24, "Input counter offset");
static_assert(offsetof(EditorInputDiagnostics, last_cursor_error) == 88, "Input error offset");
static_assert(kEditorStatusOffset + sizeof(EditorStatus) == kEditorInputDiagnosticsOffset,
              "Input diagnostics follow editor status");
static_assert(kEditorInputDiagnosticsOffset + sizeof(EditorInputDiagnostics) <=
                  2 * 1024 * 1024 + 4096,
              "Input diagnostics fit the existing mapping");
// Status flags: enabled1, focused2, paused4, manual8, ready16, overlay32,
// raw input interception64. Events are a bounded acknowledged ring. Python
// acknowledges only processed events; full queues reject actions visibly.
struct EditorSnapshot {
    EditorOwner owner = EditorOwner::Disabled;
    bool enabled = false, focused = false, manual_active = false, ready = false, paused = false,
         overlay_available = false, input_available = false;
    std::uint32_t selected_camera = 0, camera_count = 0, dropped_events = 0;
    double speed = 400, sensitivity = .08, phase = 0, duration = 0, playhead = 0;
    std::int32_t tick = 0;
    bool playing = false, busy = false, pause_after_capture = false;
    std::uint8_t scene_appearance_flags = 0;
    double playback_speed = 1;
    std::uint32_t playback_rate = 60;
    std::uint32_t video_fps = 60, video_bitrate_mbps = 20, video_codec = 0;
    bool video_fixed_step = false;
    bool video_depth = false;
    bool video_pov = false;
    double pov_duration = 10;
    bool video_depth_exr = false;
    bool video_layer_world = false;
    bool video_layer_players = false;
    bool video_layer_effects = false;
    bool video_ae_camera = false;
    bool video_usd_camera = false;
    bool renderlock_exporting = false; // camera_export_flags bit 1, published by Dolly
    double video_speed = 1;
    double horizontal_fov = 0;
    std::uint32_t view_width = 0, view_height = 0;
    CameraPose pose{};
    bool dof_available = false;
    std::array<double, 11> dof{};
    bool citadel_dof_available = false, citadel_dof_enabled = false;
    double citadel_dof_sensor = 1.0, citadel_dof_focus = 200.0;
    bool confetti_enabled = false, confetti_despawn_on_ground = false;
    double confetti_spawn_height = 250.0;
    bool follow_available = false, follow_active = false, follow_pending = false,
         replay_show_hud = false;
    double follow_distance = 135, follow_shoulder = 34, follow_height = 0;
    bool attach_available = false, attach_selected = false, attach_hide = true;
    bool attach_auto_clearance = false, attach_lock_vertical_aim = false;
    bool attach_preview = false, bone_picker = false;
    std::uint32_t attach_point = 0, attach_target_index = 0, roster_count = 0;
    char attach_bone[65]{};
    double attach_offsets[6] = {}, attach_smoothing = 0, source_blend = 0;
    std::uint32_t attach_keys = 0, shot_keys = 0;
    // Object Picker: its own mode. `object_picker` is the open flag; the placed
    // list (bounded) is copied here for the right-side window.
    bool object_picker = false;
    std::uint32_t object_count = 0;
    std::int32_t object_selected = -1;
    double object_distance = 600.0;
    EditorObjectItem object_items[kEditorObjectCount]{};
    char shot_name[96]{}, message[128]{};
};
EditorSnapshot editor_snapshot() noexcept;
// Native recorder state is separate from replay playback. Pausing a replay
// leaves its recorder open until Finish recording has drained it.
video::Status editor_recording_status() noexcept;
inline bool editor_recording_locks_cameras(video::State state) noexcept {
    return state == video::State::starting || state == video::State::recording ||
           state == video::State::finalizing;
}
// Current binding for one editor action (zeroed when unset/unavailable). Used
// by the overlay to render a key-cap hint without exposing the whole config.
EditorBinding editor_binding_snapshot(EditorAction action) noexcept;
// Published attach-camera schema offsets; false while no valid block arrived.
bool editor_attach_config(EditorAttachConfig& out) noexcept;
bool editor_follow_config(EditorFollowConfig& out) noexcept;
// Published framing-guide switch; false while no valid block has arrived.
bool editor_framing_grid_enabled() noexcept;
// Live level-view request: levels the paused camera and restores the published
// normal framing. Consumed once per rendered manual frame; false when no
// request was pending or the editor does not own the live manual camera.
bool editor_apply_level_view(CameraPose& pose) noexcept;
// Published level-view config (binding and normal framing); false while no
// valid block has arrived.
bool editor_level_view_config(EditorLevelViewConfig& out) noexcept;
// Published Object Picker config; false while no valid block has arrived.
bool editor_object_config(EditorObjectConfig& out) noexcept;
bool editor_camera_list(EditorCameraList& out) noexcept;
bool editor_bones_snapshot(EditorBones& out) noexcept;
// Latest native player roster; false while no valid block arrived.
bool editor_roster_snapshot(EditorRoster& out) noexcept;
bool editor_enqueue(EditorAction action, double value = 0,
                    const CameraPose* pose_override = nullptr) noexcept;
bool editor_panel_visible() noexcept;
void editor_set_owner(EditorOwner owner) noexcept;
void editor_overlay_available(bool available) noexcept;
void editor_text_input_active(bool active) noexcept;
bool editor_install_input_hooks() noexcept;
void editor_worker_tick(unsigned char* mapping, bool connected) noexcept;
void editor_update_view(bool replay_ready, bool paused, bool manual, const CameraPose& pose,
                        double phase = 0, std::int32_t tick = 0, double horizontal_fov = 0,
                        std::uint32_t width = 0, std::uint32_t height = 0) noexcept;
bool flight_movement_active(unsigned view_flags, EditorOwner owner, bool input_owned) noexcept;
void editor_integrate_flight(CameraPose& pose, double delta_seconds) noexcept;
void editor_reset_motion() noexcept;
#ifdef _WIN32
void editor_attach_window(HWND window) noexcept;
bool editor_window_message(HWND window, UINT message, WPARAM wparam, LPARAM lparam,
                           LRESULT& result) noexcept;
#endif
}
