#pragma once

namespace dolly {
// A rejected optional pair must never remove a trampoline that could be running.
// The caller keeps its callbacks resident and pass-through until success.
template <class Hooks> bool install_optional_pair(Hooks& hooks) {
    if (!hooks.create(0))
        return false;
    if (!hooks.create(1)) {
        hooks.remove(0);
        return false;
    }
    if (!hooks.queue_enable(0) || !hooks.queue_enable(1)) {
        hooks.queue_disable(0);
        hooks.queue_disable(1);
        hooks.remove(0);
        hooks.remove(1);
        return false;
    }
    if (!hooks.apply()) {
        // Apply can fail after enabling one callback. Retain both trampolines.
        // Disable alone does not cancel pending enablement for a callback that
        // was never enabled; another feature may apply the queue later.
        hooks.queue_disable(0);
        hooks.queue_disable(1);
        hooks.disable(0);
        hooks.disable(1);
        return false;
    }
    return true;
}
}
