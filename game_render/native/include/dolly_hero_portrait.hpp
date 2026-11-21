#pragma once
#include <cstdint>
#include <filesystem>
#include <vector>

namespace dolly {
struct HeroPortrait {
    unsigned width = 0, height = 0;
    std::vector<std::uint8_t> bgra;
};
// Worker-only, bounded disk reads. No game artwork is bundled or written to disk.
HeroPortrait load_hero_portrait(const std::filesystem::path& directory_vpk,
                                const char* model_path) noexcept;
HeroPortrait decode_hero_portrait(const std::vector<std::uint8_t>& resource) noexcept;
} // namespace dolly
