#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <cstring>
#include <cwchar>
#include <string>
#include "dolly_media.hpp"
#include "dolly_editor.hpp"
#include "dolly_video.hpp"
#include "dolly_reshade.hpp"
#include "dolly_sound_capture.hpp"

namespace dolly {
namespace {
std::atomic<ULONGLONG> session_seen{0};
// A missing controller heartbeat must never silently end an active take: the
// desktop can be busy for seconds while the game is still recording. Only an
// explicit disconnect (or a long heartbeat gap) detaches the media session.
constexpr ULONGLONG kRecordingDisconnectGraceMs = 30000;
std::atomic<ULONGLONG> disconnected_since{0};
std::atomic<bool> session_detached{true};
HANDLE mapping = nullptr;
unsigned char* memory = nullptr;
std::wstring mapping_name;
ULONGLONG next_open = 0, next_status = 0;
std::uint32_t accepted = 0, publication = 0, command_error = 0;
wchar_t command_message[384]{};
// How long voices are declined around one Dolly-issued replay pause/resume. Long
// enough to cover the game's own cue, short enough that the first sounds of a
// resumed replay are the only other thing inside it.
constexpr unsigned kUiSilenceWindowMs = 250;
bool video_active() noexcept {
    const auto state = video::status().state;
    return state == video::State::starting || state == video::State::recording ||
           state == video::State::finalizing;
}

std::uint32_t sequence(std::size_t offset) noexcept {
    return static_cast<std::uint32_t>(
        InterlockedCompareExchange(reinterpret_cast<volatile LONG*>(memory + offset), 0, 0));
}
template <std::size_t N> void copy_text(char16_t (&out)[N], const wchar_t* text) noexcept {
    static_assert(sizeof(wchar_t) == 2, "Windows UTF-16 paths");
    if (!text)
        return;
    const auto count = std::min<std::size_t>(wcsnlen(text, N), N - 1);
    std::memcpy(out, text, count * 2);
    out[count] = 0;
}
void reject(const wchar_t* message) noexcept {
    command_error = 1;
    wcsncpy(command_message, message, 383);
    command_message[383] = 0;
}
void close_mapping() noexcept {
    session_detached.store(true, std::memory_order_release);
    if (memory)
        UnmapViewOfFile(memory);
    if (mapping)
        CloseHandle(mapping);
    memory = nullptr;
    mapping = nullptr;
    mapping_name.clear();
    accepted = publication = command_error = 0;
    command_message[0] = 0;
    next_open = next_status = 0;
}
bool terminated(const char16_t* text) noexcept {
    for (unsigned i = 0; i < 1024; ++i)
        if (text[i] == 0)
            return true;
    return false;
}
void consume() noexcept {
    const auto before = sequence(8);
    if (!before || (before & 1) || before == accepted)
        return;
    MediaCommand command{};
    std::memcpy(&command, memory, sizeof(command));
    MemoryBarrier();
    if (sequence(8) != before || command.sequence != before)
        return;
    accepted = before;
    command_error = 0;
    command_message[0] = 0;
    if (std::memcmp(command.magic, "DLYMED01", 8) || command.abi != kMediaAbi ||
        (command.reserved & ~0xffff007fu) != 0 ||
        (command.command != 1 && (command.reserved >> 16) != 0) || !terminated(command.path) ||
        !terminated(command.config_path) || !terminated(command.ffmpeg_path) ||
        command.encoder > static_cast<std::uint32_t>(video::Encoder::ffmpeg) ||
        command.codec > static_cast<std::uint32_t>(video::Codec::lossless) ||
        command.quality > 63) {
        reject(L"Media protocol differs from this Dolly build.");
        return;
    }
    const auto path = reinterpret_cast<const wchar_t*>(command.path);
    const auto config = reinterpret_cast<const wchar_t*>(command.config_path);
    const auto ffmpeg = reinterpret_cast<const wchar_t*>(command.ffmpeg_path);
    switch (command.command) {
    case 1: {
        const auto editor = editor_snapshot();
        if (!editor.enabled || !editor.ready) {
            reject(L"Open a local replay and connect the native editor before recording.");
            break;
        }
        video::Options options;
        options.path = path;
        options.ffmpeg = ffmpeg;
        options.fps = command.fps;
        options.bitrate = command.bitrate;
        options.quality = command.quality;
        options.preset = command.preset;
        options.encoder = static_cast<video::Encoder>(command.encoder);
        options.codec = static_cast<video::Codec>(command.codec);
        options.fixed_step = (command.reserved & 1u) != 0;
        options.depth = (command.reserved & 2u) != 0;
        options.depth_exr = (command.reserved & 4u) != 0;
        options.shot_only = (command.reserved & 8u) != 0;
        options.white_clear = (command.reserved & 16u) != 0;
        options.camera_track = (command.reserved & 32u) != 0;
        options.camera_only = (command.reserved & 64u) != 0;
        options.max_frames = command.reserved >> 16;
        if (!video::start(options)) {
            const auto state = video::status();
            reject(state.error[0] ? state.error
                                  : L"A recording is already active or its settings are invalid.");
        }
        break;
    }
    case 2:
        video::stop(false);
        break;
    case 3:
        video::stop(true);
        break;
    case 4:
        if (!reshade_initialize_async(path, config))
            reject(L"ReShade could not load. Check the ReShade status for details.");
        break;
    case 5:
        reshade_set_enabled(false);
        break;
    case 6:
        if (!reshade_request_overlay(!(reshade_overlay_open() || reshade_overlay_pending())))
            reject(L"Load a compatible ReShade runtime before opening its menu.");
        break;
    case 7:
        if (!sound_capture_start(path)) {
            wchar_t detail[384]{};
            MultiByteToWideChar(CP_UTF8, 0, sound_capture_error(), -1, detail, 383);
            reject(detail[0] ? detail : L"Could not start source-audio capture.");
        }
        break;
    case 8:
        sound_capture_stop();
        break;
    case 9:
        // Arm the voice silence window immediately before Dolly sends its own
        // replay pause/resume command. A build without the sound hooks rejects
        // it, so the desktop can say the game's own cue will still play instead
        // of reporting a silence that never happened.
        if (!sound_silence_ui_voices(kUiSilenceWindowMs))
            reject(L"The sound hooks are unavailable; the replay pause cue will still play.");
        break;
    default:
        reject(L"Unknown native media command.");
        break;
    }
}
void publish() noexcept {
    const auto video_state = video::status();
    const auto reshade = reshade_status();
    MediaStatus status{};
    std::memcpy(status.magic, "DLYMDS01", 8);
    publication += 2;
    if (!publication)
        publication = 2;
    status.sequence = publication;
    status.abi = kMediaAbi;
    status.ack = accepted;
    status.command_error = command_error;
    status.video_state = static_cast<std::uint32_t>(video_state.state);
    status.fps = video_state.fps;
    status.width = video_state.width;
    status.height = video_state.height;
    status.frames_written = video_state.frames_written;
    status.frames_dropped = video_state.frames_dropped;
    status.duration_100ns = video_state.duration_100ns;
    status.video_first_qpc = video_state.first_qpc;
    status.qpc_frequency = video_state.qpc_frequency;
    status.video_error = video_state.error_code;
    status.reshade_state = reshade.failed
                               ? 3
                               : (reshade.loading || (reshade.enabled && !reshade.available)
                                      ? 1
                                      : (reshade.available && reshade.enabled ? 2 : 0));
    status.reshade_open = reshade.overlay_open;
    copy_text(status.video_message, video_state.error);
    wchar_t message[384]{};
    MultiByteToWideChar(CP_UTF8, 0, reshade.message, -1, message, 383);
    copy_text(status.reshade_message, message);
    copy_text(status.command_message, command_message);
    auto* slot = reinterpret_cast<volatile LONG*>(memory + kMediaStatusOffset + 8);
    InterlockedExchange(slot, static_cast<LONG>(publication - 1));
    std::memcpy(memory + kMediaStatusOffset, &status, 8);
    std::memcpy(memory + kMediaStatusOffset + 12,
                reinterpret_cast<const unsigned char*>(&status) + 12, sizeof(status) - 12);
    InterlockedExchange(slot, static_cast<LONG>(publication));
}
}

bool media_session_active() noexcept {
    // Capture is gated by the recording itself, not by the controller
    // heartbeat: a heartbeat gap while recording must not stop the take. Only
    // an explicit disconnect (or an expired grace) detaches the session.
    if (session_detached.load(std::memory_order_acquire))
        return false;
    if (video_active())
        return true;
    const auto seen = session_seen.load(std::memory_order_acquire);
    return seen && GetTickCount64() - seen < 2000;
}

void media_worker_tick(const wchar_t* session_name, bool connected) noexcept {
    try {
        const bool named = session_name && *session_name && wcsnlen(session_name, 256) != 256;
        if (!named) {
            disconnected_since.store(0, std::memory_order_release);
            session_seen.store(0, std::memory_order_release);
            video::stop(false);
            sound_capture_stop();
            reshade_set_enabled(false);
            close_mapping();
            return;
        }
        if (!connected) {
            const auto now = GetTickCount64();
            std::uint64_t unset = 0;
            disconnected_since.compare_exchange_strong(unset, now ? now : 1);
            const auto since = disconnected_since.load(std::memory_order_acquire);
            if (video_active() && since && now - since < kRecordingDisconnectGraceMs)
                return;
            disconnected_since.store(0, std::memory_order_release);
            session_seen.store(0, std::memory_order_release);
            video::stop(false);
            sound_capture_stop();
            reshade_set_enabled(false);
            close_mapping();
            return;
        }
        disconnected_since.store(0, std::memory_order_release);
        session_detached.store(false, std::memory_order_release);
        const auto now = GetTickCount64();
        session_seen.store(now, std::memory_order_release);
        if (!memory) {
            if (now < next_open)
                return;
            next_open = now + 100;
            mapping_name = std::wstring(session_name) + L".media";
            mapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, mapping_name.c_str());
            if (!mapping)
                return;
            memory = static_cast<unsigned char*>(
                MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, kMediaMappingBytes));
            if (!memory) {
                CloseHandle(mapping);
                mapping = nullptr;
                return;
            }
        }
        const auto previous = accepted;
        consume();
        sound_capture_worker_tick();
        if (previous != accepted || now >= next_status) {
            next_status = now + 50;
            publish();
        }
    } catch (...) {
        session_seen.store(0, std::memory_order_release);
        video::stop(false);
        sound_capture_stop();
        reshade_set_enabled(false);
        close_mapping();
    }
}
}
