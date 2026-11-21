#include "dolly_player_capture_lifecycle.hpp"
#include <cstdio>
#include <limits>
#include <string>

using namespace dolly;
using namespace dolly::player_capture;
static unsigned failures = 0;
static void require(bool value, const char* label) {
    if (!value) {
        ++failures;
        std::printf("FAIL %s\n", label);
    }
}
static CapturePendingImage image() {
    CapturePendingImage result;
    result.clock.accept(12, .2);
    result.generation = 7;
    result.epoch = 12;
    result.draws = 6;
    result.prepared = true;
    return result;
}
static bool complete(unsigned written = 24, unsigned dropped = 0, unsigned sealed = 24,
                     unsigned cap = 24, bool snapshots_bad = false, bool writer_drained = true,
                     unsigned refused = 0, bool writer_error = false, bool cadence_lost = false) {
    return capture_sequence_complete(writer_drained, snapshots_bad, written, dropped, sealed,
                                     cap, refused, writer_error, true, cadence_lost);
}
int main() {
    for (unsigned mask = 0; mask < 16; ++mask) {
        const bool pending = mask & 1, verified = mask & 2;
        const bool redirected = mask & 4, mismatch = mask & 8;
        require(capture_snapshot_reusable(pending, verified, redirected, mismatch) == (mask == 6),
                "proof retirement requires successful GPU and redirect completion, no pending work");
    }

    require(capture_slot_wait(true, false, 0) == CaptureSlotWait::wait,
            "full capture ring holds Present rather than losing the next shot phase");
    require(capture_slot_wait(true, false, 29999) == CaptureSlotWait::wait,
            "readback retains its bounded completion window");
    require(capture_slot_wait(false, false, 30000) == CaptureSlotWait::ready,
            "retired slot may be reused at the deadline");
    require(capture_slot_wait(true, false, 30000) == CaptureSlotWait::timeout,
            "stalled GPU or writer cannot wait without a bound");
    require(capture_slot_wait(true, true, 0) == CaptureSlotWait::stopped,
            "Stop interrupts capture-slot waiting");
    CaptureProgressWatchdog pacing;
    pacing.reset(100, 0);
    for (unsigned n = 1; n <= 8; ++n) {
        const auto now = 100 + n * 20000;
        require(capture_slot_wait(true, false, pacing.idle(now)) == CaptureSlotWait::wait,
                "slow output remains paced before each real progress event");
        pacing.observe(now, n);
    }
    require(pacing.idle(190099) == 29999, "total elapsed time is not a slow-export deadline");
    require(capture_slot_wait(true, false, pacing.idle(190100)) == CaptureSlotWait::timeout,
            "heartbeats without real GPU/disk progress cannot extend a stalled capture");
    const auto owned = image();
    // Actual production consumers share these decisions: tick must not close
    // this prepared image, and Present must be able to issue its one seal after
    // clock deactivation, without accepting another draw or relabeling pixels.
    CaptureDrainOwnership owner;
    owner.open();
    require(owner.enter_event(10), "redirect transaction admitted");
    owner.retain_prepared();
    owner.leave_event();
    owner.begin_drain();
    require(owner.prepared_pending(), "prepared image keeps drain owned");
    require(!owner.try_close(false), "stop before Present cannot close prepared image");
    require(capture_present_phase(.1, -1, true, true) == CaptureImageAdmission::skip,
            "drain does not validate image with inactive live clock");
    const auto action = capture_seal_image(owned, 7, 12, .1, -1, true);
    require(action == CaptureSeal::seal, "owned pre-stop image seals after hold");
    require(action == CaptureSeal::seal && capture_event_allowed(14, true, true),
            "completion-only seal event admitted during drain");
    if (action == CaptureSeal::seal && owner.enter_event(14, true)) {
        owner.image_sealed();
        owner.leave_event();
    }
    require(owner.prepared_pending() && !owner.try_close(false),
            "sealed image retains ownership through GPU readback despite stale drained snapshot");
    if (owner.enter_event(12)) {
        owner.image_retired();
        owner.leave_event();
    }
    require(owner.try_close(false) && owner.finish_close(), "sealed final image releases close ownership");
    require(!capture_event_allowed(14, true, false), "unqualified seal event denied");
    CaptureDrainOwnership split;
    split.open();
    split.begin_drain(); // Atomic transition precedes the separate legacy samplingDone store.
    require(split.draining(), "Present observes atomic drain before samplingDone publication");
    require(capture_present_phase(.1, -1, true, split.draining()) == CaptureImageAdmission::skip,
            "split legacy flag cannot invalidate already draining image");
    for (unsigned kind : {1u, 2u, 8u, 13u, 18u})
        require(!capture_event_allowed(kind, true), "no new capture events during drain");
    for (unsigned kind : {9u, 10u, 11u, 12u})
        require(capture_event_allowed(kind, true), "existing GPU completion events admitted");

    require(capture_seal_image(owned, 8, 12, .1, -1, true) == CaptureSeal::reject,
            "replacement generation cannot seal old image");
    require(capture_seal_image(owned, 7, 13, .1, -1, true) == CaptureSeal::reject,
            "stale Present epoch cannot seal");
    auto bad = owned;
    bad.clock.epoch = 11;
    require(capture_seal_image(bad, 7, 12, .1, -1, true) == CaptureSeal::reject,
            "clock epoch must match prepared image");
    bad = owned; bad.clock.phase = .1;
    require(capture_seal_image(bad, 7, 12, .1, -1, true) == CaptureSeal::reject,
            "repeated pinned phase rejected");
    bad = owned; bad.clock.phase = std::numeric_limits<double>::quiet_NaN();
    require(capture_seal_image(bad, 7, 12, .1, -1, true) == CaptureSeal::reject,
            "invalid pinned phase rejected");
    bad = owned; bad.unsupported = true;
    require(capture_seal_image(bad, 7, 12, .1, .2, true) == CaptureSeal::reject,
            "unsupported or truncated image never completed");
    bad = owned; bad.draws = 0;
    require(capture_seal_image(bad, 7, 12, .1, .2, true) == CaptureSeal::reject,
            "no blank image manufactured");
    bad.scene_observed = true;
    require(capture_seal_image(bad, 7, 12, .1, .2, false) == CaptureSeal::seal,
            "observed scene with no player draws seals a transparent image");
    require(capture_seal_image(bad, 7, 13, .1, .2, false) == CaptureSeal::reject,
            "empty scene cannot reuse another Present epoch");
    require(capture_seal_image(bad, 8, 12, .1, .2, false) == CaptureSeal::reject,
            "empty scene cannot reuse another capture generation");
    require(capture_seal_image(bad, 7, 12, .1, .3, false) == CaptureSeal::reject,
            "empty scene cannot be relabeled to another camera phase");
    bad.unsupported = true;
    require(capture_seal_image(bad, 7, 12, .1, .2, false) == CaptureSeal::reject,
            "scene evidence cannot hide an unsupported selected draw");
    bad = owned; bad.prepared = false;
    require(capture_seal_image(bad, 7, 12, .1, .2, true) == CaptureSeal::skip,
            "absent selected image cannot seal");
    require(capture_present_phase(.1, .2, false, false) == CaptureImageAdmission::missing,
            "live missing-image guard unchanged");
    require(capture_present_phase(.2, .2, true, false) == CaptureImageAdmission::inconsistent,
            "live duplicate-image guard unchanged");
    require(capture_seal_image(owned, 7, 12, .1, .3, false) == CaptureSeal::reject,
            "live image cannot be relabeled by changed phase");
    require(capture_seal_image(owned, 7, 12, .1, .2, false) == CaptureSeal::seal,
            "unchanged live image still seals");
    CaptureDrainOwnership raced;
    raced.open();
    const auto validated = capture_seal_image(owned, 7, 12, .1, .2, false);
    const bool permission = capture_seal_permission(true, validated);
    raced.begin_drain(); // Stop wins after the live-image validation snapshot.
    const bool entered = raced.enter_event(14, permission);
    require(entered, "validated existing seal survives concurrent stop transition");
    if (entered)
        raced.leave_event();
    require(raced.try_close(false) && raced.finish_close(), "raced seal callback drains normally");
    require(!capture_seal_permission(true, CaptureSeal::reject), "rejected image never qualifies seal event");
    require(!capture_seal_permission(true, CaptureSeal::skip), "absent image never qualifies seal event");

    require(!capture_draw_phase(.6, capture_path_phase(.7, true)),
            "completed endpoint cannot start a Players image");
    require(capture_path_phase(.7, true) == CompletedCapturePhase,
            "valid completion remains distinct from inactive path");
    require(capture_present_phase(.1, capture_path_phase(.7, true), true, false) ==
            CaptureImageAdmission::skip, "completed path does not relabel its prepared image");
    require(capture_seal_image(owned, 7, 12, .1, capture_path_phase(.7, true), false) == CaptureSeal::seal,
            "owned final image can seal after natural completion before Stop");
    require(capture_seal_image(owned, 8, 12, .1, CompletedCapturePhase, false) == CaptureSeal::reject,
            "completion cannot authorize foreign generation");
    require(capture_seal_image(owned, 7, 13, .1, CompletedCapturePhase, false) == CaptureSeal::reject,
            "completion cannot authorize stale epoch");
    require(capture_path_phase(std::numeric_limits<double>::quiet_NaN(), true) == -1 &&
            capture_path_phase(-1, true) == -1, "invalid completion never publishes completion provenance");
    require(capture_path_phase(CompletedCapturePhase, false) == -1,
            "ordinary negative phase cannot forge completed sentinel");
    require(capture_path_phase(std::numeric_limits<double>::quiet_NaN(), false) == -1 &&
            capture_path_phase(std::numeric_limits<double>::infinity(), false) == -1,
            "ordinary nonfinite phase normalizes inactive");
    require(capture_seal_image(owned, 7, 12, .1,
            capture_path_phase(std::numeric_limits<double>::quiet_NaN(), true), false) == CaptureSeal::reject,
            "invalid completion cannot authorize final image");
    require(capture_seal_image(owned, 7, 12, .1, -1, false) == CaptureSeal::reject,
            "ordinary inactive or lost session cannot authorize final image");
    CaptureImageClock endpoint;
    require(!endpoint.accept(12, capture_path_phase(.7, true)),
            "no CaptureImageClock can be created from completed endpoint");
    require(capture_present_phase(.6, capture_path_phase(.7, true), false, false) ==
            CaptureImageAdmission::skip, "completed endpoint is not a missing image");
    require(capture_draw_phase(.1, capture_path_phase(.2, false)), "running phase still admitted");
    require(!capture_draw_phase(.1, capture_path_phase(-1, false)), "inactive path cannot draw");
    bad = owned; bad.sealed = true;
    require(capture_seal_image(bad, 7, 12, .1, .2, true) == CaptureSeal::skip,
            "already sealed slot cannot seal twice");
    require(!capture_drain_finished(false, 2999, true), "no-Present drain remains bounded waiting");
    require(capture_drain_finished(false, 3000, true), "no-Present drain closes at existing deadline");
    require(capture_drain_finished(true, 0, true), "fully drained capture closes promptly");
    require(complete(), "exact healthy capture still complete");
    require(!complete(23, 0, 23), "actual short capture remains rejected");
    require(!complete(24, 1, 25), "dropped image remains rejected");
    require(!complete(24, 0, 24, 24, true), "failed snapshot remains rejected");
    require(!complete(24, 0, 24, 24, false, false), "undrained writer remains rejected");
    require(!complete(24, 0, 24, 24, false, true, 1), "refused writer remains rejected");
    require(!complete(24, 0, 24, 24, false, true, 0, true), "writer error remains rejected");
    require(!complete(24, 0, 24, 24, false, true, 0, false, true), "lost cadence remains rejected");
    CaptureDrainOwnership interrupted;
    interrupted.open();
    require(interrupted.enter_event(10), "first callback owns admission");
    interrupted.begin_drain();
    require(!interrupted.try_close(false), "close CAS cannot overtake admitted callback");
    interrupted.retain_prepared(); // Callback admitted before Stop finishes its image.
    interrupted.leave_event();
    CaptureDrainOwnership presentation;
    presentation.open();
    {
        CaptureCallbackPin callback(presentation, 12);
        require(bool(callback), "Present pins slot validation before seal event allocation");
        presentation.begin_drain();
        require(presentation.try_close(true) && !presentation.finish_close(),
                "deadline cannot retire slot during Present validation");
    }
    require(presentation.finish_close(), "Present exit acknowledges forced close");
    CaptureCallbackPin late(presentation, 12);
    require(!late, "retired capture cannot admit another consumer");
    require(interrupted.prepared_pending() && !interrupted.try_close(false),
            "pre-stop callback publishes image reservation before releasing itself");
    require(!interrupted.enter_event(2), "atomic drain transition refuses new draw writers");
    require(interrupted.enter_event(12), "readback callback still admitted");
    require(interrupted.try_close(true), "deadline blocks future admission");
    require(!interrupted.finish_close(), "forced close waits admitted callbacks");
    require(!interrupted.enter_event(14, true), "closed capture refuses late seal");
    interrupted.leave_event();
    require(interrupted.finish_close() && !interrupted.prepared_pending(),
            "deadline abandons unpresented image only after writer quiescence");
    interrupted.open();
    require(!interrupted.prepared_pending() && interrupted.enter_event(2),
            "next capture starts with no old reservation");
    interrupted.leave_event();
    ProducerCaptureState producer;
    require(producer.hides(true, false), "hiding owns producers before any capture");
    producer.start(true);
    require(!producer.hides(true, false) && !producer.hides(true, true),
            "a live sequence request owns the producer table");
    producer.finish();
    require(producer.hides(true, false),
            "first-person hiding resumes after a sequence capture ends");
    require(!producer.hides(true, true) && !producer.hides(false, false),
            "hiding never runs inside an armed capture or when cleared");
    producer.start(false);
    require(producer.hides(true, false), "non-sequence requests keep pre-arm hiding");
    producer.finish();
    require(!producer.records(false, false),
            "idle producer calls after a capture skip the producer table");
    require(producer.records(true, false) && producer.records(false, true),
            "armed captures still record producers");
    producer.start(true);
    require(producer.records(false, false), "a live request records before it arms");
    producer.finish();
    require(capture_terminal_saved(true, true, false),
            "a full diagnostic folder cannot fail a sequence take with saved frame metadata");
    require(!capture_terminal_saved(true, false, true),
            "missing frame metadata still fails a sequence take");
    require(capture_terminal_saved(false, true, true) && !capture_terminal_saved(false, true, false),
            "single-image captures keep their diagnostic proof");
    require(capture_event_dump_count(65536, 65536, true) <= CaptureSequenceEventDumpLimit &&
                capture_event_dump_count(12, 65536, true) == 12,
            "sequence event dump stays small");
    require(capture_event_dump_count(70000, 65536, false) == 65536 &&
                capture_event_dump_count(900, 65536, false) == 900,
            "single-image event dump keeps the whole bounded ring");
    require(capture_frame_audited(0) && capture_frame_audited(CaptureFrameAuditFrames - 1),
            "the first frames of a take keep their coverage audit");
    require(!capture_frame_audited(CaptureFrameAuditFrames) && !capture_frame_audited(1000),
            "later frames skip the full-image audit on the render thread");
    require(!capture_slot_failure(CaptureSlotStep::seal, true) &&
                !capture_slot_failure(CaptureSlotStep::recycle, true),
            "started readbacks and reused slots keep the take running");
    require(capture_slot_failure(CaptureSlotStep::seal, false) &&
                capture_slot_failure(CaptureSlotStep::recycle, false),
            "a slot that cannot start or be reused stalls with a named reason");
    const std::string id = "0123456789abcdef0123456789abcdef";
    require(capture_marker_request("color-sequence-v3 0 24 players 8 8 fps 60 request " + id) == id &&
                capture_marker_request("color-sequence-v4 0 24 players 8 8 fps 60 request " + id +
                                       " speed 1 scratch 00") == id,
            "a refused marker is answered with the id of the take that wrote it");
    require(capture_marker_request("gpu-snapshot-v1 1 7").empty() &&
                capture_marker_request("x request " + id.substr(1)).empty() &&
                capture_marker_request("x request " + id + "0").empty() &&
                capture_marker_request("x request " + id + "Z").empty(),
            "a marker without one exact request id answers no take");
    std::printf("Player capture lifecycle tests: %u failures\n", failures);
    return failures ? 1 : 0;
}
