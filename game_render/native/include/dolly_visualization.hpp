#pragma once

#include "dolly_path.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>
#include <memory>

namespace dolly {

constexpr std::size_t kVisualizationMappingBytes = 2 * 1024 * 1024;
constexpr std::size_t kVisualizationHeaderBytes = 64;
constexpr std::size_t kVisualizationMaxSamples = 4096;
constexpr std::size_t kVisualizationMaxMarkers = 128;
constexpr std::uint32_t kVisualizationAbi = 4;

// Header offsets match dolly.visualization_wire. The caller checks its
// seqlock, then passes precisely header + times + metadata + camera-path bytes.
// ABI3 adds full-shot authoring/source metadata and an opened-shot identity.
// ABI4 adds saved/effective XYZ slope modes/pairs and the Smooth-position flag.
#pragma pack(push, 1)
struct VisualizationHeader {
    char magic[8];
    std::uint32_t sequence, abi, enabled, selected_camera, camera_count;
    std::uint32_t path_bytes, sample_budget, marker_budget;
    std::uint32_t project_revision;
    std::uint64_t session_id;
    std::uint32_t key_metadata_bytes, flags, reserved;
};
#pragma pack(pop)
static_assert(sizeof(VisualizationHeader) == kVisualizationHeaderBytes, "Viewer header layout");

struct VisualizationCamera {
    CameraPose pose{};
    double time = 0;
    std::uint32_t index = 0;
    CameraPose authored_pose{};
    std::uint32_t source = 0;
    struct Tangent { std::uint32_t mode = 0; double incoming = 0, outgoing = 0; };
    std::array<Tangent, 3> tangents{};
};

class VisualizationPath {
public:
    // Parse/sample only on the worker. Failed loads preserve the prior object.
    // Publish successful objects immutably; the draw callback never resamples.
    bool load(const void* data, std::size_t bytes, std::string& error);
    bool enabled() const noexcept { return enabled_; }
    std::uint32_t selected_camera() const noexcept { return selected_camera_; }
    std::size_t camera_count() const noexcept { return camera_count_; }
    std::uint32_t project_revision() const noexcept { return project_revision_; }
    std::uint64_t session_id() const noexcept { return session_id_; }
    bool unwrapped_rotation() const noexcept { return unwrapped_rotation_; }
    bool smooth_position() const noexcept { return smooth_position_; }
    const std::vector<CameraPose>& points() const noexcept { return points_; }
    const std::vector<double>& point_times() const noexcept { return point_times_; }
    // Every authored key, independent of the world-guide marker budget.
    const std::vector<VisualizationCamera>& graph_keys() const noexcept { return graph_keys_; }
    const std::vector<VisualizationCamera>& cameras() const noexcept { return cameras_; }
    const std::vector<std::uint8_t>& breaks() const noexcept { return breaks_; }
    const std::vector<std::uint8_t>& channel_breaks() const noexcept { return channel_breaks_; }
    // Entry i marks a discontinuity between points i-1 and i for that channel.
    bool channel_breaks(std::size_t i, unsigned channel) const noexcept {
        return i < channel_breaks_.size() && channel < 7 && (channel_breaks_[i] & (1u << channel));
    }

private:
    bool enabled_ = false;
    std::uint32_t selected_camera_ = 0;
    std::size_t camera_count_ = 0;
    std::uint32_t project_revision_ = 0;
    std::uint64_t session_id_ = 0;
    bool unwrapped_rotation_ = false;
    bool smooth_position_ = false;
    std::vector<CameraPose> points_;
    std::vector<double> point_times_;
    std::vector<VisualizationCamera> graph_keys_;
    std::vector<std::uint8_t> breaks_;
    std::vector<std::uint8_t> channel_breaks_;
    std::vector<VisualizationCamera> cameras_;
};

// Full-shot graph actions use viewer metadata rather than weakening the
// separate 32-row camera-list page validation.
bool valid_visualization_curve_action(const VisualizationPath& path, double index,
                                      const CameraPose& request, bool select) noexcept;
// value packs index*12 + XYZ-axis*4 + Auto/Flat/Linked/Broken. The seven
// request fields retain revision, atomic slope pair, key time and session.
bool valid_visualization_tangent_action(const VisualizationPath& path, double value,
                                        const CameraPose& request) noexcept;

constexpr std::size_t kVisualizationPreviewHeaderBytes = 64;
struct VisualizationPreview {
    std::uint32_t source = 0, token = 0, revision = 0, index = 0, channel = 0;
    std::uint64_t session = 0, received_ms = 0;
    double time = 0;
    std::shared_ptr<const VisualizationPath> path;
    std::shared_ptr<const VisualizationPath> base;
    // A separate render-only envelope; invalid drafts never replace the
    // authoritative editor/path snapshot. A disabled envelope has no path.
    bool load(const void* data, std::size_t bytes, const VisualizationPath& committed,
              std::string& error);
};

struct VisualizationView {
    // Final applied Source pose and horizontal FOV from the verified main
    // view callback. pose[6] is the framing control; viewport dimensions
    // describe the rendered image containing the overlay.
    CameraPose pose{};
    double horizontal_fov = 0;
    double width = 0, height = 0;
    double near_plane = 1;
};
enum class VisualizationKind : std::uint32_t { Path = 0, Camera = 1, SelectedCamera = 2 };
struct VisualizationPoint {
    float x = 0, y = 0;
};
struct VisualizationLine {
    VisualizationPoint a{}, b{};
    VisualizationKind kind = VisualizationKind::Path;
};
struct VisualizationLabel {
    VisualizationPoint point{};
    std::uint32_t camera_index = 0;
    bool selected = false;
};
constexpr std::size_t kVisualizationMaxLines =
    kVisualizationMaxSamples - 1 + kVisualizationMaxMarkers * 8;
struct VisualizationGeometry {
    std::array<VisualizationLine, kVisualizationMaxLines> lines{};
    std::array<VisualizationLabel, kVisualizationMaxMarkers> labels{};
    std::size_t line_count = 0, label_count = 0;
};

// Pure projection into bounded caller-owned storage. No allocation, IPC,
// clocks, engine calls, or D3D work. Segments are clipped against the near
// plane and all four viewport planes. Guides intentionally have no occlusion.
bool project_visualization(const VisualizationPath& path, const VisualizationView& view,
                           VisualizationGeometry& out) noexcept;
bool project_visualization_point(const VisualizationView& view, const std::array<double, 3>& point,
                                 VisualizationPoint& out) noexcept;
bool project_visualization_line(const VisualizationView& view, const std::array<double, 3>& a,
                                const std::array<double, 3>& b, VisualizationPoint& screen_a,
                                VisualizationPoint& screen_b) noexcept;

} // namespace dolly
