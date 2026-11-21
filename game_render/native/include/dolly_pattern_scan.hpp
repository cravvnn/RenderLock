// Portable wildcard byte-pattern scanner shared by the native bridge and tests.
#pragma once

#include <cstddef>

namespace dolly {

// Scan ``data`` for a signature with an explicit mask (1 = byte must match,
// 0 = wildcard). Writes the first match offset when it is unique.
//
// Returns the total number of matches. Callers must treat any value other than
// 1 as a failure: this scanner is used to accept an unrecognized game build,
// so an ambiguous match must never be silently approved.
inline std::size_t find_pattern(const unsigned char* data, std::size_t size,
                                const unsigned char* signature, const unsigned char* mask,
                                std::size_t length, std::size_t& offset) {
    offset = 0;
    if (!data || !signature || !mask || !length || size < length)
        return 0;
    std::size_t matches = 0;
    const std::size_t last = size - length;
    for (std::size_t i = 0; i <= last; ++i) {
        bool matched = true;
        for (std::size_t j = 0; j < length; ++j) {
            if (mask[j] && data[i + j] != signature[j]) {
                matched = false;
                break;
            }
        }
        if (matched) {
            ++matches;
            if (matches > 1)
                return matches;
            offset = i;
        }
    }
    return matches;
}

}  // namespace dolly
