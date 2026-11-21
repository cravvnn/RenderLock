#pragma once
#include "dolly_capture_timing.hpp"
#include "dolly_player_producers.hpp"
#include <atomic>
#include <string>

namespace dolly::player_capture {
// Completed endpoints are not video images. A distinct internal negative
// sentinel preserves completion provenance in the same atomic phase sample.
constexpr double CompletedCapturePhase = -2.0;
inline double capture_path_phase(double phase, bool completed) noexcept {
    if (!std::isfinite(phase) || phase < 0)
        return -1.0;
    return completed ? CompletedCapturePhase : phase;
}
struct CapturePendingImage {
    CaptureImageClock clock;
    std::uint64_t generation = 0;
    std::uint64_t epoch = 0;
    unsigned draws = 0;
    bool prepared = false, sealed = false, unsupported = false;
    // A validated scene pass can contain no selected players. Its cleared
    // target is a real transparent image, not a missing render callback.
    bool scene_observed = false;
};
// A completed GPU readback alone does not retire a draw: redirect still uses
// its event records, and a failed proof must remain visible to frame rejection.
inline bool capture_snapshot_reusable(bool pending, bool verified, bool redirected,
                                      bool mismatch) noexcept {
    return !pending && verified && redirected && !mismatch;
}
enum class CaptureSeal { skip, seal, reject };
enum class CaptureSlotWait { ready, wait, stopped, timeout };
constexpr std::uint64_t CaptureStallTimeoutMs = 30000;
// Progress, not wall-clock capture duration, bounds a slow fixed-step export.
// The caller supplies actual retired GPU work and completed disk chunks.
struct CaptureProgressWatchdog {
    std::uint64_t token = 0, changed_at = 0;
    void reset(std::uint64_t now, std::uint64_t progress) noexcept {
        token = progress;
        changed_at = now;
    }
    void observe(std::uint64_t now, std::uint64_t progress) noexcept {
        if (progress != token)
            reset(now, progress);
    }
    std::uint64_t idle(std::uint64_t now) const noexcept {
        return now >= changed_at ? now - changed_at : CaptureStallTimeoutMs;
    }
};
inline CaptureSlotWait capture_slot_wait(bool busy, bool draining, std::uint64_t elapsed_ms) noexcept {
    if (draining)
        return CaptureSlotWait::stopped;
    if (!busy)
        return CaptureSlotWait::ready;
    return elapsed_ms < CaptureStallTimeoutMs ? CaptureSlotWait::wait : CaptureSlotWait::timeout;
}
inline bool capture_seal_permission(bool shot_aligned, CaptureSeal action) noexcept {
    // Stop may start after validation. Permission belongs to the validated
    // image, rather than to the earlier snapshot of the draining flag.
    return shot_aligned && action == CaptureSeal::seal;
}

inline bool capture_draw_phase(double previous, double phase) noexcept {
    return capture_phase_advances(previous, phase);
}
inline CaptureImageAdmission capture_present_phase(double previous, double phase,
                                                    bool image, bool draining) noexcept {
    // Stop may have already held or released the native camera. Only the
    // image pinned before that transition can complete; its provenance is
    // checked separately by capture_seal_image, never by a newer live clock.
    if (draining || phase == CompletedCapturePhase)
        return CaptureImageAdmission::skip;
    return capture_image_admission(previous, phase, image);
}
inline bool capture_event_allowed(unsigned kind, bool draining, bool completion_seal = false) noexcept {
    return !draining || kind == 9 || kind == 10 || kind == 11 || kind == 12 ||
           (kind == 14 && completion_seal);
}
// The same admission transaction owns callbacks and any image waiting for
// Present. A stop cannot race preparation between a separate pending read and
// the close CAS. Forced closure still waits all admitted callbacks before
// abandoning an image when Present never arrives.
class CaptureDrainOwnership {
    static constexpr std::uint64_t Closed = std::uint64_t(1) << 63;
    static constexpr std::uint64_t Draining = std::uint64_t(1) << 62;
    static constexpr std::uint64_t Prepared = std::uint64_t(1) << 32;
    static constexpr std::uint64_t WriterMask = Prepared - 1;
    static constexpr std::uint64_t PreparedMask = (Draining - 1) & ~WriterMask;
    std::atomic<std::uint64_t> state{Closed};
public:
    void open() noexcept { state.store(0, std::memory_order_release); }
    bool enter_event(unsigned kind, bool completion_seal = false) noexcept {
        auto value = state.load(std::memory_order_acquire);
        do {
            if ((value & Closed) || !capture_event_allowed(kind, (value & Draining) != 0, completion_seal))
                return false;
        } while (!state.compare_exchange_weak(value, value + 1, std::memory_order_acq_rel));
        return true;
    }
    void leave_event() noexcept { state.fetch_sub(1, std::memory_order_acq_rel); }
    // Called within admitted render transactions. Sealing starts a readback;
    // ownership lasts until validation, write submission and slot recycling.
    void retain_prepared() noexcept { state.fetch_add(Prepared, std::memory_order_acq_rel); }
    void release_prepared() noexcept { state.fetch_sub(Prepared, std::memory_order_acq_rel); }
    void image_sealed() noexcept {}
    void image_retired() noexcept { release_prepared(); }
    bool prepared_pending() const noexcept {
        return (state.load(std::memory_order_acquire) & PreparedMask) != 0;
    }
    bool draining() const noexcept {
        return (state.load(std::memory_order_acquire) & Draining) != 0;
    }
    void begin_drain() noexcept { state.fetch_or(Draining, std::memory_order_acq_rel); }
    bool try_close(bool deadline) noexcept {
        if (deadline) {
            state.fetch_or(Closed, std::memory_order_acq_rel);
            return true;
        }
        auto expected = Draining;
        return state.compare_exchange_strong(expected, Closed, std::memory_order_acq_rel);
    }
    bool finish_close() noexcept {
        auto value = state.load(std::memory_order_acquire);
        if (!(value & Closed) || (value & WriterMask))
            return false;
        return state.compare_exchange_strong(value, Closed, std::memory_order_acq_rel);
    }
    bool closed() const noexcept { return state.load(std::memory_order_acquire) == Closed; }
};
class CaptureCallbackPin {
    CaptureDrainOwnership& owner;
    bool admitted;
public:
    CaptureCallbackPin(CaptureDrainOwnership& value, unsigned kind) noexcept
        : owner(value), admitted(owner.enter_event(kind)) {}
    CaptureCallbackPin(const CaptureCallbackPin&) = delete;
    CaptureCallbackPin& operator=(const CaptureCallbackPin&) = delete;
    ~CaptureCallbackPin() { if (admitted) owner.leave_event(); }
    explicit operator bool() const noexcept { return admitted; }
};
// Producer-hook routing outside an armed capture. Hiding remembers owners
// for the draw hook; a sequence request owns the producer table instead.
class ProducerCaptureState {
    std::atomic<bool> live_{false}, sequence_{false};
public:
    void start(bool sequence) noexcept {
        sequence_.store(sequence, std::memory_order_relaxed);
        live_.store(true, std::memory_order_release);
    }
    void finish() noexcept { live_.store(false, std::memory_order_release); }
    bool live() const noexcept { return live_.load(std::memory_order_acquire); }
    bool hides(bool hiding, bool capture_active) const noexcept {
        // Mode flags outlive a take; only a live sequence request blocks hiding.
        return hiding && !capture_active && !(live() && sequence_.load(std::memory_order_relaxed));
    }
    // Whether a producer call may touch the producer table or event ring.
    bool records(bool capture_active, bool armed) const noexcept {
        return capture_active || armed || live();
    }
};
// The raw event dump is a diagnostic. A sequence take's evidence is its
// frame metadata beside the streamed frames.
constexpr std::uint64_t CaptureSequenceEventDumpLimit = 256;
inline std::uint64_t capture_event_dump_count(std::uint64_t used, std::uint64_t capacity,
                                              bool sequence) noexcept {
    const auto limit = sequence && capacity > CaptureSequenceEventDumpLimit
        ? CaptureSequenceEventDumpLimit : capacity;
    return used < limit ? used : limit;
}
inline bool capture_terminal_saved(bool sequence, bool frame_meta_saved,
                                   bool diagnostic_saved) noexcept {
    return frame_meta_saved && (sequence || diagnostic_saved);
}
// A ring slot that cannot start its readback or be reused never frees itself;
// waiting on it only ends in the progress watchdog.
enum class CaptureSlotStep { seal, recycle };
inline const char* capture_slot_failure(CaptureSlotStep step, bool ok) noexcept {
    if (ok)
        return nullptr;
    return step == CaptureSlotStep::seal ? "GPU readback could not start for a sealed frame"
                                         : "GPU frame image could not be reused";
}
// Whole-image coverage counts and their log line run on the render thread.
constexpr unsigned CaptureFrameAuditFrames = 4;
inline bool capture_frame_audited(unsigned frame) noexcept {
    return frame < CaptureFrameAuditFrames;
}
inline bool capture_drain_finished(bool drained, std::uint64_t elapsed_ms, bool sequence) noexcept {
    return drained || elapsed_ms >= (sequence ? 3000u : 250u);
}
inline CaptureSeal capture_seal_image(const CapturePendingImage& image,
                                      std::uint64_t generation, std::uint64_t epoch,
                                      double previous, double phase, bool draining) noexcept {
    if (!image.prepared || image.sealed)
        return CaptureSeal::skip;
    if (!generation || image.generation != generation || image.epoch != epoch ||
        image.clock.epoch != epoch || image.unsupported ||
        (!image.draws && !image.scene_observed) ||
        !capture_phase_advances(previous, image.clock.phase))
        return CaptureSeal::reject;
    return draining || phase == CompletedCapturePhase || image.clock.matches(epoch, phase)
        ? CaptureSeal::seal : CaptureSeal::reject;
}
// Exact v2/v3 completion contract, shared with the native terminal consumer.
// A drained short take is cancellation evidence, never successful media.
inline bool capture_sequence_complete(bool writer_drained, bool stalled, unsigned written,
    unsigned dropped, unsigned sealed, unsigned cap, unsigned refused, bool writer_error,
    bool shot_aligned, bool cadence_lost) noexcept {
    return writer_drained && !stalled && written + dropped == sealed && written >= 2 &&
           refused == 0 && !writer_error &&
           (!shot_aligned || (!cadence_lost && dropped == 0 && written == cap));
}
// The request id a start marker carries ("... request <32 hex> ..."), or empty.
// A marker refused before it is parsed still answers the take that wrote it.
inline std::string capture_marker_request(const std::string& text) {
    constexpr char key[] = " request ";
    const auto at = text.find(key);
    if (at == std::string::npos)
        return {};
    const auto first = at + sizeof(key) - 1;
    const auto last = text.find_first_not_of("0123456789abcdef", first);
    const auto size = (last == std::string::npos ? text.size() : last) - first;
    if (size != 32 || (last != std::string::npos && text[last] != ' '))
        return {};
    return text.substr(first, 32);
}
} // namespace dolly::player_capture
