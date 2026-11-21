#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdlib>
#include <iostream>
#include <new>
#include <stdexcept>
#include "dolly_media.hpp"
#include "dolly_editor.hpp"
#include "dolly_video.hpp"
#include "dolly_reshade.hpp"
#include "dolly_sound_capture.hpp"
// Fails the next allocation once, to reach media_worker_tick's catch path
// through its real throw site (the mapping name) without a production hook.
static bool fail_next_allocation = false;
void* operator new(std::size_t size) {
    if (fail_next_allocation) {
        fail_next_allocation = false;
        throw std::bad_alloc();
    }
    if (void* memory = std::malloc(size ? size : 1))
        return memory;
    throw std::bad_alloc();
}
void operator delete(void* memory) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t) noexcept { std::free(memory); }
static ULONGLONG clock_now = 1000;
static ULONGLONG review_clock() { return clock_now; }
#define GetTickCount64 review_clock
#include "../src/dolly_media_win.cpp"
#undef GetTickCount64
static dolly::video::Status video_state;
static bool audio_active = true;
static unsigned audio_stops = 0, video_stops = 0, reshade_stops = 0;
// video::stop(false) is only a request; the real encoder finalizes later and
// video::shutdown() joins it. When set, the stub leaves an active take
// finalizing, as a deferred or still-draining encoder does.
static bool stop_leaves_finalizing = false;
namespace dolly {
namespace video {
bool start(const Options&) noexcept { return true; }
void stop(bool) noexcept {
    ++video_stops;
    const bool active = video_state.state == State::starting ||
                        video_state.state == State::recording ||
                        video_state.state == State::finalizing;
    video_state.state = stop_leaves_finalizing && active ? State::finalizing : State::completed;
}
Status status() noexcept { return video_state; }
}
EditorSnapshot editor_snapshot() noexcept { return {}; }
bool reshade_initialize_async(const wchar_t*, const wchar_t*) noexcept { return true; }
void reshade_set_enabled(bool enabled) noexcept { if (!enabled) ++reshade_stops; }
bool reshade_request_overlay(bool) noexcept { return true; }
bool reshade_overlay_open() noexcept { return false; }
bool reshade_overlay_pending() noexcept { return false; }
ReShadeStatus reshade_status() noexcept { return {}; }
bool sound_capture_start(const wchar_t*) noexcept { audio_active = true; return true; }
void sound_capture_stop() noexcept { audio_active = false; ++audio_stops; }
void sound_capture_worker_tick() noexcept {}
const char* sound_capture_error() noexcept { return ""; }
bool sound_silence_ui_voices(unsigned) noexcept { return true; }
}
static void require(bool good, const char* message) { if (!good) throw std::runtime_error(message); }
static void reset(dolly::video::State state) {
    dolly::close_mapping();
    dolly::disconnected_since.store(0);
    dolly::session_seen.store(0);
    audio_active = true;
    audio_stops = video_stops = reshade_stops = 0;
    stop_leaves_finalizing = false;
    fail_next_allocation = false;
    video_state = {};
    video_state.state = state;
    clock_now = 1000;
}
int main(int argc, char** argv) {
    try {
        const std::string selected = argc > 1 ? argv[1] : "all";
        if (selected == "all" || selected == "idle") {
        reset(dolly::video::State::idle);
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(!audio_active && audio_stops == 1, "idle source capture not stopped");
        require(video_stops == 1 && reshade_stops == 1, "other cleanup skipped");
        require(!dolly::media_session_active(), "disconnected media still active");
        }
        if (selected == "all" || selected == "grace") {
        for (auto state : {dolly::video::State::starting, dolly::video::State::recording,
                           dolly::video::State::finalizing}) {
            reset(state);
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(audio_active && !audio_stops && !video_stops && !reshade_stops,
                    "recording grace stopped media prematurely");
            clock_now = 30999;
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(audio_active && !audio_stops && !video_stops && !reshade_stops,
                    "grace boundary stopped media prematurely");
            clock_now = 31000;
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(!audio_active && audio_stops == 1 && video_stops == 1 && reshade_stops == 1,
                    "grace expiry skipped source capture cleanup");
            require(!dolly::media_session_active(), "expired session still active");
        }
        }
        if (selected == "all" || selected == "unnamed") {
        reset(dolly::video::State::idle);
        dolly::media_worker_tick(nullptr, false);
        require(!audio_active && audio_stops == 1, "unnamed shutdown changed");
        // Editor exit during a recording grace skips the rest of the grace.
        reset(dolly::video::State::recording);
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(audio_active && !audio_stops, "grace did not start");
        clock_now = 2000;
        dolly::media_worker_tick(nullptr, false);
        require(!audio_active && audio_stops == 1 && video_stops == 1 && reshade_stops == 1,
                "unnamed tick kept the recording grace");
        require(dolly::disconnected_since.load() == 0, "unnamed tick kept the grace clock");
        }
        if (selected == "all" || selected == "reconnect") {
        // A heartbeat that returns mid-grace ends that grace. The next outage
        // gets a full grace measured from its own first disconnected tick.
        reset(dolly::video::State::recording);
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(dolly::disconnected_since.load() == 1000, "first outage not stamped");
        clock_now = 20000;
        dolly::media_worker_tick(L"owned-disconnected-session", true);
        require(dolly::disconnected_since.load() == 0, "reconnect kept the old grace clock");
        require(dolly::media_session_active(), "reconnected recording not active");
        clock_now = 40000;
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(audio_active && !audio_stops && !video_stops && !reshade_stops,
                "second outage inherited the first outage's grace");
        clock_now = 69999;
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(audio_active && !audio_stops && !video_stops,
                "second outage ended before its own grace");
        clock_now = 70000;
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(!audio_active && audio_stops == 1 && video_stops == 1 && reshade_stops == 1,
                "second outage grace expiry skipped cleanup");
        }
        if (selected == "all" || selected == "midgrace") {
        // A take that ends during the grace releases the rest of it at once.
        for (auto ended : {dolly::video::State::completed, dolly::video::State::cancelled,
                           dolly::video::State::failed}) {
            reset(dolly::video::State::recording);
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            clock_now = 10000;
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(audio_active && !audio_stops, "grace ended before the take");
            video_state.state = ended;
            clock_now = 10005;
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(!audio_active && audio_stops == 1 && video_stops == 1 && reshade_stops == 1,
                    "ended take kept its disconnect grace");
            require(!dolly::media_session_active(), "ended take kept the session");
        }
        }
        if (selected == "all" || selected == "finalizing") {
        // Expiry only requests the video stop. While the encoder still drains,
        // the media session is detached and audio retired, and the next tick
        // starts a new grace instead of repeating cleanup every 5 ms.
        reset(dolly::video::State::recording);
        stop_leaves_finalizing = true;
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        clock_now = 31000;
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(video_stops == 1 && audio_stops == 1 && !audio_active,
                "expiry did not request stop");
        require(video_state.state == dolly::video::State::finalizing, "stub finalized at request");
        require(!dolly::media_session_active(), "finalizing take kept the session");
        require(dolly::disconnected_since.load() == 0, "expiry kept its grace clock");
        clock_now = 31005;
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(video_stops == 1 && audio_stops == 1, "finalizing take re-cleaned inside grace");
        require(dolly::disconnected_since.load() == 31005, "finalizing take did not restamp grace");
        video_state.state = dolly::video::State::completed;
        clock_now = 31010;
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(video_stops == 2 && audio_stops == 2, "finalized take kept the second grace");
        }
        if (selected == "all" || selected == "fault") {
        // The only throw site is the unmapped connected branch's name copy.
        // The catch retires media even while connected.
        reset(dolly::video::State::recording);
        fail_next_allocation = true;
        dolly::media_worker_tick(L"owned-disconnected-session", true);
        require(!fail_next_allocation, "connected tick did not allocate the mapping name");
        require(!audio_active && audio_stops == 1 && video_stops == 1 && reshade_stops == 1,
                "catch path skipped cleanup");
        require(!dolly::media_session_active(), "catch path kept the session");
        }
        std::cout << "PASS actual media owner: idle/3 active grace states/expiry/unnamed shutdown/"
                     "reconnect/mid-grace end/finalizing request/catch\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
