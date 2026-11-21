#pragma once
#include <cstdint>

namespace dolly {
// Interface voices eligible for the pause/resume silence window. The game's
// replay pause cue does not live under one stable asset path, so the rule is the
// armed window itself: it is set only around Dolly's own pause/resume commands,
// and every voice started inside it is declined. Pure so the rule can be tested
// without the game's sound system.
inline bool ui_voice_silenced(std::uint64_t now_qpc, std::uint64_t until_qpc) noexcept {
    return until_qpc != 0 && now_qpc < until_qpc;
}
// Optional audio hooks. Caller supplies the result of the complete fingerprint
// check; otherwise all addresses require unique reviewed AOB matches.
bool sound_capture_install(void* module, bool exact) noexcept;
bool sound_capture_available() noexcept;
// Redirect the engine movie recorder's GetFramerate accessor to the current
// host_framerate while a Dolly fixed-step take is active. The recorder keeps
// writing its WAV at the requested movie fps, while the engine advances at the
// fixed-step rate, so the WAV is frame-locked to the slow-motion video.
// Immutable reviewed recorder layouts. Secondary RTTI tables are resolved
// independently; their relative order can change between builds.
struct MovieAudioLayout { std::uintptr_t table, host_cvar, recorder, audio_table, float_accessor; };
inline constexpr MovieAudioLayout movie_audio_layout(bool season2, bool build6861 = false,
                                                     bool build6864 = false) noexcept {
    if (build6864) return {0x4f68f8, 0x8b3f58, 0x5b9740, 0x4f6988, 0x3bb30};
    if (build6861) return {0x4f6950, 0x8b3f18, 0x5b9740, 0x4f6900, 0x3bb30};
    return season2 ? MovieAudioLayout{0x4f6950, 0x8b4f18, 0x5ba740, 0x4f6900, 0x3bb60}
                   : MovieAudioLayout{0x4f6890, 0x8b4dd8, 0x5ba5c0, 0x4f6920, 0x3bb60};
}
bool movie_audio_install(void* engine_module, void* sound_module = nullptr,
                         bool exact_pcm_pair = false, bool season2 = false, bool build6861 = false,
                         bool build6864 = false) noexcept;
bool movie_audio_available() noexcept;
// The movie audio-stream clock is output-frame/fps, independent of replay
// speed. PCM writes are chunked; these sample windows intentionally are not.
struct MovieAudioSample {
    std::uint64_t take = 0, first = 0, end = 0, movie_frame = 0;
    std::uint32_t rate = 0;
};
inline bool movie_audio_frame(std::uint64_t take, std::uint64_t frame, unsigned fps,
                              MovieAudioSample& sample) noexcept {
    sample = {};
    constexpr std::uint64_t rate = 44100;
    if (!take || !fps || fps > 10000 || frame >= (UINT64_MAX - fps) / rate - 1)
        return false;
    sample = {take, (frame * rate + fps / 2) / fps, ((frame + 1) * rate + fps / 2) / fps, frame,
              static_cast<unsigned>(rate)};
    return sample.end > sample.first;
}
bool movie_audio_sample(MovieAudioSample& sample) noexcept;
void sound_capture_clock(std::int32_t tick, double engine_seconds) noexcept;
bool sound_capture_start(const wchar_t* csv_path) noexcept;
// Creates <csv_path> and <csv_path>.state.csv without overwriting (Windows
// HANDLEs). On failure no file this call created is left behind, so a retry on
// the same path can succeed.
bool sound_capture_open_outputs(const wchar_t* csv_path, void*& csv, void*& state_csv) noexcept;
void sound_capture_stop() noexcept;
void sound_capture_worker_tick() noexcept;
std::uint64_t sound_capture_dropped() noexcept;
const char* sound_capture_error() noexcept;
// Silences spoken-for UI voices briefly. Deadlock's replay pause/resume ping is a
// stock interface cue with no supported off switch, so Dolly arms this window
// immediately before its own pause/resume console commands and declines to start
// eligible interface voices inside it. Nothing else is affected: the hooks are
// installed only in Dolly sessions, the window expires on its own, and no game
// setting or file is written. Returns false when the hooks are unavailable.
bool sound_silence_ui_voices(unsigned milliseconds) noexcept;
bool sound_ui_silence_armed() noexcept;
}
