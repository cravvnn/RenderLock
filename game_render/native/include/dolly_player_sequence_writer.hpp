#pragma once
// Bounded ordered writer for captured sequence frames. Completed readbacks are
// handed off in capture order and flushed by one worker. A full queue or a
// failed write is reported to the caller; memory never grows past the bound.
#include <atomic>
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <system_error>
#include <vector>
#include <windows.h>
namespace dolly::player_capture {
class SequenceWriter {
public:
    using Buffer = std::vector<unsigned char>;
    enum class Submit { accepted, waiting, failed };
    SequenceWriter() = default;
    SequenceWriter(const SequenceWriter&) = delete;
    SequenceWriter& operator=(const SequenceWriter&) = delete;
    ~SequenceWriter() { finish(); }

    bool open(const std::wstring& path, unsigned maxQueued, bool exclusive = false) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (opened_ && !closed_) return false;
        queue_.clear(); free_.clear();
        maxQueued_ = maxQueued ? maxQueued : 1;
        if (exclusive) {
            handle_ = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                                  CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (handle_ == INVALID_HANDLE_VALUE) return false;
        } else {
            file_.open(path, std::ios::binary | std::ios::trunc);
            if (!file_) return false;
        }
        opened_ = true; closed_ = false; closing_ = false;
        error_.store(false); written_.store(0); refused_.store(0); inFlight_.store(0);
        bytesWritten_.store(0);
        progress_.store(0);
        finished_.store(false);
        progressMs_.store(GetTickCount64());
        if (!synchronous_) {
            try {
                worker = std::thread([this] { run(); });
            } catch (const std::system_error&) {
                if (file_.is_open()) file_.close();
                if (handle_ != INVALID_HANDLE_VALUE) {
                    CloseHandle(handle_);
                    handle_ = INVALID_HANDLE_VALUE;
                }
                opened_ = false; closed_ = true; closing_ = false;
                error_.store(true); finished_.store(true);
                return false;
            }
        }
        return true;
    }

    void synchronous_for_tests(bool enable) { synchronous_ = enable; }
    void hold_worker_for_tests(bool hold) {
        { std::lock_guard<std::mutex> lock(holdMutex_); hold_ = hold; }
        holdCv_.notify_all();
    }
    unsigned written() const { return written_.load(); }
    unsigned refused() const { return refused_.load(); }
    unsigned in_flight() const { return inFlight_.load(); }
    bool error() const { return error_.load(); }
    std::uint64_t bytes_written() const { return bytesWritten_.load(); }
    std::uint64_t progress() const { return progress_.load(); }
    std::uint64_t progress_ms() const { return progressMs_.load(); }

    // A full queue retains the caller's exact pixels. Present can poll again
    // while holding the fixed-step frame; saturation is not a rejected image.
    Submit submit(const std::uint64_t header[8], Buffer&& pixels) {
        if (synchronous_)
            return push(header, std::move(pixels)) ? Submit::accepted : Submit::failed;
        std::unique_lock<std::mutex> lock(mutex_);
        if (!opened_ || closing_ || error_.load())
            return Submit::failed;
        if (queue_.size() >= maxQueued_)
            return Submit::waiting;
        queue_.emplace_back();
        std::memcpy(queue_.back().header, header, sizeof(queue_.back().header));
        queue_.back().pixels = std::move(pixels);
        wake_.notify_one();
        return Submit::accepted;
    }

    void begin_finish() {
        std::lock_guard<std::mutex> lock(mutex_);
        closing_ = true;
        wake_.notify_all();
        space_.notify_all();
    }
    bool finish_ready() const { return synchronous_ || finished_.load(std::memory_order_acquire); }

