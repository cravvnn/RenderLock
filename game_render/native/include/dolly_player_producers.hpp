#pragma once
#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <utility>
#include <cstdint>
#include <mutex>
#include <optional>

namespace dolly::player_capture {
// Fixed-capacity event storage. Writers and asynchronous readbacks retain slots;
// only unreferenced slots may be reused, regardless of total shot length.
template <unsigned Capacity> class CaptureEventSlots {
    std::array<std::atomic<unsigned>, Capacity> references{};
    std::atomic<unsigned> cursor{0};

public:
    std::optional<unsigned> acquire() {
        const auto first = cursor.fetch_add(1, std::memory_order_relaxed) % Capacity;
        for (unsigned n = 0; n < Capacity; ++n) {
            const auto index = (first + n) % Capacity;
            unsigned free = 0;
            if (references[index].compare_exchange_strong(free, 1, std::memory_order_acq_rel))
                return index;
        }
        return std::nullopt;
    }
    void retain(unsigned index) { references[index].fetch_add(1, std::memory_order_acq_rel); }
    void release(unsigned index) { references[index].fetch_sub(1, std::memory_order_release); }
    // Only after capture admission is closed and every writer has left.
    void reset() {
        for (auto& r : references)
            r.store(0);
        cursor.store(0);
    }
};
// Bind an image to the clock of its first selected draw. A later draw/seal
// cannot silently relabel those pixels with a different view's timestamp.
struct CaptureImageClock {
    std::uint64_t epoch = 0;
    double phase = -1;
    bool matches(std::uint64_t image_epoch, double time) const noexcept {
        return std::isfinite(time) && time >= 0 && phase == time && epoch == image_epoch;
    }
    bool accept(std::uint64_t image_epoch, double time) noexcept {
        if (!std::isfinite(time) || time < 0)
            return false;
        if (phase < 0) {
            epoch = image_epoch;
            phase = time;
        }
        return matches(image_epoch, time);
    }
};
inline long long capture_frame_index(double time, double fps) {
    return static_cast<long long>(std::floor(time * fps + 1e-6));
}

struct ProducerEntry {
    std::uint64_t frame = 0, instance = 0, records = 0, event = 0;
    std::uint32_t owner = 0;
    std::uint32_t record[8]{};
};

// Producer callbacks run on several game workers. Readers receive a copy so
// another worker cannot change the owner or record after a successful lookup.
template <unsigned Capacity> class ProducerTable {
public:
    struct Snapshot {
        std::array<ProducerEntry, Capacity> entries{};
        unsigned count = 0;
    };
    void clear() {
        std::lock_guard<std::mutex> lock(mutex_);
        state_.count = rotate_ = 0;
    }
    void store(const ProducerEntry& entry) {
        std::lock_guard<std::mutex> lock(mutex_);
        for (unsigned i = 0; i < state_.count; ++i) {
            auto& prior = state_.entries[i];
            if (prior.frame == entry.frame && prior.instance == entry.instance &&
                prior.records == entry.records) {
                prior = entry;
                return;
            }
        }
        unsigned slot = state_.count;
        if (slot < Capacity)
            ++state_.count;
        else {
            slot = rotate_;
            rotate_ = (rotate_ + 1) % Capacity;
        }
        state_.entries[slot] = entry;
    }
    std::optional<ProducerEntry> lookup(std::uint64_t frame, std::uint64_t instance,
                                        std::uint64_t records) const {
        std::lock_guard<std::mutex> lock(mutex_);
        // A reused slot from another generation is not ownership proof.
        for (unsigned i = state_.count; i-- > 0;) {
            const auto& entry = state_.entries[i];
            if (entry.frame == frame && entry.instance == instance && entry.records == records)
                return entry; // owner=0 intentionally masks a replaced owner.
        }
        return std::nullopt;
    }
    Snapshot snapshot() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return state_;
    }

private:
    mutable std::mutex mutex_;
    Snapshot state_;
    unsigned rotate_ = 0;
};

// Owner classification cache. Never let early non-player owners crowd players
// out: reuse a negative slot round-robin and keep every positive.
template <class Entry, std::size_t N>
bool owner_class_remember(std::array<Entry, N>& entries, unsigned& count, unsigned& rotate,
                          std::uint32_t handle, bool player) noexcept {
    unsigned slot = count;
    if (slot < N) {
        ++count;
    } else {
        for (unsigned step = 0; step < N; ++step) {
            const unsigned candidate = static_cast<unsigned>((rotate + step) % N);
            if (!entries[candidate].player) {
                slot = candidate;
                rotate = static_cast<unsigned>((candidate + 1) % N);
                break;
            }
        }
    }
    // Every entry is a player: classify uncached rather than evict one.
    if (slot >= N)
        return false;
    entries[slot].handle = handle;
    entries[slot].player = player;
    return true;
}

inline bool record_matches(std::uint32_t actual_id, std::uint32_t expected_id,
                           const std::uint32_t* actual, const std::uint32_t* expected) {
    if (actual_id != expected_id)
        return false;
    for (unsigned i = 0; i < 8; ++i)
        if (actual[i] != expected[i])
            return false;
    return true;
}

// A draw consumes the last submitted upload on its context, not necessarily
// the generation that CPU workers are currently preparing. Lifetime pins the
// resource so pointer reuse cannot revive old ownership.
template <unsigned Capacity, class Lifetime = std::nullptr_t> class SubmittedOwners {
public:
    std::uint64_t invalidate() {
        std::lock_guard<std::mutex> lock(mutex_);
        count_ = 0;
        lifetime_ = Lifetime{};
        return ++revision_;
    }
    bool publish(std::uint64_t revision, std::uint64_t context, std::uint64_t resource,
                 const ProducerEntry* entries, unsigned count, Lifetime lifetime = {}) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (revision != revision_ || count > Capacity)
            return false;
        context_ = context;
        resource_ = resource;
        lifetime_ = std::move(lifetime);
        for (unsigned i = 0; i < count; ++i)
            entries_[i] = entries[i];
        count_ = count;
        return true;
    }
    std::optional<ProducerEntry> lookup(std::uint64_t context, std::uint64_t resource,
                                        std::uint64_t instance) const {
        std::lock_guard<std::mutex> lock(mutex_);
        if (context != context_ || resource != resource_)
            return std::nullopt;
        for (unsigned i = 0; i < count_; ++i)
            if (entries_[i].instance == instance)
                return entries_[i];
        return std::nullopt;
    }

private:
    mutable std::mutex mutex_;
    std::array<ProducerEntry, Capacity> entries_{};
    Lifetime lifetime_{};
    std::uint64_t context_ = 0, resource_ = 0, revision_ = 0;
    unsigned count_ = 0;
};
}
