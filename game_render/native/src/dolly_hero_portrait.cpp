#include "dolly_hero_portrait.hpp"
#include <cstring>
#include <cstdio>
#include <fstream>
#include <string>
#include <stdexcept>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <wincodec.h>
#endif

namespace dolly {
namespace {
using Bytes = std::vector<std::uint8_t>;
HeroPortrait png(const Bytes& b, std::size_t start, unsigned width, unsigned height) {
#ifdef _WIN32
    const auto initialized = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(initialized) && initialized != RPC_E_CHANGED_MODE)
        return {};
    struct Resources {
        bool uninitialize;
        IWICImagingFactory* factory = nullptr;
        IWICStream* stream = nullptr;
        IWICBitmapDecoder* decoder = nullptr;
        IWICBitmapFrameDecode* frame = nullptr;
        IWICFormatConverter* converter = nullptr;
        ~Resources() {
            if (converter)
                converter->Release();
            if (frame)
                frame->Release();
            if (decoder)
                decoder->Release();
            if (stream)
                stream->Release();
            if (factory)
                factory->Release();
            if (uninitialize)
                CoUninitialize();
        }
    } r{SUCCEEDED(initialized)};
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(&r.factory))) ||
        FAILED(r.factory->CreateStream(&r.stream)) ||
        FAILED(r.stream->InitializeFromMemory(const_cast<BYTE*>(b.data() + start),
                                              DWORD(b.size() - start))) ||
        FAILED(r.factory->CreateDecoderFromStream(r.stream, nullptr, WICDecodeMetadataCacheOnDemand,
                                                  &r.decoder)) ||
        FAILED(r.decoder->GetFrame(0, &r.frame)))
        return {};
    UINT w = 0, h = 0;
    if (FAILED(r.frame->GetSize(&w, &h)) || w != width || h != height)
        return {};
    if (FAILED(r.factory->CreateFormatConverter(&r.converter)) ||
        FAILED(r.converter->Initialize(r.frame, GUID_WICPixelFormat32bppBGRA,
                                       WICBitmapDitherTypeNone, nullptr, 0,
                                       WICBitmapPaletteTypeCustom)))
        return {};
    HeroPortrait image{width, height, Bytes(width * height * 4)};
    if (SUCCEEDED(r.converter->CopyPixels(nullptr, width * 4, UINT(image.bgra.size()),
                                          image.bgra.data())))
        return image;
#endif
    return {};
}
unsigned u16(const Bytes& b, std::size_t p) {
    if (p > b.size() || b.size() - p < 2)
        throw std::runtime_error("short resource");
    return unsigned(b[p]) | unsigned(b[p + 1]) << 8;
}
std::uint32_t u32(const Bytes& b, std::size_t p) {
    return u16(b, p) | std::uint32_t(u16(b, p + 2)) << 16;
}
Bytes read(const std::filesystem::path& path, std::uint64_t offset, std::size_t size) {
    if (size > 16 * 1024 * 1024)
        throw std::runtime_error("oversize resource");
    std::ifstream file(path, std::ios::binary);
    file.seekg(0, std::ios::end);
    const auto end = file.tellg();
    if (end < 0 || offset > std::uint64_t(end) || size > std::uint64_t(end) - offset)
        throw std::runtime_error("short archive");
    Bytes result(size);
    file.seekg(std::streamoff(offset));
    if (!file.read(reinterpret_cast<char*>(result.data()), std::streamsize(size)))
        throw std::runtime_error("unreadable archive");
    return result;
}
std::string text(const Bytes& b, std::size_t& p) {
    const auto start = p;
    while (p < b.size() && b[p] && p - start < 512)
        ++p;
    if (p >= b.size() || b[p])
        throw std::runtime_error("invalid archive string");
    const auto length = p - start;
    ++p;
    return std::string(reinterpret_cast<const char*>(b.data() + start), length);
}
std::string portrait_stem(const char* model) {
    if (!model)
        return {};
    std::string path(model);
    auto name = path.substr(path.find_last_of("/\\") + 1);
    name.resize(name.find('.') == std::string::npos ? name.size() : name.find('.'));
    if (name.empty() || name.size() > 64 ||
        name.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789_") != std::string::npos)
        return {};
    // Generated from scripts/heroes.vdata: model stem -> compiled portrait
    // stem (panorama images compile as <name>.<ext> -> <name>_<ext>).
    static constexpr const char* portraits[][2] = {
        {"abrams", "bull_sm_psd"},
        {"archer", "archer_sm_psd"},
        {"astro", "astro_sm_psd"},
        {"baba", "baba_sm_psd"},
        {"bebop", "bebop_sm_psd"},
        {"boho", "hornet_sm_png"},
        {"bookworm", "bookworm_sm_psd"},
        {"chessmaster", "chessmaster_sm_psd"},
        {"chrono", "swan_sm_psd"},
        {"digger", "digger_sm_psd"},
        {"doorman", "doorman_sm_psd"},
        {"drifter", "drifter_sm_psd"},
        {"dynamo", "sumo_sm_psd"},
        {"familiar_wip", "familiar_sm_psd"},
        {"fencer", "fencer_sm_psd"},
        {"fortuna", "fortuna_sm_psd"},
        {"frank", "frank_sm_psd"},
        {"geist", "spectre_sm_psd"},
        {"gen_man", "trapper_sm_psd"},
        {"gigawatt_prisoner", "gigawatt_sm_psd"},
        {"graffiti_girl", "graf_sm_psd"},
        {"haze", "haze_sm_psd"},
        {"hornet", "hornet_sm_png"},
        {"inferno", "inferno_sm_psd"},
        {"ivy", "tengu_sm_psd"},
        {"kelvin", "kelvin_sm_psd"},
        {"lash", "skyrunner_sm_psd"},
        {"magician", "magician_sm_psd"},
        {"mcginnis", "engineer_sm_psd"},
        {"mirage", "mirage_sm_psd"},
        {"nano", "nano_sm_psd"},
        {"necro", "necro_sm_psd"},
        {"operative", "operative_sm_psd"},
        {"pocket", "synth_sm_psd"},
        {"priest", "priest_sm_psd"},
        {"punkgoat", "punkgoat_sm_psd"},
        {"ratking", "ratking_sm_psd"},
        {"shiv", "shiv_sm_psd"},
        {"unicorn", "unicorn_sm_psd"},
        {"vampirebat", "vampirebat_sm_psd"},
        {"viper", "kali_sm_psd"},
        {"viscous", "viscous_sm_psd"},
        {"warden", "warden_sm_psd"},
        {"werewolf", "werewolf_sm_psd"},
        {"wraith", "wraith_sm_psd"},
        {"wrecker", "wrecker_sm_psd"},
        {"yamato", "yamato_sm_psd"},
    };
    for (const auto& entry : portraits)
        if (name == entry[0])
            return entry[1];
    return name + "_sm_psd";
}
} // namespace

