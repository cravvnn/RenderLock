#include "dolly_depth.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <ostream>
#include <string>
#include <vector>

namespace dolly::depth {
namespace {
constexpr std::uint32_t kMaxDimension = 16384;
constexpr std::uint64_t kMaxPixels = 64ULL * 1024 * 1024;
bool dimensions(std::uint32_t width, std::uint32_t height) noexcept {
    return width && height && width <= kMaxDimension && height <= kMaxDimension &&
           std::uint64_t(width) * height <= kMaxPixels;
}
bool distance_value(float value) noexcept {
    return value > 0 && !std::isnan(value); // Allows +infinity, never -infinity.
}
std::uint32_t u32(const unsigned char* bytes) noexcept {
    return std::uint32_t(bytes[0]) | (std::uint32_t(bytes[1]) << 8) |
           (std::uint32_t(bytes[2]) << 16) | (std::uint32_t(bytes[3]) << 24);
}
double f32(const unsigned char* bytes) noexcept {
    const auto bits = u32(bytes);
    float value = 0;
    std::memcpy(&value, &bits, 4);
    return value;
}
bool near(double a, double b, double tolerance = 1e-4) noexcept {
    return std::isfinite(a) && std::isfinite(b) &&
           std::abs(a - b) <= tolerance * std::max({1.0, std::abs(a), std::abs(b)});
}
bool linearize_valid(const Projection& projection, double device_z, float& distance) noexcept {
    if (!std::isfinite(device_z) || device_z < projection.viewport_min ||
        device_z > projection.viewport_max)
        return false;
    const auto [a, b, c, d] = projection.inverse_z;
    const double z =
        (device_z - projection.viewport_min) / (projection.viewport_max - projection.viewport_min);
    const double denominator = c * z + d;
    if (denominator == 0) {
        distance = std::numeric_limits<float>::infinity();
        return true;
    }
    const double value = -(a * z + b) / denominator;
    if (!std::isfinite(value) || value <= 0 || value > std::numeric_limits<float>::max())
        return false;
    distance = static_cast<float>(value);
    return distance > 0;
}
void append(std::string& out, std::uint64_t value, unsigned bytes = 4) {
    for (unsigned i = 0; i < bytes; ++i)
        out.push_back(static_cast<char>(value >> (8 * i)));
}
void floating(std::string& out, float value) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
                  "OpenEXR requires IEEE 754 binary32");
    std::uint32_t bits = 0;
    std::memcpy(&bits, &value, 4);
    append(out, bits);
}
void attribute(std::string& out, const char* name, const char* type, const std::string& data) {
    out.append(name).push_back(0);
    out.append(type).push_back(0);
    append(out, data.size());
    out.append(data);
}
void double_attribute(std::string& out, const char* name, double value) {
    static_assert(sizeof(double) == 8 && std::numeric_limits<double>::is_iec559,
                  "OpenEXR requires IEEE 754 binary64");
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, 8);
    std::string data;
    append(data, bits, 8);
    attribute(out, name, "double", data);
}
}

bool Projection::valid() const noexcept {
    for (double value : inverse_z)
        if (!std::isfinite(value))
            return false;
    if (!std::isfinite(viewport_min) || !std::isfinite(viewport_max) || viewport_min < 0 ||
        viewport_max > 1 || viewport_min >= viewport_max)
        return false;
    const auto [a, b, c, d] = inverse_z;
    const double determinant = a * d - b * c;
    if (!std::isfinite(determinant) || determinant == 0)
        return false;
    // The projection cannot have a pole inside the visible 0..1 depth range.
    if (c != 0) {
        const double pole = -d / c;
        if (pole > 0 && pole < 1)
            return false;
    }
    for (double z : {0.0, 0.5, 1.0}) {
        const double denominator = c * z + d;
        if (denominator != 0) {
            const double value = -(a * z + b) / denominator;
            if (!std::isfinite(value) || value <= 0 || value > std::numeric_limits<float>::max())
                return false;
        }
    }
    return true;
}

bool Projection::linearize(double device_z, float& distance) const noexcept {
    return valid() && linearize_valid(*this, device_z, distance);
}

