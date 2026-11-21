#pragma once
#include <windows.h>
#include <fstream>
#include <string>

namespace dolly::player_capture {
// v4 accepts only an existing owned local-drive directory, never a link or UNC.
inline bool scratch_directory(const std::string& hex, const std::string& request,
                              std::wstring& result) {
    if (hex.empty() || hex.size() > 1536 || hex.size() % 2 || request.size() != 32 ||
        request.find_first_not_of("0123456789abcdef") != std::string::npos)
        return false;
    std::string bytes;
    for (size_t i = 0; i < hex.size(); i += 2) {
        auto digit = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            return -1;
        };
        int a = digit(hex[i]), b = digit(hex[i+1]);
        if (a < 0 || b < 0) return false;
        unsigned char value = static_cast<unsigned char>(a*16+b);
        if (value < 32) return false;
        bytes += static_cast<char>(value);
    }
    int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(),
                                   static_cast<int>(bytes.size()), nullptr, 0);
    if (length < 4) return false;
    std::wstring path(length, L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(),
                            static_cast<int>(bytes.size()), path.data(), length)) return false;
    if (!((path[0] >= L'A' && path[0] <= L'Z') || (path[0] >= L'a' && path[0] <= L'z')) ||
        path[1] != L':' || path[2] != L'\\' || path.find(L'/', 3) != std::wstring::npos ||
        path.find(L':', 2) != std::wstring::npos) return false;
    const std::wstring leaf = L"dolly-players-" + std::wstring(request.begin(), request.end());
    if (path.substr(path.find_last_of(L'\\')+1) != leaf) return false;
    for (size_t pos = 3; pos <= path.size(); ++pos) {
        if (pos != path.size() && path[pos] != L'\\') continue;
        auto end = path.substr(0, pos);
        auto name = end.substr(end.find_last_of(L'\\')+1);
        if (name == L"." || name == L".." || (!name.empty() && (name.back() == L'.' || name.back() == L' '))) return false;
        DWORD flags = GetFileAttributesW(end.c_str());
        if (flags == INVALID_FILE_ATTRIBUTES || !(flags & FILE_ATTRIBUTE_DIRECTORY) ||
            (flags & FILE_ATTRIBUTE_REPARSE_POINT)) return false;
    }
    auto ownerPath = path + L"\\.dolly-players-owner";
    DWORD flags = GetFileAttributesW(ownerPath.c_str());
    if (flags == INVALID_FILE_ATTRIBUTES || (flags & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))) return false;
    std::ifstream owner(ownerPath, std::ios::binary);
    std::string identity(33, '\0');
    owner.read(identity.data(), 33);
    if (owner.gcount() != 32 || identity.substr(0, 32) != request) return false;
    for (auto name : {L"dolly_owner_color_frame.bin", L"dolly_owner_frame_meta.bin"})
        if (GetFileAttributesW((path + L"\\" + name).c_str()) != INVALID_FILE_ATTRIBUTES) return false;
    result = path + L"\\";
    return true;
}
}
