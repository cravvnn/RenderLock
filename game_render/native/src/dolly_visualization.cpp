#include "dolly_visualization.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <utility>

namespace dolly {
namespace {
std::uint64_t u64(const unsigned char* p) noexcept {
    return std::uint64_t(p[0]) | (std::uint64_t(p[1]) << 8) | (std::uint64_t(p[2]) << 16) |
           (std::uint64_t(p[3]) << 24) | (std::uint64_t(p[4]) << 32) |
           (std::uint64_t(p[5]) << 40) | (std::uint64_t(p[6]) << 48) | (std::uint64_t(p[7]) << 56);
}
std::uint32_t u32(const unsigned char* p) noexcept {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) | (std::uint32_t(p[2]) << 16) |
           (std::uint32_t(p[3]) << 24);
}
double number(const unsigned char* p) noexcept {
    std::uint64_t bits = 0;
    for (unsigned i = 0; i < 8; ++i)
        bits |= std::uint64_t(p[i]) << (8 * i);
    double result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}
bool valid_pose(const CameraPose& pose) noexcept {
    for (double value : pose)
        if (!std::isfinite(value) || std::abs(value) > 1e8)
            return false;
    return pose[6] >= .25 && pose[6] <= 8;
}
// Match Python's shortest-angle normalization, including very large finite
// authored angles where adding 180 can round before the modulo operation.
double wrapped_angle(double value) noexcept {
    double result = std::fmod(value + 180.0, 360.0);
    if (result < 0) result += 360.0;
    return result - 180.0;
}
bool valid_authored_pose(const CameraPose& pose) noexcept {
    for (std::size_t channel = 0; channel < 7; ++channel)
        if (!std::isfinite(pose[channel]) || (channel < 3 && std::abs(pose[channel]) > 1e8))
            return false;
    return pose[6] >= .5 && pose[6] <= 4;
}
using Vec3 = std::array<double, 3>;
double dot(const Vec3& a, const Vec3& b) noexcept {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
bool finite(const Vec3& p) noexcept {
    return std::isfinite(p[0]) && std::isfinite(p[1]) && std::isfinite(p[2]);
}
struct Basis {
    Vec3 forward{}, right{}, up{};
};
Basis angle_vectors(const CameraPose& p) noexcept {
    constexpr double radians = 3.14159265358979323846 / 180;
    const double pitch = std::remainder(p[3], 360.0) * radians;
    const double yaw = std::remainder(p[4], 360.0) * radians;
    const double roll = std::remainder(p[5], 360.0) * radians;
    const double sp = std::sin(pitch), cp = std::cos(pitch), sy = std::sin(yaw), cy = std::cos(yaw);
    const double sr = std::sin(roll), cr = std::cos(roll);
    // Source coordinates: Z up, yaw zero faces +X, positive pitch looks down.
    return {{cp * cy, cp * sy, -sp},
            {-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp},
            {cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp}};
}
struct Projection {
    Basis basis{};
    Vec3 origin{};
    double tan_x = 0, tan_y = 0, near_plane = 1, width = 0, height = 0;
    bool setup(const VisualizationView& view) noexcept {
        if (!valid_pose(view.pose) || !std::isfinite(view.horizontal_fov) ||
            view.horizontal_fov <= 1 || view.horizontal_fov >= 179 || !std::isfinite(view.width) ||
            !std::isfinite(view.height) || view.width <= 0 || view.height <= 0 ||
            view.width > 65536 || view.height > 65536 || !std::isfinite(view.near_plane) ||
            view.near_plane < .001 || view.near_plane > 10000)
            return false;
        basis = angle_vectors(view.pose);
        origin = {view.pose[0], view.pose[1], view.pose[2]};
        tan_x = std::tan(view.horizontal_fov * 3.14159265358979323846 / 360);
        tan_y = tan_x / (view.width / view.height);
        width = view.width;
        height = view.height;
        near_plane = view.near_plane;
        return std::isfinite(tan_x) && std::isfinite(tan_y) && tan_x > 0 && tan_y > 0;
    }
    Vec3 camera(const Vec3& world) const noexcept {
        const Vec3 relative{world[0] - origin[0], world[1] - origin[1], world[2] - origin[2]};
        return {dot(relative, basis.right), dot(relative, basis.up), dot(relative, basis.forward)};
    }
    std::array<double, 5> planes(const Vec3& p) const noexcept {
        return {p[2] - near_plane, p[2] * tan_x + p[0], p[2] * tan_x - p[0], p[2] * tan_y + p[1],
                p[2] * tan_y - p[1]};
    }
    VisualizationPoint screen(const Vec3& p) const noexcept {
        return {float(std::clamp((.5 + .5 * p[0] / (p[2] * tan_x)) * width, 0.0, width)),
                float(std::clamp((.5 - .5 * p[1] / (p[2] * tan_y)) * height, 0.0, height))};
    }
    bool point(const Vec3& world, VisualizationPoint& out) const noexcept {
        if (!finite(world))
            return false;
        const Vec3 p = camera(world);
        if (!finite(p))
            return false;
        for (double d : planes(p))
            if (d < 0 || !std::isfinite(d))
                return false;
        out = screen(p);
        return true;
    }
    bool line(const Vec3& world_a, const Vec3& world_b, VisualizationPoint& out_a,
              VisualizationPoint& out_b) const noexcept {
        if (!finite(world_a) || !finite(world_b))
            return false;
        const Vec3 a = camera(world_a), b = camera(world_b);
        if (!finite(a) || !finite(b))
            return false;
        const auto pa = planes(a), pb = planes(b);
        double enter = 0, leave = 1;
        for (std::size_t i = 0; i < pa.size(); ++i) {
            if (!std::isfinite(pa[i]) || !std::isfinite(pb[i]))
                return false;
            if (pa[i] < 0 && pb[i] < 0)
                return false;
            if (pa[i] < 0)
                enter = std::max(enter, pa[i] / (pa[i] - pb[i]));
            else if (pb[i] < 0)
                leave = std::min(leave, pa[i] / (pa[i] - pb[i]));
            if (enter > leave)
                return false;
        }
        Vec3 clipped_a{}, clipped_b{};
        for (std::size_t i = 0; i < 3; ++i) {
            clipped_a[i] = (1 - enter) * a[i] + enter * b[i];
            clipped_b[i] = (1 - leave) * a[i] + leave * b[i];
        }
        // Floating-point rounding at a crossing must never divide by zero.
        if (clipped_a[2] <= 0 || clipped_b[2] <= 0)
            return false;
        out_a = screen(clipped_a);
        out_b = screen(clipped_b);
        return true;
    }
};
Vec3 position(const CameraPose& p) noexcept {
    return {p[0], p[1], p[2]};
}
} // namespace

bool VisualizationPath::load(const void* data, std::size_t bytes, std::string& error) {
    error.clear();
    const auto fail = [&error](const char* message) {
        error = message;
        return false;
    };
    if (!data || bytes < kVisualizationHeaderBytes || bytes > kVisualizationMappingBytes)
        return fail("Viewer packet has an invalid byte length");
    const auto* raw = static_cast<const unsigned char*>(data);
    if (std::memcmp(raw, "DLYVIS01", 8) != 0)
        return fail("Viewer packet magic does not match");
    const auto sequence = u32(raw + 8), abi = u32(raw + 12), enabled = u32(raw + 16),
               selected = u32(raw + 20);
    const auto count = u32(raw + 24), path_bytes = u32(raw + 28), samples = u32(raw + 32),
               markers = u32(raw + 36);
    if (!sequence || (sequence & 1) || (abi < 1 || abi > kVisualizationAbi) || enabled > 1 ||
        count > NativePath::max_camera_keys || samples < 2 || samples > kVisualizationMaxSamples ||
        markers < 1 || markers > kVisualizationMaxMarkers)
        return fail("Viewer packet version, sequence, count, or drawing budget is invalid");
    const std::uint64_t session_id = abi >= 3 ? u64(raw + 44) : 0;
    const std::uint32_t metadata_bytes = abi >= 3 ? u32(raw + 52) : 0;
    const std::uint32_t flags = abi >= 3 ? u32(raw + 56) : 0;
    const std::size_t key_bytes = abi == 4 ? 136 : 64;
    if (abi >= 3) {
        if (u32(raw + 60) || (flags & ~(abi == 4 ? 3u : 1u)) || metadata_bytes != count * key_bytes)
            return fail("Viewer packet has invalid curve metadata fields");
    } else for (std::size_t i = abi == 1 ? 40 : 44; i < kVisualizationHeaderBytes; ++i)
        if (raw[i])
            return fail("Viewer packet has unknown reserved fields");
    if (!enabled) {
        if (count || path_bytes || selected || flags || metadata_bytes || bytes != kVisualizationHeaderBytes)
            return fail("A cleared viewer packet must not contain a camera path");
        *this = VisualizationPath{};
        return true;
    }
    if (!count || selected >= count || samples < count || path_bytes > NativePath::max_bytes ||
        bytes != kVisualizationHeaderBytes + std::size_t(count) * 8 + metadata_bytes + path_bytes)
        return fail("Viewer packet lengths or selected camera do not match");
    const auto* times = raw + kVisualizationHeaderBytes;
    const auto* metadata = times + std::size_t(count) * 8;
    const auto* blob = metadata + metadata_bytes;
    NativePath path;
    if (!path.load(blob, path_bytes, error))
        return false;
    const bool captured_lens = u32(blob + 8) == 2;
    const auto header_bytes =
        captured_lens ? NativePath::lens_header_bytes : NativePath::header_bytes;
    const auto segment_bytes =
        captured_lens ? NativePath::lens_segment_bytes : NativePath::segment_bytes;
    if (u32(blob + 12) + 1 != count)
        return fail("Viewer timestamps do not match the camera path");
    for (std::uint32_t i = 0; i < count; ++i) {
        const double time = number(times + i * 8);
        const double expected =
            i + 1 == count ? number(blob + 40)
                           : number(blob + (count == 1 ? 32 : header_bytes + i * segment_bytes));
        if (!std::isfinite(time) || time < 0 || time != expected ||
            (i && time <= number(times + (i - 1) * 8)))
            return fail("Viewer timestamps are not the authored camera timestamps");
    }
    VisualizationPath candidate;
    candidate.enabled_ = true;
    candidate.selected_camera_ = selected;
    candidate.camera_count_ = count;
    candidate.project_revision_ = abi >= 2 ? u32(raw + 40) : 0;
    candidate.session_id_ = session_id;
    candidate.unwrapped_rotation_ = flags & 1u;
    candidate.smooth_position_ = abi == 4 && (flags & 2u);
    try {
        const std::size_t segments = count - 1;
        const std::size_t edges = segments ? std::min<std::size_t>(samples - 1, segments * 32) : 0;
        candidate.points_.reserve(edges + 1);
        candidate.point_times_.reserve(edges + 1);
        candidate.breaks_.reserve(edges + 1);
        candidate.channel_breaks_.reserve(edges + 1);
        CameraPose pose{};
        if (!path.evaluate(number(times), pose) || !valid_pose(pose))
            return fail("Viewer path produced an unsupported camera pose");
        candidate.points_.push_back(pose);
        candidate.point_times_.push_back(number(times));
        candidate.breaks_.push_back(1);
        candidate.channel_breaks_.push_back(0x7f);
        for (std::size_t i = 0; i < segments; ++i) {
            const double begin = number(times + i * 8), end = number(times + (i + 1) * 8);
            const std::size_t steps = edges / segments + (i < edges % segments ? 1 : 0);
            std::uint8_t step_mask = 0;
            for (std::size_t channel = 0; channel < 7; ++channel) {
                const auto* record = blob + header_bytes + i * segment_bytes + 16 + 40 * channel;
                if (u32(record) == 0 && number(record + 8) != number(record + 16))
                    step_mask |= std::uint8_t(1u << channel);
            }
            for (std::size_t j = 1; j <= steps; ++j) {
                const double time =
                    j == steps ? end : begin + (end - begin) * (double(j) / double(steps));
                if (!path.evaluate(time, pose) || !valid_pose(pose))
                    return fail("Viewer path produced an unsupported camera pose");
                candidate.points_.push_back(pose);
                candidate.point_times_.push_back(time);
                candidate.breaks_.push_back(std::uint8_t(j == steps && (step_mask & 7)));
                candidate.channel_breaks_.push_back(j == steps ? step_mask : 0);
            }
        }
        candidate.graph_keys_.reserve(count);
        for (std::uint32_t index = 0; index < count; ++index) {
            const double time = number(times + std::size_t(index) * 8);
            if (!path.evaluate(time, pose) || !valid_pose(pose))
                return fail("Viewer graph key has an unsupported pose");
            VisualizationCamera key{pose, time, index};
            key.authored_pose = pose;
            if (abi >= 3) {
                const auto* entry = metadata + std::size_t(index) * key_bytes;
                for (std::size_t channel = 0; channel < 7; ++channel)
                    key.authored_pose[channel] = number(entry + channel * 8);
                key.source = u32(entry + 56);
                if (!valid_authored_pose(key.authored_pose) || key.source > 1 || u32(entry + 60))
                    return fail("Viewer graph key has invalid authoring metadata");
                for (std::size_t channel = 0; channel < 7; ++channel) {
                    const double delta = key.authored_pose[channel] - pose[channel];
                    const double difference = (channel == 4 || channel == 5) && !(flags & 1u)
                        ? std::remainder(wrapped_angle(key.authored_pose[channel]) -
                                         wrapped_angle(pose[channel]), 360.0) : delta;
                    if (std::abs(difference) > 1e-6)
                        return fail("Viewer graph authoring metadata does not match playback");
                }
                if (abi == 4) for (unsigned channel = 0; channel < 3; ++channel) {
                    const auto* part = entry + 64 + channel * 24;
                    auto& tangent = key.tangents[channel];
                    tangent.mode = u32(part);
                    tangent.incoming = number(part + 8); tangent.outgoing = number(part + 16);
                    if (tangent.mode > 3 || u32(part + 4) ||
                        !std::isfinite(tangent.incoming) || !std::isfinite(tangent.outgoing) ||
                        (tangent.mode == 1 && (tangent.incoming || tangent.outgoing)) ||
                        (tangent.mode == 2 && tangent.incoming != tangent.outgoing))
                        return fail("Viewer graph has invalid XYZ tangent metadata");
                }
            }
            candidate.graph_keys_.push_back(key);
        }
        // Evenly spaced glyphs keep very large paths bounded. Reserve a slot
        // for the selected camera so editing never loses its marker.
        const std::size_t visible = std::min<std::size_t>(markers, count);
        std::vector<std::uint32_t> indices;
        indices.reserve(visible);
        if (visible == count)
            for (std::uint32_t i = 0; i < count; ++i)
                indices.push_back(i);
        else {
            indices.push_back(selected);
            for (std::size_t j = 0; j + 1 < visible; ++j) {
                const auto index =
                    std::uint32_t(visible == 2 ? 0 : j * (count - 1) / (visible - 2));
                if (index != selected)
                    indices.push_back(index);
            }
            std::sort(indices.begin(), indices.end());
        }
        candidate.cameras_.reserve(indices.size());
        for (auto index : indices) {
            const double time = number(times + std::size_t(index) * 8);
            if (!path.evaluate(time, pose) || !valid_pose(pose))
                return fail("Viewer camera marker has an unsupported pose");
            candidate.cameras_.push_back({pose, time, index});
        }
    } catch (const std::exception&) {
        return fail("Could not allocate bounded viewer geometry");
    }
    *this = std::move(candidate);
    return true;
}

bool valid_visualization_curve_action(const VisualizationPath& path, double index,
                                      const CameraPose& request, bool select) noexcept {
    if (!path.enabled() || !path.session_id() || !path.project_revision() ||
        !std::isfinite(index) || index != std::floor(index) || index < 0 ||
        index >= path.graph_keys().size()) return false;
    for (double part : request) if (!std::isfinite(part)) return false;
    for (unsigned i = 4; i <= 5; ++i)
        if (request[i] < 0 || request[i] > UINT32_MAX || request[i] != std::floor(request[i]))
            return false;
    const auto session = std::uint64_t(request[4]) | (std::uint64_t(request[5]) << 32);
    const auto& key = path.graph_keys()[std::size_t(index)];
    if (request[0] != path.project_revision() || session != path.session_id() ||
        request[3] != key.time || request[6] != 0) return false;
    if (select) return request[1] == 0 && request[2] == 0;
    if (path.selected_camera() != std::uint32_t(index) || key.source || request[1] < 0 ||
        request[1] > 6 || request[1] != std::floor(request[1]) || std::abs(request[2]) > 1e8)
        return false;
    if (std::abs(key.authored_pose[unsigned(request[1])]) > 1e8) return false;
    return request[1] != 6 || (request[2] >= .5 && request[2] <= 4);
}

bool valid_visualization_tangent_action(const VisualizationPath& path, double value,
                                        const CameraPose& request) noexcept {
    if (!std::isfinite(value) || value < 0 || value != std::floor(value) ||
        value >= path.graph_keys().size() * 12 || !path.smooth_position()) return false;
    const auto packed = std::uint32_t(value), index = packed / 12, mode = packed % 4;
    CameraPose anchor = request;
    anchor[1] = anchor[2] = 0;
    if (!valid_visualization_curve_action(path, index, anchor, true) ||
        path.selected_camera() != index || path.graph_keys()[index].source ||
        !std::isfinite(request[1]) || !std::isfinite(request[2])) return false;
    return mode < 2 ? request[1] == 0 && request[2] == 0 :
           mode != 2 || request[1] == request[2];
}

bool VisualizationPreview::load(const void* data, std::size_t bytes,
                                 const VisualizationPath& committed, std::string& error) {
    error.clear();
    const auto fail=[&](const char* message) {error=message;return false;};
    if(!data || bytes<kVisualizationPreviewHeaderBytes || bytes>kVisualizationMappingBytes)
        return fail("Curve preview envelope length is invalid");
    const auto* raw=static_cast<const unsigned char*>(data);
    const auto sequence=u32(raw+8), enabled=u32(raw+16), source=u32(raw+20), token=u32(raw+24);
    const auto payload=u32(raw+56);
    if(std::memcmp(raw,"DLYPRV01",8) || !sequence || (sequence&1) || u32(raw+12)!=1 ||
       enabled>1 || u32(raw+60) || bytes!=kVisualizationPreviewHeaderBytes+payload)
        return fail("Curve preview envelope version or fields are invalid");
    VisualizationPreview candidate;
    candidate.source=source;candidate.token=token;candidate.revision=u32(raw+28);
    candidate.session=u64(raw+32);candidate.index=u32(raw+40);candidate.channel=u32(raw+44);
    candidate.time=number(raw+48);
    if(!enabled) {
        const bool empty=!(source || token || candidate.revision || candidate.session ||
                           candidate.index || candidate.channel || candidate.time);
        if(payload || !std::isfinite(candidate.time) || candidate.time<0 ||
           candidate.index>=NativePath::max_camera_keys || candidate.channel>6 ||
           (!empty && (source<1 || source>2 || !token || !candidate.revision || !candidate.session)))
            return fail("Cleared curve preview envelope is invalid");
        *this=std::move(candidate);return true;
    }
    if(source<1 || source>2 || !token || !payload || candidate.channel>6 ||
       !committed.enabled() || !committed.session_id() || !committed.project_revision() ||
       candidate.revision!=committed.project_revision() || candidate.session!=committed.session_id() ||
       candidate.index!=committed.selected_camera() || candidate.index>=committed.graph_keys().size() ||
       candidate.time!=committed.graph_keys()[candidate.index].time || committed.graph_keys()[candidate.index].source ||
       payload<kVisualizationHeaderBytes || u32(raw+kVisualizationPreviewHeaderBytes+12)!=4)
        return fail("Curve preview does not identify the committed free camera");
    try {
        auto path=std::make_shared<VisualizationPath>();
        if(!path->load(raw+kVisualizationPreviewHeaderBytes,payload,error)) return false;
        if(!path->enabled() || path->project_revision()!=candidate.revision || path->session_id()!=candidate.session ||
           path->selected_camera()!=candidate.index || path->camera_count()!=committed.camera_count() ||
           path->graph_keys().size()!=committed.graph_keys().size())
            return fail("Curve preview payload changed its shot identity");
        for(std::size_t i=0;i<path->graph_keys().size();++i)
            if(path->graph_keys()[i].time!=committed.graph_keys()[i].time ||
               path->graph_keys()[i].source!=committed.graph_keys()[i].source)
                return fail("Curve preview changed camera times or sources");
        candidate.path=std::move(path);
    } catch(...) {return fail("Could not allocate bounded curve preview");}
    *this=std::move(candidate);return true;
}

bool project_visualization_point(const VisualizationView& view, const Vec3& point,
                                 VisualizationPoint& out) noexcept {
    Projection projection;
    return projection.setup(view) && projection.point(point, out);
}
bool project_visualization_line(const VisualizationView& view, const Vec3& a, const Vec3& b,
                                VisualizationPoint& screen_a,
                                VisualizationPoint& screen_b) noexcept {
    Projection projection;
    return projection.setup(view) && projection.line(a, b, screen_a, screen_b);
}

bool project_visualization(const VisualizationPath& path, const VisualizationView& view,
                           VisualizationGeometry& out) noexcept {
    out.line_count = out.label_count = 0;
    if (!path.enabled())
        return false;
    Projection projection;
    if (!projection.setup(view))
        return false;
    const auto append = [&](const Vec3& a, const Vec3& b, VisualizationKind kind) {
        if (out.line_count >= out.lines.size())
            return;
        auto& line = out.lines[out.line_count];
        if (projection.line(a, b, line.a, line.b)) {
            line.kind = kind;
            ++out.line_count;
        }
    };
    const auto& points = path.points();
    for (std::size_t i = 1; i < points.size(); ++i)
        if (!path.breaks()[i])
            append(position(points[i - 1]), position(points[i]), VisualizationKind::Path);
    for (const auto& camera : path.cameras()) {
        const auto& pose = camera.pose;
        const Vec3 center = position(pose);
        const auto basis = angle_vectors(pose);
        const auto kind = camera.index == path.selected_camera() ? VisualizationKind::SelectedCamera
                                                                 : VisualizationKind::Camera;
        // An orientation glyph, not a claim about the camera's final lens.
        // Use a fixed shape; framing edits must not resize the saved marker.
        std::array<Vec3, 4> corners{};
        constexpr double xs[4] = {-1, 1, 1, -1}, ys[4] = {-1, -1, 1, 1};
        for (std::size_t i = 0; i < corners.size(); ++i)
            for (std::size_t axis = 0; axis < 3; ++axis)
                corners[i][axis] = center[axis] + 24 * basis.forward[axis] +
                                   12 * xs[i] * basis.right[axis] +
                                   6.75 * ys[i] * basis.up[axis];
        for (std::size_t i = 0; i < corners.size(); ++i) {
            append(center, corners[i], kind);
            append(corners[i], corners[(i + 1) % corners.size()], kind);
        }
        if (out.label_count < out.labels.size()) {
            auto& label = out.labels[out.label_count];
            if (projection.point(center, label.point)) {
                label.camera_index = camera.index;
                label.selected = kind == VisualizationKind::SelectedCamera;
                ++out.label_count;
            }
        }
    }
    return true;
}

} // namespace dolly
