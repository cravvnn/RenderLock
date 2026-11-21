#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <cwchar>
#include <memory>
#include <string>
#include <vector>
#include "dolly_visualization_runtime.hpp"

namespace dolly {
namespace {
HANDLE mapping = nullptr;
const unsigned char* memory = nullptr;
HANDLE preview_mapping=nullptr;
const unsigned char* preview_memory=nullptr;
bool have_preview_sequence=false;
std::uint32_t preview_sequence=0;
std::shared_ptr<const VisualizationPreview> current_preview;
std::atomic<bool> preview_enabled{false};
std::array<std::array<std::uint32_t,64>,3> terminal_tokens{};
unsigned terminal_next[3]{};
std::atomic<std::uint32_t> next_native_token{0},native_token{0},native_revision{0},native_index{0},native_channel{0};
std::atomic<std::uint64_t> native_session{0},native_time{0};
std::wstring name;
ULONGLONG sampled_at = 0;
bool sampled = false, have_revision = false;
std::uint32_t revision = 0;
std::shared_ptr<const VisualizationPath> current;
// The worker owns replaced paths until the last drawing reader releases
// them. Destruction of large sampled vectors therefore stays off rendering.
std::vector<std::shared_ptr<const VisualizationPath>> retired;
constexpr std::size_t max_retired = 16;
std::atomic<VisualizationRuntimeState> state{VisualizationRuntimeState::Disconnected};

std::uint32_t read_sequence() noexcept {
    // The mapping is FILE_MAP_READ. InterlockedCompareExchange would write
    // to it; aligned volatile reads plus the full barrier are valid on the
    // required Windows x64 target and pair with the writer's Interlocked.
    const auto result = *reinterpret_cast<const volatile LONG*>(memory + 8);
    MemoryBarrier();
    return static_cast<std::uint32_t>(result);
}

void collect_retired() {
    retired.erase(std::remove_if(retired.begin(), retired.end(),
                                 [](const auto& path) { return path.use_count() == 1; }),
                  retired.end());
}

bool publish(std::shared_ptr<const VisualizationPath> path) {
    collect_retired();
    auto previous = std::atomic_load_explicit(&current, std::memory_order_acquire);
    if (previous) {
        // A stalled reader cannot force unbounded worker allocations. Retry
        // the newest revision next tick after pending readers have finished.
        if (retired.size() >= max_retired)
            return false;
        retired.push_back(previous);
    }
    std::atomic_store_explicit(&current, std::move(path), std::memory_order_release);
    return true;
}

bool publish_preview(std::shared_ptr<const VisualizationPreview> preview) {
    if(!preview) preview_enabled.store(false,std::memory_order_release);
    collect_retired();
    const auto previous=std::atomic_load_explicit(&current_preview,std::memory_order_acquire);
    if(previous && previous->path) {
        if(retired.size()>=max_retired) return false;
        retired.push_back(previous->path);
    }
    std::atomic_store_explicit(&current_preview,std::move(preview),std::memory_order_release);
    preview_enabled.store(bool(std::atomic_load_explicit(&current_preview,std::memory_order_acquire)),std::memory_order_release);
    return true;
}
void retire_token(std::uint32_t source,std::uint32_t token) {
    if(source && source<3 && token) terminal_tokens[source][terminal_next[source]++%64]=token;
}

void close_mapping() noexcept {
    if(preview_memory) {UnmapViewOfFile(preview_memory);preview_memory=nullptr;}
    if(preview_mapping) {CloseHandle(preview_mapping);preview_mapping=nullptr;}
    try {publish_preview(nullptr);} catch(...) {std::atomic_store(&current_preview,std::shared_ptr<const VisualizationPreview>{});}
    have_preview_sequence=false;
    terminal_tokens={};terminal_next[0]=terminal_next[1]=terminal_next[2]=0;
    native_token.store(0,std::memory_order_release);
    if (memory) {
        UnmapViewOfFile(memory);
        memory = nullptr;
    }
    if (mapping) {
        CloseHandle(mapping);
        mapping = nullptr;
    }
    have_revision = false;
    sampled = false;
    name.clear();
}

std::uint32_t preview_u32(const unsigned char* p) {
    return std::uint32_t(p[0])|(std::uint32_t(p[1])<<8)|(std::uint32_t(p[2])<<16)|(std::uint32_t(p[3])<<24);
}
void read_preview_snapshot() {
    const auto base=std::atomic_load_explicit(&current,std::memory_order_acquire);
    if(!base) {publish_preview(nullptr);return;}
    if(!preview_memory) {
        const auto requested=name+L".preview";
        preview_mapping=OpenFileMappingW(FILE_MAP_READ,FALSE,requested.c_str());
        if(preview_mapping) preview_memory=static_cast<const unsigned char*>(
            MapViewOfFile(preview_mapping,FILE_MAP_READ,0,0,kVisualizationMappingBytes));
        if(!preview_memory) {if(preview_mapping)CloseHandle(preview_mapping);preview_mapping=nullptr;return;}
    }
    for(unsigned attempt=0;attempt<2;++attempt) {
        const auto before=static_cast<std::uint32_t>(*reinterpret_cast<const volatile LONG*>(preview_memory+8));
        MemoryBarrier();
        if(!before || (before&1)) continue;
        if(have_preview_sequence && before==preview_sequence) return;
        unsigned char header[kVisualizationPreviewHeaderBytes];std::memcpy(header,preview_memory,sizeof(header));
        const std::size_t bytes=sizeof(header)+std::size_t(preview_u32(header+56));
        if(bytes>kVisualizationMappingBytes) {
            publish_preview(nullptr);preview_sequence=before;have_preview_sequence=true;return;
        }
        std::vector<unsigned char> packet(bytes);std::memcpy(packet.data(),preview_memory,bytes);MemoryBarrier();
        const auto after=static_cast<std::uint32_t>(*reinterpret_cast<const volatile LONG*>(preview_memory+8));
        if(before!=after || std::memcmp(header,packet.data(),sizeof(header))) continue;
        auto preview=std::make_shared<VisualizationPreview>();std::string error;
        if(!preview->load(packet.data(),packet.size(),*base,error)) publish_preview(nullptr);
        else if(!preview->path) {
            const auto previous=std::atomic_load_explicit(&current_preview,std::memory_order_acquire);
            if(preview->source) {
                retire_token(preview->source,preview->token);
                if(previous && previous->source==preview->source && previous->token==preview->token) publish_preview(nullptr);
            } else publish_preview(nullptr); // Invalid drafts may recover in the same gesture.
        } else if(std::find(terminal_tokens[preview->source].begin(),terminal_tokens[preview->source].end(),
                           preview->token)==terminal_tokens[preview->source].end()) {
            preview->received_ms=GetTickCount64();preview->base=base;publish_preview(std::move(preview));
        } else publish_preview(nullptr);
        preview_sequence=before;have_preview_sequence=true;return;
    }
}

bool header_bytes(const VisualizationHeader& h, std::size_t& bytes) noexcept {
    if (std::memcmp(h.magic, "DLYVIS01", 8) || (h.abi < 1 || h.abi > kVisualizationAbi) || h.enabled > 1 ||
        h.camera_count > NativePath::max_camera_keys || h.path_bytes > NativePath::max_bytes ||
        h.sequence == 0 || (h.sequence & 1))
        return false;
    const std::size_t times = std::size_t(h.camera_count) * sizeof(double);
    const std::size_t metadata = h.abi >= 3 ? h.key_metadata_bytes : 0;
    if (h.abi >= 3 && (metadata != std::size_t(h.camera_count) * (h.abi == 4 ? 136 : 64) ||
                       (h.flags & ~(h.abi == 4 ? 3u : 1u)) || h.reserved)) return false;
    bytes = kVisualizationHeaderBytes + times + metadata + std::size_t(h.path_bytes);
    return bytes <= kVisualizationMappingBytes;
}

void read_snapshot() {
    for (unsigned attempt = 0; attempt < 2; ++attempt) {
        const auto before = read_sequence();
        if (before & 1)
            continue;
        if (have_revision && before == revision)
            return;
        VisualizationHeader header{};
        std::memcpy(&header, memory, sizeof(header));
        MemoryBarrier();
        if (read_sequence() != before || header.sequence != before)
            continue;
        std::size_t bytes = 0;
        if (!header_bytes(header, bytes)) {
            if (publish(nullptr)) {
                revision = before;
                have_revision = true;
            }
            state.store(VisualizationRuntimeState::Invalid, std::memory_order_release);
            return;
        }
        std::vector<unsigned char> packet(bytes);
        std::memcpy(packet.data(), memory, bytes);
        MemoryBarrier();
        if (read_sequence() != before || std::memcmp(packet.data(), &header, sizeof(header)))
            continue;
        auto candidate = std::make_shared<VisualizationPath>();
        std::string error;
        const bool valid = candidate->load(packet.data(), packet.size(), error);
        const bool enabled = valid && candidate->enabled();
        if (!publish(enabled ? std::move(candidate) : nullptr))
            return;
        revision = before;
        have_revision = true;
        state.store(!valid    ? VisualizationRuntimeState::Invalid
                    : enabled ? VisualizationRuntimeState::Ready
                              : VisualizationRuntimeState::Disabled,
                    std::memory_order_release);
        return;
    }
    state.store(VisualizationRuntimeState::Updating, std::memory_order_release);
}
} // namespace

void visualization_worker_tick(const wchar_t* main_mapping_name, bool connected) noexcept {
    try {
        if (retired.capacity() < max_retired)
            retired.reserve(max_retired);
        collect_retired();
        if (!connected || !main_mapping_name || !*main_mapping_name) {
            publish(nullptr);
            close_mapping();
            state.store(VisualizationRuntimeState::Disconnected, std::memory_order_release);
            return;
        }
        // Native camera session names are short ASCII GUIDs. Bound a caller
        // error before constructing the optional mapping's derived name.
        const auto length = wcsnlen(main_mapping_name, 256);
        if (length == 256) {
            publish(nullptr);
            close_mapping();
            state.store(VisualizationRuntimeState::Unavailable, std::memory_order_release);
            return;
        }
        std::wstring requested(main_mapping_name, length);
        requested += L".viewer";
        if (requested != name) {
            publish(nullptr);
            close_mapping();
            name = std::move(requested);
        }
        const auto now = GetTickCount64();
        if (sampled && now - sampled_at < 100)
            return;
        sampled_at = now;
        sampled = true;
        if (!memory) {
            mapping = OpenFileMappingW(FILE_MAP_READ, FALSE, name.c_str());
            if (mapping) {
                memory = static_cast<const unsigned char*>(
                    MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, kVisualizationMappingBytes));
                if (!memory) {
                    CloseHandle(mapping);
                    mapping = nullptr;
                }
            }
            if (!memory) {
                state.store(VisualizationRuntimeState::Waiting, std::memory_order_release);
                return;
            }
        }
        read_snapshot();
        try {read_preview_snapshot();}
        catch(...) {try {publish_preview(nullptr);} catch(...) {}}
    } catch (...) {
        // Viewer sampling is optional. Allocation or parsing failures must
        // not terminate the camera worker or alter input ownership.
        try {
            publish(nullptr);
        } catch (...) {
        }
        state.store(VisualizationRuntimeState::Unavailable, std::memory_order_release);
    }
}

