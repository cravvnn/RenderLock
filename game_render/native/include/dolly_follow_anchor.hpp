#pragma once
#include <array>
#include <cmath>
#include <cstdint>

namespace dolly {
// Stock descriptor: angles, world anchor, pivot, normal/near offsets, FOV.
using FollowDescriptor = std::array<float, 16>;
inline bool follow_anchor_flags(std::uint32_t flags) noexcept {
    // Pending denotes retained restoration data, not an operation in flight.
    return !(flags & ~15u) && (flags & 3u) == 3u;
}
// Repair only the intermediate zero-anchor temporary. Independent offsets and
// angles remain stock; the caller and target identity are checked separately.
inline bool follow_anchor_copy(float weight, bool owned,
                               const FollowDescriptor& a, const FollowDescriptor& b,
                               const std::array<float, 3>& anchor,
                               FollowDescriptor& corrected) noexcept {
    if (!owned || !std::isfinite(weight) || !(weight > 0 && weight < 1))
        return false;
    for (unsigned i = 0; i < a.size(); ++i)
        if (!std::isfinite(a[i]) || !std::isfinite(b[i]))
            return false;
    bool nonzero = false;
    for (unsigned i = 0; i < 3; ++i) {
        if (!std::isfinite(anchor[i]) || a[3+i] != 0 || b[3+i] != anchor[i])
            return false;
        nonzero |= anchor[i] != 0;
    }
    if (!nonzero)
        return false;
    corrected = a;
    for (unsigned i = 0; i < 3; ++i)
        corrected[3+i] = anchor[i];
    return true;
}
}