bool read_per_view_projection(const void* bytes, std::size_t size, std::uint32_t width,
                              std::uint32_t height, Projection& output) noexcept {
    if (!bytes || size < 464 || !dimensions(width, height))
        return false;
    const auto* data = static_cast<const unsigned char*>(bytes);
    const auto at = [data](std::size_t offset) { return f32(data + offset); };
    if (!near(at(320), 0) || !near(at(324), 0) || at(328) != width || at(332) != height ||
        !near(at(336) * width, 1) || !near(at(340) * height, 1))
        return false;
    Projection candidate{{at(256), at(260), at(264), at(268)}, at(368), at(372)};
    if (!candidate.valid())
        return false;
    // A depth-only inverse is insufficient for oblique projections whose Z/W
    // depends on pixel X/Y. Reject them until full inverse projection is used.
    for (unsigned index : {8u, 9u, 12u, 13u})
        if (!near(at(192 + index * 4), 0))
            return false;
    // Temporal antialiasing offsets clip X/Y by a subpixel amount without
    // changing Z/W. Keep those sampled offsets, bounded to one scene pixel
    // after normalizing by the perspective W scale. The other cross-axis and
    // translation entries remain structural zeros.
    for (unsigned index : {1u, 3u, 4u, 7u})
        if (!near(at(192 + index * 4), 0))
            return false;
    const double w_scale = std::abs(at(248));
    constexpr double jitter_rounding = 1 + 4 * std::numeric_limits<float>::epsilon();
    if (!std::isfinite(w_scale) || !std::isfinite(at(200)) || !std::isfinite(at(216)) ||
        std::abs(at(200)) * width > 2 * w_scale * jitter_rounding ||
        std::abs(at(216)) * height > 2 * w_scale * jitter_rounding)
        return false;
    const double scale_x = at(192), scale_y = at(212);
    if (!std::isfinite(scale_x) || scale_x == 0 || !std::isfinite(scale_y) || scale_y == 0)
        return false;
    const double p[] = {at(232), at(236), at(248), at(252)};
    const auto& q = candidate.inverse_z;
    if (!near(p[0] * q[0] + p[1] * q[2], 1) || !near(p[0] * q[1] + p[1] * q[3], 0) ||
        !near(p[2] * q[0] + p[3] * q[2], 0) || !near(p[2] * q[1] + p[3] * q[3], 1))
        return false;
    if (!near(at(140), 0) || !near(at(156), 0) || !near(at(172), 0) || !near(at(188), 1))
        return false;
    // Three orthonormal view axes, consistent with the separately supplied
    // up/forward and camera position. This rejects unrelated 640-byte buffers.
    for (unsigned axis = 0; axis < 3; ++axis) {
        double origin = at(176 + axis * 4);
        for (unsigned component = 0; component < 3; ++component)
            origin += at(128 + component * 16 + axis * 4) * at(416 + component * 4);
        if (!near(origin, 0, .05))
            return false;
        for (unsigned other = 0; other < 3; ++other) {
            double dot = 0;
            for (unsigned component = 0; component < 3; ++component)
                dot += at(128 + component * 16 + axis * 4) * at(128 + component * 16 + other * 4);
            if (!near(dot, axis == other ? 1.0 : 0.0, .001))
                return false;
        }
        if (!near(at(432 + axis * 4), at(132 + axis * 16), .001) ||
            !near(at(448 + axis * 4), -at(136 + axis * 16), .001))
            return false;
    }
    float low = 0, high = 0;
    if (!candidate.linearize(candidate.viewport_min, low) ||
        !candidate.linearize(candidate.viewport_max, high))
        return false;
    const double near_clip = std::min(low, high), far_clip = std::max(low, high);
    if (!near(at(376), near_clip, .001) ||
        !(std::isinf(far_clip) ? std::isinf(at(380)) && at(380) > 0
                               : near(at(380), far_clip, .001)))
        return false;
    // Retained calibration for the camera bundle: the values above are already
    // verified; copy them and keep every recorded number finite.
    for (unsigned index = 0; index < 16; ++index) {
        candidate.view_matrix[index] = at(128 + index * 4);
        candidate.projection_matrix[index] = at(192 + index * 4);
    }
    for (unsigned component = 0; component < 3; ++component) {
        candidate.position[component] = at(416 + component * 4);
        candidate.up[component] = at(432 + component * 4);
        candidate.forward[component] = at(448 + component * 4);
    }
    const auto finite = [](const auto& values) {
        for (double value : values)
            if (!std::isfinite(value))
                return false;
        return true;
    };
    if (!finite(candidate.view_matrix) || !finite(candidate.projection_matrix) ||
        !finite(candidate.position) || !finite(candidate.up) || !finite(candidate.forward))
        return false;
    candidate.near_clip = near_clip;
    candidate.far_clip = far_clip;
    candidate.view_valid = true;
    output = candidate;
    return true;
}

