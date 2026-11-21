#include "dolly_player_producers.hpp"
#include "dolly_capture_timing.hpp"
#include "dolly_player_scene_generated.hpp"
#include <cstdlib>
#include <cstdio>
#include <thread>
#include <vector>
#if defined(_WIN32)
#include "dolly_player_capture.hpp"
#endif

static void require(bool value) {
    if (!value)
        std::abort();
}
#if defined(_WIN32)
static unsigned producerCalls = 0;
static int __fastcall legacy_producer(std::uintptr_t a, void* object, void* mesh, void* opaque,
                                      void* params, std::uint32_t mode, unsigned char* flag,
                                      std::uint32_t* outA, std::uint32_t* outB) {
    ++producerCalls;
    require(a == 17 && object == reinterpret_cast<void*>(21) && mesh == reinterpret_cast<void*>(22));
    require(opaque == reinterpret_cast<void*>(23) && params == reinterpret_cast<void*>(24) && mode == 31);
    *flag = 1;
    *outA = 41;
    *outB = 42;
    return 43;
}
static int __fastcall september_producer(std::uintptr_t a, void* object, void* mesh, void* opaque,
                                         void* params, std::uint32_t mode, unsigned char* flag,
                                         std::uint32_t* outA, std::uint32_t* outB, std::uint32_t* outC) {
    const auto result = legacy_producer(a, object, mesh, opaque, params, mode, flag, outA, outB);
    require(outC != nullptr);
    *outC = 44;
    return result;
}
#endif
int main() {
    using namespace dolly::player_capture;
    // Reproduce the 6745 failure: selecting the current module must select its
    // moved producer, not just change a vtable on the older September layout.
    const auto* current = reviewed_scene_profile(
        "eb8082bbb3b1895ae37c90181192e0f4b5787fe8fa2986114dd6173fdc234849", 0x9c6000,
        "ca0cd37c078fd070d9b8f4708fc892971d2a35f4c637624b366395e205723889", 0x4b7000);
    require(current && current->layout.producer == 0x5c8f0 && current->layout.table == 0x61e860);
    require(current->september && current->layout.owner == 0xd8 && current->layout.gpu_buffer == 0x70);
    // Every old pair retains its original entry and ABI. Crossed pairs, missing
    // modules, changed identities and wrong PE sizes must never enable a hook.
    const std::uintptr_t entries[] = {0x564b0, 0x5c8e0, 0x5c8e0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c9a0, 0x5c9a0, 0x5c970, 0x5c970};
    const std::uintptr_t tables[] = {0x5d4fe8, 0x61e7d0, 0x61e860, 0x61e860, 0x61e860, 0x61e800, 0x61e800, 0x61e800, 0x61e800, 0x61e800, 0x61e810, 0x61e880, 0x61e880, 0x61e808};
    static_assert(std::size(entries) == std::size(kSceneProfiles));
    static_assert(std::size(tables) == std::size(kSceneProfiles));
    unsigned profile_index = 0;
    for (const auto& scene : kSceneProfiles) {
        require(scene.layout.producer == entries[profile_index] && scene.layout.table == tables[profile_index]);
        require(scene.september == (profile_index != 0));
        ++profile_index;
        for (const auto& renderer : kSceneProfiles) {
            // Two reviewed scenes may share one renderer identity (6753/6757);
            // the (scene, renderer) pair still matches iff the renderer agrees.
            const bool same_renderer = std::strcmp(renderer.renderer_hash, scene.renderer_hash) == 0
                                       && renderer.renderer_size == scene.renderer_size;
            require(reviewed_scene_profile(scene.scene_hash, scene.scene_size,
                                           renderer.renderer_hash, renderer.renderer_size) ==
                    (same_renderer ? &scene : nullptr));
        }
        require(!reviewed_scene_profile(nullptr, scene.scene_size, scene.renderer_hash, scene.renderer_size));
        require(!reviewed_scene_profile(scene.scene_hash, scene.scene_size, nullptr, scene.renderer_size));
        require(!reviewed_scene_profile("unknown", scene.scene_size, scene.renderer_hash, scene.renderer_size));
        require(!reviewed_scene_profile(scene.scene_hash, scene.scene_size, "unknown", scene.renderer_size));
        require(!reviewed_scene_profile(scene.scene_hash, scene.scene_size + 1, scene.renderer_hash, scene.renderer_size));
        require(!reviewed_scene_profile(scene.scene_hash, scene.scene_size, scene.renderer_hash, scene.renderer_size + 1));
    }
    require(profile_index == std::size(kSceneProfiles));
#if defined(_WIN32)
    // Both real producer ABIs preserve every caller-owned output and invoke
    // the selected original exactly once; a tenth output must never be lost.
    for (bool updated : {false, true}) {
        unsigned char flag = 0;
        std::uint32_t a = 0, b = 0, c = 99;
        producerCalls = 0;
        require(forward_producer(legacy_producer, updated ? september_producer : nullptr,
                                 17, reinterpret_cast<void*>(21), reinterpret_cast<void*>(22),
                                 reinterpret_cast<void*>(23), reinterpret_cast<void*>(24),
                                 31, &flag, &a, &b, updated ? &c : nullptr) == 43);
        require(producerCalls == 1 && flag == 1 && a == 41 && b == 42 && c == (updated ? 44u : 99u));
    }
#endif
    using dolly::CaptureImageAdmission;
    using dolly::capture_image_admission;
    // Real Depth05 correction: every advancing rendered image survives even
    // though floor(phase*60) repeats 7 and jumps to9.
    double previous_phase = -1;
    for (double phase : {0., .016662598, .018737793, .035400391, .052062988, .068725586, .085418701,
                         .102081299, .118743896, .119781494, .152069092}) {
        require(capture_image_admission(previous_phase, phase, true) ==
                CaptureImageAdmission::capture);
        previous_phase = phase;
        require(capture_image_admission(previous_phase, phase, false) ==
                CaptureImageAdmission::skip);
        require(capture_image_admission(previous_phase, phase, true) ==
                CaptureImageAdmission::inconsistent);
    }
    require(capture_image_admission(previous_phase, .168731689, false) ==
            CaptureImageAdmission::missing);
    require(capture_image_admission(previous_phase, -1, true) ==
            CaptureImageAdmission::inconsistent);

    CaptureImageClock clock;
    require(!clock.accept(9, -1));
    require(!clock.accept(9, std::nan("")));
    require(clock.accept(9, .119781494));
    require(clock.accept(9, .119781494));
    require(!clock.accept(10, .119781494));
    require(!clock.accept(9, .152069092));
    require(clock.matches(9, .119781494));
    require(!clock.matches(9, .152069092));
    require(clock.phase == .119781494); // Failed checks never overwrite captured time.
    clock = CaptureImageClock{};
    require(clock.accept(10, .152069092));

    CaptureEventSlots<8> slots;
    auto pinned = slots.acquire();
    require(pinned.has_value());
    slots.retain(*pinned);
    slots.release(*pinned);
    for (unsigned i = 0; i < 100000; ++i) {
        const auto slot = slots.acquire();
        require(slot && *slot != *pinned);
        slots.release(*slot);
    }
    slots.release(*pinned);
    std::vector<unsigned> full;
    for (unsigned i = 0; i < 8; ++i) {
        const auto slot = slots.acquire();
        require(slot.has_value());
        full.push_back(*slot);
    }
    require(!slots.acquire());
    for (const auto slot : full)
        slots.release(slot);
    slots.reset();
    require(slots.acquire().has_value());
    // Exercise the lifetime contract under concurrent producers. Releasing one
    // reference must not make a still-pinned event available to another writer.
    CaptureEventSlots<8> concurrent;
    std::array<std::atomic<unsigned>, 8> owners{};
    std::vector<std::thread> slot_workers;
    std::atomic<unsigned> acquisitions{0};
    for (unsigned worker = 0; worker < 4; ++worker)
        slot_workers.emplace_back([&, worker] {
            for (unsigned n = 0; n < 10000; ++n) {
                const auto slot = concurrent.acquire();
                // A bounded scan may lose every CAS to concurrent churn even
                // when capacity exists; callers may retry on a later callback.
                if (!slot)
                    continue;
                acquisitions.fetch_add(1);
                unsigned free = 0;
                require(owners[*slot].compare_exchange_strong(free, worker + 1));
                concurrent.retain(*slot);
                concurrent.release(*slot);
                if (n % 31 == 0)
                    std::this_thread::yield();
                require(owners[*slot].load() == worker + 1);
                owners[*slot].store(0);
                concurrent.release(*slot);
            }
        });
    for (auto& worker : slot_workers)
        worker.join();
    require(acquisitions.load() > 0);
    // Every reference must have been released after all producers finish.
    for (unsigned n = 0; n < 8; ++n)
        require(concurrent.acquire().has_value());
    require(!concurrent.acquire());
    for (unsigned fps : {30u, 60u, 120u, 300u, 600u})
        for (unsigned n = 0; n < 6000; ++n)
            require(capture_frame_index(double(n) / fps, fps) == n);
    for (unsigned fps : {30u, 60u, 120u, 300u, 600u})
        for (double speed : {.05, .5, 1., 2., 4.})
            for (unsigned n = 0; n < 6000; ++n)
                require(capture_frame_index(double(n) * speed / fps, fps / speed) == n);
    ProducerTable<32> table;
    ProducerEntry selected{7, 5, 100, 1, 42, {1, 2, 3, 4, 5, 6, 7, 8}};
    table.store(selected);
    const auto saved = table.lookup(7, 5, 100);
    require(saved && saved->owner == 42);
    require(!table.lookup(8, 5, 100));
    require(!table.lookup(7, 5, 101));
    auto replaced = selected;
    replaced.owner = 0;
    table.store(replaced);
    require(table.lookup(7, 5, 100)->owner == 0);
    require(saved->owner == 42); // Returned values cannot mutate under readers.
    SubmittedOwners<2> submitted;
    auto revision = submitted.invalidate();
    require(submitted.publish(revision, 123, 100, &selected, 1));
    // Preparing the next generation cannot replace the already uploaded one.
    auto next = selected;
    next.frame = 8;
    next.owner = 77;
    table.store(next);
    require(submitted.lookup(123, 100, 5)->owner == 42);
    require(!submitted.lookup(124, 100, 5));
    require(!submitted.lookup(123, 101, 5));
    submitted.invalidate();
    require(!submitted.lookup(123, 100, 5));
    require(!submitted.publish(revision, 123, 100, &selected, 1));
    revision = submitted.invalidate();
    require(submitted.publish(revision, 123, 100, &next, 1));
    require(submitted.lookup(123, 100, 5)->owner == 77);
    revision = submitted.invalidate();
    require(!submitted.publish(revision, 123, 100, &next, 3));
    require(!submitted.lookup(123, 100, 5));
    require(record_matches(5, 5, selected.record, selected.record));
    require(!record_matches(6, 5, selected.record, selected.record));
    for (unsigned i = 0; i < 8; ++i) {
        auto changed = selected;
        ++changed.record[i];
        require(!record_matches(5, 5, changed.record, selected.record));
    }
    std::vector<std::thread> workers;
    for (unsigned worker = 0; worker < 11; ++worker)
        workers.emplace_back([&, worker] {
            for (unsigned n = 0; n < 2000; ++n) {
                ProducerEntry entry{n, worker, 100, n, worker + 1, {}};
                for (auto& word : entry.record)
                    word = n;
                table.store(entry);
                const auto read = table.lookup(n, worker, 100);
                if (read) {
                    require(read->owner == worker + 1 && read->event == n);
                    for (auto word : read->record)
                        require(word == n);
                }
            }
        });
    for (auto& worker : workers)
        worker.join();
    table.clear();
    require(table.snapshot().count == 0);
    // Owner classification cache: a full cache of players keeps every player
    // and declines new owners rather than writing past its last entry.
    {
        struct Owner { std::uint32_t handle = 0; bool player = false; };
        std::array<Owner, 4> owners{};
        unsigned count = 0, rotate = 0;
        require(owner_class_remember(owners, count, rotate, 1, false));
        for (std::uint32_t handle = 2; handle <= 4; ++handle)
            require(owner_class_remember(owners, count, rotate, handle, true));
        require(count == 4);
        require(owner_class_remember(owners, count, rotate, 5, true));
        require(owners[0].handle == 5 && owners[0].player);
        bool remembered = true, threw = false;
        try {
            remembered = owner_class_remember(owners, count, rotate, 6, true);
        } catch (...) {
            threw = true;
        }
        if (threw || remembered) {
            std::puts("FAIL full player cache wrote a slot past its last entry");
            std::fflush(stdout);
            std::abort();
        }
        for (unsigned i = 0; i < owners.size(); ++i)
            require(owners[i].player && owners[i].handle != 6);
    }
    std::puts("Player producer tests passed");
}
