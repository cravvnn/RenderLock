#include "dolly_map_edit.hpp"

#include <Windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <MinHook.h>

#include <cmath>
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

std::uint64_t find_class_table(const unsigned char* code, std::uint64_t code_rva,
                               std::uint64_t code_size) noexcept {
    // ... B3 00 00 00 | 48 8D 0D rel32 | F6 04 08 08
    static constexpr unsigned char head[] = {0xb3, 0x00, 0x00, 0x00, 0x48, 0x8d, 0x0d};
    static constexpr unsigned char tail[] = {0xf6, 0x04, 0x08, 0x08};
    if (!code || code_size < 15)
        return 0;
    std::uint64_t found = 0;
    unsigned matches = 0;
    for (std::uint64_t i = 0; i + 15 <= code_size; ++i) {
        if (std::memcmp(code + i, head, sizeof(head)) != 0 ||
            std::memcmp(code + i + 11, tail, sizeof(tail)) != 0)
            continue;
        std::int32_t rel = 0;
        std::memcpy(&rel, code + i + 7, 4);
        const std::uint64_t target = code_rva + i + 11 + std::int64_t(rel);
        if (matches && target != found)
            return 0; // disagreeing matches: not the layout this was written for
        found = target;
        ++matches;
    }
    return matches >= 2 ? found : 0;
}

bool origin_matches(const float transform[12], const float origin[3]) noexcept {
    for (int k = 0; k < 3; ++k) {
        const float value = transform[4 * k + 3];
        if (!(std::fabs(value - origin[k]) <= 0.5f))
            return false;
    }
    return true;
}

namespace {
using CreateBufferFn = HRESULT(STDMETHODCALLTYPE*)(ID3D11Device*, const D3D11_BUFFER_DESC*,
                                                   const D3D11_SUBRESOURCE_DATA*, ID3D11Buffer**);
CreateBufferFn original_create = nullptr;

// The newest bounds table the game created, and its original contents.
struct Table {
    ID3D11Buffer* buffer = nullptr;
    std::vector<unsigned char> original;
};
std::mutex table_lock;
Table table;
std::vector<Table> retired; // replaced tables still to be restored and released
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
struct HiddenObject {
    std::uint64_t address;
    float origin[3];
    std::uint8_t original_class;
};
std::vector<HiddenObject> hidden_objects;
std::uint64_t objects_seq = ~0ull;
std::uint32_t objects_class = ~0u;
unsigned char* class_table = nullptr;
bool class_table_searched = false;

// Fingerprint of a table: FNV-1a over every record's box and last word. The
// per-aggregate word (bytes 12-15) differs between game launches, so it is left
// out; boxes and order are fixed for a map build.
std::uint64_t fingerprint(const unsigned char* data, std::size_t size) noexcept {
    std::uint64_t h = 1469598103934665603ull;
    for (std::size_t i = 0; i < size; ++i)
        if (i % kRecordBytes < 12 || i % kRecordBytes >= 16)
            h = (h ^ data[i]) * 1099511628211ull;
    return h;
}

volatile Header* header() noexcept { return reinterpret_cast<volatile Header*>(memory); }

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
        Table made{*out, std::vector<unsigned char>(bytes, bytes + desc->ByteWidth)};
        const auto hash = fingerprint(made.original.data(), made.original.size());
        made.buffer->AddRef();
        std::lock_guard<std::mutex> lock(table_lock);
        if (table.buffer)
            retired.push_back(std::move(table)); // restored on the render thread
        table = std::move(made);
        table_hash = hash;
        ++table_serial;
    } catch (...) {
    }
    return hr;
}

// Readable and writable committed memory for [address, address + size).
bool writable(std::uint64_t address, std::size_t size) noexcept {
    MEMORY_BASIC_INFORMATION info{};
    if (!address || !VirtualQuery(reinterpret_cast<void*>(address), &info, sizeof(info)))
        return false;
    const auto end = reinterpret_cast<std::uint64_t>(info.BaseAddress) + info.RegionSize;
    constexpr DWORD rw = PAGE_READWRITE | PAGE_EXECUTE_READWRITE;
    return info.State == MEM_COMMIT && (info.Protect & rw) && !(info.Protect & PAGE_GUARD) &&
           address + size <= end;
}

