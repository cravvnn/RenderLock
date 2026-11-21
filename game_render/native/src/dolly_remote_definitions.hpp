// Remote build definitions loaded from beside DollyNative.dll.
//
// The editor fetches one small JSON document from the repository, validates it
// in Python and rewrites it as the compact line document parsed here. This
// keeps the injected bridge free of a JSON parser and of any network access.
//
// The format is intentionally closed: a document that has an unknown line, an
// unknown field count, a bad digest or an out-of-range value is rejected whole
// so the bridge keeps using the reviewed table compiled into the release.
//
//   DOLLY_DEFINITIONS 1
//   abi 4
//   +layout <name> <demo_global> <demo_table> <engine_table> <playing> <active>
//           <paused> <seeking> <tick> <filename>
//   +engine <sha256> <image_size> <layout>
//   +cvar <name> <table> <setter> <find> <data> <data_slot> <extended>
//   +tier0 <sha256> <image_size> <layout>
//   +client <sha256> <image_size> <setup> <caller> <vtable> <globals>
//           <engine_client> <render_fraction> <revision> <delegate>
//           <prologue_hex> <signature_hex|-> <mask_hex|->
//   end
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

namespace dolly_remote {

constexpr std::size_t kMaxClients = 32;
constexpr std::size_t kMaxEngines = 64;
constexpr std::size_t kMaxTier0 = 64;
constexpr std::size_t kMaxLayouts = 16;
constexpr std::size_t kMaxBytes = 256 * 1024;
constexpr std::size_t kMaxLine = 4096;
constexpr std::size_t kMaxSignature = 512;
constexpr std::size_t kNameLength = 24;

struct ClientProfile {
    char sha256[65]{};
    std::uint32_t image_size = 0;
    std::uintptr_t main_view_setup = 0;
    std::uintptr_t main_view_caller = 0;
    std::uintptr_t primary_vtable = 0;
    std::uintptr_t globals = 0;
    std::uintptr_t engine_client = 0;
    std::uintptr_t render_fraction = 0;
    unsigned camera_layout_revision = 0;
    std::uintptr_t view_setup_delegate = 0;
    unsigned char setup_prologue[32]{};
    std::size_t prologue_size = 0;
    unsigned char signature[kMaxSignature]{};
    unsigned char signature_mask[kMaxSignature]{};
    std::size_t signature_size = 0;
};

struct EngineProfile {
    char sha256[65]{};
    std::uint32_t image_size = 0;
    char layout[kNameLength + 1]{};
};

struct EngineLayout {
    char name[kNameLength + 1]{};
    std::uintptr_t demo_global = 0;
    std::uintptr_t demo_table = 0;
    std::uintptr_t engine_table = 0;
    std::uintptr_t playing = 0;
    std::uintptr_t active = 0;
    std::uintptr_t paused = 0;
    std::uintptr_t seeking = 0;
    std::uintptr_t tick = 0;
    std::uintptr_t filename = 0;
};

struct CvarLayout {
    char name[kNameLength + 1]{};
    std::uintptr_t table = 0;
    std::uintptr_t setter = 0;
    std::uintptr_t find = 0;
    std::uintptr_t data = 0;
    unsigned data_slot = 0;
    bool extended = false;
};

struct Tier0Profile {
    char sha256[65]{};
    std::uint32_t image_size = 0;
    char layout[kNameLength + 1]{};
};

struct Definitions {
    bool loaded = false;
    unsigned abi = 0;
    std::size_t client_count = 0;
    ClientProfile clients[kMaxClients];
    std::size_t engine_count = 0;
    EngineProfile engines[kMaxEngines];
    std::size_t engine_layout_count = 0;
    EngineLayout engine_layouts[kMaxLayouts];
    std::size_t tier0_count = 0;
    Tier0Profile tier0[kMaxTier0];
    std::size_t cvar_layout_count = 0;
    CvarLayout cvar_layouts[kMaxLayouts];
};

namespace detail {

inline bool is_space(char c) {
    return c == ' ' || c == '\t';
}

// Split one line into at most ``capacity`` tokens. Rejects empty fields so a
// missing value can never be read as a zero.
inline bool tokenize(char* line, char** tokens, std::size_t capacity, std::size_t& count) {
    count = 0;
    char* cursor = line;
    while (*cursor) {
        while (is_space(*cursor))
            ++cursor;
        if (!*cursor)
            break;
        if (count == capacity)
            return false;
        tokens[count++] = cursor;
        while (*cursor && !is_space(*cursor))
            ++cursor;
        if (*cursor)
            *cursor++ = '\0';
    }
    return true;
}

inline bool parse_hex(const char* text, std::uintptr_t& value, std::size_t max_digits = 16) {
    if (!text || text[0] != '0' || (text[1] != 'x' && text[1] != 'X'))
        return false;
    std::uintptr_t result = 0;
    std::size_t digits = 0;
    for (const char* p = text + 2; *p; ++p, ++digits) {
        if (digits == max_digits)
            return false;
        unsigned nibble = 0;
        if (*p >= '0' && *p <= '9')
            nibble = static_cast<unsigned>(*p - '0');
        else if (*p >= 'a' && *p <= 'f')
            nibble = static_cast<unsigned>(*p - 'a' + 10);
        else
            return false;
        result = (result << 4) | nibble;
    }
    if (!digits)
        return false;
    value = result;
    return true;
}

inline bool parse_decimal(const char* text, unsigned& value, unsigned limit) {
    if (!text || !*text)
        return false;
    unsigned result = 0;
    for (const char* p = text; *p; ++p) {
        if (*p < '0' || *p > '9')
            return false;
        result = result * 10 + static_cast<unsigned>(*p - '0');
        if (result > limit)
            return false;
    }
    value = result;
    return true;
}

inline bool parse_sha256(const char* text, char out[65]) {
    if (!text || std::strlen(text) != 64)
        return false;
    for (std::size_t i = 0; i < 64; ++i) {
        const char c = text[i];
        const bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
        if (!hex)
            return false;
        out[i] = c;
    }
    out[64] = '\0';
    return true;
}

inline bool parse_name(const char* text, char out[kNameLength + 1]) {
    const std::size_t length = text ? std::strlen(text) : 0;
    if (!length || length > kNameLength)
        return false;
    for (std::size_t i = 0; i < length; ++i) {
        const char c = text[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'))
            return false;
        out[i] = c;
    }
    out[length] = '\0';
    return true;
}

inline unsigned nibble(char c) {
    if (c >= '0' && c <= '9')
        return static_cast<unsigned>(c - '0');
    if (c >= 'a' && c <= 'f')
        return static_cast<unsigned>(c - 'a' + 10);
    return 16;
}

// A lone "-" means "this profile carries no signature".
inline bool parse_bytes(const char* text, unsigned char* out, std::size_t capacity,
                        std::size_t& size) {
    size = 0;
    if (!text)
        return false;
    if (std::strcmp(text, "-") == 0)
        return true;
    const std::size_t length = std::strlen(text);
    if (!length || length % 2 || length / 2 > capacity)
        return false;
    for (std::size_t i = 0; i < length; i += 2) {
        const unsigned high = nibble(text[i]), low = nibble(text[i + 1]);
        if (high > 15 || low > 15)
            return false;
        out[i / 2] = static_cast<unsigned char>((high << 4) | low);
    }
    size = length / 2;
    return true;
}

inline const EngineLayout* engine_layout(const Definitions& data, const char* name) {
    for (std::size_t i = 0; i < data.engine_layout_count; ++i)
        if (std::strcmp(data.engine_layouts[i].name, name) == 0)
            return &data.engine_layouts[i];
    return nullptr;
}

inline const CvarLayout* cvar_layout(const Definitions& data, const char* name) {
    for (std::size_t i = 0; i < data.cvar_layout_count; ++i)
        if (std::strcmp(data.cvar_layouts[i].name, name) == 0)
            return &data.cvar_layouts[i];
    return nullptr;
}

inline bool parse_client(char** tokens, std::size_t count, ClientProfile& profile) {
    if (count != 14)
        return false;
    std::uintptr_t image_size = 0, revision = 0;
    if (!parse_sha256(tokens[1], profile.sha256) ||
        !parse_hex(tokens[2], image_size, 8) || image_size > 0x40000000u ||
        !parse_hex(tokens[3], profile.main_view_setup) ||
        !parse_hex(tokens[4], profile.main_view_caller) ||
        !parse_hex(tokens[5], profile.primary_vtable) ||
        !parse_hex(tokens[6], profile.globals) ||
        !parse_hex(tokens[7], profile.engine_client) ||
        !parse_hex(tokens[8], profile.render_fraction) ||
        !parse_hex(tokens[9], revision, 2) || revision > 255 ||
        !parse_hex(tokens[10], profile.view_setup_delegate))
        return false;
    std::size_t prologue = 0;
    if (!parse_bytes(tokens[11], profile.setup_prologue, sizeof(profile.setup_prologue), prologue) ||
        prologue != sizeof(profile.setup_prologue))
        return false;
    if (!parse_bytes(tokens[12], profile.signature, sizeof(profile.signature),
                     profile.signature_size))
        return false;
    std::size_t mask_size = 0;
    if (!parse_bytes(tokens[13], profile.signature_mask, sizeof(profile.signature_mask),
                     mask_size))
        return false;
    if (mask_size != profile.signature_size)
        return false;
    for (std::size_t i = 0; i < mask_size; ++i)
        if (profile.signature_mask[i] > 1)
            return false;
    profile.image_size = static_cast<std::uint32_t>(image_size);
    profile.camera_layout_revision = static_cast<unsigned>(revision);
    profile.prologue_size = prologue;
    return true;
}

inline bool parse_engine(char** tokens, std::size_t count, EngineProfile& profile) {
    if (count != 4)
        return false;
    std::uintptr_t image_size = 0;
    if (!parse_sha256(tokens[1], profile.sha256) ||
        !parse_hex(tokens[2], image_size, 8) || image_size > 0x40000000u ||
        !parse_name(tokens[3], profile.layout))
        return false;
    profile.image_size = static_cast<std::uint32_t>(image_size);
    return true;
}

inline bool parse_engine_layout(char** tokens, std::size_t count, EngineLayout& layout) {
    if (count != 11 || !parse_name(tokens[1], layout.name))
        return false;
    std::uintptr_t* fields[9] = {
        &layout.demo_global, &layout.demo_table, &layout.engine_table,
        &layout.playing, &layout.active, &layout.paused,
        &layout.seeking, &layout.tick, &layout.filename,
    };
    for (std::size_t i = 0; i < 9; ++i)
        if (!parse_hex(tokens[2 + i], *fields[i]))
            return false;
    return true;
}

inline bool parse_cvar_layout(char** tokens, std::size_t count, CvarLayout& layout) {
    if (count != 8 || !parse_name(tokens[1], layout.name))
        return false;
    if (!parse_hex(tokens[2], layout.table) || !parse_hex(tokens[3], layout.setter) ||
        !parse_hex(tokens[4], layout.find) || !parse_hex(tokens[5], layout.data))
        return false;
    unsigned slot = 0, extended = 0;
    if (!parse_decimal(tokens[6], slot, 64) || (slot != 41 && slot != 43) ||
        !parse_decimal(tokens[7], extended, 1))
        return false;
    layout.data_slot = slot;
    layout.extended = extended != 0;
    return true;
}

inline bool parse_tier0(char** tokens, std::size_t count, Tier0Profile& profile) {
    if (count != 4)
        return false;
    std::uintptr_t image_size = 0;
    if (!parse_sha256(tokens[1], profile.sha256) ||
        !parse_hex(tokens[2], image_size, 8) || image_size > 0x40000000u ||
        !parse_name(tokens[3], profile.layout))
        return false;
    profile.image_size = static_cast<std::uint32_t>(image_size);
    return true;
}

}  // namespace detail

// Parse a complete document. Rejects the whole buffer on any fault and never
// mutates ``out`` when it fails.
inline bool parse(const char* data, std::size_t size, Definitions& out) {
    if (!data || size > kMaxBytes)
        return false;
    Definitions staged;
    bool saw_end = false;
    bool saw_abi = false;
    std::size_t start = 0, lines = 0;
    std::vector<char> buffer(kMaxLine + 1);
    while (start < size) {
        std::size_t end = start;
        while (end < size && data[end] != '\n')
            ++end;
        const std::size_t next = end < size ? end + 1 : size;
        const std::size_t begin = start;
        std::size_t length = end - start;
        if (length && data[start + length - 1] == '\r')
            --length;
        start = next;
        if (length > kMaxLine || ++lines > 8192)
            return false;
        if (length) {
            std::memcpy(buffer.data(), data + begin, length);
            buffer[length] = '\0';
            char* tokens[16]{};
            std::size_t count = 0;
            if (!detail::tokenize(buffer.data(), tokens, 16, count) || !count || saw_end)
                return false;
            const char* kind = tokens[0];
            if (std::strcmp(kind, "DOLLY_DEFINITIONS") == 0) {
                if (count != 2 || std::strcmp(tokens[1], "1") != 0 || lines != 1)
                    return false;
                continue;
            }
            if (std::strcmp(kind, "abi") == 0) {
                unsigned abi = 0;
                if (count != 2 || saw_abi || !detail::parse_decimal(tokens[1], abi, 1000))
                    return false;
                staged.abi = abi;
                saw_abi = true;
                continue;
            }
            if (std::strcmp(kind, "end") == 0) {
                if (count != 1)
                    return false;
                saw_end = true;
                continue;
            }
            if (!saw_abi)
                return false;
            if (std::strcmp(kind, "+client") == 0) {
                if (staged.client_count == kMaxClients ||
                    !detail::parse_client(tokens, count, staged.clients[staged.client_count]))
                    return false;
                ++staged.client_count;
            } else if (std::strcmp(kind, "+engine") == 0) {
                if (staged.engine_count == kMaxEngines ||
                    !detail::parse_engine(tokens, count, staged.engines[staged.engine_count]))
                    return false;
                ++staged.engine_count;
            } else if (std::strcmp(kind, "+layout") == 0) {
                if (staged.engine_layout_count == kMaxLayouts ||
                    !detail::parse_engine_layout(tokens, count,
                                                 staged.engine_layouts[staged.engine_layout_count]))
                    return false;
                ++staged.engine_layout_count;
            } else if (std::strcmp(kind, "+cvar") == 0) {
                if (staged.cvar_layout_count == kMaxLayouts ||
                    !detail::parse_cvar_layout(tokens, count,
                                               staged.cvar_layouts[staged.cvar_layout_count]))
                    return false;
                ++staged.cvar_layout_count;
            } else if (std::strcmp(kind, "+tier0") == 0) {
                if (staged.tier0_count == kMaxTier0 ||
                    !detail::parse_tier0(tokens, count, staged.tier0[staged.tier0_count]))
                    return false;
                ++staged.tier0_count;
            } else {
                return false;
            }
        }
    }
    if (!saw_end || !saw_abi)
        return false;
    // Every reference must resolve before the document can be used.
    for (std::size_t i = 0; i < staged.engine_count; ++i)
        if (!detail::engine_layout(staged, staged.engines[i].layout))
            return false;
    for (std::size_t i = 0; i < staged.tier0_count; ++i)
        if (!detail::cvar_layout(staged, staged.tier0[i].layout))
            return false;
    staged.loaded = true;
    out = staged;
    return true;
}

#ifdef _WIN32
// Read and parse a document from disk. Missing, empty or oversized files are
// an ordinary "no remote definitions" result, not an error path.
inline bool load(const wchar_t* path, Definitions& out) {
    if (!path || !*path)
        return false;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                              FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return false;
    std::vector<char> data(kMaxBytes + 1);
    DWORD read = 0;
    bool okay = false;
    if (ReadFile(file, data.data(), static_cast<DWORD>(data.size() - 1), &read, nullptr) != 0)
        okay = read > 0;
    CloseHandle(file);
    if (!okay)
        return false;
    return parse(data.data(), read, out);
}
#endif

}  // namespace dolly_remote
