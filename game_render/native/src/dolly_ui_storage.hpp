#pragma once

#include <cstddef>
#include <string>

namespace dolly {
// The complete file, including its integrity envelope, is bounded to 1 MiB.
constexpr std::size_t kUiLayoutMaxFileBytes = 1024 * 1024;

// Return raw ImGui INI text. A damaged/missing primary falls back to .bak.
// Legacy bounded plain INI is accepted; new saves carry a length/checksum.
bool load_ui_layout(const std::wstring& path, std::string& payload,
                    bool* recovered = nullptr);

// The parent directory must already exist. Prepare flushed same-directory
// temporary files, retain a valid backup, then atomically replace the primary.
// A failed primary rename retains the prior primary; its backup may have
// advanced to the same validated prior contents. Orphan temporaries are ignored.
bool save_ui_layout(const std::wstring& path, const std::string& payload);
} // namespace dolly
