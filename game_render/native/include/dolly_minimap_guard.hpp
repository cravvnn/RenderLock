#pragma once
#include <cstdint>

namespace dolly {
struct MinimapIconLayout {
    std::uintptr_t vtable;
    std::uintptr_t type_offset;
    std::uintptr_t range_offset;
};

// A current capture-point/ability icon can survive reconstruction without its
// CastRange child. Do not enter the stock update's unconditional dereference.
// All other entries, callers, objects and game modes keep the stock behavior.
template <class Read, class Replay>
bool incomplete_minimap_icon(bool reviewed_caller, const MinimapIconLayout& layout,
                             std::uintptr_t self, std::uintptr_t icon, Read read,
                             Replay replay) noexcept {
    std::uintptr_t table = 0, range = 1;
    std::uint32_t type = 0;
    return reviewed_caller && self && icon && layout.vtable && read(self, table) &&
           table == layout.vtable && read(icon + layout.type_offset, type) &&
           (type == 45 || type == 88 || type == 92) && read(icon + layout.range_offset, range) &&
           range == 0 && replay();
}
}
