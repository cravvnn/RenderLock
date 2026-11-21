// Portable, fail-closed audio address resolution. No hooks or process access.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include "dolly_pattern_scan.hpp"

namespace dolly_sound_compat {
struct Signature {
    const unsigned char* bytes;
    const unsigned char* mask;
    std::size_t size, prologue_size;
    std::uint32_t reviewed_rva;
};
struct TableReference {
    std::size_t symbol, offset, displacement, size;
};
struct Resolution {
    std::uint32_t functions[5]{};
    std::uint32_t voice_table = 0;
    std::uint32_t voice_map_offset = 0;
    std::uint32_t parameter_volume_offset = 0, parameter_rate_offset = 0;
};
struct Profile {
    std::uint32_t image_size;
    const Signature* signatures;
    std::size_t signature_count;
    const TableReference* references;
    std::size_t reference_count;
    std::uint32_t voice_table, voice_map_offset;
    std::uint32_t parameter_volume_offset, parameter_rate_offset;
};

inline bool resolve(const unsigned char* text, std::size_t text_size,
                    std::uint32_t text_rva, std::uint32_t data_rva, std::size_t data_size,
                    const Signature* signatures, std::size_t count,
                    const TableReference* references, std::size_t reference_count,
                    bool exact, std::uint32_t reviewed_table, Resolution& result) {
    result = {};
    Resolution candidate{};
    if (!text || !signatures || count != 5 || !references || !reference_count || data_size < 8)
        return false;
    for (std::size_t i = 0; i < count; ++i) {
        const auto& s = signatures[i];
        std::size_t offset = 0;
        if (!s.bytes || !s.mask || !s.size || !s.prologue_size || s.prologue_size > s.size)
            return false;
        if (exact) {
            if (s.reviewed_rva < text_rva) return false;
            offset = s.reviewed_rva - text_rva;
        } else if (dolly::find_pattern(text, text_size, s.bytes, s.mask, s.size, offset) != 1) {
            return false;
        }
        if (offset > text_size || s.size > text_size - offset ||
            std::memcmp(text + offset, s.bytes, s.prologue_size) != 0 ||
            std::uint64_t(text_rva) + offset > UINT32_MAX)
            return false;
        candidate.functions[i] = text_rva + static_cast<std::uint32_t>(offset);
        for (std::size_t j = 0; j < i; ++j)
            if (candidate.functions[j] == candidate.functions[i]) return false;
    }
    // Independently decoded RIP-relative loads must all name one aligned data
    // slot; never carry the old data RVA onto a signature-matched image.
    std::uint32_t table = 0;
    for (std::size_t i = 0; i < reference_count; ++i) {
        const auto& r = references[i];
        if (r.symbol >= count || r.size < 4 || r.displacement > r.size - 4 ||
            r.offset > signatures[r.symbol].size || r.size > signatures[r.symbol].size - r.offset)
            return false;
        const auto instruction = std::uint64_t(candidate.functions[r.symbol]) + r.offset;
        std::int32_t displacement = 0;
        std::memcpy(&displacement, text + instruction - text_rva + r.displacement, 4);
        const auto target = static_cast<std::int64_t>(instruction) + r.size + displacement;
        if (target < data_rva || std::uint64_t(target) - data_rva > data_size - 8 ||
            target > UINT32_MAX || target % 8 || (table && table != target))
            return false;
        table = static_cast<std::uint32_t>(target);
    }
    if (!table || (exact && table != reviewed_table)) return false;
    candidate.voice_table = table;
    result = candidate;
    return true;
}
inline bool resolve_profiles(const unsigned char* text, std::size_t text_size,
                             std::uint32_t text_rva, std::uint32_t data_rva,
                             std::size_t data_size, std::uint32_t image_size,
                             const Profile* profiles, std::size_t profile_count,
                             bool exact, Resolution& result) {
    result = {};
    if (!profiles || !profile_count) return false;
    Resolution selected{};
    unsigned matches = 0;
    for (std::size_t i = 0; i < profile_count; ++i) {
        const auto& p = profiles[i];
        if (exact && p.image_size != image_size) continue;
        Resolution candidate{};
        if (!p.voice_map_offset || !p.parameter_volume_offset || !p.parameter_rate_offset ||
            !resolve(text, text_size, text_rva, data_rva, data_size, p.signatures,
                     p.signature_count, p.references, p.reference_count,
                     exact, p.voice_table, candidate)) continue;
        if (++matches != 1) return false;
        candidate.voice_map_offset = p.voice_map_offset;
        candidate.parameter_volume_offset = p.parameter_volume_offset;
        candidate.parameter_rate_offset = p.parameter_rate_offset;
        selected = candidate;
    }
    if (matches != 1) return false;
    result = selected;
    return true;
}
} // namespace dolly_sound_compat