unsigned char* locate_class_table() noexcept {
    if (class_table_searched)
        return class_table;
    class_table_searched = true;
    const auto module = reinterpret_cast<const unsigned char*>(GetModuleHandleW(L"scenesystem.dll"));
    if (!module)
        return nullptr;
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(module);
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(module + dos->e_lfanew);
    const auto* section = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++section) {
        if (std::memcmp(section->Name, ".text", 5) != 0)
            continue;
        const auto rva = find_class_table(module + section->VirtualAddress, section->VirtualAddress,
                                          section->Misc.VirtualSize);
        if (rva && rva + kClassSlots <= nt->OptionalHeader.SizeOfImage &&
            writable(reinterpret_cast<std::uint64_t>(module) + rva, kClassSlots))
            class_table = const_cast<unsigned char*>(module + rva);
        break;
    }
    return class_table;
}

// Object memory can be freed by the engine at any time: every access to an
// object goes through these guarded helpers so a stale address cannot fault.
bool guarded_read(std::uint64_t address, void* out, std::size_t size) noexcept {
    __try {
        std::memcpy(out, reinterpret_cast<const void*>(address), size);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}
bool guarded_write(std::uint64_t address, std::uint8_t value) noexcept {
    __try {
        *reinterpret_cast<volatile std::uint8_t*>(address) = value;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}
// The object's transform and class byte, when the object still has `origin`.
bool read_object(std::uint64_t address, const float origin[3], std::uint8_t& cls) noexcept {
    float transform[12];
    return writable(address + kObjectTransform, kObjectClass + 1 - kObjectTransform) &&
           guarded_read(address + kObjectTransform, transform, sizeof(transform)) &&
           origin_matches(transform, origin) && guarded_read(address + kObjectClass, &cls, 1);
}

// Show every hidden object again (when it is still the object that was hidden).
void restore_objects() noexcept {
    for (const auto& object : hidden_objects) {
        std::uint8_t cls = 0;
        if (read_object(object.address, object.origin, cls) && cls == objects_class)
            guarded_write(object.address + kObjectClass, object.original_class);
    }
    hidden_objects.clear();
}

void present_objects(bool controller, const std::vector<ObjectEntry>& entries, bool have_list,
                     std::uint64_t seq, std::uint32_t spare) noexcept {
    auto* flags = locate_class_table();
    if (!flags)
        return;
    if (!controller) {
        restore_objects();
        objects_seq = ~0ull;
        return;
    }
    if (!have_list || (seq == objects_seq && spare == objects_class))
        return;
    restore_objects();
    objects_seq = seq;
    objects_class = spare;
    if (spare >= kClassSlots || !(flags[spare] & 8))
        return; // the controller has not reserved a hidden class
    for (const auto& entry : entries) {
        // An origin at the world origin identifies nothing (world geometry and
        // freed memory both read as zeros there).
        if (std::fabs(entry.origin[0]) < 1.0f && std::fabs(entry.origin[1]) < 1.0f &&
            std::fabs(entry.origin[2]) < 1.0f)
            continue;
        std::uint8_t original = 0;
        if (!read_object(entry.address, entry.origin, original))
            continue;
        if (original == spare || original >= kClassSlots || (flags[original] & 8))
            continue;
        if (guarded_write(entry.address + kObjectClass, std::uint8_t(spare)))
            hidden_objects.push_back(
                {entry.address, {entry.origin[0], entry.origin[1], entry.origin[2]}, original});
    }
}

void upload(IDXGISwapChain* chain, ID3D11Buffer* buffer, const void* data) noexcept {
    ID3D11Device* device = nullptr;
    if (FAILED(chain->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&device))) ||
        !device)
        return;
    ID3D11Device* owner = nullptr;
    buffer->GetDevice(&owner);
    if (owner == device) {
        ID3D11DeviceContext* context = nullptr;
        device->GetImmediateContext(&context);
        if (context) {
            context->UpdateSubresource(buffer, 0, nullptr, data, 0, 0);
            context->Release();
        }
    }
    if (owner)
        owner->Release();
    device->Release();
}
} // namespace