    // An empty result for a nonzero size means the buffer could not be allocated.
    Buffer take_buffer(std::size_t bytes) noexcept {
        Buffer buffer;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!free_.empty()) { buffer = std::move(free_.back()); free_.pop_back(); }
        }
        try {
            buffer.resize(bytes);
        } catch (...) {
            return Buffer();
        }
        return buffer;
    }

    // False means the frame was not accepted (overflow, closed, or write failure).
    // A rejected frame remains owned by the caller, including its pixel bytes.
    bool push(const std::uint64_t header[8], Buffer&& pixels) {
        if (!opened_ || closing_) return false;
        if (synchronous_) {
            const bool ok = write_record(header, pixels);
            if (ok) {
                written_.fetch_add(1);
                recycle(std::move(pixels));
            } else error_.store(true);
            return ok;
        }
        std::unique_lock<std::mutex> lock(mutex_);
        if (queue_.size() >= maxQueued_) {
            refused_.fetch_add(1);
            return false;
        }
        queue_.emplace_back();
        std::memcpy(queue_.back().header, header, sizeof(queue_.back().header));
        queue_.back().pixels = std::move(pixels);
        wake_.notify_one();
        return true;
    }

    // Wait for queue space, then hand off. A temporary slow destination is a
    // pause, not a failed frame: this is only used by fixed-step captures whose
    // replay clock does not advance while the writer drains. Only a timeout is
    // counted as refused, so a completed retry cannot fail the take later.
    bool push_blocking(const std::uint64_t header[8], Buffer&& pixels, unsigned timeout_ms) {
        if (!opened_ || closing_ || error_.load()) return false;
        if (synchronous_) return push(header, std::move(pixels));
        std::unique_lock<std::mutex> lock(mutex_);
        if (queue_.size() >= maxQueued_) {
            const bool ready = space_.wait_for(lock, std::chrono::milliseconds(timeout_ms), [this] {
                return !opened_ || closing_ || error_.load() || queue_.size() < maxQueued_;
            });
            if (!ready || !opened_ || closing_ || error_.load()) {
                if (opened_ && !closing_ && !error_.load()) refused_.fetch_add(1);
                return false;
            }
        }
        queue_.emplace_back();
        std::memcpy(queue_.back().header, header, sizeof(queue_.back().header));
        queue_.back().pixels = std::move(pixels);
        wake_.notify_one();
        return true;
    }

    // Drains accepted frames and closes the file. Returns false on any write
    // failure; the caller must also check refused() for rejected frames.
    bool finish() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!opened_ || closed_) return opened_ && !error_.load();
            closing_ = true;
            wake_.notify_all();
            space_.notify_all();
        }
        hold_worker_for_tests(false);
        if (worker.joinable()) worker.join();
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.clear();
        free_.clear(); // Recycled frame buffers end with the take.
        if (file_.is_open()) {
            file_.flush();
            if (!file_) error_.store(true);
            file_.close();
        }
        if (handle_ != INVALID_HANDLE_VALUE) {
            if (!FlushFileBuffers(handle_)) error_.store(true);
            CloseHandle(handle_);
            handle_ = INVALID_HANDLE_VALUE;
        }
        closed_ = true;
        return !error_.load();
    }

private:
    struct Item { std::uint64_t header[8]{}; Buffer pixels; };
    bool write_bytes(const void* bytes, std::size_t size) {
        const auto* source = static_cast<const unsigned char*>(bytes);
        while (size) {
            const auto count = static_cast<DWORD>(std::min<std::size_t>(size, 2u * 1024u * 1024u));
            if (handle_ != INVALID_HANDLE_VALUE) {
                DWORD written = 0;
                if (!WriteFile(handle_, source, count, &written, nullptr) || written != count)
                    return false;
            } else {
                file_.write(reinterpret_cast<const char*>(source), count);
                if (!file_)
                    return false;
            }
            source += count;
            size -= count;
            bytesWritten_.fetch_add(count);
            progress_.fetch_add(1);
            progressMs_.store(GetTickCount64());
        }
        return true;
    }
    bool write_record(const std::uint64_t header[8], const Buffer& pixels) {
        return write_bytes(header, 64) && write_bytes(pixels.data(), pixels.size());
    }
    void flush_output() {
        if (file_.is_open()) {
            file_.flush();
            if (!file_)
                error_.store(true);
            file_.close();
        }
        if (handle_ != INVALID_HANDLE_VALUE) {
            if (!FlushFileBuffers(handle_))
                error_.store(true);
            CloseHandle(handle_);
            handle_ = INVALID_HANDLE_VALUE;
        }
    }
    void recycle(Buffer&& buffer) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (free_.size() < maxQueued_ + 2) free_.push_back(std::move(buffer));
    }
    void run() {
        for (;;) {
            Item item;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                wake_.wait(lock, [this] { return closing_ || !queue_.empty(); });
                if (queue_.empty()) {
                    if (closing_)
                        break;
                    continue;
                }
                item = std::move(queue_.front());
                queue_.pop_front();
            }
            space_.notify_all();
            inFlight_.fetch_add(1);
            {
                std::unique_lock<std::mutex> hold(holdMutex_);
                holdCv_.wait(hold, [this] { return !hold_; });
            }
            if (!error_.load() && !write_record(item.header, item.pixels)) error_.store(true);
            if (!error_.load()) written_.fetch_add(1);
            inFlight_.fetch_sub(1);
            recycle(std::move(item.pixels));
        }
        flush_output();
        progress_.fetch_add(1);
        progressMs_.store(GetTickCount64());
        finished_.store(true, std::memory_order_release);
    }
    std::ofstream file_;
    HANDLE handle_ = INVALID_HANDLE_VALUE;
    std::thread worker;
    std::mutex mutex_;
    std::condition_variable wake_;
    std::condition_variable space_; // Wakes a fixed-step caller waiting for queue room.
    std::mutex holdMutex_;
    std::condition_variable holdCv_;
    std::deque<Item> queue_;
    std::vector<Buffer> free_;
    std::atomic<unsigned> written_{0}, refused_{0}, inFlight_{0};
    std::atomic<std::uint64_t> bytesWritten_{0}, progress_{0}, progressMs_{0};
    std::atomic<bool> finished_{false};
    std::atomic<bool> error_{false};
    unsigned maxQueued_ = 1;
    bool opened_ = false, closed_ = false, closing_ = false, synchronous_ = false, hold_ = false;
};
}
