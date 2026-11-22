#include "dolly_stream.hpp"
#include "dolly_depth_scene.hpp"

#include <Windows.h>
#include <d3d11.h>
#include <dxgi.h>

#include <array>
#include <cmath>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>

namespace dolly::stream {
namespace {

std::mutex map_lock;  // guards the mapping below (worker opens, render thread writes)
HANDLE mapping{};
unsigned char* memory{};
std::uint64_t mapping_bytes{};
ULONGLONG next_open{};
std::uint64_t sequence{};

struct ViewNote {
    std::int32_t tick{};
    bool paused{};
    double phase{};
    double pose[7]{};
    std::uint32_t ack{};
    std::uint64_t serial{};
};
std::mutex view_lock;
ViewNote last_view{};

// Render-thread-only GPU state.
struct Pending {
    bool used{};
    bool depth{};  // a depth readback for this Present is queued
    SlotHeader tag{};
};
depth::Readback depth_queue;
ID3D11Device* gpu_device{};
std::array<ID3D11Texture2D*, kSlots> staging{};
ID3D11Texture2D* resolve_target{};
std::array<Pending, kSlots> pending{};
UINT width{}, height{};
DXGI_FORMAT format{DXGI_FORMAT_UNKNOWN};
std::uint64_t presents{};

void release_gpu() noexcept {
    for (auto& texture : staging)
        if (texture) {
            texture->Release();
            texture = nullptr;
        }
    if (resolve_target) {
        resolve_target->Release();
        resolve_target = nullptr;
    }
    for (auto& slot : pending)
        slot = Pending{};
    depth_queue.reset();
    gpu_device = nullptr;
    width = height = 0;
    format = DXGI_FORMAT_UNKNOWN;
}

void close_mapping() noexcept {
    if (memory)
        UnmapViewOfFile(memory);
    if (mapping)
        CloseHandle(mapping);
    memory = nullptr;
    mapping = nullptr;
    mapping_bytes = 0;
}

Header* header() noexcept { return reinterpret_cast<Header*>(memory); }

void set_error(std::uint32_t code, const char* text) noexcept {
    if (!memory)
        return;
    header()->error = code;
    std::snprintf(header()->message, sizeof(header()->message), "%s", text);
}

bool supported(DXGI_FORMAT value) noexcept {
    switch (value) {
    case DXGI_FORMAT_B8G8R8A8_UNORM:
    case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
    case DXGI_FORMAT_R8G8B8A8_UNORM:
    case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
    case DXGI_FORMAT_R10G10B10A2_UNORM:
        return true;
    default:
        return false;
    }
}

bool ensure_gpu(ID3D11Device* device, const D3D11_TEXTURE2D_DESC& source) noexcept {
    if (gpu_device == device && width == source.Width && height == source.Height &&
        format == source.Format && staging[0])
        return true;
    release_gpu();
    D3D11_TEXTURE2D_DESC desc{};
    desc.Width = source.Width;
    desc.Height = source.Height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = source.Format;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_STAGING;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    for (auto& texture : staging)
        if (FAILED(device->CreateTexture2D(&desc, nullptr, &texture))) {
            release_gpu();
            return false;
        }
    if (source.SampleDesc.Count > 1) {
        desc.Usage = D3D11_USAGE_DEFAULT;
        desc.CPUAccessFlags = 0;
        if (FAILED(device->CreateTexture2D(&desc, nullptr, &resolve_target))) {
            release_gpu();
            return false;
        }
    }
    gpu_device = device;
    width = source.Width;
    height = source.Height;
    format = source.Format;
    return true;
}

// Copy a mapped staging texture into the next mapping slot and publish it.
bool has_depth_slots() noexcept {
    return memory &&
           kHeaderBytes + 2 * std::uint64_t(kSlots) * header()->slot_bytes <= mapping_bytes;
}

void publish(const D3D11_MAPPED_SUBRESOURCE& mapped, const SlotHeader& tag,
             const depth::RawFrame* depth_frame) noexcept {
    std::lock_guard<std::mutex> lock(map_lock);
    if (!memory)
        return;
    auto* head = header();
    const std::uint64_t row_bytes = std::uint64_t(width) * 4;
    if (row_bytes * height > head->slot_bytes) {
        ++head->dropped;
        set_error(2, "Game frame is larger than the stream slots.");
        return;
    }
    const std::uint64_t seq = ++sequence;
    const std::uint32_t index = std::uint32_t(seq % kSlots);
    auto* slot = reinterpret_cast<SlotHeader*>(memory + kSlotHeaderOffset +
                                               index * kSlotHeaderBytes);
    auto* pixels = memory + kHeaderBytes + index * head->slot_bytes;
    InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&slot->seq), 0);
    for (UINT row = 0; row < height; ++row)
        std::memcpy(pixels + row * row_bytes,
                    static_cast<const unsigned char*>(mapped.pData) + row * mapped.RowPitch,
                    row_bytes);
    SlotHeader written = tag;
    if (depth_frame) {
        const auto& frame = depth_frame->frame;
        const std::uint64_t count = std::uint64_t(frame.width) * frame.height;
        auto* values = reinterpret_cast<float*>(memory + kHeaderBytes +
                                                (kSlots + index) * head->slot_bytes);
        if (!has_depth_slots() || count * 4 > head->slot_bytes) {
            written.depth_state = depth_failed;
        } else if (depth::convert(depth_frame->pixels.data(), depth_frame->pixels.size(),
                                  std::size_t(frame.width) * 4, frame.width, frame.height,
                                  depth_frame->format, frame.projection, values,
                                  std::size_t(count))) {
            written.depth_state = depth_ok;
            written.depth_width = frame.width;
            written.depth_height = frame.height;
            written.depth_near = frame.projection.near_clip;
            written.depth_far = frame.projection.far_clip;
            const auto& p = frame.projection;
            if (p.view_valid) {
                for (int i = 0; i < 3; ++i) {
                    written.game_position[i] = p.position[i];
                    written.game_forward[i] = p.forward[i];
                    written.game_up[i] = p.up[i];
                }
                written.game_proj_x = p.projection_matrix[0];
                written.game_proj_y = p.projection_matrix[5];
                written.depth_reserved = 1;
            }
        } else {
            written.depth_state = depth_failed;
        }
    }
    written.width = width;
    written.height = height;
    written.row_bytes = std::uint32_t(row_bytes);
    written.dxgi_format = std::uint32_t(format);
    std::memcpy(reinterpret_cast<unsigned char*>(slot) + 8,
                reinterpret_cast<const unsigned char*>(&written) + 8, sizeof(SlotHeader) - 8);
    InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&slot->seq), LONG64(seq));
    InterlockedExchange64(reinterpret_cast<volatile LONG64*>(&head->latest), LONG64(seq));
}

}  // namespace