std::shared_ptr<const VisualizationPath> visualization_snapshot() noexcept {
    const auto active = state.load(std::memory_order_acquire);
    if (active != VisualizationRuntimeState::Ready && active != VisualizationRuntimeState::Updating)
        return {};
    return std::atomic_load_explicit(&current, std::memory_order_acquire);
}

std::shared_ptr<const VisualizationPreview> visualization_preview_snapshot() noexcept {
    if(!preview_enabled.load(std::memory_order_acquire)) return {};
    const auto preview=std::atomic_load_explicit(&current_preview,std::memory_order_acquire);
    if(!preview || !preview->path || GetTickCount64()-preview->received_ms>750 ||
       (preview->source==2 && (native_token.load(std::memory_order_acquire)!=preview->token ||
                              native_channel.load(std::memory_order_relaxed)!=preview->channel))) return {};
    if(preview->source==2) {
        const CameraPose anchor{double(preview->revision),0,0,preview->time,double(std::uint32_t(preview->session)),
            double(std::uint32_t(preview->session>>32)),double(preview->token)};
        if(!visualization_native_preview_request_matches(preview->index,anchor,preview->channel)) return {};
    }
    const auto base=visualization_snapshot();
    if(!base || base!=preview->base || base->project_revision()!=preview->revision ||
       base->session_id()!=preview->session || base->selected_camera()!=preview->index) return {};
    return preview;
}

