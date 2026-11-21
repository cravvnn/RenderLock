#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace dolly {

// X, Y, Z, pitch, yaw, roll, aspect ratio. Angles are degrees, continuously
// unwrapped exactly as authored by the Python editor's evaluator.
using CameraPose = std::array<double, 7>;

class NativePath {
public:
    static constexpr std::size_t max_camera_keys = 4096;
    static constexpr std::size_t header_bytes = 160;
    static constexpr std::size_t segment_bytes = 296;
    static constexpr std::size_t lens_header_bytes = 176, lens_segment_bytes = 336;
    static constexpr std::size_t max_bytes =
        lens_header_bytes + (max_camera_keys - 1) * lens_segment_bytes;

    // Load once on the non-render thread. Failure leaves a previous valid
    // object untouched. Publish a successful object immutably to the view
    // callback; load/evaluate must not run concurrently on the same object.
    bool load(const void* data, std::size_t bytes, std::string& error);

    // Binary search and polynomial evaluation only: no allocation, lock,
    // engine call, derivative calculation, or external clock access.
    // A zero lens scale means a legacy path with no captured lens metadata.
    bool evaluate(double shot_seconds, CameraPose& out,
                  double* lens_scale = nullptr) const noexcept;
    double duration() const noexcept { return duration_; }
    bool empty() const noexcept { return !loaded_; }

private:
    struct Channel {
        std::uint32_t kind = 0;
        std::uint32_t flags = 0;
        double left = 0;
        double right = 0;
        double left_derivative = 0;
        double right_derivative = 0;
    };
    struct Segment {
        double begin = 0;
        double end = 0;
        std::array<Channel, 8> channels{};
    };

    bool loaded_ = false;
    double duration_ = 0;
    double first_time_ = 0;
    double last_time_ = 0;
    std::array<double, 8> first_{};
    std::array<double, 8> last_{};
    std::vector<Segment> segments_;
};

} // namespace dolly
