#include "dolly_object_runtime.hpp"
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace dolly;
using namespace dolly::object_runtime;

void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}

EditorObjectItem item(std::uint32_t shape, float scale = 1.0f) {
    EditorObjectItem it{};
    it.shape = shape;
    it.scale = scale;
    return it;
}

int main() {
    try {
        ObjectSegment segs[kMaxSegmentsPerObject];
        std::size_t written = 0;

        // Library index -> shape, append-only order.
        require(shape_for_index(0) == Shape::Box, "marker is a box");
        require(shape_for_index(1) == Shape::Arrow, "arrow shape");
        require(shape_for_index(2) == Shape::Sphere, "sphere shape");
        require(shape_for_index(4) == Shape::Cylinder, "barrel is a cylinder");
        require(shape_for_index(99) == Shape::Invalid, "unknown index invalid");

        // Every library shape builds a bounded, non-empty wireframe.
        for (std::uint32_t shape = 0; shape < 9; ++shape) {
            auto it = item(shape);
            require(build_proxy(it, segs, kMaxSegmentsPerObject, written), "shape builds");
            require(written > 0 && written <= kMaxSegmentsPerObject, "segments bounded");
        }

        // A box is exactly 12 edges.
        require(build_proxy(item(0), segs, kMaxSegmentsPerObject, written) && written == 12,
                "box has twelve edges");

        // Scale multiplies extent: a 2x box edge spans 2x the local size.
        ObjectSegment one[kMaxSegmentsPerObject], two[kMaxSegmentsPerObject];
        std::size_t n1 = 0, n2 = 0;
        require(build_proxy(item(0, 1.0f), one, kMaxSegmentsPerObject, n1), "unit box");
        require(build_proxy(item(0, 2.0f), two, kMaxSegmentsPerObject, n2), "double box");
        double span1 = std::abs(one[0].b[2] - one[0].a[2]);
        double span2 = std::abs(two[0].b[2] - two[0].a[2]);
        require(std::abs(span2 - 2 * span1) < 1e-6, "scale doubles extent");

        // Position offsets every vertex: the centroid of all endpoints equals
        // the placed origin (the wireframe is symmetric about it).
        auto moved = item(0);
        moved.position[0] = 100.0f; moved.position[1] = 200.0f; moved.position[2] = 300.0f;
        require(build_proxy(moved, segs, kMaxSegmentsPerObject, written), "positioned box");
        double cx = 0, cy = 0, cz = 0;
        for (std::size_t i = 0; i < written; ++i) {
            cx += segs[i].a[0] + segs[i].b[0];
            cy += segs[i].a[1] + segs[i].b[1];
            cz += segs[i].a[2] + segs[i].b[2];
        }
        const double count = 2.0 * written;
        require(std::abs(cx / count - 100.0) < 1e-3 && std::abs(cy / count - 200.0) < 1e-3 &&
                    std::abs(cz / count - 300.0) < 1e-3,
                "position offsets the wireframe");

        // Fail-closed inputs.
        auto bad_shape = item(9);
        require(!build_proxy(bad_shape, segs, kMaxSegmentsPerObject, written), "bad shape rejected");
        auto bad_scale = item(0, 0.0f);
        require(!build_proxy(bad_scale, segs, kMaxSegmentsPerObject, written), "bad scale rejected");
        auto huge_scale = item(0, 100.0f);
        require(!build_proxy(huge_scale, segs, kMaxSegmentsPerObject, written), "huge scale rejected");
        auto nan_pos = item(0);
        nan_pos.position[1] = std::numeric_limits<float>::quiet_NaN();
        require(!build_proxy(nan_pos, segs, kMaxSegmentsPerObject, written), "nonfinite position");
        require(written == 0, "rejected build writes nothing");

        // Capacity is respected, never exceeded.
        ObjectSegment tiny[4];
        auto sphere = item(2);
        build_proxy(sphere, tiny, 4, written);
        require(written <= 4, "capacity respected");

        // Config validation rejects malformed/torn blocks.
        EditorObjectConfig config{};
        std::memcpy(config.magic, "DLYOBJ01", 8);
        config.abi = kEditorObjectAbi;
        config.flags = 1;
        config.count = 2;
        config.selected = 1;
        config.distance = 600.0;
        config.items[0] = item(0);
        config.items[1] = item(4);
        require(valid_editor_object_config(config), "valid config accepted");
        config.selected = 5;
        require(!valid_editor_object_config(config), "out-of-range selection rejected");
        config.selected = 1;
        config.items[0].shape = 40;
        require(!valid_editor_object_config(config), "out-of-range shape rejected");
        config.items[0] = item(0);
        config.distance = 999999.0;
        require(!valid_editor_object_config(config), "out-of-range distance rejected");
        config.distance = 600.0;
        config.items[0].scale = std::numeric_limits<float>::quiet_NaN();
        require(!valid_editor_object_config(config), "nonfinite item rejected");

        std::cout << "Object runtime tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
