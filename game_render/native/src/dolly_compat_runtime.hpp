// Runtime client-profile selection and symbol resolution for the native bridge.
//
// Included from bridge_win.cpp after the checked-memory helpers and
// module_matches, inside its anonymous namespace. Exact SHA-256 matches use the
// generated profile table directly. Otherwise a wildcard AOB signature may
// identify a build whose camera function is byte-identical modulo relocated
// addresses; every other camera symbol is then re-derived from the image and
// must resolve uniquely before the hook is installed.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "dolly_pattern_scan.hpp"
#include "dolly_remote_definitions.hpp"
#include "dolly_compat_generated.hpp"

struct CompatResolution {
    bool resolved = false;
    bool exact = false;
    std::uintptr_t setup = 0;
    std::uintptr_t caller = 0;
    std::uintptr_t view_table = 0;
    std::uintptr_t globals = 0;
    std::uintptr_t engine_client = 0;
    std::uintptr_t render_fraction = 0; // Enabled only by an exact clock-field review.
    unsigned camera_layout_revision = 0;
    std::uintptr_t view_setup_delegate = 0;
    const unsigned char* setup_prologue = kSetupPrologue;
    const char* note = "";
};

namespace compat_detail {

inline bool section_range(HMODULE module, const char* wanted, std::uintptr_t& start,
                          std::size_t& size) {
    start = 0;
    size = 0;
    if (!module)
        return false;
    auto base = reinterpret_cast<const unsigned char*>(module);
    IMAGE_DOS_HEADER dos{};
    std::memcpy(&dos, base, sizeof(dos));
    if (dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0)
        return false;
    IMAGE_NT_HEADERS64 nt{};
    std::memcpy(&nt, base + dos.e_lfanew, sizeof(nt));
    if (nt.Signature != IMAGE_NT_SIGNATURE)
        return false;
    // Read the section table from the mapped image, never from the local copy.
    auto section = reinterpret_cast<const IMAGE_SECTION_HEADER*>(
        base + dos.e_lfanew + 4 + sizeof(IMAGE_FILE_HEADER) + nt.FileHeader.SizeOfOptionalHeader);
    for (unsigned i = 0; i < nt.FileHeader.NumberOfSections; ++i, ++section) {
        char name[9]{};
        std::memcpy(name, section->Name, 8);
        if (std::strcmp(name, wanted) != 0)
            continue;
        start = reinterpret_cast<std::uintptr_t>(base) + section->VirtualAddress;
        size = section->Misc.VirtualSize ? section->Misc.VirtualSize : section->SizeOfRawData;
        return size != 0;
    }
    return false;
}

inline std::uintptr_t relative_target(std::uintptr_t instruction, std::size_t operand_offset,
                                      std::int32_t displacement) {
    // Route the signed displacement through an unsigned operand to avoid the
    // MSVC C4293 warning when the pointer arithmetic is the last operand.
    const std::uint32_t bits = static_cast<std::uint32_t>(displacement);
    return static_cast<std::uintptr_t>(
        static_cast<std::int64_t>(instruction) + static_cast<std::int64_t>(operand_offset)
        + static_cast<std::int64_t>(bits));
}

// Find the unique return address of a direct call to ``setup`` inside .text.
inline std::uintptr_t locate_caller(std::uintptr_t text, std::size_t size,
                                    std::uintptr_t setup, bool& unique) {
    unique = false;
    std::uintptr_t found = 0;
    std::size_t count = 0;
    for (std::size_t i = 0; i + 5 <= size; ++i) {
        if (*reinterpret_cast<const unsigned char*>(text + i) != 0xE8)
            continue;
        std::int32_t displacement = 0;
        std::memcpy(&displacement, reinterpret_cast<const void*>(text + i + 1), 4);
        if (relative_target(text + i, 5, displacement) != setup)
            continue;
        if (++count > 1)
            return 0;
        found = text + i + 5;
    }
    unique = count == 1;
    return found;
}

inline std::uintptr_t scan_qword(std::uintptr_t start, std::size_t size, std::uint64_t value) {
    for (std::size_t i = 0; i + 8 <= size; ++i) {
        std::uint64_t current = 0;
        std::memcpy(&current, reinterpret_cast<const void*>(start + i), 8);
        if (current == value)
            return start + i;
    }
    return 0;
}

// Resolve the CViewRender primary vtable through its RTTI complete object locator.
inline std::uintptr_t locate_vtable(HMODULE module, const char* class_name) {
    auto base = reinterpret_cast<std::uintptr_t>(module);
    char needle[64]{};
    const std::size_t class_length = std::strlen(class_name);
    if (class_length + 6 > sizeof(needle))
        return 0;
    std::memcpy(needle, ".?AV", 4);
    std::memcpy(needle + 4, class_name, class_length);
    std::memcpy(needle + 4 + class_length, "@@", 2);
    const std::size_t needle_size = class_length + 6;
    std::uintptr_t rdata = 0, data = 0;
    std::size_t rdata_size = 0, data_size = 0;
    if (!section_range(module, ".rdata", rdata, rdata_size) ||
        !section_range(module, ".data", data, data_size))
        return 0;
    const std::uintptr_t starts[2] = {rdata, data};
    const std::size_t sizes[2] = {rdata_size, data_size};
    std::uintptr_t name = 0;
    for (int range = 0; range < 2 && !name; ++range) {
        for (std::size_t i = 0; i + needle_size <= sizes[range]; ++i) {
            if (std::memcmp(reinterpret_cast<const void*>(starts[range] + i), needle,
                            needle_size) == 0) {
                name = starts[range] + i;
                break;
            }
        }
    }
    if (!name)
        return 0;
    const std::uint32_t descriptor_rva = static_cast<std::uint32_t>(name - 0x10 - base);
    for (int range = 0; range < 2; ++range) {
        for (std::size_t i = 0; i + 0x10 <= sizes[range]; ++i) {
            std::uint32_t signature = 0, offset = 0, cd_offset = 0, descriptor = 0;
            std::memcpy(&signature, reinterpret_cast<const void*>(starts[range] + i), 4);
            if (signature != 1)
                continue;
            std::memcpy(&offset, reinterpret_cast<const void*>(starts[range] + i + 4), 4);
            std::memcpy(&cd_offset, reinterpret_cast<const void*>(starts[range] + i + 8), 4);
            std::memcpy(&descriptor, reinterpret_cast<const void*>(starts[range] + i + 0xC), 4);
            if (offset != 0 || cd_offset != 0 || descriptor != descriptor_rva)
                continue;
            const auto locator = static_cast<std::uint64_t>(starts[range] + i);
            for (int search = 0; search < 2; ++search) {
                auto location = scan_qword(starts[search], sizes[search], locator);
                if (location)
                    return location + 8;
            }
        }
    }
    return 0;
}

// Resolve the client globals pointer from the SetGlobals store pattern.
inline std::uintptr_t locate_globals(std::uintptr_t text, std::size_t size,
                                     std::uintptr_t& fallback) {
    fallback = 0;
    const unsigned char tick_opcode[] = {0xc7, 0x05};
    const unsigned char tick_immediate[] = {0x89, 0x88, 0x88, 0x3c};
    const unsigned char store[] = {0x48, 0x89, 0x15};
    const unsigned char leap[] = {0x4c, 0x8d, 0x05};
    for (std::size_t i = 0; i + 10 <= size; ++i) {
        if (std::memcmp(reinterpret_cast<const void*>(text + i), tick_opcode, 2) != 0)
            continue;
        if (std::memcmp(reinterpret_cast<const void*>(text + i + 6), tick_immediate, 4) != 0)
            continue;
        for (std::size_t forward = i; forward + 7 <= size && forward < i + 0x60; ++forward) {
            if (std::memcmp(reinterpret_cast<const void*>(text + forward), store, 3) != 0)
                continue;
            std::int32_t displacement = 0;
            std::memcpy(&displacement, reinterpret_cast<const void*>(text + forward + 3), 4);
            const auto pointer = relative_target(text + forward, 7, displacement);
            for (std::int64_t back = static_cast<std::int64_t>(i) - 1;
                 back >= 0 && back >= static_cast<std::int64_t>(i) - 0x40; --back) {
                if (std::memcmp(reinterpret_cast<const void*>(text + back), leap, 3) == 0) {
                    std::int32_t fallback_displacement = 0;
                    std::memcpy(&fallback_displacement,
                                reinterpret_cast<const void*>(text + back + 3), 4);
                    fallback = relative_target(text + back, 7, fallback_displacement);
                    break;
                }
            }
            return pointer;
        }
    }
    return 0;
}

// Resolve the engine-client interface pointer from the aspect-source virtual call.
inline std::uintptr_t locate_engine_client(std::uintptr_t setup, std::size_t setup_size) {
    const unsigned char anchor[] = {0x48, 0x8b, 0x01, 0xff, 0x90, 0xb0, 0x02, 0x00, 0x00};
    const unsigned char leap[] = {0x48, 0x8b, 0x0d};
    std::uintptr_t candidate = 0;
    for (std::size_t i = 0; i + sizeof(anchor) <= setup_size; ++i) {
        if (std::memcmp(reinterpret_cast<const void*>(setup + i), anchor, sizeof(anchor)) != 0)
            continue;
        for (std::int64_t back = static_cast<std::int64_t>(i) - 1;
             back >= 0 && back >= static_cast<std::int64_t>(i) - 0x80; --back) {
            if (std::memcmp(reinterpret_cast<const void*>(setup + back), leap, 3) != 0)
                continue;
            std::int32_t displacement = 0;
            std::memcpy(&displacement, reinterpret_cast<const void*>(setup + back + 3), 4);
            candidate = relative_target(setup + back, 7, displacement);
            break;
        }
    }
    return candidate;
}

}  // namespace compat_detail

