#include "dolly_hero_portrait.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <chrono>
#include <string>
using Bytes = std::vector<std::uint8_t>;
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
void put(Bytes& b, unsigned p, unsigned v, unsigned count = 4) {
    for (unsigned i = 0; i < count; ++i)
        b.at(p + i) = std::uint8_t(v >> (8 * i));
}
Bytes texture() {
    Bytes b(84);
    put(b, 0, 68);
    put(b, 4, 12, 2);
    put(b, 6, 1, 2);
    put(b, 8, 8);
    put(b, 12, 1);
    put(b, 16, 0x41544144);
    put(b, 20, 8);
    put(b, 24, 40);
    put(b, 28, 1, 2);
    put(b, 48, 2, 2);
    put(b, 50, 2, 2);
    put(b, 52, 1, 2);
    b[54] = 28;
    b[55] = 1;
    for (unsigned i = 68; i < b.size(); ++i)
        b[i] = std::uint8_t(i);
    return b;
}
void write(const std::filesystem::path& p, const Bytes& b) {
    std::ofstream f(p, std::ios::binary);
    f.write(reinterpret_cast<const char*>(b.data()), b.size());
    require(bool(f), "Write fixture");
}
int main(int argc, char** argv) {
    try {
        auto b = texture();
        auto image = dolly::decode_hero_portrait(b);
        require(image.width == 2 && image.height == 2 && image.bgra.size() == 16 &&
                    image.bgra[0] == 68,
                "Decode exact BGRA image bytes");
        for (unsigned n = 0; n < b.size(); ++n)
            require(dolly::decode_hero_portrait(Bytes(b.begin(), b.begin() + n)).bgra.empty(),
                    "Every truncation fails closed");
        for (const auto offset : {0u, 4u, 6u, 8u, 12u, 20u, 24u, 28u, 48u, 50u, 52u, 54u, 55u}) {
            auto invalid = b;
            invalid[offset] = 255;
            require(dolly::decode_hero_portrait(invalid).bgra.empty(),
                    "Reject unsupported headers");
        }
        const auto root =
            std::filesystem::temp_directory_path() /
            ("dolly-portrait-test-" +
             std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        std::filesystem::create_directory(root);
        struct Cleanup {
            std::filesystem::path p;
            ~Cleanup() { std::filesystem::remove_all(p); }
        } cleanup{root};
        Bytes tree;
        auto text = [&](const char* s) {
            while (*s)
                tree.push_back(*s++);
            tree.push_back(0);
        };
        text("vtex_c");
        text("panorama/images/heroes");
        text("bull_sm_psd");
        auto entry = unsigned(tree.size());
        tree.resize(tree.size() + 18);
        put(tree, entry + 4, 4, 2);
        put(tree, entry + 6, 3, 2);
        put(tree, entry + 8, 2);
        put(tree, entry + 12, unsigned(b.size() - 4));
        put(tree, entry + 16, 0xffff, 2);
        tree.insert(tree.end(), b.begin(), b.begin() + 4);
        tree.insert(tree.end(), 3, 0);
        Bytes directory(28);
        put(directory, 0, 0x55aa1234);
        put(directory, 4, 2);
        put(directory, 8, unsigned(tree.size()));
        directory.insert(directory.end(), tree.begin(), tree.end());
        Bytes archive(2);
        archive.insert(archive.end(), b.begin() + 4, b.end());
        write(root / "pak01_dir.vpk", directory);
        write(root / "pak01_003.vpk", archive);
        image =
            dolly::load_hero_portrait(root / "pak01_dir.vpk", "models/heroes/abrams/abrams.vmdl");
        require(image.bgra.size() == 16 && image.bgra[0] == 68,
                "Alias and archive/preload assembly");
        require(dolly::load_hero_portrait(root / "pak01_dir.vpk", "unknown.vmdl").bgra.empty(),
                "Missing hero never receives another hero's portrait");
        // Read fresh on a new picker: artwork updates cannot leave a process-lifetime stale cache.
        archive.back() = 17;
        write(root / "pak01_003.vpk", archive);
        require(dolly::load_hero_portrait(root / "pak01_dir.vpk", "abrams.vmdl").bgra.back() == 17,
                "New picker sees updated local artwork");
        directory.resize(40);
        write(root / "pak01_dir.vpk", directory);
        require(dolly::load_hero_portrait(root / "pak01_dir.vpk", "abrams.vmdl").bgra.empty(),
                "Malformed archive fails closed");
        if (argc > 1) {
            const char* models[] = {"inferno",     "gigawatt_prisoner",
                                    "hornet",      "geist",
                                    "abrams",      "wraith",
                                    "mcginnis",    "chrono",
                                    "dynamo",      "kelvin",
                                    "haze",        "astro",
                                    "bebop",       "nano",
                                    "archer",      "digger",
                                    "shiv",        "ivy",
                                    "warden",      "yamato",
                                    "lash",        "viscous",
                                    "pocket",      "mirage",
                                    "viper",       "magician",
                                    "vampirebat",  "drifter",
                                    "priest",      "frank",
                                    "bookworm",    "doorman",
                                    "punkgoat",    "necro",
                                    "familiar_wip"};
            for (const auto model : models) {
                auto real = dolly::load_hero_portrait(argv[1], model);
                std::cout << model << ": " << real.width << "x" << real.height << '\n';
                require(!real.bgra.empty(), "Installed hero artwork did not load");
            }
        }
        std::cout << "Hero portrait tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
