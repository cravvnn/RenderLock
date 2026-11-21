#pragma once
#include <cstdint>

namespace dolly {
// Suppressing a whole draw is only safe for one identified instance. A batch
// can contain other players, so leave it untouched rather than guessing.
inline bool hide_instance_index(unsigned instances, unsigned stride, unsigned base,
                                std::uint64_t element, unsigned first, unsigned limit,
                                unsigned& index) noexcept {
    if (instances != 1 || stride != 4 || element > 0xffffffffULL)
        return false;
    const std::uint64_t offset = std::uint64_t(base) + element + std::uint64_t(first) * 4;
    if (offset > 0xffffffffULL || offset % 4 || offset / 4 >= limit)
        return false;
    index = unsigned(offset / 4);
    return true;
}

inline bool hide_owner_matches(std::uint64_t entry, std::uint32_t target,
                               std::uint64_t frame) noexcept {
    const auto tag = std::uint32_t(entry >> 32);
    const auto now = std::uint32_t(frame);
    return target && std::uint32_t(entry) == target &&
           (tag == now || std::uint32_t(tag + 1) == now);
}
} // namespace dolly
