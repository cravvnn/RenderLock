// Windows CI exercises the real DX11 overlay on WARP with synthetic editor
// state. No Deadlock modules, private addresses, injection, or console needed.
#include "dolly_overlay.hpp"
#include "dolly_editor.hpp"
#include "dolly_lite.hpp"
#include "dolly_replay_events.hpp"
#include "../src/dolly_ui_tokens_generated.hpp"
#include "dolly_bone_picker.hpp"
#include "dolly_object_picker.hpp"
#include "dolly_renderer_diagnostics.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_reshade.hpp"
#include "dolly_media.hpp"
#include "dolly_video.hpp"
#include "MinHook.h"
#include "imgui.h"
#include "imgui_internal.h"
#include "../src/dolly_overlay_panel.hpp"
#include "../src/dolly_ui_storage.hpp"
#include <d3d11.h>
#include <dxgi.h>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <vector>
#include <string>
#include <fstream>
#include <algorithm>
#include <cmath>
#include <utility>
#include <iterator>

namespace dolly { extern video::Status recording_status_stub; }

static ImGuiContext* screenshot_context = nullptr;
namespace {
bool available = false;
bool shader_loading_stub = false;
bool replay_info_known_stub = true;
std::uint32_t camera_revision_stub = 7;
double camera_time_bias_stub = 0;
double observed_framing_factor = 0;
bool curve_camera_rows_stub = false;
unsigned camera_first_stub = 0;
HWND attached = nullptr;
dolly::EditorSnapshot snapshot;
bool observed_mouse_down[5]{};
bool present_keeps_os_cursor = false;
struct ObservedAction {
    dolly::EditorAction action;
    double value, revision, time;
    dolly::CameraPose pose{};
};
std::vector<ObservedAction> observed_actions;
std::vector<ObservedAction> observed_previews;
void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}
std::wstring layout_test_directory() {
    wchar_t temporary[MAX_PATH]{};
    require(GetTempPathW(MAX_PATH,temporary)>0,"Layout test temporary directory unavailable");
    const auto directory=std::wstring(temporary)+L"DollyUi-\u00dc-\u6d4b\u8bd5-"+
        std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64());
    require(CreateDirectoryW(directory.c_str(),nullptr)!=FALSE,"Cannot create isolated Unicode layout directory");
    return directory;
}
void write_layout_fixture(const std::wstring& path,const std::string& bytes) {
    HANDLE file=CreateFileW(path.c_str(),GENERIC_WRITE,0,nullptr,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
    require(file!=INVALID_HANDLE_VALUE,"Cannot open isolated layout fixture for writing");
    DWORD written=0;
    const bool okay=WriteFile(file,bytes.data(),DWORD(bytes.size()),&written,nullptr) &&
        written==bytes.size() && FlushFileBuffers(file);
    CloseHandle(file);require(okay,"Cannot write isolated layout fixture bytes");
}
std::string read_layout_fixture(const std::wstring& path) {
    HANDLE file=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
                            nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    require(file!=INVALID_HANDLE_VALUE,"Cannot open isolated layout fixture for reading");
    LARGE_INTEGER size{};
    if(!GetFileSizeEx(file,&size) || size.QuadPart<0 || size.QuadPart>2*1024*1024) {
        CloseHandle(file);require(false,"Isolated layout fixture has an unexpected size");
    }
    std::string bytes(std::size_t(size.QuadPart),'\0');DWORD count=0;
    const bool okay=ReadFile(file,bytes.data(),DWORD(bytes.size()),&count,nullptr) && count==bytes.size();
    CloseHandle(file);require(okay,"Cannot read isolated layout fixture bytes");return bytes;
}
void ui_storage_regressions() {
    const auto directory=layout_test_directory(),path=directory+L"\\layout.ini",backup=path+L".bak",orphan=path+L".tmp";
    const std::string a="[Window][Deadlock Dolly]\nPos=140,90\nSize=700,400\nCollapsed=0\n\n"
        "[DollyUI][Preferences]\nCustom=1\nEditorCustom=1\nReducedMotion=1\nSections0=1,0,2,3\n\n";
    const std::string b="[Window][Deadlock Dolly]\nPos=220,130\nSize=720,420\nCollapsed=0\n\n"
        "[DollyUI][Preferences]\nCustom=1\nEditorCustom=1\nReducedMotion=0\nSections0=0,1,2,3\n\n";
    std::string payload;bool recovered=false;
    require(dolly::save_ui_layout(path,a),"First managed layout save failed on a Unicode path");
    require(read_layout_fixture(path)==read_layout_fixture(backup),"First layout save did not create a valid redundant copy");
    require(dolly::load_ui_layout(path,payload,&recovered) && payload==a && !recovered,
            "A saved primary layout did not round-trip exactly");
    require(dolly::save_ui_layout(path,b),"Second managed layout save failed");
    require(dolly::load_ui_layout(path,payload,&recovered) && payload==b && !recovered,
            "A valid primary did not win over its older backup");
    const auto primary_bytes=read_layout_fixture(path);
    HANDLE locked=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    require(locked!=INVALID_HANDLE_VALUE,"Cannot lock the isolated primary against atomic replacement");
    const bool replaced=dolly::save_ui_layout(path,a);
    const auto locked_bytes=read_layout_fixture(path);CloseHandle(locked);
    require(!replaced && locked_bytes==primary_bytes,
            "A failed atomic replacement truncated or changed the previous primary");
    require(dolly::load_ui_layout(path,payload,&recovered) && payload==b && !recovered,
            "The prior primary became unusable after a failed save");
    std::string backup_payload;
    require(dolly::load_ui_layout(backup,backup_payload,&recovered) && !recovered &&
                (backup_payload==a || backup_payload==b),
            "A failed save left no independently valid backup generation");
    std::string corrupt=primary_bytes;const auto position_digit=corrupt.find("Pos=220");
    require(position_digit!=std::string::npos,"Checksum fixture has no printable payload position");
    corrupt[position_digit+4]='3';write_layout_fixture(path,corrupt);
    require(dolly::load_ui_layout(path,payload,&recovered) && payload==backup_payload && recovered,
            "Checksum-corrupt primary did not recover the last validated backup");
    write_layout_fixture(path,"");
    require(dolly::load_ui_layout(path,payload,&recovered) && payload==backup_payload && recovered,
            "An empty primary did not recover the validated backup");
    require(DeleteFileW(path.c_str()),"Cannot simulate a missing primary layout");
    require(dolly::load_ui_layout(path,payload,&recovered) && payload==backup_payload && recovered,
            "A missing primary did not recover the validated backup");
    write_layout_fixture(path,a);
    require(dolly::load_ui_layout(path,payload,&recovered) && payload==a && !recovered,
            "A valid legacy layout was discarded or lost to a stale backup");
    const auto legacy_bytes=read_layout_fixture(path);
    require(!dolly::save_ui_layout(path,a+";"+std::string(dolly::kUiLayoutMaxFileBytes,'x')+"\n") &&
                read_layout_fixture(path)==legacy_bytes,
            "An oversized save replaced a valid legacy layout");
    write_layout_fixture(path,"truncated");write_layout_fixture(backup,"truncated");
    require(!dolly::load_ui_layout(path,payload,&recovered),"Invalid primary and backup were accepted as a layout");
    require(DeleteFileW(path.c_str()) && DeleteFileW(backup.c_str()),"Cannot prepare orphan-temporary layout check");
    write_layout_fixture(orphan,a);
    require(!dolly::load_ui_layout(path,payload,&recovered),"An uncommitted temporary file was treated as saved layout data");
    require(DeleteFileW(orphan.c_str()) && RemoveDirectoryW(directory.c_str()),"Cannot clean isolated layout storage fixtures");
    std::puts("Managed layout storage: Unicode, atomic replacement failure, checksum/backup recovery, legacy data and orphan exclusion passed.");
}

void append_u32(std::vector<unsigned char>& out, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        out.push_back(static_cast<unsigned char>(value >> (8 * i)));
}
void append_double(std::vector<unsigned char>& out, double value) {
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned i = 0; i < 8; ++i)
        out.push_back(static_cast<unsigned char>(bits >> (8 * i)));
}
void append_u64(std::vector<unsigned char>& out, std::uint64_t value) {
    for (unsigned i = 0; i < 8; ++i)
        out.push_back(static_cast<unsigned char>(value >> (8 * i)));
}
// Real optional viewer wire, with authoring metadata for the entire shot. This
// deliberately exceeds the camera-list page in the graph selection regression.
std::vector<unsigned char> curve_packet(unsigned count = 10, unsigned revision = 7,
                                        std::uint64_t session = 0x12345678abcdef01ULL,
                                        double change = 0, bool step_x = false, unsigned selected = 0,
                                        double yaw_offset = 0, bool unwrapped = false, bool position_only_change = false,
                                        double key_step = 3, double key_start = 0,
                                        const std::vector<dolly::CameraPose>* explicit_keys = nullptr,
                                        const std::vector<dolly::CameraPose>* authored_keys = nullptr,
                                        bool tangents = false, unsigned tangent_mode = 0, double tangent_value = 5) {
    require(count >= 1 && key_step>0 && key_start>=0, "Curve fixture needs ordered camera times");
    // Screenshot-only input can come from the real Python compiler so the
    // displayed Hermite curve and its published handle slopes share one source.
    // Ordinary regression fixtures never read external packets.
    if(std::getenv("DOLLY_UI_TEST_LAYOUT")) if(const auto* file=std::getenv("DOLLY_UI_TEST_CAPTURE_PACKET")) {
        std::ifstream input(file,std::ios::binary|std::ios::ate);
        require(input.good(),"Cannot open compiled screenshot viewer packet");
        const auto size=static_cast<std::streamoff>(input.tellg());
        require(size>=std::streamoff(dolly::kVisualizationHeaderBytes) &&
                size<=std::streamoff(dolly::kVisualizationMappingBytes),"Compiled screenshot packet exceeds viewer bounds");
        std::vector<unsigned char> packet(static_cast<std::size_t>(size));input.seekg(0);
        input.read(reinterpret_cast<char*>(packet.data()),size);
        require(input.good(),"Cannot read complete compiled screenshot viewer packet");
        dolly::VisualizationPath path;std::string error;
        require(path.load(packet.data(),packet.size(),error),"Compiled screenshot viewer packet failed normal parser");
        require(path.enabled() && path.camera_count()==count && path.selected_camera()==selected &&
                path.project_revision()==revision && path.session_id()==session &&
                path.graph_keys().front().time==key_start &&
                path.graph_keys().back().time==key_start+(count-1)*key_step,
                "Compiled screenshot packet differs from the requested scene identity/times");
        return packet;
    }
    std::vector<dolly::CameraPose> keys;
    const double other_change=position_only_change ? 0 : change;
    for (unsigned i = 0; i < count; ++i)
        keys.push_back({100.12345678901234 + 20 * std::sin(i * .6) + change,
                        -50 + 12 * std::cos(i * .4) - other_change * .6,
                        30 + 10 * std::sin(i * .7) + other_change * .4,
                        5 + 3 * std::sin(i * .5) + other_change * .1,
                        10 + 18 * std::sin(i * .4) + i * 4.0 + other_change * .2 + yaw_offset,
                        5 * std::sin(i * .3) - other_change * .1,
                        16.0 / 9 + .4 * std::sin(i * .6) + other_change * .01});
    if(explicit_keys) {
        require(explicit_keys->size()==count,"Explicit curve fixture key count differs");
        keys=*explicit_keys;
    }
    require(!authored_keys || authored_keys->size()==count,"Authored curve fixture key count differs");
    const double end = key_start+(count-1)*key_step;
    std::vector<unsigned char> path{'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    append_u32(path, 1);
    append_u32(path, count - 1);
    append_u32(path, 7);
    append_u32(path, 0);
    append_double(path, end);
    append_double(path, key_start);
    append_double(path, end);
    for (double value : keys.front()) append_double(path, value);
    for (double value : keys.back()) append_double(path, value);
    for (unsigned key = 1; key < count; ++key) {
        append_double(path, key_start+(key-1)*key_step);
        append_double(path, key_start+key*key_step);
        for (unsigned channel = 0; channel < 7; ++channel) {
            append_u32(path, step_x && channel==0 ? 0 : 1);
            append_u32(path, channel >= 3 ? 1 : 0);
            append_double(path, keys[key - 1][channel]);
            append_double(path, keys[key][channel]);
            append_double(path, 0);
            append_double(path, 0);
        }
    }
    std::vector<unsigned char> packet{'D', 'L', 'Y', 'V', 'I', 'S', '0', '1'};
    append_u32(packet, 2); // Complete, even mapping sequence.
    append_u32(packet, tangents ? 4 : 3);
    append_u32(packet, 1);
    append_u32(packet, selected);
    append_u32(packet, count);
    append_u32(packet, static_cast<std::uint32_t>(path.size()));
    append_u32(packet, 128);
    append_u32(packet, 8);
    append_u32(packet, revision);
    append_u64(packet, session);
    append_u32(packet, count * (tangents ? 136 : 64));
    append_u32(packet, (unwrapped ? 1 : 0) | (tangents ? 2 : 0));
    append_u32(packet, 0);
    require(packet.size() == dolly::kVisualizationHeaderBytes, "Curve fixture header size drifted");
    for (unsigned key = 0; key < count; ++key) append_double(packet, key_start+key*key_step);
    for (unsigned key = 0; key < count; ++key) {
        for (double value : authored_keys ? (*authored_keys)[key] : keys[key]) append_double(packet, value);
        append_u32(packet, key == 1 ? 1 : 0); // One attached key exercises read-only UI.
        append_u32(packet, 0);
        if(tangents) for(unsigned axis=0;axis<3;++axis) {
            append_u32(packet,tangent_mode);append_u32(packet,0);
            append_double(packet,tangent_mode==1 ? 0 : tangent_value);append_double(packet,tangent_mode==1 ? 0 : tangent_value);
        }
    }
    packet.insert(packet.end(), path.begin(), path.end());
    return packet;
}
std::vector<unsigned char> viewer_packet(double first_x = 100, std::uint32_t revision = 0) {
    // These two synthetic cameras project to the right of the panel. Their
    // connecting segment and glyphs must remain visible in paused flight.
    const dolly::CameraPose a{first_x, -60, 0, 0, 0, 0, 4.0 / 3.0};
    const dolly::CameraPose b{100, -90, 15, 0, 0, 0, 4.0 / 3.0};
    std::vector<unsigned char> path{'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    append_u32(path, 1);
    append_u32(path, 1);
    append_u32(path, 7);
    append_u32(path, 0);
    append_double(path, 3);
    append_double(path, 0);
    append_double(path, 3);
    for (double value : a)
        append_double(path, value);
    for (double value : b)
        append_double(path, value);
    append_double(path, 0);
    append_double(path, 3);
    for (unsigned i = 0; i < 7; ++i) {
        append_u32(path, 1);
        append_u32(path, i >= 3 ? 1 : 0);
        append_double(path, a[i]);
        append_double(path, b[i]);
        append_double(path, 0);
        append_double(path, 0);
    }
    std::vector<unsigned char> packet{'D', 'L', 'Y', 'V', 'I', 'S', '0', '1'};
    append_u32(packet, 2);
    append_u32(packet, 1);
    append_u32(packet, 1);
    append_u32(packet, 1);
    append_u32(packet, 2);
    append_u32(packet, static_cast<std::uint32_t>(path.size()));
    append_u32(packet, 128);
    append_u32(packet, 8);
    if (revision) {
        // ABI 2 carries the project revision the guides were compiled from.
        packet[12] = 2;
        append_u32(packet, revision);
    }
    packet.resize(dolly::kVisualizationHeaderBytes);
    append_double(packet, 0);
    append_double(packet, 3);
    packet.insert(packet.end(), path.begin(), path.end());
    return packet;
}
class SyntheticViewer {
    HANDLE mapping = nullptr;
    unsigned char* memory = nullptr;
    HANDLE preview_mapping=nullptr;
    unsigned char* preview_memory=nullptr;
    std::uint32_t preview_sequence=0;
    std::wstring name, viewer_name;
    std::uint32_t sequence = 0;

public:
    SyntheticViewer() {
        name = L"Local\\Dolly.Overlay.Smoke." + std::to_wstring(GetCurrentProcessId()) + L"." +
               std::to_wstring(GetTickCount64());
        viewer_name = name + L".viewer";
        dolly::visualization_worker_tick(name.c_str(), true);
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Waiting &&
                    !dolly::visualization_snapshot(),
                "Missing optional viewer mapping must not publish guides");
        dolly::visualization_worker_tick(name.c_str(), false);
        mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                     static_cast<DWORD>(dolly::kVisualizationMappingBytes),
                                     viewer_name.c_str());
        require(mapping != nullptr, "Could not create synthetic viewer mapping");
        memory = static_cast<unsigned char*>(
            MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, dolly::kVisualizationMappingBytes));
        if (!memory) {
            CloseHandle(mapping);
            mapping = nullptr;
            throw std::runtime_error("Could not map synthetic viewer packet");
        }
        publish(viewer_packet());
        dolly::visualization_worker_tick(name.c_str(), true);
        const auto path = dolly::visualization_snapshot();
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Ready &&
                    path && path->enabled() && path->camera_count() == 2,
                "Valid optional viewer mapping did not publish cameras");
    }
    ~SyntheticViewer() { close(); }
    void close() noexcept {
        dolly::visualization_worker_tick(name.c_str(), false);
        if(preview_memory) {UnmapViewOfFile(preview_memory);preview_memory=nullptr;}
        if(preview_mapping) {CloseHandle(preview_mapping);preview_mapping=nullptr;}
        if (memory) {
            UnmapViewOfFile(memory);
            memory = nullptr;
        }
        if (mapping) {
            CloseHandle(mapping);
            mapping = nullptr;
        }
    }
    void publish(const std::vector<unsigned char>& packet) {
        require(packet.size() >= 12 && packet.size() <= dolly::kVisualizationMappingBytes,
                "Invalid synthetic viewer packet size");
        sequence += 2;
        InterlockedExchange(reinterpret_cast<volatile LONG*>(memory + 8),
                            static_cast<LONG>(sequence - 1));
        std::memcpy(memory, packet.data(), 8);
        std::memcpy(memory + 12, packet.data() + 12, packet.size() - 12);
        MemoryBarrier();
        InterlockedExchange(reinterpret_cast<volatile LONG*>(memory + 8),
                            static_cast<LONG>(sequence));
    }
    void publish_preview(const std::vector<unsigned char>& packet) {
        require(packet.size()>=64 && packet.size()<=dolly::kVisualizationMappingBytes,"Invalid synthetic preview length");
        if(!preview_memory) {
            const auto requested=viewer_name+L".preview";
            preview_mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,
                DWORD(dolly::kVisualizationMappingBytes),requested.c_str());
            require(preview_mapping!=nullptr,"Cannot create synthetic preview mapping");
            preview_memory=static_cast<unsigned char*>(MapViewOfFile(preview_mapping,FILE_MAP_ALL_ACCESS,0,0,dolly::kVisualizationMappingBytes));
            require(preview_memory!=nullptr,"Cannot map synthetic preview packet");
        }
        preview_sequence+=2;
        InterlockedExchange(reinterpret_cast<volatile LONG*>(preview_memory+8),LONG(preview_sequence-1));
        std::memcpy(preview_memory,packet.data(),8);std::memcpy(preview_memory+12,packet.data()+12,packet.size()-12);
        MemoryBarrier();InterlockedExchange(reinterpret_cast<volatile LONG*>(preview_memory+8),LONG(preview_sequence));
    }
    void tick() {
        Sleep(110);
        dolly::visualization_worker_tick(name.c_str(), true);
    }
    void check_lifecycle() {
        const auto before = dolly::visualization_snapshot();
        InterlockedExchange(reinterpret_cast<volatile LONG*>(memory + 8),
                            static_cast<LONG>(sequence + 1));
        tick();
        require(dolly::visualization_runtime_state() ==
                        dolly::VisualizationRuntimeState::Updating &&
                    dolly::visualization_snapshot() == before,
                "In-progress viewer write must retain the last complete snapshot");
        auto invalid = viewer_packet();
        invalid[0] = 'X';
        publish(invalid);
        tick();
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Invalid &&
                    !dolly::visualization_snapshot(),
                "Invalid viewer packet left stale guides active");
        publish(viewer_packet());
        tick();
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Ready &&
                    dolly::visualization_snapshot(),
                "Viewer did not recover after a valid replacement packet");
        close();
        require(dolly::visualization_runtime_state() ==
                        dolly::VisualizationRuntimeState::Disconnected &&
                    !dolly::visualization_snapshot(),
                "Disconnected editor left guides active");
        HANDLE remaining = OpenFileMappingW(FILE_MAP_READ, FALSE, viewer_name.c_str());
        if (remaining)
            CloseHandle(remaining);
        require(remaining == nullptr, "Viewer retained its mapping after disconnect");
    }
};

std::vector<unsigned char> preview_packet(const std::vector<unsigned char>& inner,unsigned source,unsigned token,
                                         unsigned index,unsigned channel,double time,unsigned revision=7,
                                         std::uint64_t session=0x12345678abcdef01ULL) {
    std::vector<unsigned char> packet{'D','L','Y','P','R','V','0','1'};
    append_u32(packet,2);append_u32(packet,1);append_u32(packet,inner.empty() ? 0 : 1);
    append_u32(packet,source);append_u32(packet,token);append_u32(packet,revision);append_u64(packet,session);
    append_u32(packet,index);append_u32(packet,channel);append_double(packet,time);
    append_u32(packet,unsigned(inner.size()));append_u32(packet,0);
    require(packet.size()==64,"Synthetic preview envelope size drifted");
    packet.insert(packet.end(),inner.begin(),inner.end());return packet;
}

static void preview_runtime_regressions(SyntheticViewer& viewer) {
    auto base_bytes=curve_packet(10,7,0x12345678abcdef01ULL,0,false,4,0,false,false,3,0,nullptr,nullptr,true,3);
    auto draft_bytes=curve_packet(10,7,0x12345678abcdef01ULL,10,false,4,0,false,true,3,0,nullptr,nullptr,true,3);
    viewer.publish(base_bytes);viewer.tick();const auto base=dolly::visualization_snapshot();
    auto packet=preview_packet(draft_bytes,1,6001,4,0,12);
    viewer.publish_preview(packet);viewer.tick();auto preview=dolly::visualization_preview_snapshot();
    require(preview && preview->path->points().front()[0]==base->points().front()[0]+10 &&
            dolly::visualization_snapshot()==base,"Render-only mapping replaced or failed to preview the committed path");
    dolly::VisualizationView view;view.pose={0,0,0,0,0,0,16./9};view.horizontal_fov=90;view.width=1920;view.height=1080;
    dolly::VisualizationGeometry before,after;
    require(dolly::project_visualization(*base,view,before) && dolly::project_visualization(*preview->path,view,after) &&
            before.line_count && after.line_count && before.lines[0].a.x!=after.lines[0].a.x,
            "Preview did not deform the scene projection at a fixed camera viewpoint");
    dolly::OverlayPanel policy;dolly::EditorSnapshot state{};
    state.enabled=state.ready=state.paused=state.manual_active=true;state.owner=dolly::EditorOwner::Flight;
    state.view_width=1920;state.view_height=1080;state.focused=false;
    require(policy.preview_guides_visible(state,*preview) && policy.guides_visible(state),
            "Desktop editing cannot retain preview and committed fallback without focus");
    state.playing=true;require(!policy.preview_guides_visible(state,*preview),"Playback renders a draft preview");state.playing=false;
    const auto recording=dolly::recording_status_stub.state;
    dolly::recording_status_stub.state=dolly::video::State::starting;
    require(!policy.preview_guides_visible(state,*preview),"Recording renders a draft preview");
    dolly::recording_status_stub.state=recording;
    // The lease is 750 ms and is compared against GetTickCount64, which advances
    // in ~15.6 ms system-tick steps. Sleeping only just past the lease made the
    // quantised difference land on exactly 750 every so often, failing the strict
    // ">" comparison. Sleep well past one tick so the check measures the lease
    // rather than the clock's resolution.
    Sleep(900);
    require(!dolly::visualization_preview_snapshot(),
            "Unrefreshed preview survived its lease");
    require(dolly::visualization_snapshot()==base,
            "An unrefreshed preview discarded the saved graph");
    viewer.publish_preview(packet);viewer.tick();require(bool(dolly::visualization_preview_snapshot()),"Fresh heartbeat did not renew preview");
    auto empty=preview_packet({},0,0,0,0,0,0,0);viewer.publish_preview(empty);viewer.tick();
    require(!dolly::visualization_preview_snapshot(),"Empty invalid-draft clear left preview visible");
    viewer.publish_preview(packet);viewer.tick();require(bool(dolly::visualization_preview_snapshot()),"Invalid draft could not recover within its gesture");
    viewer.publish_preview(preview_packet({},1,6001,4,0,12));viewer.tick();
    viewer.publish_preview(packet);viewer.tick();
    require(!dolly::visualization_preview_snapshot(),"A terminal desktop token revived from a late packet");
    const auto token=dolly::visualization_begin_native_preview(*base,4,0);
    packet=preview_packet(draft_bytes,2,token,4,0,12);viewer.publish_preview(packet);viewer.tick();
    require(bool(dolly::visualization_preview_snapshot()),"Current native gesture could not render its draft");
    dolly::visualization_end_native_preview(token);
    require(!dolly::visualization_preview_snapshot(),"Native cancellation waited for Python before hiding preview");
    viewer.publish_preview(packet);viewer.tick();require(!dolly::visualization_preview_snapshot(),"Late native packet revived a cancelled gesture");
    packet=preview_packet(draft_bytes,1,6002,4,0,12);viewer.publish_preview(packet);viewer.tick();
    auto invalid=packet;invalid[0]='X';viewer.publish_preview(invalid);viewer.tick();
    require(!dolly::visualization_preview_snapshot() && dolly::visualization_snapshot()==base &&
            dolly::visualization_runtime_state()==dolly::VisualizationRuntimeState::Ready,
            "Invalid optional preview damaged the authoritative viewer boundary");
    viewer.publish_preview(packet);viewer.tick();
    viewer.publish(curve_packet(10,8,0x12345678abcdef01ULL,0,false,4,0,false,false,3,0,nullptr,nullptr,true,3));viewer.tick();
    require(!dolly::visualization_preview_snapshot(),"Changed committed revision retained an old preview");
    viewer.publish(viewer_packet());viewer.tick();
}

struct DrawRegions {
    std::size_t panel = 0, guides = 0;
};
DrawRegions render_regions(IDXGISwapChain* chain, ID3D11Device* device,
                           ID3D11DeviceContext* context, ID3D11RenderTargetView* target,
                           ID3D11Texture2D* backbuffer) {
    // Compare broad regions rather than exact antialiased pixels or UI text
    // positions. Clear each frame so hidden guides cannot pass with old pixels.
    const FLOAT clear[4] = {.04f, .08f, .95f, 1};
    context->ClearRenderTargetView(target, clear);
    context->OMSetRenderTargets(1, &target, nullptr);
    require(SUCCEEDED(chain->Present(0, 0)), "Viewer visibility Present failed");
    D3D11_TEXTURE2D_DESC desc{};
    backbuffer->GetDesc(&desc);
    desc.Usage = D3D11_USAGE_STAGING;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.BindFlags = 0;
    desc.MiscFlags = 0;
    ID3D11Texture2D* staging = nullptr;
    require(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &staging)),
            "Viewer readback texture creation failed");
    context->CopyResource(staging, backbuffer);
    D3D11_MAPPED_SUBRESOURCE pixels{};
    if (FAILED(context->Map(staging, 0, D3D11_MAP_READ, 0, &pixels))) {
        staging->Release();
        throw std::runtime_error("Viewer backbuffer readback failed");
    }
    DrawRegions result;
    ImGui::SetCurrentContext(screenshot_context);
    const float sx = desc.Width / ImGui::GetIO().DisplaySize.x;
    const float sy = desc.Height / ImGui::GetIO().DisplaySize.y;
    for (UINT y = 40; y < desc.Height - 140; ++y) {
        const auto* row = static_cast<const unsigned char*>(pixels.pData) + y * pixels.RowPitch;
        for (UINT x = 30; x < desc.Width - 10; ++x) {
            const auto* pixel = row + x * 4;
            const bool changed = pixel[0] < 7 || pixel[0] > 13 || pixel[1] < 17 || pixel[1] > 23 ||
                                 pixel[2] < 239 || pixel[2] > 245;
            bool over_ui = false;
            for (const auto* item : screenshot_context->Windows)
                if (item->Active && x/sx >= item->Pos.x - 20 && x/sx < item->Pos.x + item->Size.x + 20 &&
                    y/sy >= item->Pos.y - 20 && y/sy < item->Pos.y + item->Size.y + 20)
                    over_ui = true;
            if (changed) {
                if (x < 450)
                    ++result.panel;
                else if (x > 540 && !over_ui) {
                    ++result.guides;
                }
            }
        }
    }
    context->Unmap(staging, 0);
    staging->Release();
    return result;
}
void check_guide_visibility(IDXGISwapChain* chain, ID3D11Device* device,
                            ID3D11DeviceContext* context, ID3D11RenderTargetView* target,
                            ID3D11Texture2D* backbuffer) {
    const auto original = snapshot;
    snapshot.owner = dolly::EditorOwner::Flight;
    const auto flight = snapshot;
    auto draw = [&]() { return render_regions(chain, device, context, target, backbuffer); };
    const auto counters_before = dolly::overlay_diagnostics();
    auto pixels = draw();
    const auto counters_flight = dolly::overlay_diagnostics();
    require(counters_flight.draw_frames == counters_before.draw_frames + 1 &&
                counters_flight.guide_frames == counters_before.guide_frames + 1 &&
                counters_flight.panel_frames == counters_before.panel_frames,
            "Guide-only drawing must have its own counter without counting a panel frame");
    require(counters_flight.guide_lines > 0 && counters_flight.guide_labels > 0,
            "Guide diagnostics omitted the projected geometry");
    require(!counters_flight.overlay_active_since_ms && !counters_flight.present_active_since_ms,
            "Completed Present left an active rendering timer");
    require(counters_flight.overlay_max_us >= counters_flight.overlay_last_us &&
                counters_flight.present_max_us >= counters_flight.present_last_us,
            "Overlay and original-Present timings are inconsistent");
    require(pixels.guides > 30 && pixels.panel == 0,
            "Paused flight must retain camera guides without leaving editor panels visible");
    // Exercise the supported narrow layout here so the centered editor leaves
    // room for the synthetic world-space guide fixture on the right.
    snapshot.owner = dolly::EditorOwner::Panel;
    draw();
    ImGui::SetCurrentContext(screenshot_context);
    if (auto* panel = ImGui::FindWindowByName("Deadlock Dolly")) {
        screenshot_context->NavNextActivateId=panel->GetID("Layout");draw();draw();
        auto* layout=screenshot_context->OpenPopupStack.back().Window;
        screenshot_context->NavNextActivateId=layout->GetID("Arrange panels");draw();draw();
        ImGui::GetIO().AddKeyEvent(ImGuiKey_Escape,true);draw();
        ImGui::GetIO().AddKeyEvent(ImGuiKey_Escape,false);draw();
        ImGui::SetWindowSize(panel,ImVec2(520,panel->Size.y));
        ImGui::SetWindowPos(panel,ImVec2(20,20));
        draw();draw();
    }
    pixels = draw();
    require(pixels.panel > 100 && pixels.guides > 30,
            "Opening the panel hid the paused camera guides");
    snapshot.playing = true;
    const auto guides_before_playback = dolly::overlay_diagnostics().guide_frames;
    pixels = draw();
    require(dolly::overlay_diagnostics().guide_frames == guides_before_playback,
            "Playback without guides incorrectly counted a guide frame");
    if (!(pixels.panel > 100 && pixels.guides == 0))
        std::fprintf(stderr,"Playback pixel regions panel=%llu guides=%llu\n",pixels.panel,pixels.guides);
    require(pixels.panel > 100 && pixels.guides == 0,
            "Playback must hide guides while keeping the open panel visible");
    struct HiddenCase {
        const char* reason;
        unsigned field;
    };
    const HiddenCase cases[] = {
        {"Shot playback", 0},     {"Running replay", 1},         {"Game UI", 2},
        {"Console", 3},           {"Unfocused owner", 4},        {"Lost game focus", 5},
        {"Unready replay", 6},    {"Native flight inactive", 7}, {"Busy editor", 8},
        {"Unknown viewport", 9},  {"Disabled editor", 10},       {"Camera facing away", 11},
        {"Unknown view lens", 12}};
    for (const auto& test : cases) {
        snapshot = flight;
        switch (test.field) {
        case 0:
            snapshot.playing = true;
            break;
        case 1:
            snapshot.paused = false;
            break;
        case 2:
            snapshot.owner = dolly::EditorOwner::GameUI;
            break;
        case 3:
            snapshot.owner = dolly::EditorOwner::Console;
            break;
        case 4:
            snapshot.owner = dolly::EditorOwner::Unfocused;
            break;
        case 5:
            snapshot.focused = false;
            break;
        case 6:
            snapshot.ready = false;
            break;
        case 7:
            snapshot.manual_active = false;
            break;
        case 8:
            snapshot.busy = true;
            break;
        case 9:
            snapshot.view_width = 0;
            break;
        case 10:
            snapshot.enabled = false;
            break;
        case 11:
            snapshot.pose[4] = 180;
            break;
        case 12:
            snapshot.horizontal_fov = 0;
            break;
        }
        pixels = draw();
        if(test.field==4 || test.field==5) {
            require(pixels.guides>30 && pixels.panel==0,"Desktop focus must retain only paused render-only guides");
            continue;
        }
        if (pixels.guides || pixels.panel) {
            char message[128]{};
            std::snprintf(message, sizeof(message), "%s left editing graphics visible",
                          test.reason);
            throw std::runtime_error(message);
        }
    }
    snapshot = original;
    std::puts(
        "DX11 WARP viewer: guides without closed panels and playback/UI/focus visibility gates passed.");
}

struct BufferLifetime {
    std::atomic<bool> retired{false};
};
// SetPrivateDataInterface holds one COM reference until the owning device
// child is destroyed. Observe that lifetime instead of relying on a driver's
// implementation-specific AddRef/Release return values.
// https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicechild-setprivatedatainterface
class BufferSentinel final : public IUnknown {
    std::atomic<ULONG> references{1};
    std::shared_ptr<BufferLifetime> lifetime;
    ~BufferSentinel() { lifetime->retired.store(true, std::memory_order_release); }

public:
    explicit BufferSentinel(std::shared_ptr<BufferLifetime> value) : lifetime(std::move(value)) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** result) override {
        if (!result)
            return E_POINTER;
        *result = nullptr;
        if (!IsEqualIID(iid, __uuidof(IUnknown)))
            return E_NOINTERFACE;
        *result = static_cast<IUnknown*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override {
        return references.fetch_add(1, std::memory_order_relaxed) + 1;
    }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto remaining = references.fetch_sub(1, std::memory_order_acq_rel) - 1;
        if (!remaining)
            delete this;
        return remaining;
    }
};

std::shared_ptr<BufferLifetime> watch_buffer(ID3D11Buffer* buffer) {
    static const GUID sentinel_id = {
        0x1622bc48, 0x49b1, 0x466a, {0x95, 0xb3, 0xa4, 0x3b, 0xe0, 0x34, 0x11, 0x8f}};
    auto lifetime = std::make_shared<BufferLifetime>();
    auto* sentinel = new BufferSentinel(lifetime);
    const auto result = buffer->SetPrivateDataInterface(sentinel_id, sentinel);
    sentinel->Release();
    require(SUCCEEDED(result), "Could not attach buffer lifetime sentinel");
    require(!lifetime->retired.load(std::memory_order_acquire),
            "Buffer did not retain its lifetime sentinel");
    return lifetime;
}

void retire_game_buffers(ID3D11DeviceContext* context, ID3D11Query* completed,
                         const std::vector<std::shared_ptr<BufferLifetime>>& lifetimes) {
    // D3D11 may defer destruction. Release game bindings, submit pending work,
    // and wait for it before checking lifetime flags; a live overlay must not
    // retain earlier game vertex/index buffers in its private context state.
    // https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-flush
    context->ClearState();
    context->End(completed);
    context->Flush();
    const auto deadline = GetTickCount64() + 3000;
    for (;;) {
        BOOL ready = FALSE;
        const auto result =
            context->GetData(completed, &ready, sizeof(ready), D3D11_ASYNC_GETDATA_DONOTFLUSH);
        require(SUCCEEDED(result), "GPU completion query failed during buffer-retirement check");
        if (result == S_OK && ready)
            break;
        require(GetTickCount64() < deadline,
                "GPU completion timed out during buffer-retirement check");
        Sleep(1);
    }
    context->Flush();
    for (std::size_t i = 0; i < lifetimes.size(); ++i) {
        if (!lifetimes[i]->retired.load(std::memory_order_acquire)) {
            char message[128]{};
            std::snprintf(message, sizeof(message),
                          "Overlay retained retired game %s buffer at frame %zu",
                          i % 2 ? "index" : "vertex", i / 2);
            throw std::runtime_error(message);
        }
    }
}

void stress_game_buffer_lifetimes(IDXGISwapChain* chain, ID3D11Device* device,
                                  ID3D11DeviceContext* context, ID3D11RenderTargetView* target) {
    constexpr int frames = 384;
    constexpr dolly::EditorOwner owners[] = {dolly::EditorOwner::Panel, dolly::EditorOwner::Flight,
                                             dolly::EditorOwner::GameUI};
    std::vector<std::shared_ptr<BufferLifetime>> lifetimes;
    lifetimes.reserve(frames * 2);
    D3D11_QUERY_DESC query_desc{D3D11_QUERY_EVENT, 0};
    ID3D11Query* completed = nullptr;
    require(SUCCEEDED(device->CreateQuery(&query_desc, &completed)),
            "Could not create buffer-retirement query");
    try {
        for (int frame = 0; frame < frames; ++frame) {
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            snapshot.owner = owners[(frame / 4) % 3];
            require(dolly::visualization_snapshot() != nullptr,
                    "Buffer lifetime stress lost its camera guides");
            D3D11_BUFFER_DESC buffer_desc{};
            buffer_desc.ByteWidth = 1024;
            buffer_desc.Usage = D3D11_USAGE_DEFAULT;
            buffer_desc.BindFlags = D3D11_BIND_VERTEX_BUFFER;
            ID3D11Buffer* vertex = nullptr;
            ID3D11Buffer* index = nullptr;
            require(SUCCEEDED(device->CreateBuffer(&buffer_desc, nullptr, &vertex)),
                    "Synthetic game vertex-buffer creation failed");
            buffer_desc.BindFlags = D3D11_BIND_INDEX_BUFFER;
            const auto index_result = device->CreateBuffer(&buffer_desc, nullptr, &index);
            if (FAILED(index_result)) {
                vertex->Release();
                throw std::runtime_error("Synthetic game index-buffer creation failed");
            }
            lifetimes.push_back(watch_buffer(vertex));
            lifetimes.push_back(watch_buffer(index));
            const UINT stride = 16u * (1u + static_cast<UINT>(frame % 2));
            const UINT vertex_offset = 16u * static_cast<UINT>(frame % 4);
            const UINT index_offset = 4u * static_cast<UINT>(frame % 8);
            const auto index_format = frame % 2 ? DXGI_FORMAT_R32_UINT : DXGI_FORMAT_R16_UINT;
            context->IASetVertexBuffers(0, 1, &vertex, &stride, &vertex_offset);
            context->IASetIndexBuffer(index, index_format, index_offset);
            context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            context->OMSetRenderTargets(1, &target, nullptr);
            const auto presented = chain->Present(0, 0);
            ID3D11Buffer* after_vertex = nullptr;
            ID3D11Buffer* after_index = nullptr;
            UINT after_stride = 0, after_vertex_offset = 0, after_index_offset = 0;
            DXGI_FORMAT after_index_format{};
            context->IAGetVertexBuffers(0, 1, &after_vertex, &after_stride, &after_vertex_offset);
            context->IAGetIndexBuffer(&after_index, &after_index_format, &after_index_offset);
            const bool restored = after_vertex == vertex && after_index == index &&
                                  after_stride == stride && after_vertex_offset == vertex_offset &&
                                  after_index_format == index_format &&
                                  after_index_offset == index_offset;
            if (after_vertex)
                after_vertex->Release();
            if (after_index)
                after_index->Release();
            vertex->Release();
            index->Release();
            require(SUCCEEDED(presented), "Present failed during game-buffer lifetime stress");
            require(restored, "Overlay did not restore the game's vertex/index-buffer bindings");
            if ((frame + 1) % 32 == 0)
                retire_game_buffers(context, completed, lifetimes);
        }
    } catch (...) {
        completed->Release();
        throw;
    }
    completed->Release();
    snapshot.owner = dolly::EditorOwner::Panel;
    std::puts(
        "DX11 WARP lifetime stress: 384 panel/guide/game-UI frames, 768 game buffers retired.");
}
}
static bool framebuffer_scale_probe = false;
namespace dolly {
bool framing_grid_enabled_stub = false;
video::Status recording_status_stub{};
video::Status editor_recording_status() noexcept {
    return recording_status_stub;
}
EditorCinematicStatus cinematic_status_stub{};
bool editor_cinematic_status(EditorCinematicStatus& out) noexcept {
    out = cinematic_status_stub;
    return out.token != 0;
}
EditorSnapshot editor_snapshot() noexcept {
    return snapshot;
}
bool editor_framing_grid_enabled() noexcept {
    return framing_grid_enabled_stub;
}
bool editor_request_framing_factor(double factor) noexcept {
    observed_framing_factor=factor;
    return std::isfinite(factor) && factor > 0;
}
bool replay_third_player_stub = false;
unsigned replay_events_status_stub = 2;
bool editor_replay_events(EditorReplayEvents& out) noexcept {
    out={}; std::memcpy(out.magic,"DLYKILL1",8);out.abi=1;out.status=replay_events_status_stub;out.total_ticks=146926;
    out.player_count=2;out.players[0].id=1;out.players[1].id=2;
    std::strcpy(out.players[0].name,"Ivy player");std::strcpy(out.players[1].name,"Haze player");
    if(replay_third_player_stub) {
        out.player_count=3;out.players[2].id=3;
        std::strcpy(out.players[2].name,"Unrelated player");
    }
    out.count=4;out.kills[0]={4000,1,2};out.kills[1]={5000,2,1};out.kills[2]={6000,1,2};out.kills[3]={8000,2,1};
    std::strcpy(out.message,"4 kills in this replay");return true;
}
bool editor_lite_replay_info(EditorLiteReplayInfo& out) noexcept {
    out = {};std::memcpy(out.magic,"DLYLITE1",8);out.abi=1;out.revision=camera_revision_stub;
    out.first=camera_first_stub;out.count=std::min(snapshot.camera_count-camera_first_stub,32u);out.flags=(replay_info_known_stub ? 3 : 1) | (shader_loading_stub ? 4 : 0);out.start_tick=3389;out.total_ticks=replay_info_known_stub ? 146926 : -1;out.tick_rate=64;
    for(unsigned i=0;i<out.count;i++)out.ticks[i]=3389+192*(out.first+i);
    return true;
}
bool editor_camera_list(EditorCameraList& out) noexcept {
    out = {};
    std::memcpy(out.magic, "DLYCAMS1", 8);
    out.abi = 1;
    out.revision = camera_revision_stub;
    out.total = snapshot.camera_count;
    out.first = camera_first_stub;
    out.count = std::min<std::uint32_t>(out.total-out.first, kEditorCameraListCount);
    out.flags = 3;
    for (std::uint32_t i = 0; i < out.count; ++i) {
        out.rows[i] = {double(out.first+i) * 3 + camera_time_bias_stub, 16.0 / 9, double(out.first+i) * 4, i % 2, 0};
        const auto graph=dolly::visualization_snapshot();
        if(curve_camera_rows_stub && graph && out.first+i<graph->graph_keys().size()) {
            const auto& key=graph->graph_keys()[out.first+i];
            out.rows[i]={key.time,key.authored_pose[6],key.authored_pose[5],key.source,0};
        }
    }
    return true;
}
EditorBinding editor_binding_snapshot(EditorAction action) noexcept {
    EditorBinding binding{};
    // A representative default so the key-cap hints render in the smoke test.
    if (action == EditorAction::Capture) {
        binding.vk = 'K';
        binding.modifiers = 3;
    } else if (action == EditorAction::PlayPath) {
        binding.vk = 0x74; // F5
    } else if (action == EditorAction::PlayPause) {
        binding.vk = 'P';
    } else if (action == EditorAction::Stop) {
        binding.vk = 0x08; // Backspace
    } else if (action == EditorAction::Flight) {
        binding.vk = 0x79; // F10
    } else if (action == EditorAction::GameUI) {
        binding.vk = 0x78; // F9
    }
    return binding;
}
bool editor_roster_snapshot(EditorRoster& out) noexcept {
    out = EditorRoster{};
    if (!snapshot.attach_available)
        return false;
    out.count = 1;
    std::snprintf(out.players[0].model_path, sizeof(out.players[0].model_path),
                  "models/heroes_staging/astro/astro.vmdl");
    out.players[0].handle = 19464268;
    out.players[0].entity_index = 76;
    return true;
}
bool editor_enqueue(EditorAction action, double value, const CameraPose* pose) noexcept {
    try {
        const bool preview = action >= EditorAction::PreviewCameraCurveValue &&
                             action <= EditorAction::ClearCameraCurvePreview;
        auto& events = preview ? observed_previews : observed_actions;
        events.push_back({action, value, pose ? (*pose)[0] : 0,
                                    pose ? (*pose)[1] : 0, pose ? *pose : CameraPose{}});
    } catch (...) {
        return false;
    }
    return true;
}
bool editor_bones_snapshot(EditorBones& out) noexcept {
    out = EditorBones{};
    out.count = out.total = 3;
    std::snprintf(out.names[0], 64, "head");
    std::snprintf(out.names[1], 64, "hand_R");
    std::snprintf(out.names[2], 64, "weapon_bone_R");
    return snapshot.attach_available;
}
bool editor_panel_visible() noexcept {
    return snapshot.owner == EditorOwner::Panel;
}
void editor_set_owner(EditorOwner value) noexcept {
    snapshot.owner = value;
}
void editor_overlay_available(bool value) noexcept {
    available = value;
}
void editor_text_input_active(bool) noexcept {
    if (ImGui::GetCurrentContext()) {
        screenshot_context = ImGui::GetCurrentContext();
        const auto& io = ImGui::GetIO();
        for (int i = 0; i < 5; ++i)
            observed_mouse_down[i] = io.MouseDown[i];
        present_keeps_os_cursor = (io.ConfigFlags & ImGuiConfigFlags_NoMouseCursorChange) != 0;
        if (framebuffer_scale_probe && ImGui::GetCurrentContext()->WithinFrameScope) {
            // Use client-coordinate geometry and a tight clip near the bottom
            // right. A smaller swapchain must scale BOTH viewport and scissors.
            auto* draw = ImGui::GetForegroundDrawList();
            const ImVec2 low(io.DisplaySize.x * .88f, io.DisplaySize.y * .88f);
            const ImVec2 high(io.DisplaySize.x * .98f, io.DisplaySize.y * .98f);
            draw->PushClipRect(low, ImVec2(io.DisplaySize.x * .93f, high.y));
            draw->AddRectFilled(low, high, IM_COL32(255, 0, 255, 255));
            draw->PopClipRect();
        }
    }
}
void editor_attach_window(HWND value) noexcept {
    attached = value;
}
bool editor_window_message(HWND, UINT, WPARAM, LPARAM, LRESULT&) noexcept {
    return false;
}
}
// Exercise presentation state directly as well as through DX11. This keeps
// delayed acknowledgement checks deterministic and uses real ImGui controls.
static void panel_option_regressions() {
    auto* previous_context = ImGui::GetCurrentContext();
    const auto previous_snapshot = snapshot;
    auto* test_context = ImGui::CreateContext();
    ImGui::SetCurrentContext(test_context);
    auto& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(1920, 1080);
    io.DeltaTime = 1.f / 60;
    io.IniFilename = nullptr;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    dolly::OverlayPanel panel;
    panel.style_panel();
    panel.load_panel_fonts(1);
    io.Fonts->Build();
    snapshot.dof_available = true;
    snapshot.dof = {1, 1, -100, 0, 180, 2000, -100, 0, 180, 2000, .5};
    snapshot.video_fixed_step = false;
    snapshot.video_depth = false;
    auto frame = [&] {
        ImGui::NewFrame();
        panel.draw_panel(snapshot);
        panel.draw_lite_campaths(snapshot);
        panel.draw_lite_timeline(snapshot);
        ImGui::Render();
        panel.flush_layout();
    };
    auto card = [&](const char* name) -> ImGuiWindow* {
        for (auto* window : test_context->Windows)
            if (std::strstr(window->Name, name)) return window;
        for (auto* window : test_context->Windows)
            std::fprintf(stderr, "Test window: %s (active %d, skip %d, size %.0f %.0f)\n", window->Name, window->Active, window->SkipItems, window->Size.x, window->Size.y);
        throw std::runtime_error(std::string("Presentation test card missing: ") + name);
    };
    frame(); frame(); frame();
    auto* main = ImGui::FindWindowByName("Deadlock Dolly");
    test_context->NavNextActivateId=main->GetID("Layout");frame();frame();
    auto* layout=test_context->OpenPopupStack.back().Window;
    test_context->NavNextActivateId=layout->GetID("Arrange panels");frame();frame();
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    ImGui::SetWindowSize(main, ImVec2(900, 820));
    ImGui::SetWindowPos(main, ImVec2(420, 20));
    ImGui::FocusWindow(main);
    frame(); frame();
    auto* shared_transport = ImGui::FindWindowByName("##lite-timeline");
    auto* reworked_campaths = ImGui::FindWindowByName("##lite-campaths");
    require(reworked_campaths && reworked_campaths->Size.x >= 250,
            "Campaths needs a readable card width, including the empty state");
    require(shared_transport && main->Pos.y + main->Size.y <= shared_transport->Pos.y,
            "Resizing the main editor covers the shared timeline");
    // Custom kill hit targets must obey disabled replay state too. Ordinary
    // ImGui widgets already honor BeginDisabled; hand-drawn markers need it.
    observed_actions.clear();
    const float kill_x = panel.timeline_x0() + (panel.timeline_x1() - panel.timeline_x0()) * 4000.f / 146925.f;
    io.AddMousePosEvent(kill_x, panel.timeline_y() - 10);
    snapshot.busy = true;
    frame(); frame();
    io.AddMouseButtonEvent(0, true);
    frame(); frame();
    require(observed_actions.empty(), "Busy replay accepts a custom kill-marker seek");
    io.AddMouseButtonEvent(0, false);
    snapshot.busy = false;
    frame(); frame();
    io.AddMouseButtonEvent(0, true);
    frame(); frame();
    require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::SeekReplayTick &&
                observed_actions[0].value == 4000,
            "Ready replay kill marker no longer seeks its actual tick");
    io.AddMouseButtonEvent(0, false);
    frame(); frame();
    {
        // The reset confirmation stays cancellable when the replay turns busy
        // while it is open; only its Reset button follows the busy state.
        auto* cameras = card("##cameras-card");
        test_context->NavNextActivateId = cameras->GetID("Reset camera path");
        frame(); frame();
        require(test_context->OpenPopupStack.Size == 1 &&
                    test_context->OpenPopupStack.back().Window &&
                    std::strstr(test_context->OpenPopupStack.back().Window->Name,
                                "Reset camera path?"),
                "Reset camera path did not open its confirmation");
        auto* confirm = test_context->OpenPopupStack.back().Window;
        // Locate both confirmation buttons by pointer while enabled. Keyboard
        // activation bypasses BeginDisabled, so the busy checks click instead.
        const auto reset_id = confirm->GetID("Reset here");
        const auto cancel_id = confirm->GetID("Cancel");
        const float row_y = confirm->DC.CursorMaxPos.y - 6;
        float reset_x = -1, cancel_x = -1;
        for (float x = confirm->Pos.x; x < confirm->Pos.x + confirm->Size.x; x += 3) {
            io.AddMousePosEvent(x, row_y);
            frame();
            if (test_context->HoveredId == reset_id && reset_x < 0) reset_x = x;
            if (test_context->HoveredId == cancel_id && cancel_x < 0) cancel_x = x;
        }
        require(reset_x >= 0 && cancel_x >= 0, "Reset confirmation buttons were not found");
        const auto click = [&](float x) {
            io.AddMousePosEvent(x + 4, row_y);
            frame();
            io.AddMouseButtonEvent(0, true);
            frame();
            io.AddMouseButtonEvent(0, false);
            frame(); frame();
        };
        snapshot.busy = true;
        frame(); frame();
        observed_actions.clear();
        click(reset_x);
        require(observed_actions.empty() && test_context->OpenPopupStack.Size == 1,
                "A busy replay accepted a camera path reset");
        click(cancel_x);
        require(test_context->OpenPopupStack.empty(),
                "A busy replay left the reset confirmation impossible to cancel");
        snapshot.busy = false;
        frame(); frame();
    }
    observed_actions.clear();
    // A row can be reordered by a different edit during live bank preview.
    // Never continue the gesture on the new occupant of the same row index.
    auto* campaths = ImGui::FindWindowByName("##lite-campaths");
    auto* camera_table=test_context->Tables.GetByKey(campaths->GetID("##lite-campath-details"));
    require(camera_table && camera_table->ColumnsCount==4,"Campaths must use the reference table");
    const float bank_x=(camera_table->Columns[3].WorkMinX+camera_table->Columns[3].WorkMaxX)*.5f;
    const float bank_y=camera_table->OuterRect.Min.y+camera_table->InstanceDataFirst.LastTopHeadersRowHeight+camera_table->RowMinHeight*.5f;
    io.MouseClickedTime[0] = -1e30;
    io.AddMousePosEvent(bank_x, bank_y);
    io.AddMouseButtonEvent(0, true);
    frame(); frame();
    io.AddMousePosEvent(bank_x + 16, bank_y);
    frame(); frame();
    require(!observed_actions.empty() && observed_actions.back().action == dolly::EditorAction::SetCameraRoll,
            "Bank drag did not retain live preview");
    observed_actions.clear();
    camera_revision_stub = 8;
    camera_time_bias_stub = 1;
    frame(); frame();
    io.AddMousePosEvent(bank_x + 32, bank_y);
    frame(); frame();
    io.AddMouseButtonEvent(0, false);
    frame(); frame();
    require(observed_actions.empty(), "Bank drag continued on a reordered camera row");
    camera_revision_stub = 7;
    camera_time_bias_stub = 0;
    frame(); frame();
    // Confirmation belongs to the reviewed path, not an incoming replacement.
    observed_actions.clear();
    test_context->NavNextActivateId = campaths->GetID("Clear All");
    frame(); frame();
    auto* confirmation = ImGui::FindWindowByName("Clear cameras?");
    require(confirmation && confirmation->Active, "Clear All confirmation missing");
    io.AddMousePosEvent(kill_x, panel.timeline_y() - 10);
    frame(); frame();
    io.AddMouseButtonEvent(0, true);
    frame(); frame();
    require(observed_actions.empty(), "Kill marker seeks through a modal confirmation");
    io.AddMouseButtonEvent(0, false);
    frame(); frame();
    camera_revision_stub = 8;
    frame(); frame();
    test_context->NavNextActivateId = confirmation->GetID("Clear");
    frame(); frame();
    require(observed_actions.empty(), "Changed campath can be cleared through an old confirmation");
    camera_revision_stub = 7;
    snapshot.busy = true;
    frame(); frame();
    test_context->NavNextActivateId = confirmation->GetID("Clear");
    frame(); frame();
    require(observed_actions.empty(), "Busy campath can be cleared through an open confirmation");
    snapshot.busy = false;
    for (auto state : {dolly::video::State::starting, dolly::video::State::recording,
                       dolly::video::State::finalizing}) {
        dolly::recording_status_stub.state = state;
        frame(); frame();
        test_context->NavNextActivateId = confirmation->GetID("Clear");
        frame(); frame();
        require(observed_actions.empty(), "Paused recording can be cleared through an open confirmation");
    }
    for (auto state : {dolly::video::State::idle, dolly::video::State::completed,
                       dolly::video::State::cancelled, dolly::video::State::failed})
        require(!dolly::editor_recording_locks_cameras(state),
                "A terminal recorder still locks camera editing");
    dolly::recording_status_stub = {};
    test_context->NavNextActivateId = confirmation->GetID("Cancel");
    frame(); frame();
    test_context->NavNextActivateId = campaths->GetID("Clear All");
    frame(); frame();
    test_context->NavNextActivateId = confirmation->GetID("Clear");
    frame(); frame();
    require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::ClearCameraPath &&
                observed_actions[0].revision == 7,
            "Reviewed campath cannot be cleared with its original revision");
    observed_actions.clear();
    test_context->NavNextActivateId = main->GetID("Look###LOOK");
    frame(); frame();
    auto* dof = card("##dof-card");
    test_context->NavNextActivateId = dof->GetID("Individual focus overrides");
    frame(); frame();
    const auto* individual = test_context->Tables.GetByKey(dof->GetID("##individual-focus"));
    require(individual && individual->ColumnsCount == 2,
            "Look page omits the four individual native focus overrides");
    for (int index : {6, 7, 8, 9}) {
        observed_actions.clear();
        const auto field_seed = ImHashData(&index, sizeof(index), individual->ID);
        test_context->NavNextActivateId = ImGui::GetIDWithSeed("##value", nullptr, field_seed);
        test_context->NavNextActivateFlags = ImGuiActivateFlags_PreferInput;
        frame(); frame();
        require(test_context->InputTextState.ID == test_context->ActiveId && test_context->ActiveId,
                "Individual focus override cannot accept an exact typed value");
        io.AddInputCharactersUTF8("12.50");
        frame();
        io.AddKeyEvent(ImGuiKey_Enter, true);
        frame();
        io.AddKeyEvent(ImGuiKey_Enter, false);
        frame(); frame();
        require(observed_actions.size() == 1 &&
                    unsigned(observed_actions[0].action) == unsigned(dolly::EditorAction::SetDofEnabled) + index &&
                    std::abs(observed_actions[0].value - 12.5) < .001,
                "Individual focus override dispatches the wrong effect or value");
    }
    test_context->NavNextActivateId = main->GetID("Export###EXPORT");
    frame(); frame();
    auto* settings = card("##export-settings");
    observed_actions.clear();
    test_context->NavNextActivateId = settings->GetID("Fixed-step export");
    frame(); frame();
    require(!observed_actions.empty() && observed_actions.back().action == dolly::EditorAction::SetVideoFixedStep,
            "Fixed-step export checkbox is disconnected");
    // Capture and layers are available together in the continuous Export tab.
    frame(); frame();
    auto* passes = card("##export-passes");
    test_context->NavNextActivateId = passes->GetID("Depth master (.mov)");
    frame(); frame();
    frame(); frame();
    const auto checkmark = ImGui::GetColorU32(ImGuiCol_CheckMark);
    bool fixed_step_checked = false;
    for (const auto& vertex : settings->DrawList->VtxBuffer)
        fixed_step_checked |= (vertex.col & 0x00ffffffu) == (checkmark & 0x00ffffffu);
    require(fixed_step_checked,
            "A second export edit erases the first pending checkbox before acknowledgement");
    const auto duration_snapshot=snapshot;
    const auto duration_recording=dolly::recording_status_stub;
    snapshot.camera_count=1;snapshot.selected_camera=0;snapshot.attach_selected=true;snapshot.attach_preview=true;
    snapshot.video_pov=false;snapshot.busy=snapshot.playing=false;snapshot.paused=true;
    dolly::recording_status_stub.state=dolly::video::State::completed;
    for(int i=0;i<20;++i)frame();
    auto* source_card=card("##export-camera");
    ImRect duration_rect;
    for(const char* seconds:{"5 seconds","15 seconds"}) {
        observed_actions.clear();test_context->NavNextActivateId=source_card->GetID("##export-duration");frame();frame();
        require(test_context->OpenPopupStack.Size && test_context->OpenPopupStack.back().Window,
                "The single attached-camera Replay duration dropdown cannot open during paused preview");
        duration_rect=source_card->NavRectRel[ImGuiNavLayer_Main];
        require(duration_rect.GetWidth()>0 && duration_rect.GetHeight()>0,
                "The active duration combo has no pointer rectangle");
        test_context->NavNextActivateId=test_context->OpenPopupStack.back().Window->GetID(seconds);frame();frame();
        require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetPovDuration &&
                    observed_actions[0].value==(seconds[0]=='5' ? 5 : 15),
                "The real Replay duration dropdown lost its duration request or changed another control");
        // Close an already-open chooser before testing that a disabled field
        // cannot open a new one. An existing popup alone is not an edit.
        if(!test_context->OpenPopupStack.empty()) ImGui::ClosePopupToLevel(0,true);
        frame();frame();
    }
    for(unsigned guard=0;guard<3;++guard) {
        snapshot.busy=guard==0;snapshot.playing=guard==1;
        dolly::recording_status_stub.state=guard==2 ? dolly::video::State::recording : dolly::video::State::completed;
        observed_actions.clear();
        const auto point=duration_rect.GetCenter();
        io.AddMousePosEvent(source_card->Pos.x+point.x,source_card->Pos.y+point.y);frame();frame();
        io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);frame();frame();
        if(!test_context->OpenPopupStack.empty() || !observed_actions.empty())
            std::fprintf(stderr,"Duration guard %u: popup %d name %s actions %zu busy %d playing %d recorder %u\n",guard,
                         test_context->OpenPopupStack.Size,test_context->OpenPopupStack.Size && test_context->OpenPopupStack.back().Window ?
                         test_context->OpenPopupStack.back().Window->Name : "none",observed_actions.size(),int(snapshot.busy),int(snapshot.playing),unsigned(dolly::recording_status_stub.state));
        require(test_context->OpenPopupStack.empty() && observed_actions.empty(),
                "A busy, playing or recording attached camera accepted an export-duration edit");
    }
    snapshot=duration_snapshot;dolly::recording_status_stub=duration_recording;frame();frame();
    // Each remaining tab has its own finished working surface. Follow and
    // Bone targets remain separate from the numeric framing controls.
    test_context->NavNextActivateId = main->GetID("Follow###FOLLOW");
    snapshot.follow_available = true;
    snapshot.follow_pending = true;
    snapshot.attach_available = true; // The shared synthetic roster producer is available.
    frame(); frame();
    auto* follow_target = card("##follow-card");
    auto* follow_framing = card("##follow-framing");
    require(follow_target->Active && follow_framing->Active,
            "Follow target and framing surfaces are not both available");
    test_context->NavNextActivateId = follow_target->GetID("##follow-hero");
    frame(); frame();
    require(!test_context->OpenPopupStack.empty(), "Follow hero chooser cannot open");
    auto* hero_popup = test_context->OpenPopupStack.back().Window;
    int hero_index = 0;
    const auto hero_seed = ImHashData(&hero_index, sizeof(hero_index), hero_popup->IDStack.back());
    test_context->NavNextActivateId = ImGui::GetIDWithSeed("Holliday (76)", nullptr, hero_seed);
    frame(); frame();
    observed_actions.clear();
    test_context->NavNextActivateId = follow_framing->GetID("Preview Follow");
    frame(); frame();
    require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::StartGameFollow &&
                observed_actions[0].value == snapshot.follow_distance &&
                observed_actions[0].pose[0] == snapshot.follow_shoulder &&
                observed_actions[0].pose[1] == snapshot.follow_height &&
                observed_actions[0].pose[2] == 0 && observed_actions[0].pose[3] == 19464268 &&
                observed_actions[0].pose[4] == 76,
            "Follow framing changes the existing target/offset payload");
    observed_actions.clear();
    test_context->NavNextActivateId = follow_target->GetID("Restore rig");
    frame(); frame();
    require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::StopGameFollow,
            "Follow layout changes its existing restore action");
    test_context->NavNextActivateId = main->GetID("Bone Picker###BONE PICKER");
    snapshot.attach_available = true;
    snapshot.attach_selected = true;
    frame(); frame();
    auto* attach_target = card("##attach-card");
    auto* attach_framing = card("##attach-framing");
    require(attach_target->Active && attach_framing->Active,
            "Bone target and framing surfaces are not both available");
    observed_actions.clear();
    test_context->NavNextActivateId = attach_target->GetID("Snap");
    frame(); frame();
    require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::AttachSnap,
            "Bone layout changes its existing Snap action");
    const auto snap_rect = attach_target->NavRectRel[ImGuiNavLayer_Main];
    require(snap_rect.GetWidth() > 0 && snap_rect.GetHeight() > 0,
            "Snap button has no pointer rectangle");
    snapshot.attach_preview = true;
    frame(); frame();
    observed_actions.clear();
    const auto snap_point = snap_rect.GetCenter();
    io.AddMousePosEvent(attach_target->Pos.x + snap_point.x, attach_target->Pos.y + snap_point.y);
    frame(); frame();
    io.AddMouseButtonEvent(0, true); frame();
    io.AddMouseButtonEvent(0, false); frame(); frame();
    require(observed_actions.empty(), "Attached preview queued a free-camera snap");
    snapshot.attach_preview = false;
    snapshot.attach_point = 2;
    snapshot.camera_count = 2;
    snapshot.selected_camera = 1;
    frame(); frame();
    observed_actions.clear();
    test_context->NavNextActivateId = attach_framing->GetID("Lock vertical aim");
    frame(); frame();
    require(observed_actions.size() == 1 &&
                observed_actions[0].action == dolly::EditorAction::SetAttachVerticalAimLock &&
                observed_actions[0].value == 1 && observed_actions[0].pose[0] == camera_revision_stub &&
                observed_actions[0].pose[1] == snapshot.selected_camera,
            "Bone framing lost the selected-camera level-aim request");
    const auto aim_rect = attach_framing->NavRectRel[ImGuiNavLayer_Main];
    require(aim_rect.GetWidth() > 0 && aim_rect.GetHeight() > 0,
            "Vertical aim lock has no pointer rectangle");
    snapshot.attach_preview = true;
    frame(); frame();
    observed_actions.clear();
    const auto aim_point = aim_rect.GetCenter();
    io.AddMousePosEvent(attach_framing->Pos.x + aim_point.x, attach_framing->Pos.y + aim_point.y);
    frame(); frame();
    io.AddMouseButtonEvent(0, true); frame();
    io.AddMouseButtonEvent(0, false); frame(); frame();
    require(observed_actions.empty(), "Attached preview accepted a vertical-aim change");
    snapshot.attach_preview = false;
    frame(); frame();
    observed_actions.clear();
    test_context->NavNextActivateId = attach_framing->GetID("##attach-offset-0");
    test_context->NavNextActivateFlags = ImGuiActivateFlags_PreferInput;
    frame(); frame();
    require(test_context->InputTextState.ID == test_context->ActiveId && test_context->ActiveId,
            "Bone framing cannot type an exact offset");
    io.AddInputCharactersUTF8("5.50");
    frame();
    io.AddKeyEvent(ImGuiKey_Enter, true); frame();
    io.AddKeyEvent(ImGuiKey_Enter, false); frame(); frame();
    require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::SetAttachOffsets &&
                observed_actions[0].value == 0 && observed_actions[0].pose[0] == 5.5 &&
                observed_actions[0].pose[2] == snapshot.attach_offsets[2],
            "Bone framing changes its existing six-axis offset payload");
    // A snapped camera can be farther than the small slider's ten-unit range.
    // Exact entry must keep that distance and all other authored offsets.
    snapshot.attach_offsets[0] = 17.5;
    snapshot.attach_offsets[1] = -31.25;
    snapshot.attach_offsets[2] = 8.75;
    frame(); frame();
    observed_actions.clear();
    test_context->NavNextActivateId = attach_framing->GetID("##attach-offset-0");
    test_context->NavNextActivateFlags = ImGuiActivateFlags_PreferInput;
    frame(); frame();
    require(test_context->InputTextState.ID == test_context->ActiveId && test_context->ActiveId,
            "A snapped offset cannot be edited as an exact number");
    io.AddInputCharactersUTF8("75.25");
    frame();
    io.AddKeyEvent(ImGuiKey_Enter, true); frame();
    io.AddKeyEvent(ImGuiKey_Enter, false); frame(); frame();
    require(observed_actions.size() == 1 &&
                observed_actions[0].action == dolly::EditorAction::SetAttachOffsets &&
                observed_actions[0].pose[0] == 75.25 &&
                observed_actions[0].pose[1] == -31.25 &&
                observed_actions[0].pose[2] == 8.75,
            "Editing a snapped offset truncated it or changed another axis");
    test_context->NavNextActivateId = main->GetID("Export###EXPORT");
    frame(); frame();
    observed_actions.clear();
    test_context->NavNextActivateId = main->GetID("Record video");
    frame(); frame();
    require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::StartVideo &&
                observed_actions[0].value == 0,
            "Export recording action is not retained in its fixed footer");
    for (float width : {640.f, 1280.f, 1920.f}) {
        io.DisplaySize = ImVec2(width, width == 640.f ? 480.f : 1080.f);
        dolly::recording_status_stub.state = dolly::video::State::recording;
        for (const char* page : {"Camera###CAMERA", "Follow###FOLLOW", "Bone Picker###BONE PICKER",
                                 "Look###LOOK", "Export###EXPORT"}) {
            test_context->NavNextActivateId = main->GetID(page);
            frame(); frame();
            observed_actions.clear();
            test_context->NavNextActivateId = main->GetID("Finish recording");
            frame(); frame();
            require(observed_actions.size() == 1 && observed_actions[0].action == dolly::EditorAction::StopVideo,
                    "An editor page hides Finish recording while the replay is paused");
            const auto bounds = test_context->NavWindow->NavRectRel[ImGuiNavLayer_Main];
            require(test_context->NavWindow == main && bounds.Min.x >= 0 &&
                        bounds.Max.x <= main->Size.x && bounds.Max.y <= main->Size.y,
                    "Finish recording is clipped in an editor footer");
            dolly::recording_status_stub.state = dolly::video::State::finalizing;
            observed_actions.clear();
            test_context->NavNextActivateId = main->GetID("Finish recording");
            frame(); frame();
            require(observed_actions.empty(), "A finalizing recorder still exposes Finish recording");
            dolly::recording_status_stub.state = dolly::video::State::recording;
        }
    }
    dolly::recording_status_stub = {};
    ImGui::DestroyContext(test_context);
    ImGui::SetCurrentContext(previous_context);
    snapshot = previous_snapshot;
    observed_actions.clear();
    std::puts("Look focus overrides and concurrent pending export controls passed.");
}
static void ui_rework_regressions() {
    auto* previous = ImGui::GetCurrentContext();
    const auto original = snapshot;
    char temporary_directory[MAX_PATH]{},section_file[MAX_PATH]{};
    require(GetTempPathA(MAX_PATH,temporary_directory) && GetTempFileNameA(temporary_directory,"dol",0,section_file),
            "Cannot create isolated layout preference test");
    require(DeleteFileA(section_file),"Cannot prepare a fresh isolated layout preference path");
    require(SetEnvironmentVariableA("DOLLY_UI_LAYOUT_FILE",section_file),"Cannot isolate managed layout storage");
    const std::wstring section_path(section_file,section_file+std::strlen(section_file));
    auto* ctx=ImGui::CreateContext();
    ImGui::SetCurrentContext(ctx);
    auto& io=ImGui::GetIO();
    io.DisplaySize=ImVec2(1920,1080); io.DeltaTime=1.f/60;
    io.IniFilename=nullptr; io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
    dolly::OverlayPanel panel; panel.style_panel(); panel.initialize_layout(); panel.load_panel_fonts(1);
    require(io.IniFilename==nullptr,"Managed layout storage re-enabled direct ImGui disk writes");
    io.Fonts->Build();
    snapshot.camera_count=2; snapshot.tick=4000; snapshot.paused=true;
    snapshot.pause_after_capture=false;
    snapshot.ready=true; snapshot.busy=false; snapshot.playing=false;
    snapshot.attach_preview=false; snapshot.follow_available=true;
    snapshot.attach_available=true; snapshot.dof_available=true;
    auto frame=[&] { ImGui::NewFrame(); panel.draw_panel(snapshot); panel.draw_lite_campaths(snapshot);
                    panel.draw_lite_timeline(snapshot); ImGui::Render(); panel.flush_layout(); };
    auto settle=[&] { for(int i=0;i<30;++i) frame(); };
    settle();
    auto* main=ImGui::FindWindowByName("Deadlock Dolly");
    auto* timeline=ImGui::FindWindowByName("##lite-timeline");
    auto* paths=ImGui::FindWindowByName("##lite-campaths");
    require(ctx->Tables.GetByKey(paths->GetID("##lite-campath-details")) != nullptr,
            "Campaths must use the four-column reference table");
    const auto menu_before=main->Pos,paths_before=paths->Pos,timeline_before=timeline->Pos;
    io.AddMousePosEvent(main->Pos.x+main->Size.x*.5f,main->Pos.y+7);frame();
    require(ImGui::GetMouseCursor()==ImGuiMouseCursor_ResizeAll,
            "Main header does not expose the shared four-arrow move cursor");
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
    io.AddMousePosEvent(io.MousePos.x-48,io.MousePos.y+30);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);settle();
    require(main->Pos.x<menu_before.x-40 && main->Pos.y>menu_before.y+20,
            "Main menu cannot move without Arrange panels");
    require(paths->Pos.x==paths_before.x && paths->Pos.y==paths_before.y &&
            timeline->Pos.x==timeline_before.x && timeline->Pos.y==timeline_before.y,
            "Moving only the main menu moves another panel");
    const auto main_after_drag=main->Pos, timeline_after_drag=timeline->Pos;
    const auto paths_size_before=paths->Size;
    observed_actions.clear();
    const ImVec2 campath_header(paths->Pos.x+50,paths->Pos.y+20);
    io.AddMousePosEvent(campath_header.x,campath_header.y);frame();frame();
    require(ImGui::GetMouseCursor()==ImGuiMouseCursor_ResizeAll &&
                (paths->Flags & ImGuiWindowFlags_NoResize),
            "Campaths header lacks the main panel's move feedback outside Arrange mode");
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
    io.AddMousePosEvent(campath_header.x+64,campath_header.y-24);frame();frame();
    require(ImGui::GetMouseCursor()==ImGuiMouseCursor_ResizeAll,
            "Campaths drag loses its four-arrow move cursor");
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);settle();
    const ImVec2 expected_paths_position(
        std::clamp(paths_before.x+64,12.f,std::max(12.f,io.DisplaySize.x-paths_size_before.x-12)),
        std::clamp(paths_before.y-24,12.f,std::max(12.f,io.DisplaySize.y-paths_size_before.y-12)));
    require(std::abs(paths->Pos.x-expected_paths_position.x)<1 &&
                std::abs(paths->Pos.y-expected_paths_position.y)<1 && paths->Pos.x>paths_before.x,
            "Campaths header cannot move freely without Arrange mode");
    require(paths->Size.x==paths_size_before.x && paths->Size.y==paths_size_before.y &&
                (paths->Flags & ImGuiWindowFlags_NoResize) &&
                main->Pos.x==main_after_drag.x && main->Pos.y==main_after_drag.y &&
                timeline->Pos.x==timeline_after_drag.x && timeline->Pos.y==timeline_after_drag.y &&
                observed_actions.empty(),
            "Moving campaths changes another panel, unlocks resize or emits a backend action");
    std::string header_ini;
    require(dolly::load_ui_layout(section_path,header_ini),"Cannot load the real header-release layout save");
    char expected_campath_position[96]{};
    std::snprintf(expected_campath_position,sizeof(expected_campath_position),
                  "[Window][##lite-campaths]\nPos=%d,%d\n",int(paths->Pos.x),int(paths->Pos.y));
    require(header_ini.find(expected_campath_position)!=std::string::npos &&
                header_ini.find("\nCustom=1\n")!=std::string::npos,
            "A standard-mode campaths drag does not save its position automatically");
    // The header hit area must leave the camera table and ordinary controls
    // untouched; a pointer drag on a camera row cannot become a panel drag.
    const auto stable_paths_position=paths->Pos;
    const auto* header_table=ctx->Tables.GetByKey(paths->GetID("##lite-campath-details"));
    const float row_x=(header_table->Columns[0].WorkMinX+header_table->Columns[0].WorkMaxX)*.5f;
    const float row_y=header_table->OuterRect.Min.y+header_table->InstanceDataFirst.LastTopHeadersRowHeight+header_table->RowMinHeight*.5f;
    io.MouseClickedTime[0]=-1e30;
    io.AddMousePosEvent(row_x,row_y);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
    io.AddMousePosEvent(row_x+12,row_y);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);settle();
    require(paths->Pos.x==stable_paths_position.x && paths->Pos.y==stable_paths_position.y &&
                observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SelectCamera,
            "A camera row gesture moves the campaths panel or loses camera selection");
    observed_actions.clear();
    const ImVec2 bounded_header(paths->Pos.x+50,paths->Pos.y+20);
    io.AddMousePosEvent(bounded_header.x,bounded_header.y);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
    io.AddMousePosEvent(-1000,-1000);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);settle();
    require(paths->Pos.x==12 && paths->Pos.y==12,
            "Campaths header drag can lose the panel outside the viewport");
    const ImVec2 return_header(paths->Pos.x+50,paths->Pos.y+20);
    io.AddMousePosEvent(return_header.x,return_header.y);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
    io.AddMousePosEvent(return_header.x+stable_paths_position.x-paths->Pos.x,
                        return_header.y+stable_paths_position.y-paths->Pos.y);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);settle();
    require(std::abs(paths->Pos.x-stable_paths_position.x)<1 &&
                std::abs(paths->Pos.y-stable_paths_position.y)<1 && observed_actions.empty(),
            "A clamped campaths panel cannot be dragged back without a backend action");
    auto activate=[&](ImGuiWindow* window,const char* item) {
        if(!window) {std::fprintf(stderr,"Missing activation window for %s\n",item);require(false,"Missing activation window");}
        ctx->NavNextActivateId=window->GetID(item);frame();frame();
    };
    auto find_card=[&](const char* name) -> ImGuiWindow* {
        for(auto* window:ctx->Windows)if(window->Active && std::strstr(window->Name,name))return window;
        return nullptr;
    };
    auto drag_section=[&](ImGuiWindow* source,ImGuiWindow* target) {
        require(source && target,"Section drag fixture is missing a card");
        const ImVec2 from(source->Pos.x+source->Size.x-9,source->Pos.y+source->WindowPadding.y+7);
        const ImVec2 to(target->Pos.x+target->WindowPadding.x+18,target->Pos.y+target->WindowPadding.y+7);
        io.AddMousePosEvent(from.x,from.y);frame();frame();
        io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
        io.AddMousePosEvent(from.x+20,from.y);frame();frame();
        require(ctx->DragDropActive,"Section header does not begin a real drag");
        io.AddMousePosEvent(to.x,to.y);frame();frame();
        io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);frame();settle();
    };
    observed_actions.clear();
    auto* replay_card=find_card("##cameras-card");auto* free_card=find_card("##flight-card");
    // Locked sections must keep controls and arrangement untouched.
    io.AddMousePosEvent(free_card->Pos.x+25,free_card->Pos.y+15);frame();frame();
    io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(io.MousePos.x+25,io.MousePos.y+25);frame();frame();
    require(!ctx->DragDropActive,"Sections can still be dragged with Arrange panels off");
    io.AddMouseButtonEvent(0,false);settle();
    activate(main,"Layout");auto* arrange_popup=ctx->OpenPopupStack.back().Window;
    activate(arrange_popup,"Arrange panels");
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    drag_section(free_card,replay_card);
    std::string disk_ini;
    require(dolly::load_ui_layout(section_path,disk_ini),"Cannot load the real section-drop layout save");
    require(disk_ini.find("Sections0=1,0,2,3")!=std::string::npos,"Section drop is not saved automatically to disk");
    require(free_card->Pos.x<replay_card->Pos.x,"Free camera and Replay/shot do not swap places");
    require(observed_actions.empty(),"Rearranging sections emits a backend action");
    ctx->NavNextActivateId=replay_card->GetID("Capture camera here");frame();frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::Capture,
            "Reordered Replay/shot controls lost their existing action");
    observed_actions.clear();
    std::size_t section_ini_length=0;
    const std::string section_ini(ImGui::SaveIniSettingsToMemory(&section_ini_length));
    require(section_ini.find("EditorCustom=1")!=std::string::npos && section_ini.find("\nCustom=1\n")!=std::string::npos &&
            section_ini.find("Sections0=1,0,2,3")!=std::string::npos,
            "Independent menu position and section order are not serialized");
    auto* restored_context=ImGui::CreateContext();ImGui::SetCurrentContext(restored_context);
    auto& restored_io=ImGui::GetIO();restored_io.DisplaySize=io.DisplaySize;restored_io.DeltaTime=1.f/60;
    dolly::OverlayPanel restored;restored.style_panel();restored.initialize_layout();restored.load_panel_fonts(1);restored_io.Fonts->Build();
    ImGui::LoadIniSettingsFromMemory(disk_ini.data(),disk_ini.size());
    auto restored_frame=[&] {ImGui::NewFrame();restored.draw_panel(snapshot);restored.draw_lite_campaths(snapshot);restored.draw_lite_timeline(snapshot);ImGui::Render();restored.flush_layout();};
    for(int i=0;i<30;++i)restored_frame();
    ImGuiWindow* restored_replay=nullptr,*restored_free=nullptr;
    for(auto* window:restored_context->Windows)if(window->Active) {
        if(std::strstr(window->Name,"##cameras-card"))restored_replay=window;
        if(std::strstr(window->Name,"##flight-card"))restored_free=window;
    }
    require(restored_replay && restored_free && restored_free->Pos.x<restored_replay->Pos.x,
            "Section order does not survive a fresh UI instance");
    auto* restored_main=ImGui::FindWindowByName("Deadlock Dolly");
    require(std::abs(restored_main->Pos.x-main->Pos.x)<1 && std::abs(restored_main->Pos.y-main->Pos.y)<1,
            "Independent main-menu position does not restore");
    auto* restored_paths=ImGui::FindWindowByName("##lite-campaths");
    require(restored_paths && std::abs(restored_paths->Pos.x-paths->Pos.x)<1 &&
                std::abs(restored_paths->Pos.y-paths->Pos.y)<1 &&
                std::abs(restored_paths->Size.x-paths->Size.x)<1 &&
                std::abs(restored_paths->Size.y-paths->Size.y)<1,
            "Standard-mode campaths position does not survive a fresh UI instance");
    const char* malformed="[DollyUI][Preferences]\nSections0=99,0,2,3\nSections0=0,0,2,3\nSections3=1,1,2,3\nSections-1=0,1,2,3\nSections0=0,1,2,3junk\n";
    ImGui::LoadIniSettingsFromMemory(malformed);for(int i=0;i<3;++i)restored_frame();
    require(restored_free->Pos.x<restored_replay->Pos.x,"Malformed layout data overwrites a valid section order");
    ImGui::DestroyContext(restored_context);ImGui::SetCurrentContext(ctx);
    std::puts("Independent header drag, real section swap, preserved actions, fresh-instance restore and invalid-layout rejection passed.");
    activate(main,"Bone Picker###BONE PICKER");settle();
    auto* bone_size_card=find_card("##attach-card");
    activate(bone_size_card,"Reset");settle();
    const auto bone_row_before=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main];observed_actions.clear();
    const auto bone_size_before=bone_size_card->Size;const auto bone_font_before=bone_size_card->FontRefSize;
    const ImVec2 resize_point(bone_size_card->Pos.x+bone_size_card->Size.x-7,bone_size_card->Pos.y+bone_size_card->Size.y-7);
    io.AddMousePosEvent(resize_point.x,resize_point.y);frame();frame();
    io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(resize_point.x+55,resize_point.y+55);frame();frame();
    io.AddMouseButtonEvent(0,false);settle();
    if(!(bone_size_card->Size.x>bone_size_before.x+20 && bone_size_card->Size.y>bone_size_before.y+30))
        std::fprintf(stderr,"Resize bone %.1fx%.1f -> %.1fx%.1f active %u hovered %s\n",bone_size_before.x,bone_size_before.y,bone_size_card->Size.x,bone_size_card->Size.y,ctx->ActiveId,ctx->HoveredWindow ? ctx->HoveredWindow->Name : "none");
    require(bone_size_card->Size.x>bone_size_before.x+20 && bone_size_card->Size.y>bone_size_before.y+30,"Bone camera resize handle does not enlarge its section");
    require(bone_size_card->FontRefSize==bone_font_before,"Section resize changes the font size");
    activate(bone_size_card,"Reset");settle();
    const auto bone_row_after=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main];observed_actions.clear();
    require(bone_row_after.Min.y>bone_row_before.Min.y+5 && bone_row_after.GetHeight()>bone_row_before.GetHeight()+1,
            "Taller sections do not distribute their controls vertically");
    const std::string resized_ini(ImGui::SaveIniSettingsToMemory());
    require(resized_ini.find("Heights2=0.00,0.00,0.00,0.00")==std::string::npos,"Section height is not persisted");
    if(const auto* output=std::getenv("DOLLY_UI_TEST_RESIZED_LAYOUT_OUT")) {
        std::ofstream saved(output,std::ios::binary);saved<<resized_ini;require(saved.good(),"Cannot save resized-layout capture fixture");
    }
    const auto resized_expected=bone_size_card->Size;
    auto* size_context=ImGui::CreateContext();ImGui::SetCurrentContext(size_context);
    auto& size_io=ImGui::GetIO();size_io.DisplaySize=io.DisplaySize;size_io.DeltaTime=1.f/60;
    dolly::OverlayPanel size_panel;size_panel.style_panel();size_panel.initialize_layout();size_panel.load_panel_fonts(1);size_io.Fonts->Build();
    ImGui::LoadIniSettingsFromMemory(resized_ini.data(),resized_ini.size());
    ImGui::LoadIniSettingsFromMemory("[Window][##lite-campaths]\nPos=16,588\nSize=340,280\n\n[DollyUI][Preferences]\nSidebarWidthCurrent=0\n");
    auto size_frame=[&]{ImGui::NewFrame();size_panel.draw_panel(snapshot);size_panel.draw_lite_campaths(snapshot);size_panel.draw_lite_timeline(snapshot);ImGui::Render();size_panel.flush_layout();};
    size_frame();size_context->NavNextActivateId=ImGui::FindWindowByName("Deadlock Dolly")->GetID("Bone Picker###BONE PICKER");
    for(int i=0;i<30;++i)size_frame();
    require(std::abs(ImGui::FindWindowByName("##lite-campaths")->Size.x-340)<1,
            "A saved custom sidebar width was replaced by default-width migration");
    ImGuiWindow* restored_size=nullptr;
    for(auto* window:size_context->Windows)if(window->Active && std::strstr(window->Name,"##attach-card"))restored_size=window;
    if(restored_size && !(std::abs(restored_size->Size.x-resized_expected.x)<1 && std::abs(restored_size->Size.y-resized_expected.y)<1))
        std::fprintf(stderr,"Resized restore expected %.1fx%.1f got %.1fx%.1f main %.1fx%.1f\n",resized_expected.x,resized_expected.y,restored_size->Size.x,restored_size->Size.y,ImGui::FindWindowByName("Deadlock Dolly")->Size.x,ImGui::FindWindowByName("Deadlock Dolly")->Size.y);
    require(restored_size && std::abs(restored_size->Size.x-resized_expected.x)<1 && std::abs(restored_size->Size.y-resized_expected.y)<1,"Resized section dimensions do not survive a fresh UI instance");
    ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nWidths2=nan,1,1\nHeights2=-10,0,0,0\nColumns2=99,1,-1,-1\n");
    for(int i=0;i<3;++i)size_frame();
    require(std::abs(restored_size->Size.x-resized_expected.x)<1 && std::abs(restored_size->Size.y-resized_expected.y)<1,"Invalid saved sizes overwrite the valid arrangement");
    ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nCustom=0\nSidebarWidthCurrent=0\n");
    for(int i=0;i<3;++i)size_frame();
    require(ImGui::FindWindowByName("##lite-campaths")->Size.x<260,
            "An old non-custom sidebar width does not adopt the narrower default");
    size_io.IniFilename=nullptr;ImGui::DestroyContext(size_context);ImGui::SetCurrentContext(ctx);
    activate(main,"Export###EXPORT");settle();
    auto* expanded_size=find_card("##export-settings");
    if(!expanded_size->StateStorage.GetInt(expanded_size->GetID("Encoder & quality"),0))activate(expanded_size,"Encoder & quality");
    settle();
    const ImVec2 expanded_edge(expanded_size->Pos.x+expanded_size->Size.x-7,expanded_size->Pos.y+expanded_size->Size.y-7);
    io.AddMousePosEvent(expanded_edge.x,expanded_edge.y);frame();frame();io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(expanded_edge.x,expanded_edge.y+30);frame();frame();io.AddMouseButtonEvent(0,false);settle();
    const float expanded_outer=expanded_size->Size.y;
    activate(expanded_size,"Encoder & quality");settle();
    require(std::abs(expanded_size->Size.y-expanded_outer)<1,"A user-sized expanded section loses its outer height when collapsed");
    ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nHeights4=0,0,0,0\n");
    activate(main,"Bone Picker###BONE PICKER");settle();
    // A card may be placed in unused column space, not just swapped.
    const auto body=bone_size_card->ParentWindow;
    const ImVec2 empty_from(bone_size_card->Pos.x+20,bone_size_card->Pos.y+15);
    auto* bone_frame_card=find_card("##attach-framing");
    // Restore the extra height first so the two cards can share a tall column.
    ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nHeights2=0,0,0,0\n");
    ImGui::SetWindowSize(main,ImVec2(940,700));settle();
    const ImVec2 empty_to(bone_frame_card->Pos.x+25,bone_frame_card->Pos.y+bone_frame_card->Size.y+25);
    io.AddMousePosEvent(empty_from.x,empty_from.y);frame();frame();io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(empty_from.x+20,empty_from.y);frame();frame();
    require(ctx->DragDropActive,"Empty placement did not start a section drag");
    io.AddMousePosEvent(empty_to.x,empty_to.y);frame();frame();io.AddMouseButtonEvent(0,false);settle();
    if(!(std::abs(bone_size_card->Pos.x-bone_frame_card->Pos.x)<1 && bone_size_card->Pos.y>bone_frame_card->Pos.y+bone_frame_card->Size.y))
        std::fprintf(stderr,"Empty target %.1f %.1f; bone %.1f %.1f %.1fx%.1f frame %.1f %.1f %.1fx%.1f body %.1fx%.1f\n%s\n",empty_to.x,empty_to.y,bone_size_card->Pos.x,bone_size_card->Pos.y,bone_size_card->Size.x,bone_size_card->Size.y,bone_frame_card->Pos.x,bone_frame_card->Pos.y,bone_frame_card->Size.x,bone_frame_card->Size.y,body->Size.x,body->Size.y,ImGui::SaveIniSettingsToMemory());
    require(std::abs(bone_size_card->Pos.x-bone_frame_card->Pos.x)<1 && bone_size_card->Pos.y>bone_frame_card->Pos.y+bone_frame_card->Size.y,"Empty column space cannot receive a section");
    require(body->Scroll.y==0,"Custom section placement adds scrolling");
    ImGui::SetWindowSize(main,ImVec2(940,520));settle();
    const auto menu_before_matrix=main->Pos,menu_size_before_matrix=main->Size;
    const auto timeline_before_matrix=timeline->Pos,timeline_size_before_matrix=timeline->Size;
    const auto dof_before_reorder=snapshot.dof;
    snapshot.dof={1,1,-100,0,180,1490,-100,0,180,2000,.5};
    unsigned checked_arrangements=0;
    for(const auto viewport : {ImVec2(1280,720),ImVec2(640,480)}) {
        io.DisplaySize=viewport;settle();
        for(int page : {3,4}) {
            activate(main,page==3 ? "Look###LOOK" : "Export###EXPORT");settle();
            auto* advanced=find_card(page==3 ? "##dof-card" : "##export-settings");
            const char* header=page==3 ? "Individual focus overrides" : "Encoder & quality";
            if(!advanced->StateStorage.GetInt(advanced->GetID(header),0))activate(advanced,header);
            settle();
            require(advanced->StateStorage.GetInt(advanced->GetID(header),0),"Permutation fixture failed to expand its controls");
            unsigned order[]={0,1,2,3};
            do {
                char preference[120];std::snprintf(preference,sizeof(preference),"[DollyUI][Preferences]\nSections%d=%u,%u,%u,%u\n",page,order[0],order[1],order[2],order[3]);
                ImGui::LoadIniSettingsFromMemory(preference);settle();
                ImGuiWindow* content=nullptr;float last_bottom=0;
                for(auto* card:ctx->Windows)if(card->Active && card->ParentWindow && std::strstr(card->ParentWindow->Name,"##editor-content")) {
                    content=card->ParentWindow;last_bottom=std::max(last_bottom,card->Pos.y+card->Size.y);
                    require(card->ScrollMax.y<=1 && card->DC.CursorMaxPos.y<=card->Pos.y+card->Size.y+1,
                            "A saved expanded section arrangement internally clips or scrolls its controls");
                    for(auto* other:ctx->Windows)if(other!=card && other->Active && other->ParentWindow==card->ParentWindow) {
                        const bool overlap=card->Pos.x<other->Pos.x+other->Size.x-1 && other->Pos.x<card->Pos.x+card->Size.x-1 &&
                                           card->Pos.y<other->Pos.y+other->Size.y-1 && other->Pos.y<card->Pos.y+card->Size.y-1;
                        require(!overlap,"Rearranged sections overlap");
                    }
                }
                require(content && main->DC.CursorMaxPos.y<=main->InnerClipRect.Max.y+1,
                        "An expanded section arrangement loses its content or covers the fixed footer");
                if(last_bottom>content->InnerClipRect.Max.y+1) {
                    require(content->ScrollMax.y>0 && !(content->Flags&ImGuiWindowFlags_NoScrollWithMouse),
                            "Expanded aligned rows extend beyond a viewport with no usable scrolling");
                    if(checked_arrangements==0 || checked_arrangements==24 || checked_arrangements==48 || checked_arrangements==72)
                        std::fprintf(stderr,"Expanded aligned rows %.0fx%.0f page %d: last border %.1f clip %.1f..%.1f row-content %.1f scroll-max %.1f\n",
                            viewport.x,viewport.y,page,last_bottom,content->InnerClipRect.Min.y,content->InnerClipRect.Max.y,
                            content->ContentSize.y,content->ScrollMax.y);
                    io.AddMousePosEvent(content->InnerClipRect.Min.x+12,content->InnerClipRect.Min.y+12);frame();frame();
                    for(unsigned step=0;step<16;++step) {io.AddMouseWheelEvent(0,-10);frame();frame();}
                    settle();last_bottom=0;float last_control=0;
                    for(auto* card:ctx->Windows)if(card->Active && card->ParentWindow==content) {
                        last_bottom=std::max(last_bottom,card->Pos.y+card->Size.y);
                        last_control=std::max(last_control,card->DC.CursorMaxPos.y);
                    }
                    require(content->Scroll.y>=content->ScrollMax.y-1 && last_bottom<=content->InnerClipRect.Max.y+1 &&
                                last_control<=content->InnerClipRect.Max.y+1,
                            "Real wheel input cannot reach the final expanded card border and controls");
                    ImGui::SetScrollY(content,0);settle();
                } else require(content->ScrollMax.y<=1,"An expanded arrangement that fits acquired unnecessary scrolling");
                ++checked_arrangements;
            }while(std::next_permutation(std::begin(order),std::end(order)));
        }
    }
    require(checked_arrangements==96,"Section arrangement coverage is incomplete");
    ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nSections3=0,1,2,3\nSections4=0,1,2,3\n");
    snapshot.dof=dof_before_reorder;io.DisplaySize=ImVec2(1920,1080);
    ImGui::SetWindowPos(main,menu_before_matrix);ImGui::SetWindowSize(main,menu_size_before_matrix);
    ImGui::SetWindowPos(timeline,timeline_before_matrix);ImGui::SetWindowSize(timeline,timeline_size_before_matrix);settle();activate(main,"Camera###CAMERA");settle();
    std::puts("All96 expanded Look/Export arrangements preserve internal bounds/no overlap/footer and actual wheel reachability; fitting rows retain zero scroll.");
    replay_info_known_stub=false;frame();frame();
    require(panel.timeline_view_end()<=1.1f,"Unknown replay fixture is not bounded");
    replay_info_known_stub=true;settle();
    require(panel.timeline_view_end()>=146925,"Replay metadata arrival leaves the ruler stuck at 0-1");
    activate(main,"Look###LOOK");settle();
    ImGuiWindow* content=nullptr;
    float last_card_bottom=0;
    for(auto* window:ctx->Windows) if(window->Active && window->ParentWindow &&
            std::strstr(window->ParentWindow->Name,"##editor-content")) {
        content=window->ParentWindow;
        last_card_bottom=std::max(last_card_bottom,window->Pos.y+window->Size.y);
    }
    unsigned look_cards=0;
    for(auto* window:ctx->Windows) if(window->Active && window->ParentWindow &&
            std::strstr(window->ParentWindow->Name,"##editor-content")) ++look_cards;
    require(look_cards==4,"Look must display its four control cards without an Effects placeholder");
    require(content && main->Size.y<=520,"The editor footer still hangs below an oversized menu");
    const float matched_height = main->Size.y;
    float matched_font=0;
    for (const char* tab : {"Camera###CAMERA", "Follow###FOLLOW", "Bone Picker###BONE PICKER", "Look###LOOK", "Export###EXPORT"}) {
        activate(main,tab);settle();
        require(std::abs(main->Size.y-matched_height)<1,"Main tabs change the menu height");
        for (auto* card:ctx->Windows) if (card->Active && card->ParentWindow &&
                std::strstr(card->ParentWindow->Name,"##editor-content")) {
            if (!matched_font) matched_font=card->FontRefSize;
            require(std::abs(card->FontRefSize-matched_font)<.1f,"Main tabs use different content font sizes");
        }
    }
    require(main->Size.y<=io.DisplaySize.y-timeline->Size.y-40 && !main->ScrollbarY && main->ScrollMax.y<=1,
            "The continuous editor does not fit its current controls");
    observed_actions.clear();activate(main,"Stop / restore");
    require(ctx->NavWindow==main && ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main].GetWidth()<=160,
            "Stop / restore still stretches across the editor");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::Stop,
            "The compact Stop / restore button lost its existing action");
    activate(main,"Camera###CAMERA");settle();
    activate(timeline,"Play cinematic");
    const auto cinematic_rect=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main];
    require(cinematic_rect.Min.x<260 && cinematic_rect.Min.y<45,
            "Play cinematic must sit next to Replay at the top left");
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    activate(timeline,"##next-player");
    const float next_arrow_x=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main].Min.x;
    activate(timeline,"##next-player");
    require(std::abs(ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main].Min.x-next_arrow_x)<.1f,
            "Player name length moves the navigation arrows");
    observed_actions.clear();
    activate(timeline,"< Previous");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::PreviousView,
            "Timeline previous camera button lost the saved-view action");
    const float previous_x=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main].Min.x;
    const auto before_camera_seek = snapshot;
    snapshot.tick = 31646;
    snapshot.duration = 3;
    snapshot.selected_camera = 0;
    snapshot.playhead = 0;
    frame();
    frame();
    const int second_camera = 1;
    const auto second_row_seed = ImHashData(&second_camera, sizeof(second_camera), paths->ID);
    observed_actions.clear();
    ctx->NavNextActivateId = ImGui::GetIDWithSeed("##camera", nullptr, second_row_seed);
    frame();
    frame();
    require(observed_actions.size() == 1 &&
                observed_actions[0].action == dolly::EditorAction::SelectCamera &&
                observed_actions[0].value == 1 &&
                observed_actions[0].pose[0] == camera_revision_stub,
            "Camera 2 selection lost its index or current revision");
    snapshot.selected_camera = 1;
    snapshot.playhead = 3;
    for (int echoed_frame = 0; echoed_frame < 4; ++echoed_frame)
        frame();
    require(panel.timeline_mode() == 0, "Selecting Camera 2 switched away from Replay");
    observed_actions.clear();
    activate(timeline, "Seek camera");
    require(observed_actions.size() == 1 &&
                observed_actions[0].action == dolly::EditorAction::SeekCamera &&
                observed_actions[0].value == 1 &&
                observed_actions[0].pose[0] == camera_revision_stub,
            "Replay Seek camera re-seeks the current tick instead of the selected camera");
    const float seek_x=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main].Min.x;
    const auto seek_rect=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main];
    require(std::abs((seek_rect.Min.x + seek_rect.Max.x) * .5f + timeline->DC.CursorStartPos.x -
                     timeline->Pos.x - timeline->Size.x * .5f) < 2,
            "Seek camera is not centered on the timeline");
    snapshot.camera_count = 40;
    snapshot.duration = 117;
    camera_first_stub = 32;
    frame();
    frame();
    observed_actions.clear();
    activate(timeline, "Seek camera");
    require(observed_actions.size() == 1 &&
                observed_actions[0].action == dolly::EditorAction::SeekCamera &&
                observed_actions[0].value == 1 &&
                observed_actions[0].pose[0] == camera_revision_stub,
            "Browsing another camera page disabled seeking the selected camera");
    snapshot.camera_count = 2;
    snapshot.duration = 3;
    camera_first_stub = 0;
    frame();
    frame();
    activate(timeline, "Campaths###lite-campath-mode");
    observed_actions.clear();
    io.AddMousePosEvent((panel.timeline_x0() + panel.timeline_x1()) * .5f, panel.timeline_y());
    frame();
    frame();
    io.AddMouseButtonEvent(0, true);
    frame();
    frame();
    io.AddMouseButtonEvent(0, false);
    frame();
    frame();
    require(observed_actions.empty(),
            "Campaths draft scrub unexpectedly seeks before confirmation");
    activate(timeline, "Seek here");
    require(observed_actions.size() == 1 &&
                observed_actions[0].action == dolly::EditorAction::SeekShot &&
                observed_actions[0].value > 0 && observed_actions[0].value < 3,
            "Campaths Seek here lost its independently chosen shot cursor");
    activate(timeline, "Replay###lite-timeline-mode");
    snapshot = before_camera_seek;
    frame();
    frame();
    observed_actions.clear();activate(timeline,"Resume");
    const auto resume_rect=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main];
    require(resume_rect.Min.y>seek_rect.Max.y && resume_rect.Min.x>timeline->WindowPadding.x &&
                resume_rect.Min.x<timeline->Size.x*.5f,
            "Resume is still isolated from the centered playback controls");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::PlayPause,
            "Relocated Resume lost its replay pause/play action");
    observed_actions.clear();activate(timeline,"Pause after capture");
    const auto pause_capture_rect=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main];
    require(pause_capture_rect.Min.x>resume_rect.Max.x &&
                std::abs(pause_capture_rect.Min.y-resume_rect.Min.y)<1 &&
                pause_capture_rect.Max.x<timeline->Size.x-timeline->WindowPadding.x,
            "Pause after capture does not fit beside Resume");
    require(observed_actions.size()==1 &&
                observed_actions[0].action==dolly::EditorAction::SetPauseAfterCapture &&
                observed_actions[0].value==1,
            "Pause after capture does not enable the shared capture preference");
    // A second click must act on the pending checked value, even before the
    // desktop has acknowledged it. Otherwise the outgoing false publication
    // flashes back and clicking again incorrectly sends a second enable.
    frame();frame();observed_actions.clear();activate(timeline,"Pause after capture");
    require(observed_actions.size()==1 &&
                observed_actions[0].action==dolly::EditorAction::SetPauseAfterCapture &&
                observed_actions[0].value==0,
            "A delayed publication flashes the capture pause preference back off");
    frame();frame();activate(timeline,"Pause after capture");
    snapshot.pause_after_capture=true;frame();frame();
    observed_actions.clear();activate(timeline,"Pause after capture");
    require(observed_actions.size()==1 &&
                observed_actions[0].action==dolly::EditorAction::SetPauseAfterCapture &&
                observed_actions[0].value==0,
            "Pause after capture cannot disable an acknowledged preference");
    snapshot.pause_after_capture=false;frame();frame();
    observed_actions.clear();activate(timeline,"Forward >");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::NextView &&
                previous_x<seek_x && seek_x<ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main].Min.x,
            "Camera view buttons do not surround Seek here");
    observed_actions.clear();activate(timeline,"##lite-tick");
    auto tick_rect=ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main];
    tick_rect.Translate(ImVec2(ctx->NavWindow->Pos.x-timeline->Pos.x,
                              ctx->NavWindow->Pos.y-timeline->Pos.y));
    require(tick_rect.Min.x>timeline->Size.x*.7f && tick_rect.Min.y<seek_rect.Min.y,
            "Tick editor must replace the last ruler label at the right end");
    const ImVec2 tick_click(timeline->Pos.x+(tick_rect.Min.x+tick_rect.Max.x)*.5f,
                            timeline->Pos.y+(tick_rect.Min.y+tick_rect.Max.y)*.5f);
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    io.AddMousePosEvent(tick_click.x,tick_click.y);frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);frame();
    require(ctx->ActiveId==timeline->GetID("##lite-tick"),"Relocated tick field cannot be clicked with the mouse");
    io.AddInputCharactersUTF8("561");frame();
    io.AddKeyEvent(ImGuiKey_Enter,true);frame();io.AddKeyEvent(ImGuiKey_Enter,false);frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SeekReplayTick &&
                observed_actions[0].value==561,"Relocated tick input no longer seeks on Enter");
    observed_actions.clear();
    io.AddKeyEvent(ImGuiKey_Space,true);
    ctx->NavNextActivateId=timeline->GetID("Play cinematic");frame();frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::PrepareCinematic,
            "Cinematic playback starts before replay preparation");
    auto play_count=[&] {
        return std::count_if(observed_actions.begin(),observed_actions.end(),[](const auto& action) {
            return action.action==dolly::EditorAction::PlayPath;
        });
    };
    dolly::cinematic_status_stub.token=unsigned(observed_actions[0].value);
    dolly::cinematic_status_stub.revision=camera_revision_stub;
    dolly::cinematic_status_stub.state=1;
    snapshot.busy=true;frame();frame();
    require(play_count()==0,"Cinematic starts during preparation");
    dolly::cinematic_status_stub.state=2;
    frame();require(play_count()==0,"Cinematic starts while the worker is still busy");
    snapshot.busy=false;frame();frame();frame();
    require(play_count()==1,"Completed preparation must automatically play exactly once");
    const auto* prompt=ImGui::FindWindowByName("##cinematic-ready");
    require(!prompt || !prompt->Active,"Cinematic still requires a Space confirmation");
    io.AddKeyEvent(ImGuiKey_Space,false);frame();
    // An old completion or a canceled/failed seek cannot start playback.
    auto prepare_request=[&] {
        observed_actions.clear();activate(timeline,"Play cinematic");
        require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::PrepareCinematic,
                "Cinematic preparation request is missing");
        return unsigned(observed_actions[0].value);
    };
    auto token=prepare_request();
    dolly::cinematic_status_stub.token=token-1;dolly::cinematic_status_stub.state=2;frame();frame();
    require(play_count()==0,"An old preparation completion starts playback");
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    const auto retry_token=prepare_request();
    require(retry_token!=token,"Retrying a canceled preparation reuses its pending token");
    dolly::cinematic_status_stub.token=token;frame();frame();
    require(play_count()==0,"A canceled preparation starts playback during a retry");
    activate(main,"Stop / restore");
    dolly::cinematic_status_stub.token=retry_token;frame();frame();
    require(play_count()==0,"Stop allows a pending preparation to start playback");
    token=prepare_request();
    dolly::cinematic_status_stub.token=token;dolly::cinematic_status_stub.state=3;frame();frame();
    require(play_count()==0,"Failed cinematic preparation starts playback");
    token=prepare_request();
    ++camera_revision_stub;frame();
    dolly::cinematic_status_stub.token=token;dolly::cinematic_status_stub.state=2;frame();frame();
    require(play_count()==0,"An edited path starts an old cinematic request");
    --camera_revision_stub;
    // The marker remains independently clickable with the tracker on its tick,
    // including the closest supported one-tick replay view.
    io.AddMousePosEvent(panel.timeline_x0()+(panel.timeline_x1()-panel.timeline_x0())*4000.f/146925.f,panel.timeline_y());
    io.AddMouseWheelEvent(0,100); frame(); frame();
    require(panel.timeline_view_end()-panel.timeline_view_start()<=1.1f,"Replay cannot zoom to individual ticks");
    const float kill_x=panel.timeline_x0()+(panel.timeline_x1()-panel.timeline_x0())*
                       (4000.f-panel.timeline_view_start())/(panel.timeline_view_end()-panel.timeline_view_start());
    io.AddMousePosEvent(kill_x,panel.timeline_y()-19); frame();frame();
    observed_actions.clear();io.AddMouseButtonEvent(0,true);frame();frame();
    io.AddMouseButtonEvent(0,false);frame();frame();
    if (observed_actions.size()!=1)
        std::fprintf(stderr,"Kill zoom %.3f..%.3f, pointer %.1f %.1f, hovered %s, %zu actions\n",
                     panel.timeline_view_start(),panel.timeline_view_end(),io.MousePos.x,io.MousePos.y,
                     ctx->HoveredWindow ? ctx->HoveredWindow->Name : "none",observed_actions.size());
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SeekReplayTick &&
                observed_actions[0].value==4000,"Playhead or zoom hides the kill marker's hit target");
    // This tick is Ivy's kill of Haze. Both participants retain the hit target,
    // with kill/death colors, while an unrelated selected player must not.
    dolly::replay_third_player_stub=true;
    activate(timeline,"##next-player");
    observed_actions.clear();
    io.AddMousePosEvent(kill_x,panel.timeline_y()-19);frame();frame();
    io.AddMouseButtonEvent(0,true);frame();frame();
    io.AddMouseButtonEvent(0,false);frame();frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SeekReplayTick &&
                observed_actions[0].value==4000,"Selected player's death marker is not clickable");
    activate(timeline,"##next-player");
    observed_actions.clear();
    io.AddMousePosEvent(kill_x,panel.timeline_y()-19);frame();frame();
    io.AddMouseButtonEvent(0,true);frame();frame();
    io.AddMouseButtonEvent(0,false);frame();frame();
    require(observed_actions.empty(),"Unrelated players' events remain clickable after changing player");
    activate(timeline,"##next-player");
    dolly::replay_third_player_stub=false;
    observed_actions.clear();
    io.AddMousePosEvent(kill_x,panel.timeline_y()-19);frame();frame();
    io.AddMouseButtonEvent(0,true);frame();frame();
    io.AddMouseButtonEvent(0,false);frame();frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SeekReplayTick &&
                observed_actions[0].value==4000,"Selecting the killer does not restore their marker");
    // Reloading an equally long replay must discard an old one-tick zoom.
    snapshot.ready=false;frame();frame();
    snapshot.ready=true;frame();frame();
    require(panel.timeline_view_start()==0 && panel.timeline_view_end()==146925,
            "Replay startup retains a previous zoom and hides kill events");
    io.AddMousePosEvent((panel.timeline_x0()+panel.timeline_x1())*.5f,panel.timeline_y());
    io.AddMouseWheelEvent(0,8);frame();frame();
    require(panel.timeline_view_end()-panel.timeline_view_start()<146925,
            "Manual zoom no longer works after initial replay fit");
    dolly::replay_events_status_stub=1;frame();frame();
    dolly::replay_events_status_stub=2;frame();frame();
    require(panel.timeline_view_start()==0 && panel.timeline_view_end()==146925,
            "Newly loaded kill index does not fit all replay events");
    activate(main,"Layout");
    require(!ctx->OpenPopupStack.empty(),"Layout preferences cannot open");
    auto* layout=ctx->OpenPopupStack.back().Window;
    if(paths->Flags&ImGuiWindowFlags_NoMove)activate(layout,"Arrange panels");
    activate(layout,"Reduce motion");
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    require(!(paths->Flags&ImGuiWindowFlags_NoMove) && !(timeline->Flags&ImGuiWindowFlags_NoResize),
            "Arrange panels does not unlock the campaths and timeline");
    ImGui::SetWindowSize(main,ImVec2(420,400));settle();
    require(main->Size.x>=640 && main->Size.y>=520,
            "Editor resizing can still shrink controls into tiny text");
    ImGui::SetWindowSize(main,ImVec2(940,520));settle();
    ImGui::SetWindowSize(paths,ImVec2(220,paths->Size.y));settle();
    require(paths->Size.x<260 && paths->Size.x>=220,"Campath sidebar cannot resize down to its readable table minimum");
    const auto* narrow_table=ctx->Tables.GetByKey(paths->GetID("##lite-campath-details"));
    require(narrow_table && narrow_table->Columns[0].WorkMaxX-narrow_table->Columns[0].WorkMinX>=ImGui::CalcTextSize("Camera 64").x+4,
            "Narrow sidebar clips camera names during their slide animation");
    require(narrow_table->Columns[3].WorkMaxX-narrow_table->Columns[3].WorkMinX-4>=ImGui::CalcTextSize("-180.0").x,
            "Narrow sidebar clips valid negative Bank values");
    const auto edge_width=paths->Size.x;
    const ImVec2 edge(paths->Pos.x+paths->Size.x-3,paths->Pos.y+paths->Size.y*.5f);
    io.AddMousePosEvent(edge.x,edge.y);frame();frame();io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(edge.x+60,edge.y);frame();frame();io.AddMouseButtonEvent(0,false);settle();
    require(paths->Size.x>edge_width+40 && paths->Size.x<=360,"Sidebar width grip cannot adjust within its safe limits");
    ImGui::SetWindowSize(paths,ImVec2(edge_width,paths->Size.y));settle();
    // Exercise the real header gesture, then serialize with ImGui's normal
    // settings writer. No project data or real user settings are touched.
    const auto before=paths->Pos;
    io.AddMousePosEvent(before.x+45,before.y+7);frame();
    io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(before.x+105,before.y-23);frame();frame();
    io.AddMouseButtonEvent(0,false);frame();
    if(!(paths->Pos.x>before.x+40))std::fprintf(stderr,"Path drag %.0f %.0f -> %.0f %.0f, main %.0f %.0f size%.0f %.0f, hovered%s, active%u, popups%d, flags%d\n",before.x,before.y,paths->Pos.x,paths->Pos.y,main->Pos.x,main->Pos.y,main->Size.x,main->Size.y,ctx->HoveredWindow ? ctx->HoveredWindow->Name : "none",ctx->ActiveId,ctx->OpenPopupStack.Size,int(paths->Flags));
    require(paths->Pos.x>before.x+40,"Campath handle does not move the panel");
    ImGui::SetWindowSize(timeline,ImVec2(560,180));frame();frame();
    require(!timeline->ScrollbarX && timeline->ScrollMax.x<=1 && !timeline->ScrollbarY,
            "A custom narrow timeline clips or scrolls its transport");
    std::size_t ini_length=0;
    const std::string ini(ImGui::SaveIniSettingsToMemory(&ini_length));
    require(ini.find("[Window][##lite-campaths]")!=std::string::npos &&
                ini.find("[Window][##lite-timeline]")!=std::string::npos &&
                ini.find("ReducedMotion=1")!=std::string::npos,"Panel positions and motion preference do not save");
    const auto saved=paths->Pos;
    ImGui::SetWindowPos(paths,ImVec2(500,50));frame();
    ImGui::LoadIniSettingsFromMemory(ini.c_str(),ini.size());frame();frame();
    require(std::abs(paths->Pos.x-saved.x)<1,"Saved campath position does not restore");
    activate(main,"Layout");layout=ctx->OpenPopupStack.back().Window;
    activate(layout,"Classic arrangement");settle();
    require(paths->Pos.x<25 && main->Pos.x>paths->Pos.x+paths->Size.x &&
                std::abs(io.DisplaySize.x-main->Pos.x-main->Size.x-20)<1 &&
                std::abs(main->Pos.y-96)<1 && std::abs(paths->Pos.y-96)<1 &&
                timeline->Pos.y>main->Pos.y+main->Size.y,
            "Classic arrangement must reset the banner-safe upper-left/right anchors and lower timeline");
    for(unsigned cameras : {0u,1u,2u,64u}) {
        snapshot.camera_count=cameras;snapshot.selected_camera=0;settle();
        require(paths->DC.CursorMaxPos.y<=paths->InnerClipRect.Max.y+1,
                "The campath Delete/Clear All row is clipped");
    }
    snapshot.camera_count=2;settle();
    activate(main,"Layout");layout=ctx->OpenPopupStack.back().Window;
    if(paths->Flags&ImGuiWindowFlags_NoMove)activate(layout,"Arrange panels");
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    ImGui::SetWindowSize(paths,ImVec2(284,442));
    snapshot.camera_count=3;snapshot.selected_camera=2;settle();
    activate(main,"Layout");layout=ctx->OpenPopupStack.back().Window;
    activate(layout,"Reduce motion");
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    snapshot.camera_count=4;snapshot.selected_camera=3;
    for (int animation_frame=0;animation_frame<18;++animation_frame) {
        frame();
        const auto rgb=dolly::ui::SELECTED;
        const auto fill=IM_COL32((rgb>>16)&255,(rgb>>8)&255,rgb&255,255);
        for (const auto& vertex:paths->DrawList->VtxBuffer)
            if ((vertex.col&0xffffff)==(fill&0xffffff))
                require(vertex.pos.x<=paths->InnerClipRect.Max.x+1,
                        "A newly placed camera animates its row outside the sidebar bounds");
        require(paths->DC.CursorMaxPos.y<=paths->InnerClipRect.Max.y+1,
                "A newly placed camera clips the sidebar footer while animating");
    }
    settle();
    observed_actions.clear();
    // The filled tail must include Camera 03 after adding Camera 04.
    const int middle_index=2;
    const ImGuiID visible_row_seed=ImHashData(&middle_index,sizeof(middle_index),paths->IDStack.back());
    ctx->NavNextActivateId=ImHashStr("##camera",0,visible_row_seed);frame();frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SelectCamera &&
                observed_actions[0].value==2,"Campath tail does not backfill preceding cameras");
    require(paths->DC.CursorMaxPos.y<=paths->InnerClipRect.Max.y+1,"Four-camera tail clips its footer");
    snapshot.camera_count=2;snapshot.selected_camera=0;settle();
    snapshot.camera_count=32;snapshot.selected_camera=31;settle();
    snapshot.camera_count=33;snapshot.selected_camera=32;camera_first_stub=1;settle();
    observed_actions.clear();
    const int boundary_middle=31;
    const auto boundary_seed=ImHashData(&boundary_middle,sizeof(boundary_middle),paths->ID);
    ctx->NavNextActivateId=ImGui::GetIDWithSeed("##camera",nullptr,boundary_seed);frame();frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SelectCamera &&
                observed_actions[0].value==31,"Camera 33 is isolated from preceding cameras across a wire boundary");
    snapshot.camera_count=2;snapshot.selected_camera=0;camera_first_stub=0;settle();
    // Every tab submits all its cards in one continuous surface, including
    // compact and user-resized workspaces. Advanced controls expand inline.
    io.DisplaySize=ImVec2(640,480);settle();
    io.AddMousePosEvent(main->Pos.x+main->Size.x-main->WindowPadding.x-31,
                       main->Pos.y+main->WindowPadding.y+13);frame();frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);frame();
    io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);frame();frame();
    require(!ctx->OpenPopupStack.empty(),"Always-draggable header intercepts the compact Layout button");
    ImGui::ClosePopupToLevel(0,true);frame();
    activate(main,"Layout");layout=ctx->OpenPopupStack.back().Window;
    if(paths->Flags&ImGuiWindowFlags_NoMove)activate(layout,"Arrange panels");
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
    ImGui::SetWindowSize(main,ImVec2(520,328));settle();
    const char* tabs[]={"Camera###CAMERA","Follow###FOLLOW","Bone Picker###BONE PICKER","Look###LOOK","Export###EXPORT"};
    const unsigned counts[]={2,2,2,4,4};
    auto check_continuous_cards=[&](bool reach_end) {
        ImGuiWindow* content=nullptr;float bottom=0;
        for(auto* card:ctx->Windows)if(card->Active && card->ParentWindow &&
                std::strstr(card->ParentWindow->Name,"##editor-content")) {
            content=card->ParentWindow;bottom=std::max(bottom,card->Pos.y+card->Size.y);
            require(!card->ScrollbarY && card->ScrollMax.y<=1 &&
                        card->DC.CursorMaxPos.y<=card->Pos.y+card->Size.y+1,
                    "Continuous inline controls internally clip or scroll their card");
            for(auto* other:ctx->Windows)if(other!=card && other->Active && other->ParentWindow==content) {
                const bool overlap=card->Pos.x<other->Pos.x+other->Size.x-1 && other->Pos.x<card->Pos.x+card->Size.x-1 &&
                                   card->Pos.y<other->Pos.y+other->Size.y-1 && other->Pos.y<card->Pos.y+card->Size.y-1;
                require(!overlap,"Continuous inline control cards overlap");
            }
        }
        require(content && main->DC.CursorMaxPos.y<=main->InnerClipRect.Max.y+1,
                "Continuous inline controls lose their content or cover the fixed footer");
        if(bottom>content->InnerClipRect.Max.y+1) {
            require(content->ScrollMax.y>0 && !(content->Flags&ImGuiWindowFlags_NoScrollWithMouse) &&
                        bottom<=content->InnerClipRect.Max.y+content->ScrollMax.y-content->Scroll.y+1,
                    "Expanded inline cards extend beyond the usable parent scroll range");
            if(reach_end) {
                io.AddMousePosEvent(content->InnerClipRect.Min.x+12,content->InnerClipRect.Min.y+12);frame();frame();
                for(unsigned step=0;step<16;++step) {io.AddMouseWheelEvent(0,-10);frame();frame();}
                settle();bottom=0;float last_control=0;
                for(auto* card:ctx->Windows)if(card->Active && card->ParentWindow==content) {
                    bottom=std::max(bottom,card->Pos.y+card->Size.y);
                    last_control=std::max(last_control,card->DC.CursorMaxPos.y);
                }
                require(content->Scroll.y>=content->ScrollMax.y-1 && bottom<=content->InnerClipRect.Max.y+1 &&
                            last_control<=content->InnerClipRect.Max.y+1,
                        "Actual wheel input cannot reveal expanded inline card borders and final controls");
                ImGui::SetScrollY(content,0);settle();
            }
        } else require(content->ScrollMax.y<=1,"Fitting continuous inline controls acquired unnecessary scrolling");
    };
    for(unsigned tab=0;tab<5;++tab) {
        activate(main,tabs[tab]);settle();
        unsigned visible=0;
        for(auto* window:ctx->Windows) if(window->Active && window->ParentWindow &&
                std::strstr(window->ParentWindow->Name,"##editor-content")) {
            ++visible;
        }
        require(visible==counts[tab],"A tab still hides controls behind another section page");
        check_continuous_cards(true);
        const char* card_name=tab==2 ? "##attach-framing" : tab==3 ? "##dof-card" : tab==4 ? "##export-settings" : nullptr;
        if(card_name) {
            ImGuiWindow* advanced=nullptr;
            for(auto* window:ctx->Windows) if(window->Active && std::strstr(window->Name,card_name)) advanced=window;
            require(advanced,"Inline advanced controls are missing");
            const float font_before=advanced->FontRefSize;
            activate(advanced,tab==2 ? "Rotation & transition" : tab==3 ? "Individual focus overrides" : "Encoder & quality");
            if(tab==2) activate(advanced,"Model visibility");
            settle();
            require(std::abs(advanced->FontRefSize-font_before)<.1f,"Expanding inline controls changes the UI font size");
            check_continuous_cards(true);
        }
    }
    // Near a fitting boundary, font rounding/text wrapping must not alternate
    // the layout height and intermittently clip the footer or a card.
    for(const auto viewport : {ImVec2(600,900),ImVec2(800,600),ImVec2(1280,720)}) {
        io.DisplaySize=viewport;
        settle();
        activate(main,"Layout");layout=ctx->OpenPopupStack.back().Window;
        activate(layout,"Classic arrangement");
        activate(main,viewport.x<=800 ? "Look###LOOK" : "Export###EXPORT");
        settle();
        const char* header=viewport.x<=800 ? "Individual focus overrides" : "Encoder & quality";
        const char* card_name=viewport.x<=800 ? "##dof-card" : "##export-settings";
        ImGuiWindow* advanced=nullptr;
        for(auto* window:ctx->Windows) if(window->Active && std::strstr(window->Name,card_name)) advanced=window;
        if(advanced && advanced->StateStorage.GetInt(advanced->GetID(header),0)) activate(advanced,header);
        settle();
        check_continuous_cards(true);
        std::vector<std::pair<ImGuiWindow*,ImRect>> stable_cards;
        for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow &&
                std::strstr(window->ParentWindow->Name,"##editor-content"))
            stable_cards.emplace_back(window,ImRect(window->Pos,ImVec2(window->Pos.x+window->Size.x,window->Pos.y+window->Size.y)));
        for(int stable_frame=0;stable_frame<20;++stable_frame) {
            frame();
            check_continuous_cards(false);
            for(const auto& [window,bounds]:stable_cards)
                require(std::abs(window->Pos.y-bounds.Min.y)<1 && std::abs(window->Size.y-bounds.GetHeight())<1,
                        "Stable inline cards oscillate their row position or height");
        }
    }
    io.DisplaySize=ImVec2(1280,720);
    snapshot.camera_count=40;snapshot.selected_camera=33;camera_first_stub=32;settle();
    ImGui::SetWindowPos(paths,ImVec2(16,20));settle();
    observed_actions.clear();activate(paths,"##previous-cameras");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::CameraPage &&
                observed_actions[0].value==0,"Previous campath block does not request its destination");
    // Leave the outgoing partial block published for several frames before
    // acknowledging, just as the real desktop round trip does.
    for(int i=0;i<10;++i) frame();
    camera_first_stub=0;settle();observed_actions.clear();
    const int last_camera=31;
    const auto row_seed=ImHashData(&last_camera,sizeof(last_camera),paths->ID);
    ctx->NavNextActivateId=ImGui::GetIDWithSeed("##camera",nullptr,row_seed);frame();frame();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SelectCamera &&
                observed_actions[0].value==31,"A delayed previous-block acknowledgement loses the last camera page");
    camera_first_stub=32;settle();observed_actions.clear();
    // The shorter custom panel has more local pages within a wire block.
    // Reach the block boundary before testing a stale request rejection.
    for(int i=0;i<8 && observed_actions.empty();++i) activate(paths,"##previous-cameras");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::CameraPage &&
                observed_actions[0].revision==7,"Stale-page fixture did not reach its block boundary");
    camera_revision_stub=8;frame();frame();
    // A prior Arrive/Bank/Undo can reject the queued page's old revision.
    // The current block stays put; its navigation must become available again.
    observed_actions.clear();activate(paths,"##previous-cameras");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::CameraPage &&
                observed_actions[0].revision==8,"A rejected stale page permanently disables campath navigation");
    camera_revision_stub=7;
    camera_first_stub=0;
    std::puts("UI rework: individual kill ticks, movable/saved panels, corrected Classic preset, continuous tabs and unclipped campath footer passed.");
    ImGui::DestroyContext(ctx); ImGui::SetCurrentContext(previous); snapshot=original;
    require(DeleteFileA(section_file),"Cannot remove isolated layout preference test");
    const std::string section_backup=std::string(section_file)+".bak";
    DeleteFileA(section_backup.c_str());
    require(SetEnvironmentVariableA("DOLLY_UI_LAYOUT_FILE","none"),"Cannot restore disabled fixture layout storage");
}
static void default_panel_placement_regressions(float font_scale) {
    auto* previous=ImGui::GetCurrentContext();const auto original=snapshot;
    for(const auto viewport:{ImVec2(1280,720),ImVec2(2560,1440)}) {
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=viewport;io.DeltaTime=1.f/60;
        io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
        dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(font_scale);
        ImGui::GetStyle().ScaleAllSizes(font_scale);io.Fonts->Build();
        snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
        snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
        snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=10;snapshot.selected_camera=0;snapshot.duration=27;
        auto frame=[&] {
            ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_campaths(snapshot);
            panel.draw_lite_timeline(snapshot);ImGui::Render();panel.flush_layout();
        };
        auto settle=[&]{for(int i=0;i<30;++i)frame();};settle();
        const float top=std::min(96*font_scale,viewport.y*.15f);
        auto* main=ImGui::FindWindowByName("Deadlock Dolly");
        auto activate=[&](ImGuiWindow* window,const char* label) {ctx->NavNextActivateId=window->GetID(label);settle();};
        auto check_main=[&] {
            require(main && main->Active,"Fresh/reset layout lost its main panel");
            require(std::abs(main->Pos.y-top)<1 && std::abs(viewport.x-main->Pos.x-main->Size.x-20*font_scale)<1,
                    "Fresh/reset main panel is not anchored below the banner at the upper right");
            const auto* timeline=ImGui::FindWindowByName("##lite-timeline");
            require(main->Pos.x>=0 && main->Pos.y+main->Size.y<=timeline->Pos.y-12*font_scale+1 &&
                        main->DC.CursorMaxPos.y<=main->InnerClipRect.Max.y+1,
                    "Banner-safe main panel clips its footer or covers the timeline");
        };
        auto check_paths=[&] {
            auto* paths=ImGui::FindWindowByName("##lite-campaths");
            require(paths && paths->Active,"Fresh/reset layout lost its camera panel");
            require(std::abs(paths->Pos.x-16*font_scale)<1 && std::abs(paths->Pos.y-top)<1,
                    "Fresh/reset camera panel is not anchored below the banner at the upper left");
            const auto* timeline=ImGui::FindWindowByName("##lite-timeline");
            require(paths->Pos.x+paths->Size.x<=viewport.x+1 &&
                        paths->Pos.y+paths->Size.y<=timeline->Pos.y-12*font_scale+1 &&
                        paths->DC.CursorMaxPos.y<=paths->InnerClipRect.Max.y+1,
                    "Banner-safe camera panel clips its actions or covers the timeline");
            require(ctx->Tables.GetByKey(paths->GetID("##lite-campath-details")),
                    "The camera body scroller changed the existing table identity");
            ImGuiWindow* body=nullptr;
            for(auto* candidate:ctx->Windows) if(candidate->Active && candidate->ParentWindow==paths &&
                    std::strstr(candidate->Name,"##campaths-content"))body=candidate;
            if(body) {
                require(body->ScrollMax.y>0 && !(body->Flags&ImGuiWindowFlags_NoScrollWithMouse),
                        "A shortened camera panel provides no usable body scrolling");
                ImGui::SetScrollY(body,body->ScrollMax.y);settle();
                observed_actions.clear();activate(paths,"Save Path");
                require(observed_actions.size()==1 && observed_actions.back().action==dolly::EditorAction::SaveShot &&
                            ctx->NavWindow==body && ctx->NavWindow->NavRectRel[ImGuiNavLayer_Main].Max.y+body->Pos.y<=body->InnerClipRect.Max.y+1,
                        "Scrolling cannot reach Save Path with its original control identity");
                activate(paths,"Clear All");
                require(ctx->OpenPopupStack.Size && ctx->OpenPopupStack.back().Window,
                        "Scrolling cannot reach the last camera action with its original control identity");
                activate(ctx->OpenPopupStack.back().Window,"Cancel");
                require(paths->DC.CursorMaxPos.y<=paths->InnerClipRect.Max.y+1,
                        "Scrolling camera controls obscures the fixed header and Tools row");
                ImGui::SetScrollY(body,0);settle();
            }
        };
        check_main();
        struct MainPage { unsigned index;const char* label;const char* card; };
        for(const auto& page:{MainPage{0,"Camera###CAMERA","##cameras-card"},MainPage{1,"Follow###FOLLOW","##follow-card"},
                             MainPage{2,"Bone Picker###BONE PICKER","##attach-card"},MainPage{3,"Look###LOOK","##dof-card"},
                             MainPage{4,"Export###EXPORT","##export-settings"}}) {
            activate(main,page.label);
            const auto tab_rect=panel.main_tab_rect(page.index);
            require(tab_rect.z>tab_rect.x && tab_rect.w>tab_rect.y && tab_rect.z<=main->InnerClipRect.Max.x+1 &&
                        tab_rect.w<=main->InnerClipRect.Max.y+1,"A main tab's actual pointer bounds leave its visible header");
            activate(main,std::strcmp(page.label,"Camera###CAMERA") ? "Camera###CAMERA" : "Export###EXPORT");
            io.AddMousePosEvent((tab_rect.x+tab_rect.z)*.5f,(tab_rect.y+tab_rect.w)*.5f);
            frame();frame();io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);settle();
            bool selected=false;for(auto* card:ctx->Windows)if(card->Active && std::strstr(card->Name,page.card))selected=true;
            if(!selected)std::fprintf(stderr,"Pointer tab %s %.0fx%.0f scale %.2f rect %.1f %.1f..%.1f %.1f mouse %.1f %.1f hovered %u active %u nav %u window %s\n",
                page.label,viewport.x,viewport.y,font_scale,tab_rect.x,tab_rect.y,tab_rect.z,tab_rect.w,io.MousePos.x,io.MousePos.y,
                ctx->HoveredId,ctx->ActiveId,ctx->NavId,ctx->HoveredWindow ? ctx->HoveredWindow->Name : "none");
            require(selected,"A main tab is blocked from real mouse clicks in the banner-safe header");
            ImGuiWindow* content=nullptr;unsigned cards=0;
            for(auto* card:ctx->Windows)if(card->Active && card->ParentWindow &&
                    std::strstr(card->ParentWindow->Name,"##editor-content")) {
                content=card->ParentWindow;++cards;
                require(card->FontRefSize>=14*font_scale && card->DC.CursorMaxPos.y<=card->Pos.y+card->Size.y+1,
                        "Banner-safe default layout shrinks or internally clips an existing main control");
            }
            require(cards && content,"Banner-safe default layout lost a main tab's controls");
            float bottom=0;for(auto* card:ctx->Windows)if(card->Active && card->ParentWindow==content)
                bottom=std::max(bottom,card->Pos.y+card->Size.y);
            if(bottom>content->InnerClipRect.Max.y+1) {
                require(content->ScrollMax.y>0 && !(content->Flags&ImGuiWindowFlags_NoScrollWithMouse),
                        "Banner-safe default layout has unreachable main controls");
                ImGui::SetScrollY(content,content->ScrollMax.y);settle();bottom=0;
                for(auto* card:ctx->Windows)if(card->Active && card->ParentWindow==content)
                    bottom=std::max(bottom,card->Pos.y+card->Size.y);
                require(bottom<=content->InnerClipRect.Max.y+1,"Default main scrolling cannot reveal the last control card");
                ImGui::SetScrollY(content,0);settle();
            }
        }
        activate(main,"Camera###CAMERA");
        const bool compact=viewport.x<900*font_scale || viewport.y<650*font_scale;
        if(compact) {activate(main,"Paths");check_paths();activate(ImGui::FindWindowByName("##lite-campaths"),"Tools");}
        else check_paths();
        // A reset is tested after loading and moving a custom arrangement,
        // rather than merely checking the initial coordinates a second time.
        const auto before_drag=main->Pos;
        io.AddMousePosEvent(main->Pos.x+30*font_scale,main->Pos.y+7*font_scale);frame();frame();
        io.AddMouseButtonEvent(0,true);frame();io.AddMousePosEvent(io.MousePos.x-20*font_scale,io.MousePos.y+10*font_scale);frame();frame();
        io.AddMouseButtonEvent(0,false);settle();
        require(std::abs(main->Pos.x-std::max(12*font_scale,before_drag.x-20*font_scale))<1 &&
                    std::abs(main->Pos.y-(before_drag.y+10*font_scale))<1,
                "The compact header title cannot drag its main panel independently of inline tabs");
        activate(ImGui::FindWindowByName("##lite-timeline"),"Curves###lite-curves-mode");
        require(panel.timeline_mode()==2,"Reset fixture did not leave the Replay tab first");
        activate(main,"Layout");
        require(ctx->OpenPopupStack.Size>0 && ctx->OpenPopupStack.back().Window,"Reset layout menu is unavailable");
        activate(ctx->OpenPopupStack.back().Window,"Classic arrangement");check_main();
        require(panel.timeline_mode()==0,"Reset arrangement did not return to Replay");
        if(compact) {activate(main,"Paths");check_paths();activate(ImGui::FindWindowByName("##lite-campaths"),"Tools");}
        else check_paths();
        if(font_scale==1.5f && viewport.y==720) {
            ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nEditorCustom=1\n");
            ImGui::SetWindowSize(main,ImVec2(640*font_scale,main->Size.y));settle();
            require(std::abs(main->Size.x-640*font_scale)<1,"Custom narrow main fixture did not apply its saved width");
            for(const auto& page:{MainPage{0,"Camera###CAMERA","##cameras-card"},MainPage{4,"Export###EXPORT","##export-settings"}}) {
                activate(main,page.label);const auto tab_rect=panel.main_tab_rect(page.index);
                require(tab_rect.z<=main->InnerClipRect.Max.x+1,
                        "A custom 640-logical-width main header overflows at 150 percent");
                activate(main,std::strcmp(page.label,"Camera###CAMERA") ? "Camera###CAMERA" : "Export###EXPORT");
                io.AddMousePosEvent((tab_rect.x+tab_rect.z)*.5f,(tab_rect.y+tab_rect.w)*.5f);
                frame();frame();io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);settle();
                bool selected=false;for(auto* card:ctx->Windows)if(card->Active && std::strstr(card->Name,page.card))selected=true;
                require(selected && main->DC.CursorMaxPos.x<=main->InnerClipRect.Max.x+1 &&
                            main->DC.CursorMaxPos.y<=main->InnerClipRect.Max.y+1,
                        "A custom narrow main loses pointer tab access or clips its fixed controls");
            }
        }
        require(io.IniFilename==nullptr,"Disabled fixture layout storage opened ImGui disk writes");
        ImGui::DestroyContext(ctx);
    }
    ImGui::SetCurrentContext(previous);snapshot=original;
    std::puts("Default/reset panels: upper-left/right banner clearance, compact access and footer bounds passed at 720p/1440p.");
}
static void card_row_alignment_regressions(float font_scale) {
    auto* previous=ImGui::GetCurrentContext();const auto original=snapshot;
    unsigned aligned_pairs=0,single_column_pages=0;
    for(const auto viewport:{ImVec2(640,480),ImVec2(1280,720),ImVec2(2560,1440)}) {
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=viewport;io.DeltaTime=1.f/60;
        io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
        dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(font_scale);
        ImGui::GetStyle().ScaleAllSizes(font_scale);io.Fonts->Build();
        ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nTimelineMode=1\n");
        require(panel.timeline_mode()==0,"A legacy Campaths tab overrides the new-session Replay default");
        ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nTimelineMode=2\n");
        require(panel.timeline_mode()==0,"A legacy Curves tab overrides the new-session Replay default");
        snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
        snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
        snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=2;snapshot.selected_camera=0;snapshot.duration=3;
        auto frame=[&] {ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_campaths(snapshot);
                       panel.draw_lite_timeline(snapshot);ImGui::Render();panel.flush_layout();};
        auto settle=[&](unsigned frames=30) {for(unsigned i=0;i<frames;++i)frame();};settle();
        auto* main=ImGui::FindWindowByName("Deadlock Dolly");
        auto activate=[&](ImGuiWindow* window,const char* label) {ctx->NavNextActivateId=window->GetID(label);settle();};
        auto cards=[&] {
            std::vector<ImGuiWindow*> result;
            for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow &&
                    std::strstr(window->ParentWindow->Name,"##editor-content"))result.push_back(window);
            return result;
        };
        auto check_rows=[&] {
            auto visible=cards();require(!visible.empty(),"Row alignment lost all main editor cards");
            std::sort(visible.begin(),visible.end(),[](auto* a,auto* b) {
                return std::abs(a->Pos.x-b->Pos.x)<1 ? a->Pos.y<b->Pos.y : a->Pos.x<b->Pos.x;
            });
            ImGuiWindow* reference[4]{},*previous_card=nullptr;unsigned columns=0,row=0;float last_x=-FLT_MAX;
            for(auto* card:visible) {
                if(std::abs(card->Pos.x-last_x)>1) {last_x=card->Pos.x;row=0;previous_card=nullptr;++columns;}
                require(card->ScrollMax.y<=1 && !card->ScrollbarY &&
                            card->DC.CursorMaxPos.y<=card->Pos.y+card->Size.y+1,
                        "An aligned card clips or internally scrolls its controls");
                if(reference[row]) {
                    auto* first=reference[row];
                    if(!(std::abs(first->Pos.y-card->Pos.y)<1.1f &&
                         std::abs(first->Pos.y+first->Size.y-card->Pos.y-card->Size.y)<1.1f)) {
                        std::fprintf(stderr,"Card row %.0fx%.0f scale %.2f: %s y %.2f h %.2f, %s y %.2f h %.2f\n",
                            viewport.x,viewport.y,font_scale,first->Name,first->Pos.y,first->Size.y,card->Name,card->Pos.y,card->Size.y);
                        for(auto* detail:{first,card})std::fprintf(stderr,
                            "  %s: SizeFull %.2f %.2f content %.2f ideal %.2f start %.2f cursor %.2f idealMax %.2f padding %.2f %.2f deco %.2f %.2f scrollbar %.2f autoFitY %d flags %x child %x\n",
                            detail->Name,detail->SizeFull.x,detail->SizeFull.y,detail->ContentSize.y,detail->ContentSizeIdeal.y,
                            detail->DC.CursorStartPos.y,detail->DC.CursorMaxPos.y,detail->DC.IdealMaxPos.y,
                            detail->WindowPadding.x,detail->WindowPadding.y,detail->DecoOuterSizeY1,detail->DecoOuterSizeY2,
                            detail->ScrollbarSizes.y,int(detail->AutoFitFramesY),unsigned(detail->Flags),unsigned(detail->ChildFlags));
                        std::fprintf(stderr,"  currentStyle padding %.2f %.2f frame %.2f %.2f min %.2f %.2f\n",
                            ImGui::GetStyle().WindowPadding.x,ImGui::GetStyle().WindowPadding.y,
                            ImGui::GetStyle().FramePadding.x,ImGui::GetStyle().FramePadding.y,
                            ImGui::GetStyle().WindowMinSize.x,ImGui::GetStyle().WindowMinSize.y);
                        for(unsigned i=0;i<4;++i) {
                            const auto h=panel.main_card_heights(i);
                            std::fprintf(stderr,"  allocation %u: natural-logical %.3f draw %.3f manual-logical %.3f extra %.3f\n",i,h.x,h.y,h.z,h.w);
                        }
                    }
                    require(std::abs(first->Pos.y-card->Pos.y)<1.1f &&
                                std::abs(first->Pos.y+first->Size.y-card->Pos.y-card->Size.y)<1.1f,
                            "Neighboring main cards have unequal row top or bottom edges");
                    ++aligned_pairs;
                } else reference[row]=card;
                if(previous_card) {
                    require(card->Pos.y>=previous_card->Pos.y+previous_card->Size.y+2,
                            "Aligned card rows overlap or lose their vertical gap");
                }
                previous_card=card;++row;
            }
            if(columns==1)++single_column_pages;
            if(main->DC.CursorMaxPos.y>main->InnerClipRect.Max.y+1)
                std::fprintf(stderr,"Aligned main footer %.0fx%.0f scale %.2f page %s: pos %.2f %.2f size %.2f %.2f cursor %.2f %.2f clip %.2f %.2f..%.2f %.2f font %.2f\n",
                    viewport.x,viewport.y,font_scale,visible.front()->Name,main->Pos.x,main->Pos.y,main->Size.x,main->Size.y,
                    main->DC.CursorMaxPos.x,main->DC.CursorMaxPos.y,main->InnerClipRect.Min.x,main->InnerClipRect.Min.y,
                    main->InnerClipRect.Max.x,main->InnerClipRect.Max.y,main->FontRefSize);
            require(main->DC.CursorMaxPos.y<=main->InnerClipRect.Max.y+1,
                    "Row alignment covers the fixed main footer");
            return visible;
        };
        auto reachable=[&] {
            auto visible=cards();auto* content=visible.front()->ParentWindow;
            float bottom=0;for(auto* card:visible)bottom=std::max(bottom,card->Pos.y+card->Size.y);
            if(bottom>content->InnerClipRect.Max.y+1) {
                require(content->ScrollMax.y>0 && !(content->Flags&ImGuiWindowFlags_NoScrollWithMouse),
                        "An aligned row below the viewport has no usable parent scrolling");
                io.AddMousePosEvent(visible.front()->Pos.x+10*font_scale,
                    std::clamp(visible.front()->Pos.y+10*font_scale,content->InnerClipRect.Min.y+4,content->InnerClipRect.Max.y-4));
                frame();frame();io.AddMouseWheelEvent(0,-1);settle();
                require(content->Scroll.y>0,"Wheel input over an aligned card cannot scroll its editor content");
                // Actual wheel input follows the current range as rounded
                // measurements settle. SetScrollY(previous_max) can stop two
                // pixels short at150%; very short viewports also have a thumb
                // as large as their track, so they must remain wheel-usable.
                for(unsigned step=0;step<16;++step) {io.AddMouseWheelEvent(0,-10);frame();frame();}
                settle();bottom=0;
                require(content->Scroll.y>=content->ScrollMax.y-1,
                        "Wheel scrolling an aligned editor cannot reach its current end");
                for(auto* card:cards())bottom=std::max(bottom,card->Pos.y+card->Size.y);
                if(bottom>content->InnerClipRect.Max.y+1) {
                    std::fprintf(stderr,"Aligned reachability %.0fx%.0f scale %.2f page %s: bottom %.2f clip %.2f..%.2f scroll %.2f max %.2f size %.2f content %.2f cursor %.2f\n",
                        viewport.x,viewport.y,font_scale,visible.front()->Name,bottom,content->InnerClipRect.Min.y,
                        content->InnerClipRect.Max.y,content->Scroll.y,content->ScrollMax.y,content->Size.y,content->ContentSize.y,
                        content->DC.CursorMaxPos.y);
                    for(auto* card:cards())std::fprintf(stderr,"  %s y %.2f h %.2f natural-bottom %.2f\n",
                        card->Name,card->Pos.y,card->Size.y,card->DC.CursorMaxPos.y);
                }
                require(bottom<=content->InnerClipRect.Max.y+1,
                        "Scrolling cannot reveal the last aligned card row");
                ImGui::SetScrollY(content,0);settle();
            }
        };
        const char* pages[]{"Camera###CAMERA","Follow###FOLLOW","Bone Picker###BONE PICKER","Look###LOOK","Export###EXPORT"};
        const unsigned counts[]{2,2,2,4,4};
        for(unsigned page=0;page<5;++page) {
            activate(main,pages[page]);auto visible=check_rows();
            require(visible.size()==counts[page],"Aligned editor page lost an existing control card");
            std::vector<ImRect> measured;
            for(auto* card:visible)measured.emplace_back(card->Pos,ImVec2(card->Pos.x+card->Size.x,card->Pos.y+card->Size.y));
            settle(90);auto stable=check_rows();
            for(unsigned i=0;i<visible.size();++i) {
                const auto* card=stable[i];
                require(std::abs(card->Pos.y-measured[i].Min.y)<1.1f &&
                            std::abs(card->Size.y-measured[i].GetHeight())<1.1f,
                        "Derived card row padding feeds back into natural height or oscillates");
            }
            reachable();
        }
        if(viewport.y==720 && font_scale==1) {
            const auto dof_before=snapshot.dof;const bool dof_available_before=snapshot.dof_available;
            snapshot.dof_available=true;snapshot.dof={1,1,-100,0,180,1490,-100,0,180,2000,.5};
            activate(main,pages[3]);
            auto find_card=[&](const char* name) -> ImGuiWindow* {
                for(auto* card:cards())if(std::strstr(card->Name,name))return card;
                return nullptr;
            };
            auto* dof=find_card("##dof-card");
            require(dof,"Scrolled Arrange fixture has no first-row card");
            if(!dof->StateStorage.GetInt(dof->GetID("Individual focus overrides"),0))
                activate(dof,"Individual focus overrides");
            require(dof->StateStorage.GetInt(dof->GetID("Individual focus overrides"),0),
                    "Scrolled Arrange fixture did not expand its real controls");
            activate(main,"Layout");
            require(ctx->OpenPopupStack.Size>0,"Scrolled Arrange fixture cannot open Layout");
            activate(ctx->OpenPopupStack.back().Window,"Arrange panels");
            ImGui::ClosePopupToLevel(0,true);settle();
            auto* source=find_card("##confetti-card");
            require(source,"Scrolled Arrange fixture has no last-row card");
            auto* content=source->ParentWindow;
            require(content->ScrollMax.y>0,"Scrolled Arrange fixture did not create overflowing aligned rows");
            ImGui::SetScrollY(content,content->ScrollMax.y);settle();
            const ImVec2 from(source->Pos.x+source->Size.x-9,source->Pos.y+source->WindowPadding.y+7);
            const ImVec2 to(dof->Pos.x+dof->WindowPadding.x+18,from.y);
            require(from.y>content->InnerClipRect.Min.y && from.y<content->InnerClipRect.Max.y &&
                        to.y>dof->Pos.y+dof->Size.y && to.y<content->InnerClipRect.Max.y,
                    "Scrolled Arrange fixture lacks a visible source header or empty destination");
            observed_actions.clear();
            io.AddMousePosEvent(from.x,from.y);frame();frame();io.AddMouseButtonEvent(0,true);frame();
            io.AddMousePosEvent(from.x+20,from.y);frame();frame();
            require(ctx->DragDropActive,"A scrolled card header cannot start an actual section drag");
            io.AddMousePosEvent(to.x,to.y);frame();frame();io.AddMouseButtonEvent(0,false);settle();
            if(!(std::abs(source->Pos.x-dof->Pos.x)<1 && source->Pos.y>=dof->Pos.y+dof->Size.y))
                std::fprintf(stderr,"Scrolled Arrange: source %.1f %.1f target %.1f %.1f scroll %.1f max %.1f drop %.1f %.1f\n",
                    source->Pos.x,source->Pos.y,dof->Pos.x,dof->Pos.y,content->Scroll.y,content->ScrollMax.y,to.x,to.y);
            require(std::abs(source->Pos.x-dof->Pos.x)<1 && source->Pos.y>=dof->Pos.y+dof->Size.y,
                    "A scrolled card cannot move into visible empty column space");
            require(observed_actions.empty(),"Rearranging a scrolled card emits a backend camera action");
            check_rows();reachable();
            snapshot.dof=dof_before;snapshot.dof_available=dof_available_before;
        }
        if(viewport.y==1440) {
            activate(main,pages[0]);
            ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nSections0=1,0,2,3\nColumns0=1,0,-1,-1\nWidths0=1.2,0.8,1\nHeights0=330,0,0,0\n");settle();
            const auto resized=check_rows();
            require(resized.size()==2 && resized[0]->Size.x>resized[1]->Size.x &&
                        std::strstr(resized[0]->Name,"##flight-card") && resized[1]->Size.y>=330*font_scale-1,
                    "Row alignment replaced custom card order, width or explicit minimum height");
            const float tall=resized[0]->Size.y,font=resized[0]->FontRefSize;
            const std::string saved(ImGui::SaveIniSettingsToMemory());
            require(saved.find("Heights0=330.00,0.00,0.00,0.00")!=std::string::npos &&
                        saved.find("Widths0=1.2000,0.8000,1.0000")!=std::string::npos,
                    "Derived row padding was serialized as a manual height or changed column weights");
            ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nHeights0=0,0,0,0\n");settle();
            const auto natural=check_rows();
            require(natural[0]->Size.y<tall-20*font_scale && natural[0]->FontRefSize==font,
                    "Removing a manual minimum cannot recover natural row height without changing fonts");
            reachable();
        }
        ImGui::DestroyContext(ctx);
    }
    require(aligned_pairs>0,"Card alignment fixture never exercised neighboring cards");
    if(font_scale>1)require(single_column_pages>0,"Card alignment fixture never exercised a single-column layout");
    ImGui::SetCurrentContext(previous);snapshot=original;
    std::puts("Main cards: all five pages align visual rows, stay stable, preserve manual order/width/minimums, and remain scroll-reachable.");
}
[[noreturn]] static void layout_crash_child() {
    wchar_t file_path[32768]{};
    const auto length=GetEnvironmentVariableW(L"DOLLY_UI_LAYOUT_FILE",file_path,DWORD(std::size(file_path)));
    require(length && length<std::size(file_path),"Crash fixture has no isolated layout path");
    auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
    auto& io=ImGui::GetIO();io.DisplaySize=ImVec2(1920,1080);io.DeltaTime=1.f/60;
    dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(1);io.Fonts->Build();
    snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=snapshot.manual_active=true;
    snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=2;snapshot.selected_camera=0;snapshot.duration=3;
    auto frame=[&] {ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_campaths(snapshot);
                    panel.draw_lite_timeline(snapshot);ImGui::Render();panel.flush_layout();};
    auto settle=[&] {for(int i=0;i<8;++i)frame();};settle();
    auto drag=[&](ImGuiWindow* window,float dx,float dy) {
        io.AddMousePosEvent(window->Pos.x+window->Size.x*.5f,window->Pos.y+7);frame();frame();
        io.AddMouseButtonEvent(0,true);frame();io.AddMousePosEvent(io.MousePos.x+dx,io.MousePos.y+dy);frame();frame();
        io.AddMouseButtonEvent(0,false);settle();
    };
    drag(ImGui::FindWindowByName("Deadlock Dolly"),-64,-40);
    drag(ImGui::FindWindowByName("##lite-campaths"),64,-40);
    std::string payload;
    require(dolly::load_ui_layout(file_path,payload) && payload.find("EditorCustom=1")!=std::string::npos &&
                payload.find("\nCustom=1\n")!=std::string::npos,
            "Completed header gestures were not saved before the crash fixture exit");
    char expected[512]{};std::string values;
    for(const char* name:{"Deadlock Dolly","##lite-campaths","##lite-timeline"}) {
        const auto* window=ImGui::FindWindowByName(name);
        std::snprintf(expected,sizeof(expected),"%.3f %.3f %.3f %.3f\n",window->Pos.x,window->Pos.y,window->Size.x,window->Size.y);
        values+=expected;
    }
    write_layout_fixture(std::wstring(file_path)+L".expected",values);
    // Deliberately skip context teardown and the normal overlay shutdown flush.
    ExitProcess(0);
}
static void layout_crash_restore_regressions() {
    const auto directory=layout_test_directory(),path=directory+L"\\crash.ini";
    require(SetEnvironmentVariableW(L"DOLLY_UI_LAYOUT_FILE",path.c_str()),"Cannot isolate crash-save fixture storage");
    wchar_t executable[32768]{};
    require(GetModuleFileNameW(nullptr,executable,DWORD(std::size(executable)))>0,"Crash fixture executable path unavailable");
    std::wstring command=L"\""+std::wstring(executable)+L"\" --layout-crash-fixture";
    STARTUPINFOW startup{};startup.cb=sizeof(startup);startup.dwFlags=STARTF_USESHOWWINDOW;startup.wShowWindow=SW_HIDE;
    PROCESS_INFORMATION child{};
    require(CreateProcessW(executable,command.data(),nullptr,nullptr,TRUE,CREATE_NO_WINDOW,nullptr,nullptr,&startup,&child),
            "Cannot start owned no-window crash-save fixture");
    const auto wait=WaitForSingleObject(child.hProcess,10000);
    if(wait!=WAIT_OBJECT_0) {TerminateProcess(child.hProcess,3);WaitForSingleObject(child.hProcess,1000);}
    DWORD code=1;GetExitCodeProcess(child.hProcess,&code);CloseHandle(child.hThread);CloseHandle(child.hProcess);
    require(wait==WAIT_OBJECT_0 && code==0,"Owned crash-save fixture did not complete its bounded save");
    float expected[3][4]{};const auto expected_text=read_layout_fixture(path+L".expected");
    require(std::sscanf(expected_text.c_str(),"%f %f %f %f %f %f %f %f %f %f %f %f",
                &expected[0][0],&expected[0][1],&expected[0][2],&expected[0][3],
                &expected[1][0],&expected[1][1],&expected[1][2],&expected[1][3],
                &expected[2][0],&expected[2][1],&expected[2][2],&expected[2][3])==12,
            "Crash fixture did not retain its observed window geometry");
    std::string saved;
    require(dolly::load_ui_layout(path,saved),"Completed gesture layout was lost after abrupt process exit");
    auto* previous=ImGui::GetCurrentContext();const auto original=snapshot;
    for(unsigned source=0;source<3;++source) {
        if(source==1) {
            require(dolly::save_ui_layout(path,saved),"Cannot prepare a validated recovery copy");
            write_layout_fixture(path,"interrupted primary write");bool recovered=false;std::string recovered_payload;
            require(dolly::load_ui_layout(path,recovered_payload,&recovered) && recovered && recovered_payload==saved,
                    "Crash fixture backup is not a complete copy of the saved custom layout");
        }
        if(source==2) {
            // A published last-used Curves tab and meaningful custom windows
            // must coexist: only session navigation resets, never geometry.
            auto legacy=saved;
            const auto mode=legacy.find("TimelineMode=0");
            require(mode!=std::string::npos,"Custom geometry fixture lacks its timeline mode");
            legacy.replace(mode,std::strlen("TimelineMode=0"),"TimelineMode=2");
            write_layout_fixture(path,legacy); // Published legacy plain-INI format.
        }
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=ImVec2(1920,1080);io.DeltaTime=1.f/60;
        dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(1);io.Fonts->Build();
        snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=snapshot.manual_active=true;
        snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
        snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=2;snapshot.selected_camera=0;snapshot.duration=3;
        auto frame=[&] {ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_campaths(snapshot);
                        panel.draw_lite_timeline(snapshot);ImGui::Render();panel.flush_layout();};
        for(unsigned i=0;i<30;++i)frame();
        require(panel.timeline_mode()==0 && panel.timeline_x1()>panel.timeline_x0(),
                "Restoring a legacy last-used Curves tab overrode Replay beside saved custom geometry");
        unsigned index=0;
        for(const char* name:{"Deadlock Dolly","##lite-campaths","##lite-timeline"}) {
            const auto* window=ImGui::FindWindowByName(name);
            require(window && window->Active && std::abs(window->Pos.x-expected[index][0])<1 &&
                        std::abs(window->Pos.y-expected[index][1])<1 && std::abs(window->Size.x-expected[index][2])<1 &&
                        std::abs(window->Size.y-expected[index][3])<1,
                    "Fresh UI instance replaced saved custom geometry after crash/recovery/legacy load");
            ++index;
        }
        require(io.IniFilename==nullptr,"Managed restored layout enabled truncate-in-place ImGui saves");
        if(source==2) {
            const auto before=read_layout_fixture(path);
            HANDLE locked=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
            require(locked!=INVALID_HANDLE_VALUE,"Cannot lock the managed primary during a real gesture save");
            auto* main=ImGui::FindWindowByName("Deadlock Dolly");
            const auto old_x=main->Pos.x;
            io.AddMousePosEvent(main->Pos.x+main->Size.x*.5f,main->Pos.y+7);frame();frame();
            io.AddMouseButtonEvent(0,true);frame();io.AddMousePosEvent(io.MousePos.x-24,io.MousePos.y);frame();frame();
            io.AddMouseButtonEvent(0,false);frame();frame();
            const auto after_failed_save=read_layout_fixture(path);CloseHandle(locked);
            require(after_failed_save==before,"A failed real gesture save damaged the previous custom layout");
            require(std::abs(main->Pos.x-(old_x-24))<1,"The failed-save fixture did not complete its real header gesture");
            require(panel.flush_layout(true),"A failed layout save was not retained for a later retry");
            char expected_position[128]{};
            std::snprintf(expected_position,sizeof(expected_position),"[Window][Deadlock Dolly]\nPos=%d,%d\n",int(main->Pos.x),int(main->Pos.y));
            std::string retried;require(dolly::load_ui_layout(path,retried) && retried.find(expected_position)!=std::string::npos,
                    "Retry did not persist the completed gesture after the file lock was released");
        }
        ImGui::DestroyContext(ctx);
    }
    ImGui::SetCurrentContext(previous);snapshot=original;
    require(SetEnvironmentVariableA("DOLLY_UI_LAYOUT_FILE","none"),"Cannot restore disabled test layout persistence");
    require(DeleteFileW(path.c_str()) && DeleteFileW((path+L".bak").c_str()) &&
                DeleteFileW((path+L".expected").c_str()) && RemoveDirectoryW(directory.c_str()),
            "Cannot clean the isolated crash/recovery fixtures");
    std::puts("Managed layout UI: saved gestures survive abrupt exit, backup recovery, legacy load and a failed-save retry.");
}
static void layout_view_restore_regressions(SyntheticViewer& viewer) {
    const auto directory=layout_test_directory(),path=directory+L"\\views.ini";
    require(SetEnvironmentVariableW(L"DOLLY_UI_LAYOUT_FILE",path.c_str()),"Cannot isolate saved view fixture");
    auto* previous=ImGui::GetCurrentContext();const auto original=snapshot;
    const auto old_revision=camera_revision_stub;
    viewer.publish(curve_packet());viewer.tick();camera_revision_stub=7;
    for(unsigned restored=0;restored<2;++restored) {
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=restored ? ImVec2(800,600) : ImVec2(1920,1080);io.DeltaTime=1.f/60;
        io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
        dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(1);io.Fonts->Build();
        snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
        snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
        snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=10;snapshot.selected_camera=0;snapshot.duration=27;
        auto frame=[&]{ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_campaths(snapshot);
                      panel.draw_lite_timeline(snapshot);ImGui::Render();panel.flush_layout();};
        auto settle=[&]{for(int i=0;i<30;++i)frame();};settle();
        auto activate=[&](ImGuiWindow* window,const char* label){
            require(window && window->Active,"Saved-view fixture has no active control owner");
            const bool lane=std::strstr(label,"curve-lane-")!=nullptr;
            if(lane) { ctx->NavNextActivateId=window->GetID("Graphs");settle(); require(ctx->OpenPopupStack.Size>0,"Saved-view Graphs menu did not open"); }
            ctx->NavNextActivateId=window->GetID(label);settle();
            if(lane) { ImGui::ClosePopupToLevel(0,true);settle(); }
        };
        auto* main=ImGui::FindWindowByName("Deadlock Dolly");auto* timeline=ImGui::FindWindowByName("##lite-timeline");
        if(!restored) {
            activate(main,"Export###EXPORT");activate(timeline,"Curves###lite-curves-mode");
            activate(timeline,"Z###curve-axis-2");activate(timeline,"Rotation###curve-group-1");
            activate(timeline,"Roll###curve-axis-5");activate(timeline,"Framing###curve-group-2");
            activate(timeline,"Stack graphs###Compare");activate(timeline,"Framing###curve-lane-2");activate(timeline,"Position###curve-lane-0");
            activate(timeline,"Show original###Show Before");require(panel.curve_before_revision()==7,"View persistence fixture did not capture Before");
            io.DisplaySize=ImVec2(800,600);settle();activate(main,"Paths");
            require(!main->Active && ImGui::FindWindowByName("##lite-campaths")->Active,"Paths did not open before saving the compact view");
            require(panel.flush_layout(true),"Chosen editor views could not be saved");
            std::string legacy;
            require(dolly::load_ui_layout(path,legacy),"Cannot load the chosen editor views before legacy migration");
            const auto mode=legacy.find("TimelineMode=0");
            require(mode!=std::string::npos,"A saved session persists the last timeline tab instead of Replay");
            legacy.replace(mode,std::strlen("TimelineMode=0"),"TimelineMode=2");
            require(dolly::save_ui_layout(path,legacy),"Cannot prepare an old last-used Curves preference");
        } else {
            require(!main || !main->Active,"The saved compact Paths choice was not restored");
            require(panel.timeline_mode()==0 && panel.timeline_x1()>panel.timeline_x0() &&
                        !panel.curve_plot_rect(5).z && !panel.curve_plot_rect(6).z,
                    "A new session restored the previous Curves tab instead of Replay");
            io.DisplaySize=ImVec2(1920,1080);settle();
            activate(timeline,"Curves###lite-curves-mode");
            require(panel.curve_group()==2 && panel.curve_compare() && panel.curve_lane_count()==2 &&
                        panel.curve_plot_rect(5).z>panel.curve_plot_rect(5).x &&
                        panel.curve_plot_rect(6).z>panel.curve_plot_rect(6).x,
                    "The saved Curves view lost its group, lane mask or rotation axis");
            require(panel.curve_before_revision()==0 && panel.curve_before_session()==0,
                    "A prior session's Before snapshot leaked into persisted presentation state");
            io.DisplaySize=ImVec2(800,600);settle();
            activate(ImGui::FindWindowByName("##lite-campaths"),"Tools");
            bool export_card=false;for(auto* card:ctx->Windows)if(card->Active && std::strstr(card->Name,"##export-settings"))export_card=true;
            require(export_card,"Returning from saved Paths did not restore the selected Export tab");
            io.DisplaySize=ImVec2(1920,1080);settle();activate(timeline,"Stack graphs###Compare");activate(timeline,"Position###curve-group-0");
            require(panel.curve_plot_rect(2).z>panel.curve_plot_rect(2).x,
                    "A saved inactive Position axis was not restored when reopening its group");
            activate(timeline,"Replay###lite-timeline-mode");
            require(panel.timeline_x1()>panel.timeline_x0(),"A restored Curves workspace cannot return to the original replay timeline");
        }
        ImGui::DestroyContext(ctx);
    }
    ImGui::SetCurrentContext(previous);snapshot=original;camera_revision_stub=old_revision;
    viewer.publish(viewer_packet());viewer.tick();SetEnvironmentVariableA("DOLLY_UI_LAYOUT_FILE","none");
    require(DeleteFileW(path.c_str()) && DeleteFileW((path+L".bak").c_str()) && RemoveDirectoryW(directory.c_str()),
            "Cannot clean saved editor-view fixtures");
    std::puts("New sessions start Replay even with an old Curves preference; Paths/main tab/Curve group/axes/lanes restore; Before remains session-local.");
}
static void unified_curve_regressions(SyntheticViewer& viewer, float font_scale=1) {
    constexpr std::uint64_t session = 0x12345678abcdef01ULL;
    viewer.publish(curve_packet());viewer.tick();
    auto* previous=ImGui::GetCurrentContext();
    const auto original=snapshot;
    const auto original_recording_state=dolly::recording_status_stub.state;
    const auto original_camera_revision=camera_revision_stub;
    const auto original_camera_first=camera_first_stub;
    const auto original_camera_time_bias=camera_time_bias_stub;
    auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
    auto& io=ImGui::GetIO();io.DisplaySize=ImVec2(1280,720);io.DeltaTime=1.f/60;
    io.IniFilename=nullptr;io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
    dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(font_scale);
    ImGui::GetStyle().ScaleAllSizes(font_scale);
    io.Fonts->Build();
    snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
    snapshot.busy=snapshot.playing=snapshot.attach_preview=false;
    snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=10;snapshot.selected_camera=0;
    snapshot.duration=27;
    camera_revision_stub=7;camera_first_stub=0;camera_time_bias_stub=0;
    auto frame=[&]{ImGui::NewFrame();
                  if(snapshot.enabled && snapshot.focused && dolly::editor_panel_visible())panel.draw_panel(snapshot);
                  panel.draw_lite_campaths(snapshot);
                  panel.draw_lite_timeline(snapshot);ImGui::Render();panel.flush_layout();};
    auto settle=[&]{for(int i=0;i<5;++i)frame();};settle();
    auto* timeline=ImGui::FindWindowByName("##lite-timeline");
    auto activate=[&](const char* label) {
        const bool lane=std::strstr(label,"curve-lane-")!=nullptr;
        if(lane) { ctx->NavNextActivateId=timeline->GetID("Graphs");settle(); require(ctx->OpenPopupStack.Size>0,"Stacked Graphs menu did not open"); }
        ctx->NavNextActivateId=timeline->GetID(label);settle();
        if(lane) { ImGui::ClosePopupToLevel(0,true);settle(); }
    };
    activate("Curves###lite-curves-mode");
    require(panel.curve_group()==0 && panel.curve_lane_count()==1 && !panel.curve_compare(),
            "Curves must open in a single Position lane");
    // Start with one camera, then capture a short path in the same session.
    // Previously the initial Y scale survived each new camera and wheel-out
    // restored an unrelated one-second range for a real subsecond shot.
    constexpr auto short_session=session+100;
    constexpr double short_start=.125,short_step=.32,short_end=short_start+2*short_step;
    auto short_path=[&](unsigned count,unsigned revision,double change,unsigned selected,double step=.32) {
        snapshot.camera_count=count;snapshot.selected_camera=selected;snapshot.duration=short_start+(count-1)*step;
        camera_revision_stub=revision;
        viewer.publish(curve_packet(count,revision,short_session,change,false,selected,0,false,true,step,short_start));
        viewer.tick();settle();
    };
    auto visible_key=[&](unsigned axis) {
        const auto point=panel.curve_selected_point(axis);
        const auto plot=panel.curve_plot_rect(axis);
        return point.x>=plot.x+4*font_scale && point.x<=plot.z-4*font_scale &&
            point.y>=plot.y && point.y<=plot.w;
    };
    observed_actions.clear();short_path(1,1,0,0);
    require(panel.curve_view_start()==short_start && panel.curve_view_end()==short_start+1 && visible_key(0),
            "The one-camera graph lost its finite fallback or accessible first key");
    activate("Z###curve-axis-2");require(visible_key(2),"An initially opened axis has no visible camera key");
    short_path(2,2,1000,0);
    activate("X###curve-axis-0");
    require(visible_key(0),"Opening an axis after capturing another camera retained its old vertical range");
    short_path(3,3,5000,2);
    require(panel.curve_view_start()==short_start && panel.curve_view_end()==short_end && visible_key(0),
            "Capturing three cameras did not frame the actual subsecond path and all new values");
    const auto short_plot=panel.curve_plot_rect(0);
    const auto last_point=panel.curve_selected_point(0);
    require(std::abs(last_point.x-(short_plot.z-6*font_scale))<1 && observed_actions.empty(),
            "The final camera is not at the full timeline end, or initial framing edited the shot");
    io.AddMousePosEvent(last_point.x,last_point.y);frame();frame();io.AddMouseWheelEvent(0,2);settle();
    const double short_zoom_start=panel.curve_view_start(),short_zoom_end=panel.curve_view_end();
    const double short_low=panel.curve_scale_low(0),short_high=panel.curve_scale_high(0);
    require(short_zoom_end-short_zoom_start<2*short_step && visible_key(0),
            "The short-shot endpoint cannot anchor intentional zoom");
    short_path(3,4,5000.0001,2);
    require(panel.curve_view_start()==short_zoom_start && panel.curve_view_end()==short_zoom_end &&
                panel.curve_scale_low(0)==short_low && panel.curve_scale_high(0)==short_high,
            "An in-range value revision changed deliberate zoom or stable vertical bounds");
    const auto endpoint=panel.curve_selected_point(0);
    const auto endpoint_plot=panel.curve_plot_rect(0);
    io.AddMousePosEvent(endpoint.x,endpoint.y);frame();frame();
    observed_actions.clear();io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(endpoint.x,endpoint_plot.w+2*(endpoint_plot.w-endpoint_plot.y));frame();frame();
    const auto edge_draft=panel.curve_draft_point();
    require(panel.curve_editing() && panel.curve_draft_outside() &&
                edge_draft.y>=endpoint_plot.y && edge_draft.y<=endpoint_plot.w && observed_actions.empty(),
            "An endpoint drag lost its visible edge indicator or committed before release");
    io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
                observed_actions[0].value==2 && observed_actions[0].pose[0]==4 &&
                observed_actions[0].pose[1]==0 && observed_actions[0].pose[3]==short_end,
            "The inset endpoint cannot commit one revision-bound graph edit");
    const double base_last=100.12345678901234+20*std::sin(2*.6);
    const double drag_value=observed_actions[0].pose[2];
    require(drag_value<short_low,
            "The offscreen draft was clamped to the visible data range instead of preserving its value");
    observed_actions.clear();short_path(3,5,drag_value-base_last,2);
    require(visible_key(0) && panel.curve_view_start()==short_zoom_start && panel.curve_view_end()==short_zoom_end &&
                panel.curve_scale_low(0)<short_low && observed_actions.empty(),
            "The accepted out-of-bounds drag remains inaccessible or reset intentional horizontal zoom");
    constexpr double outside_numeric=-15000.12345678901;
    ctx->NavNextActivateId=timeline->GetID("##curve-value");ctx->NavNextActivateFlags=ImGuiActivateFlags_PreferInput;
    frame();frame();require(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId,
                          "Cannot open precise numeric input after offscreen drag recovery");
    io.AddInputCharactersUTF8("-15000.12345678901");frame();io.AddKeyEvent(ImGuiKey_Enter,true);frame();
    io.AddKeyEvent(ImGuiKey_Enter,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].pose[2]==outside_numeric,
            "Automatic graph recovery changed precise numeric commit semantics");
    observed_actions.clear();short_path(3,6,outside_numeric-base_last,2);
    require(visible_key(0) && panel.curve_view_start()==short_zoom_start && panel.curve_view_end()==short_zoom_end &&
                observed_actions.empty(),"An accepted numeric edit needs a manual fit to recover its key");
    const double recovered_low=panel.curve_scale_low(0),recovered_high=panel.curve_scale_high(0);
    camera_revision_stub=7;settle();
    require(panel.curve_scale_low(0)==recovered_low && panel.curve_scale_high(0)==recovered_high &&
                panel.curve_view_start()==short_zoom_start && panel.curve_view_end()==short_zoom_end && observed_actions.empty(),
            "A mismatched stale graph reframed or changed the current shot");
    camera_revision_stub=6;settle();
    const auto pan_plot=panel.curve_plot_rect(0);
    const ImVec2 pan_at((pan_plot.x+pan_plot.z)*.5f,(pan_plot.y+pan_plot.w)*.5f);
    io.AddMousePosEvent(pan_at.x,pan_at.y);frame();frame();
    io.AddMouseButtonEvent(1,true);frame();
    io.AddMousePosEvent(pan_at.x+20*font_scale,pan_at.y);frame();
    // Removing the interior key changes the key count, with identical first
    // and last times. It must discard the old capture's vertical range, even
    // while an old right-button pan is still physically held.
    short_path(2,8,0,1,2*short_step);
    require(panel.curve_view_start()==short_start && panel.curve_view_end()==short_end && visible_key(0) &&
                panel.curve_scale_high(0)-panel.curve_scale_low(0)<recovered_high-recovered_low,
            "Removing an interior camera retained old zoom/range or a held pan overwrote the new full view");
    io.AddMouseButtonEvent(1,false);settle();
    io.AddMousePosEvent((short_plot.x+short_plot.z)*.5f,(short_plot.y+short_plot.w)*.5f);frame();frame();
    io.AddMouseWheelEvent(0,-20);settle();
    require(panel.curve_view_start()==short_start && panel.curve_view_end()==short_end,
            "Wheel zoom-out added a one-second floor to a real subsecond shot");
    snapshot.camera_count=0;snapshot.selected_camera=0;viewer.publish(viewer_packet());viewer.tick();settle();
    require(panel.curve_plot_rect(0).z==0 && observed_actions.empty(),
            "An empty/mismatched graph retained editable plot geometry or dispatched a camera action");
    viewer.publish(curve_packet());viewer.tick();camera_revision_stub=7;
    snapshot.camera_count=10;snapshot.selected_camera=0;snapshot.duration=27;settle();
    {
        constexpr auto alignment_session=session+200;
        std::vector<dolly::CameraPose> keys(5,dolly::CameraPose{0,0,0,0,0,0,16./9});
        const double x_values[]{5,14.51234567890123,15,16.9876543210123,25};
        for(unsigned i=0;i<keys.size();++i) keys[i][0]=x_values[i];
        auto publish_alignment=[&](unsigned revision=50,bool unwrapped=false,
                                   const std::vector<dolly::CameraPose>* authored=nullptr,
                                   std::uint64_t identity=0) {
            if(!identity) identity=alignment_session;
            snapshot.camera_count=unsigned(keys.size());snapshot.selected_camera=2;snapshot.duration=12;
            camera_revision_stub=revision;
            viewer.publish(curve_packet(unsigned(keys.size()),revision,identity,0,false,2,0,unwrapped,false,3,0,&keys,authored));
            viewer.tick();settle();
        };
        publish_alignment();
        auto hover_key=[&](unsigned axis) {
            const auto point=panel.curve_selected_point(axis);
            io.AddMousePosEvent(point.x,point.y);settle();return point;
        };
        auto begin_key_drag=[&](unsigned axis) {
            const auto point=hover_key(axis);observed_actions.clear();
            io.MouseClickedTime[0]=-1e30;
            io.AddMouseButtonEvent(0,true);frame();
            require(panel.curve_editing(),"Cannot begin the real graph adjustment fixture");return point;
        };
        auto release_cancelled=[&] {
            io.AddMouseButtonEvent(0,false);frame();io.AddMouseButtonEvent(1,false);
            io.AddKeyEvent(ImGuiMod_Alt,false);settle();
            require(observed_actions.empty(),"A cancelled adjustment later dispatched an edit, Undo or replay seek");
        };
        hover_key(0);
        ImGuiWindow* key_tooltip=nullptr;
        for(auto* item:ctx->Windows) if(item->Active && (item->Flags&ImGuiWindowFlags_Tooltip)) key_tooltip=item;
        require(key_tooltip && std::abs(key_tooltip->WindowPadding.x-10*font_scale)<.1f &&
                    std::abs(key_tooltip->WindowPadding.y-8*font_scale)<.1f &&
                    key_tooltip->Pos.x>=-.5f && key_tooltip->Pos.y>=-.5f &&
                    key_tooltip->Pos.x+key_tooltip->Size.x<=io.DisplaySize.x+.5f &&
                    key_tooltip->Pos.y+key_tooltip->Size.y<=io.DisplaySize.y+.5f,
                "Graph hover tooltip inherits zero plot padding or escapes the viewport");
        // A real zoomed graph cancellation must not turn its right click into
        // pan, including subsequent held movement and the final left release.
        const auto initial=panel.curve_selected_point(0);
        io.AddMousePosEvent(initial.x,initial.y);frame();frame();io.AddMouseWheelEvent(0,2);settle();
        const auto cancel_start=panel.curve_view_start(),cancel_end=panel.curve_view_end();
        const auto cancel_at=begin_key_drag(0);
        io.AddMousePosEvent(cancel_at.x,cancel_at.y-3*font_scale);frame();
        io.AddMouseButtonEvent(1,true);frame();
        require(!panel.curve_editing() && observed_actions.empty(),"Right-click did not cancel the graph draft before left release");
        io.AddMousePosEvent(cancel_at.x+80*font_scale,cancel_at.y);frame();frame();
        require(panel.curve_view_start()==cancel_start && panel.curve_view_end()==cancel_end,
                "The graph cancellation click or held right button also panned the timeline");
        release_cancelled();
        // Both typed and horizontal numeric drafts cancel before blur can
        // dispatch the value. Reopening unchanged input must preserve precision.
        observed_actions.clear();ctx->NavNextActivateId=timeline->GetID("##curve-value");
        ctx->NavNextActivateFlags=ImGuiActivateFlags_PreferInput;frame();frame();
        require(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId,"Cannot open numeric cancel fixture");
        io.AddInputCharactersUTF8("123.45678901234567");frame();
        io.AddMouseButtonEvent(1,true);frame();require(!panel.curve_editing(),"Right-click left precise input editing active");
        release_cancelled();
        ctx->NavNextActivateId=timeline->GetID("##curve-value");ctx->NavNextActivateFlags=ImGuiActivateFlags_PreferInput;
        frame();frame();io.AddKeyEvent(ImGuiKey_Enter,true);frame();io.AddKeyEvent(ImGuiKey_Enter,false);settle();
        require(observed_actions.empty(),"Reopening a cancelled numeric draft restored or rounded the discarded value");
        const auto field=panel.curve_value_rect();
        io.AddMousePosEvent((field.x+field.z)*.5f,(field.y+field.w)*.5f);frame();frame();
        io.MouseClickedTime[0]=-1e30;io.AddMouseButtonEvent(0,true);frame();
        io.AddMousePosEvent((field.x+field.z)*.5f+24*font_scale,(field.y+field.w)*.5f);frame();frame();
        require(panel.curve_editing() && observed_actions.empty(),"Numeric drag fixture committed before release");
        io.AddMouseButtonEvent(1,true);frame();release_cancelled();
        require(panel.curve_view_start()==cancel_start && panel.curve_view_end()==cancel_end,
                "Cancelling numeric input also changed graph zoom or pan");
        activate("Fit all###Fit");
        auto point=begin_key_drag(0);
        const auto plot=panel.curve_plot_rect(0);
        const double units=(panel.curve_scale_high(0)-panel.curve_scale_low(0))/(plot.w-plot.y);
        const float previous_y=point.y-float((keys[1][0]-keys[2][0])/units)+3*font_scale;
        io.AddMousePosEvent(point.x,previous_y);frame();
        const auto free_value=panel.curve_draft_value();
        io.AddKeyEvent(ImGuiMod_Alt,true);frame();
        require(panel.curve_snapped_neighbor()==1 && panel.curve_draft_value()==keys[1][0] && observed_actions.empty(),
                "Alt did not align exactly to the immediate attached neighbor without dispatch");
        io.AddKeyEvent(ImGuiMod_Alt,false);frame();
        require(panel.curve_snapped_neighbor()<0 && panel.curve_draft_value()==free_value,
                "Releasing Alt changed the original free-drag anchor");
        // ImGui trickles MousePos after a changed modifier into the following
        // frame. Settle Alt first so the assertion observes the new pointer.
        io.AddKeyEvent(ImGuiMod_Alt,true);frame();
        const float next_y=point.y-float((keys[3][0]-keys[2][0])/units)-font_scale;
        io.AddMousePosEvent(point.x,next_y);frame();
        if(!(panel.curve_snapped_neighbor()==3 && panel.curve_draft_value()==keys[3][0]))
            std::fprintf(stderr,"Following snap scale%.2f: pointer%.3f,%.3f requested%.3f,%.3f Alt%d neighbor%d value%.17g expected%.17g previous%.17g queued%d editing%d units%.9g\n",
                font_scale,io.MousePos.x,io.MousePos.y,point.x,next_y,int(io.KeyAlt),panel.curve_snapped_neighbor(),
                panel.curve_draft_value(),keys[3][0],keys[1][0],ctx->InputEventsQueue.Size,int(panel.curve_editing()),units);
        require(panel.curve_snapped_neighbor()==3 && panel.curve_draft_value()==keys[3][0],
                "Alt did not choose the closer following camera value");
        io.AddMousePosEvent(point.x,plot.w+200*font_scale);frame();
        require(panel.curve_snapped_neighbor()<0,"Alt snapped to a neighboring value outside its visual threshold");
        io.AddMousePosEvent(point.x,previous_y);frame();io.AddMouseButtonEvent(0,false);frame();
        io.AddKeyEvent(ImGuiMod_Alt,false);settle();
        require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
                    observed_actions[0].value==2 && observed_actions[0].pose[0]==50 && observed_actions[0].pose[1]==0 &&
                    observed_actions[0].pose[2]==keys[1][0] && observed_actions[0].pose[3]==6 &&
                    observed_actions[0].pose[4]==double(std::uint32_t(alignment_session)) &&
                    observed_actions[0].pose[5]==double(std::uint32_t(alignment_session>>32)),
                "Snapped release lost full precision, channel/time/session guards or its single-edit contract");
        keys[1][0]=14.5;keys[3][0]=15.5;publish_alignment(51);
        begin_key_drag(0);io.AddKeyEvent(ImGuiMod_Alt,true);frame();
        require(panel.curve_snapped_neighbor()==1 && panel.curve_draft_value()==14.5,
                "Equidistant neighboring values did not deterministically choose the earlier camera");
        io.AddMouseButtonEvent(1,true);frame();release_cancelled();
        // Displayed yaw lives on a continuous branch; the selected authored
        // value can have a different turn offset from its neighbor.
        const double yaw_values[]{-10,-1.23456789012345,-5,2.3456789012345,10};
        for(unsigned i=0;i<keys.size();++i) keys[i][4]=yaw_values[i];
        auto authored=keys;authored[1][4]+=360;authored[2][4]+=720;
        publish_alignment(52,false,&authored);
        activate("Rotation###curve-group-1");activate("Yaw###curve-axis-4");
        point=begin_key_drag(4);
        auto angle_plot=panel.curve_plot_rect(4);
        double angle_units=(panel.curve_scale_high(4)-panel.curve_scale_low(4))/(angle_plot.w-angle_plot.y);
        io.AddMousePosEvent(point.x,point.y-float((keys[1][4]-keys[2][4])/angle_units));
        io.AddKeyEvent(ImGuiMod_Alt,true);frame();io.AddMouseButtonEvent(0,false);frame();
        io.AddKeyEvent(ImGuiMod_Alt,false);settle();
        require(observed_actions.size()==1 && observed_actions[0].pose[1]==4 &&
                    observed_actions[0].pose[2]==authored[1][4]+360 && observed_actions[0].pose[3]==6,
                "Shortest-angle alignment introduced a turn jump or lost the neighbor's exact authored fraction");
        for(unsigned i=0;i<keys.size();++i) keys[i][4]+=720;
        publish_alignment(53,true);
        point=begin_key_drag(4);angle_plot=panel.curve_plot_rect(4);
        angle_units=(panel.curve_scale_high(4)-panel.curve_scale_low(4))/(angle_plot.w-angle_plot.y);
        io.AddMousePosEvent(point.x,point.y-float((keys[1][4]-keys[2][4])/angle_units));
        io.AddKeyEvent(ImGuiMod_Alt,true);frame();io.AddMouseButtonEvent(0,false);frame();
        io.AddKeyEvent(ImGuiMod_Alt,false);settle();
        require(observed_actions.size()==1 && observed_actions[0].pose[2]==keys[1][4],
                "Unwrapped rotation alignment changed an explicitly authored turn");
        begin_key_drag(4);io.AddKeyEvent(ImGuiMod_Alt,true);frame();++camera_revision_stub;frame();
        io.AddMouseButtonEvent(0,false);io.AddKeyEvent(ImGuiMod_Alt,false);settle();
        require(observed_actions.empty() && !panel.curve_editing(),"A stale revision committed an Alt-aligned draft");
        publish_alignment(53,true);begin_key_drag(4);io.AddKeyEvent(ImGuiMod_Alt,true);frame();
        publish_alignment(54,true,nullptr,alignment_session+1);
        io.AddMouseButtonEvent(0,false);io.AddKeyEvent(ImGuiMod_Alt,false);settle();
        require(observed_actions.empty() && !panel.curve_editing(),"A changed session committed an Alt-aligned draft");
        activate("Position###curve-group-0");activate("X###curve-axis-0");
        viewer.publish(curve_packet());viewer.tick();camera_revision_stub=7;
        snapshot.camera_count=10;snapshot.selected_camera=0;snapshot.duration=27;settle();
    }
    for(const auto viewport:{ImVec2(1280,720),ImVec2(2560,1440)}) {
        io.DisplaySize=viewport;settle();
        auto* paths=ImGui::FindWindowByName("##lite-campaths");
        require((!paths || !paths->Active || (paths->Pos.y>=0 && paths->Pos.y+paths->Size.y<=timeline->Pos.y-12)) &&
                timeline->Pos.y+timeline->Size.y<=io.DisplaySize.y+1 &&
                timeline->DC.CursorMaxPos.y<=timeline->InnerClipRect.Max.y+1,
                "Curves dock clips its footer, leaves the viewport or overlaps Campaths");
        require(!paths || !paths->Active || ctx->Tables.GetByKey(paths->GetID("##lite-campath-details")),
                "Curves dock hides the camera table");
        const auto plot=panel.curve_plot_rect(0);
        if(!(plot.z-plot.x>500 && plot.w-plot.y>65))
            std::fprintf(stderr,"Curves plot scale %.2f viewport %.0fx%.0f: plot %.1f %.1f..%.1f %.1f dock %.1f %.1f %.1fx%.1f cursor %.1f clip %.1f font %.1f\n",
                         font_scale,viewport.x,viewport.y,plot.x,plot.y,plot.z,plot.w,timeline->Pos.x,timeline->Pos.y,
                         timeline->Size.x,timeline->Size.y,timeline->DC.CursorMaxPos.y,timeline->InnerClipRect.Max.y,timeline->FontRefSize);
        require(plot.z-plot.x>500 && plot.w-plot.y>65,
                "The single graph lost its usable width or editing height");
    }
    io.DisplaySize=ImVec2(1280,720);settle();
    observed_actions.clear();
    ctx->NavNextActivateId=timeline->GetID("##curve-value");
    ctx->NavNextActivateFlags=ImGuiActivateFlags_PreferInput;frame();frame();
    require(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId,
            "Curve numeric editor cannot open for exact fractional input");
    io.AddKeyEvent(ImGuiKey_Enter,true);frame();
    io.AddKeyEvent(ImGuiKey_Enter,false);settle();
    require(observed_actions.empty(),"Opening and accepting an unchanged curve value rounded the saved double");
    const auto value_rect=panel.curve_value_rect();
    const ImRect exact_field(ImVec2(value_rect.x,value_rect.y),ImVec2(value_rect.z,value_rect.w));
    require(exact_field.GetWidth()>0 && exact_field.GetHeight()>0,
            "The exact curve value has no rendered widget rectangle");
    const ImVec2 exact_point=exact_field.GetCenter();
    io.AddMousePosEvent(exact_point.x,exact_point.y);frame();frame();
    io.AddKeyEvent(ImGuiMod_Ctrl,true);frame();io.AddMouseButtonEvent(0,true);frame();
    const auto ctrl_click_active=ctx->ActiveId;
    const auto ctrl_click_input=ctx->InputTextState.ID;
    const bool ctrl_click_modifier=io.KeyCtrl;
    io.AddMouseButtonEvent(0,false);frame();io.AddKeyEvent(ImGuiMod_Ctrl,false);frame();
    if(!(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId))
        std::fprintf(stderr,"Exact Ctrl-click at %.1f %.1f, widget rect %.1f %.1f %.1f %.1f; click active %u input %u ctrl %d; final active %u input %u hovered %s expected %u\n",
                     exact_point.x,exact_point.y,exact_field.Min.x,exact_field.Min.y,exact_field.Max.x,exact_field.Max.y,
                     ctrl_click_active,ctrl_click_input,int(ctrl_click_modifier),ctx->ActiveId,ctx->InputTextState.ID,
                     ctx->HoveredWindow ? ctx->HoveredWindow->Name : "none",timeline->GetID("##curve-value"));
    require(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId,
            "Ctrl-click cannot open the exact curve value behind its rounded idle label");
    io.AddKeyEvent(ImGuiKey_Enter,true);frame();io.AddKeyEvent(ImGuiKey_Enter,false);settle();
    require(observed_actions.empty(),"Ctrl-click and Enter rounded the original 13-digit curve value");
    // Frame all is view-only, but leaving an edited numeric field must finish
    // its same one guarded edit for mouse and navigation activation alike.
    const double drafted_value=345.67890123456789;
    auto begin_fractional_draft=[&] {
        observed_actions.clear();
        ctx->NavNextActivateId=timeline->GetID("##curve-value");
        ctx->NavNextActivateFlags=ImGuiActivateFlags_PreferInput;frame();frame();
        if(!(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId))
            std::fprintf(stderr,"Draft input activation: revision %u active %u input %u expected %u editing %d navwin %s field %.1f %.1f %.1f %.1f\n",
                         camera_revision_stub,ctx->ActiveId,ctx->InputTextState.ID,timeline->GetID("##curve-value"),int(panel.curve_editing()),
                         ctx->NavWindow ? ctx->NavWindow->Name : "none",panel.curve_value_rect().x,panel.curve_value_rect().y,
                         panel.curve_value_rect().z,panel.curve_value_rect().w);
        require(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId,
                "Cannot open the real curve field before reframing");
        io.AddInputCharactersUTF8("345.67890123456789");frame();
        require(panel.curve_editing() && observed_actions.empty(),
                "A numeric draft was committed while still typing");
    };
    begin_fractional_draft();
    const auto fit_rect=panel.curve_frame_all_rect();
    require(fit_rect.z>fit_rect.x && fit_rect.w>fit_rect.y,"Frame all has no visible pointer target");
    io.AddMousePosEvent((fit_rect.x+fit_rect.z)*.5f,(fit_rect.y+fit_rect.w)*.5f);frame();frame();
    io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);settle();
    const auto mouse_fit_actions=observed_actions;
    begin_fractional_draft();
    ctx->NavNextActivateId=timeline->GetID("Frame all###Fit");settle();
    std::printf("Numeric draft -> Frame all: mouse actions %zu, navigation actions %zu at scale %.2f\n",
                mouse_fit_actions.size(),observed_actions.size(),font_scale);
    const auto is_precise_draft=[&](const std::vector<ObservedAction>& actions) {
        return actions.size()==1 && actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
            actions[0].pose[0]==7 && actions[0].pose[1]==0 && actions[0].pose[2]==drafted_value &&
            actions[0].pose[3]==0 && actions[0].pose[4]==double(std::uint32_t(session)) &&
            actions[0].pose[5]==double(std::uint32_t(session>>32));
    };
    require(is_precise_draft(mouse_fit_actions) && is_precise_draft(observed_actions),
            "Frame all discarded or duplicated the active precise numeric edit");
    // Once the edit is published, framing is still a view operation: no Undo,
    // extra authoring request or replay seek, and the changed key is in view.
    camera_revision_stub=8;
    viewer.publish(curve_packet(10,8,session,drafted_value-100.12345678901234,false,0,0,false,true));viewer.tick();settle();
    const auto published_point=panel.curve_selected_point(0);
    const auto published_plot=panel.curve_plot_rect(0);
    require(published_plot.z>published_plot.x && published_plot.w>published_plot.y &&
                published_point.y>=published_plot.y && published_point.y<=published_plot.w,
            "Frame all fitted old samples and left the accepted numeric edit outside the graph");
    observed_actions.clear();activate("Frame all###Fit");
    require(observed_actions.empty(),"Frame all changed history or sought the replay after a committed edit");
    ctx->NavNextActivateId=timeline->GetID("##curve-value");ctx->NavNextActivateFlags=ImGuiActivateFlags_PreferInput;frame();frame();
    activate("Frame all###Fit");require(observed_actions.empty(),"Frame all changed an untouched exact numeric value");
    begin_fractional_draft();
    io.AddKeyEvent(ImGuiMod_Ctrl,true);io.AddKeyEvent(ImGuiKey_A,true);frame();
    io.AddKeyEvent(ImGuiKey_A,false);io.AddKeyEvent(ImGuiMod_Ctrl,false);frame();
    io.AddInputCharactersUTF8("987.65432109876543");frame();
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();
    io.AddKeyEvent(ImGuiKey_Escape,false);settle();activate("Frame all###Fit");
    require(observed_actions.empty(),"Frame all restored or committed an Escape-cancelled numeric draft");
    camera_revision_stub=7;viewer.publish(curve_packet());viewer.tick();settle();activate("Frame all###Fit");
    begin_fractional_draft();camera_revision_stub=8;viewer.publish(curve_packet(10,8,session));viewer.tick();settle();activate("Frame all###Fit");
    require(observed_actions.empty(),"Frame all committed a stale numeric revision");
    camera_revision_stub=7;viewer.publish(curve_packet());viewer.tick();settle();
    unsigned channel=0;
    auto drag=[&](bool release) {
        const auto point=panel.curve_selected_point(channel);
        const auto plot=panel.curve_plot_rect(channel);
        if(!(point.x>=plot.x && point.x<=plot.z && point.y>=plot.y && point.y<=plot.w))
            std::fprintf(stderr,"Curve hit target ch%u scale%.2f group%d stack%d lanes%u revision%u selected%u point%.1f,%.1f plot%.1f,%.1f..%.1f,%.1f\n",
                         channel,font_scale,panel.curve_group(),int(panel.curve_compare()),panel.curve_lane_count(),camera_revision_stub,
                         snapshot.selected_camera,point.x,point.y,plot.x,plot.y,plot.z,plot.w);
        require(point.x>=plot.x && point.x<=plot.z && point.y>=plot.y && point.y<=plot.w,
                "Selected curve key has no visible hit target");
        io.AddMousePosEvent(point.x,point.y);frame();frame();
        observed_actions.clear();io.AddMouseButtonEvent(0,true);frame();
        io.AddMousePosEvent(point.x,point.y-10);frame();frame();
        require(observed_actions.empty(),"Curve graph changed the shot before release");
        if(release){io.AddMouseButtonEvent(0,false);frame();frame();}
    };
    drag(true);
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
            observed_actions[0].value==0 && observed_actions[0].pose[0]==7 &&
            observed_actions[0].pose[1]==0 && observed_actions[0].pose[2]>100 &&
            observed_actions[0].pose[3]==0 && observed_actions[0].pose[4]==double(std::uint32_t(session)) &&
            observed_actions[0].pose[5]==double(std::uint32_t(session>>32)),
            "Curve release lost its revision, channel, key time, session or value");
    drag(false);io.AddKeyEvent(ImGuiKey_Escape,true);frame();
    io.AddKeyEvent(ImGuiKey_Escape,false);io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.empty(),"Escape committed a curve graph draft");
    drag(false);snapshot.focused=false;frame();snapshot.focused=true;
    io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.empty(),"Focus loss committed a curve graph draft");
    drag(false);++camera_revision_stub;frame();io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.empty(),"A stale graph revision committed a draft");
    camera_revision_stub=7;settle();
    snapshot.selected_camera=1;viewer.publish(curve_packet(10,7,session,0,false,1));viewer.tick();settle();drag(true);
    require(observed_actions.empty(),"An attached camera accepted a graph position edit");
    snapshot.selected_camera=0;viewer.publish(curve_packet());viewer.tick();settle();snapshot.playing=true;drag(true);
    require(observed_actions.empty(),"Playback accepted a graph position edit");
    snapshot.playing=false;snapshot.busy=true;settle();drag(true);
    require(observed_actions.empty(),"A busy editor accepted a curve draft");
    snapshot.busy=false;dolly::recording_status_stub.state=dolly::video::State::recording;settle();drag(true);
    require(observed_actions.empty(),"Recording accepted a curve draft");
    dolly::recording_status_stub.state=dolly::video::State::completed;settle();
    activate("Rotation###curve-group-1");activate("Pitch###curve-axis-3");channel=3;
    require(panel.curve_group()==1,"Rotation tab did not become active");
    drag(true);
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
                observed_actions[0].pose[1]==3 && observed_actions[0].pose[2]>5,
            "Rotation graph did not edit the selected pitch channel");
    activate("Framing###curve-group-2");channel=6;
    require(panel.curve_group()==2,"Framing tab did not become active");
    drag(true);
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
                observed_actions[0].pose[1]==6 && observed_actions[0].pose[2]>16.0/9 &&
                observed_actions[0].pose[2]<=4,
            "Framing graph did not edit supported aspect ratio values");
    struct ChannelCase { const char* group;const char* axis;unsigned channel;double initial; };
    for(const auto& item:{ChannelCase{"Position###curve-group-0","Y###curve-axis-1",1,-38},
                         ChannelCase{"Position###curve-group-0","Z###curve-axis-2",2,30},
                         ChannelCase{"Rotation###curve-group-1","Yaw###curve-axis-4",4,10},
                         ChannelCase{"Rotation###curve-group-1","Roll###curve-axis-5",5,0}}) {
        activate(item.group);activate(item.axis);channel=item.channel;drag(true);
        require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
                    observed_actions[0].pose[1]==channel && observed_actions[0].pose[2]>item.initial,
                "A curve channel selector edited another channel or lost its authored value");
    }
    activate("Rotation###curve-group-1");activate("Pitch###curve-axis-3");
    activate("Position###curve-group-0");channel=0;
    activate("X###curve-axis-0");
    const auto zoom_plot=panel.curve_plot_rect(0);
    io.AddMousePosEvent((zoom_plot.x+zoom_plot.z)*.5f,(zoom_plot.y+zoom_plot.w)*.5f);frame();frame();
    io.AddMouseWheelEvent(0,2);settle();
    require(panel.curve_view_end()-panel.curve_view_start()<27,
            "Wheel zoom did not change the shared curve time range");
    const auto common_start=panel.curve_view_start(),common_end=panel.curve_view_end();
    activate("Rotation###curve-group-1");activate("Framing###curve-group-2");activate("Position###curve-group-0");
    require(panel.curve_view_start()==common_start && panel.curve_view_end()==common_end,
            "Switching curve groups discarded horizontal zoom");
    auto readable_main_controls=[&] {
        auto* main=ImGui::FindWindowByName("Deadlock Dolly");
        require(main && main->Active,"Curves removed the main editor controls");
        for(const char* page:{"Camera###CAMERA","Follow###FOLLOW","Bone Picker###BONE PICKER","Look###LOOK","Export###EXPORT"}) {
            ctx->NavNextActivateId=main->GetID(page);
            for(int frame_index=0;frame_index<20;++frame_index)frame();
            ImGuiWindow* content=nullptr;
            unsigned cards=0;
            for(auto* child:ctx->Windows) if(child->Active && child->ParentWindow &&
                    std::strstr(child->ParentWindow->Name,"##editor-content")) {
                content=child->ParentWindow;++cards;
                require(child->FontRefSize>=14*font_scale,
                        "Curves comparison shrank existing main controls below readable text size");
                require(child->DC.CursorMaxPos.y<=child->Pos.y+child->Size.y+1,
                        "An existing editor card clips controls inside its own height");
            }
            require(cards && content,"A main editor tab lost its control cards while Curves was open");
            require(content->Size.y>=40*font_scale-1,
                    "Stacked graphs leave no readable main content viewport");
            float bottom=0;
            for(const auto* child:ctx->Windows) if(child->Active && child->ParentWindow==content)
                bottom=std::max(bottom,child->Pos.y+child->Size.y);
            if(bottom>content->InnerClipRect.Max.y+1) {
                require(content->ScrollMax.y>0 && !(content->Flags&ImGuiWindowFlags_NoScrollWithMouse),
                        "Controls below the Curves-reduced editor viewport cannot be reached by scrolling");
                ImGui::SetScrollY(content,content->ScrollMax.y);settle();
                bottom=0;
                for(const auto* child:ctx->Windows) if(child->Active && child->ParentWindow==content)
                    bottom=std::max(bottom,child->Pos.y+child->Size.y);
                if(!(content->Scroll.y>0 && bottom<=content->InnerClipRect.Max.y+1)) {
                    std::fprintf(stderr,"Curves reachability page %s scale %.2f lanes %u: bottom %.2f clip %.2f..%.2f scroll %.2f max %.2f size %.2f content %.2f\n",
                                 page,font_scale,panel.curve_lane_count(),bottom,content->InnerClipRect.Min.y,content->InnerClipRect.Max.y,
                                 content->Scroll.y,content->ScrollMax.y,content->Size.y,content->ContentSize.y);
                    for(const auto* child:ctx->Windows)if(child->Active && child->ParentWindow==content)
                        std::fprintf(stderr,"  Card %s y %.2f height %.2f cursor %.2f skip %d clip %.2f..%.2f\n",
                                     child->Name,child->Pos.y,child->Size.y,child->DC.CursorMaxPos.y,int(child->SkipItems),
                                     child->InnerClipRect.Min.y,child->InnerClipRect.Max.y);
                }
                require(content->Scroll.y>0 && bottom<=content->InnerClipRect.Max.y+1,
                        "Scrolling cannot reveal the last existing editor controls");
                ImGui::SetScrollY(content,0);settle();
            }
        }
        ctx->NavNextActivateId=main->GetID("Camera###CAMERA");settle();
    };
    activate("Stack graphs###Compare");
    require(panel.curve_compare() && panel.curve_lane_count()==2,
            "Compare must start with two readable lanes");
    require(!panel.curve_group_controls_visible() && panel.curve_graphs_rect().z>panel.curve_graphs_rect().x,
            "Stack graphs retains redundant group tabs or lost its Graphs menu");
    const auto graphs_button=panel.curve_graphs_rect();
    io.AddMousePosEvent((graphs_button.x+graphs_button.z)*.5f,(graphs_button.y+graphs_button.w)*.5f);frame();frame();
    io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);settle();
    require(ctx->OpenPopupStack.Size && ctx->OpenPopupStack.back().Window,
            "The actual Graphs button cannot open its lane menu");
    const auto* graphs_popup=ctx->OpenPopupStack.back().Window;
    require(graphs_popup->Pos.x>=0 && graphs_popup->Pos.y>=0 &&
                graphs_popup->Pos.x+graphs_popup->Size.x<=io.DisplaySize.x+1 &&
                graphs_popup->Pos.y+graphs_popup->Size.y<=io.DisplaySize.y+1,
            "The Graphs menu leaves the viewport");
    ImGui::ClosePopupToLevel(0,true);settle();
    observed_actions.clear();activate("Position###curve-lane-0");
    require(panel.curve_lane_count()==1,"Graphs cannot hide an individual lane");
    activate("Rotation###curve-lane-1");
    require(panel.curve_lane_count()==1,"Graphs allowed every lane to be hidden");
    activate("Position###curve-lane-0");
    require(panel.curve_lane_count()==2 && observed_actions.empty(),
            "Graphs lane visibility changed authoring or lost its prior selection");
    ImGuiWindow* plots=nullptr;
    for(auto* child:ctx->Windows) if(child->Active && child->ParentWindow==timeline &&
            std::strstr(child->Name,"##curve-plots")) plots=child;
    require(plots,"Stacked inline axes have no graph body");
    // Keep the user's existing horizontal zoom. Select a free key inside that
    // range instead of trying to drag camera0, which is intentionally outside.
    snapshot.selected_camera=4;viewer.publish(curve_packet(10,7,session,0,false,4));viewer.tick();settle();
    auto click_stacked_axis=[&](unsigned axis) {
        auto rect=panel.curve_axis_rect(axis);
        require(rect.z>rect.x && rect.w>rect.y,"A normal-width stacked lane replaced its inline axes with a dropdown");
        const float scroll=std::max(0.f,plots->Scroll.y+rect.y-plots->InnerClipRect.Min.y);
        ImGui::SetScrollY(plots,scroll);settle();rect=panel.curve_axis_rect(axis);
        require(rect.y>=plots->InnerClipRect.Min.y-1 && rect.w<=plots->InnerClipRect.Max.y+1,
                "A stacked axis button cannot be reached through the graph scroller");
        observed_actions.clear();io.AddMousePosEvent((rect.x+rect.z)*.5f,(rect.y+rect.w)*.5f);frame();frame();
        io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);settle();
        require(observed_actions.empty() && panel.curve_plot_rect(axis).z>panel.curve_plot_rect(axis).x,
                "A stacked axis click changed the shot or selected another channel");
    };
    for(unsigned axis:{0u,1u,2u,3u,4u,5u}) {
        click_stacked_axis(axis);
        if(axis==1 || axis==5) {
            channel=axis;const auto point=panel.curve_selected_point(axis);
            ImGui::SetScrollY(plots,std::max(0.f,plots->Scroll.y+point.y-(plots->InnerClipRect.Min.y+plots->InnerClipRect.Max.y)*.5f));settle();
            drag(true);
            require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
                        observed_actions[0].value==4 && observed_actions[0].pose[1]==axis && observed_actions[0].pose[0]==7,
                    "A graph drag after an inline axis click edited a different authoring channel");
        }
    }
    require(!panel.curve_lane_compact_axes(0) && !panel.curve_lane_compact_axes(1),
            "Normal stacked Position/Rotation lanes hide their inline axis choices");
    click_stacked_axis(0);click_stacked_axis(3);channel=0;ImGui::SetScrollY(plots,0);settle();
    snapshot.selected_camera=0;viewer.publish(curve_packet());viewer.tick();settle();
    readable_main_controls();
    activate("Framing###curve-lane-2");
    require(panel.curve_lane_count()==3,"Compare cannot include all three groups");
    readable_main_controls();
    const auto position_plot=panel.curve_plot_rect(0),rotation_plot=panel.curve_plot_rect(3),framing_plot=panel.curve_plot_rect(6);
    require(position_plot.w<rotation_plot.y && rotation_plot.w<framing_plot.y &&
                std::abs(position_plot.x-rotation_plot.x)<1 && std::abs(position_plot.z-framing_plot.z)<1 &&
                panel.curve_view_start()==common_start && panel.curve_view_end()==common_end,
            "Compare lanes overlap, lose their aligned time axis or reset horizontal zoom");
    if(font_scale==1) {
        for(const auto& plot:{position_plot,rotation_plot,framing_plot})
            require(plot.w-plot.y>=2*plots->FontRefSize && plot.y>=plots->InnerClipRect.Min.y-1 && plot.w<=plots->InnerClipRect.Max.y+1,
                    "The normal720p three-graph overview hides or collapses an entire plot");
        require(framing_plot.w+6+plots->FontRefSize<=plots->InnerClipRect.Max.y+1 && plots->ScrollMax.y<=1,
                "The normal720p overview clips its shared time ruler or requires scrolling");
    }
    activate("Frame all###Fit");
    activate("Show original###Show Before");
    const double before_value=panel.curve_before_value(0,0);
    require(panel.curve_before_revision()==7 && panel.curve_before_session()==session,
            "Show Before did not capture the current revision and session");
    camera_revision_stub=8;viewer.publish(curve_packet(10,8,session,30));viewer.tick();settle();
    require(panel.curve_before_revision()==7 && panel.curve_before_value(0,0)==before_value,
            "An ordinary edit replaced the user's Before reference");
    activate("Stack graphs###Compare");
    activate("Frame all###Fit");
    // Exercise the old curve's actual rendered location. Its pixels cannot
    // become an editable hit target or dispatch a camera selection.
    const auto reference_point=panel.curve_before_selected_point(0);
    const auto reference_plot=panel.curve_plot_rect(0);
    require(reference_point.x>=reference_plot.x && reference_point.x<=reference_plot.z &&
                reference_point.y>=reference_plot.y && reference_point.y<=reference_plot.w,
            "Before comparison does not use the current graph's value mapping");
    io.AddMousePosEvent(reference_point.x,reference_point.y);frame();frame();observed_actions.clear();
    io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(reference_point.x,reference_point.y-10);frame();frame();
    require(!panel.curve_editing(),"The read-only Before curve accepted a drag");
    io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.empty(),"The read-only Before curve dispatched a camera action");
    const auto now=panel.curve_selected_point(0);
    io.AddMousePosEvent(now.x,now.y);frame();frame();
    observed_actions.clear();
    io.AddMouseButtonEvent(0,true);frame();
    io.AddKeyEvent(ImGuiKey_Escape,true);frame();
    io.AddKeyEvent(ImGuiKey_Escape,false);io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.empty() && panel.curve_before_revision()==7 &&
                panel.curve_before_value(0,0)==before_value,
            "Cancel changed the Before snapshot or committed a curve draft");
    activate("Update original###Replace Before");
    require(panel.curve_before_revision()==8 && panel.curve_before_value(0,0)==before_value+30,
            "Replace Before did not explicitly capture the new current curve");
    snapshot.owner=dolly::EditorOwner::Flight;settle();
    for(const auto* name:{"Deadlock Dolly","##lite-campaths","##lite-timeline"}) {
        auto* hidden=ImGui::FindWindowByName(name);
        require(!hidden || !hidden->Active,"F8 flight ownership left a unified editor panel visible");
    }
    snapshot.owner=dolly::EditorOwner::Panel;settle();
    require(timeline->Active && panel.timeline_mode()==2 && panel.curve_before_revision()==8,
            "F8 return lost the open curve workspace or Before reference");
    for(const auto& action:{std::pair<const char*,dolly::EditorAction>{"< Previous",dolly::EditorAction::PreviousView},
                           {"Forward >",dolly::EditorAction::NextView},{"Redo",dolly::EditorAction::RedoShot}}) {
        activate("More");
        require(ctx->OpenPopupStack.Size>0 && ctx->OpenPopupStack.back().Window,
                "Curve transport More does not open its preserved controls");
        auto* popup=ctx->OpenPopupStack.back().Window;
        observed_actions.clear();ctx->NavNextActivateId=popup->GetID(action.first);settle();
        require(observed_actions.size()==1 && observed_actions[0].action==action.second,
                "A preserved Curves transport action changed its existing command");
        if(action.second==dolly::EditorAction::RedoShot)
            require(observed_actions[0].pose[0]==8,"Curves Redo lost its revision guard");
        ImGui::ClosePopupToLevel(0,true);settle();
    }
    snapshot.manual_active=true;snapshot.horizontal_fov=90;activate("More");
    require(ctx->OpenPopupStack.Size>0 && ctx->OpenPopupStack.back().Window,
            "Curve transport More did not open for FOV editing");
    auto* fov_popup=ctx->OpenPopupStack.back().Window;
    observed_framing_factor=0;ctx->NavNextActivateId=fov_popup->GetID("##lite-fov");
    ctx->NavNextActivateFlags=ImGuiActivateFlags_PreferInput;frame();frame();
    require(ctx->InputTextState.ID==ctx->ActiveId && ctx->ActiveId,
            "Preserved Curves FOV cannot accept a precise value");
    io.AddInputCharactersUTF8("80");frame();io.AddKeyEvent(ImGuiKey_Enter,true);frame();
    io.AddKeyEvent(ImGuiKey_Enter,false);settle();
    require(std::isfinite(observed_framing_factor) && observed_framing_factor>0 &&
                std::abs(observed_framing_factor-1)>.01,
            "Preserved Curves FOV did not request the existing framing transition");
    ImGui::ClosePopupToLevel(0,true);settle();
    viewer.publish(curve_packet(10,8,session+1));viewer.tick();settle();
    require(panel.curve_before_revision()==0,"A new shot session retained the previous shot's Before curve");
    viewer.publish(curve_packet(40,7,session+1,0,false,38));viewer.tick();
    camera_revision_stub=7;snapshot.camera_count=40;snapshot.duration=117;snapshot.selected_camera=38;settle();
    activate("Frame all###Fit");
    const auto far_key=panel.curve_selected_point(0);
    snapshot.selected_camera=0;viewer.publish(curve_packet(40,7,session+1));viewer.tick();settle();
    io.AddMousePosEvent(far_key.x,far_key.y);frame();frame();observed_actions.clear();
    io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SelectCurveCamera &&
                observed_actions[0].value==38 && observed_actions[0].pose[0]==7 &&
                observed_actions[0].pose[3]==114 && observed_actions[0].pose[4]==double(std::uint32_t(session+1)),
            "A key beyond the 32-camera page cannot be selected with full-shot identity");
    snapshot.selected_camera=38;viewer.publish(curve_packet(40,7,session+1,0,false,38));viewer.tick();settle();drag(true);
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
                observed_actions[0].value==38 && observed_actions[0].pose[3]==114,
            "A selected key beyond the 32-camera page cannot be edited");
    // One sampled edge per segment is enough to expose the historical error:
    // skipping a discontinuity erased an entire held step. Inspect triangles
    // emitted by the production graph, rather than a duplicated evaluator.
    io.DisplaySize=ImVec2(2560,1440);
    viewer.publish(curve_packet(100,7,session+2,0,true,50));viewer.tick();
    snapshot.camera_count=100;snapshot.duration=297;snapshot.selected_camera=50;settle();activate("Frame all###Fit");
    const auto held=panel.curve_selected_point(0);
    snapshot.selected_camera=51;viewer.publish(curve_packet(100,7,session+2,0,true,51));viewer.tick();settle();
    const auto arrival=panel.curve_selected_point(0);
    snapshot.selected_camera=50;viewer.publish(curve_packet(100,7,session+2,0,true,50));viewer.tick();settle();
    auto covers_curve=[&](ImVec2 point) {
        const unsigned rgb=dolly::ui::CURVE_X;
        const ImU32 color=IM_COL32((rgb>>16)&255,(rgb>>8)&255,rgb&255,255);
        const ImDrawList* draw=nullptr;
        for(const auto* child:ctx->Windows)
            if(child->Active && child->ParentWindow==timeline && std::strstr(child->Name,"##curve-plots"))
                draw=child->DrawList;
        require(draw!=nullptr,"The curve plot child is missing from the rendered workspace");
        const auto cross=[](ImVec2 a,ImVec2 b,ImVec2 p) {
            return (b.x-a.x)*(p.y-a.y)-(b.y-a.y)*(p.x-a.x);
        };
        for(int index=0;index+2<draw->IdxBuffer.Size;index+=3) {
            const auto& a=draw->VtxBuffer[draw->IdxBuffer[index]];
            const auto& b=draw->VtxBuffer[draw->IdxBuffer[index+1]];
            const auto& c=draw->VtxBuffer[draw->IdxBuffer[index+2]];
            if(a.col!=color || b.col!=color || c.col!=color)continue;
            const float ab=cross(a.pos,b.pos,point),bc=cross(b.pos,c.pos,point),ca=cross(c.pos,a.pos,point);
            if((ab>=-.01f && bc>=-.01f && ca>=-.01f) || (ab<=.01f && bc<=.01f && ca<=.01f))return true;
        }
        return false;
    };
    const float midpoint=(held.x+arrival.x)*.5f;
    require(std::abs(held.y-arrival.y)>6*font_scale && covers_curve(ImVec2(midpoint,held.y)),
            "A held step curve has a missing horizontal segment");
    require(!covers_curve(ImVec2(midpoint,(held.y+arrival.y)*.5f)),
            "A held step curve drew a diagonal interpolation across its jump");
    viewer.publish(curve_packet(10,8,session+3,0,false,0,349));viewer.tick();
    camera_revision_stub=8;snapshot.camera_count=10;snapshot.duration=27;snapshot.selected_camera=0;settle();
    activate("Rotation###curve-group-1");activate("Yaw###curve-axis-4");activate("Show original###Show Before");
    viewer.publish(curve_packet(10,9,session+3,0,false,0,-11));viewer.tick();camera_revision_stub=9;settle();activate("Frame all###Fit");
    const auto shortest_current=panel.curve_selected_point(4),shortest_before=panel.curve_before_selected_point(4);
    require(panel.curve_before_value(4,0)==359 && panel.curve_before_display_offset(4)==-360 &&
                std::abs(shortest_current.y-shortest_before.y)<.5f*font_scale,
            "Shortest-angle Before comparison invented a 360-degree visual change");
    auto before_geometry=[&] {
        const unsigned rgb=dolly::ui::CURVE_BEFORE;
        const ImU32 color=IM_COL32((rgb>>16)&255,(rgb>>8)&255,rgb&255,0)&~IM_COL32_A_MASK;
        unsigned vertices=0;
        for(const auto* child:ctx->Windows)
            if(child->Active && child->ParentWindow==timeline && std::strstr(child->Name,"##curve-plots"))
                for(const auto& vertex:child->DrawList->VtxBuffer)
                    if((vertex.col&~IM_COL32_A_MASK)==color && (vertex.col&IM_COL32_A_MASK))++vertices;
        return vertices;
    };
    require(before_geometry()>20,"Aligned Before comparison emitted no real dashed reference geometry");
    viewer.publish(curve_packet(10,10,session+3,0,false,0,-11,true));viewer.tick();camera_revision_stub=10;settle();activate("Frame all###Fit");
    const auto unwrapped_current=panel.curve_selected_point(4),unwrapped_before=panel.curve_before_selected_point(4);
    const auto angle_plot=panel.curve_plot_rect(4);
    require(panel.curve_before_value(4,0)==359 && panel.curve_before_display_offset(4)==0 &&
                std::abs(unwrapped_current.y-unwrapped_before.y)>(angle_plot.w-angle_plot.y)*.5f && before_geometry()>20,
            "Unwrapped Before comparison hid an authored full turn");
    viewer.publish(viewer_packet());viewer.tick();snapshot.camera_count=2;snapshot.selected_camera=0;snapshot.duration=3;settle();
    require(panel.curve_before_revision()==0,"Legacy viewer data retained a Before session");
    // The new dock supplements the existing two timeline domains. Exercise
    // their existing actions after leaving Curves, then return to the editor.
    activate("Replay###lite-timeline-mode");
    snapshot.follow_available=true;settle();observed_actions.clear();activate("##next-player");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::FocusReplayPlayer &&
                panel.timeline_view_end()>=146925,
            "Leaving Curves lost replay player navigation or the full replay ruler");
    activate("Campaths###lite-campath-mode");observed_actions.clear();activate("< Previous");
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::PreviousView,
            "Leaving Curves lost the existing camera view navigation");
    activate("Curves###lite-curves-mode");
    require(panel.curve_lane_count()==1 && panel.curve_group()==1,
            "Returning from Replay/Campaths lost the current curve group");
    // A persisted narrow custom timeline keeps its width/X while the normal
    // bottom dock follows mode and comparison height changes.
    viewer.publish(curve_packet(10,7,session+4));viewer.tick();camera_revision_stub=7;
    snapshot.camera_count=10;snapshot.duration=27;snapshot.selected_camera=0;settle();
    ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nCustom=1\n");
    activate("Replay###lite-timeline-mode");
    ImVec2 custom_anchor(80*font_scale,io.DisplaySize.y-300*font_scale);
    float custom_width=560*font_scale;
    const float custom_legacy_height=230*font_scale;
    float custom_bottom=io.DisplaySize.y-16*font_scale;
    ImGui::SetWindowPos(timeline,custom_anchor);ImGui::SetWindowSize(timeline,ImVec2(custom_width,custom_legacy_height));settle();
    auto require_custom_timeline=[&] {
        if(!(std::abs(timeline->Size.x-custom_width)<1 &&
             std::abs(timeline->Pos.x-custom_anchor.x)<1 &&
             std::abs(timeline->Pos.y+timeline->Size.y-custom_bottom)<1))
            std::fprintf(stderr,"Custom anchor scale%.2f mode%u expected-width%.1f x%.1f bottom%.1f actual-pos%.1f,%.1f size%.1f,%.1f full%.1f,%.1f bottom%.1f\n",
                font_scale,panel.timeline_mode(),custom_width,custom_anchor.x,custom_bottom,
                timeline->Pos.x,timeline->Pos.y,timeline->Size.x,timeline->Size.y,
                timeline->SizeFull.x,timeline->SizeFull.y,timeline->Pos.y+timeline->Size.y);
        require(std::abs(timeline->Size.x-custom_width)<1 &&
                    std::abs(timeline->Pos.x-custom_anchor.x)<1 &&
                    std::abs(timeline->Pos.y+timeline->Size.y-custom_bottom)<1,
                "Changing timeline modes reset custom width/X or detached the normal bottom dock");
        if(!(timeline->DC.CursorMaxPos.x<=timeline->InnerClipRect.Max.x+1 &&
             timeline->DC.CursorMaxPos.y<=timeline->InnerClipRect.Max.y+1 &&
             timeline->ScrollMax.x<=1 && timeline->ScrollMax.y<=1))
            std::fprintf(stderr,"Custom dock scale %.2f group %d compare %d lanes %u: pos %.1f %.1f size %.1fx%.1f cursor %.1f %.1f clipmax %.1f %.1f scrollmax %.1f %.1f\n",
                         font_scale,panel.curve_group(),int(panel.curve_compare()),panel.curve_lane_count(),
                         timeline->Pos.x,timeline->Pos.y,timeline->Size.x,timeline->Size.y,
                         timeline->DC.CursorMaxPos.x,timeline->DC.CursorMaxPos.y,
                         timeline->InnerClipRect.Max.x,timeline->InnerClipRect.Max.y,timeline->ScrollMax.x,timeline->ScrollMax.y);
        require(timeline->DC.CursorMaxPos.x<=timeline->InnerClipRect.Max.x+1 &&
                    timeline->DC.CursorMaxPos.y<=timeline->InnerClipRect.Max.y+1 &&
                    timeline->ScrollMax.x<=1 && timeline->ScrollMax.y<=1 &&
                    !timeline->ScrollbarX && !timeline->ScrollbarY,
                "A narrow custom timeline clips or scrolls its toolbar/footer controls");
        if(panel.timeline_mode()!=2) {
            const bool compact=timeline->Size.y<130*font_scale;
            const float padding=timeline->Pos.y+timeline->Size.y-timeline->DC.CursorMaxPos.y;
            if(!(padding>=12*font_scale && padding<=22*font_scale))
                std::fprintf(stderr,"Replay fit scale%.2f width%.1f height%.1f content%.1f bottom-padding%.1f\n",
                    font_scale,timeline->Size.x,timeline->Size.y,timeline->DC.CursorMaxPos.y-timeline->Pos.y,padding);
            require(padding>=12*font_scale && padding<=22*font_scale,
                    "Replay/Campaths lose normal bottom padding or retain unused editor space");
            require(compact ? timeline->Size.y<130*font_scale :
                              timeline->Size.y>160*font_scale && timeline->Size.y<220*font_scale,
                    "Replay/Campaths do not fit their active compact/full transport rows");
        }
    };
    auto custom_mode=[&](const char* name) {
        ctx->NavNextActivateId=timeline->GetID("##timeline-view");settle();
        if(ctx->OpenPopupStack.Size>0 && ctx->OpenPopupStack.back().Window)
            ctx->NavNextActivateId=ctx->OpenPopupStack.back().Window->GetID(name);
        else {
            const char* button=std::strcmp(name,"Replay")==0 ? "Replay###lite-timeline-mode" :
                std::strcmp(name,"Campaths")==0 ? "Campaths###lite-campath-mode" : "Curves###lite-curves-mode";
            ctx->NavNextActivateId=timeline->GetID(button);
        }
        settle();
        require_custom_timeline();
        if(std::strcmp(name,"Replay")==0)
            require(panel.timeline_view_end()>100000,"Custom mode navigation did not restore the Replay ruler");
        if(std::strcmp(name,"Campaths")==0)
            require(panel.timeline_view_end()<=snapshot.duration+.01,
                    "Custom mode navigation did not restore the shot-time ruler");
    };
    require_custom_timeline();
    custom_mode("Campaths");custom_mode("Replay");custom_mode("Curves");
    const float single_height=timeline->Size.y;
    activate("Stack graphs###Compare");require_custom_timeline();
    require(timeline->Size.y>single_height && panel.curve_lane_count()==3,
            "Compare cannot expand the custom dock without resetting its placement");
    require(panel.curve_lane_compact_axes(0) && panel.curve_lane_compact_axes(1),
            "The narrow stacked dock did not use its aligned compact axis fallback");
    ImGui::SetScrollY(plots,0);settle();
    const int compact_group=0;
    const auto compact_seed=ImHashData(&compact_group,sizeof(compact_group),plots->ID);
    for(const char* axis:{"Y","X"}) {
        ctx->NavNextActivateId=ImGui::GetIDWithSeed("##lane-axis",nullptr,compact_seed);settle();
        require(ctx->OpenPopupStack.Size && ctx->OpenPopupStack.back().Window,
                "The real compact stacked-axis dropdown cannot open");
        ctx->NavNextActivateId=ctx->OpenPopupStack.back().Window->GetID(axis);settle();
        if(ctx->OpenPopupStack.Size) ImGui::ClosePopupToLevel(0,true);
        settle();const unsigned selected_axis=axis[0]=='Y' ? 1u : 0u;
        require(panel.curve_plot_rect(selected_axis).z>panel.curve_plot_rect(selected_axis).x,
                "The compact stacked-axis fallback selected another channel");
    }
    observed_actions.clear();activate("View");
    require(ctx->OpenPopupStack.Size && ctx->OpenPopupStack.back().Window,
            "The narrow curve header lost its view controls");
    ctx->NavNextActivateId=ctx->OpenPopupStack.back().Window->GetID("Frame all###Fit");settle();
    require(observed_actions.empty(),"The compact Frame all view action changed authoring or replay state");
    activate("More");
    require(ctx->OpenPopupStack.Size>0 && ctx->OpenPopupStack.back().Window,
            "A narrow custom Curves dock cannot open its preserved transport controls");
    const auto* custom_popup=ctx->OpenPopupStack.back().Window;
    require(custom_popup->Pos.x>=0 && custom_popup->Pos.y>=0 &&
                custom_popup->Pos.x+custom_popup->Size.x<=io.DisplaySize.x+1 &&
                custom_popup->Pos.y+custom_popup->Size.y<=io.DisplaySize.y+1 &&
                custom_popup->DC.CursorMaxPos.x<=custom_popup->InnerClipRect.Max.x+1 &&
                custom_popup->DC.CursorMaxPos.y<=custom_popup->InnerClipRect.Max.y+1,
            "Narrow Curves More controls overflow their popup or viewport");
    ImGui::ClosePopupToLevel(0,true);settle();
    custom_mode("Replay");custom_mode("Campaths");custom_mode("Curves");
    require(panel.curve_compare() && panel.curve_lane_count()==3,
            "Returning to a custom Curves dock lost its comparison view");
    for(const float logical_width:{650.f,700.f}) {
        custom_width=logical_width*font_scale;
        ImGui::SetWindowSize(timeline,ImVec2(custom_width,timeline->Size.y));settle();
        require_custom_timeline();
        activate("Stack graphs###Compare");require_custom_timeline();
        require(!panel.curve_compare(),"A boundary-width custom dock cannot return to a single graph");
        activate("Stack graphs###Compare");require_custom_timeline();
        require(panel.curve_compare() && panel.curve_lane_count()==3,
                "A boundary-width custom dock lost its stacked comparison controls");
        require(!panel.curve_lane_compact_axes(0) && !panel.curve_lane_compact_axes(1),
                "A wider stacked dock failed to restore its inline axis buttons");
        custom_mode("Replay");custom_mode("Campaths");custom_mode("Curves");
    }
    custom_mode("Replay");
    io.DisplaySize=ImVec2(1280*font_scale,720*font_scale);settle();
    custom_bottom=io.DisplaySize.y-16*font_scale;
    ImGui::SetWindowPos(timeline,ImVec2(custom_anchor.x,custom_bottom-timeline->Size.y));settle();
    for(float logical_width:{800.f,560.f,900.f,700.f}) {
        custom_width=logical_width*font_scale;
        ImGui::SetWindowSize(timeline,ImVec2(custom_width,timeline->Size.y));settle();
        require_custom_timeline();
        custom_mode("Campaths");custom_mode("Curves");custom_mode("Replay");
        const auto header_rect=[&](const char* label) {
            const auto id=timeline->GetID(label);
            ImGui::SetFocusID(id,timeline);frame();
            require(timeline->NavLastIds[ImGuiNavLayer_Main]==id,"Adaptive-header fixture lost its real control");
            return timeline->NavRectRel[ImGuiNavLayer_Main];
        };
        const auto cinematic_rect=header_rect("Play cinematic");
        const auto previous_player_rect=header_rect("##previous-player");
        const auto next_player_rect=header_rect("##next-player");
        require(previous_player_rect.Min.x>=cinematic_rect.Max.x+4*font_scale &&
                    next_player_rect.Max.x<timeline->Size.x-timeline->WindowPadding.x,
                "An adaptive Replay header overlaps the cinematic/player controls");
        if(timeline->Size.y>160*font_scale)
            require(next_player_rect.Max.x+4*font_scale<=timeline->Size.x-timeline->WindowPadding.x-185*font_scale,
                    "A full Replay header overlaps the player selector and kill hint");
        const auto stable_size=timeline->Size,stable_pos=timeline->Pos;
        for(unsigned i=0;i<40;++i)frame();
        require(timeline->Size.x==stable_size.x && timeline->Size.y==stable_size.y &&
                    timeline->Pos.x==stable_pos.x && timeline->Pos.y==stable_pos.y,
                "Fitted Replay geometry jitters or grows again without a layout change");
    }
    // The real resize grip can cross the compact-width boundary while keeping
    // a short transport usable. Grow to full rows only after the user releases.
    auto* main=ImGui::FindWindowByName("Deadlock Dolly");
    ctx->NavNextActivateId=main->GetID("Layout");settle();
    require(ctx->OpenPopupStack.Size && ctx->OpenPopupStack.back().Window,"Resize fixture cannot open Layout");
    ctx->NavNextActivateId=ctx->OpenPopupStack.back().Window->GetID("Arrange panels");settle();
    ImGui::ClosePopupToLevel(0,true);settle();
    const auto grip=ImVec2(timeline->Pos.x+timeline->Size.x-2*font_scale,
                           timeline->Pos.y+timeline->Size.y-2*font_scale);
    io.AddMousePosEvent(grip.x,grip.y);frame();frame();io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(grip.x+150*font_scale,grip.y);frame();frame();
    require(timeline->Size.x>820*font_scale && timeline->Size.y<130*font_scale && ctx->ActiveId,
            "A real width resize loses the compact layout or resizes its height before release");
    require(timeline->DC.CursorMaxPos.y<=timeline->InnerClipRect.Max.y+1,
            "A width resize hides the compact transport's bottom controls");
    io.AddMouseButtonEvent(0,false);settle();custom_width=timeline->Size.x;
    require_custom_timeline();
    require(timeline->Size.y>160*font_scale,"Releasing a wide resize did not reveal full transport rows");
    // Arrange retains a real free-position gesture, including its horizontal
    // placement. Leaving Arrange reattaches only Y to the normal bottom dock.
    const auto drag_origin=timeline->Pos;
    const auto handle=ImVec2(timeline->Pos.x+timeline->Size.x*.5f,timeline->Pos.y+7*font_scale);
    io.AddMousePosEvent(handle.x,handle.y);frame();frame();io.AddMouseButtonEvent(0,true);frame();
    io.AddMousePosEvent(handle.x+24*font_scale,handle.y-48*font_scale);frame();frame();
    io.AddMouseButtonEvent(0,false);settle();
    require(std::abs(timeline->Pos.x-drag_origin.x-24*font_scale)<1 &&
                std::abs(timeline->Pos.y-drag_origin.y+48*font_scale)<1,
            "Arrange no longer allows an explicit free timeline drag");
    custom_anchor.x=timeline->Pos.x;custom_bottom=timeline->Pos.y+timeline->Size.y;
    require_custom_timeline();
    ctx->NavNextActivateId=main->GetID("Layout");settle();
    require(ctx->OpenPopupStack.Size && ctx->OpenPopupStack.back().Window,"Arrange exit fixture cannot open Layout");
    ctx->NavNextActivateId=ctx->OpenPopupStack.back().Window->GetID("Arrange panels");settle();
    ImGui::ClosePopupToLevel(0,true);settle();
    custom_bottom=io.DisplaySize.y-16*font_scale;require_custom_timeline();
    custom_mode("Curves");custom_mode("Campaths");custom_mode("Replay");
    // A narrow default viewport must use the same compact policy, independently
    // of a saved custom width or the screen-height threshold.
    ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nCustom=0\nReducedMotion=1\n");
    io.DisplaySize=ImVec2(720*font_scale,720*font_scale);settle();
    require(timeline->Size.y<130*font_scale &&
                std::abs(io.DisplaySize.y-timeline->Pos.y-timeline->Size.y-16*font_scale)<1 &&
                timeline->DC.CursorMaxPos.y<=timeline->InnerClipRect.Max.y+1,
            "A default narrow Replay dock reserves full-height editor space or loses bottom controls");
    io.IniFilename=nullptr;ImGui::DestroyContext(ctx);ImGui::SetCurrentContext(previous);
    dolly::recording_status_stub.state=original_recording_state;
    camera_revision_stub=original_camera_revision;camera_first_stub=original_camera_first;
    camera_time_bias_stub=original_camera_time_bias;
    snapshot=original;viewer.publish(viewer_packet());viewer.tick();
    std::puts("Unified curves: group/channel edits, release-only session payloads, guards/cancel, aligned Compare, stable Before and full-shot selection passed.");
}
static void tangent_ui_regressions(SyntheticViewer& viewer,float font_scale) {
    constexpr std::uint64_t session=0x12345678aabbccddULL;
    const auto original=snapshot;const auto original_revision=camera_revision_stub;
    const auto original_recording=dolly::recording_status_stub.state;
    auto* previous=ImGui::GetCurrentContext();auto* ctx=ImGui::CreateContext();
    ImGui::SetCurrentContext(ctx);auto& io=ImGui::GetIO();
    io.IniFilename=nullptr;io.DisplaySize=ImVec2(1920,1080);io.DeltaTime=1.f/60;
    io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
    dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(font_scale);
    ImGui::GetStyle().ScaleAllSizes(font_scale);io.Fonts->Build();
    snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
    snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
    snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=10;snapshot.selected_camera=4;snapshot.duration=27;
    camera_revision_stub=7;dolly::recording_status_stub.state=dolly::video::State::idle;
    auto frame=[&]{ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_campaths(snapshot);
                  panel.draw_lite_timeline(snapshot);ImGui::Render();};
    auto settle=[&]{for(int i=0;i<5;++i)frame();};
    auto publish=[&](unsigned mode=0,unsigned revision=7,std::uint64_t identity=0x12345678aabbccddULL,double value=5) {
        camera_revision_stub=revision;
        viewer.publish(curve_packet(10,revision,identity,0,false,snapshot.selected_camera,0,false,false,3,0,nullptr,nullptr,true,mode,value));
        viewer.tick();settle();
    };
    publish();auto* timeline=ImGui::FindWindowByName("##lite-timeline");
    ctx->NavNextActivateId=timeline->GetID("Curves###lite-curves-mode");settle();
    auto press_handle=[&](unsigned side) {
        const auto point=panel.curve_tangent_point(side),key=panel.curve_selected_point(0);
        require(point.x>0 && std::hypot(point.x-key.x,point.y-key.y)<=56.1f*font_scale &&
                std::hypot(point.x-key.x,point.y-key.y)>=18.f*font_scale,
                "XYZ tangent arm is missing or longer than the compact visual bound");
        io.AddMousePosEvent(point.x,point.y);frame();frame();observed_actions.clear();observed_previews.clear();
        io.AddMouseButtonEvent(0,true);frame();require(panel.curve_editing(),"Round XYZ tangent tip cannot start a gesture");
        return point;
    };
    auto point=press_handle(1);io.AddMousePosEvent(point.x,point.y-10*font_scale);
    for(unsigned i=0;i<12;++i)frame();
    require(observed_actions.empty() && observed_previews.size()>=2 &&
            observed_previews.back().action==dolly::EditorAction::PreviewCameraPositionTangents &&
            observed_previews.back().pose[2]>5 && observed_previews.back().pose[6]>0,
            "A held native handle did not stream guarded stationary preview heartbeats before committing");
    io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraPositionTangents &&
            observed_actions[0].value==51 && observed_actions[0].pose[1]==5 && observed_actions[0].pose[2]>5 &&
            observed_actions[0].pose[0]==7 && observed_actions[0].pose[3]==12 &&
            observed_actions[0].pose[4]==double(std::uint32_t(session)) &&
            observed_actions[0].pose[5]==double(std::uint32_t(session>>32)),
            "Auto XYZ handle did not send one independent atomic pair with complete identity");
    require(observed_previews.back().action==dolly::EditorAction::ClearCameraCurvePreview,
            "Native handle release did not terminate its render-only preview");
    publish(2);point=press_handle(1);io.AddMousePosEvent(point.x,point.y-10*font_scale);frame();frame();
    io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].value==50 &&
            observed_actions[0].pose[1]==observed_actions[0].pose[2] && observed_actions[0].pose[1]>5,
            "Linked XYZ handle did not retain equal arrival/departure slopes");
    publish(3);point=press_handle(0);io.AddMousePosEvent(point.x,point.y-10*font_scale);frame();frame();
    io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].value==51 &&
            observed_actions[0].pose[1]<5 && observed_actions[0].pose[2]==5,
            "Broken arrival handle changed departure or the slope sign");
    publish(3,7,session,3.1234567890123);
    const auto precise_field=panel.curve_tangent_value_rect(0);
    io.AddMousePosEvent((precise_field.x+precise_field.z)*.5f,(precise_field.y+precise_field.w)*.5f);frame();frame();
    observed_actions.clear();io.AddKeyEvent(ImGuiMod_Ctrl,true);frame();io.AddMouseButtonEvent(0,true);frame();
    io.AddMouseButtonEvent(0,false);frame();io.AddKeyEvent(ImGuiMod_Ctrl,false);frame();
    require(ctx->ActiveId && ctx->InputTextState.ID==ctx->ActiveId,"Fractional tangent cannot open precise input");
    io.AddKeyEvent(ImGuiKey_Enter,true);frame();io.AddKeyEvent(ImGuiKey_Enter,false);settle();
    require(observed_actions.empty() && !panel.curve_editing(),
            "Untouched fractional tangent focus/Enter rounded the original or dispatched an Undoable edit");
    publish();point=press_handle(1);io.AddMousePosEvent(point.x,point.y-8*font_scale);frame();
    io.AddMouseButtonEvent(1,true);frame();io.AddMouseButtonEvent(1,false);io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.empty() && !panel.curve_editing(),"Right-click committed an XYZ handle draft");
    for(bool new_session:{false,true}) {
        publish();point=press_handle(1);io.AddMousePosEvent(point.x,point.y-8*font_scale);frame();
        publish(0,new_session ? 7 : 8,new_session ? session+1 : session);
        io.AddMouseButtonEvent(0,false);settle();
        require(observed_actions.empty() && !panel.curve_editing(),"Changed revision/session committed a tangent draft");
    }
    publish();auto mode_rect=panel.curve_tangent_mode_rect();
    require(mode_rect.z>mode_rect.x,"XYZ tangent mode control is missing");
    io.AddMousePosEvent((mode_rect.x+mode_rect.z)*.5f,(mode_rect.y+mode_rect.w)*.5f);frame();
    io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);settle();
    require(ctx->OpenPopupStack.Size && ctx->OpenPopupStack.back().Window,"XYZ tangent mode menu cannot open");
    observed_actions.clear();ctx->NavNextActivateId=ctx->OpenPopupStack.back().Window->GetID("Flat");settle();
    require(observed_actions.size()==1 && observed_actions[0].value==49 &&
            observed_actions[0].pose[1]==0 && observed_actions[0].pose[2]==0,
            "Flat mode did not send a slope-free atomic reset");
    publish(3);const auto numeric=panel.curve_tangent_value_rect(0);
    io.AddMousePosEvent((numeric.x+numeric.z)*.5f,(numeric.y+numeric.w)*.5f);frame();frame();
    io.AddKeyEvent(ImGuiMod_Ctrl,true);frame();io.AddMouseButtonEvent(0,true);frame();
    io.AddMouseButtonEvent(0,false);frame();io.AddKeyEvent(ImGuiMod_Ctrl,false);frame();
    require(ctx->ActiveId && ctx->InputTextState.ID==ctx->ActiveId,"Arrival slope cannot accept precise numeric input");
    observed_actions.clear();io.AddInputCharactersUTF8("12.12345678901234");frame();
    require(observed_actions.empty() && panel.curve_editing(),"Typing a slope committed before field completion");
    io.AddKeyEvent(ImGuiKey_Enter,true);frame();io.AddKeyEvent(ImGuiKey_Enter,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].value==51 &&
            observed_actions[0].pose[1]==12.12345678901234 && observed_actions[0].pose[2]==5,
            "Precise arrival slope lost double precision, changed departure or dispatched twice");
    publish();const auto key=panel.curve_selected_point(0);
    io.AddMousePosEvent(key.x,key.y);frame();frame();observed_actions.clear();observed_previews.clear();
    io.AddMouseButtonEvent(0,true);frame();io.AddMousePosEvent(key.x,key.y-10*font_scale);
    for(unsigned i=0;i<12;++i)frame();
    require(observed_actions.empty() && observed_previews.size()>=2 &&
            observed_previews.back().action==dolly::EditorAction::PreviewCameraCurveValue &&
            observed_previews.back().pose[1]==0 && observed_previews.back().pose[6]>0,
            "Native key drag did not preview without committing the saved camera");
    const auto drafted_value=observed_previews.back().pose[2];
    io.AddMouseButtonEvent(0,false);settle();
    require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetCameraCurveValue &&
            observed_actions[0].pose[2]==drafted_value &&
            observed_previews.back().action==dolly::EditorAction::ClearCameraCurvePreview,
            "Native key release did not commit exactly the previewed value and terminate preview");
    for(unsigned gate=0;gate<4;++gate) {
        publish();const auto tip=panel.curve_tangent_point(1);
        if(gate==0)snapshot.paused=false;
        if(gate==1)snapshot.attach_preview=true;
        if(gate==2)dolly::recording_status_stub.state=dolly::video::State::starting;
        if(gate==3)snapshot.selected_camera=1;
        if(gate==3)publish();else settle();
        observed_actions.clear();io.AddMousePosEvent(tip.x,tip.y);frame();
        io.AddMouseButtonEvent(0,true);frame();io.AddMousePosEvent(tip.x,tip.y-10);frame();
        io.AddMouseButtonEvent(0,false);settle();
        require(observed_actions.empty() && !panel.curve_editing(),"Blocked/attached XYZ handle changed authoring");
        snapshot.paused=true;snapshot.attach_preview=false;snapshot.selected_camera=4;
        dolly::recording_status_stub.state=dolly::video::State::idle;
    }
    publish();auto dormant=curve_packet(10,7,session,0,false,4,0,false,false,3,0,nullptr,nullptr,true,3);
    dormant[56]&=~2u;viewer.publish(dormant);viewer.tick();settle();
    require(panel.curve_tangent_point(0).x==0 && panel.curve_tangent_point(1).x==0 &&
            panel.curve_tangent_mode_rect().z==0,"Dormant XYZ handles are editable outside Smooth");
    ImGui::DestroyContext(ctx);ImGui::SetCurrentContext(previous);snapshot=original;
    camera_revision_stub=original_revision;dolly::recording_status_stub.state=original_recording;
    observed_actions.clear();viewer.publish(viewer_packet());viewer.tick();
}

static void timeline_bottom_dock_regressions() {
    auto* previous=ImGui::GetCurrentContext();const auto original=snapshot;
    bool docked=true;
    for(float scale:{1.f,1.5f}) for(float logical_width:{560.f,1120.f})
        for(float saved_height:{260.f,logical_width<760 ? 110.f : 190.f}) {
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=ImVec2(1280*scale,720*scale);io.DeltaTime=1.f/60;
        io.IniFilename=nullptr;
        dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(scale);
        ImGui::GetStyle().ScaleAllSizes(scale);io.Fonts->Build();
        char saved[512]{};
        const float saved_x=(logical_width<760 ? 400.f : 80.f)*scale;
        // Reproduce an existing layout floating 64 logical pixels above the
        // viewport, including the first frame before an ImGui window exists.
        std::snprintf(saved,sizeof(saved),"[Window][##lite-timeline]\nPos=%d,%d\nSize=%d,%d\nCollapsed=0\n\n[DollyUI][Preferences]\nCustom=1\nReducedMotion=1\n",
            int(saved_x),int((720-64-saved_height)*scale),int(logical_width*scale),int(saved_height*scale));
        ImGui::LoadIniSettingsFromMemory(saved);
        snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
        snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
        snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=2;
        for(unsigned frame=0;frame<8;++frame) {
            ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_timeline(snapshot);ImGui::Render();
            const auto* timeline=ImGui::FindWindowByName("##lite-timeline");
            const float gap=io.DisplaySize.y-timeline->Pos.y-timeline->Size.y;
            const bool correct=std::abs(gap-16*scale)<1 &&
                std::abs(timeline->Pos.x-saved_x)<1 && std::abs(timeline->Size.x-logical_width*scale)<1;
            docked&=correct;
            if(frame==0 || frame==7)
                std::fprintf(stderr,"Saved bottom dock scale%.2f width%.0f saved-height%.0f frame%u gap%.1f expected%.1f pos%.1f,%.1f size%.1f,%.1f %s\n",
                    scale,logical_width,saved_height,frame,gap,16*scale,timeline->Pos.x,timeline->Pos.y,
                    timeline->Size.x,timeline->Size.y,correct ? "PASS" : "FAIL");
        }
        for(float logical_height:{900.f,600.f,720.f}) {
            io.DisplaySize.y=logical_height*scale;
            for(unsigned frame=0;frame<5;++frame) {
                ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_timeline(snapshot);ImGui::Render();
                const auto* timeline=ImGui::FindWindowByName("##lite-timeline");
                const float gap=io.DisplaySize.y-timeline->Pos.y-timeline->Size.y;
                const bool correct=std::abs(gap-16*scale)<1 &&
                    std::abs(timeline->Pos.x-saved_x)<1 && std::abs(timeline->Size.x-logical_width*scale)<1;
                docked&=correct;
                if(!correct)
                    std::fprintf(stderr,"Viewport bottom dock scale%.2f width%.0f viewport-height%.0f frame%u gap%.1f expected%.1f pos%.1f,%.1f size%.1f,%.1f FAIL\n",
                        scale,logical_width,logical_height,frame,gap,16*scale,timeline->Pos.x,timeline->Pos.y,
                        timeline->Size.x,timeline->Size.y);
            }
        }
        ImGui::DestroyContext(ctx);
    }
    ImGui::SetCurrentContext(previous);snapshot=original;
    require(docked,"A saved floating timeline fails to attach 16 scaled pixels above the viewport bottom");
}
static void timeline_saved_height_regressions(float scale) {
    auto* previous=ImGui::GetCurrentContext();const auto original=snapshot;
    for(float logical_width:{560.f,1120.f}) {
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=ImVec2(1280*scale,720*scale);io.DeltaTime=1.f/60;
        io.IniFilename=nullptr;
        dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(scale);
        ImGui::GetStyle().ScaleAllSizes(scale);io.Fonts->Build();
        char saved[512]{};
        std::snprintf(saved,sizeof(saved),"[Window][##lite-timeline]\nPos=%d,%d\nSize=%d,%d\nCollapsed=0\n\n[DollyUI][Preferences]\nCustom=1\nReducedMotion=1\n",
            int(80*scale),int(180*scale),int(logical_width*scale),int(260*scale));
        ImGui::LoadIniSettingsFromMemory(saved);
        require(!ImGui::FindWindowByName("##lite-timeline") &&
                    ImGui::FindWindowSettingsByID(ImHashStr("##lite-timeline")),
                "Saved-height fixture did not exercise settings before the first window exists");
        snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
        snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
        snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=2;
        for(unsigned frame=0;frame<8;++frame) {
            ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_timeline(snapshot);ImGui::Render();
            const auto* timeline=ImGui::FindWindowByName("##lite-timeline");
            const float bottom=timeline->Pos.y+timeline->Size.y;
            require(std::abs(timeline->Pos.x-80*scale)<1 &&
                        std::abs(timeline->Size.x-logical_width*scale)<1 && std::abs(bottom-(io.DisplaySize.y-16*scale))<1,
                    "First-load fitting changes saved width/X or detaches the normal bottom dock");
            if(frame>=3) {
                const float padding=bottom-timeline->DC.CursorMaxPos.y;
                std::printf("Fresh Replay fit scale%.2f width%.0f height%.1f content-bottom%.1f padding%.1f\n",
                    scale,logical_width,timeline->Size.y,timeline->DC.CursorMaxPos.y-timeline->Pos.y,padding);
                require(padding>=12*scale && padding<=22*scale &&
                            timeline->DC.CursorMaxPos.y<=timeline->InnerClipRect.Max.y+1 &&
                            timeline->Size.y<(logical_width<760 ? 130.f : 220.f)*scale,
                        "First-load Replay retains reserved graph space or loses bottom controls/padding");
            }
        }
        ImGui::DestroyContext(ctx);
    }
    ImGui::SetCurrentContext(previous);snapshot=original;
}
static void dropdown_preview_regressions(float font_scale) {
    auto* previous=ImGui::GetCurrentContext();
    const auto original=snapshot;
    const auto original_recording=dolly::recording_status_stub.state;
    wchar_t old_layout[32768]{};
    const auto old_layout_length=GetEnvironmentVariableW(L"DOLLY_UI_LAYOUT_FILE",old_layout,DWORD(std::size(old_layout)));
    require(old_layout_length<std::size(old_layout),"Dropdown fixture cannot preserve its layout override");
    require(SetEnvironmentVariableW(L"DOLLY_UI_LAYOUT_FILE",L"none"),"Dropdown fixture cannot isolate layout storage");
    unsigned export_cases=0;
    for(float main_width:{640.f,940.f}) for(unsigned arrangement=0;arrangement<3;++arrangement) {
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=ImVec2(1280*font_scale,720*font_scale);io.DeltaTime=1.f/60;
        io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
        dolly::OverlayPanel panel;panel.style_panel();panel.initialize_layout();panel.load_panel_fonts(font_scale);
        ImGui::GetStyle().ScaleAllSizes(font_scale);io.Fonts->Build();
        ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nEditorCustom=1\nReducedMotion=1\n");
        if(arrangement==1)
            ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nWidths4=0.2,3,1\n");
        if(arrangement==2)
            ImGui::LoadIniSettingsFromMemory("[DollyUI][Preferences]\nSections4=3,0,1,2\nColumns4=2,0,1,2\nWidths4=3,0.2,1\n");
        snapshot.enabled=snapshot.focused=snapshot.ready=snapshot.paused=true;
        snapshot.busy=snapshot.playing=snapshot.attach_preview=snapshot.bone_picker=snapshot.object_picker=false;
        snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=2;snapshot.selected_camera=0;
        snapshot.duration=3;snapshot.playback_speed=.25;snapshot.video_pov=false;
        snapshot.pov_duration=5;
        dolly::recording_status_stub.state=dolly::video::State::idle;
        auto frame=[&] {ImGui::NewFrame();panel.draw_panel(snapshot);panel.draw_lite_timeline(snapshot);
                       ImGui::Render();panel.flush_layout();};
        auto settle=[&](unsigned count=12) {for(unsigned i=0;i<count;++i)frame();};settle();
        auto* main=ImGui::FindWindowByName("Deadlock Dolly");
        require(main && main->Active,"Dropdown fixture lost the main menu");
        // Ask the production Begin() constraints for its true supported
        // minimum height rather than guessing a logical minimum ourselves.
        ImGui::SetWindowSize(main,ImVec2(main_width*font_scale,main_width==640 ? 1.f : 520*font_scale));settle();
        require(std::abs(main->Size.x-main_width*font_scale)<1,"Dropdown fixture missed the requested main width");
        auto activate=[&](ImGuiWindow* window,const char* label) {
            require(window && window->Active,"Dropdown fixture cannot activate a missing window");
            ctx->NavNextActivateId=window->GetID(label);settle();
        };
        auto card=[&](const char* label) -> ImGuiWindow* {
            for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow &&
                    std::strstr(window->ParentWindow->Name,"##editor-content") && std::strstr(window->Name,label))return window;
            return nullptr;
        };
        auto reveal=[&](ImGuiWindow* window) {
            require(window && window->ParentWindow,"Dropdown fixture has no card to reveal");
            auto* content=window->ParentWindow;
            ImGui::SetScrollY(content,std::max(0.f,content->Scroll.y+window->Pos.y-content->InnerClipRect.Min.y));settle();
        };
        auto open_combo=[&](ImGuiWindow* window,const char* id) -> ImGuiWindow* {
            activate(window,id);
            require(ctx->OpenPopupStack.Size==1 && ctx->OpenPopupStack.back().Window &&
                        ctx->OpenPopupStack.back().Window->Active,"A real dropdown control cannot open its popup");
            return ctx->OpenPopupStack.back().Window;
        };
        auto preview_fits=[&](ImGuiWindow* window,const char* id,const char* preview,float horizontal_padding) {
            const ImRect local=window->NavRectRel[ImGuiNavLayer_Main];
            const ImRect rect(ImVec2(window->Pos.x+local.Min.x,window->Pos.y+local.Min.y),
                              ImVec2(window->Pos.x+local.Max.x,window->Pos.y+local.Max.y));
            const float text=io.FontDefault->CalcTextSizeA(window->FontRefSize,FLT_MAX,0,preview).x;
            const float needed=text+rect.GetHeight()+2*horizontal_padding;
            if(!(window->NavLastIds[ImGuiNavLayer_Main]==window->GetID(id) && rect.GetWidth()+.1f>=needed))
                std::fprintf(stderr,"Dropdown preview %s scale %.2f main %.0f arrangement %u: control %.2f text %.2f arrow %.2f padding %.2f font %.2f\n",
                    id,font_scale,main_width,arrangement,rect.GetWidth(),text,rect.GetHeight(),horizontal_padding,window->FontRefSize);
            require(window->NavLastIds[ImGuiNavLayer_Main]==window->GetID(id) && rect.GetWidth()+.1f>=needed,
                    "A dropdown preview clips full text against its real arrow and frame padding");
            require(rect.Min.x>=window->InnerClipRect.Min.x-1 && rect.Max.x<=window->InnerClipRect.Max.x+1,
                    "A dropdown frame escapes its own visible card");
        };
        activate(main,"Export###EXPORT");
        auto* source=card("##export-camera");require(source,"Export reflow lost Camera source");
        reveal(source);source=card("##export-camera");
        auto* popup=open_combo(source,"##export-source");
        // The source card's explicit padding is the actual effective workspace
        // scale even if another page uses compact content scaling.
        preview_fits(source,"##export-source","Camera path / Bone camera",source->WindowPadding.x*10.f/12.f);
        ImGui::ClosePopupToLevel(0,true);settle();
        for(int target:{1,0}) {
            source=card("##export-camera");reveal(source);
            popup=open_combo(source,"##export-source");
            observed_actions.clear();
            const char* label=target ? "Player POV" : "Camera path / Bone camera";
            ctx->NavNextActivateId=ImHashStr(label,0,popup->GetID(target));settle();
            require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetVideoSource &&
                        observed_actions[0].value==target && ctx->OpenPopupStack.empty(),
                    "Export source popup changed its original0/1 command mapping");
            snapshot.video_pov=target!=0;settle();
        }
        source=card("##export-camera");auto* content=source->ParentWindow;
        unsigned cards=0,columns=0;float x[4]{};
        for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow==content) {
            bool seen=false;for(unsigned i=0;i<columns;++i)seen|=std::abs(x[i]-window->Pos.x)<1;
            if(!seen)x[columns++]=window->Pos.x;
            ++cards;
            require(window->ScrollMax.y<=1 && !window->ScrollbarY &&
                        window->DC.CursorMaxPos.y<=window->Pos.y+window->Size.y+1,
                    "Export reflow internally clips or scrolls a control card");
        }
        require(cards==4,"Export reflow removed one of its existing control cards");
        if(main_width==940 && arrangement==0)require(columns>=2,"Normal-width Export collapses unnecessarily to one column");
        std::vector<ImVec2> stable;
        for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow==content)stable.push_back(window->Size);
        settle(90);unsigned index=0;
        for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow==content) {
            require(index<stable.size() && std::abs(window->Size.x-stable[index].x)<1 &&
                        std::abs(window->Size.y-stable[index].y)<1.1f,
                    "Export column reflow or automatic card measurement never settles");
            ++index;
        }
        require(index==stable.size(),"Export reflow changes its card count after settling");
        ImGui::SetScrollY(content,0);settle();
        float bottom=0;for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow==content)
            bottom=std::max(bottom,window->Pos.y+window->Size.y);
        if(bottom>content->InnerClipRect.Max.y+1) {
            require(content->ScrollMax.y>0 && !(content->Flags&ImGuiWindowFlags_NoScrollWithMouse),
                    "Minimum-height Export has controls below the view without scrolling");
            io.AddMousePosEvent(content->InnerClipRect.GetCenter().x,content->InnerClipRect.GetCenter().y);frame();frame();
            for(unsigned step=0;step<20;++step) {io.AddMouseWheelEvent(0,-10);frame();frame();}settle();
            require(content->Scroll.y>=content->ScrollMax.y-1,"Wheel input cannot reach the final Export control card");
            bottom=0;for(auto* window:ctx->Windows)if(window->Active && window->ParentWindow==content)
                bottom=std::max(bottom,window->Pos.y+window->Size.y);
            require(bottom<=content->InnerClipRect.Max.y+1,"Final Export card remains clipped at the end of scrolling");
        }
        require(main->DC.CursorMaxPos.y<=main->InnerClipRect.Max.y+1,"Export scrolling covers the fixed footer");
        ++export_cases;
        if(main_width==640 && arrangement==0) {
            activate(main,"Camera###CAMERA");auto* camera=card("##cameras-card");
            for(double target:{.5,.25}) {
                reveal(camera);popup=open_combo(camera,"##playback-speed");
                char preview[24]{};std::snprintf(preview,sizeof(preview),"%.3g x",snapshot.playback_speed);
                preview_fits(camera,"##playback-speed",preview,camera->WindowPadding.x*10.f/12.f);
                char label[24]{};std::snprintf(label,sizeof(label),"%.3g x",target);
                observed_actions.clear();ctx->NavNextActivateId=popup->GetID(label);settle();
                require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetPlaybackSpeed &&
                            observed_actions[0].value==target,"Camera speed dropdown changed its fractional speed command");
                snapshot.playback_speed=target;settle();
            }
            auto* timeline=ImGui::FindWindowByName("##lite-timeline");activate(timeline,"Curves###lite-curves-mode");
            require(panel.timeline_mode()==2,"Fractional speed fixture did not enter Curves");
            for(double target:{.5,.25}) {
                popup=open_combo(timeline,"##curve-speed");
                char preview[24]{};std::snprintf(preview,sizeof(preview),"%gx",snapshot.playback_speed);
                preview_fits(timeline,"##curve-speed",preview,ImGui::GetStyle().FramePadding.x);
                char label[24]{};std::snprintf(label,sizeof(label),"%gx",target);
                observed_actions.clear();ctx->NavNextActivateId=popup->GetID(label);settle();
                require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetPlaybackSpeed &&
                            observed_actions[0].value==target,"Curve speed dropdown changed its fractional speed command");
                snapshot.playback_speed=target;settle();
            }
            require(timeline->DC.CursorMaxPos.y<=timeline->InnerClipRect.Max.y+1,
                    "Widening fractional curve speeds pushes transport below its dock");
        }
        require(io.IniFilename==nullptr,"Dropdown fixture opened user profile persistence");
        ImGui::DestroyContext(ctx);
    }
    require(export_cases==6,"Dropdown fixture missed a width/arrangement case");
    require(SetEnvironmentVariableW(L"DOLLY_UI_LAYOUT_FILE",old_layout_length ? old_layout : nullptr),
            "Dropdown fixture cannot restore its layout override");
    ImGui::SetCurrentContext(previous);snapshot=original;dolly::recording_status_stub.state=original_recording;
    observed_actions.clear();
    std::printf("Dropdown previews scale %.2f: full Export labels, source0/1 actions, minimum-width/weighted/reordered scroll access, and fractional Camera/Curves speeds passed.\n",font_scale);
}
static void shader_notice_regressions(float scale) {
    auto* previous=ImGui::GetCurrentContext();const auto original=snapshot;
    const bool original_loading=shader_loading_stub;
    const auto original_first=camera_first_stub;
    for(const auto viewport:{ImVec2(640,480),ImVec2(1280,720),ImVec2(2560,1440)}) {
        auto* ctx=ImGui::CreateContext();ImGui::SetCurrentContext(ctx);
        auto& io=ImGui::GetIO();io.DisplaySize=viewport;io.DeltaTime=1.f/60;io.IniFilename=nullptr;
        io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
        dolly::OverlayPanel panel;panel.style_panel();panel.load_panel_fonts(scale);
        ImGui::GetStyle().ScaleAllSizes(scale);io.Fonts->Build();
        snapshot.focused=true;snapshot.enabled=false;snapshot.owner=dolly::EditorOwner::Disabled;
        snapshot.camera_count=2;camera_first_stub=0;shader_loading_stub=true;
        ImGuiWindow* sentinel=nullptr;std::string notice_text;
        auto frame=[&](bool capture_text=false) {
            io.AddMousePosEvent(viewport.x*.5f,viewport.y*.7f);
            ImGui::NewFrame();
            ImGui::SetNextWindowPos(ImVec2(4*scale,4*scale),ImGuiCond_Always);
            ImGui::SetNextWindowSize(ImVec2(100*scale,50*scale),ImGuiCond_Always);
            ImGui::Begin("##shader-notice-focus-sentinel",nullptr,ImGuiWindowFlags_NoDecoration|
                ImGuiWindowFlags_NoSavedSettings|ImGuiWindowFlags_NoMove|ImGuiWindowFlags_NoResize);
            sentinel=ImGui::GetCurrentWindow();ImGui::TextUnformatted("Focus owner");ImGui::End();
            ImGui::FocusWindow(sentinel);
            const auto colors=ctx->ColorStack.Size,vars=ctx->StyleVarStack.Size,fonts=ctx->FontStack.Size;
            const auto font=ImGui::GetFont();const float font_size=ImGui::GetFontSize();
            if(capture_text) {ctx->LogBuffer.clear();ImGui::LogToBuffer();}
            if(panel.shader_notice_visible(snapshot))panel.draw_shader_notice();
            if(capture_text) {notice_text=ctx->LogBuffer.c_str();ImGui::LogFinish();}
            require(ctx->ColorStack.Size==colors && ctx->StyleVarStack.Size==vars && ctx->FontStack.Size==fonts &&
                        ImGui::GetFont()==font && ImGui::GetFontSize()==font_size,
                    "Shader notice leaks theme or font state into the next UI");
            ImGui::Render();
        };
        for(unsigned i=0;i<5;++i)frame();frame(true);
        auto* notice=ImGui::FindWindowByName("##shader-loading");
        require(panel.shader_notice_visible(snapshot) && notice && notice->Active,
                "Shader preload notice disappears with the Dolly menu disabled");
        require((notice->Flags&ImGuiWindowFlags_NoInputs)==ImGuiWindowFlags_NoInputs &&
                    (notice->Flags&ImGuiWindowFlags_NoSavedSettings) &&
                    (notice->Flags&ImGuiWindowFlags_NoFocusOnAppearing) && ctx->NavWindow==sentinel &&
                    ctx->HoveredWindow!=notice && ctx->ActiveIdWindow!=notice && !io.WantCaptureMouse,
                "Shader notice takes focus, pointer input, or persistent workspace state");
        require(notice->Pos.x>=0 && notice->Pos.y>=0 &&
                    notice->Pos.x+notice->Size.x<=viewport.x+1 && notice->Pos.y+notice->Size.y<=viewport.y+1 &&
                    notice->DC.CursorMaxPos.x<=notice->InnerClipRect.Max.x+1 &&
                    notice->DC.CursorMaxPos.y<=notice->InnerClipRect.Max.y+1,
                "Shader notice or its full text is clipped at a supported viewport/DPI");
        require(notice->WindowPadding.x>=16*scale && notice->WindowPadding.y>=12*scale,
                "Shader notice loses readable padding as UI scale increases");
        require(std::abs(notice->Pos.x+notice->Size.x*.5f-viewport.x*.5f)<=1 &&
                    std::abs(notice->Pos.y+notice->Size.y-viewport.y*.75f)<=1,
                "Shader notice no longer uses its established bottom-middle anchor");
        require(notice_text.find("Deadlock Dolly")!=std::string::npos &&
                    notice_text.find("Loading shaders")!=std::string::npos &&
                    notice_text.find("Your replay will open automatically.")!=std::string::npos &&
                    notice_text.find('%')==std::string::npos,
                "Shader notice loses its identity/action explanation or invents a completion percentage");
        const auto notice_size=notice->Size;
        for(unsigned i=0;i<20;++i)frame();
        require(notice->Size.x==notice_size.x && notice->Size.y==notice_size.y,
                "Static shader notice layout changes as time elapses without new loading evidence");
        snapshot.focused=false;
        require(!panel.shader_notice_visible(snapshot),"Unfocused game retains the loading notice");
        frame();require(!notice->Active,"Loading notice pixels remain after focus is lost");
        snapshot.focused=true;frame();
        require(notice->Active && ctx->NavWindow==sentinel,"Loading notice does not restore without stealing focus");
        shader_loading_stub=false;
        require(!panel.shader_notice_visible(snapshot),"Finished preload retains the loading notice gate");
        frame();require(!notice->Active,"Loading notice survives completed preload while menu stays disabled");
        snapshot.enabled=true;snapshot.owner=dolly::EditorOwner::Panel;frame();
        require(!notice->Active && !panel.shader_notice_visible(snapshot),
                "Opening the Dolly menu invents a new shader-loading phase");
        const char* saved=ImGui::SaveIniSettingsToMemory();
        require(std::strstr(saved,"shader-loading")==nullptr && io.IniFilename==nullptr,
                "Loading notice becomes a saved user workspace window");
        ImGui::DestroyContext(ctx);
    }
    ImGui::SetCurrentContext(previous);snapshot=original;shader_loading_stub=original_loading;
    camera_first_stub=original_first;
    std::printf("Shader notice scale %.2f: three viewports, complete padded text, phase/focus gate, disabled-menu fallback, no input/focus/persistence, and balanced theme/fonts passed.\n",scale);
}
#include "rail_regressions.hpp"
int main(int argc, char** argv) {
    std::setvbuf(stdout,nullptr,_IONBF,0);
    try {
        if (argc>=2 && std::strcmp(argv[1],"--rail-check")==0) {
            rail_regressions(argc==3 ? argv[2] : nullptr);
            return 0;
        }
        if (argc == 3 && std::strcmp(argv[1], "--rail-motion") == 0) {
            rail_regressions(argv[2], true);
            return 0;
        }
        const bool rail_hook = argc==2 && std::strcmp(argv[1],"--rail-hook-check")==0;
        // Existing geometry/curve regressions exercise the supported classic fallback.
        SetEnvironmentVariableA("DOLLY_OVERLAY_UI", rail_hook ? nullptr : "classic");
        if(argc==2 && std::strcmp(argv[1],"--layout-crash-fixture")==0)layout_crash_child();
        SetEnvironmentVariableA("DOLLY_UI_LAYOUT_FILE", "none");
        const bool lite_flight_screenshot = argc == 3 && std::strcmp(argv[1], "--screenshot-lite-flight") == 0;
        const bool lite_replay_screenshot = argc == 3 && std::strcmp(argv[1], "--screenshot-lite-replay") == 0;
        const bool lite_screenshot = lite_replay_screenshot || lite_flight_screenshot || (argc == 3 && std::strcmp(argv[1], "--screenshot-lite") == 0);
        // The reworked editor must be the default with no opt-in environment or launch flag.
        SetEnvironmentVariableA("DOLLY_LITE_UI", nullptr);
        const bool follow_screenshot = argc == 3 && std::strcmp(argv[1], "--screenshot-follow") == 0;
        const bool bone_screenshot = argc == 3 && std::strcmp(argv[1], "--screenshot-bone") == 0;
        const bool lens_screenshot = argc == 3 && std::strcmp(argv[1], "--screenshot-lens") == 0;
        const bool export_screenshot =
            argc == 3 && std::strcmp(argv[1], "--screenshot-export") == 0;
        const bool object_screenshot =
            argc == 3 && std::strcmp(argv[1], "--screenshot-object") == 0;
        const bool screenshot = follow_screenshot || bone_screenshot || lite_screenshot || lens_screenshot || export_screenshot || object_screenshot ||
                                (argc == 3 && std::strcmp(argv[1], "--screenshot") == 0);
        const bool layout_screenshot = screenshot && std::getenv("DOLLY_UI_TEST_LAYOUT");
        const bool graph_screenshot = layout_screenshot &&
            (std::getenv("DOLLY_UI_TEST_POSITION_GRAPH") || std::getenv("DOLLY_UI_TEST_CURVES"));
        double screenshot_curve_step=3;
        bool screenshot_tangents=false;unsigned screenshot_tangent_mode=0;
        unsigned render_width = lite_screenshot || rail_hook ? 1920 : 800;
        unsigned render_height = lite_screenshot || rail_hook ? 1080 : (screenshot ? 1100 : 600);
        if (layout_screenshot) {
            if (const auto* width = std::getenv("DOLLY_UI_TEST_WIDTH"))
                render_width = unsigned(std::clamp(std::atoi(width), 640, 3840));
            if (const auto* height = std::getenv("DOLLY_UI_TEST_HEIGHT"))
                render_height = unsigned(std::clamp(std::atoi(height), 480, 2160));
        }
        {
            // Every function key binds (F1-F24); its chip names it exactly.
            char cap[32]{};
            for (unsigned vk = 0x70; vk <= 0x87; ++vk) {
                char expected[8]{};
                std::snprintf(expected, sizeof(expected), "F%u", vk - 0x6f);
                require(dolly::key_cap_label(std::uint16_t(vk), 0, cap, sizeof(cap)) &&
                            std::strcmp(cap, expected) == 0,
                        "Function key chip text is wrong");
            }
            require(dolly::key_cap_label(0x7a, 5, cap, sizeof(cap)) &&
                        std::strcmp(cap, "CTRL+SHIFT+F11") == 0,
                    "Modified F11 chip text is wrong");
            require(dolly::key_cap_label('Q', 2, cap, sizeof(cap)) &&
                        std::strcmp(cap, "ALT+Q") == 0 &&
                        !dolly::key_cap_label(0x2d, 0, cap, sizeof(cap)),
                    "Key chip text regressed");
        }
        dolly::reshade_set_enabled(false);
        require(!dolly::reshade_overlay_pending() && !dolly::reshade_overlay_open() &&
                    !dolly::reshade_available(),
                "An unconfigured optional ReShade runtime must not hide the editor");
        require(!dolly::reshade_request_overlay(true),
                "Opening absent ReShade must leave ordinary editor input available");
        const auto instance = GetModuleHandleW(nullptr);
        WNDCLASSW wc{};
        wc.lpfnWndProc = DefWindowProcW;
        wc.hInstance = instance;
        wc.lpszClassName = L"Dolly.Overlay.Smoke";
        require(RegisterClassW(&wc) != 0, "Could not register synthetic window");
        HWND window =
            CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, wc.lpszClassName, L"Dolly graphics smoke", WS_OVERLAPPEDWINDOW, 32,
                            32, 800, screenshot ? 1100 : 600, nullptr, nullptr, instance, nullptr);
        require(window != nullptr, "Could not create synthetic window");
        if(lite_screenshot || layout_screenshot || rail_hook){RECT r{0,0,LONG(render_width),LONG(render_height)};AdjustWindowRect(&r,WS_OVERLAPPEDWINDOW,FALSE);SetWindowPos(window,nullptr,0,0,r.right-r.left,r.bottom-r.top,SWP_NOMOVE|SWP_NOZORDER|SWP_NOACTIVATE);}
        // Overlay discovery requires a visible window. Offline runs move this
        // non-activating test surface off screen instead of taking game focus.
        if (GetEnvironmentVariableA("DOLLY_UI_TEST_HIDDEN", nullptr, 0))
            SetWindowPos(window, nullptr, -32000, -32000, 0, 0,
                         SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
        ShowWindow(window, SW_SHOWNOACTIVATE);
        UpdateWindow(window);
        const auto before_proc = GetWindowLongPtrW(window, GWLP_WNDPROC);
        DXGI_SWAP_CHAIN_DESC desc{};
        desc.BufferCount = 1;
        desc.BufferDesc.Width = render_width;
        desc.BufferDesc.Height = render_height;
        desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        desc.OutputWindow = window;
        desc.SampleDesc.Count = 1;
        desc.Windowed = TRUE;
        desc.SwapEffect = DXGI_SWAP_EFFECT_SEQUENTIAL;
        IDXGISwapChain* chain = nullptr;
        ID3D11Device* device = nullptr;
        ID3D11DeviceContext* context = nullptr;
        const D3D_FEATURE_LEVEL feature = D3D_FEATURE_LEVEL_11_0;
        require(SUCCEEDED(D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
                                                        &feature, 1, D3D11_SDK_VERSION, &desc,
                                                        &chain, &device, nullptr, &context)),
                "WARP DX11 device creation failed");
        // Optional graphics diagnostics must stay inside their unused block and
        // fail closed on an unrecognized renderer, without changing camera state.
        std::vector<unsigned char> diagnostic_memory(dolly::kMappingBytes, 0xa5);
        for (const auto& profile : dolly::kRendererDiagnosticLayouts) {
            require(dolly::renderer_diagnostic_layout(profile.hash, profile.image_size,
                                                      profile.timestamp) == &profile,
                    "Reviewed renderer diagnostic layout did not resolve");
            require(!dolly::renderer_diagnostic_layout(profile.hash, profile.image_size + 1,
                                                       profile.timestamp) &&
                        !dolly::renderer_diagnostic_layout(profile.hash, profile.image_size,
                                                           profile.timestamp + 1) &&
                        !dolly::renderer_diagnostic_layout("unknown", profile.image_size,
                                                           profile.timestamp),
                    "Renderer diagnostic layout accepted changed identity metadata");
        }
        dolly::renderer_diagnostics_probe(0, "");
        dolly::renderer_diagnostics_tick(diagnostic_memory.data());
        dolly::RendererDiagnostics diagnostic{};
        std::memcpy(&diagnostic, diagnostic_memory.data() + dolly::kRendererDiagnosticsOffset,
                    sizeof(diagnostic));
        require(std::memcmp(diagnostic.magic, "DLYGFX01", 8) == 0 &&
                    diagnostic.abi == dolly::kRendererDiagnosticsAbi && !(diagnostic.sequence & 1),
                "Optional renderer diagnostic header is invalid");
        require(diagnostic.state == std::uint32_t(dolly::RendererProbeState::Waiting),
                "Missing renderer should leave the optional probe waiting");
        for (std::size_t i = 0; i < diagnostic_memory.size(); ++i)
            if (i < dolly::kRendererDiagnosticsOffset ||
                i >= dolly::kRendererDiagnosticsOffset + sizeof(diagnostic))
                require(diagnostic_memory[i] == 0xa5,
                        "Graphics diagnostics overwrote camera/editor mapping data");
        dolly::renderer_diagnostics_probe(
            reinterpret_cast<std::uintptr_t>(GetModuleHandleW(L"kernel32.dll")), "unknown");
        Sleep(1050); // one bounded sample interval; the probe never polls per frame
        dolly::renderer_diagnostics_tick(diagnostic_memory.data());
        std::memcpy(&diagnostic, diagnostic_memory.data() + dolly::kRendererDiagnosticsOffset,
                    sizeof(diagnostic));
        require(diagnostic.state == std::uint32_t(dolly::RendererProbeState::Unsupported) &&
                    diagnostic.sample == 2,
                "Unrecognized module must not permit private renderer reads");
        require(MH_Initialize() == MH_OK, "MinHook initialization failed");
        require(dolly::install_overlay_hooks(), "DXGI public-method hook installation failed");
        snapshot.enabled = true;
        snapshot.focused = true;
        snapshot.ready = true;
        snapshot.paused = true;
        snapshot.manual_active = true;
        snapshot.horizontal_fov = 90;
        snapshot.view_width = 800;
        snapshot.view_height = 600;
        snapshot.pose = {0, 0, 0, 0, 0, 0, 4.0 / 3.0};
        SyntheticViewer viewer;
        snapshot.owner = dolly::EditorOwner::Panel;
        snapshot.camera_count = 2;
        if(lite_screenshot){snapshot.tick=3470;snapshot.view_width=1920;snapshot.view_height=1080;}
        if (layout_screenshot) {
            snapshot.tick = 3470;
            snapshot.view_width = render_width;
            snapshot.view_height = render_height;
            if (const auto* count = std::getenv("DOLLY_UI_TEST_CAMERAS"))
                snapshot.camera_count = unsigned(std::clamp(std::atoi(count), 0, 128));
            if (const auto* speed=std::getenv("DOLLY_UI_TEST_PLAYBACK_SPEED")) {
                const double value=std::atof(speed);
                require(std::isfinite(value) && value>0 && value<=100,"Invalid screenshot playback speed");
                snapshot.playback_speed=value;
            }
            if (const auto* recording = std::getenv("DOLLY_UI_TEST_RECORDING")) {
                if (std::strcmp(recording, "recording") == 0)
                    dolly::recording_status_stub.state = dolly::video::State::recording;
                else if (std::strcmp(recording, "finalizing") == 0)
                    dolly::recording_status_stub.state = dolly::video::State::finalizing;
                else
                    require(false, "Unknown recording screenshot fixture state");
            }
        }
        if(graph_screenshot) {
            curve_camera_rows_stub=true;
            snapshot.camera_count=10;snapshot.selected_camera=3;
            if (const auto* count=std::getenv("DOLLY_UI_TEST_CAMERAS"))
                snapshot.camera_count=unsigned(std::clamp(std::atoi(count),2,128));
            if (const auto* duration=std::getenv("DOLLY_UI_TEST_CURVE_DURATION")) {
                const double value=std::atof(duration);
                require(std::isfinite(value) && value>0 && value<=3600,"Invalid screenshot curve duration");
                screenshot_curve_step=value/(snapshot.camera_count-1);
            }
            snapshot.selected_camera=std::min(3u,snapshot.camera_count-1);
            if(const auto* selected=std::getenv("DOLLY_UI_TEST_SELECTED_CAMERA"))
                snapshot.selected_camera=unsigned(std::clamp(std::atoi(selected),0,int(snapshot.camera_count)-1));
            if(const auto* pose=std::getenv("DOLLY_UI_TEST_VIEW_POSE")) {
                require(std::sscanf(pose,"%lf,%lf,%lf,%lf,%lf,%lf,%lf",&snapshot.pose[0],&snapshot.pose[1],
                    &snapshot.pose[2],&snapshot.pose[3],&snapshot.pose[4],&snapshot.pose[5],&snapshot.pose[6])==7,
                    "Invalid screenshot viewing pose");
                for(double value:snapshot.pose)require(std::isfinite(value) && std::abs(value)<=1e8,"Invalid screenshot viewing pose component");
                require(snapshot.pose[6]>=.5 && snapshot.pose[6]<=4,"Invalid screenshot viewing aspect");
            }
            if(const auto* mode=std::getenv("DOLLY_UI_TEST_TANGENT_MODE")) {
                const char* modes[]{"auto","flat","linked","broken"};bool found=false;
                for(unsigned i=0;i<4;++i) if(std::strcmp(mode,modes[i])==0) {screenshot_tangent_mode=i;found=true;}
                require(found,"Unknown XYZ tangent screenshot fixture mode");screenshot_tangents=true;
            }
            viewer.publish(curve_packet(snapshot.camera_count,7,0x12345678abcdef01ULL,0,false,
                snapshot.selected_camera,0,false,false,screenshot_curve_step,0,nullptr,nullptr,
                screenshot_tangents,screenshot_tangent_mode));viewer.tick();
        }
        if (screenshot) {
            snapshot.dof_available = true;
            snapshot.dof = {1, 1, -100, 0, 180, 1490, -100, 0, 180, 2000, .5};
            snapshot.attach_available = true;
            snapshot.attach_selected = true;
            snapshot.attach_keys = 2;
            snapshot.shot_keys = 2;
            snapshot.attach_target_index = 0;
            snapshot.attach_point = 0;
            snapshot.attach_offsets[2] = 6.0;
            snapshot.attach_smoothing = .15;
            snapshot.roster_count = 1;
            snapshot.follow_available = true;
            snapshot.citadel_dof_available = true;
            snapshot.citadel_dof_enabled = true;
        }
        if (object_screenshot) {
            // Render the Object Picker with a few placed objects so the whole
            // right-side panel (library, placed list, place/snap/tool/drop rows)
            // can be reviewed visually for clipping.
            snapshot.object_picker = true;
            snapshot.object_distance = 600.0;
            snapshot.object_count = 3;
            snapshot.object_selected = 1;
            for (std::uint32_t i = 0; i < snapshot.object_count; ++i) {
                snapshot.object_items[i] = {};
                snapshot.object_items[i].shape = i + 3;  // crate, barrel, pillar
                snapshot.object_items[i].scale = 1.0f;
                snapshot.object_items[i].position[0] = 400.0f + 200.0f * i;
                snapshot.object_items[i].position[1] = 0.0f;
                snapshot.object_items[i].position[2] = 0.0f;
            }
        }
        snapshot.duration = layout_screenshot && snapshot.camera_count > 2 ? (snapshot.camera_count - 1) * 3 : 3;
        if(graph_screenshot) snapshot.duration=(snapshot.camera_count-1)*screenshot_curve_step;
        std::snprintf(snapshot.shot_name, sizeof(snapshot.shot_name), "Synthetic editor smoke");
        ID3D11Texture2D* backbuffer = nullptr;
        ID3D11RenderTargetView* target = nullptr;
        require(SUCCEEDED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                           reinterpret_cast<void**>(&backbuffer))),
                "Backbuffer unavailable");
        require(SUCCEEDED(device->CreateRenderTargetView(backbuffer, nullptr, &target)),
                "Render target creation failed");
        const FLOAT clear[4] = {lite_screenshot||layout_screenshot?0.f:.04f, lite_screenshot||layout_screenshot?0.f:.08f, lite_screenshot||layout_screenshot?0.f:.95f, 1};
        D3D11_VIEWPORT original_viewport{7, 9, 113, 127, .1f, .8f};
        for (int i = 0; i < 3; ++i) {
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            context->ClearRenderTargetView(target, clear);
            context->OMSetRenderTargets(1, &target, nullptr);
            context->RSSetViewports(1, &original_viewport);
            require(SUCCEEDED(chain->Present(0, 0)), "Synthetic Present failed");
            if (!rail_hook && i == 0 && !object_screenshot && (!lite_screenshot || layout_screenshot)) {
                // All camera tools must be present in the default editor.
                ImGui::SetCurrentContext(screenshot_context);
                require(ImGui::FindWindowByName("Deadlock Dolly") != nullptr,
                        "Default full editor missing");
                auto* transport = ImGui::FindWindowByName("##lite-timeline");
                require(transport != nullptr, "Shared transport missing");
                screenshot_context->NavNextActivateId = transport->GetID("Campaths###lite-campath-mode");
                require(SUCCEEDED(chain->Present(0, 0)), "Shot transport activation failed");
                require(SUCCEEDED(chain->Present(0, 0)), "Shot transport render failed");
            }
            if (screenshot && i == 0 && !object_screenshot && (!lite_screenshot || layout_screenshot)) {
                const char* page =
                    lens_screenshot ? "LOOK" : (export_screenshot ? "EXPORT" : "CAMERA");
                require(screenshot_context != nullptr, "Overlay ImGui context missing");
                auto* panel = ImGui::FindWindowByName("Deadlock Dolly");
                require(panel != nullptr, "Editor screenshot panel missing");
                const char* label = lens_screenshot ? "Look###LOOK" : (export_screenshot ? "Export###EXPORT" :
                    (follow_screenshot ? "Follow###FOLLOW" : (bone_screenshot ? "Bone Picker###BONE PICKER" : "Camera###CAMERA")));
                screenshot_context->NavNextActivateId = panel->GetID(label);
            }
        }
        if (rail_hook) {
            ImGui::SetCurrentContext(screenshot_context);
            require(ImGui::FindWindowByName("##rail-status") && ImGui::FindWindowByName("##lite-timeline"),
                "Real rail hook lost status or transport");
            require(!dolly::overlay_close_drawers_on_escape(),"Closed rail consumed Escape");
            auto* tab=ImGui::FindWindowByName("##rail-tab2");require(tab!=nullptr,"Real rail tab missing");
            screenshot_context->NavNextActivateId=tab->GetID("##rail-button");
            for(int n=0;n<3;++n)require(SUCCEEDED(chain->Present(0,0)),"Rail activation Present failed");
            require(dolly::overlay_close_drawers_on_escape(),"First Escape did not close real rail drawer");
            require(SUCCEEDED(chain->Present(0,0)),"Rail Escape Present failed");
            require(!dolly::overlay_close_drawers_on_escape(),"Second Escape must reach the editor's flight action");
            auto* drawer=ImGui::FindWindowByName("##rail-drawer");
            require(!drawer || !drawer->Active,"Escape left a live rail drawer");
            snapshot.owner=dolly::EditorOwner::Flight;
            require(SUCCEEDED(chain->Present(0,0)),"Rail flight Present failed");
            require(!ImGui::FindWindowByName("##rail-status")->Active,"Flight failed to hide the rail");
            dolly::shutdown_overlay();
            target->Release();backbuffer->Release();context->Release();device->Release();chain->Release();
            DestroyWindow(window);UnregisterClassW(wc.lpszClassName,instance);
            std::puts("Real WARP rail hook: embedded assets, first/second Escape and flight ownership passed.");
            return 0;
        }
        if (layout_screenshot && lite_replay_screenshot) {
            auto* transport=ImGui::FindWindowByName("##lite-timeline");
            screenshot_context->NavNextActivateId=transport->GetID("Replay###lite-timeline-mode");
            for (int i=0;i<3;++i) require(SUCCEEDED(chain->Present(0,0)),"Replay fixture render failed");
        }
        if (layout_screenshot && std::getenv("DOLLY_UI_TEST_ADVANCED") &&
            (lens_screenshot || export_screenshot)) {
            ImGui::SetCurrentContext(screenshot_context);
            auto* main = ImGui::FindWindowByName("Deadlock Dolly");
            auto render_fixture = [&] {
                context->ClearRenderTargetView(target, clear);
                context->OMSetRenderTargets(1, &target, nullptr);
                context->RSSetViewports(1, &original_viewport);
                require(SUCCEEDED(chain->Present(0, 0)), "Advanced fixture Present failed");
            };
            screenshot_context->NavNextActivateId=main->GetID("Layout");render_fixture();render_fixture();
            auto* layout=screenshot_context->OpenPopupStack.back().Window;
            screenshot_context->NavNextActivateId=layout->GetID("Arrange panels");render_fixture();render_fixture();
            ImGui::GetIO().AddKeyEvent(ImGuiKey_Escape,true);render_fixture();
            ImGui::GetIO().AddKeyEvent(ImGuiKey_Escape,false);render_fixture();
            ImGui::ClosePopupToLevel(0,true);render_fixture();
            ImGui::SetWindowSize(main, ImVec2(740, 820));
            for (int i = 0; i < 3; ++i) render_fixture();
            const char* card_name = lens_screenshot ? "##dof-card" : "##export-settings";
            ImGuiWindow* card = nullptr;
            for (auto* window : screenshot_context->Windows)
                if (std::strstr(window->Name, card_name)) card = window;
            require(card != nullptr, "Advanced fixture card missing");
            screenshot_context->NavNextActivateId = card->GetID(
                lens_screenshot ? "Individual focus overrides" : "Encoder & quality");
            for (int i = 0; i < 3; ++i) render_fixture();
        }
        if (lite_screenshot && !layout_screenshot) {
            ImGui::SetCurrentContext(screenshot_context);
            auto* campaths = ImGui::FindWindowByName("##lite-campaths");
            require(campaths && campaths->Active, "Campaths missing with the menu open");
            require(screenshot_context->Tables.GetByKey(campaths->GetID("##lite-campath-details")),
                    "Campaths must match the compact four-column reference table");
            const auto old_snapshot = snapshot;
            snapshot.enabled = false;
            snapshot.owner = dolly::EditorOwner::Disabled;
            shader_loading_stub = true;
            require(SUCCEEDED(chain->Present(0,0)), "Shader notice Present failed");
            auto* notice = ImGui::FindWindowByName("##shader-loading");
            require(notice && notice->Active && (notice->Flags & ImGuiWindowFlags_NoInputs),
                    "Shader notice must render without menu or editor input");
            shader_loading_stub = false;
            snapshot = old_snapshot;
            require(SUCCEEDED(chain->Present(0,0)), "Shader notice clear Present failed");
            require(!notice->Active, "Shader notice persisted after preload finished");
            auto render_lite = [&] {
                context->ClearRenderTargetView(target,clear);
                context->OMSetRenderTargets(1,&target,nullptr);
                context->RSSetViewports(1,&original_viewport);
                require(SUCCEEDED(chain->Present(0,0)),"Lite timeline Present failed");
            };
            auto* timeline_window = ImGui::FindWindowByName("##lite-timeline");
            require(timeline_window, "Lite controls missing");
            const auto rendered_header_before=campaths->Pos;
            const auto rendered_size_before=campaths->Size;
            auto& header_io=ImGui::GetIO();
            const ImVec2 rendered_header(campaths->Pos.x+50,campaths->Pos.y+20);
            observed_actions.clear();
            header_io.AddMousePosEvent(rendered_header.x,rendered_header.y);render_lite();render_lite();
            require(ImGui::GetMouseCursor()==ImGuiMouseCursor_ResizeAll &&
                        (campaths->Flags & ImGuiWindowFlags_NoResize),
                    "Rendered campaths header needs Arrange mode or lacks the four-arrow cursor");
            header_io.AddMouseButtonEvent(0,true);render_lite();
            header_io.AddMousePosEvent(rendered_header.x+36,rendered_header.y);render_lite();render_lite();
            header_io.AddMouseButtonEvent(0,false);render_lite();render_lite();
            require(std::abs(campaths->Pos.x-rendered_header_before.x-36)<1 &&
                        campaths->Pos.y==rendered_header_before.y &&
                        campaths->Size.x==rendered_size_before.x && campaths->Size.y==rendered_size_before.y &&
                        observed_actions.empty(),
                    "Rendered standard-mode header drag does not move only the campaths panel");
            const auto replay_rect=dolly::overlay_diagnostics();
            observed_actions.clear();
            auto& replay_io=ImGui::GetIO();
            replay_io.AddMousePosEvent(replay_rect.timeline_x0+(replay_rect.timeline_x1-replay_rect.timeline_x0)*.6f,replay_rect.timeline_y);
            replay_io.AddMouseButtonEvent(0,true); render_lite(); render_lite();
            replay_io.AddMouseButtonEvent(0,false); render_lite(); render_lite();
            require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SeekReplayTick &&
                    observed_actions[0].value>50000 && observed_actions[0].value<100000,
                    "Default Replay timeline did not seek across the full replay tick range");
            screenshot_context->NavNextActivateId = timeline_window->GetID("Campaths###lite-campath-mode");
            render_lite(); render_lite();
            const auto shot_rect = dolly::overlay_diagnostics();
            // The bottom dock sits a fixed distance above the render target's
            // bottom, so measure against the frame's own display height. The
            // absolute 1080 row this used to assume failed on the CI runner,
            // whose windowed swap chain is smaller than a desktop one.
            const auto lite_display = ImGui::GetIO().DisplaySize;
            char shot_detail[192];
            std::snprintf(shot_detail, sizeof(shot_detail),
                          "Shot controls did not move into the timeline (display %.0fx%.0f, "
                          "timeline %.0f-%.0f at y %.0f, view %ux%u)",
                          lite_display.x, lite_display.y, shot_rect.timeline_x0,
                          shot_rect.timeline_x1, shot_rect.timeline_y,
                          snapshot.view_width, snapshot.view_height);
            require(shot_rect.timeline_x1 > shot_rect.timeline_x0 && shot_rect.timeline_y > 0 &&
                    shot_rect.timeline_y > lite_display.y * 0.83f, shot_detail);
            auto* lite_main = ImGui::FindWindowByName("Deadlock Dolly");
            require(lite_main != nullptr, "Lite menu missing");
            ImGui::FocusWindow(lite_main);
            ImGui::ClearActiveID();
            render_lite(); render_lite();
            const auto prior_focus = screenshot_context->NavId;
            dolly::overlay_raw_key(VK_TAB, 0x0f, true);
            for (int i=0;i<6;i++) render_lite();
            require(!ImGui::IsKeyDown(ImGuiKey_Tab) && screenshot_context->NavId == prior_focus &&
                    screenshot_context->ActiveId == 0, "Holding Tab selected Lite controls");
            dolly::overlay_raw_key(VK_TAB, 0x0f, false);
            render_lite();
            observed_actions.clear();
            auto& lite_io = ImGui::GetIO();
            lite_io.AddMousePosEvent((shot_rect.timeline_x0+shot_rect.timeline_x1)*.5f,shot_rect.timeline_y);
            lite_io.AddMouseButtonEvent(0,true);render_lite();render_lite();
            lite_io.AddMouseButtonEvent(0,false);render_lite();render_lite();
            require(observed_actions.empty(),"Shot slider must wait for Seek here before seeking");
            screenshot_context->NavNextActivateId = timeline_window->GetID("Seek here");
            render_lite();render_lite();
            require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SeekShot &&
                    observed_actions[0].value>0 && observed_actions[0].value<snapshot.duration,
                    "Lite Seek here failed to apply the selected shot time");
            observed_actions.clear();
            screenshot_context->NavNextActivateId = timeline_window->GetID("0.25x");
            render_lite(); render_lite();
            require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::SetPlaybackSpeed &&
                    observed_actions[0].value==.25, "0.25x did not set playback speed");
            snapshot.playback_speed=.25;
            render_lite();
            const auto rgb=dolly::ui::SELECTED_BLUE;
            const auto blue=IM_COL32((rgb>>16)&255,(rgb>>8)&255,rgb&255,255);
            bool blue_fill=false;
            for(const auto& vertex : timeline_window->DrawList->VtxBuffer)
                if(vertex.col==blue)blue_fill=true;
            require(blue_fill,"Selected 0.25x speed lacks its blue highlight");
            observed_actions.clear();
            auto* camera_table=screenshot_context->Tables.GetByKey(campaths->GetID("##lite-campath-details"));
            require(camera_table && camera_table->ColumnsCount==4,"Camera table is missing");
            // The collapsed Position graph consumes a row in a short sidebar.
            // Page to camera 2 when only one data row fits, then click its actual
            // rendered bounds. Keep proving the second camera's exact payload.
            if (camera_table->CurrentRow == 1) {
                screenshot_context->NavNextActivateId = campaths->GetID("##next-cameras");
                render_lite(); render_lite();
                require(observed_actions.empty(), "Local camera pagination unexpectedly dispatched an edit");
            }
            float arrive_x=(camera_table->Columns[1].WorkMinX+camera_table->Columns[1].WorkMaxX)*.5f;
            float arrive_y=(camera_table->RowPosY1+camera_table->RowPosY2)*.5f;
            lite_io.AddMousePosEvent(arrive_x,arrive_y);
            lite_io.AddKeyEvent(ImGuiMod_Ctrl,true);
            lite_io.AddMouseButtonEvent(0,true);render_lite();render_lite();
            lite_io.AddMouseButtonEvent(0,false);render_lite();
            lite_io.AddKeyEvent(ImGuiMod_Ctrl,false);render_lite();
            if (!screenshot_context->ActiveId || screenshot_context->InputTextState.ID!=screenshot_context->ActiveId)
                std::fprintf(stderr,"Arrive input: active%u text%u hovered%s at%.1f %.1f table rows%d y%.1f..%.1f lastrow%.1f..%.1f minheight%.1f sidebar%.1f..%.1f recording%u\n",
                    screenshot_context->ActiveId,screenshot_context->InputTextState.ID,
                    screenshot_context->HoveredWindow ? screenshot_context->HoveredWindow->Name : "none",
                    arrive_x,arrive_y,camera_table->CurrentRow,camera_table->OuterRect.Min.y,camera_table->OuterRect.Max.y,
                    camera_table->RowPosY1,camera_table->RowPosY2,camera_table->RowMinHeight,
                    campaths->Pos.y,campaths->Pos.y+campaths->Size.y,unsigned(dolly::recording_status_stub.state));
            require(screenshot_context->ActiveId && screenshot_context->InputTextState.ID==screenshot_context->ActiveId,
                    "Lite Arrive/s Ctrl-click did not open a numeric editor");
            // A different edit or an acknowledgement can arrive while this
            // field owns a typed draft. Keep the original gesture revision so
            // authoritative dispatch can reject a stale camera index.
            camera_revision_stub = 8;
            render_lite();
            lite_io.AddInputCharactersUTF8("1.25");render_lite();
            lite_io.AddKeyEvent(ImGuiKey_Enter,true);render_lite();
            lite_io.AddKeyEvent(ImGuiKey_Enter,false);render_lite();render_lite();
            unsigned arrival_edits = 0;
            for(const auto& edit : observed_actions)
                if(edit.action==dolly::EditorAction::SetCameraTime) {
                    require(edit.value==1 && edit.revision==7 && std::abs(edit.time-1.25)<.001,
                            "Arrive edit lost the camera index, revision, or typed time");
                    ++arrival_edits;
                }
            require(arrival_edits==1,"Arrive edit must commit exactly once");
            camera_revision_stub = 7;
            render_lite();
            observed_actions.clear();
            lite_io.MouseClickedTime[0] = -1e30; // A new drag, rather than the editor's double-click shortcut.
            // Temp-input exit can settle the auto-sized card on the following
            // frame. Read its current field geometry before a new gesture.
            render_lite();render_lite();
            arrive_x=(camera_table->Columns[1].WorkMinX+camera_table->Columns[1].WorkMaxX)*.5f;
            arrive_y=(camera_table->RowPosY1+camera_table->RowPosY2)*.5f;
            lite_io.AddMousePosEvent(arrive_x,arrive_y);
            lite_io.AddMouseButtonEvent(0,true);render_lite();render_lite();
            lite_io.AddMousePosEvent(arrive_x+16,arrive_y);render_lite();render_lite();
            lite_io.AddMouseButtonEvent(0,false);render_lite();render_lite();
            unsigned arrival_drags = 0;
            for(const auto& edit : observed_actions)
                if(edit.action==dolly::EditorAction::SetCameraTime) {
                    require(edit.value==1 && edit.revision==7 && edit.time>1.25,
                            "Arrive drag failed to increase the chosen camera time");
                    ++arrival_drags;
                }
            if(arrival_drags!=1)std::fprintf(stderr,"Arrive drag: %u edits, %zu actions at %.1f %.1f\n",arrival_drags,observed_actions.size(),arrive_x,arrive_y);
            require(arrival_drags==1,"Arrive drag must commit once on release");

            auto* main = ImGui::FindWindowByName("Deadlock Dolly");
            screenshot_context->NavNextActivateId = main->GetID("Layout");
            render_lite();render_lite();
            require(!screenshot_context->OpenPopupStack.empty(),
                    "F8 visibility fixture did not open its layout popup");
            const ImVec2 panel_positions[] = {main->Pos, campaths->Pos, timeline_window->Pos};
            const ImVec2 panel_sizes[] = {main->Size, campaths->Size, timeline_window->Size};
            auto* layout_popup = screenshot_context->OpenPopupStack.back().Window;
            snapshot.owner = dolly::EditorOwner::Flight;
            const auto before_paused_close = dolly::overlay_diagnostics();
            render_lite();render_lite();
            require(!main->Active && !campaths->Active && !timeline_window->Active &&
                        layout_popup && !layout_popup->Active,
                    "Paused flight leaves a workspace panel or popup visible");
            require(dolly::overlay_diagnostics().guide_frames == before_paused_close.guide_frames + 2 &&
                        dolly::overlay_diagnostics().panel_frames == before_paused_close.panel_frames,
                    "Closing the workspace also hides paused camera guides");
            // F8's existing Panel -> Flight handoff closes the whole workspace.
            // During a shot there are no guides, so no Dolly pixels should draw.
            snapshot.playing = true;
            const auto before_close = dolly::overlay_diagnostics();
            for (int i=0;i<3;i++) {
                const auto hidden = render_regions(chain,device,context,target,backbuffer);
                require(hidden.panel == 0 && hidden.guides == 0,
                        "Closing the main menu leaves timeline, campaths or popup pixels visible");
            }
            require(dolly::overlay_diagnostics().draw_frames == before_close.draw_frames &&
                        dolly::overlay_diagnostics().panel_frames == before_close.panel_frames,
                    "Closed workspace still submits an editor render frame");
            snapshot.owner = dolly::EditorOwner::Panel;
            render_lite();render_lite();
            const char* panel_names[] = {"Deadlock Dolly", "##lite-campaths", "##lite-timeline"};
            for (unsigned i=0;i<3;++i) {
                const auto* restored = ImGui::FindWindowByName(panel_names[i]);
                require(restored && restored->Active && restored->LastFrameActive == screenshot_context->FrameCount,
                        "Reopening the main menu does not restore every workspace panel");
                require(restored->Pos.x == panel_positions[i].x && restored->Pos.y == panel_positions[i].y &&
                            restored->Size.x == panel_sizes[i].x && restored->Size.y == panel_sizes[i].y,
                        "F8 workspace visibility changes a panel's saved layout");
            }
            require(snapshot.playing,
                    "Reopening the workspace interrupts cinematic playback");
            ImGui::ClosePopupToLevel(0,true);
            snapshot.playing = false;
            render_lite();render_lite();
            if (lite_replay_screenshot) {
                screenshot_context->NavNextActivateId = timeline_window->GetID("Replay###lite-timeline-mode");
                render_lite(); render_lite();
            }
            if (lite_flight_screenshot) snapshot.owner = dolly::EditorOwner::Flight;
            for(int i=0;i<3;i++) {
                context->ClearRenderTargetView(target,clear);
                context->OMSetRenderTargets(1,&target,nullptr);
                context->RSSetViewports(1,&original_viewport);
                require(SUCCEEDED(chain->Present(0,0)),"Campath restore Present failed");
            }
            std::puts("F8 workspace: main, campaths and timeline hide together and reopen with the same layout.");
        }
        require(available && attached == window,
                "Overlay did not attach to the presenting game window");
        if(screenshot && layout_screenshot)if(const auto* preferences=std::getenv("DOLLY_UI_TEST_CUSTOM_LAYOUT_FILE")) {
            ImGui::LoadIniSettingsFromDisk(preferences);
            for(int i=0;i<5;++i)require(SUCCEEDED(chain->Present(0,0)),"Custom layout capture failed to render");
        }
        if (screenshot && layout_screenshot && std::getenv("DOLLY_UI_TEST_SWAP_SECTIONS")) {
            static constexpr const char* names[5][4]={{"##cameras-card","##flight-card",nullptr,nullptr},{"##follow-card","##follow-framing",nullptr,nullptr},
                {"##attach-card","##attach-framing",nullptr,nullptr},{"##dof-card","##citadel-dof-card","##appearance-card","##confetti-card"},
                {"##export-camera","##export-settings","##export-passes","##video-card"}};
            const int page=lens_screenshot ? 3 : export_screenshot ? 4 : bone_screenshot ? 2 : follow_screenshot ? 1 : 0;
            unsigned source_index=0,target_index=0;
            require(std::sscanf(std::getenv("DOLLY_UI_TEST_SWAP_SECTIONS"),"%u,%u",&source_index,&target_index)==2 &&
                    source_index<(page>=3 ? 4u : 2u) && target_index<(page>=3 ? 4u : 2u),"Invalid section swap capture fixture");
            auto find_section=[&](unsigned index) -> ImGuiWindow* {
                for(auto* item:screenshot_context->Windows)if(item->Active && std::strstr(item->Name,names[page][index]))return item;
                return nullptr;
            };
            auto render_section=[&] {context->ClearRenderTargetView(target,clear);context->OMSetRenderTargets(1,&target,nullptr);
                context->RSSetViewports(1,&original_viewport);require(SUCCEEDED(chain->Present(0,0)),"Section swap capture failed to render");};
            Sleep(260);render_section();
            auto* arrange_main=ImGui::FindWindowByName("Deadlock Dolly");
            screenshot_context->NavNextActivateId=arrange_main->GetID("Layout");render_section();render_section();
            auto* arrange_window=screenshot_context->OpenPopupStack.back().Window;
            if(arrange_main->Flags&ImGuiWindowFlags_NoResize) {
                screenshot_context->NavNextActivateId=arrange_window->GetID("Arrange panels");render_section();render_section();
            }
            ImGui::ClosePopupToLevel(0,true);render_section();render_section();
            auto* source=find_section(source_index);auto* destination=find_section(target_index);
            require(source && destination,"Section swap capture fixture lost a section");
            const ImVec2 from(source->Pos.x+source->Size.x-9,source->Pos.y+source->WindowPadding.y+7);
            const ImVec2 to(destination->Pos.x+destination->WindowPadding.x+12,destination->Pos.y+destination->WindowPadding.y+7);
            auto& section_io=ImGui::GetIO();section_io.AddMousePosEvent(from.x,from.y);render_section();render_section();
            section_io.AddMouseButtonEvent(ImGuiMouseButton_Left,true);render_section();
            section_io.AddMousePosEvent(from.x+20,from.y);render_section();render_section();
            if(!screenshot_context->DragDropActive)
                std::fprintf(stderr,"Drag page %d source %s %.1f %.1f %.1fx%.1f from %.1f %.1f mouse %.1f %.1f active %u hovered %s main-resize %d parent clip %.1f %.1f %.1f %.1f\n",page,source->Name,source->Pos.x,source->Pos.y,source->Size.x,source->Size.y,from.x,from.y,section_io.MousePos.x,section_io.MousePos.y,screenshot_context->ActiveId,screenshot_context->HoveredWindow ? screenshot_context->HoveredWindow->Name : "none",!(arrange_main->Flags&ImGuiWindowFlags_NoResize),source->ParentWindow->ClipRect.Min.x,source->ParentWindow->ClipRect.Min.y,source->ParentWindow->ClipRect.Max.x,source->ParentWindow->ClipRect.Max.y);
            require(screenshot_context->DragDropActive,"Rendered section capture did not start a drag");
            section_io.AddMousePosEvent(to.x,to.y);render_section();render_section();
            if(!std::getenv("DOLLY_UI_TEST_DRAG_GHOST")) {
            section_io.AddMouseButtonEvent(ImGuiMouseButton_Left,false);render_section();render_section();
            section_io.AddMousePosEvent(-100,-100);render_section();
            std::size_t saved_length=0;const std::string saved(ImGui::SaveIniSettingsToMemory(&saved_length));
            char expected[80];unsigned slots[]={0,1,2,3};std::swap(slots[source_index],slots[target_index]);
            std::snprintf(expected,sizeof(expected),"Sections%d=%u,%u,%u,%u",page,slots[0],slots[1],slots[2],slots[3]);
            require(saved.find(expected)!=std::string::npos,"Rendered section drag did not save the selected arrangement");
            }
        }
        if(graph_screenshot) {
            ImGui::SetCurrentContext(screenshot_context);
            auto* dock=ImGui::FindWindowByName("##lite-timeline");
            require(dock && dock->Active,"Graph screenshot requires a visible timeline dock");
            auto render_curves=[&] {
                context->ClearRenderTargetView(target,clear);context->OMSetRenderTargets(1,&target,nullptr);
                require(SUCCEEDED(chain->Present(0,0)),"Unified curve fixture Present failed");
            };
            auto activate_curve=[&](const char* label) {
                const bool lane=std::strstr(label,"curve-lane-")!=nullptr;
                if(lane) {
                    screenshot_context->NavNextActivateId=dock->GetID("Graphs");
                    for(int i=0;i<5;++i)render_curves();
                    require(screenshot_context->OpenPopupStack.Size>0,"Screenshot Graphs menu did not open");
                }
                screenshot_context->NavNextActivateId=dock->GetID(label);
                for(int i=0;i<5;++i)render_curves();
                if(lane) {ImGui::ClosePopupToLevel(0,true);for(int i=0;i<5;++i)render_curves();}
            };
            activate_curve("Curves###lite-curves-mode");
            const int group=std::getenv("DOLLY_UI_TEST_CURVES") ?
                std::clamp(std::atoi(std::getenv("DOLLY_UI_TEST_CURVES")),0,2) : 0;
            const char* groups[]={"Position###curve-group-0","Rotation###curve-group-1","Framing###curve-group-2"};
            activate_curve(groups[group]);
            if (const auto* axis=std::getenv("DOLLY_UI_TEST_CURVE_AXIS")) {
                const char* axes[]={"X###curve-axis-0","Y###curve-axis-1","Z###curve-axis-2",
                                    "Pitch###curve-axis-3","Yaw###curve-axis-4","Roll###curve-axis-5"};
                activate_curve(axes[std::clamp(std::atoi(axis),0,5)]);
            }
            if (const auto* lanes=std::getenv("DOLLY_UI_TEST_CURVE_COMPARE")) {
                activate_curve("Stack graphs###Compare");
                if (std::atoi(lanes)==3)activate_curve("Framing###curve-lane-2");
            }
            if (std::getenv("DOLLY_UI_TEST_CURVE_BEFORE")) {
                activate_curve("Show original###Show Before");
                camera_revision_stub=8;
                viewer.publish(curve_packet(snapshot.camera_count,8,0x12345678abcdef01ULL,12,false,
                    snapshot.selected_camera,0,false,false,screenshot_curve_step,0,nullptr,nullptr,
                    screenshot_tangents,screenshot_tangent_mode));viewer.tick();
                for(int i=0;i<5;++i)render_curves();
                activate_curve("Frame all###Fit");
            }
            if(std::getenv("DOLLY_UI_TEST_CURVE_HOVER")) {
                // Find a filled selected-key diamond from the actual emitted
                // production mesh. Font glyphs use different texture UVs, so
                // they cannot masquerade as a white solid camera marker.
                const auto uv=ImGui::GetIO().Fonts->TexUvWhitePixel;
                constexpr auto rgb=dolly::ui::TEXT;
                const auto color=IM_COL32((rgb>>16)&255,(rgb>>8)&255,rgb&255,255);
                ImVec2 hit{};bool found=false;
                for(auto* child:screenshot_context->Windows) if(child->Active && child->ParentWindow==dock &&
                        std::strstr(child->Name,"##curve-plots")) {
                    const auto* draw=child->DrawList;
                    for(int i=0;i+2<draw->IdxBuffer.Size && !found;i+=3) {
                        const auto& a=draw->VtxBuffer[draw->IdxBuffer[i]];
                        const auto& b=draw->VtxBuffer[draw->IdxBuffer[i+1]];
                        const auto& c=draw->VtxBuffer[draw->IdxBuffer[i+2]];
                        if(a.col==color && b.col==color && c.col==color &&
                           a.uv.x==uv.x && a.uv.y==uv.y && b.uv.x==uv.x && b.uv.y==uv.y && c.uv.x==uv.x && c.uv.y==uv.y) {
                            hit=ImVec2((a.pos.x+b.pos.x+c.pos.x)/3,(a.pos.y+b.pos.y+c.pos.y)/3);
                            found=child->InnerClipRect.Contains(hit);
                        }
                    }
                }
                require(found,"Tooltip screenshot has no actual selected-camera diamond");
                ImGui::GetIO().AddMousePosEvent(hit.x,hit.y);
                for(int i=0;i<8;++i)render_curves();
                bool tooltip=false;
                for(auto* item:screenshot_context->Windows) if(item->Active && (item->Flags&ImGuiWindowFlags_Tooltip)) {
                    tooltip=true;
                    require(item->WindowPadding.x>=9 && item->WindowPadding.y>=7 &&
                                item->Pos.x>=0 && item->Pos.y>=0 &&
                                item->Pos.x+item->Size.x<=render_width+1 && item->Pos.y+item->Size.y<=render_height+1,
                            "Rendered graph tooltip lacks padding or leaves the viewport");
                }
                require(tooltip,"Actual graph camera hover did not open a tooltip");
            }
            if(const auto* file=std::getenv("DOLLY_UI_TEST_CAPTURE_PREVIEW_PACKET")) {
                const auto base=dolly::visualization_snapshot();const auto pose=snapshot.pose;
                std::ifstream input(file,std::ios::binary|std::ios::ate);
                require(input.good(),"Cannot open compiled screenshot preview packet");
                const auto size=static_cast<std::streamoff>(input.tellg());
                require(size>=64 && size<=std::streamoff(dolly::kVisualizationMappingBytes),"Screenshot preview packet length is invalid");
                std::vector<unsigned char> bytes(static_cast<std::size_t>(size));input.seekg(0);
                input.read(reinterpret_cast<char*>(bytes.data()),size);require(input.good(),"Cannot read screenshot preview packet");
                dolly::VisualizationPreview parsed;std::string error;
                require(base && parsed.load(bytes.data(),bytes.size(),*base,error) && parsed.path,
                    "Screenshot preview packet failed normal identity/parser validation");
                const auto* stage=std::getenv("DOLLY_UI_TEST_PREVIEW_CAPTURE_STAGE");if(!stage)stage="during";
                const bool before=std::strcmp(stage,"before")==0,clear=std::strcmp(stage,"clear")==0;
                require(before || clear || std::strcmp(stage,"during")==0,"Unknown screenshot preview stage");
                if(std::getenv("DOLLY_UI_TEST_PREVIEW_UNFOCUSED")) {
                    require(parsed.source==1,"Unfocused preview capture requires a desktop draft");
                    snapshot.focused=false;snapshot.owner=dolly::EditorOwner::Unfocused;
                }
                unsigned token=parsed.token;
                if(!before) {
                    if(parsed.source==2) {
                        token=dolly::visualization_begin_native_preview(*base,parsed.index,parsed.channel);
                        for(unsigned i=0;i<4;++i)bytes[24+i]=static_cast<unsigned char>(token>>(8*i));
                    }
                    viewer.publish_preview(bytes);viewer.tick();
                    require(bool(dolly::visualization_preview_snapshot()),"Actual preview mapping did not publish screenshot draft");
                    if(clear) {
                        if(parsed.source==2) {
                            dolly::visualization_end_native_preview(token);
                            require(!dolly::visualization_preview_snapshot(),"Screenshot native cancel did not hide draft immediately");
                        }
                        viewer.publish_preview(preview_packet({},parsed.source,token,parsed.index,parsed.channel,parsed.time,
                            parsed.revision,parsed.session));viewer.tick();
                        require(!dolly::visualization_preview_snapshot(),"Screenshot terminal clear retained draft");
                    }
                }
                for(int i=0;i<5;++i)render_curves();
                require(dolly::visualization_snapshot()==base && snapshot.pose==pose,
                    "Render-only screenshot preview changed the committed graph or viewing camera");
                if(!snapshot.focused) require(!ImGui::GetIO().WantCaptureMouse && !ImGui::GetIO().WantCaptureKeyboard,
                    "Unfocused desktop preview captured game input");
                std::printf("Render-only preview capture %s: source %u token %u, committed revision %u/session %llu; fixed view preserved.\n",
                    stage,parsed.source,token,base->project_revision(),static_cast<unsigned long long>(base->session_id()));
            }
        }

        if (screenshot && std::getenv("DOLLY_UI_TEST_SHADER_ONLY")) {
            ImGui::SetCurrentContext(screenshot_context);
            const auto prior_snapshot=snapshot;
            snapshot.enabled=false;
            snapshot.owner=dolly::EditorOwner::Disabled;
            auto render_notice=[&] {
                context->ClearRenderTargetView(target,clear);
                context->OMSetRenderTargets(1,&target,nullptr);
                context->RSSetViewports(1,&original_viewport);
                require(SUCCEEDED(chain->Present(0,0)),"Shader review Present failed");
            };
            const float notice_scale=ImGui::GetIO().FontDefault->FontSize/16.f;
            auto inspect_notice=[&] {
                auto* notice=ImGui::FindWindowByName("##shader-loading");
                require(notice && notice->Active,"Shader notice missing without the editor");
                const auto display=ImGui::GetIO().DisplaySize;
                std::printf("Geometry: buffer %ux%u, client %.0fx%.0f, DPI scale %.2f, notice %.0fx%.0f at %.0f %.0f.\n",
                            render_width,render_height,display.x,display.y,notice_scale,
                            notice->Size.x,notice->Size.y,notice->Pos.x,notice->Pos.y);
                const int required=ImGuiWindowFlags_NoInputs|ImGuiWindowFlags_NoSavedSettings|
                    ImGuiWindowFlags_NoFocusOnAppearing|ImGuiWindowFlags_NoDecoration;
                require((notice->Flags&required)==required,"Shader notice input or persistence policy changed");
                require(notice->Pos.x>=0 && notice->Pos.y>=0 &&
                        notice->Pos.x+notice->Size.x<=display.x+1 &&
                        notice->Pos.y+notice->Size.y<=display.y+1,"Shader notice leaves its viewport");
                require(std::abs(notice->Pos.x+notice->Size.x*.5f-display.x*.5f)<=1,
                        "Shader notice is not horizontally centered");
                require(std::abs(notice->Pos.y+notice->Size.y-display.y*.75f)<=1,
                        "Shader notice does not keep its established bottom-middle anchor");
                require(notice->ScrollMax.y<=1 &&
                        notice->DC.CursorMaxPos.y<=notice->InnerClipRect.Max.y+1,
                        "Shader notice clips its content");
                require(!ImGui::GetIO().WantCaptureMouse && !ImGui::GetIO().WantCaptureKeyboard,
                        "Shader notice captures game input");
                for(const auto* name:{"Deadlock Dolly","##lite-campaths","##lite-timeline"}) {
                    const auto* panel=ImGui::FindWindowByName(name);
                    require(!panel || !panel->Active,"Shader fixture includes an editor surface");
                }
                return notice;
            };
            observed_actions.clear();
            shader_loading_stub=true;
            for(int i=0;i<5;++i)render_notice();
            auto* notice=inspect_notice();
            shader_loading_stub=false;
            snapshot=prior_snapshot;
            render_notice();render_notice();
            require(!notice->Active,"Shader notice remains after the loading flag clears");
            snapshot.enabled=false;snapshot.owner=dolly::EditorOwner::Disabled;
            shader_loading_stub=true;
            for(int i=0;i<5;++i)render_notice();
            notice=inspect_notice();
            require(observed_actions.empty(),"Shader notice enqueues an editor action");
            D3D11_TEXTURE2D_DESC notice_texture{};
            backbuffer->GetDesc(&notice_texture);
            notice_texture.Usage=D3D11_USAGE_STAGING;
            notice_texture.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
            notice_texture.BindFlags=notice_texture.MiscFlags=0;
            ID3D11Texture2D* notice_staging=nullptr;
            require(SUCCEEDED(device->CreateTexture2D(&notice_texture,nullptr,&notice_staging)),
                    "Shader notice readback texture failed");
            context->CopyResource(notice_staging,backbuffer);
            D3D11_MAPPED_SUBRESOURCE notice_pixels{};
            require(SUCCEEDED(context->Map(notice_staging,0,D3D11_MAP_READ,0,&notice_pixels)),
                    "Shader notice readback failed");
            std::ofstream output(argv[2],std::ios::binary);
            output<<"P6\n"<<notice_texture.Width<<" "<<notice_texture.Height<<"\n255\n";
            std::size_t changed=0;
            for(unsigned y=0;y<notice_texture.Height;++y) {
                const auto* row=static_cast<const char*>(notice_pixels.pData)+y*notice_pixels.RowPitch;
                for(unsigned x=0;x<notice_texture.Width;++x) {
                    output.write(row+x*4,3);
                    changed+=row[x*4]!=0 || row[x*4+1]!=0 || row[x*4+2]!=0;
                }
            }
            output.close();
            context->Unmap(notice_staging,0);notice_staging->Release();
            require(output.good() && changed>100,"Shader notice capture has no real rendered pixels");
            std::printf("Shader notice %ux%u: %.0fx%.0f at %.0f %.0f; bottom margin %.0f; "
                        "NoInputs/no-capture/flag-clear/no-editor/no-actions/content-bounds PASS; pixels %zu.\n",
                        render_width,render_height,notice->Size.x,notice->Size.y,notice->Pos.x,notice->Pos.y,
                        render_height-notice->Pos.y-notice->Size.y,changed);
            return 0;
        }
        if (screenshot) {
            const bool add_camera=std::getenv("DOLLY_UI_TEST_ADD_CAMERA")!=nullptr;
            if (add_camera) {
                ++snapshot.camera_count;snapshot.selected_camera=snapshot.camera_count-1;
                ++camera_revision_stub;
                snapshot.duration=(snapshot.camera_count-1)*3;
            } else if(const auto* elapsed=std::getenv("DOLLY_UI_TEST_REORDER_CAPTURE_MS")) Sleep(DWORD(std::clamp(std::atoi(elapsed),0,300)));
            else Sleep(260); // Capture the settled endpoint of presentation animations.
            // Nested auto-fit windows apply measured content sizes on the next
            // frame. Let those frames settle too before inspecting pixels.
            for(int i=0;i<(add_camera ? 2 : 8);++i) {
                context->ClearRenderTargetView(target,clear);
                context->OMSetRenderTargets(1,&target,nullptr);
                require(SUCCEEDED(chain->Present(0,0)),"Settled screenshot Present failed");
            }
        }
        if(graph_screenshot && snapshot.focused) {
            // Curves keeps full-size main controls in a deliberately scrollable
            // viewport. Prove that the bottom controls can actually be reached,
            // then restore the original scroll before capturing the image.
            ImGui::SetCurrentContext(screenshot_context);
            ImGuiWindow* content=nullptr;
            float bottom=0;
            for(const auto* card:screenshot_context->Windows)
                if(card->Active && card->ParentWindow && std::strstr(card->ParentWindow->Name,"##editor-content")) {
                    content=card->ParentWindow;bottom=std::max(bottom,card->Pos.y+card->Size.y);
                    require(card->FontRefSize>=14,"Curves screenshot contains unreadably small existing controls");
                    require(card->DC.CursorMaxPos.y<=card->Pos.y+card->Size.y+1,
                            "Curves screenshot clips controls inside an editor card");
                }
            require(content!=nullptr,"Curves screenshot lost the main editor content");
            if(bottom>content->InnerClipRect.Max.y+1) {
                require(content->ScrollMax.y>0 && !(content->Flags&ImGuiWindowFlags_NoScrollWithMouse),
                        "Curves screenshot hides controls without an accessible scroll region");
                const float previous_scroll=content->Scroll.y;
                auto render_scroll=[&] {
                    for(int i=0;i<8;++i) {
                        context->ClearRenderTargetView(target,clear);context->OMSetRenderTargets(1,&target,nullptr);
                        require(SUCCEEDED(chain->Present(0,0)),"Curves control reachability Present failed");
                    }
                };
                ImGui::SetScrollY(content,content->ScrollMax.y);render_scroll();bottom=0;
                for(const auto* card:screenshot_context->Windows)if(card->Active && card->ParentWindow==content)
                    bottom=std::max(bottom,card->Pos.y+card->Size.y);
                require(content->Scroll.y>0 && bottom<=content->InnerClipRect.Max.y+1,
                        "Curves screenshot cannot scroll to its final existing controls");
                ImGui::SetScrollY(content,previous_scroll);render_scroll();
            }
        }
        ID3D11RenderTargetView* after_target = nullptr;
        context->OMGetRenderTargets(1, &after_target, nullptr);
        require(after_target == target, "Overlay did not restore the game's output target");
        if (after_target)
            after_target->Release();
        D3D11_VIEWPORT after_viewport{};
        UINT count = 1;
        context->RSGetViewports(&count, &after_viewport);
        require(count == 1 &&
                    std::memcmp(&after_viewport, &original_viewport, sizeof(after_viewport)) == 0,
                "Overlay did not restore the game's viewport");
        // A real panel must change backbuffer pixels; successful hook installation
        // alone would pass without ever rendering anything into the game.
        D3D11_TEXTURE2D_DESC texture{};
        backbuffer->GetDesc(&texture);
        texture.Usage = D3D11_USAGE_STAGING;
        texture.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        texture.BindFlags = 0;
        texture.MiscFlags = 0;
        ID3D11Texture2D* staging = nullptr;
        require(SUCCEEDED(device->CreateTexture2D(&texture, nullptr, &staging)),
                "Readback texture creation failed");
        context->CopyResource(staging, backbuffer);
        D3D11_MAPPED_SUBRESOURCE pixels{};
        require(SUCCEEDED(context->Map(staging, 0, D3D11_MAP_READ, 0, &pixels)),
                "Rendered panel readback failed");
        ImGui::SetCurrentContext(screenshot_context);
        const auto* rendered_panel = ImGui::FindWindowByName("Deadlock Dolly");
        const unsigned sample_x = rendered_panel ? unsigned(rendered_panel->Pos.x + 8) : 40;
        const unsigned sample_y = rendered_panel ? unsigned(rendered_panel->Pos.y + 8) : 80;
        const auto* sample = static_cast<const unsigned char*>(pixels.pData) +
            std::min(sample_y, texture.Height - 1) * pixels.RowPitch +
            std::min(sample_x, texture.Width - 1) * 4;
        const bool drawn = sample[2] < 180;
        if (screenshot) {
            // Optional no-game visual review of the real rendered panel.
            if (layout_screenshot) {
                if(rendered_panel && rendered_panel->Active)
                    std::printf("Editor: %.0fx%.0f at %.0f %.0f; content bottom %.0f / %.0f; scroll %.0f.\n",
                                rendered_panel->Size.x,rendered_panel->Size.y,rendered_panel->Pos.x,rendered_panel->Pos.y,
                                rendered_panel->DC.CursorMaxPos.y,rendered_panel->InnerClipRect.Max.y,rendered_panel->ScrollMax.y);
                require(!rendered_panel || !rendered_panel->Active ||
                            (rendered_panel->ScrollMax.y<=1 &&
                             rendered_panel->DC.CursorMaxPos.y<=rendered_panel->InnerClipRect.Max.y+1),
                        "Layout fixture clips its editor footer");
                for(const auto* window:screenshot_context->Windows)
                    if(window->Active && window->ParentWindow &&
                       std::strstr(window->ParentWindow->Name,"##editor-content")) {
                        std::printf("Card %s: %.0fx%.0f, measured height %.0f, font %.1f, bottom %.0f / %.0f; parent height %.0f, content %.0f.\n",window->Name,
                                    window->Size.x,window->Size.y,window->DC.CursorMaxPos.y-window->Pos.y+window->WindowPadding.y,window->FontRefSize,
                                    window->Pos.y+window->Size.y,window->ParentWindow->InnerClipRect.Max.y,
                                    window->ParentWindow->Size.y,window->ParentWindow->ContentSize.y);
                        require(window->Pos.y+window->Size.y<=window->ParentWindow->InnerClipRect.Max.y+1 ||
                                    (window->ParentWindow->ScrollMax.y>0 && window->FontRefSize>=14 &&
                                     !(window->ParentWindow->Flags&ImGuiWindowFlags_NoScrollWithMouse) &&
                                     window->Pos.y+window->Size.y<=window->ParentWindow->InnerClipRect.Max.y+
                                         window->ParentWindow->ScrollMax.y-window->ParentWindow->Scroll.y+1 &&
                                     window->DC.CursorMaxPos.y<=window->Pos.y+window->Size.y+1),
                                "Layout fixture clips an editor card");
                    }
                for (const auto* name : {"Deadlock Dolly", "##lite-campaths", "##lite-timeline"}) {
                    const auto* window = ImGui::FindWindowByName(name);
                    if (!window || !window->Active) {
                        require(render_width < 900 || render_height < 650 ||
                                    std::getenv("DOLLY_UI_TEST_PREVIEW_UNFOCUSED"),
                                "A desktop working surface disappeared");
                        continue;
                    }
                    require(window->Pos.x >= 0 && window->Pos.y >= 0 &&
                                window->Pos.x + window->Size.x <= render_width + 1 &&
                                window->Pos.y + window->Size.y <= render_height + 1,
                            "Layout fixture leaves a working surface outside its viewport");
                }
                const auto* sidebar = ImGui::FindWindowByName("##lite-campaths");
                if (!std::getenv("DOLLY_UI_TEST_ADVANCED") && render_width>=900 && render_height>=650)
                    require(!sidebar || !sidebar->Active || !rendered_panel ||
                            sidebar->Pos.x+sidebar->Size.x+12<=rendered_panel->Pos.x,
                            "Default sidebar and main editor overlap");
                for (auto* card:screenshot_context->Windows) if(card->Active && std::strstr(card->Name,"##dof-card")) {
                    const auto* fields=screenshot_context->Tables.GetByKey(card->GetID("##focus-ranges"));
                    require(fields,"Focus range fields are missing");
                    const auto caption=ImGui::GetFont()->CalcTextSizeA(card->FontRefSize,FLT_MAX,0,"N. blurry");
                    const auto number=ImGui::GetFont()->CalcTextSizeA(card->FontRefSize,FLT_MAX,0,"2000.00");
                    const float field_scale=card->FontRefSize/16.f;
                    for(int column=0;column<2;++column)
                        require(fields->Columns[column].WorkMaxX-fields->Columns[column].WorkMinX>=
                                std::max(caption.x,number.x+4*field_scale),
                                "Narrow focus fields clip their labels or typical DOF values");
                }
                const auto* transport = ImGui::FindWindowByName("##lite-timeline");
                require(!sidebar || !sidebar->Active || sidebar->Pos.y + sidebar->Size.y <= transport->Pos.y - 12,
                        "Layout fixture campaths cover the timeline");
                // A post-Begin move can make logical bounds pass while the
                // already emitted background still overlaps the transport.
                if (sidebar && sidebar->Active) for (const auto& vertex : sidebar->DrawList->VtxBuffer)
                    require(vertex.pos.y <= sidebar->Pos.y + sidebar->Size.y + 1,
                            "Campath background or controls extend below its logical bounds");
                std::printf("Layout %ux%u with %u cameras: working surfaces fit; track %.0f px.\n",
                            render_width, render_height, snapshot.camera_count,
                            dolly::overlay_diagnostics().timeline_x1 - dolly::overlay_diagnostics().timeline_x0);
            }
            std::ofstream output(argv[2], std::ios::binary);
            unsigned capture_x=0,capture_y=0,capture_width=texture.Width,capture_height=texture.Height;
            const bool sidebar_only=std::getenv("DOLLY_UI_TEST_SIDEBAR_ONLY")!=nullptr;
            if (sidebar_only || std::getenv("DOLLY_UI_TEST_TIMELINE_ONLY")) {
                const auto* surface=ImGui::FindWindowByName(sidebar_only ? "##lite-campaths" : "##lite-timeline");
                require(surface && surface->Active,"Surface screenshot requires a visible panel");
                capture_x=unsigned(std::max(0.f,surface->Pos.x-8));
                capture_y=unsigned(std::max(0.f,surface->Pos.y-8));
                capture_width=std::min(texture.Width-capture_x,unsigned(surface->Size.x+16));
                capture_height=std::min(texture.Height-capture_y,unsigned(surface->Size.y+16));
            }
            output << "P6\n" << capture_width << " " << capture_height << "\n255\n";
            for (unsigned y = capture_y; y < capture_y+capture_height; ++y) {
                const auto row = static_cast<const char*>(pixels.pData) + y * pixels.RowPitch;
                for (unsigned x = capture_x; x < capture_x+capture_width; ++x)
                    output.write(row + x * 4, 3);
            }
            output.close();
            require(bool(output), "Could not save the panel screenshot");
            context->Unmap(staging, 0);
            staging->Release();
            return 0;
        }
        context->Unmap(staging, 0);
        staging->Release();
        require(drawn, "Present ran but the in-game panel did not change the backbuffer");
        ui_storage_regressions();
        layout_crash_restore_regressions();
        layout_view_restore_regressions(viewer);
        default_panel_placement_regressions(1);
        default_panel_placement_regressions(1.5f);
        shader_notice_regressions(1.f);
        shader_notice_regressions(1.5f);
        timeline_bottom_dock_regressions();
        timeline_saved_height_regressions(1.f);
        timeline_saved_height_regressions(1.5f);
        card_row_alignment_regressions(1);
        card_row_alignment_regressions(1.5f);
        dropdown_preview_regressions(1);
        dropdown_preview_regressions(1.5f);
        panel_option_regressions();
        ui_rework_regressions();
        unified_curve_regressions(viewer);
        unified_curve_regressions(viewer,1.5f);
        tangent_ui_regressions(viewer,1.f);
        tangent_ui_regressions(viewer,1.5f);
        preview_runtime_regressions(viewer);
        // Exercise rendered UI shortcuts with local ImGui events. This is not
        // OS-wide input and does not launch or attach to Deadlock.
        {
            ImGui::SetCurrentContext(screenshot_context);
            auto* panel = ImGui::FindWindowByName("Deadlock Dolly");
            require(panel != nullptr, "Camera panel missing");
            ImGui::FocusWindow(panel);
            auto render = [&] {
                require(SUCCEEDED(chain->Present(0, 0)), "Camera UI Present failed");
            };
            render();
            // Cinematic playback is reachable from every page and both modes.
            auto* transport = ImGui::FindWindowByName("##lite-timeline");
            const auto narrow_track = dolly::overlay_diagnostics();
            require(narrow_track.timeline_x1 - narrow_track.timeline_x0 >= 240,
                    "Narrow editor leaves too little timeline width for camera editing");
            snapshot.camera_count = 32;
            render(); render(); render();
            auto* long_campaths = ImGui::FindWindowByName("##lite-campaths");
            if (!long_campaths || !long_campaths->Active) {
                screenshot_context->NavNextActivateId=panel->GetID("Paths"); render(); render();
                long_campaths=ImGui::FindWindowByName("##lite-campaths");
            }
            require(long_campaths && long_campaths->Pos.y >= 0 &&
                        long_campaths->Pos.y + long_campaths->Size.y <= transport->Pos.y - 12,
                    "Long campath covers the timeline or pushes sidebar actions off screen");
            snapshot.camera_count = 2;
            render(); render();
            if (!panel->Active) {
                screenshot_context->NavNextActivateId=long_campaths->GetID("Tools"); render(); render();
            }
            auto play_cinematic = [&] {
                observed_actions.clear();
                screenshot_context->NavNextActivateId=transport->GetID("Play cinematic"); render(); render();
                require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::PrepareCinematic,
                        "Play cinematic must first prepare the replay");
                dolly::cinematic_status_stub.token=unsigned(observed_actions[0].value);
                dolly::cinematic_status_stub.revision=camera_revision_stub;
                dolly::cinematic_status_stub.state=1;
                render();render();
                require(observed_actions.size()==1,"Cinematic starts before preparation finishes");
                dolly::cinematic_status_stub.state=2;render();render();render();
                const auto* prompt=ImGui::FindWindowByName("##cinematic-ready");
                require(!prompt || !prompt->Active,"Cinematic still opens the Space prompt");
                require(observed_actions.size()==2 && observed_actions[1].action==dolly::EditorAction::PlayPath,
                        "Prepared cinematic must automatically play exactly once");
            };
            const char* pages[] = {"Camera###CAMERA","Follow###FOLLOW","Bone Picker###BONE PICKER","Look###LOOK","Export###EXPORT"};
            for (const char* page : pages) {
                screenshot_context->NavNextActivateId=panel->GetID(page);render();render();
                play_cinematic();
            }
            screenshot_context->NavNextActivateId=panel->GetID("Camera###CAMERA");render();
            screenshot_context->NavNextActivateId=transport->GetID("Replay###lite-timeline-mode");render();render();
            play_cinematic();
            observed_actions.clear();
            snapshot.follow_available = true;
            screenshot_context->NavNextActivateId=transport->GetID("##next-player");render();
            require(observed_actions.size()==1 && observed_actions[0].action==dolly::EditorAction::FocusReplayPlayer,
                    "Named player navigation must request the verified camera selection");
            screenshot_context->NavNextActivateId=transport->GetID("Campaths###lite-campath-mode");render();render();
            observed_actions.clear();
            std::puts("Every editor tab: cinematic in both timeline modes and compact player navigation passed.");
            auto shortcut = [&](ImGuiKey key, bool shift, dolly::EditorAction expected) {
                observed_actions.clear();
                auto& io = ImGui::GetIO();
                io.AddKeyEvent(ImGuiMod_Ctrl, true);
                io.AddKeyEvent(ImGuiMod_Shift, shift);
                io.AddKeyEvent(key, true);
                render();
                render();
                require(observed_actions.size() == 1 && observed_actions[0].action == expected &&
                            observed_actions[0].revision == 7,
                        "Rendered history shortcut did not enqueue one revision-bound action");
                io.AddKeyEvent(key, false);
                io.AddKeyEvent(ImGuiMod_Ctrl, false);
                io.AddKeyEvent(ImGuiMod_Shift, false);
                render();
            };
            shortcut(ImGuiKey_Z, false, dolly::EditorAction::UndoShot);
            shortcut(ImGuiKey_Y, false, dolly::EditorAction::RedoShot);
            shortcut(ImGuiKey_Z, true, dolly::EditorAction::RedoShot);
            std::puts("Rendered history shortcuts: Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z passed.");
            // Draggable camera ticks: Alt+drag pins the playhead and commits
            // exactly one revision-bound SetCameraTime on release; a plain drag
            // scrubs the slider instead of grabbing the tick.
            {
                viewer.publish(viewer_packet(100, camera_revision_stub));
                viewer.tick();
                render();
                const auto timeline = dolly::visualization_snapshot();
                require(timeline && timeline->cameras().size() == 2,
                        "Synthetic timeline missing for the tick drag test");
                const auto before = dolly::overlay_diagnostics();
                require(before.timeline_x1 > before.timeline_x0 && before.timeline_y > 0,
                        "Shot timeline rect was not recorded");
                const auto& camera = timeline->cameras()[0];
                const float span = std::max(float(snapshot.duration), .001f);
                const float tick_x =
                    before.timeline_x0 +
                    (before.timeline_x1 - before.timeline_x0) *
                        std::clamp(float(camera.time) / span, 0.0f, 1.0f);
                const float target_x = std::min(before.timeline_x1 - 2.0f, tick_x + 40);
                observed_actions.clear();
                auto& io = ImGui::GetIO();
                auto drag = [&](bool down, float x) {
                    io.AddMousePosEvent(x, before.timeline_y);
                    io.AddMouseButtonEvent(0, down);
                    render();
                };
                // A plain drag must scrub without moving a tick.
                drag(false, tick_x);
                drag(true, tick_x);
                drag(true, target_x);
                drag(false, target_x);
                require(observed_actions.empty() &&
                            dolly::overlay_diagnostics().timeline_drags ==
                                before.timeline_drags,
                        "Plain timeline drag must scrub without moving a tick");
                // A guide snapshot from an older project revision must not pair
                // its camera index with a newer camera list.
                require(timeline->project_revision() == 7, "Synthetic guide revision changed");
                camera_revision_stub = 8;
                io.AddKeyEvent(ImGuiMod_Alt, true);
                io.MouseClickedTime[0] = -1e30;
                drag(false, tick_x);
                drag(true, tick_x);
                drag(true, target_x);
                drag(false, target_x);
                io.AddKeyEvent(ImGuiMod_Alt, false);
                render();
                camera_revision_stub = 7;
                render();
                require(observed_actions.empty() &&
                            dolly::overlay_diagnostics().timeline_drags ==
                                before.timeline_drags,
                        "Alt tick drag paired a stale guide index with a newer camera list");
                // Alt+drag moves the tick and commits once on release.
                io.AddKeyEvent(ImGuiMod_Alt, true);
                io.MouseClickedTime[0] = -1e30;
                drag(false, tick_x);
                drag(true, tick_x);
                drag(true, target_x);
                drag(false, target_x);
                io.AddKeyEvent(ImGuiMod_Alt, false);
                render();
                const double expected_time =
                    (std::nearbyint(3389 + 64 * double(span) * double((target_x - before.timeline_x0) /
                                          (before.timeline_x1 - before.timeline_x0))) - 3389) / 64;
                if (observed_actions.size() != 1)
                    std::fprintf(stderr, "Tick drag actions=%zu before=%llu after=%llu\n", observed_actions.size(),
                        static_cast<unsigned long long>(before.timeline_drags),
                        static_cast<unsigned long long>(dolly::overlay_diagnostics().timeline_drags));
                require(observed_actions.size() == 1 &&
                            observed_actions[0].action == dolly::EditorAction::SetCameraTime &&
                            observed_actions[0].value == camera.index &&
                            observed_actions[0].revision == 7 &&
                            std::abs(observed_actions[0].time - expected_time) < 1e-3,
                        "Alt tick drag did not enqueue one revision-bound SetCameraTime");
                require(dolly::overlay_diagnostics().timeline_drags ==
                            before.timeline_drags + 1,
                        "Alt tick drag was not counted as committed");
                std::puts("Rendered timeline: plain drag scrubs; Alt+drag commits SetCameraTime passed.");
            }
            // The Shot timeline wheel-zooms its visible window and right-drag
            // pans it; wheeling out restores the full duration.
            {
                const auto before_zoom = dolly::overlay_diagnostics();
                const float full = before_zoom.timeline_view_end - before_zoom.timeline_view_start;
                require(full > 0, "Shot timeline view window was not recorded");
                const float mid_x = (before_zoom.timeline_x0 + before_zoom.timeline_x1) * .5f;
                auto& io = ImGui::GetIO();
                io.AddMousePosEvent(mid_x, before_zoom.timeline_y);
                io.AddMouseWheelEvent(0.0f, 1.0f);
                render();
                render();
                const auto zoomed = dolly::overlay_diagnostics();
                const float span = zoomed.timeline_view_end - zoomed.timeline_view_start;
                require(span < full, "Wheel over the Shot timeline did not zoom in");
                require(zoomed.timeline_view_start >= -1e-3f &&
                            zoomed.timeline_view_end <= float(snapshot.duration) + 1e-3f,
                        "Zoomed Shot timeline left the shot duration");
                // Right-drag pans the zoomed window (dragging right moves the
                // visible window earlier in the shot).
                io.AddMousePosEvent(mid_x, zoomed.timeline_y);
                io.AddMouseButtonEvent(1, true);
                render();
                io.AddMousePosEvent(mid_x + 40.0f, zoomed.timeline_y);
                render();
                io.AddMouseButtonEvent(1, false);
                render();
                require(dolly::overlay_diagnostics().timeline_view_start <
                            zoomed.timeline_view_start,
                        "Right-drag did not pan the zoomed Shot timeline");
                // Wheel out restores the full duration.
                for (int i = 0; i < 40; ++i) {
                    io.AddMousePosEvent(mid_x, zoomed.timeline_y);
                    io.AddMouseWheelEvent(0.0f, -1.0f);
                    render();
                }
                const auto restored = dolly::overlay_diagnostics();
                require(std::abs((restored.timeline_view_end - restored.timeline_view_start) - full) < 1e-2f,
                        "Wheel out did not restore the full Shot timeline");
                std::puts("Rendered timeline: wheel zoom, right-drag pan and full-range restore passed.");
            }
            // A camera captured past the visible range comes into view; the
            // window is not left on the older, shorter path.
            {
                const auto saved = snapshot;
                viewer.publish(curve_packet(3, camera_revision_stub));
                viewer.tick();
                snapshot.camera_count = 3;
                snapshot.duration = 6;
                render();
                render();
                require(std::abs(dolly::overlay_diagnostics().timeline_view_end - 6) < 1e-3f,
                        "Shot timeline did not fit a longer camera path");
                viewer.publish(curve_packet(4, camera_revision_stub));
                viewer.tick();
                snapshot.camera_count = 4;
                snapshot.duration = 9;
                render();
                render();
                const auto grown = dolly::overlay_diagnostics();
                require(grown.timeline_view_start < 1e-3f &&
                            std::abs(grown.timeline_view_end - 9) < 1e-3f,
                        "A camera added past the visible range stayed off the Shot timeline");
                snapshot = saved;
                viewer.publish(viewer_packet(100, camera_revision_stub));
                viewer.tick();
                render();
                render();
                std::puts("Rendered timeline: a longer camera path refits the view passed.");
            }
            // Framing grid: fixed thirds and centre cross while the paused
            // editor owns the view; independent of the path-guides toggle.
            {
                const auto before = dolly::overlay_diagnostics();
                dolly::framing_grid_enabled_stub = true;
                render();
                render();
                require(dolly::overlay_diagnostics().grid_frames == before.grid_frames + 2,
                        "Framing grid did not draw while the paused editor owned the view");
                const auto before_closed = dolly::overlay_diagnostics();
                const auto owner = snapshot.owner;
                snapshot.owner = dolly::EditorOwner::Flight;
                render();
                require(dolly::overlay_diagnostics().grid_frames == before.grid_frames + 3 &&
                            dolly::overlay_diagnostics().panel_frames == before_closed.panel_frames,
                        "Closing the panels hides the framing grid or redraws the main menu");
                snapshot.owner = owner;
                dolly::framing_grid_enabled_stub = false;
                render();
                require(dolly::overlay_diagnostics().grid_frames == before.grid_frames + 3,
                        "Framing grid kept drawing after being disabled");
                std::puts("Rendered framing grid: enabled/disabled draw counts passed.");
            }
            auto* cameras = ImGui::FindWindowByName("##lite-campaths");
            if (!cameras || !cameras->Active) {
                screenshot_context->NavNextActivateId=panel->GetID("Paths"); render(); render();
                cameras=ImGui::FindWindowByName("##lite-campaths");
            }
            require(cameras && cameras->Active, "Rendered camera cards missing");
            auto* chooser=screenshot_context->Tables.GetByKey(cameras->GetID("##lite-campath-details"));
            require(chooser && chooser->ColumnsCount==4,"Rendered camera table is missing");
            const float x = (chooser->Columns[0].WorkMinX+chooser->Columns[0].WorkMaxX)*.5f;
            const float compact_header = ImGui::GetIO().DisplaySize.x < 900 || ImGui::GetIO().DisplaySize.y < 650
                                             ? ImGui::GetFrameHeightWithSpacing() : 0;
            const float y = chooser->OuterRect.Min.y+chooser->InstanceDataFirst.LastTopHeadersRowHeight+chooser->RowMinHeight*1.5f;
            auto mouse = [&](bool down) {
                ImGui::GetIO().AddMousePosEvent(x, y);
                ImGui::GetIO().AddMouseButtonEvent(0, down);
                render();
            };
            observed_actions.clear();
            mouse(false);
            mouse(true);
            mouse(false);
            if (observed_actions.size() != 1 ||
                observed_actions[0].action != dolly::EditorAction::SelectCamera ||
                observed_actions[0].value != 1) {
                std::fprintf(stderr, "Chooser click at %.1f %.1f: %zu actions\n", x, y,
                             observed_actions.size());
                for (const auto& action : observed_actions)
                    std::fprintf(stderr, "Action %u value %.1f revision %.1f\n",
                                 unsigned(action.action), action.value, action.revision);
            }
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::SelectCamera &&
                        observed_actions[0].value == 1 && observed_actions[0].revision == 7,
                    "A camera row click must select only, with its current revision");
            observed_actions.clear();
            mouse(true);
            mouse(false);
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::ViewCamera &&
                        observed_actions[0].value == 1 && observed_actions[0].revision == 7,
                    "A camera row double-click must request the selected view");
            std::puts("Rendered camera chooser: click selects, double-click views passed.");
            if (compact_header) {
                screenshot_context->NavNextActivateId=cameras->GetID("Tools"); render(); render();
            }
        }
        check_guide_visibility(chain, device, context, target, backbuffer);
        stress_game_buffer_lifetimes(chain, device, context, target);
        viewer.check_lifecycle();
        // F9 gives Deadlock its own mouse capture for hero/replay UI interaction.
        // A hidden ImGui backend used to receive every mouse-up and ReleaseCapture
        // even when Dolly did not own the click. Do not touch that capture.
        SetCapture(window);
        require(GetCapture() == window, "Could not establish game mouse capture");
        snapshot.owner = dolly::EditorOwner::GameUI;
        SendMessageW(window, WM_LBUTTONUP, 0, 0);
        require(GetCapture() == window, "Hidden overlay released the game's mouse capture");
        require(SUCCEEDED(chain->Present(0, 0)), "Hidden-overlay Present failed");
        require(GetCapture() == window, "Hidden-overlay Present changed game mouse capture");
        snapshot.owner = dolly::EditorOwner::Panel;
        SendMessageW(window, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(30, 30));
        require(SUCCEEDED(chain->Present(0, 0)), "Panel Present for legacy mouse-down failed");
        require(observed_mouse_down[0], "Legacy mouse-down did not reach the panel");
        SendMessageW(window, WM_LBUTTONUP, 0, MAKELPARAM(30, 30));
        require(GetCapture() == window, "Panel input changed an existing game mouse capture");
        require(SUCCEEDED(chain->Present(0, 0)), "Panel Present after UI handoff failed");
        require(!observed_mouse_down[0], "Legacy mouse-up did not reach the panel");
        require(GetCapture() == window, "Panel Present changed game mouse capture");
        dolly::overlay_raw_mouse_button(0, true);
        require(SUCCEEDED(chain->Present(0, 0)) && observed_mouse_down[0],
                "Raw-only mouse-down did not reach the panel");
        dolly::overlay_raw_mouse_button(0, false);
        require(SUCCEEDED(chain->Present(0, 0)) && !observed_mouse_down[0],
                "Raw-only mouse-up did not reach the panel");
        require(GetCapture() == window, "Raw-only input changed game mouse capture");
        require(present_keeps_os_cursor, "Present is allowed to change the OS mouse cursor");
        ReleaseCapture();
        // Exercise the real transparent picker canvas, not merely the pure
        // selection helpers. The original implementation drew circles but
        // provided no ImGui surface that could own a scene click.
        {
            auto catalog = std::make_shared<dolly::PickerCatalog>();
            catalog->sequence = 42;
            catalog->total = 4;
            catalog->portrait.width = catalog->portrait.height = 2;
            catalog->portrait.bgra = {11, 22, 33, 255, 11, 22, 33, 255,
                                      11, 22, 33, 255, 11, 22, 33, 255};
            dolly::PickerSample sample{};
            sample.count = 4;
            const char* names[] = {"head", "pelvis", "hand_R", "ankle_L"};
            for (unsigned i = 0; i < 4; ++i) {
                dolly::PickerBone bone{};
                bone.source_index = i;
                std::strcpy(bone.name, names[i]);
                catalog->bones.push_back(bone);
                sample.transforms[i][2] = 90 - 25 * float(i);
            }
            sample.transforms[2][1] = -35;
            dolly::picker_publish(catalog);
            auto pose = snapshot.pose;
            snapshot.bone_picker = true;
            auto render_picker = [&] {
                dolly::picker_camera(
                    catalog->sequence, 10, true, true, pose, 90, {},
                    [](void* p, dolly::PickerSample& out, const char*&) noexcept {
                        out = *static_cast<dolly::PickerSample*>(p);
                        return true;
                    },
                    &sample);
                require(SUCCEEDED(chain->Present(0, 0)), "Picker Present failed");
            };
            render_picker();
            render_picker();
            // The UI borrows the renderer's portrait; validate the actual GPU
            // pixels and request-change clearing, not an implementation mock.
            auto portrait_view = [&]() -> ID3D11ShaderResourceView* {
                auto* picker = ImGui::FindWindowByName("Bone Picker");
                require(picker != nullptr, "Picker panel missing");
                for (const auto& command : picker->DrawList->CmdBuffer)
                    if (command.GetTexID() && command.GetTexID() != ImGui::GetIO().Fonts->TexID)
                        return reinterpret_cast<ID3D11ShaderResourceView*>(command.GetTexID());
                return nullptr;
            };
            auto check_portrait = [&](unsigned char expected_blue) {
                auto* view = portrait_view();
                require(view != nullptr, "Portrait image missing from picker draw data");
                ID3D11Resource* resource = nullptr;
                view->GetResource(&resource);
                ID3D11Texture2D* image = nullptr;
                require(SUCCEEDED(resource->QueryInterface(__uuidof(ID3D11Texture2D),
                                                           reinterpret_cast<void**>(&image))),
                        "Portrait is not a texture");
                resource->Release();
                D3D11_TEXTURE2D_DESC description{};
                image->GetDesc(&description);
                require(description.Width == 2 && description.Height == 2 &&
                            description.Format == DXGI_FORMAT_B8G8R8A8_UNORM,
                        "Portrait texture dimensions/format changed");
                description.Usage = D3D11_USAGE_STAGING;
                description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
                description.BindFlags = description.MiscFlags = 0;
                ID3D11Texture2D* readback = nullptr;
                require(SUCCEEDED(device->CreateTexture2D(&description, nullptr, &readback)),
                        "Portrait readback creation failed");
                context->CopyResource(readback, image);
                D3D11_MAPPED_SUBRESOURCE pixels{};
                require(SUCCEEDED(context->Map(readback, 0, D3D11_MAP_READ, 0, &pixels)),
                        "Portrait readback failed");
                const auto* pixel = static_cast<const unsigned char*>(pixels.pData);
                const bool matches = pixel[0] == expected_blue && pixel[1] == 22 &&
                                     pixel[2] == 33 && pixel[3] == 255;
                context->Unmap(readback, 0);
                readback->Release();
                image->Release();
                require(matches, "Picker displayed stale or altered portrait pixels");
            };
            check_portrait(11);
            dolly::PickerFrame frame{};
            require(dolly::picker_snapshot(frame) && frame.ready, "Synthetic picker not ready");
            ImGui::SetCurrentContext(screenshot_context);
            const auto size = ImGui::GetIO().DisplaySize;
            dolly::VisualizationView view{frame.view, frame.fov, size.x, size.y, 1};
            std::array<double, 3> point{};
            dolly::VisualizationPoint screen{};
            require(dolly::picker_position(frame.sample, catalog->bones[2], point) &&
                        dolly::project_visualization_point(view, point, screen),
                    "Synthetic hand not visible");
            auto mouse = [&](bool down) {
                ImGui::SetCurrentContext(screenshot_context);
                ImGui::GetIO().AddMousePosEvent(float(screen.x), float(screen.y));
                ImGui::GetIO().AddMouseButtonEvent(0, down);
                render_picker();
            };
            mouse(false);
            mouse(true);
            mouse(false);
            require(dolly::picker_snapshot(frame) && frame.selected == 2,
                    "Clicking the visible hand marker did not select its source bone");
            const auto before_orbit = frame.view;
            dolly::overlay_raw_mouse_button(2, true);
            render_picker();
            ImGui::GetIO().AddMousePosEvent(float(screen.x) + 100, float(screen.y) + 20);
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view != before_orbit &&
                        frame.selected == 2,
                    "Middle drag must orbit the real canvas without selecting a new bone");
            dolly::overlay_raw_mouse_button(2, false);
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame), "Read released orbit");
            const auto stopped_view = frame.view;
            ImGui::GetIO().AddMousePosEvent(float(screen.x) + 150, float(screen.y) + 25);
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view == stopped_view,
                    "Mouse motion after release cannot orbit");
            view.pose = frame.view;
            require(dolly::project_visualization_point(view, point, screen),
                    "Hand visible after orbit");
            require(dolly::picker_select(42, 0),
                    "Select different bone before checking rotated hand");
            mouse(false);
            mouse(true);
            mouse(false);
            require(dolly::picker_snapshot(frame) && frame.selected == 2,
                    "Rotated marker hit target must follow the applied camera immediately");
            const auto before_panel_drag = frame.view;
            ImGui::GetIO().AddMousePosEvent(size.x - 100, 100);
            render_picker();
            dolly::overlay_raw_mouse_button(2, true);
            render_picker();
            ImGui::GetIO().AddMousePosEvent(float(screen.x), float(screen.y));
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view == before_panel_drag,
                    "Middle drag starting on the panel must not become a scene orbit");
            dolly::overlay_raw_mouse_button(2, false);
            render_picker();
            render_picker();
            dolly::overlay_raw_mouse_button(2, true);
            render_picker();
            ImGui::GetIO().AddFocusEvent(false);
            render_picker();
            ImGui::GetIO().AddFocusEvent(true);
            ImGui::GetIO().AddMousePosEvent(float(screen.x) + 50, float(screen.y));
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view == before_panel_drag,
                    "Losing focus must cancel a held middle drag");
            dolly::picker_camera(42, 10, false, true, pose, 90, {}, nullptr, nullptr);
            // A new request without artwork must not retain the old hero image.
            catalog = std::make_shared<dolly::PickerCatalog>(*catalog);
            catalog->sequence = 43;
            catalog->portrait = {};
            dolly::picker_publish(catalog);
            render_picker();
            render_picker();
            require(portrait_view() == nullptr, "New empty portrait retained the previous image");
            catalog = std::make_shared<dolly::PickerCatalog>(*catalog);
            dolly::picker_camera(43, 10, false, true, pose, 90, {}, nullptr, nullptr);
            catalog->sequence = 44;
            catalog->portrait.width = catalog->portrait.height = 2;
            catalog->portrait.bgra = {99, 22, 33, 255, 99, 22, 33, 255,
                                      99, 22, 33, 255, 99, 22, 33, 255};
            dolly::picker_publish(catalog);
            render_picker();
            render_picker();
            check_portrait(99);
            std::puts("Picker portrait: GPU pixels and request-change clearing/replacement passed.");
            dolly::picker_camera(44, 10, false, true, pose, 90, {}, nullptr, nullptr);
            snapshot.bone_picker = false;
            require(SUCCEEDED(chain->Present(0, 0)), "Picker cleanup Present failed");
        }
        // The Object Picker is its own right-side mode. Render it with one placed
        // proxy object and require its window plus projected wireframe line(s).
        {
            snapshot.object_picker = true;
            snapshot.ready = true;
            snapshot.horizontal_fov = 90;
            snapshot.view_width = 2560;
            snapshot.view_height = 1440;
            snapshot.object_count = 1;
            snapshot.object_selected = 0;
            snapshot.object_distance = 600.0;
            snapshot.object_items[0] = {};
            snapshot.object_items[0].shape = 0;      // marker (box)
            // Place the box straight ahead of the camera using the same math the
            // picker uses, so it is guaranteed on-screen for this frame.
            dolly::VisualizationView place_view{snapshot.pose, 90,
                                                double(snapshot.view_width),
                                                double(snapshot.view_height), 1};
            std::array<double, 3> centre{};
            require(dolly::object_place_screen(place_view, snapshot.view_width * 0.5,
                                               snapshot.view_height * 0.5, 600.0, centre),
                    "Object placement failed in smoke test");
            snapshot.object_items[0].position[0] = float(centre[0]);
            snapshot.object_items[0].position[1] = float(centre[1]);
            snapshot.object_items[0].position[2] = float(centre[2]);
            snapshot.object_items[0].scale = 40.0f;
            auto render_objects = [&] {
                require(SUCCEEDED(chain->Present(0, 0)), "Object Picker Present failed");
            };
            render_objects();
            render_objects();
            auto* window = ImGui::FindWindowByName("##object-picker");
            require(window != nullptr, "Object Picker window missing");
            auto* canvas = ImGui::FindWindowByName("##object-canvas");
            require(canvas != nullptr, "Object Picker scene canvas missing");
            // Proxy wireframes draw on the background draw list; count line
            // elements across the whole frame's draw data (post-Render).
            std::size_t lines = 0;
            const auto* data = ImGui::GetDrawData();
            for (int list = 0; list < data->CmdListsCount; ++list)
                for (const auto& command : data->CmdLists[list]->CmdBuffer)
                    lines += command.ElemCount;
            require(lines >= 2, "Object Picker drew no proxy wireframe lines");
            std::puts("Object Picker: right-side window and proxy wireframe render passed.");
            // Move gizmo math is exercised in object_picker_tests; here confirm
            // the panel still renders with a selection ring and the tool row.
            snapshot.object_selected = 0;
            render_objects();
            require(ImGui::FindWindowByName("##object-picker") != nullptr,
                    "Object Picker window missing with selection");
            snapshot.object_picker = false;
            snapshot.object_count = 0;
            snapshot.object_selected = -1;
            require(SUCCEEDED(chain->Present(0, 0)), "Object Picker cleanup Present failed");
        }
        // Exercise the real Present capture across the editor-to-path handoff.
        // A valid session starts without focus (desktop export), then survives
        // loss of manual input, pose readiness and later focus changes.
        wchar_t temporary[MAX_PATH]{};
        require(GetTempPathW(MAX_PATH, temporary) != 0, "Video temporary directory unavailable");
        const auto movie = std::wstring(temporary) + L"DollyHandoff-" +
                           std::to_wstring(GetCurrentProcessId()) + L".mp4";
        require(GetFileAttributesW(movie.c_str()) == INVALID_FILE_ATTRIBUTES,
                "Handoff output already exists");
        const auto saved_snapshot = snapshot;
        snapshot.focused = false;
        require(dolly::video::start(movie.c_str(), 30, 2000000), "Handoff recording start failed");
        for (unsigned stage = 0; stage < 3; ++stage) {
            if (stage == 1) {
                snapshot.focused = true;
                snapshot.enabled = false;
                snapshot.ready = false;
            }
            if (stage == 2)
                snapshot.focused = false;
            const auto goal = dolly::video::status().frames_written + 3;
            const auto deadline = GetTickCount64() + 10000;
            while (dolly::video::status().frames_written < goal && GetTickCount64() < deadline) {
                dolly::media_worker_tick(L"Local\\DollyHandoffSmoke", true);
                const float color[] = {0.2f, 0.5f, 0.8f, 1.0f};
                context->ClearRenderTargetView(target, color);
                require(SUCCEEDED(chain->Present(0, 0)), "Handoff recording Present failed");
                const auto video = dolly::video::status();
                require(video.state == dolly::video::State::starting ||
                            video.state == dolly::video::State::recording,
                        "Camera handoff or focus change stopped the recorder");
                Sleep(10);
            }
            require(dolly::video::status().frames_written >= goal,
                    stage == 0 ? "Unfocused recording never received its first game frame"
                               : "Recording stopped producing frames during camera handoff");
        }
        dolly::media_worker_tick(nullptr, false);
        require(!dolly::media_session_active(), "Disconnected media session remained active");
        dolly::video::shutdown();
        require(dolly::video::status().state == dolly::video::State::completed,
                "Disconnect did not finalize the recording");
        DeleteFileW(movie.c_str());
        snapshot = saved_snapshot;
        context->OMSetRenderTargets(0, nullptr, nullptr);
        target->Release();
        backbuffer->Release();
        require(SUCCEEDED(chain->ResizeBuffers(1, 720, 480, DXGI_FORMAT_UNKNOWN, 0)),
                "Overlay retained a backbuffer reference across ResizeBuffers");
        require(SUCCEEDED(chain->Present(0, 0)) && available,
                "Overlay did not recover after resize");
        framebuffer_scale_probe = true;
        require(SUCCEEDED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                           reinterpret_cast<void**>(&backbuffer))),
                "Resized backbuffer unavailable");
        require(SUCCEEDED(device->CreateRenderTargetView(backbuffer, nullptr, &target)),
                "Resized render target unavailable");
        const float black[] = {0, 0, 0, 1};
        context->ClearRenderTargetView(target, black);
        require(SUCCEEDED(chain->Present(0, 0)), "Scaled panel Present failed");
        backbuffer->GetDesc(&texture);
        texture.Usage = D3D11_USAGE_STAGING;
        texture.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        texture.BindFlags = texture.MiscFlags = 0;
        staging = nullptr;
        require(SUCCEEDED(device->CreateTexture2D(&texture, nullptr, &staging)),
                "Scaled panel readback texture failed");
        context->CopyResource(staging, backbuffer);
        require(SUCCEEDED(context->Map(staging, 0, D3D11_MAP_READ, 0, &pixels)),
                "Scaled panel readback failed");
        const auto* inside = static_cast<const unsigned char*>(pixels.pData) +
                             UINT(texture.Height * .94f) * pixels.RowPitch +
                             UINT(texture.Width * .90f) * 4;
        const auto* clipped = static_cast<const unsigned char*>(pixels.pData) +
                              UINT(texture.Height * .94f) * pixels.RowPitch +
                              UINT(texture.Width * .96f) * 4;
        const bool fitted = inside[0] > 240 && inside[1] < 8 && inside[2] > 240;
        // Persistent transport can legitimately draw below the clipped probe.
        // Its magenta pixels must not escape the requested clip rectangle.
        const bool scissors = !(clipped[0] > 240 && clipped[1] < 8 && clipped[2] > 240);
        context->Unmap(staging, 0);
        staging->Release();
        target->Release();
        backbuffer->Release();
        framebuffer_scale_probe = false;
        require(fitted, "Client-sized panel was clipped by a smaller render buffer");
        require(scissors, "Panel clip rectangles did not scale with the render buffer");
        dolly::shutdown_overlay();
        require(!available && attached == nullptr, "Shutdown did not release editor ownership");
        require(GetWindowLongPtrW(window, GWLP_WNDPROC) == before_proc,
                "Shutdown did not restore the original window callback");
        context->Release();
        device->Release();
        chain->Release();
        DestroyWindow(window);
        UnregisterClassW(wc.lpszClassName, instance);
        std::puts(
            "DX11 WARP overlay: panel/guides, mapping lifecycle, state restoration, buffer retirement, resize and UI mouse ownership passed.");
        return 0;
    } catch (const std::exception& error) {
        dolly::shutdown_overlay();
        std::fprintf(stderr, "Overlay smoke failed: %s\n", error.what());
        return 1;
    }
}
