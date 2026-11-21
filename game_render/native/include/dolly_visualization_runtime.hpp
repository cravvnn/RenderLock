#pragma once

#include "dolly_visualization.hpp"
#include <cstdint>
#include <memory>

namespace dolly {

// Call only from the existing control worker. The optional read-only mapping
// is separate from ABI-3 camera control and is sampled no faster than 10 Hz.
// A disconnected editor drops guides immediately and closes the mapping.
void visualization_worker_tick(const wchar_t* main_mapping_name, bool connected) noexcept;

// Immutable pre-sampled geometry. No IPC, allocation, engine calls, or GPU
// work. Retired snapshots remain worker-owned until readers release them.
std::shared_ptr<const VisualizationPath> visualization_snapshot() noexcept;
std::shared_ptr<const VisualizationPreview> visualization_preview_snapshot() noexcept;
// Render-local gesture admission never changes camera state or saved curves.
std::uint32_t visualization_begin_native_preview(const VisualizationPath&, unsigned index, unsigned channel) noexcept;
void visualization_end_native_preview(std::uint32_t token) noexcept;
bool visualization_native_preview_request_matches(double index, const CameraPose&, unsigned channel = UINT32_MAX) noexcept;

enum class VisualizationRuntimeState : std::uint32_t {
    Disconnected = 0,
    Waiting = 1,
    Ready = 2,
    Disabled = 3,
    Invalid = 4,
    Updating = 5,
    Unavailable = 6
};
VisualizationRuntimeState visualization_runtime_state() noexcept;

} // namespace dolly
