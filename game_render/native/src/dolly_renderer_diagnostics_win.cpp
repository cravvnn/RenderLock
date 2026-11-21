#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstring>
#include <cstdio>
#include <limits>
#include "dolly_renderer_diagnostics.hpp"
#include "dolly_overlay.hpp"
#include "dolly_render_class.hpp"
namespace dolly {
namespace {
constexpr std::uintptr_t kRetirementContext = 0x40;
const RendererDiagnosticLayout* layout = nullptr;
std::uintptr_t renderer = 0;
bool verified = false;
char observed_hash[65]{};
std::uint32_t image_size = 0;
std::uint64_t samples = 0;
ULONGLONG next_sample = 0;
#pragma pack(push, 1)
struct QueueHeader {
    std::uint16_t allocated, capacity_flags;
    std::uint32_t padding;
    std::uint64_t storage;
    std::uint16_t head, tail, free_head, count;
};
struct QueueNode {
    std::uint64_t buffer;
    std::uint16_t previous, next;
    std::uint32_t padding;
};
#pragma pack(pop)
static_assert(sizeof(QueueHeader) == 24 && sizeof(QueueNode) == 16, "Reviewed render queue layout");
bool read_bytes(std::uintptr_t from, void* destination, std::size_t size) noexcept {
    if (from < 0x10000 || size > std::numeric_limits<std::uintptr_t>::max() - from)
        return false;
    // ReadProcessMemory validates the entire range and fails on concurrently
    // unmapped memory. No direct pointer dereference or engine callback occurs.
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(from), destination,
                             size, &got) &&
           got == size;
}
template <class T> bool read(std::uintptr_t from, T& destination) noexcept {
    return read_bytes(from, &destination, sizeof(destination));
}
bool headers(std::uintptr_t base, IMAGE_NT_HEADERS64& nt) noexcept {
    IMAGE_DOS_HEADER dos{};
    return read(base, dos) && dos.e_magic == IMAGE_DOS_SIGNATURE && dos.e_lfanew > 0 &&
           dos.e_lfanew < 4096 && read(base + dos.e_lfanew, nt) &&
           nt.Signature == IMAGE_NT_SIGNATURE && nt.FileHeader.Machine == IMAGE_FILE_MACHINE_AMD64;
}
bool frame_at(std::uintptr_t system, std::uintptr_t offset, std::uint32_t& frame) noexcept {
    std::uintptr_t context = 0, table = 0;
    return layout && read(system + offset, context) && read(context, table) &&
           table >= renderer + layout->table_begin && table < renderer + layout->table_end &&
           read(context + 0x10, frame);
}
bool node_sample(const QueueHeader& header, std::uint16_t index, QueueNode& node,
                 std::uint32_t& stamp) noexcept {
    if (index == 0xffff || index >= header.allocated)
        return false;
    std::uintptr_t table = 0;
    return read(std::uintptr_t(header.storage) + std::uintptr_t(index) * sizeof(QueueNode), node) &&
           read(std::uintptr_t(node.buffer), table) && layout && table == renderer + layout->buffer_table &&
           read(std::uintptr_t(node.buffer) + 8, stamp);
}
bool node_unchanged(const QueueHeader& header, std::uint16_t index,
                    const QueueNode& before) noexcept {
    QueueNode after{};
    return read(std::uintptr_t(header.storage) + std::uintptr_t(index) * sizeof(QueueNode),
                after) &&
           std::memcmp(&before, &after, sizeof(after)) == 0;
}
void publish(unsigned char* memory, RendererDiagnostics& result) noexcept {
    auto out = memory + kRendererDiagnosticsOffset;
    auto sequence = reinterpret_cast<volatile LONG*>(out + 8);
    LONG old = InterlockedCompareExchange(sequence, 0, 0);
    LONG even = (old & 1) ? old + 1 : old;
    InterlockedExchange(sequence, even + 1);
    std::memcpy(out, &result, 8);
    std::memcpy(out + 12, reinterpret_cast<unsigned char*>(&result) + 12, sizeof(result) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, even + 2);
}
void sample_queue(RendererDiagnostics& result) noexcept {
    auto finish = [&](RendererProbeState state, const char* message) {
        result.state = std::uint32_t(state);
        std::snprintf(result.message, sizeof(result.message), "%s", message);
    };
    if (!renderer) {
        finish(RendererProbeState::Waiting, "Waiting for the DirectX 11 renderer module.");
        return;
    }
    if (!verified || !layout) {
        finish(
            RendererProbeState::Unsupported,
            "Renderer fingerprint is not covered by this optional read-only probe. Camera support is unchanged.");
        return;
    }
    IMAGE_NT_HEADERS64 nt{};
    if (reinterpret_cast<std::uintptr_t>(GetModuleHandleW(L"rendersystemdx11.dll")) != renderer ||
        !headers(renderer, nt) || nt.FileHeader.TimeDateStamp != layout->timestamp ||
        nt.OptionalHeader.SizeOfImage != layout->image_size) {
        finish(RendererProbeState::Unreadable,
               "Renderer module changed or became unavailable; private diagnostic reads stopped.");
        return;
    }
    std::uintptr_t system = 0;
    QueueHeader before{}, after{};
    if (!read(renderer + layout->system_pointer, system) || system != renderer + layout->system_object ||
        !read(system + layout->queue, before)) {
        finish(RendererProbeState::Unreadable, "Renderer queue header is unavailable.");
        return;
    }
    result.flags |= 1;
    result.pending_count = before.count;
    result.capacity = before.capacity_flags & 0x7fff;
    result.allocated_slots = before.allocated;
    result.head_index = before.head;
    result.tail_index = before.tail;
    if (before.allocated > result.capacity || before.count > before.allocated ||
        (before.count && (before.storage < 0x10000 || before.storage > 0x00007fffffff0000ULL ||
                          before.head >= before.allocated || before.tail >= before.allocated))) {
        finish(RendererProbeState::Racing,
               "Queue metadata changed while sampled; this observation is incomplete.");
        return;
    }
    // Safety valve: the engine's queue capacity is 32767 and overflowing it is
    // fatal. Ask Dolly's overlay to stop optional rendering well before that so
    // the engine can drain. Hysteresis prevents flapping near the threshold.
    constexpr std::uint32_t kPressureHigh = 20000, kPressureLow = 8000;
    static bool pressure = false;
    if (!pressure && result.pending_count >= kPressureHigh)
        pressure = true;
    else if (pressure && result.pending_count <= kPressureLow)
        pressure = false;
    overlay_set_renderer_pressure(pressure);
    if (frame_at(system, kRetirementContext, result.retirement_frame))
        result.flags |= 4;
    if (frame_at(system, layout->execution_context, result.execution_frame))
        result.flags |= 8;
    QueueNode head{}, tail{};
    const bool head_ok =
        before.count && node_sample(before, before.head, head, result.head_buffer_frame);
    const bool tail_ok =
        before.count && node_sample(before, before.tail, tail, result.tail_buffer_frame);
    if (head_ok)
        result.flags |= 16;
    if (tail_ok)
        result.flags |= 32;
    // At most two nodes are observed; never traverse the engine's large queue
    // or acquire its lock. Equality is a best-effort consistency check, not a
    // guarantee of atomicity across separate renderer threads or reused slots.
    const bool stable = read(system + layout->queue, after) &&
                        std::memcmp(&before, &after, sizeof(before)) == 0 &&
                        (!head_ok || node_unchanged(before, before.head, head)) &&
                        (!tail_ok || node_unchanged(before, before.tail, tail));
    if (!stable) {
        result.flags &= ~(16u | 32u);
        finish(RendererProbeState::Racing,
               "Renderer queue changed during this bounded read-only sample.");
        return;
    }
    result.flags |= 2;
    finish(
        RendererProbeState::Supported,
        pressure
            ? "Renderer vertex-buffer queue is near capacity; Dolly paused its in-game drawing so the engine can recover. Stop replay skipping if the game remains slow."
            : "Read-only queue observation. Head/tail frame stamps are samples, not queue-wide minimum/maximum values.");
}
} // namespace
void renderer_diagnostics_probe(std::uintptr_t module, const char* sha256) noexcept {
    renderer = module;
    verified = false;
    layout = nullptr;
    image_size = 0;
    std::snprintf(observed_hash, sizeof(observed_hash), "%s", sha256 ? sha256 : "");
    IMAGE_NT_HEADERS64 nt{};
    if (module && headers(module, nt)) {
        image_size = nt.OptionalHeader.SizeOfImage;
        layout = renderer_diagnostic_layout(observed_hash, image_size, nt.FileHeader.TimeDateStamp);
        verified = layout != nullptr;
    }
}
void renderer_diagnostics_tick(unsigned char* memory) noexcept {
    if (!memory)
        return;
    auto now = GetTickCount64();
    if (now < next_sample)
        return;
    // Twice a second so the renderer-pressure safety valve can react before
    // the engine's 16-bit vertex-buffer queue overflows.
    next_sample = now + 500;
    RendererDiagnostics result{};
    std::memcpy(result.magic, "DLYGFX01", 8);
    result.abi = kRendererDiagnosticsAbi;
    result.sample = ++samples;
    result.uptime_ms = now;
    result.image_size = image_size;
    std::memcpy(result.renderer_hash, observed_hash, sizeof(observed_hash));
    const auto overlay = overlay_diagnostics();
    result.present_calls = overlay.present_calls;
    result.panel_frames = overlay.panel_frames;
    result.init_attempts = overlay.init_attempts;
    result.init_successes = overlay.init_successes;
    result.release_calls = overlay.release_calls;
    result.resize_calls = overlay.resize_calls;
    result.overlay_draw_frames = overlay.draw_frames;
    result.guide_frames = overlay.guide_frames;
    result.overlay_last_us = overlay.overlay_last_us;
    result.overlay_max_us = overlay.overlay_max_us;
    result.present_last_us = overlay.present_last_us;
    result.present_max_us = overlay.present_max_us;
    result.overlay_lock_skips = overlay.lock_skips;
    result.overlay_active_since_ms = overlay.overlay_active_since_ms;
    result.present_active_since_ms = overlay.present_active_since_ms;
    result.guide_lines = overlay.guide_lines;
    result.guide_labels = overlay.guide_labels;
    classify::diagnostic(result.layers, sizeof(result.layers));
    auto status = memory + kControlBytes;
    auto sequence = reinterpret_cast<volatile LONG*>(status + 8);
    LONG before = InterlockedCompareExchange(sequence, 0, 0);
    if (!(before & 1)) {
        std::memcpy(&result.main_view_frames, status + offsetof(Status, frame_count),
                    sizeof(result.main_view_frames));
        MemoryBarrier();
        if (before == InterlockedCompareExchange(sequence, 0, 0))
            result.flags |= 64;
    }
    sample_queue(result);
    publish(memory, result);
}
} // namespace dolly
