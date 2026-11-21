#include "dolly_depth.hpp"
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}
void close_to(float actual, double expected, const char* message) {
    require(std::abs(actual - expected) <= std::abs(expected) * 1e-6, message);
}
std::uint32_t read32(const std::string& data, std::size_t at) {
    require(at + 4 <= data.size(), "Truncated EXR integer");
    std::uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i)
        value |= std::uint32_t(static_cast<unsigned char>(data[at + i])) << (8 * i);
    return value;
}
}

int main(int argc, char** argv) {
    try {
        using namespace dolly::depth;
        const Projection captured{{0, -1, 0.1428571492433548, 0}, 0, 1};
        require(captured.valid(), "Captured reverse projection rejected");
        float z = 0;
        require(captured.linearize(1, z), "Near clip conversion failed");
        close_to(z, 7, "Captured near clip differs from 7 game units");
        require(captured.linearize(.001, z), "Distant depth conversion failed");
        close_to(z, 7000, "Distant depth was clipped or normalized");
        require(captured.linearize(0, z) && std::isinf(z) && z > 0,
                "Infinite far plane must remain positive infinity");
        // Finite forward-Z projection with near=2, far=200.
        const Projection forward{{0, -400, -198, 200}, 0, 1};
        require(forward.linearize(0, z), "Forward near clip rejected");
        close_to(z, 2, "Forward near plane incorrect");
        require(forward.linearize(1, z), "Forward far clip rejected");
        close_to(z, 200, "Forward far plane incorrect");
        auto viewport = captured;
        viewport.viewport_min = .25;
        viewport.viewport_max = .75;
        require(viewport.linearize(.5, z), "Viewport depth normalization failed");
        close_to(z, 14, "Viewport depth range ignored");
        require(!viewport.linearize(.2, z), "Out-of-viewport depth accepted");
        require(!captured.linearize(std::nan(""), z), "NaN depth accepted");
        require(!captured.linearize(std::numeric_limits<double>::infinity(), z),
                "Infinite device depth accepted");
        require(!Projection{}.valid(), "Empty calibration accepted");
        require(!Projection{{0, -1, 1, -.5}, 0, 1}.valid(),
                "Projection with an interior pole accepted");
        require(!Projection{{0, 1, 1, 0}, 0, 1}.valid(), "Negative scene distance accepted");
        require(!Projection{{0, -1, 1, 0}, 1, 0}.valid(), "Reversed viewport range accepted");

        std::array<unsigned char, 640> per_view{};
        const auto put = [&per_view](unsigned offset, float value) {
            std::uint32_t bits = 0;
            std::memcpy(&bits, &value, 4);
            for (unsigned i = 0; i < 4; ++i)
                per_view[offset + i] = static_cast<unsigned char>(bits >> (i * 8));
        };
        for (unsigned offset : {128u, 148u, 168u, 188u, 192u, 212u, 372u, 436u})
            put(offset, 1);
        put(236, 7);
        put(248, -1);
        put(260, -1);
        put(264, 1.0f / 7);
        put(328, 17);
        put(332, 3);
        put(336, 1.0f / 17);
        put(340, 1.0f / 3);
        put(376, 7);
        put(380, std::numeric_limits<float>::infinity());
        put(456, -1);
        Projection parsed;
        require(read_per_view_projection(per_view.data(), per_view.size(), 17, 3, parsed),
                "Valid per-view layout rejected");
        require(parsed.view_valid, "Complete calibration did not retain the view");
        require(parsed.view_matrix[0] == 1 && parsed.view_matrix[5] == 1 &&
                    parsed.view_matrix[10] == 1 && parsed.view_matrix[15] == 1,
                "Retained view matrix changed");
        require(parsed.projection_matrix[0] == 1 && parsed.projection_matrix[5] == 1 &&
                    parsed.projection_matrix[11] == 7 && parsed.projection_matrix[14] == -1,
                "Retained projection matrix changed");
        require(parsed.position[0] == 0 && parsed.position[1] == 0 && parsed.position[2] == 0 &&
                    parsed.up[1] == 1 && parsed.forward[2] == -1,
                "Retained camera vectors changed");
        close_to(static_cast<float>(parsed.near_clip), 7, "Retained near clip changed");
        require(std::isinf(parsed.far_clip), "Retained far clip must stay infinite");
        require(!read_per_view_projection(per_view.data(), per_view.size(), 16, 3, parsed),
                "Projection for a different viewport accepted");
        const auto saved_per_view = per_view;
        // A rolled, translated camera with temporal clip-X/Y offsets is still
        // non-oblique: Z/W and therefore linear depth do not change. Exercise
        // realistic scene sizes and non-unit homogeneous projection scales.
        const auto jittered_view = [&](unsigned width, unsigned height, float scale, float pixels_x,
                                       float pixels_y) {
            per_view = saved_per_view;
            const float cosine = static_cast<float>(std::cos(37.9796998 * 3.141592653589793 / 180));
            const float sine = static_cast<float>(std::sin(37.9796998 * 3.141592653589793 / 180));
            constexpr float x = 6815.65f, y = -195.05f, z = 1094.457f;
            put(128, cosine);
            put(132, -sine);
            put(144, sine);
            put(148, cosine);
            put(176, -x * cosine - y * sine);
            put(180, x * sine - y * cosine);
            put(184, -z);
            put(416, x);
            put(420, y);
            put(424, z);
            put(432, -sine);
            put(436, cosine);
            put(192, .75f * scale);
            put(212, 1.8f * scale); // Aspect 2.4.
            put(236, 7 * scale);
            put(248, -scale);
            put(260, -1 / scale);
            put(264, 1 / (7 * scale));
            put(328, static_cast<float>(width));
            put(332, static_cast<float>(height));
            put(336, 1.0f / width);
            put(340, 1.0f / height);
            const std::array<float, 2> offsets{2 * pixels_x * scale / width,
                                               2 * pixels_y * scale / height};
            put(200, offsets[0]);
            put(216, offsets[1]);
            return offsets;
        };
        for (const auto size :
             {std::array<unsigned, 2>{2560, 1440}, std::array<unsigned, 2>{1706, 960}}) {
            for (float scale : {.25f, 1.0f, 4.0f}) {
                for (const auto pixels :
                     {std::array<float, 2>{-1, 1}, std::array<float, 2>{.5f, -.5f},
                      std::array<float, 2>{0, 0}}) {
                    const auto offsets =
                        jittered_view(size[0], size[1], scale, pixels[0], pixels[1]);
                    require(read_per_view_projection(per_view.data(), per_view.size(), size[0],
                                                     size[1], parsed),
                            "Bounded temporal projection jitter or rolled view rejected");
                    require(parsed.projection_matrix[2] == offsets[0] &&
                                parsed.projection_matrix[6] == offsets[1],
                            "Temporal projection offsets were discarded or rounded");
                    require(parsed.linearize(.5, z), "Temporal jitter broke linear depth");
                    close_to(z, 14, "Temporal jitter changed linear depth");
                    const auto& p = parsed.projection_matrix;
                    const double view_z = -14, clip_w = p[14] * view_z + p[15];
                    const double before_x = (p[0] * 3 / clip_w + 1) * size[0] / 2;
                    const double after_x = ((p[0] * 3 + p[2] * view_z) / clip_w + 1) * size[0] / 2;
                    const double before_y = (1 - p[5] * -2 / clip_w) * size[1] / 2;
                    const double after_y = (1 - (p[5] * -2 + p[6] * view_z) / clip_w) * size[1] / 2;
                    require(std::abs(after_x - before_x + pixels[0]) < 1e-6 &&
                                std::abs(after_y - before_y - pixels[1]) < 1e-6,
                            "Retained projection does not preserve the sampled pixel offset");
                }
                for (unsigned axis = 0; axis < 2; ++axis) {
                    for (float sign : {-1.0f, 1.0f}) {
                        jittered_view(size[0], size[1], scale, axis == 0 ? sign * 1.01f : 0,
                                      axis == 1 ? sign * 1.01f : 0);
                        require(!read_per_view_projection(per_view.data(), per_view.size(), size[0],
                                                          size[1], parsed),
                                "Projection offset larger than one scene pixel accepted");
                    }
                }
            }
        }
        for (unsigned offset : {196u, 204u, 208u, 220u, 224u, 228u, 240u, 244u}) {
            jittered_view(2560, 1440, 1, .5f, -.5f);
            put(offset, .001f);
            require(!read_per_view_projection(per_view.data(), per_view.size(), 2560, 1440, parsed),
                    "Temporal jitter allowance admitted oblique or cross-axis projection");
        }
        for (unsigned offset : {200u, 216u}) {
            for (float value : {std::numeric_limits<float>::infinity(),
                                std::numeric_limits<float>::quiet_NaN()}) {
                jittered_view(2560, 1440, 1, 0, 0);
                put(offset, value);
                require(
                    !read_per_view_projection(per_view.data(), per_view.size(), 2560, 1440, parsed),
                    "Non-finite temporal projection offset accepted");
            }
        }
        for (unsigned offset : {128u, 140u, 188u, 196u, 200u, 204u, 208u, 216u, 220u, 224u, 236u,
                                264u, 328u, 336u, 376u, 436u, 456u}) {
            per_view = saved_per_view;
            put(offset, 123);
            require(!read_per_view_projection(per_view.data(), per_view.size(), 17, 3, parsed),
                    "Corrupt or unrelated per-view buffer accepted");
        }
        for (unsigned offset : {192u, 212u}) {
            per_view = saved_per_view;
            put(offset, 0);
            require(!read_per_view_projection(per_view.data(), per_view.size(), 17, 3, parsed),
                    "Zero projection scale accepted");
        }
        per_view = saved_per_view;
        put(128, std::numeric_limits<float>::infinity());
        require(!read_per_view_projection(per_view.data(), per_view.size(), 17, 3, parsed),
                "Non-finite view matrix accepted");
        per_view = saved_per_view;

        // D24 little-endian words. Stencil is deliberately nonzero and each
        // row has four poison padding bytes that must never become a pixel.
        const std::array<unsigned char, 24> packed = {255, 255, 255, 2,  0,  0,  128, 255,
                                                      71,  72,  73,  74, 0,  0,  0,   7,
                                                      0,   0,   64,  2,  71, 72, 73,  74};
        std::array<float, 4> decoded{};
        require(convert(packed.data(), packed.size(), 12, 2, 2, Format::d24s8, captured,
                        decoded.data(), decoded.size()),
                "Padded D24 decode failed");
        close_to(decoded[0], 7, "Stencil contaminated depth");
        close_to(decoded[1], 14, "First row order changed");
        require(std::isinf(decoded[2]), "Far plane lost in D24 decode");
        close_to(decoded[3], 28, "Second row padding sampled");
        require(!convert(packed.data(), 19, 12, 2, 2, Format::d24s8, captured, decoded.data(), 4),
                "Short source read accepted");
        require(!convert(packed.data(), packed.size(), 7, 2, 2, Format::d24s8, captured,
                         decoded.data(), 4),
                "Short pitch accepted");
        require(!convert(packed.data(), packed.size(), 12, 2, 2, Format::d24s8, captured,
                         decoded.data(), 3),
                "Short output accepted");
        require(!convert(packed.data(), packed.size(), std::numeric_limits<std::size_t>::max(), 2,
                         2, Format::d24s8, captured, decoded.data(), 4),
                "Overflowing row span accepted");
        const std::array<unsigned char, 8> d32 = {0, 0, 128, 63, 0, 0, 0, 63};
        require(convert(d32.data(), 8, 8, 2, 1, Format::d32_float, captured, decoded.data(), 4),
                "D32 float decode failed");
        close_to(decoded[1], 14, "D32 bits were interpreted as UNORM");
        const std::array<unsigned char, 4> nan32 = {0, 0, 192, 127};
        require(!convert(nan32.data(), 4, 4, 1, 1, Format::d32_float, captured, decoded.data(), 4),
                "NaN D32 sample accepted");

        const std::array<float, 6> master = {
            7, 14, 70000.125f, 1e20f, std::numeric_limits<float>::infinity(), 28};
        std::array<unsigned char, 6> gray{};
        require(preview(master.data(), master.size(), 7, 21, gray.data()), "Preview failed");
        require(gray[0] == 255 && gray[1] == 128 && gray[2] == 0 && gray[4] == 0,
                "Preview range mapping incorrect");
        require(master[2] == 70000.125f && master[3] == 1e20f,
                "Preview altered the lossless master");
        require(!preview(master.data(), master.size(), 21, 7, gray.data()),
                "Invalid preview range accepted");

        Frame frame{3, 2, 9007199254740993ULL, 123.125, captured};
        std::ostringstream output(std::ios::out | std::ios::binary);
        require(write_exr(output, frame, master.data(), master.size()), "EXR write failed");
        const auto bytes = output.str();
        require(read32(bytes, 0) == 20000630 && read32(bytes, 4) == 2, "Invalid EXR magic/version");
        std::size_t at = 8;
        bool channel = false, sample = false;
        while (bytes.at(at)) {
            const auto end = bytes.find('\0', at);
            const auto name = bytes.substr(at, end - at);
            at = bytes.find('\0', end + 1) + 1;
            const auto size = read32(bytes, at);
            at += 4;
            if (name == "channels") {
                channel = true;
                require(bytes.substr(at, 2) == std::string("Z", 2) && read32(bytes, at + 2) == 2,
                        "Depth is not a 32-bit float Z channel");
            }
            if (name == "dollySample") {
                sample = true;
                require(bytes.substr(at, size) == "9007199254740993", "Sample ID lost precision");
            }
            at += size;
        }
        require(channel && sample, "Depth metadata missing");
        ++at;
        for (unsigned y = 0; y < 2; ++y) {
            const auto offset = std::uint64_t(read32(bytes, at + y * 8)) |
                                (std::uint64_t(read32(bytes, at + y * 8 + 4)) << 32);
            require(read32(bytes, offset) == y && read32(bytes, offset + 4) == 12,
                    "EXR scanline table points to wrong data");
            for (unsigned x = 0; x < 3; ++x) {
                const auto bits = read32(bytes, offset + 8 + x * 4);
                std::uint32_t expected = 0;
                std::memcpy(&expected, &master[y * 3 + x], 4);
                require(bits == expected, "EXR changed a float sample");
            }
        }
        std::ostringstream invalid;
        require(!write_exr(invalid, frame, master.data(), 5) && invalid.str().empty(),
                "Invalid frame wrote a partial header");
        std::ostringstream failed;
        failed.setstate(std::ios::badbit);
        require(!write_exr(failed, frame, master.data(), master.size()), "Stream failure ignored");
        if (argc == 2) {
            std::ofstream fixture(argv[1], std::ios::out | std::ios::binary);
            require(write_exr(fixture, frame, master.data(), master.size()),
                    "Fixture write failed");
        }
        if (argc == 3 && std::string(argv[1]) == "--per-view") {
            std::ifstream capture(argv[2], std::ios::binary);
            const std::vector<char> raw((std::istreambuf_iterator<char>(capture)), {});
            require(read_per_view_projection(raw.data(), raw.size(), 2560, 1440, parsed),
                    "Captured per-view buffer rejected");
            require(parsed.linearize(.5, z), "Captured calibration could not convert depth");
            close_to(z, 14, "Captured calibration changed");
        }
        std::puts("Depth projection, row pitch, preview and float EXR tests passed.");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
