#include <windows.h>
#include "dolly_player_scratch.hpp"
#include "dolly_player_sequence_writer.hpp"
#include "dolly_player_capture_lifecycle.hpp"
#include <atomic>
#include <filesystem>
#include <iostream>
#include <chrono>
#include <thread>

#define CHECK(x) do { if (!(x)) { std::cerr << "Failed line " << __LINE__ << '\n'; return 1; } } while (0)
int main() {
    namespace fs = std::filesystem;
    using namespace dolly::player_capture;
    const std::string request(32, 'a');
    auto root = fs::temp_directory_path() / ("dolly-scratch-test-" + std::to_string(GetCurrentProcessId()));
    CHECK(!fs::exists(root));
    CHECK(fs::create_directory(root));
    CHECK(fs::weakly_canonical(root).parent_path() == fs::weakly_canonical(fs::temp_directory_path()));
    auto folder = root / fs::path(L"Unicode-\u03b1 space") / ("dolly-players-" + request);
    fs::create_directories(folder);
    struct Cleanup { fs::path root; ~Cleanup(){ std::error_code error; fs::remove_all(root,error); } } cleanup{root};
    std::ofstream(folder / ".dolly-players-owner") << request;
    auto utf8 = folder.u8string();
    std::string hex;
    for (unsigned char c : utf8) { hex += "0123456789abcdef"[c>>4]; hex += "0123456789abcdef"[c&15]; }
    std::wstring path;
    CHECK(scratch_directory(hex,request,path));
    CHECK(!scratch_directory(hex,std::string(32,'b'),path));
    CHECK(!scratch_directory("00",request,path));
    CHECK(!scratch_directory("ff",request,path));
    CHECK(!scratch_directory(hex+"zz",request,path));
    auto bundle = folder / "dolly_owner_color_frame.bin";
    SequenceWriter writer;
    writer.synchronous_for_tests(true);
    CHECK(writer.open(bundle.wstring(),2,true));
    std::uint64_t header[8]={1,2,3,4,5,6,7,8};
    SequenceWriter::Buffer pixels(8,42);
    CHECK(writer.push(header,std::move(pixels)));
    CHECK(writer.finish());
    CHECK(fs::file_size(bundle)==72);
    CHECK(!scratch_directory(hex,request,path));
    // Reproduce a slow destination with a held worker and a full bounded queue.
    // A failed handoff must not empty the caller's completed readback: the next
    // render poll can still inspect it while capture failure is being drained.
    for (bool exclusive : {false, true}) {
        SequenceWriter slow;
        slow.hold_worker_for_tests(true);
        auto output = root / (exclusive ? "slow-exclusive.bin" : "slow-stream.bin");
        CHECK(slow.open(output.wstring(), 2, exclusive));
        SequenceWriter::Buffer first(8, 17);
        CHECK(slow.push(header, std::move(first)));
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!slow.in_flight() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::yield();
        CHECK(slow.in_flight() == 1);
        for (unsigned n = 0; n < 2; ++n) {
            SequenceWriter::Buffer queued(8, 29);
            CHECK(slow.push(header, std::move(queued)));
        }
        SequenceWriter::Buffer rejected(8, 107);
        const auto* retained = rejected.data();
        CHECK(!slow.push(header, std::move(rejected)));
        CHECK(rejected.size() == 8 && rejected.data() == retained);
        CHECK(rejected == SequenceWriter::Buffer(8, 107));
        CHECK(slow.refused() == 1);
        CHECK(slow.finish()); // Releases the held worker and drains accepted frames.
        CHECK(slow.written() == 3 && fs::file_size(output) == 3 * 72);
        CHECK(!slow.push(header, std::move(rejected)));
        CHECK(rejected == SequenceWriter::Buffer(8, 107));
    }
    SequenceWriter collision;
    CHECK(!collision.open(bundle.wstring(),2,true));
    CHECK(fs::file_size(bundle)==72);
    fs::remove(bundle);
    // Production's nonblocking handoff retains pixels through saturation past
    // the former 1.5s deadline, with unchanged memory/record order and no drops.
    {
        SequenceWriter paced;
        paced.hold_worker_for_tests(true);
        auto output = root / "automatic.bin";
        CHECK(paced.open(output.wstring(), 1, true));
        SequenceWriter::Buffer a(8, 11), b(8, 22), c(8, 33);
        CHECK(paced.submit(header, std::move(a)) == SequenceWriter::Submit::accepted);
        const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!paced.in_flight() && std::chrono::steady_clock::now() < end) std::this_thread::yield();
        CHECK(paced.in_flight() == 1);
        CHECK(paced.submit(header, std::move(b)) == SequenceWriter::Submit::accepted);
        const auto* retained = c.data();
        const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(1600);
        do {
            CHECK(paced.submit(header, std::move(c)) == SequenceWriter::Submit::waiting);
            CHECK(c.data() == retained && c == SequenceWriter::Buffer(8, 33));
            CHECK(paced.refused() == 0 && paced.bytes_written() == 0);
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        } while (std::chrono::steady_clock::now() < until);
        paced.hold_worker_for_tests(false);
        auto result = SequenceWriter::Submit::waiting;
        const auto resume = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (result == SequenceWriter::Submit::waiting && std::chrono::steady_clock::now() < resume) {
            result = paced.submit(header, std::move(c));
            std::this_thread::yield();
        }
        CHECK(result == SequenceWriter::Submit::accepted);
        paced.begin_finish();
        const auto finish = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!paced.finish_ready() && std::chrono::steady_clock::now() < finish) std::this_thread::yield();
        CHECK(paced.finish_ready() && paced.finish());
        CHECK(paced.written() == 3 && paced.refused() == 0 && paced.bytes_written() == 216);
        std::ifstream verify(output, std::ios::binary);
        for (unsigned char expected : {11, 22, 33}) {
            std::uint64_t actual[8]; unsigned char data[8];
            verify.read(reinterpret_cast<char*>(actual), 64);
            verify.read(reinterpret_cast<char*>(data), 8);
            CHECK(verify && std::memcmp(actual, header, 64) == 0);
            for (auto value : data) CHECK(value == expected);
        }
    }
    // A fixed-step capture waits for scratch space instead of rejecting a
    // frame. The blocked call must hand off after the worker drains and must
    // keep the caller's buffer when a real timeout expires.
    {
        SequenceWriter closing;
        closing.hold_worker_for_tests(true);
        CHECK(closing.open((root / "async-close.bin").wstring(), 1, true));
        SequenceWriter::Buffer pixels(8, 71), retained(8, 99);
        CHECK(closing.submit(header, std::move(pixels)) == SequenceWriter::Submit::accepted);
        const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!closing.in_flight() && std::chrono::steady_clock::now() < end) std::this_thread::yield();
        CHECK(closing.in_flight() == 1);
        closing.begin_finish(); // Must return while the writer is still held.
        CHECK(!closing.finish_ready());
        CHECK(closing.submit(header, std::move(retained)) == SequenceWriter::Submit::failed);
        CHECK(retained == SequenceWriter::Buffer(8, 99));
        closing.hold_worker_for_tests(false);
        CHECK(closing.finish());
        CHECK(closing.finish_ready() && closing.written() == 1);
    }
    {
        SequenceWriter paced;
        paced.hold_worker_for_tests(true);
        auto pacedPath = root / "paced.bin";
        CHECK(paced.open(pacedPath.wstring(), 2, false));
        {
            SequenceWriter::Buffer first(8, 5);
            CHECK(paced.push_blocking(header, std::move(first), 500));
            const auto ready = std::chrono::steady_clock::now() + std::chrono::seconds(2);
            while (!paced.in_flight() && std::chrono::steady_clock::now() < ready)
                std::this_thread::yield();
            CHECK(paced.in_flight() == 1);
            SequenceWriter::Buffer second(8, 6), third(8, 7);
            CHECK(paced.push_blocking(header, std::move(second), 500));
            CHECK(paced.push_blocking(header, std::move(third), 500));
            std::thread release([&] {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
                paced.hold_worker_for_tests(false);
            });
            SequenceWriter::Buffer fourth(8, 8);
            CHECK(paced.push_blocking(header, std::move(fourth), 2000));
            release.join();
            CHECK(paced.refused() == 0);
        }
        CHECK(paced.finish());
        CHECK(paced.written() == 4 && fs::file_size(pacedPath) == 4 * 72);
    }
    {
        SequenceWriter stuck;
        stuck.hold_worker_for_tests(true);
        auto stuckPath = root / "stuck.bin";
        CHECK(stuck.open(stuckPath.wstring(), 1, false));
        SequenceWriter::Buffer first(8, 1), second(8, 2);
        CHECK(stuck.push_blocking(header, std::move(first), 500));
        CHECK(stuck.push_blocking(header, std::move(second), 500));
        SequenceWriter::Buffer rejected(8, 9);
        const auto* retained = rejected.data();
        CHECK(!stuck.push_blocking(header, std::move(rejected), 50));
        CHECK(rejected.size() == 8 && rejected.data() == retained);
        CHECK(rejected == SequenceWriter::Buffer(8, 9));
        CHECK(stuck.refused() == 1);
        CHECK(stuck.finish());
        CHECK(stuck.written() == 2 && fs::file_size(stuckPath) == 2 * 72);
    }
    // push/push_blocking read opened_/closing_ before taking mutex_. That is
    // race-free only because begin_finish happens after the caller's closing
    // handoff: here a thread join, in production CaptureDrainOwnership.
    {
        SequenceWriter late;
        auto latePath = root / "closed-before-push.bin";
        CHECK(late.open(latePath.wstring(), 2, false));
        std::thread closer([&] { late.begin_finish(); });
        closer.join();
        bool blockingAccepted = true, pushAccepted = true;
        SequenceWriter::Buffer a(8, 3), b(8, 4);
        std::thread pusher([&] {
            blockingAccepted = late.push_blocking(header, std::move(a), 500);
            pushAccepted = late.push(header, std::move(b));
        });
        pusher.join();
        CHECK(!blockingAccepted && !pushAccepted);
        CHECK(a == SequenceWriter::Buffer(8, 3) && b == SequenceWriter::Buffer(8, 4));
        CHECK(late.refused() == 0);
        CHECK(late.finish());
        CHECK(late.written() == 0 && fs::file_size(latePath) == 0);
    }
    // The production order: render pushes only inside an admitted callback,
    // and tick calls begin_finish only after finish_close retires them. A
    // pinned push_blocking waiting on a held writer delays closure until its
    // own timeout; nothing is accepted after closing and nothing hangs.
    {
        CaptureDrainOwnership owner;
        SequenceWriter gated;
        gated.hold_worker_for_tests(true);
        auto gatedPath = root / "gated-close.bin";
        owner.open();
        CHECK(gated.open(gatedPath.wstring(), 1, false));
        SequenceWriter::Buffer first(8, 1), second(8, 2), third(8, 3);
        CHECK(gated.push_blocking(header, std::move(first), 500));
        const auto ready = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!gated.in_flight() && std::chrono::steady_clock::now() < ready)
            std::this_thread::yield();
        CHECK(gated.in_flight() == 1);
        CHECK(gated.push_blocking(header, std::move(second), 500)); // Queue is now full.
        std::atomic<unsigned> order{0}, pushReturned{0};
        std::atomic<bool> admitted{false}, pushAccepted{true};
        const auto started = std::chrono::steady_clock::now();
        std::thread render([&] {
            CaptureCallbackPin pin(owner, 12);
            if (!pin)
                return;
            admitted.store(true);
            pushAccepted.store(gated.push_blocking(header, std::move(third), 300));
            pushReturned.store(order.fetch_add(1) + 1);
        });
        while (!admitted.load() && std::chrono::steady_clock::now() - started < std::chrono::seconds(2))
            std::this_thread::yield();
        CHECK(admitted.load());
        owner.begin_drain();
        CHECK(owner.try_close(true)); // Forced deadline: admission closes now.
        bool closed = false;
        while (!(closed = owner.finish_close()) &&
               std::chrono::steady_clock::now() - started < std::chrono::seconds(3))
            std::this_thread::yield();
        CHECK(closed);
        const unsigned closeOrder = order.fetch_add(1) + 1;
        gated.begin_finish();
        render.join();
        CHECK(std::chrono::steady_clock::now() - started < std::chrono::seconds(3));
        CHECK(pushReturned.load() != 0 && pushReturned.load() < closeOrder);
        CHECK(!pushAccepted.load() && third == SequenceWriter::Buffer(8, 3));
        CHECK(gated.refused() == 1);
        {
            CaptureCallbackPin after(owner, 12);
            CHECK(!after);
        }
        SequenceWriter::Buffer fourth(8, 4);
        CHECK(!gated.push_blocking(header, std::move(fourth), 500));
        CHECK(gated.refused() == 1);
        CHECK(gated.finish());
        CHECK(gated.written() == 2 && fs::file_size(gatedPath) == 2 * 72);
    }
    {
        // Render-thread callers are noexcept: a frame buffer that cannot be
        // allocated is reported as an empty buffer, never an exception.
        SequenceWriter spare;
        bool threw = false;
        SequenceWriter::Buffer huge;
        try { huge = spare.take_buffer(static_cast<std::size_t>(-1)); } catch (...) { threw = true; }
        CHECK(!threw && huge.empty());
    }
    {
        // A finished take returns its recycled frame buffers to the system.
        SequenceWriter released;
        released.synchronous_for_tests(true);
        const auto output = root / "released.bin";
        CHECK(released.open(output.wstring(), 2));
        std::uint64_t frameHeader[8]{};
        CHECK(released.push(frameHeader, SequenceWriter::Buffer(1 << 20, 5)));
        CHECK(released.finish());
        CHECK(released.take_buffer(0).capacity() == 0);
    }
    CHECK(scratch_directory(hex,request,path));
    std::ofstream(folder / ".dolly-players-owner",std::ios::app) << 'x';
    CHECK(!scratch_directory(hex,request,path));
    std::cout << "Players scratch ownership and exclusive writer passed\n";
}
