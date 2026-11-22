#include "dolly_map_edit.hpp"

#include <Windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <MinHook.h>

#include <cstring>
#include <mutex>
#include <string>
#include <vector>

namespace dolly::map_edit {

bool looks_like_bounds(std::uint32_t byte_width, std::uint32_t stride, std::uint32_t usage,
                       std::uint32_t bind, std::uint32_t misc, const void* data) noexcept {
    constexpr std::uint32_t wanted_bind = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS;
    if (!data || stride != kRecordBytes || usage != D3D11_USAGE_DEFAULT ||
        (bind & wanted_bind) != wanted_bind || !(misc & D3D11_RESOURCE_MISC_BUFFER_STRUCTURED) ||
        byte_width % kRecordBytes || byte_width < 64 * kRecordBytes)
        return false;
    const auto* bytes = static_cast<const unsigned char*>(data);
    const std::uint32_t records = byte_width / kRecordBytes;
    std::uint32_t previous = 0;
    for (std::uint32_t i = 0; i < records; ++i) {
        float box[8];
        std::uint32_t words[8];
        std::memcpy(box, bytes + std::size_t(i) * kRecordBytes, sizeof(box));
        std::memcpy(words, box, sizeof(words));
        for (int k : {0, 1, 2}) {
            const float lo = box[k], hi = box[k + 4];
            if (!(lo <= hi) || lo < -1.0e6f || hi > 1.0e6f)
                return false;
        }
        if (!words[3] || words[7] < previous)
            return false;
        previous = words[7];
    }
    return true;
}

std::uint32_t hide_records(unsigned char* table, std::uint32_t records,
                           const std::uint32_t* ids, std::uint32_t count) noexcept {
    // A box far outside the world: every view's culling rejects it.
    constexpr float away = 1.0e8f;
    std::uint32_t hidden = 0;
    for (std::uint32_t i = 0; i < count; ++i) {
        if (ids[i] >= records)
            continue;
        float* box = reinterpret_cast<float*>(table + std::size_t(ids[i]) * kRecordBytes);
        for (int k : {0, 1, 2, 4, 5, 6})
            box[k] = away;
        ++hidden;
    }
    return hidden;
}

namespace {
using CreateBufferFn = HRESULT(STDMETHODCALLTYPE*)(ID3D11Device*, const D3D11_BUFFER_DESC*,
                                                   const D3D11_SUBRESOURCE_DATA*, ID3D11Buffer**);
CreateBufferFn original_create = nullptr;

// The newest bounds table the game created, and its original contents.
std::mutex table_lock;
ID3D11Buffer* table_buffer = nullptr;
std::vector<unsigned char> table_original;
std::uint64_t table_serial = 0;
std::uint64_t table_hash = 0;

// "<session>.mapedit", opened by the bridge worker.
std::mutex map_lock;
HANDLE mapping{};
unsigned char* memory{};
std::uint64_t mapping_bytes{};
ULONGLONG next_open{};

// Render thread: what the game currently shows.
std::uint64_t shown_seq = ~0ull;
std::uint64_t shown_serial = 0;
bool shown_edits = false;

std::uint64_t fnv(const unsigned char* data, std::size_t size) noexcept {
    std::uint64_t h = 1469598103934665603ull;
    for (std::size_t i = 0; i < size; ++i)
        h = (h ^ data[i]) * 1099511628211ull;
    return h;
}

Header* header() noexcept { return reinterpret_cast<Header*>(memory); }

void close_mapping() noexcept {
    if (memory)
        UnmapViewOfFile(memory);
    if (mapping)
        CloseHandle(mapping);
    memory = nullptr;
    mapping = nullptr;
    mapping_bytes = 0;
}

HRESULT STDMETHODCALLTYPE create_buffer(ID3D11Device* device, const D3D11_BUFFER_DESC* desc,
                                        const D3D11_SUBRESOURCE_DATA* init, ID3D11Buffer** out) {
    const HRESULT hr = original_create(device, desc, init, out);
    if (FAILED(hr) || !desc || !init || !out || !*out)
        return hr;
    try {
        if (!looks_like_bounds(desc->ByteWidth, desc->StructureByteStride, desc->Usage,
                               desc->BindFlags, desc->MiscFlags, init->pSysMem))
            return hr;
        const auto* bytes = static_cast<const unsigned char*>(init->pSysMem);
        std::vector<unsigned char> copy(bytes, bytes + desc->ByteWidth);
        const auto hash = fnv(copy.data(), copy.size());
        (*out)->AddRef();
        std::lock_guard<std::mutex> lock(table_lock);
        if (table_buffer)
            table_buffer->Release();
        table_buffer = *out;
        table_original.swap(copy);
        table_hash = hash;
        ++table_serial;
    } catch (...) {
    }
    return hr;
}
} // namespace

bool install(ID3D11Device* device) noexcept {
    if (!device || original_create)
        return false;
    const auto init = MH_Initialize();
    if (init != MH_OK && init != MH_ERROR_ALREADY_INITIALIZED)
        return false;
    auto** table = *reinterpret_cast<void***>(device);
    if (MH_CreateHook(table[3], reinterpret_cast<void*>(&create_buffer),
                      reinterpret_cast<void**>(&original_create)) != MH_OK)
        return false;
    if (MH_EnableHook(table[3]) != MH_OK) {
        MH_RemoveHook(table[3]);
        original_create = nullptr;
        return false;
    }
    return true;
}

void worker_tick(const wchar_t* session_name, bool connected) noexcept {
    try {
        std::lock_guard<std::mutex> lock(map_lock);
        const bool named = session_name && *session_name && wcsnlen(session_name, 256) != 256;
        if (!named || !connected) {
            close_mapping();
            return;
        }
        if (!memory) {
            const auto now = GetTickCount64();
            if (now < next_open)
                return;
            next_open = now + 500;
            const std::wstring name = std::wstring(session_name) + L".mapedit";
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
            if (mapping_bytes < kRecordsOffset ||
                std::memcmp(head->magic, kMagic, sizeof(kMagic)) != 0 ||
                head->version != kVersion || head->id_capacity > kIdCapacity) {
                close_mapping();
                return;
            }
            header()->records_capacity =
                std::uint32_t((mapping_bytes - kRecordsOffset) / kRecordBytes);
        }
        // Publish the game's table once per new table.
        std::lock_guard<std::mutex> table(table_lock);
        auto* head = header();
        if (!table_serial || head->records_serial == table_serial)
            return;
        const std::uint64_t records = table_original.size() / kRecordBytes;
        head->record_count = 0;
        if (records <= head->records_capacity) {
            std::memcpy(memory + kRecordsOffset, table_original.data(), table_original.size());
            head->records_hash = table_hash;
            head->record_count = std::uint32_t(records);
        }
        MemoryBarrier();
        head->records_serial = table_serial;
    } catch (...) {
        close_mapping();
    }
}

void present(IDXGISwapChain* chain) noexcept {
    if (!chain || !original_create)
        return;
    try {
        std::vector<std::uint32_t> ids;
        std::uint64_t seq = 0;
        bool have_list = false;
        bool controller = false;
        std::uint64_t list_hash = 0;
        {
            std::lock_guard<std::mutex> lock(map_lock);
            controller = memory != nullptr;
            if (memory) {
                auto* head = header();
                seq = head->hide_seq;
                MemoryBarrier();
                list_hash = head->hide_hash;
                if (!(seq & 1)) {
                    const std::uint32_t count = head->hide_count;
                    if (count <= head->id_capacity) {
                        const auto* source =
                            reinterpret_cast<const std::uint32_t*>(memory + kIdsOffset);
                        ids.assign(source, source + count);
                        MemoryBarrier();
                        have_list = head->hide_seq == seq;
                    }
                }
            }
        }
        std::uint32_t hidden = 0;
        {
        std::lock_guard<std::mutex> lock(table_lock);
        if (!table_buffer)
            return;
        if (have_list) {
            if (seq == shown_seq && table_serial == shown_serial)
                return;
        } else {
            // No controller (or it is mid-write): restore the stock map once
            // when the controller went away; otherwise keep what is shown.
            if (controller || !shown_edits || table_serial != shown_serial)
                return;
            ids.clear();
            seq = ~0ull;
        }
        if (have_list && list_hash && list_hash != table_hash)
            ids.clear(); // made for another map build: show the stock map
        std::vector<unsigned char> data = table_original;
        const auto records = std::uint32_t(data.size() / kRecordBytes);
        hidden = hide_records(data.data(), records, ids.data(), std::uint32_t(ids.size()));
        ID3D11Device* device = nullptr;
        if (FAILED(chain->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&device))) ||
            !device)
            return;
        ID3D11DeviceContext* context = nullptr;
        device->GetImmediateContext(&context);
        device->Release();
        if (!context)
            return;
        context->UpdateSubresource(table_buffer, 0, nullptr, data.data(), 0, 0);
        context->Release();
        shown_seq = seq;
        shown_serial = table_serial;
        shown_edits = hidden != 0;
        }
        std::lock_guard<std::mutex> map(map_lock);
        if (memory && have_list) {
            header()->applied_hidden = hidden;
            MemoryBarrier();
            header()->applied_seq = seq;
        }
    } catch (...) {
    }
}

} // namespace dolly::map_edit