void worker_tick(const wchar_t* session_name, bool connected) noexcept {
    try {
        std::lock_guard<std::mutex> lock(map_lock);
        const bool named = session_name && *session_name && wcsnlen(session_name, 256) != 256;
        if (!named || !connected) {
            close_mapping();
            return;
        }
        if (memory)
            return;
        const auto now = GetTickCount64();
        if (now < next_open)
            return;
        next_open = now + 250;
        const std::wstring name = std::wstring(session_name) + L".frames";
        mapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, name.c_str());
        if (!mapping)
            return;
        memory = static_cast<unsigned char*>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, 0));
        MEMORY_BASIC_INFORMATION info{};
        if (!memory || !VirtualQuery(memory, &info, sizeof(info))) {
            close_mapping();
            return;
        }
        mapping_bytes = info.RegionSize;
        const auto* head = header();
        if (std::memcmp(head->magic, kMagic, sizeof(kMagic)) != 0 || head->version != kVersion ||
            head->slots != kSlots ||
            kHeaderBytes + std::uint64_t(kSlots) * head->slot_bytes > mapping_bytes) {
            close_mapping();
            return;
        }
    } catch (...) {
        close_mapping();
    }
}

void note_view(std::int32_t tick, bool paused, double phase, const double pose[6], double fov,
               std::uint32_t camera_ack) noexcept {
    std::lock_guard<std::mutex> lock(view_lock);
    last_view.tick = tick;
    last_view.paused = paused;
    last_view.phase = phase;
    for (int i = 0; i < 6; ++i)
        last_view.pose[i] = pose[i];
    last_view.pose[6] = fov;
    last_view.ack = camera_ack;
    ++last_view.serial;
}

