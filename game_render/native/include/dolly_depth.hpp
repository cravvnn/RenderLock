#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <iosfwd>

namespace dolly::depth {
// The inverse projection's lower-right 2x2, as supplied by the scene's
// PerViewConstantBuffer_t. It must be sampled with the depth, never guessed
// from FOV or a different frame's camera status.
struct Projection {
    std::array<double, 4> inverse_z{};
    double viewport_min = 0, viewport_max = 1;
    // Retained from the same validated buffer when the full layout parsed: the
    // world->view affine matrix (row-vector convention, row-major), the camera
    // position/up/forward vectors, the clip-from-view projection matrix
    // (column-vector convention, row-major) and the linearized clip range.
    // view_valid stays false for a hand-built projection-only record; see
    // docs/CAMERA_BUNDLE.md for the stored conventions.
    std::array<double, 16> view_matrix{};
    std::array<double, 16> projection_matrix{};
    std::array<double, 3> position{}, up{}, forward{};
    double near_clip = 0, far_clip = 0;
    bool view_valid = false;
    bool valid() const noexcept;
    // Positive camera-axis distance in game units; +infinity means the far
    // endpoint of an infinite projection. False means invalid calibration/data.
    bool linearize(double device_z, float& distance) const noexcept;
};

enum class Format { d24s8, d32_float };
// Reviewed PerViewConstantBuffer_t layout. Checks the viewport, projection
// inverse, camera basis and view transform; a buffer's size/slot alone is not
// evidence that it contains scene calibration. Invoke only for the supported
// renderer profile and depth source, not as an arbitrary GPU-buffer scanner.
bool read_per_view_projection(const void* bytes, std::size_t size, std::uint32_t width,
                              std::uint32_t height, Projection& output) noexcept;
// No allocation or GPU work. Row pitch is bytes; output is tightly packed,
// top-down. Invalid input may leave a partial output, which callers must discard.
bool convert(const void* source, std::size_t source_bytes, std::size_t row_pitch,
             std::uint32_t width, std::uint32_t height, Format format, const Projection& projection,
             float* destination, std::size_t destination_count) noexcept;
bool preview(const float* source, std::size_t count, double near_distance, double far_distance,
             std::uint8_t* destination) noexcept;

struct Frame {
    std::uint32_t width = 0, height = 0;
    std::uint64_t sample = 0;
    double replay_time = 0;
    Projection projection;
    std::uint64_t capture_pts_100ns = 0;
};
// Standard single-part scanline OpenEXR, one 32-bit FLOAT Z channel, without
// lossy conversion or compression. Call only on an output worker. The stream
// must be empty at offset zero; file creation/finalization belongs to its owner.
bool write_exr(std::ostream& output, const Frame& frame, const float* pixels, std::size_t count);
}
