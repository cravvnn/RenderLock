#pragma once
#include "dolly_editor.hpp"
#include <array>
#include <cstddef>
#include <cstdint>

namespace dolly {
namespace object_runtime {

// Pure proxy-geometry generator for the Object Picker. The editor wire layout
// and validation live in dolly_editor.hpp (EditorObjectConfig); this unit only
// turns one placed item into world-space wireframe segments. It has no engine
// dependency and calls nothing external, so it works while the particle-table
// (B) and model/entity (C) research is unresolved. A later real-model backend
// replaces only this generator behind the same ObjectSegment interface.

constexpr std::size_t kMaxSegmentsPerObject = 32;

struct ObjectSegment {
    std::array<double, 3> a{};
    std::array<double, 3> b{};
};

// Which proxy shape a library index maps to. Mirrors dolly/object_library.py
// PROXY_SHAPES, in append-only library order.
enum class Shape : std::uint32_t { Box = 0, Sphere = 1, Cylinder = 2, Arrow = 3, Invalid = 4 };

Shape shape_for_index(std::uint32_t library_index) noexcept;

// Generate segments for one item. ``written`` is set to the number produced.
// Returns false (with ``written`` = 0) for an unknown shape, non-finite
// transform, or out-of-range scale. Never writes more than ``capacity``.
bool build_proxy(const EditorObjectItem& item, ObjectSegment* out, std::size_t capacity,
                 std::size_t& written) noexcept;

} // namespace object_runtime
} // namespace dolly
