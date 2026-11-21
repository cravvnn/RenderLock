#include "dolly_path.hpp"

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}

void u32(std::vector<std::uint8_t>& data, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        data.push_back(std::uint8_t(value >> (i * 8)));
}

void number(std::vector<std::uint8_t>& data, double value) {
    std::uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned i = 0; i < 8; ++i)
        data.push_back(std::uint8_t(bits >> (i * 8)));
}

void set_number(std::vector<std::uint8_t>& data, std::size_t offset, double value) {
    std::vector<std::uint8_t> encoded;
    number(encoded, value);
    for (std::size_t i = 0; i < encoded.size(); ++i)
        data.at(offset + i) = encoded[i];
}

std::vector<std::uint8_t> fixture() {
    const char magic[] = "DLYPATH";
    std::vector<std::uint8_t> data(magic, magic + 8);
    u32(data, 1);
    u32(data, 2);
    u32(data, 7);
    u32(data, 0);
    number(data, 8);
    number(data, 1);
    number(data, 4);
    for (int i = 0; i < 7; ++i)
        number(data, i);
    for (int i = 0; i < 7; ++i)
        number(data, i + 30);
    const double times[] = {1, 2, 4};
    const double values[] = {0, 10, 30};
    for (int segment = 0; segment < 2; ++segment) {
        number(data, times[segment]);
        number(data, times[segment + 1]);
        for (int i = 0; i < 7; ++i) {
            u32(data, i == 6 ? 0 : (i == 0 ? 2 : 1));
            u32(data, i >= 3 ? 1 : 0);
            number(data, i + values[segment]);
            number(data, i + values[segment + 1]);
            number(data, i == 0 ? 10 : 0);
            number(data, i == 0 ? 10 : 0);
        }
    }
    return data;
}

