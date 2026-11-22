#pragma once
// Hide static map pieces in the running game (RenderLock map editing).
//
// The world renderer keeps one GPU buffer with the world bounding box of every
// aggregate fragment (static props and merged map geometry); its GPU culling
// reads it for every view. Moving one record's box out of the world stops the
// game drawing that fragment in all passes. Baked lighting is unchanged.
//
// The controller (Blender) creates "<session>.mapedit", laid out as below:
//   0   char magic[8] = "DLYMED01"
//   8   u32 version (1), u32 id_capacity
//   16  u64 hide_seq      controller: odd while it writes the list
//   24  u32 hide_count, u32 reserved
//   32  u64 applied_seq   native: hide_seq last applied to the game
//   40  u32 record_count  native: records in the published table (0 = none yet)
//   44  u32 applied_hidden native: records hidden by the last apply
//   48  u64 records_serial native: bumps when the game creates a new table
//   56  u64 records_hash  native: FNV-1a 64 of the original table
//   64  u32 records_capacity
//   4096                  hide ids (u32 record indices), id_capacity entries
//   kRecordsOffset        the original records, 32 bytes each:
//                         float min[3], u32 aggregate, float max[3], u32 first
#include <cstdint>

struct IDXGISwapChain;
struct ID3D11Device;

namespace dolly::map_edit {

constexpr char kMagic[8] = {'D', 'L', 'Y', 'M', 'E', 'D', '0', '1'};
constexpr std::uint32_t kVersion = 1;
constexpr std::uint64_t kIdsOffset = 4096;
constexpr std::uint32_t kIdCapacity = 65536;
constexpr std::uint64_t kRecordsOffset = kIdsOffset + 4ull * kIdCapacity;
constexpr std::uint32_t kRecordBytes = 32;

#pragma pack(push, 1)
struct Header {
    char magic[8];
    std::uint32_t version;
    std::uint32_t id_capacity;
    std::uint64_t hide_seq;
    std::uint32_t hide_count;
    std::uint32_t reserved;
    std::uint64_t applied_seq;
    std::uint32_t record_count;
    std::uint32_t applied_hidden;
    std::uint64_t records_serial;
    std::uint64_t records_hash;
    std::uint32_t records_capacity;
};
#pragma pack(pop)

// The fragment bounds table has a distinctive shape: 32-byte structured
// records, GPU-writable, created with data, boxes ordered (min <= max) and the
// last word non-decreasing. Exposed for the native tests.
bool looks_like_bounds(std::uint32_t byte_width, std::uint32_t stride, std::uint32_t usage,
                       std::uint32_t bind, std::uint32_t misc, const void* data) noexcept;
// Overwrite the listed records' boxes so culling drops them; returns how many.
std::uint32_t hide_records(unsigned char* table, std::uint32_t records,
                           const std::uint32_t* ids, std::uint32_t count) noexcept;

// Startup: watch buffer creation on this device's driver (vtable shared).
bool install(ID3D11Device* device) noexcept;
// Bridge worker: open/close "<session>.mapedit" and publish the table.
void worker_tick(const wchar_t* session_name, bool connected) noexcept;
// Render thread, before each Present: apply a changed hide list.
void present(IDXGISwapChain* chain) noexcept;

} // namespace dolly::map_edit
