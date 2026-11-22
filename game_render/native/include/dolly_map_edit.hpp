#pragma once
// Hide static map pieces in the running game (RenderLock map editing).
//
// Two kinds of static pieces:
// - Aggregate fragments (static props and merged map geometry, most of a map).
//   The world renderer keeps one GPU buffer with the world bounding box of every
//   fragment; its GPU culling reads it for every view. Moving a record's box out
//   of the world stops the game drawing that fragment in all passes.
// - Standalone scene objects (lamps, signs, glass, NPC props...). The scene
//   system skips, in every view, objects whose class has flag 8 in its class
//   flag table. The controller reserves an empty class (sc_setclassflags
//   <class> 8) and the native side moves chosen objects into it by writing the
//   object's class byte, restoring the original byte to show it again.
// Baked lighting is unchanged either way.
//
// The controller (Blender) creates "<session>.mapedit", laid out as below:
//   0   char magic[8] = "DLYMED01"
//   8   u32 version (2), u32 id_capacity
//   16  u64 hide_seq      controller: odd while it writes the fragment list
//   24  u32 hide_count, u32 reserved
//   32  u64 applied_seq   native: hide_seq last applied to the game
//   40  u32 record_count  native: records in the published table (0 = none yet)
//   44  u32 applied_hidden native: records hidden by the last apply
//   48  u64 records_serial native: bumps when the game creates a new table
//   56  u64 records_hash  native: FNV-1a 64 of the original boxes and last
//                         words (the aggregate word changes between launches)
//   64  u32 records_capacity, u32 reserved
//   72  u64 hide_hash     controller: records_hash the list was made for; a
//                         list for another table is not applied
//   80  u64 object_seq    controller: odd while it writes the object list
//   88  u32 object_count  controller: entries at kObjectsOffset
//   92  u32 spare_class   controller: class id used to hide (>= 64 = none)
//   96  u64 object_applied_seq native: object_seq last applied
//   104 u32 objects_hidden native: objects moved into the spare class
//   108 u32 class_table   native: 1 when the class flag table was found
//   112 u8  class_flags[64] native: live copy of the class flag table
//   1024                  object entries (kObjectCapacity): u64 address,
//                         float origin[3], u32 reserved
//   4096                  hide ids (u32 record indices), id_capacity entries
//   kRecordsOffset        the original records, 32 bytes each:
//                         float min[3], u32 aggregate, float max[3], u32 first
#include <cstdint>

struct IDXGISwapChain;
struct ID3D11Device;

namespace dolly::map_edit {

constexpr char kMagic[8] = {'D', 'L', 'Y', 'M', 'E', 'D', '0', '1'};
constexpr std::uint32_t kVersion = 2;
constexpr std::uint64_t kObjectsOffset = 1024;
constexpr std::uint32_t kObjectCapacity = 128;
constexpr std::uint64_t kIdsOffset = 4096;
constexpr std::uint32_t kIdCapacity = 65536;
constexpr std::uint64_t kRecordsOffset = kIdsOffset + 4ull * kIdCapacity;
constexpr std::uint32_t kRecordBytes = 32;
constexpr std::uint32_t kClassSlots = 64;

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
    std::uint32_t reserved2;
    std::uint64_t hide_hash;
    std::uint64_t object_seq;
    std::uint32_t object_count;
    std::uint32_t spare_class;
    std::uint64_t object_applied_seq;
    std::uint32_t objects_hidden;
    std::uint32_t class_table;
    std::uint8_t class_flags[kClassSlots];
};
struct ObjectEntry {
    std::uint64_t address;
    float origin[3];
    std::uint32_t reserved;
};
#pragma pack(pop)
static_assert(sizeof(Header) == 176, "mapedit header layout");
static_assert(sizeof(ObjectEntry) == 24, "mapedit object entry layout");
static_assert(kObjectsOffset + kObjectCapacity * sizeof(ObjectEntry) <= kIdsOffset,
              "object entries overlap the hide ids");

// Scene object fields used here (scenesystem, build 6864 layout).
constexpr std::uint32_t kObjectTransform = 0x30; // 3x4 row-major, origin at +0x3c/+0x4c/+0x5c
constexpr std::uint32_t kObjectClass = 0xb3;

// The fragment bounds table has a distinctive shape: 32-byte structured
// records, GPU-writable, created with data, boxes ordered (min <= max) and the
// last word non-decreasing. Exposed for the native tests.
bool looks_like_bounds(std::uint32_t byte_width, std::uint32_t stride, std::uint32_t usage,
                       std::uint32_t bind, std::uint32_t misc, const void* data) noexcept;
// Overwrite the listed records' boxes so culling drops them; returns how many.
std::uint32_t hide_records(unsigned char* table, std::uint32_t records,
                           const std::uint32_t* ids, std::uint32_t count) noexcept;
// Find the class flag table in scenesystem's code: every visibility test reads
// it as `movzx eax, byte [obj+0xb3]; lea rcx, [rip+table]; test byte [rax+rcx], 8`.
// Returns the table's offset from `base` when all matches agree, else 0.
std::uint64_t find_class_table(const unsigned char* code, std::uint64_t code_rva,
                               std::uint64_t code_size) noexcept;
// True when the object's origin matches the expected one (within half a unit).
bool origin_matches(const float transform[12], const float origin[3]) noexcept;

// Startup: watch buffer creation on this device's driver (vtable shared).
bool install(ID3D11Device* device) noexcept;
// Bridge worker: open/close "<session>.mapedit" and publish the table.
void worker_tick(const wchar_t* session_name, bool connected) noexcept;
// Render thread, before each Present: apply changed hide lists.
void present(IDXGISwapChain* chain) noexcept;

} // namespace dolly::map_edit