inline CompatResolution resolve_client_profile(HMODULE client, bool allow_signature,
                                              const dolly_remote::Definitions* remote = nullptr) {
    CompatResolution result;
    std::uintptr_t text = 0;
    std::size_t text_size = 0;
    if (!compat_detail::section_range(client, ".text", text, text_size))
        return result;
    for (std::size_t i = 0; i < dolly_compat::kCompatClientProfileCount; ++i) {
        const auto& profile = dolly_compat::kCompatClientProfiles[i];
        if (!module_matches(client, profile.sha256, profile.image_size))
            continue;
        result.resolved = true;
        result.exact = true;
        result.setup = profile.main_view_setup;
        result.caller = profile.main_view_caller;
        result.view_table = profile.primary_vtable;
        result.globals = profile.globals;
        result.engine_client = profile.engine_client;
        result.render_fraction = profile.render_fraction;
        result.camera_layout_revision = profile.camera_layout_revision;
        result.view_setup_delegate = profile.view_setup_delegate;
        result.setup_prologue = profile.setup_prologue;
        result.note = "exact reviewed hash";
        return result;
    }
    if (remote && remote->loaded) {
        for (std::size_t i = 0; i < remote->client_count; ++i) {
            const auto& profile = remote->clients[i];
            if (!module_matches(client, profile.sha256, profile.image_size))
                continue;
            result.resolved = true;
            result.exact = true;
            result.setup = profile.main_view_setup;
            result.caller = profile.main_view_caller;
            result.view_table = profile.primary_vtable;
            result.globals = profile.globals;
            result.engine_client = profile.engine_client;
            result.render_fraction = profile.render_fraction;
            result.camera_layout_revision = profile.camera_layout_revision;
            result.view_setup_delegate = profile.view_setup_delegate;
            result.setup_prologue = profile.setup_prologue;
            result.note = "exact hash from published definitions";
            return result;
        }
    }
    if (!allow_signature)
        return result;
    // A published profile's own signature is checked before the legacy bundled
    // fallback: it identifies this exact build, while the bundled patterns only
    // describe older builds with the same code shape.
    if (remote && remote->loaded) {
        for (std::size_t i = 0; i < remote->client_count; ++i) {
            const auto& profile = remote->clients[i];
            if (!profile.signature_size)
                continue;
            std::size_t offset = 0;
            const std::size_t matches = dolly::find_pattern(
                reinterpret_cast<const unsigned char*>(text), text_size, profile.signature,
                profile.signature_mask, profile.signature_size, offset);
            if (matches != 1 || text + offset != text + profile.main_view_setup)
                continue;
            if (std::memcmp(reinterpret_cast<const void*>(text + offset), profile.setup_prologue,
                            sizeof(profile.setup_prologue)) != 0)
                continue;
            result.resolved = true;
            result.exact = false;
            result.setup = profile.main_view_setup;
            result.caller = profile.main_view_caller;
            result.view_table = profile.primary_vtable;
            result.globals = profile.globals;
            result.engine_client = profile.engine_client;
            result.render_fraction = profile.render_fraction;
            result.camera_layout_revision = profile.camera_layout_revision;
            result.view_setup_delegate = profile.view_setup_delegate;
            result.setup_prologue = profile.setup_prologue;
            result.note = "published signature, reviewed offsets";
            return result;
        }
    }
    for (std::size_t i = 0; i < dolly_compat::kCompatClientProfileCount; ++i) {
        const auto& profile = dolly_compat::kCompatClientProfiles[i];
        // The September 29 virtual wrapper and moved view fields have only an
        // exact-build review. Legacy AOB discovery cannot establish that chain.
        if (profile.camera_layout_revision || !profile.signature || !profile.signature_size)
            continue;
        std::size_t offset = 0;
        const std::size_t matches = dolly::find_pattern(
            reinterpret_cast<const unsigned char*>(text), text_size, profile.signature,
            profile.signature_mask, profile.signature_size, offset);
        if (matches != 1)
            continue;
        const std::uintptr_t setup = text + offset;
        bool unique_caller = false;
        const std::uintptr_t caller =
            compat_detail::locate_caller(text, text_size, setup, unique_caller);
        const std::uintptr_t view_table = compat_detail::locate_vtable(client, "CViewRender");
        std::uintptr_t fallback = 0;
        const std::uintptr_t globals = compat_detail::locate_globals(text, text_size, fallback);
        const std::uintptr_t engine_client = compat_detail::locate_engine_client(setup, 0x860);
        if (!unique_caller || !caller || !view_table || !globals || !engine_client)
            continue;
        // Require the discovered setup to keep the reviewed prologue shape.
        if (std::memcmp(reinterpret_cast<const void*>(setup), kSetupPrologue,
                        sizeof(kSetupPrologue)) != 0)
            continue;
        result.resolved = true;
        result.exact = false;
        result.setup = setup;
        result.caller = caller;
        result.view_table = view_table;
        result.globals = globals;
        result.engine_client = engine_client;
        result.note = "AOB signature, layout re-derived";
        return result;
    }
    return result;
}