bool install(ID3D11Device* device) noexcept {
    if (!device || original_create)
        return false;
    const auto init = MH_Initialize();
    if (init != MH_OK && init != MH_ERROR_ALREADY_INITIALIZED)
        return false;
    auto** vtable = *reinterpret_cast<void***>(device);
    if (MH_CreateHook(vtable[3], reinterpret_cast<void*>(&create_buffer),
                      reinterpret_cast<void**>(&original_create)) != MH_OK)
        return false;
    if (MH_EnableHook(vtable[3]) != MH_OK) {
        MH_RemoveHook(vtable[3]);
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
            if (mapping_bytes < kRecordsOffset ||
                std::memcmp(const_cast<const char*>(header()->magic), kMagic, sizeof(kMagic)) != 0 ||
                header()->version != kVersion) {
                close_mapping();
                return;
            }
            header()->records_capacity =
                std::uint32_t((mapping_bytes - kRecordsOffset) / kRecordBytes);
        }
        // Publish the game's table once per new table.
        std::lock_guard<std::mutex> tables(table_lock);
        auto* head = header();
        if (!table_serial || head->records_serial == table_serial)
            return;
        const std::uint64_t records = table.original.size() / kRecordBytes;
        head->record_count = 0;
        if (records <= (mapping_bytes - kRecordsOffset) / kRecordBytes) {
            std::memcpy(memory + kRecordsOffset, table.original.data(), table.original.size());
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
        std::vector<ObjectEntry> objects;
        std::uint64_t seq = 0, list_hash = 0, object_seq = 0;
        std::uint32_t spare = ~0u;
        bool have_list = false, have_objects = false, controller = false;
        {
            std::lock_guard<std::mutex> lock(map_lock);
            controller = memory != nullptr;
            if (memory) {
                auto* head = header();
                if (class_table)
                    std::memcpy(const_cast<std::uint8_t*>(head->class_flags), class_table,
                                kClassSlots);
                head->class_table = class_table ? 1 : 0;
                seq = head->hide_seq;
                MemoryBarrier();
                // Fragment list: copy only when it changed (or the table did).
                if (!(seq & 1) && (seq != shown_seq || table_serial != shown_serial)) {
                    list_hash = head->hide_hash;
                    const std::uint32_t count = head->hide_count;
                    if (count <= kIdCapacity) {
                        const auto* source =
                            reinterpret_cast<const std::uint32_t*>(memory + kIdsOffset);
                        ids.assign(source, source + count);
                        MemoryBarrier();
                        have_list = head->hide_seq == seq;
                    }
                } else if (!(seq & 1)) {
                    have_list = true; // unchanged
                }
                object_seq = head->object_seq;
                MemoryBarrier();
                spare = head->spare_class;
                if (!(object_seq & 1)) {
                    const std::uint32_t count = head->object_count;
                    if (count <= kObjectCapacity) {
                        const auto* source =
                            reinterpret_cast<const ObjectEntry*>(memory + kObjectsOffset);
                        objects.assign(source, source + count);
                        MemoryBarrier();
                        have_objects = head->object_seq == object_seq;
                    }
                }
            }
        }
        present_objects(controller, objects, have_objects, object_seq, spare);
        if (have_objects && class_table) {
            std::lock_guard<std::mutex> map(map_lock);
            if (memory) {
                header()->objects_hidden = std::uint32_t(hidden_objects.size());
                MemoryBarrier();
                header()->object_applied_seq = object_seq;
            }
        }

        std::uint32_t hidden = 0;
        bool applied = false;
        {
            std::lock_guard<std::mutex> lock(table_lock);
            for (auto& old : retired) {
                if (shown_edits)
                    upload(chain, old.buffer, old.original.data());
                old.buffer->Release();
            }
            retired.clear();
            if (!table.buffer)
                return;
            const bool changed = table_serial != shown_serial;
            if (have_list) {
                if (seq == shown_seq && !changed)
                    return;
            } else {
                // No controller (or it is mid-write): restore the stock map
                // once the controller went away; otherwise keep what is shown.
                if (controller || (!shown_edits && !changed))
                    return;
                ids.clear();
                seq = ~0ull;
            }
            if (list_hash != table_hash)
                ids.clear(); // made for another table (or no table): stock map
            std::vector<unsigned char> data = table.original;
            const auto records = std::uint32_t(data.size() / kRecordBytes);
            hidden = hide_records(data.data(), records, ids.data(), std::uint32_t(ids.size()));
            if (hidden || shown_edits)
                upload(chain, table.buffer, data.data());
            shown_seq = seq;
            shown_serial = table_serial;
            shown_edits = hidden != 0;
            applied = have_list;
        }
        std::lock_guard<std::mutex> map(map_lock);
        if (memory && applied) {
            header()->applied_hidden = hidden;
            MemoryBarrier();
            header()->applied_seq = seq;
        }
    } catch (...) {
    }
}

} // namespace dolly::map_edit
