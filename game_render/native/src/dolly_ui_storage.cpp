// Per-user UI preferences only. This helper never touches game or shot data.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include "dolly_ui_storage.hpp"

#include <atomic>
#include <charconv>
#include <cstdint>
#include <cstdio>
#include <string_view>

namespace dolly {
namespace {
constexpr char envelope[] = "; Deadlock Dolly UI layout v1\n";
std::atomic<std::uint32_t> next_temporary{0};

struct File {
    HANDLE value = INVALID_HANDLE_VALUE;
    explicit File(HANDLE handle = INVALID_HANDLE_VALUE) : value(handle) {}
    ~File() { if (value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    File(const File&) = delete;
    File& operator=(const File&) = delete;
    bool close() {
        if (value == INVALID_HANDLE_VALUE) return true;
        const auto previous = value; value = INVALID_HANDLE_VALUE;
        return CloseHandle(previous) != 0;
    }
};
struct Temporary {
    std::wstring path;
    ~Temporary() { if (!path.empty()) DeleteFileW(path.c_str()); }
};

bool plain_ini(std::string_view text) {
    if (text.empty() || text.size() > kUiLayoutMaxFileBytes) return false;
    for (unsigned char c : text)
        if (!c || (c < 32 && c != '\r' && c != '\n' && c != '\t')) return false;
    bool section = false, preferences = false;
    std::size_t begin = 0;
    while (begin < text.size()) {
        const auto end = text.find('\n', begin);
        auto line = text.substr(begin, end == std::string_view::npos ? text.size() - begin : end - begin);
        if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
        while (!line.empty() && (line.front() == ' ' || line.front() == '\t')) line.remove_prefix(1);
        while (!line.empty() && (line.back() == ' ' || line.back() == '\t')) line.remove_suffix(1);
        if (!line.empty() && line.front() != ';' && line.front() != '#') {
            if (line.front() == '[') {
                const auto split = line.find("][");
                if (split == std::string_view::npos || split < 2 || line.back() != ']' ||
                    line.find('[', 1) != split + 1 || line.find(']') != split)
                    return false;
                section = true;
                if (line == "[DollyUI][Preferences]") preferences = true;
            } else if (!section) return false;
            // Unknown settings handlers own their body syntax. Keep their
            // content intact instead of rewriting or inventing migrations.
        }
        if (end == std::string_view::npos) break;
        begin = end + 1;
    }
    return preferences;
}

std::uint64_t checksum(std::string_view text) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : text) { hash ^= c; hash *= 1099511628211ULL; }
    return hash;
}

bool decode(const std::string& stored, std::string& payload) {
    const std::string_view input(stored);
    const std::string_view prefix(envelope);
    if (input.substr(0, prefix.size()) != prefix) {
        if (!plain_ini(input)) return false;
        payload = stored;
        return true;
    }
    auto remaining = input.substr(prefix.size());
    const auto bytes_end = remaining.find('\n');
    if (bytes_end == std::string_view::npos) return false;
    const auto bytes_line = remaining.substr(0, bytes_end);
    if (bytes_line.substr(0, 6) != "Bytes=") return false;
    const auto size_text = bytes_line.substr(6);
    std::uint64_t size = 0;
    const auto size_result = std::from_chars(size_text.data(), size_text.data() + size_text.size(), size);
    if (size_text.empty() || size_result.ec != std::errc{} ||
        size_result.ptr != size_text.data() + size_text.size() || size > kUiLayoutMaxFileBytes)
        return false;
    remaining.remove_prefix(bytes_end + 1);
    const auto hash_end = remaining.find('\n');
    if (hash_end == std::string_view::npos) return false;
    const auto hash_line = remaining.substr(0, hash_end);
    if (hash_line.substr(0, 7) != "Hash64=" || hash_line.size() != 23) return false;
    const auto hash_text = hash_line.substr(7);
    std::uint64_t expected = 0;
    const auto hash_result = std::from_chars(hash_text.data(), hash_text.data() + hash_text.size(), expected, 16);
    if (hash_result.ec != std::errc{} || hash_result.ptr != hash_text.data() + hash_text.size())
        return false;
    remaining.remove_prefix(hash_end + 1);
    if (remaining.empty() || remaining.front() != '\n') return false;
    remaining.remove_prefix(1);
    if (remaining.size() != size || checksum(remaining) != expected || !plain_ini(remaining))
        return false;
    payload.assign(remaining.data(), remaining.size());
    return true;
}

bool read_file(const std::wstring& path, std::string& contents) {
    File file(CreateFileW(path.c_str(), GENERIC_READ,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (file.value == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file.value, &size) || size.QuadPart <= 0 ||
        size.QuadPart > static_cast<LONGLONG>(kUiLayoutMaxFileBytes)) return false;
    std::string data(static_cast<std::size_t>(size.QuadPart), '\0');
    std::size_t copied = 0;
    while (copied < data.size()) {
        DWORD received = 0;
        if (!ReadFile(file.value, data.data() + copied, DWORD(data.size() - copied), &received, nullptr) ||
            !received) return false;
        copied += received;
    }
    LARGE_INTEGER final_size{};
    if (!GetFileSizeEx(file.value, &final_size) || final_size.QuadPart != size.QuadPart) return false;
    contents.swap(data);
    return true;
}

bool valid_file(const std::wstring& path, std::string& stored, std::string& payload) {
    return read_file(path, stored) && decode(stored, payload);
}

bool prepare_temporary(const std::wstring& destination, const std::string& contents,
                       Temporary& temporary) {
    for (unsigned attempt = 0; attempt < 16; ++attempt) {
        temporary.path = destination + L".tmp." + std::to_wstring(GetCurrentProcessId()) + L"." +
                         std::to_wstring(GetTickCount64()) + L"." + std::to_wstring(++next_temporary);
        File file(CreateFileW(temporary.path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                             CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
        if (file.value == INVALID_HANDLE_VALUE) {
            const auto error = GetLastError();
            // Never remove a temporary owned by another save/process.
            temporary.path.clear();
            if (error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS) continue;
            return false;
        }
        std::size_t copied = 0;
        while (copied < contents.size()) {
            DWORD written = 0;
            if (!WriteFile(file.value, contents.data() + copied, DWORD(contents.size() - copied), &written, nullptr) ||
                !written) return false;
            copied += written;
        }
        if (!FlushFileBuffers(file.value)) return false;
        return file.close();
    }
    return false;
}

bool commit_temporary(Temporary& temporary, const std::wstring& destination) {
    if (!MoveFileExW(temporary.path.c_str(), destination.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) return false;
    temporary.path.clear();
    return true;
}
} // namespace

bool load_ui_layout(const std::wstring& path, std::string& payload, bool* recovered) {
    payload.clear();
    if (recovered) *recovered = false;
    if (path.empty()) return false;
    try {
        std::string stored;
        if (valid_file(path, stored, payload)) return true;
        if (!valid_file(path + L".bak", stored, payload)) return false;
        if (recovered) *recovered = true;
        return true;
    } catch (...) {
        payload.clear();
        return false;
    }
}

bool save_ui_layout(const std::wstring& path, const std::string& payload) {
    if (path.empty()) return false;
    try {
        if (!plain_ini(payload)) return false;
        char hash[17]{};
        std::snprintf(hash, sizeof(hash), "%016llx", static_cast<unsigned long long>(checksum(payload)));
        const std::string encoded = std::string(envelope) + "Bytes=" + std::to_string(payload.size()) +
                                    "\nHash64=" + hash + "\n\n" + payload;
        if (encoded.size() > kUiLayoutMaxFileBytes) return false;
        std::string primary, primary_payload, backup, backup_payload;
        const bool primary_valid = valid_file(path, primary, primary_payload);
        const bool backup_valid = valid_file(path + L".bak", backup, backup_payload);
        const bool write_backup = primary_valid || !backup_valid;
        // Prepare every byte before changing either existing file. Write/flush
        // failures cannot truncate or replace a previously saved preference.
        Temporary new_primary, new_backup;
        if (!prepare_temporary(path, encoded, new_primary)) return false;
        if (write_backup && !prepare_temporary(path + L".bak", primary_valid ? primary : encoded, new_backup))
            return false;
        if (write_backup && !commit_temporary(new_backup, path + L".bak")) return false;
        return commit_temporary(new_primary, path);
    } catch (...) {
        return false;
    }
}
} // namespace dolly
