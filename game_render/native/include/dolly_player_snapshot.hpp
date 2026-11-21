#pragma once
#include <d3d11.h>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace dolly::player_capture {
class GpuSnapshot {
    ID3D11DeviceContext* context = nullptr;
    ID3D11Buffer *instance = nullptr, *identity = nullptr, *sourceInstance = nullptr,
                 *sourceIdentity = nullptr;
    ID3D11Query* query = nullptr;
    UINT expectedId = 0;
    UINT instanceCapacity = 0, identityCapacity = 0;
    bool started = false, retained = false;
    static constexpr std::size_t PoolBudget = 64u * 1024 * 1024;
    inline static std::atomic<std::size_t> pooled{0};
    void drop_sources() noexcept {
        if (sourceInstance)
            sourceInstance->Release();
        if (sourceIdentity)
            sourceIdentity->Release();
        if (context)
            context->Release();
        sourceInstance = sourceIdentity = nullptr;
        context = nullptr;
        started = false;
    }
    void drop_buffers() noexcept {
        if (retained) {
            pooled.fetch_sub(static_cast<std::size_t>(instanceCapacity) + identityCapacity,
                             std::memory_order_relaxed);
            retained = false;
        }
        if (query)
            query->Release();
        if (instance)
            instance->Release();
        if (identity)
            identity->Release();
        query = nullptr;
        instance = identity = nullptr;
        instanceCapacity = identityCapacity = 0;
    }

public:
    unsigned id = 0, record[8]{};
    unsigned reuseHits = 0;
    HRESULT failure = S_OK;
    static std::size_t pooled_bytes() noexcept { return pooled.load(std::memory_order_relaxed); }
    ~GpuSnapshot() { release(); }
    void release() noexcept {
        drop_sources();
        drop_buffers();
    }
    // Drop an in-flight copy without reading it; keep pooled staging buffers.
    void abandon() noexcept {
        drop_sources();
        if (!retained)
            drop_buffers();
    }
    bool begin(ID3D11DeviceContext* c, ID3D11Buffer* records, ID3D11Buffer* ids, UINT byteOffset,
               UINT expectedInstance) noexcept {
        if (started || !c || !records || !ids || c->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE)
            return false;
        D3D11_BUFFER_DESC a{}, b{};
        records->GetDesc(&a);
        ids->GetDesc(&b);
        if (a.StructureByteStride != 32 || a.ByteWidth % 32 || a.ByteWidth > 16 * 1024 * 1024 ||
            b.ByteWidth > 16 * 1024 * 1024 || byteOffset % 4 || byteOffset > b.ByteWidth ||
            b.ByteWidth - byteOffset < 4 || std::uint64_t(expectedInstance) * 32 + 32 > a.ByteWidth)
            return false;
        ID3D11Device *device = nullptr, *da = nullptr, *db = nullptr;
        c->GetDevice(&device);
        records->GetDevice(&da);
        ids->GetDevice(&db);
        bool owned = device == da && device == db;
        da->Release();
        db->Release();
        if (!owned) {
            device->Release();
            return false;
        }
        ID3D11Device* stagingDevice = nullptr;
        if (instance)
            instance->GetDevice(&stagingDevice);
        const bool sameDevice = stagingDevice == device;
        if (stagingDevice)
            stagingDevice->Release();
        // Fixed-size staging can be reused only on its creating device.
        if (sameDevice && instance && instanceCapacity == 32 && identity && identityCapacity == 4)
            ++reuseHits;
        else {
            drop_buffers();
            D3D11_BUFFER_DESC stage{};
            stage.ByteWidth = 32;
            stage.Usage = D3D11_USAGE_STAGING;
            stage.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            failure = device->CreateBuffer(&stage, nullptr, &instance);
            if (SUCCEEDED(failure)) {
                instanceCapacity = 32;
                stage.ByteWidth = 4;
                failure = device->CreateBuffer(&stage, nullptr, &identity);
                if (SUCCEEDED(failure))
                    identityCapacity = 4;
            }
            D3D11_QUERY_DESC q{D3D11_QUERY_EVENT, 0};
            if (SUCCEEDED(failure))
                failure = device->CreateQuery(&q, &query);
            if (FAILED(failure)) {
                device->Release();
                drop_buffers();
                return false;
            }
            const auto bytes = static_cast<std::size_t>(instanceCapacity) + identityCapacity;
            retained = pooled.load(std::memory_order_relaxed) + bytes <= PoolBudget;
            if (retained)
                pooled.fetch_add(bytes, std::memory_order_relaxed);
        }
        device->Release();
        context = c;
        c->AddRef();
        sourceInstance = records;
        records->AddRef();
        sourceIdentity = ids;
        ids->AddRef();
        expectedId = expectedInstance;
        started = true;
        // Snapshot only the identity and proposed record. poll rejects any
        // different GPU identity before accepting the copied record.
        const D3D11_BOX idBox{byteOffset, 0, 0, byteOffset + 4, 1, 1};
        const UINT recordOffset = expectedInstance * 32;
        const D3D11_BOX recordBox{recordOffset, 0, 0, recordOffset + 32, 1, 1};
        c->CopySubresourceRegion(identity, 0, 0, 0, 0, ids, 0, &idBox);
        c->CopySubresourceRegion(instance, 0, 0, 0, 0, records, 0, &recordBox);
        c->End(query);
        return true;
    }
    // 0 pending, 1 completed, -1 rejected/error. No waiting or Flush.
    int poll(ID3D11DeviceContext* c) noexcept {
        if (!context || c != context)
            return -1;
        BOOL ready = FALSE;
        failure = c->GetData(query, &ready, sizeof(ready), D3D11_ASYNC_GETDATA_DONOTFLUSH);
        if (failure == S_FALSE || (SUCCEEDED(failure) && !ready))
            return 0;
        if (FAILED(failure)) {
            release();
            return -1;
        }
        D3D11_MAPPED_SUBRESOURCE mapped{};
        failure = c->Map(identity, 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (failure == DXGI_ERROR_WAS_STILL_DRAWING)
            return 0;
        if (FAILED(failure)) {
            release();
            return -1;
        }
        std::memcpy(&id, mapped.pData, 4);
        c->Unmap(identity, 0);
        if (id != expectedId) {
            failure = E_BOUNDS;
            release();
            return -1;
        }
        failure = c->Map(instance, 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (failure == DXGI_ERROR_WAS_STILL_DRAWING)
            return 0;
        if (FAILED(failure)) {
            release();
            return -1;
        }
        std::memcpy(record, mapped.pData, 32);
        c->Unmap(instance, 0);
        drop_sources();
        if (!retained)
            drop_buffers();
        return 1;
    }
};
}