void capture(IDXGISwapChain* chain, ID3D11Device* device, ID3D11DeviceContext* context,
             const depth::SceneFrame* scene) noexcept {
    bool want_depth = false;
    {
        std::lock_guard<std::mutex> lock(map_lock);
        if (!memory || !header()->enabled)
            return;
        ++header()->presents;
        want_depth = header()->depth && has_depth_slots();
    }
    if (!chain || !device || !context)
        return;
    ID3D11Texture2D* back = nullptr;
    if (FAILED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&back))))
        return;
    D3D11_TEXTURE2D_DESC desc{};
    back->GetDesc(&desc);
    if (!supported(desc.Format)) {
        back->Release();
        std::lock_guard<std::mutex> lock(map_lock);
        if (memory) {
            ++header()->dropped;
            set_error(1, "Unsupported game backbuffer format.");
        }
        return;
    }
    if (!ensure_gpu(device, desc)) {
        back->Release();
        return;
    }
    const std::uint64_t present = ++presents;
    // Publish finished copies oldest first (usually the one from two Presents
    // ago). A frame still waiting for its colour or depth holds back newer
    // ones, so frames stay in order and each keeps its own depth.
    for (std::uint64_t age = kSlots - 1; age >= 1; --age) {
        if (present <= age)
            continue;
        const auto ready = std::uint32_t((present - age) % kSlots);
        auto& slot = pending[ready];
        if (!slot.used)
            continue;
        D3D11_MAPPED_SUBRESOURCE mapped{};
        const HRESULT mapped_result =
            context->Map(staging[ready], 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (mapped_result == DXGI_ERROR_WAS_STILL_DRAWING)
            break;
        if (FAILED(mapped_result)) {
            slot = Pending{};
            continue;
        }
        depth::RawFrame depth_frame;
        bool depth_ready = false, waiting = false;
        if (slot.depth) {
            for (;;) {
                const auto result = depth_queue.poll(context, depth_frame);
                if (result == depth::ReadbackResult::ready) {
                    if (depth_frame.frame.sample < slot.tag.present)
                        continue;  // depth of a colour copy that was dropped
                    depth_ready = depth_frame.frame.sample == slot.tag.present;
                    break;
                }
                if (result == depth::ReadbackResult::pending) {
                    waiting = true;
                    break;
                }
                if (result != depth::ReadbackResult::empty) {
                    // A failed readback stays failed in the queue; start over.
                    slot.tag.depth_state = depth_failed;
                    depth_queue.reset();
                    for (auto& other : pending)
                        if (other.depth && &other != &slot) {
                            other.depth = false;
                            other.tag.depth_state = depth_failed;
                        }
                }
                break;
            }
        }
        if (waiting) {
            context->Unmap(staging[ready], 0);
            break;  // depth still on the GPU; try again next Present
        }
        publish(mapped, slot.tag, depth_ready ? &depth_frame : nullptr);
        context->Unmap(staging[ready], 0);
        slot = Pending{};
    }
    const auto target = std::uint32_t(present % kSlots);
    if (pending[target].used) {  // still not read back; drop the old copy
        pending[target] = Pending{};
        std::lock_guard<std::mutex> lock(map_lock);
        if (memory)
            ++header()->dropped;
    }
    if (resolve_target) {
        context->ResolveSubresource(resolve_target, 0, back, 0, desc.Format);
        context->CopyResource(staging[target], resolve_target);
    } else {
        context->CopyResource(staging[target], back);
    }
    back->Release();
    SlotHeader tag{};
    tag.present = present;
    {
        std::lock_guard<std::mutex> lock(view_lock);
        tag.view_serial = last_view.serial;
        tag.tick = last_view.tick;
        tag.paused = last_view.paused ? 1u : 0u;
        tag.phase = last_view.phase;
        for (int i = 0; i < 7; ++i)
            tag.pose[i] = last_view.pose[i];
        tag.camera_ack = last_view.ack;
    }
    bool queued_depth = false;
    if (want_depth) {
        tag.depth_state = depth_missing;
        depth::ProjectionBuffer calibration[14]{};
        const auto count = scene ? scene->calibration(calibration, 14) : 0;
        if (scene && scene->result == depth::SceneResult::ready && count && scene->width &&
            scene->height && scene->texture()) {
            const depth::Frame metadata{scene->width, scene->height, present, 0.0, {}};
            const auto result =
                depth_queue.enqueue(device, context, scene->texture(), metadata, calibration, count);
            if (result == depth::ReadbackResult::ready)
                queued_depth = true;
            else if (result != depth::ReadbackResult::full)
                tag.depth_state = depth_failed;
        }
    }
    LARGE_INTEGER now{};
    QueryPerformanceCounter(&now);
    tag.qpc = now.QuadPart;
    pending[target] = Pending{true, queued_depth, tag};
}

bool wants_depth() noexcept {
    std::lock_guard<std::mutex> lock(map_lock);
    return memory && header()->enabled && header()->depth && has_depth_slots();
}

bool camera(double out[8]) noexcept {
    std::lock_guard<std::mutex> lock(map_lock);
    if (!memory)
        return false;
    const auto* block = reinterpret_cast<const volatile CameraBlock*>(memory + kCameraOffset);
    for (int attempt = 0; attempt < 4; ++attempt) {
        const std::uint64_t before = block->seq;
        if (before & 1)
            continue;
        if (!block->enabled)
            return false;
        for (int i = 0; i < 8; ++i)
            out[i] = block->pose[i];
        MemoryBarrier();
        if (block->seq == before && before) {
            for (int i = 0; i < 8; ++i)
                if (!std::isfinite(out[i]))
                    return false;
            return out[6] > 1 && out[6] < 179 && out[7] > 0.1 && out[7] < 10;
        }
    }
    return false;
}

bool active() noexcept {
    std::lock_guard<std::mutex> lock(map_lock);
    return memory && header()->enabled;
}

void reset_resources() noexcept { release_gpu(); }

}  // namespace dolly::stream
