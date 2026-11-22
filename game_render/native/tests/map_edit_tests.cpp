#include "dolly_map_edit.hpp"

#include <d3d11.h>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {
int failures = 0;
void check(bool ok, const char* what) {
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    }
}
std::vector<unsigned char> table(unsigned records) {
    std::vector<unsigned char> data(records * 32);
    for (unsigned i = 0; i < records; ++i) {
        float box[8] = {float(i), -1.0f, 0.0f, 0.0f, float(i) + 1.0f, 1.0f, 2.0f, 0.0f};
        std::uint32_t words[8];
        std::memcpy(words, box, sizeof(box));
        words[3] = 0x1000 + i / 10;
        words[7] = i * 3;
        std::memcpy(data.data() + i * 32, words, 32);
    }
    return data;
}
} // namespace

int main() {
    using namespace dolly::map_edit;
    const unsigned bind = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS;
    const unsigned misc = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    auto data = table(100);
    const auto size = unsigned(data.size());
    check(looks_like_bounds(size, 32, D3D11_USAGE_DEFAULT, bind, misc, data.data()), "accepts table");
    check(!looks_like_bounds(size, 16, D3D11_USAGE_DEFAULT, bind, misc, data.data()), "stride");
    check(!looks_like_bounds(size, 32, D3D11_USAGE_IMMUTABLE, bind, misc, data.data()), "usage");
    check(!looks_like_bounds(size, 32, D3D11_USAGE_DEFAULT, D3D11_BIND_SHADER_RESOURCE, misc,
                             data.data()),
          "bind");
    check(!looks_like_bounds(size, 32, D3D11_USAGE_DEFAULT, bind, 0, data.data()), "structured");
    check(!looks_like_bounds(size, 32, D3D11_USAGE_DEFAULT, bind, misc, nullptr), "no data");
    check(!looks_like_bounds(32 * 10, 32, D3D11_USAGE_DEFAULT, bind, misc, data.data()), "tiny");
    auto inverted = data;
    float big = 50.0f;
    std::memcpy(inverted.data() + 5 * 32, &big, 4); // min x above max x
    check(!looks_like_bounds(size, 32, D3D11_USAGE_DEFAULT, bind, misc, inverted.data()), "inverted box");
    auto unordered = data;
    std::uint32_t low = 0;
    std::memcpy(unordered.data() + 7 * 32 + 28, &low, 4);
    check(!looks_like_bounds(size, 32, D3D11_USAGE_DEFAULT, bind, misc, unordered.data()), "order");

    auto edited = data;
    const std::uint32_t ids[] = {3, 99, 100, 5000};
    check(hide_records(edited.data(), 100, ids, 4) == 2, "hides only valid ids");
    float box[8];
    std::memcpy(box, edited.data() + 3 * 32, 32);
    check(box[0] > 1.0e7f && box[4] > 1.0e7f && box[2] > 1.0e7f && box[6] > 1.0e7f, "box moved away");
    check(std::memcmp(edited.data() + 3 * 32 + 12, data.data() + 3 * 32 + 12, 4) == 0, "aggregate word kept");
    check(std::memcmp(edited.data() + 3 * 32 + 28, data.data() + 3 * 32 + 28, 4) == 0, "first word kept");
    check(std::memcmp(edited.data() + 4 * 32, data.data() + 4 * 32, 32) == 0, "others untouched");
    // Class flag table: two agreeing visibility tests resolve to one table.
    std::vector<unsigned char> code(256, 0x90);
    const unsigned char test_a[] = {0x0f, 0xb6, 0x87, 0xb3, 0x00, 0x00, 0x00, 0x48, 0x8d, 0x0d,
                                    0, 0, 0, 0, 0xf6, 0x04, 0x08, 0x08};
    auto place = [&](std::size_t at, std::int32_t target_rva, std::uint64_t code_rva) {
        std::memcpy(code.data() + at, test_a, sizeof(test_a));
        const std::int32_t rel = std::int32_t(target_rva - std::int64_t(code_rva + at + 3 + 11));
        std::memcpy(code.data() + at + 3 + 7, &rel, 4);
    };
    place(10, 0x9000, 0x1000);
    check(find_class_table(code.data(), 0x1000, code.size()) == 0, "one match is not enough");
    place(100, 0x9000, 0x1000);
    check(find_class_table(code.data(), 0x1000, code.size()) == 0x9000, "two agreeing matches");
    place(180, 0x9100, 0x1000);
    check(find_class_table(code.data(), 0x1000, code.size()) == 0, "disagreeing matches");
    const float transform[12] = {1, 0, 0, -904, 0, 1, 0, 1648, 0, 0, 1, 560};
    const float near_origin[3] = {-904.2f, 1648.0f, 560.3f};
    const float far_origin[3] = {-904.0f, 1650.0f, 560.0f};
    check(origin_matches(transform, near_origin), "origin within half a unit");
    check(!origin_matches(transform, far_origin), "origin elsewhere");
    if (!failures)
        std::puts("map edit tests passed");
    return failures ? 1 : 0;
}
