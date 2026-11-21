#pragma once
#include <string>
#include <string_view>

namespace dolly::video {
// FFmpeg runs at loglevel error. Keep its first diagnostic, before shutdown
// summaries such as "Nothing was written" obscure the original failure.
inline std::wstring encoder_error_summary(std::wstring_view log, std::size_t limit = 110) {
    const auto start = log.find_first_not_of(L" \t\r\n");
    if (start == std::wstring_view::npos || !limit)
        return {};
    log.remove_prefix(start);
    log = log.substr(0, log.find_first_of(L"\r\n"));
    // Remove FFmpeg's component/address prefix, never arbitrary bracketed text.
    if (log.front() == L'[') {
        const auto end = log.find(L"] ");
        if (end != std::wstring_view::npos && log.substr(0, end).find(L" @ ") != std::wstring_view::npos)
            log.remove_prefix(end + 2);
    }
    const auto end = log.find_last_not_of(L" \t");
    if (end == std::wstring_view::npos)
        return {};
    log = log.substr(0, end + 1);
    if (log.size() > limit && limit >= 3)
        return std::wstring(log.substr(0, limit - 3)) + L"...";
    return std::wstring(log.substr(0, limit));
}
} // namespace dolly::video
