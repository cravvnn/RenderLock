#include "dolly_path.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <limits>
#include <utility>

namespace dolly {
namespace {

static_assert(sizeof(double) == 8 && std::numeric_limits<double>::is_iec559,
              "Dolly path format requires IEEE-754 binary64");

class Reader {
public:
    explicit Reader(const void* data) : cursor_(static_cast<const std::uint8_t*>(data)) {}

    std::uint32_t u32() noexcept {
        std::uint32_t value = 0;
        for (unsigned i = 0; i != 4; ++i)
            value |= std::uint32_t(*cursor_++) << (i * 8);
        return value;
    }
    double number() noexcept {
        std::uint64_t bits = 0;
        for (unsigned i = 0; i != 8; ++i)
            bits |= std::uint64_t(*cursor_++) << (i * 8);
        double value;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }
    void skip(std::size_t bytes) noexcept { cursor_ += bytes; }

private:
    const std::uint8_t* cursor_;
};

template <std::size_t N> bool finite_pose(const std::array<double, N>& pose) noexcept {
    for (double value : pose)
        if (!std::isfinite(value))
            return false;
    return true;
}

} // namespace

bool NativePath::load(const void* data, std::size_t bytes, std::string& error) {
    error.clear();
    auto fail = [&error](const char* reason) {
        error = reason;
        return false;
    };
    if (!data || bytes < header_bytes || bytes > max_bytes)
        return fail("Native path has an invalid byte length");
    if (std::memcmp(data, "DLYPATH\0", 8) != 0)
        return fail("Native path magic does not match");
    Reader reader(data);
    reader.skip(8);
    const auto version = reader.u32();
    const auto count = reader.u32();
    const auto channels = reader.u32();
    const auto reserved = reader.u32();
    if (!((version == 1 && channels == 7) || (version == 2 && channels == 8)) || reserved != 0)
        return fail("Native path version, channel count, or reserved field is invalid");
    const auto header_size = version == 2 ? lens_header_bytes : header_bytes;
    const auto segment_size = version == 2 ? lens_segment_bytes : segment_bytes;
    if (count >= max_camera_keys || bytes != header_size + std::size_t(count) * segment_size)
        return fail("Native path segment count does not match its byte length");

    NativePath candidate;
    candidate.duration_ = reader.number();
    candidate.first_time_ = reader.number();
    candidate.last_time_ = reader.number();
    for (unsigned i = 0; i < channels; ++i)
        candidate.first_[i] = reader.number();
    for (unsigned i = 0; i < channels; ++i)
        candidate.last_[i] = reader.number();
    if (version == 2 && (candidate.first_[7] < .001 || candidate.first_[7] > 100 ||
                         candidate.last_[7] < .001 || candidate.last_[7] > 100))
        return fail("Native path lens is outside its supported range");
    if (!std::isfinite(candidate.duration_) || !std::isfinite(candidate.first_time_) ||
        !std::isfinite(candidate.last_time_) || candidate.first_time_ < 0 ||
        candidate.last_time_ < candidate.first_time_ ||
        candidate.duration_ < candidate.last_time_ || !finite_pose(candidate.first_) ||
        !finite_pose(candidate.last_))
        return fail("Native path header contains nonfinite or unordered values");
    if (count == 0 &&
        (candidate.first_time_ != candidate.last_time_ || candidate.first_ != candidate.last_))
        return fail("A single-key native path must have identical endpoints");

    try {
        candidate.segments_.reserve(count);
        auto previous = candidate.first_;
        double previous_time = candidate.first_time_;
        for (std::uint32_t index = 0; index < count; ++index) {
            Segment segment;
            segment.begin = reader.number();
            segment.end = reader.number();
            if (!std::isfinite(segment.begin) || !std::isfinite(segment.end) ||
                segment.begin != previous_time || segment.end <= segment.begin ||
                segment.end > candidate.last_time_)
                return fail("Native path segments are not ordered and contiguous");
            for (std::size_t channel_index = 0; channel_index < channels; ++channel_index) {
                Channel& channel = segment.channels[channel_index];
                channel.kind = reader.u32();
                channel.flags = reader.u32();
                channel.left = reader.number();
                channel.right = reader.number();
                channel.left_derivative = reader.number();
                channel.right_derivative = reader.number();
                if (channel.kind > 2 || channel.flags > 1 || !std::isfinite(channel.left) ||
                    !std::isfinite(channel.right) || !std::isfinite(channel.left_derivative) ||
                    !std::isfinite(channel.right_derivative) ||
                    channel.left != previous[channel_index])
                    return fail(
                        "Native path channel has invalid coefficients or disconnected endpoints");
                if (channel_index == 7 &&
                    (channel.flags != 1 || channel.left < .001 || channel.left > 100 ||
                     channel.right < .001 || channel.right > 100))
                    return fail("Native path lens coefficients are invalid");
                previous[channel_index] = channel.right;
            }
            candidate.segments_.push_back(segment);
            previous_time = segment.end;
        }
        if (previous_time != candidate.last_time_ || previous != candidate.last_)
            return fail("Native path final segment does not match the final key");
    } catch (const std::exception&) {
        return fail("Could not allocate native camera path storage");
    }
    candidate.loaded_ = true;
    *this = std::move(candidate);
    return true;
}

bool NativePath::evaluate(double shot_seconds, CameraPose& out, double* lens_scale) const noexcept {
    if (!loaded_ || !std::isfinite(shot_seconds))
        return false;
    if (shot_seconds <= first_time_ || segments_.empty()) {
        std::copy_n(first_.begin(), out.size(), out.begin());
        if (lens_scale)
            *lens_scale = first_[7];
        return true;
    }
    if (shot_seconds >= last_time_) {
        std::copy_n(last_.begin(), out.size(), out.begin());
        if (lens_scale)
            *lens_scale = last_[7];
        return true;
    }
    // bisect_right on segment begin times makes a step channel switch at
    // the authored key itself, exactly like the editor's evaluator.
    std::size_t low = 0, high = segments_.size();
    while (low < high) {
        const std::size_t middle = low + (high - low) / 2;
        if (segments_[middle].begin <= shot_seconds)
            low = middle + 1;
        else
            high = middle;
    }
    const Segment& segment = segments_[low - 1];
    const double span = segment.end - segment.begin;
    const double u = (shot_seconds - segment.begin) / span;
    const double u2 = u * u, u3 = u * u * u;
    std::array<double, 8> result{};
    for (std::size_t i = 0; i < result.size(); ++i) {
        const Channel& channel = segment.channels[i];
        double value = channel.left;
        if (channel.kind == 1) {
            value = (1 - u) * channel.left + u * channel.right;
        } else if (channel.kind == 2) {
            value =
                ((2 * u3 - 3 * u2 + 1) * channel.left +
                 (u3 - 2 * u2 + u) * span * channel.left_derivative +
                 (-2 * u3 + 3 * u2) * channel.right + (u3 - u2) * span * channel.right_derivative);
            if (!std::isfinite(value))
                value = (1 - u) * channel.left + u * channel.right;
            else if (channel.flags & 1)
                value = std::max(std::min(channel.left, channel.right),
                                 std::min(std::max(channel.left, channel.right), value));
        }
        if (!std::isfinite(value))
            return false;
        result[i] = value;
    }
    std::copy_n(result.begin(), out.size(), out.begin());
    if (lens_scale)
        *lens_scale = result[7];
    return true;
}

} // namespace dolly