bool convert(const void* source, std::size_t source_bytes, std::size_t row_pitch,
             std::uint32_t width, std::uint32_t height, Format format, const Projection& projection,
             float* destination, std::size_t destination_count) noexcept {
    if (!source || !destination || !dimensions(width, height) || !projection.valid() ||
        (format != Format::d24s8 && format != Format::d32_float) ||
        destination_count < std::uint64_t(width) * height || row_pitch < std::uint64_t(width) * 4)
        return false;
    if (row_pitch > (std::numeric_limits<std::size_t>::max() - std::size_t(width) * 4) / height ||
        source_bytes < (height - 1) * row_pitch + std::size_t(width) * 4)
        return false;
    const auto* bytes = static_cast<const unsigned char*>(source);
    for (std::uint32_t y = 0; y < height; ++y) {
        for (std::uint32_t x = 0; x < width; ++x) {
            const auto bits = u32(bytes + y * row_pitch + x * 4);
            double device_z = 0;
            if (format == Format::d24s8) {
                device_z = double(bits & 0xffffffu) / 16777215.0;
            } else {
                float decoded = 0;
                std::memcpy(&decoded, &bits, 4);
                device_z = decoded;
            }
            if (!linearize_valid(projection, device_z, destination[std::size_t(y) * width + x]))
                return false;
        }
    }
    return true;
}

bool preview(const float* source, std::size_t count, double near_distance, double far_distance,
             std::uint8_t* destination) noexcept {
    if (!source || !destination || !std::isfinite(near_distance) || !std::isfinite(far_distance) ||
        near_distance < 0 || far_distance <= near_distance)
        return false;
    for (std::size_t i = 0; i < count; ++i) {
        if (!distance_value(source[i]))
            return false;
        const double value =
            std::clamp((far_distance - source[i]) / (far_distance - near_distance), 0.0, 1.0);
        destination[i] = static_cast<std::uint8_t>(std::lround(value * 255));
    }
    return true;
}

bool write_exr(std::ostream& output, const Frame& frame, const float* pixels, std::size_t count) {
    if (!dimensions(frame.width, frame.height) || !pixels ||
        count != std::uint64_t(frame.width) * frame.height || !frame.projection.valid() ||
        !std::isfinite(frame.replay_time) || frame.replay_time < 0 || output.tellp() != 0)
        return false;
    for (std::size_t i = 0; i < count; ++i)
        if (!distance_value(pixels[i]))
            return false;
    std::string header;
    append(header, 20000630);
    append(header, 2);
    std::string channels("Z", 2);
    append(channels, 2); // FLOAT, no conversion to HALF.
    append(channels, 0); // pLinear and three reserved bytes.
    append(channels, 1);
    append(channels, 1);
    channels.push_back(0);
    attribute(header, "channels", "chlist", channels);
    attribute(header, "compression", "compression", std::string(1, 0));
    std::string window;
    append(window, 0);
    append(window, 0);
    append(window, frame.width - 1);
    append(window, frame.height - 1);
    attribute(header, "dataWindow", "box2i", window);
    attribute(header, "displayWindow", "box2i", window);
    attribute(header, "lineOrder", "lineOrder", std::string(1, 0));
    std::string one, center;
    floating(one, 1);
    floating(center, 0);
    floating(center, 0);
    attribute(header, "pixelAspectRatio", "float", one);
    attribute(header, "screenWindowCenter", "v2f", center);
    attribute(header, "screenWindowWidth", "float", one);
    attribute(header, "dollySample", "string", std::to_string(frame.sample));
    attribute(header, "dollyDepthUnits", "string", "positive camera-axis game units");
    double_attribute(header, "dollyReplayTime", frame.replay_time);
    attribute(header, "dollyCapturePTS100ns", "string", std::to_string(frame.capture_pts_100ns));
    constexpr const char* names[] = {"dollyInverseZA", "dollyInverseZB", "dollyInverseZC",
                                     "dollyInverseZD"};
    for (unsigned i = 0; i < 4; ++i)
        double_attribute(header, names[i], frame.projection.inverse_z[i]);
    double_attribute(header, "dollyViewportMinZ", frame.projection.viewport_min);
    double_attribute(header, "dollyViewportMaxZ", frame.projection.viewport_max);
    header.push_back(0);
    const auto first_row = std::uint64_t(header.size()) + std::uint64_t(frame.height) * 8;
    const auto row_bytes = std::uint64_t(frame.width) * 4;
    for (std::uint32_t y = 0; y < frame.height; ++y)
        append(header, first_row + y * (row_bytes + 8), 8);
    output.write(header.data(), static_cast<std::streamsize>(header.size()));
    std::string row;
    row.reserve(static_cast<std::size_t>(row_bytes + 8));
    for (std::uint32_t y = 0; y < frame.height && output; ++y) {
        row.clear();
        append(row, y);
        append(row, row_bytes);
        for (std::uint32_t x = 0; x < frame.width; ++x)
            floating(row, pixels[std::size_t(y) * frame.width + x]);
        output.write(row.data(), static_cast<std::streamsize>(row.size()));
    }
    return bool(output);
}
}
