// Tests for the wildcard signature scanner used to fall back to an
// unlisted-but-byte-identical game build.
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <vector>

#include "dolly_pattern_scan.hpp"

static int failures = 0;

static void check(bool condition, const char* message) {
    if (!condition) {
        std::printf("FAIL: %s\n", message);
        ++failures;
    }
}

static std::size_t scan(const std::vector<unsigned char>& data,
                        const std::vector<unsigned char>& signature,
                        const std::vector<unsigned char>& mask, std::size_t& offset) {
    return dolly::find_pattern(data.data(), data.size(), signature.data(), mask.data(),
                               signature.size(), offset);
}

int main() {
    {
        const std::vector<unsigned char> data{0x00, 0x11, 0x22, 0x33, 0x44, 0x55};
        const std::vector<unsigned char> signature{0x11, 0x22, 0x33};
        const std::vector<unsigned char> mask{1, 1, 1};
        std::size_t offset = 0;
        check(scan(data, signature, mask, offset) == 1, "exact unique match counts once");
        check(offset == 1, "exact unique match offset");
    }
    {
        const std::vector<unsigned char> data{0xAA, 0x11, 0x00, 0x33, 0xBB, 0x11, 0x99, 0x33};
        const std::vector<unsigned char> signature{0x11, 0x22, 0x33};
        const std::vector<unsigned char> mask{1, 0, 1};
        std::size_t offset = 0;
        check(scan(data, signature, mask, offset) == 2, "two wildcard matches are ambiguous");
        check(offset == 1, "first wildcard match offset is retained");
    }
    {
        const std::vector<unsigned char> data{0xAA, 0x11, 0x00, 0x33, 0xBB};
        const std::vector<unsigned char> signature{0x11, 0x22, 0x33};
        const std::vector<unsigned char> mask{1, 0, 1};
        std::size_t offset = 7;
        check(scan(data, signature, mask, offset) == 1, "single wildcard match");
        check(offset == 1, "single wildcard match offset");
    }
    {
        const std::vector<unsigned char> data{0x01, 0x02, 0x03};
        const std::vector<unsigned char> signature{0x01, 0x02, 0x03, 0x04};
        const std::vector<unsigned char> mask{1, 1, 1, 1};
        std::size_t offset = 9;
        check(scan(data, signature, mask, offset) == 0, "short data cannot match");
        check(offset == 0, "short data resets offset");
    }
    {
        const std::vector<unsigned char> data{0x01, 0x02, 0x03};
        const std::vector<unsigned char> signature{};
        const std::vector<unsigned char> mask{};
        std::size_t offset = 9;
        check(dolly::find_pattern(data.data(), data.size(), nullptr, nullptr, 0, offset) == 0,
              "empty signature is rejected");
    }
    {
        const std::vector<unsigned char> data{0x7F, 0x7F, 0x7F};
        const std::vector<unsigned char> signature{0x7E, 0x7E, 0x7E};
        const std::vector<unsigned char> mask{1, 1, 1};
        std::size_t offset = 0;
        check(scan(data, signature, mask, offset) == 0, "no match returns zero");
    }

    if (failures) {
        std::printf("%d pattern scanner test(s) failed\n", failures);
        return 1;
    }
    std::printf("pattern scanner tests passed\n");
    return 0;
}