std::uint32_t visualization_begin_native_preview(const VisualizationPath& path,unsigned index,unsigned channel) noexcept {
    if(index>=path.graph_keys().size() || channel>6 || !path.project_revision() || !path.session_id()) return 0;
    native_token.store(0,std::memory_order_release);
    auto token=next_native_token.fetch_add(1,std::memory_order_relaxed)+1;
    if(!token) token=next_native_token.fetch_add(1,std::memory_order_relaxed)+1;
    native_revision.store(path.project_revision(),std::memory_order_relaxed);
    native_session.store(path.session_id(),std::memory_order_relaxed);native_index.store(index,std::memory_order_relaxed);
    native_channel.store(channel,std::memory_order_relaxed);
    std::uint64_t bits;const double time=path.graph_keys()[index].time;std::memcpy(&bits,&time,sizeof(bits));
    native_time.store(bits,std::memory_order_relaxed);native_token.store(token,std::memory_order_release);
    return token;
}
void visualization_end_native_preview(std::uint32_t token) noexcept {
    native_token.compare_exchange_strong(token,0,std::memory_order_acq_rel);
}
bool visualization_native_preview_request_matches(double index,const CameraPose& request,unsigned channel) noexcept {
    for(double part:request) if(!std::isfinite(part)) return false;
    if(request[6]<=0 || request[6]>UINT32_MAX || request[6]!=std::floor(request[6]) ||
       index<0 || index!=std::floor(index) ||
       (channel!=UINT32_MAX && channel!=native_channel.load(std::memory_order_relaxed))) return false;
    const auto token=std::uint32_t(request[6]);
    if(native_token.load(std::memory_order_acquire)!=token || index!=native_index.load(std::memory_order_relaxed) ||
       request[0]!=native_revision.load(std::memory_order_relaxed) ||
       request[4]<0 || request[4]>UINT32_MAX || request[4]!=std::floor(request[4]) ||
       request[5]<0 || request[5]>UINT32_MAX || request[5]!=std::floor(request[5]) ||
       (std::uint64_t(request[4])|(std::uint64_t(request[5])<<32))!=native_session.load(std::memory_order_relaxed)) return false;
    std::uint64_t bits;std::memcpy(&bits,&request[3],sizeof(bits));
    return bits==native_time.load(std::memory_order_relaxed) && native_token.load(std::memory_order_acquire)==token;
}

VisualizationRuntimeState visualization_runtime_state() noexcept {
    return state.load(std::memory_order_acquire);
}
} // namespace dolly
