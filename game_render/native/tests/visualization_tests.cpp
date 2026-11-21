#include "dolly_visualization.hpp"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
void require(bool ok, const char* message) {
    if (!ok)
        throw std::runtime_error(message);
}
bool close(double a, double b) {
    return std::abs(a - b) < .001;
}
void u32(std::vector<unsigned char>& out, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        out.push_back(static_cast<unsigned char>(value >> (8 * i)));
}
void number(std::vector<unsigned char>& out, double value) {
    std::uint64_t bits;
    std::memcpy(&bits, &value, 8);
    for (unsigned i = 0; i < 8; ++i)
        out.push_back(static_cast<unsigned char>(bits >> (8 * i)));
}
void change_u32(std::vector<unsigned char>& data, std::size_t offset, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        data.at(offset + i) = static_cast<unsigned char>(value >> (8 * i));
}
void change_number(std::vector<unsigned char>& data, std::size_t offset, double value) {
    std::vector<unsigned char> encoded;
    number(encoded, value);
    for (unsigned i = 0; i < 8; ++i)
        data.at(offset + i) = encoded[i];
}
std::vector<unsigned char> packet(unsigned cameras = 3, unsigned samples = 2048,
                                  unsigned markers = 64, bool step = false, bool lens = false) {
    std::vector<unsigned char> blob{'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    u32(blob, lens ? 2 : 1);
    u32(blob, cameras - 1);
    u32(blob, lens ? 8 : 7);
    u32(blob, 0);
    number(blob, cameras - 1);
    number(blob, 0);
    number(blob, cameras - 1);
    auto pose = [](unsigned index) {
        return dolly::CameraPose{100 + double(index) * 10, double(index) * 2, 0, 0, 0, 0, 2};
    };
    for (auto v : pose(0))
        number(blob, v);
    if (lens)
        number(blob, .9);
    for (auto v : pose(cameras - 1))
        number(blob, v);
    if (lens)
        number(blob, .9);
    for (unsigned i = 0; i + 1 < cameras; ++i) {
        number(blob, i);
        number(blob, i + 1);
        for (unsigned c = 0; c < 7; ++c) {
            u32(blob, step ? 0 : 1);
            u32(blob, c >= 3 ? 1 : 0);
            number(blob, pose(i)[c]);
            number(blob, pose(i + 1)[c]);
            number(blob, 0);
            number(blob, 0);
        }
        if (lens) {
            u32(blob, 1);
            u32(blob, 1);
            number(blob, .9);
            number(blob, .9);
            number(blob, 0);
            number(blob, 0);
        }
    }
    std::vector<unsigned char> out{'D', 'L', 'Y', 'V', 'I', 'S', '0', '1'};
    u32(out, 2);
    u32(out, 1);
    u32(out, 1);
    u32(out, cameras / 2);
    u32(out, cameras);
    u32(out, std::uint32_t(blob.size()));
    u32(out, samples);
    u32(out, markers);
    out.resize(64);
    for (unsigned i = 0; i < cameras; ++i)
        number(out, i);
    out.insert(out.end(), blob.begin(), blob.end());
    return out;
}
std::vector<unsigned char> curve_packet(unsigned cameras = 70, unsigned step_channel = 7) {
    auto bytes = packet(cameras, 2048, 64);
    const std::size_t old_blob = 64 + std::size_t(cameras) * 8;
    if (step_channel < 7) {
        const auto value = [step_channel](unsigned i) { return step_channel == 6 ? 1.0 + .01 * i : double(i); };
        change_number(bytes, old_blob + 48 + step_channel * 8, value(0));
        change_number(bytes, old_blob + 104 + step_channel * 8, value(cameras - 1));
        for (unsigned i = 0; i + 1 < cameras; ++i) {
            const auto record = old_blob + 160 + i * 296 + 16 + step_channel * 40;
            change_u32(bytes, record, 0);
            change_number(bytes, record + 8, value(i));
            change_number(bytes, record + 16, value(i + 1));
        }
    }
    dolly::NativePath path;
    std::string error;
    require(path.load(bytes.data() + old_blob, bytes.size() - old_blob, error), "Curve fixture path rejected");
    std::vector<unsigned char> metadata;
    for (unsigned i = 0; i < cameras; ++i) {
        dolly::CameraPose pose;
        require(path.evaluate(i, pose), "Curve fixture sample rejected");
        for (double value : pose) number(metadata, value);
        u32(metadata, i == 10 ? 1 : 0); u32(metadata, 0);
    }
    bytes.insert(bytes.begin() + old_blob, metadata.begin(), metadata.end());
    change_u32(bytes, 12, 3); change_u32(bytes, 40, 17);
    change_u32(bytes, 44, 0x89abcdef); change_u32(bytes, 48, 0xf1234567);
    change_u32(bytes, 52, cameras * 64);
    return bytes;
}
void test_curve_contract() {
    auto bytes = curve_packet();
    dolly::VisualizationPath path;
    std::string error;
    require(path.load(bytes.data(), bytes.size(), error), "ABI3 full-shot graph rejected");
    require(path.session_id() == 0xf123456789abcdefULL && path.graph_keys().size() == 70 &&
            path.graph_keys()[69].authored_pose[0] == 790 && path.graph_keys()[10].source == 1,
            "Graph authoring metadata/session changed");
    dolly::CameraPose request{17, 0, 0, 69, 0x89abcdef, 0xf1234567, 0};
    require(dolly::valid_visualization_curve_action(path, 69, request, true),
            "Full-shot selection beyond a 32-row page rejected");
    require(!dolly::valid_visualization_curve_action(path, 69, request, false),
            "Value edit accepted a different graph selection");
    request[3] = 35;
    for (unsigned channel = 0; channel < 7; ++channel) {
        request[1] = channel; request[2] = channel == 6 ? 2.39 : 123.125;
        require(dolly::valid_visualization_curve_action(path, 35, request, false),
                "Valid unified curve channel rejected");
    }
    request[2] = 4.01;
    require(!dolly::valid_visualization_curve_action(path, 35, request, false), "Out-of-range aspect accepted");
    request[1] = 0; request[2] = 123;
    for (unsigned field : {0u, 3u, 4u, 5u, 6u}) {
        auto stale = request; stale[field] += 1;
        require(!dolly::valid_visualization_curve_action(path, 35, stale, false), "Stale curve anchor accepted");
    }
    for (double invalid : {-.5, 7.0, .5, std::numeric_limits<double>::infinity()}) {
        auto malformed = request; malformed[1] = invalid;
        require(!dolly::valid_visualization_curve_action(path, 35, malformed, false), "Invalid curve axis accepted");
    }
    for (double invalid : {-1.0, 70.0, .5, std::numeric_limits<double>::quiet_NaN()})
        require(!dolly::valid_visualization_curve_action(path, invalid, request, false), "Invalid full-shot index accepted");
    request[3] = 10; request[1] = request[2] = 0;
    require(dolly::valid_visualization_curve_action(path, 10, request, true), "Tracked camera could not be selected");
    // Reselect the tracked key in the immutable snapshot: every group stays read-only.
    auto attached = bytes; change_u32(attached, 20, 10);
    require(path.load(attached.data(), attached.size(), error), "Tracked curve fixture rejected");
    request[2] = 2;
    require(!dolly::valid_visualization_curve_action(path, 10, request, false), "Tracked camera accepted a value edit");
    for (unsigned channel : {3u, 4u, 5u, 6u}) {
        auto stepped = curve_packet(3, channel);
        require(path.load(stepped.data(), stepped.size(), error), "Stepped rotation/framing curve rejected");
        require(path.channel_breaks(32, channel) && path.channel_breaks(64, channel) && !path.breaks()[32],
                "Channel-specific discontinuity lost or changed world guides");
    }
    auto wrapped = curve_packet(3);
    const std::size_t metadata = 64 + 3 * 8;
    change_number(wrapped, metadata + 64 + 4 * 8, 360);
    require(path.load(wrapped.data(), wrapped.size(), error) && path.graph_keys()[1].pose[4] == 0 &&
            path.graph_keys()[1].authored_pose[4] == 360, "Authored wrapped angle lost");
    auto huge = wrapped; change_number(huge, metadata + 64 + 4 * 8, 3.6e12);
    require(path.load(huge.data(), huge.size(), error) && path.graph_keys()[1].authored_pose[4] == 3.6e12,
            "Legacy huge shortest angle stopped valid guides from rendering");
    dolly::CameraPose huge_edit{17, 4, 0, 1, 0x89abcdef, 0xf1234567, 0};
    require(!dolly::valid_visualization_curve_action(path, 1, huge_edit, false),
            "An out-of-editor-range original value accepted a destructive clamp edit");
    change_u32(wrapped, 56, 1);
    require(!path.load(wrapped.data(), wrapped.size(), error), "Unwrapped authoring/playback mismatch accepted");
    for (std::size_t offset : {std::size_t(52), std::size_t(56), std::size_t(60), metadata + 60}) {
        auto bad = curve_packet(3); change_u32(bad, offset, 0xffffffff);
        require(!path.load(bad.data(), bad.size(), error), "Malformed curve metadata accepted");
    }
    auto bad_source = curve_packet(3); change_u32(bad_source, metadata + 56, 2);
    require(!path.load(bad_source.data(), bad_source.size(), error), "Unknown camera source accepted");
    auto mismatched = curve_packet(3); change_number(mismatched, metadata, 12345);
    require(!path.load(mismatched.data(), mismatched.size(), error), "Unrelated authoring position accepted");
}
std::vector<unsigned char> tangent_packet() {
    auto bytes=curve_packet(3);
    const std::size_t metadata=64+3*8;
    std::vector<unsigned char> expanded;
    for(unsigned index=0;index<3;++index) {
        expanded.insert(expanded.end(),bytes.begin()+metadata+index*64,bytes.begin()+metadata+(index+1)*64);
        for(unsigned axis=0;axis<3;++axis) {
            u32(expanded,axis);u32(expanded,0);
            number(expanded,axis==1 ? 0 : 5);number(expanded,axis==1 ? 0 : 5);
        }
    }
    bytes.erase(bytes.begin()+metadata,bytes.begin()+metadata+3*64);
    bytes.insert(bytes.begin()+metadata,expanded.begin(),expanded.end());
    change_u32(bytes,12,4);change_u32(bytes,52,3*136);change_u32(bytes,56,2);
    return bytes;
}
void test_tangent_contract() {
    auto bytes=tangent_packet();dolly::VisualizationPath path;std::string error;
    require(path.load(bytes.data(),bytes.size(),error) && path.smooth_position(),"ABI4 XYZ tangent metadata rejected");
    require(path.graph_keys()[1].tangents[0].incoming==5 && path.graph_keys()[1].tangents[2].mode==2,
            "XYZ tangent modes/slopes changed in native parsing");
    dolly::CameraPose request{17,12.125,-8.25,1,0x89abcdef,0xf1234567,0};
    require(dolly::valid_visualization_tangent_action(path,12+3,request),"Atomic broken tangent pair rejected");
    require(!dolly::valid_visualization_tangent_action(path,12+2,request),"Unequal linked tangent pair accepted");
    request[2]=request[1];require(dolly::valid_visualization_tangent_action(path,12+2,request),"Linked tangent pair rejected");
    request[1]=request[2]=1e12;
    require(dolly::valid_visualization_tangent_action(path,12+2,request),"Finite short-interval slopes received a world-coordinate cap");
    request[1]=request[2]=0;
    for(unsigned mode:{0u,1u}) require(dolly::valid_visualization_tangent_action(path,12+mode,request),"Auto/Flat reset rejected");
    request[1]=1;require(!dolly::valid_visualization_tangent_action(path,12,request),"Auto accepted authored slopes");
    request[1]=request[2]=0;
    for(unsigned field:{0u,3u,4u,5u,6u}) {
        auto stale=request;stale[field]+=1;
        require(!dolly::valid_visualization_tangent_action(path,15,stale),"Stale tangent gesture accepted");
    }
    for(double invalid:{-1.,12.5,36.,std::numeric_limits<double>::infinity()})
        require(!dolly::valid_visualization_tangent_action(path,invalid,request),"Malformed tangent target accepted");
    auto dormant=bytes;change_u32(dormant,56,0);
    require(path.load(dormant.data(),dormant.size(),error) && !path.smooth_position() &&
            path.graph_keys()[1].tangents[2].incoming==5 &&
            !dolly::valid_visualization_tangent_action(path,15,request),"Dormant tangents were lost or editable outside Smooth");
    const std::size_t tangent=64+3*8+136+64;
    for(unsigned offset:{0u,4u}) {
        auto malformed=bytes;change_u32(malformed,tangent+offset,9);
        require(!path.load(malformed.data(),malformed.size(),error),"Malformed XYZ tangent metadata accepted");
    }
    auto nonfinite=bytes;change_number(nonfinite,tangent+8,std::numeric_limits<double>::infinity());
    require(!path.load(nonfinite.data(),nonfinite.size(),error),"Nonfinite XYZ tangent metadata accepted");
}
void test_preview_envelope() {
    const auto base_bytes=tangent_packet();dolly::VisualizationPath base;std::string error;
    require(base.load(base_bytes.data(),base_bytes.size(),error),"Preview base fixture rejected");
    auto inner=base_bytes;
    const std::size_t metadata=64+3*8,blob=metadata+3*136;
    change_number(inner,metadata+136+8,12); // The middle camera moves from Y2 to Y12.
    change_number(inner,blob+160+16+40+16,12);
    change_number(inner,blob+160+296+16+40+8,12);
    std::vector<unsigned char> bytes{'D','L','Y','P','R','V','0','1'};
    u32(bytes,2);u32(bytes,1);u32(bytes,1);u32(bytes,1);u32(bytes,77);u32(bytes,17);
    u32(bytes,0x89abcdef);u32(bytes,0xf1234567);u32(bytes,1);u32(bytes,1);number(bytes,1);
    u32(bytes,unsigned(inner.size()));u32(bytes,0);bytes.insert(bytes.end(),inner.begin(),inner.end());
    dolly::VisualizationPreview preview;
    require(preview.load(bytes.data(),bytes.size(),base,error) && preview.source==1 && preview.token==77 &&
            preview.path && preview.path->graph_keys()[1].pose[1]==12 && base.graph_keys()[1].pose[1]==2,
            "Render-only preview did not preserve an independent committed graph");
    auto maximum_token=bytes;change_u32(maximum_token,24,0xffffffff);
    require(preview.load(maximum_token.data(),maximum_token.size(),base,error) && preview.token==0xffffffff,
            "Maximum nonzero u32 preview token was rejected");
    auto zero_token=bytes;change_u32(zero_token,24,0);
    require(!preview.load(zero_token.data(),zero_token.size(),base,error),"Enabled preview accepted a zero token");
    for(unsigned offset:{12u,16u,20u,28u,32u,40u,44u,56u,60u}) {
        auto malformed=bytes;change_u32(malformed,offset,0xffffffff);
        if(preview.load(malformed.data(),malformed.size(),base,error))
            throw std::runtime_error("Malformed preview envelope accepted at byte "+std::to_string(offset));
    }
    auto wrong_time=bytes;change_number(wrong_time,48,1.125);
    require(!preview.load(wrong_time.data(),wrong_time.size(),base,error),"Retimed preview anchor accepted");
    auto clear=bytes;clear.resize(64);change_u32(clear,16,0);change_u32(clear,56,0);
    require(preview.load(clear.data(),clear.size(),base,error) && !preview.path && preview.token==77,
            "Identified terminal preview clear rejected");
    for(unsigned offset:{20u,24u,28u,32u}) {
        auto partial=clear;change_u32(partial,offset,0);
        if(offset==32)change_u32(partial,36,0);
        require(!preview.load(partial.data(),partial.size(),base,error),"Partial terminal preview identity accepted");
    }
    for(unsigned offset:{40u,44u}) {
        auto malformed=clear;change_u32(malformed,offset,0xffffffff);
        require(!preview.load(malformed.data(),malformed.size(),base,error),"Terminal preview clear accepted an out-of-range target");
    }
    auto negative_time=clear;change_number(negative_time,48,-1);
    require(!preview.load(negative_time.data(),negative_time.size(),base,error),"Terminal preview clear accepted negative time");
    for(std::size_t i=20;i<64;++i)clear[i]=0;
    require(preview.load(clear.data(),clear.size(),base,error) && !preview.path && !preview.token,
            "Empty preview clear rejected");
    auto partial_empty=clear;change_u32(partial_empty,28,17);
    require(!preview.load(partial_empty.data(),partial_empty.size(),base,error),"Empty preview clear accepted partial identity");
}
dolly::VisualizationView view() {
    dolly::VisualizationView v;
    v.pose = {0, 0, 0, 0, 0, 0, 2};
    v.horizontal_fov = 90;
    v.width = 1000;
    v.height = 500;
    return v;
}
void test_projection() {
    auto v = view();
    dolly::VisualizationPoint p{}, a{}, b{};
    require(dolly::project_visualization_point(v, {100, 0, 0}, p) && close(p.x, 500) &&
                close(p.y, 250),
            "Center projection failed");
    require(dolly::project_visualization_point(v, {100, -50, 25}, p) && close(p.x, 750) &&
                close(p.y, 125),
            "Source right/up axes failed");
    require(!dolly::project_visualization_point(v, {-100, 0, 0}, p),
            "Behind-camera point projected");
    require(!dolly::project_visualization_point(v, {100, 500, 0}, p), "Offscreen point projected");
    require(!dolly::project_visualization_line(v, {-100, 20, 0}, {-10, -30, 0}, a, b),
            "Behind-camera line projected");
    require(dolly::project_visualization_line(v, {-100, -300, 0}, {100, 0, 0}, a, b),
            "Crossing near/frustum segment lost");
    require(close(a.x, 1000) && close(b.x, 500) && close(a.y, 250),
            "Near/frustum clipping generated streak");
    require(dolly::project_visualization_line(v, {100, 1000, 0}, {100, -1000, 0}, a, b) &&
                close(a.x, 0) && close(b.x, 1000),
            "Viewport clipping failed");
    require(dolly::project_visualization_line(v, {100, 0, 1000}, {100, 0, -1000}, a, b) &&
                close(a.y, 0) && close(b.y, 500),
            "Vertical clipping failed");
    require(dolly::project_visualization_line(v, {-1, 0, 0}, {100, 0, 0}, a, b) &&
                close(a.x, 500) && close(b.x, 500),
            "Near-plane center crossing failed");
    v.pose[4] = 90;
    require(dolly::project_visualization_point(v, {0, 100, 0}, p) && close(p.x, 500),
            "Yaw projection failed");
    v = view();
    v.pose[3] = 45;
    require(dolly::project_visualization_point(v, {100, 0, -100}, p) && close(p.y, 250),
            "Pitch sign incorrect");
    v = view();
    v.pose[5] = 90;
    require(dolly::project_visualization_point(v, {100, 0, 25}, p) && close(p.x, 375) &&
                close(p.y, 250),
            "Roll projection failed");
    v = view();
    v.pose[6] = 1;
    require(dolly::project_visualization_point(v, {100, -50, 25}, p) && close(p.x, 750) &&
                close(p.y, 125),
            "Framing override stretched guide projection instead of uniform zoom");
    v.horizontal_fov = std::numeric_limits<double>::quiet_NaN();
    require(!dolly::project_visualization_point(v, {100, 0, 0}, p), "Invalid lens accepted");
}
void test_world_camera_markers() {
    dolly::VisualizationGeometry baseline;
    for (double aspect : {.5, 16.0 / 9, 4.0}) {
        auto bytes = packet(1);
        change_number(bytes, 72 + 48 + 6 * 8, aspect);
        change_number(bytes, 72 + 104 + 6 * 8, aspect);
        dolly::VisualizationPath path;
        std::string error;
        require(path.load(bytes.data(), bytes.size(), error), "Marker fixture rejected");
        auto v = view();
        v.pose[6] = aspect;
        dolly::VisualizationGeometry geometry;
        require(dolly::project_visualization(path, v, geometry) && geometry.line_count == 8,
                "World marker missing");
        if (!baseline.line_count)
            baseline = geometry;
        for (std::size_t i = 0; i < 8; ++i)
            require(close(baseline.lines[i].b.x, geometry.lines[i].b.x) &&
                        close(baseline.lines[i].b.y, geometry.lines[i].b.y),
                    "Framing value changed marker geometry independently of actual FOV");
        // The first corner remains the same real world point when looking up
        // or down. A screen-sized/billboard approximation fails this check.
        for (double pitch : {-15.0, 0.0, 15.0}) {
            v.pose[3] = pitch;
            require(dolly::project_visualization(path, v, geometry), "Pitched marker lost");
            dolly::VisualizationPoint corner;
            require(dolly::project_visualization_point(v, {124, 12, -6.75}, corner) &&
                        close(geometry.lines[0].b.x, corner.x) &&
                        close(geometry.lines[0].b.y, corner.y),
                    "Marker corner drifted away from its world position");
        }
        v = view();
        v.horizontal_fov = 60;
        require(dolly::project_visualization(path, v, geometry), "Zoomed marker lost");
        const double scale = 1 / std::tan(3.14159265358979323846 / 6);
        require(close((geometry.lines[0].b.x - 500) / (baseline.lines[0].b.x - 500), scale) &&
                    close((geometry.lines[0].b.y - 250) / (baseline.lines[0].b.y - 250), scale),
                "Zoom must scale world marker width and height equally");
    }
}
void self_test() {
    test_projection();
    test_world_camera_markers();
    test_curve_contract();
    test_tangent_contract();
    test_preview_envelope();
    for (bool step : {false, true}) {
        auto lens_bytes = packet(3, 2048, 64, step, true);
        dolly::VisualizationPath lens_path;
        std::string lens_error;
        require(lens_path.load(lens_bytes.data(), lens_bytes.size(), lens_error),
                "Captured-lens viewer packet rejected");
        require(lens_path.points().front()[0] == 100 && lens_path.points().back()[0] == 120 &&
                    lens_path.cameras()[1].pose[0] == 110,
                "Captured-lens guide positions changed");
        require(bool(lens_path.breaks()[32]) == step && bool(lens_path.breaks()[64]) == step,
                "Captured-lens step continuity changed");
    }
    auto bytes = packet();
    std::string error;
    dolly::VisualizationPath path;
    require(path.load(bytes.data(), bytes.size(), error), "Valid viewer packet rejected");
    require(path.enabled() && path.points().size() == 65 && path.cameras().size() == 3,
            "Viewer sampling budget failed");
    require(path.points().front()[0] == 100 && path.points().back()[0] == 120,
            "Viewer endpoints changed");
    require(path.points()[32][0] == 110 && path.points()[16][0] == 105,
            "Viewer sampling does not match authored timing");
    require(path.selected_camera() == 1, "Selected camera changed");
    require(path.project_revision()==0 && path.point_times().size()==path.points().size() &&
                path.point_times()[16]==.5 && path.point_times()[32]==1 &&
                path.graph_keys().size()==3 && path.graph_keys()[1].time==1 &&
                path.graph_keys()[1].pose[0]==110,
            "Legacy guide packet lost exact timed graph data");
    auto revision_packet=bytes;
    change_u32(revision_packet,12,2);change_u32(revision_packet,40,17);
    require(path.load(revision_packet.data(),revision_packet.size(),error) && path.project_revision()==17,
            "Revision-bound graph packet rejected");
    change_u32(revision_packet,44,1);
    require(!path.load(revision_packet.data(),revision_packet.size(),error),
            "Graph packet accepted unknown reserved fields");
    dolly::VisualizationGeometry geometry;
    require(dolly::project_visualization(path, view(), geometry) && geometry.label_count == 3,
            "Camera markers did not project");
    require(geometry.line_count <= dolly::kVisualizationMaxLines, "Drawing exceeded its budget");
    bool selected = false;
    for (std::size_t i = 0; i < geometry.label_count; ++i)
        selected |= geometry.labels[i].selected && geometry.labels[i].camera_index == 1;
    require(selected, "Selected marker missing");
    auto stepped = packet(3, 2048, 64, true);
    require(path.load(stepped.data(), stepped.size(), error), "Stepped path rejected");
    require(path.breaks()[32] && path.breaks()[64],
            "Step position jumps drawn as continuous travel");
    auto large = packet(4096, 4096, 128);
    require(path.load(large.data(), large.size(), error) && path.points().size() == 4096 &&
                path.cameras().size() <= 128 && path.graph_keys().size()==4096 &&
                path.point_times().size()==4096 && path.graph_keys()[4095].time==4095,
            "Maximum path budgets failed");
    selected = false;
    for (const auto& camera : path.cameras())
        selected |= camera.index == 2048;
    require(selected, "Decimation removed selected camera");
    auto single = packet(1);
    require(path.load(single.data(), single.size(), error) && path.points().size() == 1,
            "Single-camera path rejected");
    require(dolly::project_visualization(path, view(), geometry) && geometry.label_count == 1,
            "Single-camera glyph missing");
    for (std::size_t offset :
         {std::size_t(8), std::size_t(12), std::size_t(16), std::size_t(20), std::size_t(24),
          std::size_t(28), std::size_t(32), std::size_t(36), std::size_t(40)}) {
        auto bad = bytes;
        change_u32(bad, offset, 0xFFFFFFFF);
        require(!path.load(bad.data(), bad.size(), error) && path.points().size() == 1,
                "Invalid header accepted or previous path destroyed");
    }
    auto bad = bytes;
    change_number(bad, 64 + 8, 1.5);
    require(!path.load(bad.data(), bad.size(), error), "Unrelated camera timestamp accepted");
    bad = bytes;
    bad.push_back(0);
    require(!path.load(bad.data(), bad.size(), error), "Trailing data accepted");
    for (std::size_t size = 0; size < bytes.size(); ++size)
        require(!path.load(bytes.data(), size, error), "Truncated packet accepted");
    bad = bytes;
    bad.resize(64);
    change_u32(bad, 16, 0);
    change_u32(bad, 20, 0);
    change_u32(bad, 24, 0);
    change_u32(bad, 28, 0);
    require(path.load(bad.data(), bad.size(), error) && !path.enabled(),
            "Clear packet did not disable guides");
    geometry.line_count = 123;
    require(!dolly::project_visualization(path, view(), geometry) && geometry.line_count == 0,
            "Disabled guides retained stale lines");
}
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2) {
            std::ifstream input(argv[1], std::ios::binary);
            std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)), {});
            dolly::VisualizationPath path;
            std::string error;
            if (!path.load(bytes.data(), bytes.size(), error))
                throw std::runtime_error(error);
            std::cout.precision(17);
            std::cout << path.points().size() << ' ' << path.cameras().size() << ' '
                      << path.selected_camera() << '\n';
            for (const auto& pose : path.points()) {
                for (double value : pose)
                    std::cout << value << ' ';
                std::cout << '\n';
            }
            return 0;
        }
        self_test();
        std::cout << "Native visualization tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
