#pragma once
#include <cstdint>

namespace dolly::depth {
// Ownership contract for live depth observation.
//
// The overlay publishes the game's device/context once per device generation
// and clears it before releasing either resource. A depth recording request
// arms observation; the render thread then creates exactly one scene tracker
// for the published size. Resize, device loss, disable and session end
// invalidate the tracker before the game's resources change. Hook
// installation is process-wide and stays resident; a null tracker keeps the
// hooks inert, so removing observation never tears the hooks down.
class LiveDepth {
public:
    void publish(std::uintptr_t device, std::uintptr_t immediate, std::uint32_t width,
                 std::uint32_t height) noexcept {
        if (device == 0 || immediate == 0 || width == 0 || height == 0) {
            clear_device();
            return;
        }
        // The render callback republishes the same device every Present. Only
        // a real change is a new generation; otherwise the tracker would be
        // destroyed and recreated on every frame.
        if (published_ && device_ == device && immediate_ == immediate && width_ == width &&
            height_ == height)
            return;
        device_ = device;
        immediate_ = immediate;
        width_ = width;
        height_ = height;
        published_ = true;
        ++generation_;
        tracker_generation_ = 0;
    }
    void clear_device() noexcept {
        device_ = immediate_ = 0;
        width_ = height_ = 0;
        published_ = false;
        ++generation_;
        tracker_generation_ = 0;
    }
    // Depth recording requested or released. Hooks stay installed.
    void request(bool enabled) noexcept { requested_ = enabled; }
    void note_hooks(bool installed) noexcept { hooks_ = installed; }
    // Record the result of creating or dropping the tracker for this generation.
    void note_tracker(bool valid) noexcept { tracker_generation_ = valid ? generation_ : 0; }
    bool published() const noexcept { return published_; }
    bool requested() const noexcept { return requested_; }
    bool hooks() const noexcept { return hooks_; }
    bool active() const noexcept {
        return tracker_generation_ != 0 && tracker_generation_ == generation_;
    }
    bool needs_hooks() const noexcept { return requested_ && published_ && !hooks_; }
    bool needs_tracker() const noexcept { return requested_ && published_ && !active(); }
    bool needs_drop() const noexcept { return active() && !requested_; }
    std::uint32_t generation() const noexcept { return generation_; }
    std::uint32_t width() const noexcept { return width_; }
    std::uint32_t height() const noexcept { return height_; }
    std::uintptr_t device() const noexcept { return device_; }
    std::uintptr_t immediate() const noexcept { return immediate_; }

private:
    std::uintptr_t device_ = 0, immediate_ = 0;
    std::uint32_t width_ = 0, height_ = 0;
    std::uint32_t generation_ = 0, tracker_generation_ = 0;
    bool published_ = false, requested_ = false, hooks_ = false;
};
}