HeroPortrait decode_hero_portrait(const Bytes& b) noexcept {
    try {
        // Source 2 resource header v12, texture v1. Only the current small,
        // single-mip BGRA/PNG icons are accepted. Unknown formats
        // fail closed rather than guessing pixel/compression layout.
        const std::size_t payload = u32(b, 0);
        if (u16(b, 4) != 12 || u16(b, 6) != 1 || payload > b.size())
            return {};
        const std::size_t table = 8ull + u32(b, 8), count = u32(b, 12);
        if (count > 64 || table > payload || count * 12 > payload - table)
            return {};
        for (std::size_t i = 0; i < count; ++i) {
            const auto row = table + i * 12;
            if (u32(b, row) != 0x41544144) // DATA
                continue;
            const std::size_t data = row + 4ull + u32(b, row + 4), size = u32(b, row + 8);
            if (data > payload || size > payload - data || size < 40 || u16(b, data) != 1)
                return {};
            const unsigned w = u16(b, data + 20), h = u16(b, data + 22);
            if (!w || !h || w > 256 || h > 256 || u16(b, data + 24) != 1 || b[data + 27] != 1)
                return {};
            if (b[data + 26] == 16 && b.size() - payload >= 8 &&
                b.size() - payload <= 1024 * 1024 &&
                std::memcmp(b.data() + payload, "\x89PNG\r\n\x1a\n", 8) == 0)
                return png(b, payload, w, h);
            if (b[data + 26] != 28 || b.size() - payload != std::size_t(w) * h * 4)
                return {};
            return {w, h, Bytes(b.begin() + payload, b.end())};
        }
    } catch (...) {
    }
    return {};
}

HeroPortrait load_hero_portrait(const std::filesystem::path& directory,
                                const char* model) noexcept {
    try {
        const auto wanted = portrait_stem(model);
        if (wanted.empty())
            return {};
        const auto header = read(directory, 0, 28);
        const auto version = u32(header, 4);
        if (u32(header, 0) != 0x55aa1234 || (version != 1 && version != 2))
            return {};
        const unsigned header_size = version == 2 ? 28 : 12;
        const auto tree = read(directory, header_size, u32(header, 8));
        std::size_t p = 0;
        for (;;) {
            const auto ext = text(tree, p);
            if (ext.empty())
                break;
            for (;;) {
                const auto folder = text(tree, p);
                if (folder.empty())
                    break;
                for (;;) {
                    const auto name = text(tree, p);
                    if (name.empty())
                        break;
                    const auto preload = u16(tree, p + 4), archive = u16(tree, p + 6);
                    const auto offset = u32(tree, p + 8), length = u32(tree, p + 12);
                    if (u16(tree, p + 16) != 0xffff)
                        return {};
                    p += 18;
                    if (p > tree.size() || preload > tree.size() - p)
                        return {};
                    if (ext == "vtex_c" && folder == "panorama/images/heroes" && name == wanted) {
                        if (length > 1024 * 1024 || (archive > 999 && archive != 0x7fff))
                            return {};
                        auto file = directory;
                        std::uint64_t start = offset;
                        if (archive == 0x7fff)
                            start += header_size + tree.size();
                        else {
                            char suffix[24]{};
                            std::snprintf(suffix, sizeof(suffix), "pak01_%03u.vpk", archive);
                            file = directory.parent_path() / suffix;
                        }
                        auto resource = Bytes(tree.begin() + p, tree.begin() + p + preload);
                        const auto tail = read(file, start, length);
                        resource.insert(resource.end(), tail.begin(), tail.end());
                        return decode_hero_portrait(resource);
                    }
                    p += preload;
                }
            }
        }
    } catch (...) {
    }
    return {};
}
} // namespace dolly