void self_test() {
    auto bytes = fixture();
    dolly::NativePath path;
    dolly::CameraPose pose{};
    std::string error;
    require(path.empty() && !path.evaluate(0, pose), "Empty path must not evaluate");
    require(path.load(bytes.data(), bytes.size(), error), "Valid path did not load");
    require(error.empty() && !path.empty() && path.duration() == 8, "Header was not retained");
    require(path.evaluate(-100, pose) && pose[0] == 0 && pose[6] == 6, "First hold failed");
    require(path.evaluate(1.5, pose) && pose[0] == 5 && pose[2] == 7 && pose[6] == 6,
            "Linear/Hermite/step evaluation failed");
    require(path.evaluate(2, pose) && pose[0] == 10 && pose[6] == 16,
            "Step channel must switch exactly on the authored key");
    require(path.evaluate(3, pose) && pose[0] == 20 && pose[6] == 16, "Nonuniform segment failed");
    require(path.evaluate(4, pose) && pose[0] == 30 && pose[6] == 36, "Final key is not exact");
    require(path.evaluate(100, pose) && pose[0] == 30, "Last hold failed");
    const auto held = pose;
    require(!path.evaluate(std::numeric_limits<double>::quiet_NaN(), pose) && pose == held,
            "Nonfinite evaluation must leave output untouched");
    require(!path.load(nullptr, bytes.size(), error), "Null input accepted");
    for (std::size_t size = 0; size < bytes.size(); ++size)
        require(!path.load(bytes.data(), size, error), "Truncated blob accepted");
    auto changed = bytes;
    changed.push_back(0);
    require(!path.load(changed.data(), changed.size(), error), "Trailing bytes accepted");
    const std::size_t byte_offsets[] = {0, 8, 12, 16, 20, 176, 180};
    for (auto offset : byte_offsets) {
        changed = bytes;
        changed[offset] = 0xff;
        require(!path.load(changed.data(), changed.size(), error),
                "Malformed header/channel accepted");
    }
    const std::size_t finite_offsets[] = {24, 32, 40, 48, 104, 160, 168, 184, 192, 200, 208};
    for (auto offset : finite_offsets) {
        changed = bytes;
        set_number(changed, offset, std::numeric_limits<double>::infinity());
        require(!path.load(changed.data(), changed.size(), error),
                "Nonfinite coefficient accepted");
    }
    changed = bytes;
    set_number(changed, 160 + dolly::NativePath::segment_bytes, 2.1);
    require(!path.load(changed.data(), changed.size(), error),
            "Disconnected time intervals accepted");
    changed = bytes;
    set_number(changed, 184 + dolly::NativePath::segment_bytes, 11);
    require(!path.load(changed.data(), changed.size(), error),
            "Disconnected channel endpoints accepted");
    changed = bytes;
    set_number(changed, 24, 3);
    require(!path.load(changed.data(), changed.size(), error),
            "Duration before final camera key accepted");
    require(path.evaluate(3, pose) && pose[0] == 20, "Failed load corrupted previous valid path");

    // A cubic overflow falls back to weighted linear interpolation, as it
    // does in the editor. This is distinct from rejecting nonfinite input.
    changed = bytes;
    set_number(changed, 48, 1.6e308);
    set_number(changed, 104, 1.6e308);
    for (int segment = 0; segment < 2; ++segment) {
        set_number(changed, 184 + segment * dolly::NativePath::segment_bytes, 1.6e308);
        set_number(changed, 192 + segment * dolly::NativePath::segment_bytes, 1.6e308);
    }
    set_number(changed, 200, std::numeric_limits<double>::max());
    set_number(changed, 208, -std::numeric_limits<double>::max());
    require(path.load(changed.data(), changed.size(), error),
            "Finite extreme derivatives rejected");
    require(path.evaluate(1.5, pose) && pose[0] == 1.6e308, "Extreme derivative fallback failed");
    // Version 2 preserves a captured lens independently of the live game FOV.
    std::vector<std::uint8_t> lens{'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    u32(lens, 2);
    u32(lens, 1);
    u32(lens, 8);
    u32(lens, 0);
    number(lens, 1);
    number(lens, 0);
    number(lens, 1);
    for (int end = 0; end < 2; ++end)
        for (int channel = 0; channel < 8; ++channel)
            number(lens, channel == 7 ? .75 + .25 * end : channel == 6 ? 16.0 / 9 : 0);
    number(lens, 0);
    number(lens, 1);
    for (int channel = 0; channel < 8; ++channel) {
        u32(lens, 1);
        u32(lens, 1);
        number(lens, channel == 7 ? .75 : channel == 6 ? 16.0 / 9 : 0);
        number(lens, channel == 7 ? 1 : channel == 6 ? 16.0 / 9 : 0);
        number(lens, 0);
        number(lens, 0);
    }
    require(path.load(lens.data(), lens.size(), error), "Captured lens path rejected");
    double scale = 0;
    require(path.evaluate(.5, pose, &scale) && scale == .875, "Captured lens interpolation failed");
    require(path.evaluate(-1, pose, &scale) && scale == .75, "Captured lens first hold failed");
    require(path.evaluate(2, pose, &scale) && scale == 1, "Captured lens last hold failed");
    changed = lens;
    set_number(changed, 104, std::numeric_limits<double>::quiet_NaN());
    require(!path.load(changed.data(), changed.size(), error), "Nonfinite captured lens accepted");
    for (std::size_t size = 0; size < lens.size(); ++size)
        require(!path.load(lens.data(), size, error), "Truncated lens path accepted");
    require(path.load(bytes.data(), bytes.size(), error) && path.evaluate(1, pose, &scale) &&
                scale == 0,
            "Legacy lens fallback changed");
    std::cout << "Native path self-tests passed\n";
}

} // namespace

// This host test also acts as a tiny test-only parity runner. Python writes
// the real compiler output and compares these values to Project.evaluate.
int main(int argc, char** argv) {
    try {
        if (argc == 1) {
            self_test();
            return 0;
        }
        std::ifstream input(argv[1], std::ios::binary);
        require(bool(input), "Cannot open test path blob");
        std::vector<char> bytes((std::istreambuf_iterator<char>(input)), {});
        dolly::NativePath path;
        std::string error;
        if (!path.load(bytes.data(), bytes.size(), error))
            throw std::runtime_error(error);
        std::cout << std::setprecision(17);
        for (int i = 2; i < argc; ++i) {
            char* end = nullptr;
            const double time = std::strtod(argv[i], &end);
            require(end && *end == 0, "Invalid test timestamp");
            dolly::CameraPose pose;
            require(path.evaluate(time, pose), "Could not evaluate test timestamp");
            for (std::size_t field = 0; field < pose.size(); ++field)
                std::cout << (field ? " " : "") << pose[field];
            std::cout << '\n';
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
