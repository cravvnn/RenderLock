#include "dolly_depth_live.hpp"
#include <cstdio>
#include <cstdlib>

static void require(bool okay) {
    if (!okay) {
        std::fputs("Live depth ownership regression failed.\n", stderr);
        std::exit(1);
    }
}
int main() {
    using dolly::depth::LiveDepth;
    LiveDepth live;
    // A request before the overlay publishes a device changes nothing.
    require(!live.published() && !live.needs_hooks() && !live.needs_tracker());
    live.request(true);
    require(!live.needs_hooks() && !live.needs_tracker());
    live.publish(0x10, 0x20, 2560, 1440);
    require(live.published() && live.needs_hooks() && live.needs_tracker());
    const auto first = live.generation();
    live.note_hooks(true);
    require(live.hooks() && !live.needs_hooks());
    live.note_tracker(true);
    require(live.active() && !live.needs_tracker() && !live.needs_drop());
    // The render callback republishes the same device every Present; that must
    // not churn the tracker generation.
    live.publish(0x10, 0x20, 2560, 1440);
    require(live.generation() == first && live.active() && !live.needs_tracker());
    // A resize is a new generation: the old tracker cannot stay active.
    live.publish(0x10, 0x20, 1920, 1080);
    require(live.generation() != first && !live.active() && live.needs_tracker());
    require(!live.needs_hooks() && live.width() == 1920 && live.height() == 1080);
    live.note_tracker(true);
    require(live.active());
    // Disabling asks for a drop but keeps the process-wide hooks inert.
    live.request(false);
    require(live.needs_drop() && !live.needs_tracker());
    live.note_tracker(false);
    require(!live.active() && live.hooks());
    // Device loss invalidates the tracker and unpublishes the pointers.
    live.request(true);
    live.note_tracker(true);
    live.clear_device();
    require(!live.published() && !live.active() && !live.needs_tracker());
    require(live.device() == 0 && live.immediate() == 0);
    // Invalid dimensions behave like device loss.
    live.publish(0x10, 0x20, 0, 1440);
    require(!live.published() && live.width() == 0 && live.height() == 0);
    std::puts("Live depth ownership contract passed.");
}
