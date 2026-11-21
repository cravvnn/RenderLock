#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "dolly_sound_compat_generated.hpp"
#include "MinHook.h"
#include "dolly_sound_capture.hpp"

namespace dolly {
namespace {
// Addresses are published only after exact-profile or unique AOB resolution.
dolly_sound_compat::Resolution sound_symbols{};
constexpr unsigned kCapacity = 8192;
constexpr unsigned kMaxVoices = 4096;
constexpr std::int32_t kNoVoice = -1;
using VoiceStartFn = std::int64_t(__fastcall*)(std::uint64_t, void*, const char*);
using VoiceStopFn = void(__fastcall*)(std::uint64_t, void*);
using VoiceMapRemoveFn = std::uint64_t(__fastcall*)(void*, const std::int32_t*, std::uint32_t);
using VmixStartFn = std::uint64_t(__fastcall*)(void*, void*, char);
using EventNameFn = const char*(__fastcall*)(void*);
VoiceStartFn original_voice_start = nullptr;
VoiceStopFn original_voice_stop = nullptr;
VoiceMapRemoveFn original_voice_map_remove = nullptr;
VmixStartFn original_vmix_start = nullptr;
EventNameFn event_name = nullptr;
std::uintptr_t sound_base = 0;
std::atomic<bool> installed{false};
std::atomic<bool> recording{false};
std::atomic<unsigned> inflight{0};
// Interface voices are declined while this QPC deadline is in the future. Dolly
// arms it immediately before its own replay pause/resume console commands.
std::atomic<std::uint64_t> ui_silence_until_qpc{0};
constexpr unsigned kUiSilenceMaxMs = 2000;
std::atomic<std::uint64_t> dropped{0};
std::atomic<std::int32_t> last_tick{-1};
std::atomic<double> last_engine_time{0};
std::atomic<int> error_code{0};
std::atomic<std::int32_t> active_ids[kMaxVoices]{};
SRWLOCK queue_lock = SRWLOCK_INIT;
HANDLE output = INVALID_HANDLE_VALUE;
HANDLE state_output = INVALID_HANDLE_VALUE;
std::int64_t next_state_qpc = 0;
unsigned head = 0, tail = 0;
LARGE_INTEGER frequency{};
thread_local char current_event[128]{};

// True while the armed window covers this voice. Called on the audio thread
// before any voice is allocated.
bool sound_ui_voice_silenced(const char* asset) noexcept {
    (void)asset;  // The window is the rule; the asset is kept for diagnostics.
    const auto until = ui_silence_until_qpc.load(std::memory_order_acquire);
    if (!until)
        return false;
    LARGE_INTEGER now{};
    QueryPerformanceCounter(&now);
    if (ui_voice_silenced(std::uint64_t(now.QuadPart), until))
        return true;
    // Expire once, so a finished transition leaves no armed state behind.
    std::uint64_t expected = until;
    ui_silence_until_qpc.compare_exchange_strong(expected, 0, std::memory_order_acq_rel,
                                                 std::memory_order_relaxed);
    return false;
}

struct VoiceRecord {
    char kind;
    std::int64_t qpc;
    double engine_time;
    std::int32_t tick;
    std::int32_t slot;
    std::int32_t voice_id;
    float source_volume;
    float source_rate_parameter;
    float source_x;
    float source_y;
    float source_z;
    char event[128];
    char asset[384];
};
VoiceRecord queue[kCapacity]{};

bool read_bytes(std::uintptr_t source, void* destination, std::size_t bytes) noexcept {
    SIZE_T got = 0;
    return source >= 0x10000 && ReadProcessMemory(GetCurrentProcess(),
            reinterpret_cast<const void*>(source), destination, bytes, &got) && got == bytes;
}
template <class T> bool read(std::uintptr_t source, T& destination) noexcept {
    return read_bytes(source, &destination, sizeof(destination));
}
template <std::size_t N> void copy_text(char (&destination)[N], const char* source) noexcept {
    destination[0] = 0;
    if (!source) return;
    std::size_t index = 0;
    for (; index + 1 < N; ++index) {
        const char value = source[index];
        if (!value) break;
        destination[index] = value;
    }
    destination[index] = 0;
}
void enqueue(const VoiceRecord& record) noexcept {
    AcquireSRWLockExclusive(&queue_lock);
    const unsigned next = (head + 1) % kCapacity;
    if (next == tail) dropped.fetch_add(1, std::memory_order_relaxed);
    else { queue[head] = record; head = next; }
    ReleaseSRWLockExclusive(&queue_lock);
}
std::string quoted(const char* value) {
    std::string result = "\"";
    for (const char* p = value; *p; ++p) {
        if (*p == '"') result += '"';
        if (*p == '\r' || *p == '\n') result += ' ';
        else result += *p;
    }
    result += '"';
    return result;
}
bool write_text(const std::string& text) noexcept {
    DWORD written = 0;
    return output != INVALID_HANDLE_VALUE &&
           WriteFile(output, text.data(), DWORD(text.size()), &written, nullptr) &&
           written == text.size();
}
bool write_state_text(const std::string& text) noexcept {
    DWORD written = 0;
    return state_output != INVALID_HANDLE_VALUE &&
           WriteFile(state_output, text.data(), DWORD(text.size()), &written, nullptr) &&
           written == text.size();
}
float as_float(const unsigned char* bytes, unsigned offset) noexcept {
    float value = 0;
    std::memcpy(&value, bytes + offset, sizeof(value));
    return value;
}
void sample_voice_state() noexcept {
    if (!recording.load(std::memory_order_acquire) ||
        state_output == INVALID_HANDLE_VALUE) return;
    LARGE_INTEGER stamp{};
    QueryPerformanceCounter(&stamp);
    if (stamp.QuadPart < next_state_qpc) return;
    next_state_qpc = stamp.QuadPart + frequency.QuadPart / 10;
    std::uintptr_t table = 0, slots = 0, meters = 0;
    std::int32_t count = 0;
    if (!read(sound_base + sound_symbols.voice_table, table) || !read(table, count) ||
        count <= 0 || count > kMaxVoices || !read(table + 8, slots) ||
        !read(table + 0x20, meters) || !slots || !meters) return;
    std::string batch;
    batch.reserve(16384);
    for (std::int32_t slot = 0; slot < count; ++slot) {
        unsigned char slot_data[0x40]{};
        if (!read_bytes(slots + std::uintptr_t(slot) * 0x40,
                        slot_data, sizeof(slot_data))) continue;
        std::uintptr_t voice = 0;
        std::int32_t voice_id = kNoVoice;
        std::memcpy(&voice, slot_data, sizeof(voice));
        std::memcpy(&voice_id, slot_data + 8, sizeof(voice_id));
        if (!voice || voice_id == kNoVoice) continue;
        unsigned char state[0x84]{};
        if (!read_bytes(meters + std::uintptr_t(slot) * 0x84,
                        state, sizeof(state))) continue;
        char line[256]{};
        const int length = std::snprintf(line, sizeof(line),
            "%.9f,%lld,%d,%d,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g\r\n",
            double(stamp.QuadPart) / double(frequency.QuadPart),
            static_cast<long long>(stamp.QuadPart), slot, voice_id,
            as_float(slot_data, 0x20), as_float(state, 0x14),
            as_float(state, 0x60), as_float(state, 0x64),
            as_float(state, 0), as_float(state, 4), as_float(state, 8));
        if (length > 0 && length < int(sizeof(line))) batch.append(line, length);
    }
    if (!batch.empty() && !write_state_text(batch)) {
        error_code.store(4);
        recording.store(false, std::memory_order_release);
    }
}
void drain() noexcept {
    VoiceRecord batch[16];
    for (;;) {
        unsigned count = 0;
        AcquireSRWLockExclusive(&queue_lock);
        while (tail != head && count < 16) {
            batch[count++] = queue[tail];
            tail = (tail + 1) % kCapacity;
        }
        ReleaseSRWLockExclusive(&queue_lock);
        if (!count) break;
        for (unsigned index = 0; index < count; ++index) {
            const auto& item = batch[index];
            char numbers[160]{};
            std::snprintf(numbers, sizeof(numbers), "%s,%lld,%d,%.9f,%d,%d,%.9g,%.9g,%.9g,%.9g,%.9g,",
                          item.kind == 'S' ? "start" : "stop",
                          static_cast<long long>(item.qpc), item.tick, item.engine_time,
                          item.slot, item.voice_id, item.source_volume,
                          item.source_rate_parameter, item.source_x, item.source_y,
                          item.source_z);
            if (!write_text(std::string(numbers) + quoted(item.event) + "," +
                            quoted(item.asset) + "\r\n")) {
                error_code.store(4);
                recording.store(false);
                return;
            }
        }
    }
}
std::int64_t __fastcall voice_start_hook(std::uint64_t first, void* parameters,
                                         const char* asset) noexcept {
    // Declining before the original call makes this a mute rather than a filter:
    // the voice is never allocated, so nothing is mixed, rendered or captured.
    // A negative slot is the same "no voice" result the engine returns when it
    // cannot allocate one, which every caller already handles.
    if (sound_ui_voice_silenced(asset))
        return kNoVoice;
    const auto slot = original_voice_start(first, parameters, asset);
    if (!recording.load(std::memory_order_relaxed) || slot < 0 || slot > 65535)
        return slot;
    inflight.fetch_add(1, std::memory_order_acq_rel);
    if (recording.load(std::memory_order_relaxed)) {
        VoiceRecord entry{};
        LARGE_INTEGER stamp{};
        QueryPerformanceCounter(&stamp);
        entry.qpc = stamp.QuadPart;
        entry.kind = 'S';
        entry.tick = last_tick.load(std::memory_order_relaxed);
        entry.engine_time = last_engine_time.load(std::memory_order_relaxed);
        entry.slot = std::int32_t(slot);
        entry.voice_id = -1;
        const auto parameter_address = reinterpret_cast<std::uintptr_t>(parameters);
        // Initial gain and legacy ramp-time metadata are profile-specific floats.
        // The nearby pitch percentage is an integer, not this rate column.
        read(parameter_address + sound_symbols.parameter_volume_offset, entry.source_volume);
        read(parameter_address + sound_symbols.parameter_rate_offset, entry.source_rate_parameter);
        read(parameter_address, entry.source_x);
        read(parameter_address + 4, entry.source_y);
        read(parameter_address + 8, entry.source_z);
        copy_text(entry.event, current_event);
        copy_text(entry.asset, asset);
        std::uintptr_t table = 0, slots = 0;
        std::int32_t count = 0;
        if (read(sound_base + sound_symbols.voice_table, table) && read(table, count) &&
            count > 0 && slot < count && read(table + 8, slots))
            read(slots + std::uintptr_t(slot) * 0x40 + 8, entry.voice_id);
        if (entry.asset[0] && entry.voice_id != kNoVoice && entry.slot < kMaxVoices)
            active_ids[entry.slot].store(entry.voice_id, std::memory_order_release);
        if (entry.asset[0]) enqueue(entry);
    }
    inflight.fetch_sub(1, std::memory_order_acq_rel);
    return slot;
}
void __fastcall voice_stop_hook(std::uint64_t first, void* voice) noexcept {
    if (recording.load(std::memory_order_relaxed) && voice) {
        inflight.fetch_add(1, std::memory_order_acq_rel);
        if (recording.load(std::memory_order_relaxed)) {
            std::uintptr_t table = 0, slots = 0;
            std::int32_t count = 0;
            if (read(sound_base + sound_symbols.voice_table, table) && read(table, count) &&
                count > 0 && count <= 4096 && read(table + 8, slots)) {
                for (std::int32_t slot = 0; slot < count; ++slot) {
                    const auto address = slots + std::uintptr_t(slot) * 0x40;
                    std::uintptr_t candidate = 0;
                    if (!read(address, candidate) || candidate != reinterpret_cast<std::uintptr_t>(voice))
                        continue;
                    VoiceRecord entry{};
                    LARGE_INTEGER stamp{};
                    QueryPerformanceCounter(&stamp);
                    entry.kind = 'E';
                    entry.qpc = stamp.QuadPart;
                    entry.tick = last_tick.load(std::memory_order_relaxed);
                    entry.engine_time = last_engine_time.load(std::memory_order_relaxed);
                    entry.slot = slot;
                    entry.voice_id = -1;
                    read(address + 8, entry.voice_id);
                    auto expected = entry.voice_id;
                    if (entry.voice_id != kNoVoice &&
                        active_ids[slot].compare_exchange_strong(expected, kNoVoice,
                            std::memory_order_acq_rel))
                        enqueue(entry);
                }
            }
        }
        inflight.fetch_sub(1, std::memory_order_acq_rel);
    }
    original_voice_stop(first, voice);
}
std::uint64_t __fastcall voice_map_remove_hook(void* map, const std::int32_t* id,
                                               std::uint32_t hash) noexcept {
    if (recording.load(std::memory_order_relaxed) && id) {
        std::uintptr_t table = 0;
        std::int32_t voice_id = kNoVoice;
        if (read(sound_base + sound_symbols.voice_table, table) &&
            map == reinterpret_cast<void*>(table + sound_symbols.voice_map_offset) &&
            read(reinterpret_cast<std::uintptr_t>(id), voice_id)) {
            inflight.fetch_add(1, std::memory_order_acq_rel);
            for (std::int32_t slot = 0; slot < kMaxVoices; ++slot) {
                auto expected = voice_id;
                if (!active_ids[slot].compare_exchange_strong(expected, kNoVoice,
                        std::memory_order_acq_rel)) continue;
                VoiceRecord entry{};
                LARGE_INTEGER stamp{};
                QueryPerformanceCounter(&stamp);
                entry.kind = 'E';
                entry.qpc = stamp.QuadPart;
                entry.tick = last_tick.load(std::memory_order_relaxed);
                entry.engine_time = last_engine_time.load(std::memory_order_relaxed);
                entry.slot = slot;
                entry.voice_id = voice_id;
                enqueue(entry);
                break;
            }
            inflight.fetch_sub(1, std::memory_order_acq_rel);
        }
    }
    return original_voice_map_remove(map, id, hash);
}
std::uint64_t __fastcall vmix_start_hook(void* configuration, void* context,
                                          char mode) noexcept {
    if (!recording.load(std::memory_order_relaxed))
        return original_vmix_start(configuration, context, mode);
    char previous[sizeof(current_event)]{};
    std::memcpy(previous, current_event, sizeof(previous));
    copy_text(current_event, event_name(context));
    const auto result = original_vmix_start(configuration, context, mode);
    std::memcpy(current_event, previous, sizeof(previous));
    return result;
}
void poll_released_voices() noexcept {
    if (!recording.load(std::memory_order_acquire)) return;
    std::uintptr_t table = 0, slots = 0;
    std::int32_t count = 0;
    if (!read(sound_base + sound_symbols.voice_table, table) || !read(table, count) ||
        count <= 0 || count > kMaxVoices || !read(table + 8, slots)) return;
    for (std::int32_t slot = 0; slot < count; ++slot) {
        auto id = active_ids[slot].load(std::memory_order_acquire);
        if (id == kNoVoice) continue;
        const auto address = slots + std::uintptr_t(slot) * 0x40;
        std::uintptr_t voice = 0;
        std::int32_t current_id = kNoVoice;
        if (!read(address, voice) || !read(address + 8, current_id) ||
            (voice && current_id == id)) continue;
        if (!active_ids[slot].compare_exchange_strong(id, kNoVoice,
                std::memory_order_acq_rel)) continue;
        VoiceRecord entry{};
        LARGE_INTEGER stamp{};
        QueryPerformanceCounter(&stamp);
        entry.kind = 'E';
        entry.qpc = stamp.QuadPart;
        entry.tick = last_tick.load(std::memory_order_relaxed);
        entry.engine_time = last_engine_time.load(std::memory_order_relaxed);
        entry.slot = slot;
        entry.voice_id = id;
        enqueue(entry);
    }
}
} // namespace

// Read a bounded PE section snapshot: scans never dereference arbitrary image
// memory and allocation/read failure simply leaves this optional feature off.
bool resolve_sound_image(void* module, bool exact, dolly_sound_compat::Resolution& symbols) {
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    IMAGE_DOS_HEADER dos{};
    IMAGE_NT_HEADERS64 nt{};
    if (!read(base, dos) || dos.e_magic != IMAGE_DOS_SIGNATURE ||
        dos.e_lfanew < sizeof(dos) || dos.e_lfanew > 0x100000 ||
        !read(base + dos.e_lfanew, nt) || nt.Signature != IMAGE_NT_SIGNATURE ||
        nt.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
        nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
        nt.FileHeader.SizeOfOptionalHeader != sizeof(IMAGE_OPTIONAL_HEADER64) ||
        !nt.FileHeader.NumberOfSections || nt.FileHeader.NumberOfSections > 96 ||
        nt.OptionalHeader.SizeOfImage > 0x10000000) return false;
    const auto image_size = nt.OptionalHeader.SizeOfImage;
    const auto headers_end = std::uint64_t(dos.e_lfanew) + sizeof(nt) +
                            nt.FileHeader.NumberOfSections * sizeof(IMAGE_SECTION_HEADER);
    if (headers_end > image_size) return false;
    IMAGE_SECTION_HEADER text{}, data{};
    for (unsigned i = 0; i < nt.FileHeader.NumberOfSections; ++i) {
        IMAGE_SECTION_HEADER section{};
        if (!read(base + dos.e_lfanew + sizeof(nt) + i * sizeof(section), section) ||
            section.VirtualAddress > image_size ||
            section.Misc.VirtualSize > image_size - section.VirtualAddress) return false;
        if (std::memcmp(section.Name, ".text\0\0\0", 8) == 0) {
            if (text.Misc.VirtualSize || !(section.Characteristics & IMAGE_SCN_MEM_EXECUTE) ||
                !(section.Characteristics & IMAGE_SCN_MEM_READ)) return false;
            text = section;
        }
        if (std::memcmp(section.Name, ".data\0\0\0", 8) == 0) {
            if (data.Misc.VirtualSize || !(section.Characteristics & IMAGE_SCN_MEM_WRITE) ||
                !(section.Characteristics & IMAGE_SCN_MEM_READ) ||
                (section.Characteristics & IMAGE_SCN_MEM_EXECUTE)) return false;
            data = section;
        }
    }
    if (!text.Misc.VirtualSize || data.Misc.VirtualSize < 8) return false;
    std::vector<unsigned char> bytes(text.Misc.VirtualSize);
    if (!read_bytes(base + text.VirtualAddress, bytes.data(), bytes.size())) return false;
    using namespace dolly_sound_compat;
    return resolve_profiles(bytes.data(), bytes.size(), text.VirtualAddress, data.VirtualAddress,
                            data.Misc.VirtualSize, image_size, kProfiles,
                            sizeof(kProfiles) / sizeof(kProfiles[0]), exact, symbols);
}

bool sound_capture_install(void* module, bool exact) noexcept {
    if (installed.load()) return true;
    if (!module) { error_code.store(10); return false; }
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    dolly_sound_compat::Resolution symbols{};
    try {
        if (!resolve_sound_image(module, exact, symbols)) { error_code.store(11); return false; }
    } catch (...) { error_code.store(11); return false; }
    sound_symbols = symbols;
    sound_base = base;
    event_name = reinterpret_cast<EventNameFn>(base + symbols.functions[4]);
    auto voice = reinterpret_cast<void*>(base + symbols.functions[0]);
    auto stop_entry = reinterpret_cast<void*>(base + symbols.functions[1]);
    auto remove_entry = reinterpret_cast<void*>(base + symbols.functions[2]);
    auto vmix_entry = reinterpret_cast<void*>(base + symbols.functions[3]);
    if (MH_CreateHook(voice, reinterpret_cast<void*>(voice_start_hook),
                      reinterpret_cast<void**>(&original_voice_start)) != MH_OK) {
        error_code.store(12);
        return false;
    }
    if (MH_CreateHook(vmix_entry, reinterpret_cast<void*>(vmix_start_hook),
                      reinterpret_cast<void**>(&original_vmix_start)) != MH_OK) {
        error_code.store(13);
        MH_RemoveHook(voice);
        return false;
    }
    if (MH_CreateHook(stop_entry, reinterpret_cast<void*>(voice_stop_hook),
                      reinterpret_cast<void**>(&original_voice_stop)) != MH_OK) {
        error_code.store(14);
        MH_RemoveHook(vmix_entry);
        MH_RemoveHook(voice);
        return false;
    }
    if (MH_CreateHook(remove_entry, reinterpret_cast<void*>(voice_map_remove_hook),
                      reinterpret_cast<void**>(&original_voice_map_remove)) != MH_OK) {
        error_code.store(15);
        MH_RemoveHook(stop_entry);
        MH_RemoveHook(vmix_entry);
        MH_RemoveHook(voice);
        return false;
    }
    if (MH_EnableHook(voice) != MH_OK) {
        error_code.store(16);
        MH_RemoveHook(remove_entry);
        MH_RemoveHook(stop_entry);
        MH_RemoveHook(vmix_entry);
        MH_RemoveHook(voice);
        return false;
    }
    if (MH_EnableHook(vmix_entry) != MH_OK) {
        error_code.store(17);
        MH_DisableHook(voice);
        MH_RemoveHook(remove_entry);
        MH_RemoveHook(stop_entry);
        MH_RemoveHook(vmix_entry);
        MH_RemoveHook(voice);
        return false;
    }
    if (MH_EnableHook(stop_entry) != MH_OK) {
        error_code.store(18);
        MH_DisableHook(vmix_entry);
        MH_DisableHook(voice);
        MH_RemoveHook(remove_entry);
        MH_RemoveHook(stop_entry);
        MH_RemoveHook(vmix_entry);
        MH_RemoveHook(voice);
        return false;
    }
    if (MH_EnableHook(remove_entry) != MH_OK) {
        error_code.store(19);
        MH_DisableHook(stop_entry);
        MH_DisableHook(vmix_entry);
        MH_DisableHook(voice);
        MH_RemoveHook(remove_entry);
        MH_RemoveHook(stop_entry);
        MH_RemoveHook(vmix_entry);
        MH_RemoveHook(voice);
        return false;
    }
    QueryPerformanceFrequency(&frequency);
    error_code.store(0);
    installed.store(true);
    return true;
}
bool sound_capture_available() noexcept { return installed.load(); }

// The Deadlock 6795 engine2 movie recorder owns two clocks:
//   * the engine frame time, read through this virtual accessor;
//   * the WAV sample clock, advanced once per rendered frame by the movie
//     recorder at 1 / movie_fps seconds.
// Dolly's fixed-step export sets host_framerate to video_fps / speed. If the
// accessor returns host_framerate while the WAV clock keeps using movie_fps,
// the WAV lasts exactly as long as the slow-motion video and carries the same
// authored replay window. No TGA frames are produced because the recorded
// command asks for the `wav` format only.
namespace {
constexpr std::uintptr_t kMovieGetFramerateRva = 0xfc7c0;
constexpr std::uintptr_t kMovieFramerateOffset = 0xf4;

using ConVarFloatFn = float(__fastcall*)(void*);
ConVarFloatFn convar_float = nullptr;
void* host_framerate_cvar = nullptr;
std::atomic<bool> movie_installed{false};
std::atomic<std::uintptr_t> movie_reader_base{0};

// The recorder's field-clock getter (fcf00) returns output_frame / movie_fps.
// Secondary slot0 is the adjusting destructor thunk, used only as an identity
// check. Read the frame clock rather than the writer's512-sample block edges.
bool read_movie_audio(std::uintptr_t base, MovieAudioSample& sample) noexcept {
    __try {
        const bool season2 = reinterpret_cast<std::uintptr_t>(host_framerate_cvar) ==
                             base + movie_audio_layout(true).host_cvar;
        const bool build6861 = reinterpret_cast<std::uintptr_t>(host_framerate_cvar) ==
                               base + movie_audio_layout(false, true).host_cvar;
        const bool build6864 = reinterpret_cast<std::uintptr_t>(host_framerate_cvar) ==
                               base + movie_audio_layout(false, false, true).host_cvar;
        const auto p = reinterpret_cast<const char*>(base + movie_audio_layout(season2, build6861, build6864).recorder);
        if ((*reinterpret_cast<const unsigned short*>(p + 0xec) & 2) == 0 ||
            *reinterpret_cast<const unsigned*>(p + 0x108) <= 1)
            return false;
        const auto name = *reinterpret_cast<const char* const*>(p + 0xd0);
        if (!name)
            return false;
        std::uint64_t take = 0;
        for (unsigned i = 0; i < 512 && name[i]; ++i) {
            if (std::strncmp(name + i, "dolly_audio_", 12) != 0)
                continue;
            for (unsigned j = 0; j < 10; ++j) {
                const char c = name[i + 12 + j];
                const unsigned digit = c >= '0' && c <= '9'   ? c - '0'
                                       : c >= 'a' && c <= 'f' ? c - 'a' + 10
                                                              : 16;
                if (digit == 16)
                    return false;
                take = take * 16 + digit;
            }
            break;
        }
        const auto frame = *reinterpret_cast<const std::uint64_t*>(p + 0xd8);
        const auto fps = *reinterpret_cast<const float*>(p + 0xf4);
        if (!std::isfinite(fps) || fps < 1 || fps > 10000 || fps != std::floor(fps))
            return false;
        return movie_audio_frame(take, frame, static_cast<unsigned>(fps), sample);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

float movie_framerate(void* self) noexcept {
    if (self) {
        const auto value =
            *reinterpret_cast<const float*>(static_cast<const char*>(self) + kMovieFramerateOffset);
        if (std::isfinite(value) && value > 0.0f && value <= 10000.0f)
            return value;
    }
    return 0.0f;
}

float __fastcall movie_get_framerate_hook(void* self) noexcept {
    if (convar_float && host_framerate_cvar) {
        const float host = convar_float(host_framerate_cvar);
        if (std::isfinite(host) && host > 0.0f && host <= 10000.0f)
            return host;
    }
    return movie_framerate(self);
}
} // namespace

bool movie_audio_install(void* engine_module, void* sound_module, bool exact_pcm_pair, bool season2, bool build6861,
                         bool build6864) noexcept {
    // A second call only adds the sample map: soundsystem.dll can load after
    // the accessor was redirected, and the map needs the reviewed pair.
    const bool installed_before = movie_installed.load(std::memory_order_acquire);
    const bool add_reader = exact_pcm_pair && sound_module &&
                            !movie_reader_base.load(std::memory_order_acquire);
    if (installed_before && !add_reader)
        return true;
    if (!engine_module)
        return false;
    const auto base = reinterpret_cast<std::uintptr_t>(engine_module);
    const auto layout = movie_audio_layout(season2, build6861, build6864);
    const auto slot = reinterpret_cast<void**>(base + layout.table + 0x38);
    const auto target = reinterpret_cast<void*>(base + kMovieGetFramerateRva);
    // Exact reviewed accessor: movss xmm0, [rcx+0xf4]; ret.
    constexpr unsigned char expected[] = {0xF3, 0x0F, 0x10, 0x81, 0xF4, 0x00, 0x00, 0x00, 0xC3};
    if (!installed_before) {
        if (*slot != target ||
            std::memcmp(reinterpret_cast<const void*>(base + kMovieGetFramerateRva), expected,
                        sizeof(expected))) {
            return false;
        }
        convar_float = reinterpret_cast<ConVarFloatFn>(base + layout.float_accessor);
        host_framerate_cvar = reinterpret_cast<void*>(base + layout.host_cvar);
    }
    if (add_reader) {
        // Read-only clock support is restricted to the reviewed engine/sound
        // pair. The native video frame samples this same stream clock.
        const auto time_slot = reinterpret_cast<void**>(base + layout.audio_table);
        const auto frame_slot = reinterpret_cast<void**>(base + layout.table + 6 * 8);
        constexpr unsigned char frame_getter[] = {0x48, 0x8b, 0x81, 0xd8, 0, 0, 0, 0xc3};
        if (*time_slot != reinterpret_cast<void*>(base + 0xfe5bc) ||
            *frame_slot != reinterpret_cast<void*>(base + 0xfc7b0) ||
            std::memcmp(reinterpret_cast<void*>(base + 0xfc7b0), frame_getter,
                        sizeof(frame_getter)))
            return installed_before;
        movie_reader_base.store(base, std::memory_order_release);
    }
    if (!installed_before) {
        DWORD old = 0;
        if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old))
            return false;
        *slot = reinterpret_cast<void*>(&movie_get_framerate_hook);
        DWORD ignored = 0;
        VirtualProtect(slot, sizeof(void*), old, &ignored);
        movie_installed.store(true, std::memory_order_release);
    }
    // Publish a process-scoped capability marker. The desktop can then refuse a
    // fixed-step audio take before it records the wrong speed.
    wchar_t temp[MAX_PATH]{};
    const DWORD length = GetTempPathW(MAX_PATH, temp);
    if (length) {
        const std::wstring marker = std::wstring(temp, length) + L"dolly_movie_audio_" +
                                    std::to_wstring(GetCurrentProcessId()) + L".ready";
        HANDLE file = CreateFileW(marker.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                                  CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            if (movie_reader_base.load(std::memory_order_acquire)) {
                constexpr char capability[] = "pcm-sample-map-v1";
                DWORD written = 0;
                WriteFile(file, capability, sizeof(capability) - 1, &written, nullptr);
            }
            CloseHandle(file);
        }
    }
    return true;
}

bool movie_audio_available() noexcept {
    return movie_installed.load(std::memory_order_acquire);
}

bool movie_audio_sample(MovieAudioSample& sample) noexcept {
    sample = {};
    const auto base = movie_reader_base.load(std::memory_order_acquire);
    return base && read_movie_audio(base, sample);
}

void sound_capture_clock(std::int32_t tick, double engine_seconds) noexcept {
    last_tick.store(tick, std::memory_order_relaxed);
    last_engine_time.store(engine_seconds, std::memory_order_relaxed);
}
bool sound_capture_open_outputs(const wchar_t* csv_path, void*& csv,
                                void*& state_csv) noexcept {
    csv = state_csv = nullptr;
    if (!csv_path || !*csv_path)
        return false;
    try {
        const std::wstring state_path = std::wstring(csv_path) + L".state.csv";
        HANDLE events = CreateFileW(csv_path, GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                                    CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (events == INVALID_HANDLE_VALUE)
            return false;
        HANDLE states = CreateFileW(state_path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                                    CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (states == INVALID_HANDLE_VALUE) {
            CloseHandle(events);
            DeleteFileW(csv_path); // Created by this call (CREATE_NEW).
            return false;
        }
        csv = events;
        state_csv = states;
        return true;
    } catch (...) {
        return false;
    }
}
bool sound_capture_start(const wchar_t* csv_path) noexcept {
    if (!installed.load()) {
        if (error_code.load() == 0) error_code.store(1);
        return false;
    }
    if (recording.load() || output != INVALID_HANDLE_VALUE ||
        state_output != INVALID_HANDLE_VALUE) {
        error_code.store(2);
        return false;
    }
    if (!csv_path || !*csv_path) { error_code.store(3); return false; }
    void* opened = nullptr;
    void* opened_state = nullptr;
    if (!sound_capture_open_outputs(csv_path, opened, opened_state)) {
        error_code.store(3);
        return false;
    }
    output = static_cast<HANDLE>(opened);
    state_output = static_cast<HANDLE>(opened_state);
    error_code.store(0);
    dropped.store(0);
    for (auto& id : active_ids) id.store(kNoVoice, std::memory_order_relaxed);
    AcquireSRWLockExclusive(&queue_lock);
    head = tail = 0;
    ReleaseSRWLockExclusive(&queue_lock);
    LARGE_INTEGER started{};
    QueryPerformanceCounter(&started);
    next_state_qpc = 0;
    char metadata[512]{};
    std::snprintf(metadata, sizeof(metadata),
                  "# qpc_frequency=%lld; capture_start_qpc=%lld; capture_start_tick=%d; capture_start_engine_seconds=%.9f; capture=source_voice_starts_and_stops; sound_voice_table_rva=%u; sound_voice_map_offset=%u\r\n",
                  static_cast<long long>(frequency.QuadPart),
                  static_cast<long long>(started.QuadPart), last_tick.load(),
                  last_engine_time.load(), sound_symbols.voice_table, sound_symbols.voice_map_offset);
    if (!write_text(std::string(metadata) +
                    "kind,qpc_ticks,demo_tick,engine_seconds,voice_slot,voice_id,source_volume,source_rate_parameter,source_x,source_y,source_z,soundevent,vsnd_path\r\n") ||
        !write_state_text("perf_counter,qpc_ticks,voice_slot,voice_id,slot_20,raw_14,state_b_0,state_b_1,raw_00,raw_04,raw_08\r\n")) {
        error_code.store(4);
        CloseHandle(state_output);
        state_output = INVALID_HANDLE_VALUE;
        CloseHandle(output);
        output = INVALID_HANDLE_VALUE;
        // Both files were created by this start; a retry must be able to reuse the path.
        try {
            DeleteFileW((std::wstring(csv_path) + L".state.csv").c_str());
        } catch (...) {
        }
        DeleteFileW(csv_path);
        return false;
    }
    recording.store(true, std::memory_order_release);
    return true;
}
void sound_capture_stop() noexcept {
    recording.store(false, std::memory_order_release);
    for (unsigned retry = 0; retry < 1000 && inflight.load(); ++retry) Sleep(1);
    if (output != INVALID_HANDLE_VALUE) {
        drain();
        const auto count = dropped.load();
        LARGE_INTEGER finished{};
        QueryPerformanceCounter(&finished);
        char footer[160]{};
        std::snprintf(footer, sizeof(footer), "# dropped=%llu; write_error=%d; capture_end_qpc=%lld\r\n",
                      static_cast<unsigned long long>(count),
                      error_code.load(),
                      static_cast<long long>(finished.QuadPart));
        write_text(footer);
        FlushFileBuffers(output);
        CloseHandle(output);
        output = INVALID_HANDLE_VALUE;
    }
    if (state_output != INVALID_HANDLE_VALUE) {
        FlushFileBuffers(state_output);
        CloseHandle(state_output);
        state_output = INVALID_HANDLE_VALUE;
    }
}
void sound_capture_worker_tick() noexcept {
    if (output != INVALID_HANDLE_VALUE) {
        poll_released_voices();
        sample_voice_state();
        drain();
    }
}
std::uint64_t sound_capture_dropped() noexcept { return dropped.load(); }
bool sound_silence_ui_voices(unsigned milliseconds) noexcept {
    if (!installed.load(std::memory_order_relaxed) || frequency.QuadPart <= 0)
        return false;
    const auto clamped = milliseconds < kUiSilenceMaxMs ? milliseconds : kUiSilenceMaxMs;
    if (!clamped)
        return false;
    LARGE_INTEGER now{};
    QueryPerformanceCounter(&now);
    const auto until = std::uint64_t(now.QuadPart) +
                       std::uint64_t(frequency.QuadPart) * clamped / 1000;
    // Repeated arming extends the window instead of shortening it, so a burst of
    // pause/resume commands keeps one continuous window open.
    auto current = ui_silence_until_qpc.load(std::memory_order_relaxed);
    while (until > current &&
           !ui_silence_until_qpc.compare_exchange_weak(current, until,
                                                       std::memory_order_acq_rel,
                                                       std::memory_order_relaxed)) {
    }
    return true;
}
bool sound_ui_silence_armed() noexcept {
    const auto until = ui_silence_until_qpc.load(std::memory_order_acquire);
    if (!until)
        return false;
    LARGE_INTEGER now{};
    QueryPerformanceCounter(&now);
    return std::uint64_t(now.QuadPart) < until;
}
const char* sound_capture_error() noexcept {
    switch (error_code.load()) {
    case 1: return "Verified audio hook is unavailable.";
    case 2: return "A source-audio capture is already active.";
    case 3: return "Could not create a new source-audio CSV at that path.";
    case 4: return "The source-audio CSV could not be written.";
    case 10: return "The soundsystem module is not loaded yet.";
    case 11: return "The soundsystem voice profile did not match this build.";
    case 12: return "Could not hook the soundsystem voice start.";
    case 13: return "Could not hook the soundsystem vmix start.";
    case 14: return "Could not hook the soundsystem voice stop.";
    case 15: return "Could not hook the soundsystem voice map removal.";
    case 16: return "Could not enable the soundsystem voice start hook.";
    case 17: return "Could not enable the soundsystem vmix start hook.";
    case 18: return "Could not enable the soundsystem voice stop hook.";
    case 19: return "Could not enable the soundsystem voice map removal hook.";
    default: return "";
    }
}
} // namespace dolly
