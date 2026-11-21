#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <array>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include "dolly_depth_readback.hpp"

namespace dolly::depth {
namespace {
template <class T> struct Com {
    T* p = nullptr;
    ~Com() { reset(); }
    void reset() noexcept {
        if (p)
            p->Release();
        p = nullptr;
    }
};
constexpr std::size_t kSlots = 3;
constexpr std::size_t kCalibrationSlots = 14;
constexpr std::uint64_t kByteLimit = 128ULL * 1024 * 1024;
bool same_device(ID3D11DeviceContext* context, ID3D11Texture2D* source,
                 ID3D11Device* expected) noexcept {
    Com<ID3D11Device> context_device, source_device;
    context->GetDevice(&context_device.p);
    source->GetDevice(&source_device.p);
    return context_device.p == expected && source_device.p == expected;
}
}

struct Readback::Impl {
    struct Slot {
        Com<ID3D11Texture2D> staging;
        std::array<Com<ID3D11Buffer>, kCalibrationSlots> calibration;
        std::array<std::uint32_t, kCalibrationSlots> sizes{};
        std::size_t calibration_count = 0;
        Frame frame;
    };
    std::array<Slot, kSlots> slots;
    Com<ID3D11Device> device;
    std::uint32_t width = 0, height = 0;
    DXGI_FORMAT dxgi = DXGI_FORMAT_UNKNOWN;
    Format format = Format::d24s8;
    std::size_t head = 0, count = 0;
    HRESULT failure = S_OK;
    const char* reason = "none";
    std::size_t calibration_candidates = 0, valid_calibrations = 0;
    void reset() noexcept {
        for (auto& slot : slots) {
            slot.staging.reset();
            for (auto& buffer : slot.calibration)
                buffer.reset();
            slot.sizes.fill(0);
            slot.calibration_count = 0;
        }
        device.reset();
        width = height = 0;
        dxgi = DXGI_FORMAT_UNKNOWN;
        head = count = 0;
        failure = S_OK;
        reason = "none";
        calibration_candidates = valid_calibrations = 0;
    }
};

Readback::Readback() : impl(std::make_unique<Impl>()) {}
Readback::~Readback() = default;
void Readback::reset() noexcept {
    impl->reset();
}
std::size_t Readback::pending() const noexcept {
    return impl->count;
}
long Readback::error() const noexcept {
    return impl->failure;
}
const char* Readback::diagnostic() const noexcept {
    static thread_local char text[192]{};
    std::snprintf(text, sizeof(text), "%s; calibration=%zu/%zu size=%ux%u", impl->reason,
                  impl->valid_calibrations, impl->calibration_candidates, impl->width,
                  impl->height);
    return text;
}

ReadbackResult Readback::enqueue(ID3D11Device* device, ID3D11DeviceContext* context,
                                 ID3D11Texture2D* source, const Frame& frame,
                                 const ProjectionBuffer* calibration,
                                 std::size_t calibration_count) noexcept {
    auto& state = *impl;
    if (FAILED(state.failure))
        return ReadbackResult::failed;
    if (!device || !context || !source || context->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE ||
        (!calibration_count && !frame.projection.valid()) ||
        calibration_count > kCalibrationSlots || (calibration_count && !calibration) ||
        !std::isfinite(frame.replay_time) || frame.replay_time < 0 ||
        !same_device(context, source, device))
        return ReadbackResult::invalid;
    std::array<std::uint32_t, kCalibrationSlots> copy_bytes{};
    for (std::size_t i = 0; i < calibration_count; ++i) {
        const auto& input = calibration[i];
        if (!input.buffer || input.offset % 16 || input.bytes % 16)
            return ReadbackResult::invalid;
        Com<ID3D11Device> owner;
        input.buffer->GetDevice(&owner.p);
        D3D11_BUFFER_DESC buffer{};
        input.buffer->GetDesc(&buffer);
        if (owner.p != device || input.offset > buffer.ByteWidth ||
            input.bytes > buffer.ByteWidth - input.offset)
            return ReadbackResult::invalid;
        copy_bytes[i] = std::min(640u, input.bytes ? input.bytes : buffer.ByteWidth - input.offset);
        if (copy_bytes[i] < 464)
            return ReadbackResult::invalid;
    }
    D3D11_TEXTURE2D_DESC desc{};
    source->GetDesc(&desc);
    if (!desc.Width || !desc.Height || desc.Width != frame.width || desc.Height != frame.height ||
        desc.Width > 16384 || desc.Height > 16384 || desc.MipLevels != 1 || desc.ArraySize != 1 ||
        desc.SampleDesc.Count != 1 || !(desc.BindFlags & D3D11_BIND_DEPTH_STENCIL) ||
        std::uint64_t(desc.Width) * desc.Height * 4 * kSlots > kByteLimit)
        return ReadbackResult::invalid;
    Format format;
    DXGI_FORMAT staging_format;
    if (desc.Format == DXGI_FORMAT_R24G8_TYPELESS || desc.Format == DXGI_FORMAT_D24_UNORM_S8_UINT) {
        format = Format::d24s8;
        staging_format = DXGI_FORMAT_R24G8_TYPELESS;
    } else if (desc.Format == DXGI_FORMAT_R32_TYPELESS || desc.Format == DXGI_FORMAT_D32_FLOAT) {
        format = Format::d32_float;
        staging_format = DXGI_FORMAT_R32_TYPELESS;
    } else {
        return ReadbackResult::invalid;
    }
    if (state.device.p && (state.device.p != device || state.width != desc.Width ||
                           state.height != desc.Height || state.dxgi != staging_format))
        return ReadbackResult::invalid; // Resize must explicitly end/reset the sample sequence.
    if (state.count == kSlots)
        return ReadbackResult::full;
    if (!state.device.p) {
        state.device.p = device;
        device->AddRef();
        state.width = desc.Width;
        state.height = desc.Height;
        state.dxgi = staging_format;
        state.format = format;
    }
    auto& slot = state.slots[(state.head + state.count) % kSlots];
    if (!slot.staging.p) {
        auto staging = desc;
        staging.Format = staging_format;
        staging.Usage = D3D11_USAGE_STAGING;
        staging.BindFlags = 0;
        staging.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        staging.MiscFlags = 0;
        state.failure = device->CreateTexture2D(&staging, nullptr, &slot.staging.p);
        if (FAILED(state.failure))
            return ReadbackResult::failed;
    }
    for (std::size_t i = 0; i < calibration_count; ++i) {
        if (!slot.calibration[i].p || slot.sizes[i] != copy_bytes[i]) {
            slot.calibration[i].reset();
            D3D11_BUFFER_DESC buffer{};
            buffer.ByteWidth = copy_bytes[i];
            buffer.Usage = D3D11_USAGE_STAGING;
            buffer.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            state.failure = device->CreateBuffer(&buffer, nullptr, &slot.calibration[i].p);
            if (FAILED(state.failure))
                return ReadbackResult::failed;
            slot.sizes[i] = copy_bytes[i];
        }
    }
    context->CopyResource(slot.staging.p, source);
    for (std::size_t i = 0; i < calibration_count; ++i) {
        D3D11_BOX range{calibration[i].offset, 0, 0, calibration[i].offset + copy_bytes[i], 1, 1};
        context->CopySubresourceRegion(slot.calibration[i].p, 0, 0, 0, 0, calibration[i].buffer, 0,
                                       &range);
    }
    state.failure = device->GetDeviceRemovedReason();
    if (FAILED(state.failure))
        return ReadbackResult::failed;
    slot.frame = frame;
    slot.calibration_count = calibration_count;
    ++state.count;
    return ReadbackResult::ready;
}

ReadbackResult Readback::poll(ID3D11DeviceContext* context, RawFrame& output) noexcept {
    auto& state = *impl;
    if (FAILED(state.failure))
        return ReadbackResult::failed;
    if (!context || context->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE) {
        state.reason = "invalid readback context";
        return ReadbackResult::invalid;
    }
    if (!state.count) {
        state.reason = "no pending depth frame";
        return ReadbackResult::empty;
    }
    Com<ID3D11Device> device;
    context->GetDevice(&device.p);
    if (device.p != state.device.p) {
        state.reason = "readback device changed";
        return ReadbackResult::invalid;
    }
    auto& slot = state.slots[state.head];
    state.calibration_candidates = slot.calibration_count;
    state.valid_calibrations = 0;
    D3D11_MAPPED_SUBRESOURCE mapped{};
    const auto result =
        context->Map(slot.staging.p, 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
    if (result == DXGI_ERROR_WAS_STILL_DRAWING)
        return ReadbackResult::pending;
    if (FAILED(result)) {
        state.failure = result;
        state.reason = "depth texture map failed";
        return ReadbackResult::failed;
    }
    bool copied = false;
    bool calibration_pending = false;
    try {
        auto metadata = slot.frame;
        bool calibrated = !slot.calibration_count;
        bool ambiguous = false;
        for (std::size_t i = 0; i < slot.calibration_count; ++i) {
            D3D11_MAPPED_SUBRESOURCE buffer{};
            const auto mapped_buffer = context->Map(slot.calibration[i].p, 0, D3D11_MAP_READ,
                                                    D3D11_MAP_FLAG_DO_NOT_WAIT, &buffer);
            if (mapped_buffer == DXGI_ERROR_WAS_STILL_DRAWING) {
                calibration_pending = true;
                break;
            }
            if (FAILED(mapped_buffer)) {
                state.failure = mapped_buffer;
                state.reason = "camera calibration map failed";
                break;
            }
            Projection candidate;
            const bool valid = read_per_view_projection(buffer.pData, slot.sizes[i], state.width,
                                                        state.height, candidate);
            context->Unmap(slot.calibration[i].p, 0);
            if (valid) {
                ++state.valid_calibrations;
                if (calibrated) {
                    // Duplicated per-view bindings are fine. Conflicting
                    // calibrations must never be resolved by binding order.
                    const auto& prior = metadata.projection;
                    ambiguous |= prior.inverse_z != candidate.inverse_z ||
                                 prior.viewport_min != candidate.viewport_min ||
                                 prior.viewport_max != candidate.viewport_max ||
                                 prior.view_valid != candidate.view_valid;
                    if (prior.view_valid && candidate.view_valid)
                        ambiguous |= prior.view_matrix != candidate.view_matrix ||
                                     prior.projection_matrix != candidate.projection_matrix ||
                                     prior.position != candidate.position ||
                                     prior.up != candidate.up || prior.forward != candidate.forward;
                } else {
                    metadata.projection = candidate;
                    calibrated = true;
                }
            }
        }
        const auto row_bytes = std::size_t(state.width) * 4;
        if (!calibration_pending && SUCCEEDED(state.failure) && (!calibrated || ambiguous)) {
            state.failure = HRESULT_FROM_WIN32(ERROR_INVALID_DATA);
            state.reason =
                ambiguous ? "conflicting camera calibrations" : "no valid camera calibration";
        }
        if (!calibration_pending && SUCCEEDED(state.failure) && calibrated && !ambiguous &&
            mapped.pData && mapped.RowPitch >= row_bytes) {
            RawFrame frame;
            frame.frame = metadata;
            frame.format = state.format;
            frame.pixels.resize(row_bytes * state.height);
            for (std::uint32_t y = 0; y < state.height; ++y)
                std::memcpy(frame.pixels.data() + y * row_bytes,
                            static_cast<const unsigned char*>(mapped.pData) +
                                std::size_t(y) * mapped.RowPitch,
                            row_bytes);
            output = std::move(frame);
            copied = true;
        }
    } catch (...) {
        state.failure = E_OUTOFMEMORY;
        state.reason = "depth readback allocation failed";
    }
    context->Unmap(slot.staging.p, 0);
    if (calibration_pending)
        return ReadbackResult::pending;
    if (!copied) {
        if (SUCCEEDED(state.failure)) {
            state.failure = E_FAIL;
            state.reason = "invalid depth row stride";
        }
        return ReadbackResult::failed;
    }
    state.reason = "ready";
    state.head = (state.head + 1) % kSlots;
    --state.count;
    return ReadbackResult::ready;
}
}
