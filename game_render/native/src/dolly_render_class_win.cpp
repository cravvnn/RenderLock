#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11_1.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <unordered_map>
#include "dolly_render_class.hpp"

namespace dolly::classify {
namespace {
std::atomic<bool> gProbe{false};
std::atomic<std::uint64_t> gDraws{0}, gIndexed{0}, gInstanced{0}, gAutomatic{0};
std::atomic<std::uint64_t> gBlended{0}, gDepthWrite{0}, gNoLayout{0};
std::mutex gMutex;
struct Signature {
    const void* layout = nullptr;
    const void* vertex = nullptr;
    const void* pixel = nullptr;
    std::uint64_t draws = 0;
    std::uint32_t method = 0;
    bool blended = false, depth_write = false, no_layout = false;
};
std::array<Signature, 8> gTop{};
unsigned gTopCount = 0;
template <class T> struct Com {
    T* p = nullptr;
    ~Com() { reset(); }
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
    void reset(T* value = nullptr) noexcept {
        if (p)
            p->Release();
        p = value;
    }
    T** put() {
        reset();
        return &p;
    }
};
void record(const Signature& sample) noexcept {
    std::lock_guard<std::mutex> lock(gMutex);
    for (unsigned i = 0; i < gTopCount; ++i) {
        auto& entry = gTop[i];
        if (entry.layout == sample.layout && entry.vertex == sample.vertex &&
            entry.pixel == sample.pixel && entry.method == sample.method) {
            entry.draws += sample.draws;
            return;
        }
    }
    Signature* target = nullptr;
    if (gTopCount < gTop.size()) {
        target = &gTop[gTopCount++];
    } else {
        target = &gTop[0];
        for (auto& entry : gTop)
            if (entry.draws < target->draws)
                target = &entry;
    }
    *target = sample;
}
}
void probe(bool enabled) noexcept {
    if (enabled == gProbe.load(std::memory_order_acquire))
        return;
    {
        std::lock_guard<std::mutex> lock(gMutex);
        if (enabled) {
            gTop = {};
            gTopCount = 0;
        }
    }
    gProbe.store(enabled, std::memory_order_release);
}
bool probing() noexcept {
    return gProbe.load(std::memory_order_acquire);
}
void draw(ID3D11DeviceContext* context, unsigned method) noexcept {
    if (!context || !probing())
        return;
    try {
        gDraws.fetch_add(1, std::memory_order_relaxed);
        if (method == 0 || method == 2 || method == 5 || method == 6)
            gIndexed.fetch_add(1, std::memory_order_relaxed);
        if (method == 2 || method == 3 || method == 5 || method == 6)
            gInstanced.fetch_add(1, std::memory_order_relaxed);
        if (method == 4)
            gAutomatic.fetch_add(1, std::memory_order_relaxed);
        Com<ID3D11InputLayout> layout;
        context->IAGetInputLayout(layout.put());
        Com<ID3D11VertexShader> vertex;
        context->VSGetShader(vertex.put(), nullptr, nullptr);
        Com<ID3D11PixelShader> pixel;
        context->PSGetShader(pixel.put(), nullptr, nullptr);
        Com<ID3D11BlendState> blend;
        FLOAT factors[4]{};
        UINT mask = 0;
        context->OMGetBlendState(blend.put(), factors, &mask);
        Com<ID3D11DepthStencilState> depth;
        UINT stencil = 0;
        context->OMGetDepthStencilState(depth.put(), &stencil);
        Signature sample;
        sample.layout = layout.p;
        sample.vertex = vertex.p;
        sample.pixel = pixel.p;
        sample.draws = 1;
        sample.method = method;
        sample.no_layout = layout.p == nullptr;
        D3D11_BLEND_DESC blend_desc{};
        if (blend.p) {
            blend.p->GetDesc(&blend_desc);
            const auto& target = blend_desc.RenderTarget[0];
            sample.blended = target.BlendEnable &&
                             (target.SrcBlend != D3D11_BLEND_ONE ||
                              target.DestBlend != D3D11_BLEND_ZERO);
        }
        D3D11_DEPTH_STENCIL_DESC depth_desc{};
        depth_desc.DepthEnable = TRUE;
        depth_desc.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        if (depth.p)
            depth.p->GetDesc(&depth_desc);
        sample.depth_write = depth_desc.DepthEnable && depth_desc.DepthWriteMask == D3D11_DEPTH_WRITE_MASK_ALL;
        if (sample.blended)
            gBlended.fetch_add(1, std::memory_order_relaxed);
        if (sample.depth_write)
            gDepthWrite.fetch_add(1, std::memory_order_relaxed);
        if (sample.no_layout)
            gNoLayout.fetch_add(1, std::memory_order_relaxed);
        record(sample);
    } catch (...) {
    }
}
void diagnostic(char* output, unsigned capacity) noexcept {
    if (!output || !capacity)
        return;
    std::array<Signature, 8> top{};
    unsigned count = 0;
    {
        std::lock_guard<std::mutex> lock(gMutex);
        top = gTop;
        count = gTopCount;
    }
    int used = std::snprintf(
        output, capacity,
        "draws=%llu idx=%llu inst=%llu auto=%llu blend=%llu dw=%llu nolayout=%llu",
        static_cast<unsigned long long>(gDraws.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(gIndexed.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(gInstanced.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(gAutomatic.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(gBlended.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(gDepthWrite.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(gNoLayout.load(std::memory_order_relaxed)));
    for (unsigned i = 0; i < count && used > 0 && static_cast<unsigned>(used) < capacity; ++i) {
        const auto& entry = top[i];
        used += std::snprintf(output + used, capacity - static_cast<unsigned>(used),
                              " | l=%llx v=%llx p=%llx n=%llu m=%u %s%s%s",
                              static_cast<unsigned long long>(
                                  reinterpret_cast<std::uintptr_t>(entry.layout)),
                              static_cast<unsigned long long>(
                                  reinterpret_cast<std::uintptr_t>(entry.vertex)),
                              static_cast<unsigned long long>(
                                  reinterpret_cast<std::uintptr_t>(entry.pixel)),
                              static_cast<unsigned long long>(entry.draws), entry.method,
                              entry.blended ? "B" : "-",
                              entry.depth_write ? "D" : "-", entry.no_layout ? "N" : "-");
    }
}
}
