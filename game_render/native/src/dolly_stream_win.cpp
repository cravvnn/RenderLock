#include "dolly_stream.hpp"

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
    SlotHeader tag{};
};
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
void publish(const D3D11_MAPPED_SUBRESOURCE& mapped, const SlotHeader& tag) noexcept {
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

void capture(IDXGISwapChain* chain, ID3D11Device* device, ID3D11DeviceContext* context) noexcept {
    {
        std::lock_guard<std::mutex> lock(map_lock);
        if (!memory || !header()->enabled)
            return;
        ++header()->presents;
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
    // Publish the frame copied two Presents ago (it is usually finished by now).
    const auto ready = std::uint32_t((present + 1) % kSlots);
    if (pending[ready].used) {
        D3D11_MAPPED_SUBRESOURCE mapped{};
        const HRESULT result =
            context->Map(staging[ready], 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (SUCCEEDED(result)) {
            publish(mapped, pending[ready].tag);
            context->Unmap(staging[ready], 0);
            pending[ready].used = false;
        } else if (result != DXGI_ERROR_WAS_STILL_DRAWING) {
            pending[ready].used = false;
        }
    }
    const auto target = std::uint32_t(present % kSlots);
    if (pending[target].used) {  // still not read back; drop the old copy
        pending[target].used = false;
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
    LARGE_INTEGER now{};
    QueryPerformanceCounter(&now);
    tag.qpc = now.QuadPart;
    pending[target] = Pending{true, tag};
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
