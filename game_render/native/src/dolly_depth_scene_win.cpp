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
#include <vector>
#include "dolly_depth_scene.hpp"

namespace dolly::depth {
namespace {
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
};
constexpr std::size_t kBindings = 14, kMaxEvents = 256, kMaxContexts = 64;
const GUID kCommandData = {
    0x421d0f73, 0xbd87, 0x4eda, {0xa1, 0x79, 0x55, 0x0c, 0x94, 0x4c, 0x1b, 0x4e}};
const GUID kCommandType = {
    0x2c1e2256, 0x1747, 0x49e6, {0x87, 0x05, 0x5b, 0x2a, 0x6c, 0x0d, 0xae, 0xa9}};
std::atomic<std::uint64_t> next_epoch{0};
std::atomic<std::uint64_t> gDrawCalls{0}, gMatchedDraws{0};
std::atomic<bool> gHooksInstalled{false};
std::atomic<std::uint32_t> gLastResult{1}; // SceneResult::missing
enum class EventKind : std::uint32_t { none = 0, ready, clear, incompatible, uncalibrated };
std::atomic<std::uint32_t> gLastEvent{0}, gConsumedEvent{0}, gConsumedTargets{0};
const char* event_kind_name(std::uint32_t value) noexcept {
    switch (static_cast<EventKind>(value)) {
    case EventKind::ready: return "ready";
    case EventKind::clear: return "clear";
    case EventKind::incompatible: return "incompatible";
    case EventKind::uncalibrated: return "uncalibrated";
    case EventKind::none: return "none";
    }
    return "none";
}
std::mutex gDiagnosticMutex;
char gLastRejected[160]{}, gConsumedRejected[160]{};
// Sticky identity of the reviewed scene-target family observed at any
// resolution, plus the recording size the tracker was built for. A depth take
// that never verifies a frame is usually the game rendering the scene at
// another size (upscaling or resolution scaling), with multisampling, or with
// an unusable view or format. Naming the observed target and both sizes is
// what turns that silent zero-frame take into an actionable message.
char gFamilySeen[160]{};
std::uint32_t gFamilyWidth = 0, gFamilyHeight = 0;
std::uint64_t gFamilyDraws = 0;
std::atomic<std::uint32_t> gRecordingWidth{0}, gRecordingHeight{0};
// Last verified scene-target size. With upscaling or resolution scaling this
// is below the recording size and is the resolution the paired depth data is
// captured at.
std::atomic<std::uint32_t> gSceneWidth{0}, gSceneHeight{0};
std::mutex gFailureMutex;
char gLastFailure[160]{}, gConsumedFailure[160]{};
void note_failure(const char* reason) noexcept {
    std::unique_lock<std::mutex> lock(gFailureMutex, std::try_to_lock);
    if (lock.owns_lock())
        std::snprintf(gLastFailure, sizeof(gLastFailure), "%s", reason);
}
const char* comparison_name(D3D11_COMPARISON_FUNC value) noexcept {
    switch (value) {
    case D3D11_COMPARISON_NEVER: return "NEVER";
    case D3D11_COMPARISON_LESS: return "LESS";
    case D3D11_COMPARISON_EQUAL: return "EQUAL";
    case D3D11_COMPARISON_LESS_EQUAL: return "LESS_EQUAL";
    case D3D11_COMPARISON_GREATER: return "GREATER";
    case D3D11_COMPARISON_NOT_EQUAL: return "NOT_EQUAL";
    case D3D11_COMPARISON_GREATER_EQUAL: return "GREATER_EQUAL";
    case D3D11_COMPARISON_ALWAYS: return "ALWAYS";
    }
    return "unknown";
}
const char* scene_result_name(SceneResult result) noexcept {
    switch (result) {
    case SceneResult::ready: return "ready";
    case SceneResult::missing: return "missing";
    case SceneResult::cleared: return "cleared";
    case SceneResult::ambiguous: return "ambiguous";
    case SceneResult::incompatible_view: return "incompatible";
    case SceneResult::untracked_commands: return "untracked";
    case SceneResult::failed: return "failed";
    }
    return "unknown";
}
bool scene_name_matches(const char* name, std::uint32_t width, std::uint32_t height) noexcept {
    // Reviewed family from both game captures:
    // scratchrendertarget_<id>_<width>x<height>_<a>_<b>.vtex
    // The middle ID and trailing pair are not stable runtime identifiers, so
    // match the family plus the full resolution instead of one captured name.
    constexpr char prefix[] = "scratchrendertarget_";
    if (!name)
        return false;
    const auto length = std::strlen(name);
    const auto prefix_length = sizeof(prefix) - 1;
    if (length <= prefix_length + 5 || std::strncmp(name, prefix, prefix_length) != 0 ||
        std::strcmp(name + length - 5, ".vtex") != 0)
        return false;
    char resolution[48]{};
    std::snprintf(resolution, sizeof(resolution), "_%ux%u_", width, height);
    return std::strstr(name, resolution) != nullptr;
}
bool scene_family_name(const char* name) noexcept {
    // Reviewed family only: scratchrendertarget_<id>_<width>x<height>_<a>_<b>.vtex
    // The resolution is deliberately not compared here, so a target that
    // differs only by size can still be recorded in the diagnostic.
    constexpr char prefix[] = "scratchrendertarget_";
    if (!name)
        return false;
    const auto length = std::strlen(name);
    const auto prefix_length = sizeof(prefix) - 1;
    return length > prefix_length + 5 && std::strncmp(name, prefix, prefix_length) == 0 &&
           std::strcmp(name + length - 5, ".vtex") == 0;
}
bool reject_name(const char* name) noexcept {
    if (name) {
        std::unique_lock<std::mutex> lock(gDiagnosticMutex, std::try_to_lock);
        if (lock.owns_lock())
            std::snprintf(gLastRejected, sizeof(gLastRejected), "%s", name);
    }
    return false;
}
void note_family(const char* name, std::uint32_t width, std::uint32_t height) noexcept {
    std::unique_lock<std::mutex> lock(gDiagnosticMutex, std::try_to_lock);
    if (!lock.owns_lock())
        return;
    std::snprintf(gFamilySeen, sizeof(gFamilySeen), "%s", name ? name : "");
    gFamilyWidth = width;
    gFamilyHeight = height;
    ++gFamilyDraws;
}
}

struct SceneResources {
    Com<ID3D11Texture2D> depth;
    // Size of the reviewed scene target, in its own pixels.
    std::uint32_t width = 0, height = 0;
    std::array<Com<ID3D11Buffer>, kBindings> buffers;
    std::array<std::uint32_t, kBindings> sizes{};
    std::size_t count = 0;
};
ID3D11Texture2D* SceneFrame::texture() const noexcept {
    return resources ? resources->depth.p : nullptr;
}
std::size_t SceneFrame::calibration(ProjectionBuffer* output, std::size_t capacity) const noexcept {
    if (!resources || !output || capacity < resources->count)
        return 0;
    for (std::size_t i = 0; i < resources->count; ++i)
        output[i] = {resources->buffers[i].p, 0, resources->sizes[i]};
    return resources->count;
}

namespace {
struct Event {
    bool clear = false;
    std::shared_ptr<SceneResources> source;
    bool incompatible_view = false;
    bool uncalibrated = false;
};
// Keep the observation list as a compact state history instead of an append
// log. Alternating scene targets otherwise overflow the per-frame budget and
// force the oldest transitions out, which can misreport a frame. A new state
// for one target supersedes the older states it overwrites:
//   ready        rewrites the target, so it replaces every earlier event;
//   incompatible keeps an earlier ready (ready -> incompatible matters);
//   uncalibrated keeps an earlier ready (the frame's projection still maps);
//   clear        keeps an earlier ready/incompatible and only drops older clears.
void append_event(std::vector<Event>& events, Event event) {
    if (event.source) {
        const auto* target = event.source->depth.p;
        events.erase(std::remove_if(events.begin(), events.end(),
                                    [&](const Event& existing) {
                                        if (!existing.source || existing.source->depth.p != target)
                                            return false;
                                        if (event.clear)
                                            return existing.clear;
                                        if (event.incompatible_view)
                                            return existing.incompatible_view;
                                        if (event.uncalibrated)
                                            return existing.uncalibrated;
                                        return true;
                                    }),
                     events.end());
    }
    if (events.size() >= kMaxEvents) {
        // Distinct-target alternation is bounded, so this stays a last resort.
        note_failure("event budget");
        events.erase(events.begin());
    }
    events.push_back(std::move(event));
}
struct CommandData {
    std::uint64_t epoch = 0;
    std::vector<Event> events;
};
struct CommandInterface : IUnknown {
    virtual const CommandData& data() const noexcept = 0;
};
class CommandHolder final : public CommandInterface {
    std::atomic<ULONG> refs{1};
    CommandData value;

public:
    explicit CommandHolder(CommandData data) : value(std::move(data)) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void** out) override {
        if (!out)
            return E_POINTER;
        *out = nullptr;
        if (id != __uuidof(IUnknown) && id != kCommandType)
            return E_NOINTERFACE;
        *out = static_cast<CommandInterface*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto left = --refs;
        if (!left)
            delete this;
        return left;
    }
    const CommandData& data() const noexcept override { return value; }
};
}

struct SceneTracker::Impl {
    Com<ID3D11Device> device;
    std::uint32_t width, height;
    std::uint64_t epoch = ++next_epoch;
    std::mutex mutex;
    std::atomic<bool> failed{false};
    struct Context {
        Com<ID3D11DeviceContext> owner;
        std::vector<Event> events;
    };
    std::unordered_map<ID3D11DeviceContext*, Context> contexts;
    SceneFrame frame;
    Com<ID3D11Texture2D> first_source;
    // Size of the first chosen target this frame. A larger reviewed target
    // supersedes it (the scene can be rendered below the recording size with
    // upscaling enabled); a smaller one never replaces the chosen scene.
    std::uint32_t first_width = 0, first_height = 0;
    // Distinct reviewed scene targets observed this frame, for the diagnostic
    // only. Bounded, so unusual frames cannot grow it.
    std::array<const void*, 16> seen_targets{};
    std::size_t seen_count = 0;

    void note_target(const void* target) noexcept {
        for (std::size_t i = 0; i < seen_count; ++i)
            if (seen_targets[i] == target)
                return;
        if (seen_count < seen_targets.size())
            seen_targets[seen_count++] = target;
    }

    Impl(ID3D11Device* value, std::uint32_t w, std::uint32_t h) : width(w), height(h) {
        device.p = value;
        if (value)
            value->AddRef();
        if (!value || !w || !h || w > 16384 || h > 16384) {
            note_failure("invalid device or size");
            failed = true;
        }
    }
    bool owns(ID3D11DeviceContext* context) const noexcept {
        if (!context || failed)
            return false;
        Com<ID3D11Device> owner;
        context->GetDevice(&owner.p);
        return owner.p == device.p;
    }
    bool source(ID3D11DepthStencilView* view, Com<ID3D11Texture2D>& result,
                std::uint32_t* out_width = nullptr, std::uint32_t* out_height = nullptr) noexcept {
        if (!view)
            return false;
        Com<ID3D11Resource> resource;
        view->GetResource(&resource.p);
        if (!resource.p || FAILED(resource.p->QueryInterface(__uuidof(ID3D11Texture2D),
                                                             reinterpret_cast<void**>(&result.p))))
            return false;
        D3D11_TEXTURE2D_DESC desc{};
        result.p->GetDesc(&desc);
        D3D11_DEPTH_STENCIL_VIEW_DESC dsv{};
        view->GetDesc(&dsv);
        // Reviewed scene-target family; the captured numeric ID is only one
        // instance of it. The name is read before the property checks so a
        // family target that cannot back a paired depth frame can be named in
        // the diagnostic instead of being skipped without a reason. The target
        // size is not compared with the recording: with upscaling or resolution
        // scaling the game renders the scene below the swapchain size, and the
        // paired depth is captured at the target's own resolution.
        char name[160]{};
        UINT size = sizeof(name) - 1;
        if (FAILED(result.p->GetPrivateData(WKPDID_D3DDebugObjectName, &size, name)) ||
            size >= sizeof(name))
            return reject_name(nullptr);
        if (!scene_family_name(name))
            return reject_name(name);
        if (desc.SampleDesc.Count != 1) {
            char reason[64]{};
            std::snprintf(reason, sizeof(reason), "target multisample %u",
                          static_cast<unsigned>(desc.SampleDesc.Count));
            note_family(name, desc.Width, desc.Height);
            note_failure(reason);
            return reject_name(name);
        }
        if (desc.ArraySize != 1 || desc.MipLevels != 1 ||
            dsv.ViewDimension != D3D11_DSV_DIMENSION_TEXTURE2D || dsv.Texture2D.MipSlice != 0 ||
            (dsv.Flags & D3D11_DSV_READ_ONLY_DEPTH) != 0 ||
            (dsv.Format != DXGI_FORMAT_D24_UNORM_S8_UINT &&
             dsv.Format != DXGI_FORMAT_D32_FLOAT)) {
            note_family(name, desc.Width, desc.Height);
            note_failure("target depth view or format");
            return reject_name(name);
        }
        if (!scene_name_matches(name, desc.Width, desc.Height)) {
            note_family(name, desc.Width, desc.Height);
            note_failure("target name");
            return reject_name(name);
        }
        if (out_width)
            *out_width = desc.Width;
        if (out_height)
            *out_height = desc.Height;
        gMatchedDraws.fetch_add(1, std::memory_order_relaxed);
        return true;
    }
    Context* record(ID3D11DeviceContext* context) {
        auto found = contexts.find(context);
        if (found != contexts.end())
            return &found->second;
        if (contexts.size() >= kMaxContexts) {
            note_failure("context budget");
            failed = true;
            return nullptr;
        }
        auto& entry = contexts.try_emplace(context).first->second;
        entry.owner.p = context;
        context->AddRef();
        return &entry;
    }
    void apply(const Event& event) {
        gLastEvent.store(static_cast<std::uint32_t>(event.incompatible_view
                                                        ? EventKind::incompatible
                                                    : event.uncalibrated
                                                        ? EventKind::uncalibrated
                                                    : event.clear ? EventKind::clear
                                                                  : EventKind::ready),
                         std::memory_order_relaxed);
        // Two different supported scene targets in one frame stay ambiguous.
        if (frame.result == SceneResult::ambiguous)
            return;
        if (event.incompatible_view) {
            // Only an unsupported alteration of the chosen scene target can
            // invalidate the sample. A later full supported scene draw rewrites
            // that target and clears the state again, so depth prepasses or
            // secondary passes cannot poison the whole frame. Unsupported draws
            // on other family targets never touch the chosen scene.
            if (frame.resources && frame.resources->depth.p == event.source->depth.p) {
                frame.resources.reset();
                frame.result = SceneResult::incompatible_view;
                frame.width = frame.height = 0;
            }
            return;
        }
        if (event.uncalibrated) {
            // A supported scene write with no per-view constants. It used this
            // frame's camera, so the frame's verified projection still maps the
            // target; keep the sample. Without one the frame stays unverified.
            const bool chosen = frame.resources && frame.resources->depth.p == event.source->depth.p;
            if (!chosen && frame.width && event.source->width < frame.width)
                return; // a smaller family target cannot invalidate the chosen scene
            if (!chosen) {
                frame.resources.reset();
                frame.result = SceneResult::missing;
                frame.width = frame.height = 0;
            }
            return;
        }
        if (event.clear) {
            if (frame.resources && frame.resources->depth.p == event.source->depth.p) {
                frame.resources.reset();
                frame.result = SceneResult::cleared;
                frame.width = frame.height = 0;
            }
            return;
        }
        // A supported scene draw. The reviewed family can appear at more than
        // one size (upscaling or resolution scaling renders the scene below the
        // recording size), so the largest full-target pass wins; a smaller
        // family target never replaces the chosen scene, and two different
        // targets of the same size stay ambiguous.
        if (first_source.p) {
            const bool same = first_source.p == event.source->depth.p;
            const auto chosen_area = std::uint64_t(first_width) * first_height;
            const auto incoming_area = std::uint64_t(event.source->width) * event.source->height;
            if (!same && incoming_area < chosen_area)
                return;
            if (!same && incoming_area == chosen_area) {
                frame = {SceneResult::ambiguous, {}, 0, 0};
                return;
            }
            if (!same) {
                first_source.p->Release();
                first_source.p = event.source->depth.p;
                first_source.p->AddRef();
                first_width = event.source->width;
                first_height = event.source->height;
            }
        } else {
            first_source.p = event.source->depth.p;
            first_source.p->AddRef();
            first_width = event.source->width;
            first_height = event.source->height;
        }
        frame = {SceneResult::ready, event.source, event.source->width, event.source->height};
    }
    bool snapshot(ID3D11DeviceContext* context, SceneResources& target) {
        std::array<ID3D11Buffer*, kBindings> bindings{};
        std::array<UINT, kBindings> first{}, counts{};
        Com<ID3D11DeviceContext1> extended;
        if (SUCCEEDED(context->QueryInterface(__uuidof(ID3D11DeviceContext1),
                                              reinterpret_cast<void**>(&extended.p)))) {
            extended.p->VSGetConstantBuffers1(0, kBindings, bindings.data(), first.data(),
                                              counts.data());
        } else {
            context->VSGetConstantBuffers(0, kBindings, bindings.data());
        }
        // Build into a local count and commit only on success, so a failed
        // snapshot leaves an existing verified calibration untouched.
        std::size_t count = 0;
        for (std::size_t slot = 0; slot < kBindings; ++slot) {
            Com<ID3D11Buffer> bound;
            bound.p = bindings[slot];
            if (!bound.p)
                continue;
            D3D11_BUFFER_DESC desc{};
            bound.p->GetDesc(&desc);
            const auto offset = std::uint64_t(first[slot]) * 16;
            if (offset > desc.ByteWidth)
                continue;
            auto bytes = std::min<std::uint64_t>(640, desc.ByteWidth - offset);
            if (extended.p)
                bytes = std::min<std::uint64_t>(bytes, std::uint64_t(counts[slot]) * 16);
            if (bytes < 464)
                continue;
            if (!target.buffers[count].p || target.sizes[count] != bytes) {
                target.buffers[count].reset();
                D3D11_BUFFER_DESC copy{};
                copy.ByteWidth = static_cast<UINT>(bytes);
                copy.Usage = D3D11_USAGE_DEFAULT;
                if (FAILED(device.p->CreateBuffer(&copy, nullptr, &target.buffers[count].p))) {
                    // One failed copy must not discard a usable observation.
                    // The readback parser rejects inconsistent calibration.
                    note_failure("calibration buffer");
                    continue;
                }
                target.sizes[count] = static_cast<UINT>(bytes);
            }
            D3D11_BOX range{static_cast<UINT>(offset),         0, 0,
                            static_cast<UINT>(offset + bytes), 1, 1};
            context->CopySubresourceRegion(target.buffers[count].p, 0, 0, 0, 0, bound.p, 0,
                                           &range);
            ++count;
        }
        if (!count)
            return false;
        target.count = count;
        return true;
    }
};

SceneTracker::SceneTracker(ID3D11Device* device, std::uint32_t width, std::uint32_t height)
    : impl(std::make_unique<Impl>(device, width, height)) {
    gRecordingWidth.store(width, std::memory_order_relaxed);
    gRecordingHeight.store(height, std::memory_order_relaxed);
}
SceneTracker::~SceneTracker() = default;

void SceneTracker::draw(ID3D11DeviceContext* context) noexcept {
    if (!impl->owns(context))
        return;
    gDrawCalls.fetch_add(1, std::memory_order_relaxed);
    try {
        Com<ID3D11DepthStencilView> dsv;
        context->OMGetRenderTargets(0, nullptr, &dsv.p);
        Com<ID3D11Texture2D> texture;
        std::uint32_t texture_width = 0, texture_height = 0;
        if (!impl->source(dsv.p, texture, &texture_width, &texture_height))
            return;
        Com<ID3D11DepthStencilState> state;
        UINT stencil = 0;
        context->OMGetDepthStencilState(&state.p, &stencil);
        D3D11_DEPTH_STENCIL_DESC desc{};
        // A null state has enabled writes and the forward-Z LESS comparison.
        desc.DepthEnable = true;
        desc.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        desc.DepthFunc = D3D11_COMPARISON_LESS;
        if (state.p)
            state.p->GetDesc(&desc);
        if (!desc.DepthEnable || desc.DepthWriteMask != D3D11_DEPTH_WRITE_MASK_ALL)
            return;
        UINT count = 1;
        D3D11_VIEWPORT viewport{};
        context->RSGetViewports(&count, &viewport);
        const bool full_viewport = count == 1 && viewport.TopLeftX == 0 &&
                                   viewport.TopLeftY == 0 && viewport.Width == texture_width &&
                                   viewport.Height == texture_height && viewport.MinDepth == 0 &&
                                   viewport.MaxDepth == 1;
        const bool reversed_test = desc.DepthFunc == D3D11_COMPARISON_GREATER_EQUAL ||
                                   desc.DepthFunc == D3D11_COMPARISON_GREATER;
        // An EQUAL test with writes can only store a depth that is already
        // stored: the fragment passes only when its depth equals the stored
        // value, and the write then stores that same value. Such a pass cannot
        // alter the verified scene sample, so ignore it instead of treating it
        // as an incompatible overwrite. A frame with no supported main pass
        // still fails closed through the normal missing/cleared states.
        const bool value_preserving = desc.DepthFunc == D3D11_COMPARISON_EQUAL;
        const bool supported = full_viewport && reversed_test;
        const bool harmless = full_viewport && value_preserving;
        std::lock_guard<std::mutex> lock(impl->mutex);
        impl->note_target(texture.p);
        if (!supported && !harmless &&
            (!impl->first_source.p || impl->first_source.p == texture.p)) {
            if (full_viewport) {
                char reason[64]{};
                std::snprintf(reason, sizeof(reason), "depth function=%s",
                              comparison_name(desc.DepthFunc));
                note_failure(reason);
            } else {
                note_failure("viewport");
            }
        }
        if (harmless)
            return;
        auto* record = impl->record(context);
        if (!record)
            return;
        // Reuse the previous event when the same scene target is drawn again
        // without an intervening clear or unsupported overwrite.
        const bool reused = supported && !record->events.empty() &&
                            !record->events.back().clear &&
                            !record->events.back().incompatible_view &&
                            record->events.back().source->depth.p == texture.p;
        std::shared_ptr<SceneResources> snapshot;
        if (reused) {
            snapshot = record->events.back().source;
            snapshot->width = texture_width;
            snapshot->height = texture_height;
        } else {
            snapshot = std::make_shared<SceneResources>();
            snapshot->depth.p = texture.p;
            texture.p->AddRef();
            snapshot->width = texture_width;
            snapshot->height = texture_height;
        }
        // Calibration is copied before the event is trusted. A supported draw
        // without per-view constants keeps the frame's verified projection; an
        // unsupported alteration still fails the frame closed.
        const bool usable = supported && impl->snapshot(context, *snapshot);
        const bool uncalibrated = supported && !usable;
        if (uncalibrated)
            note_failure("calibration snapshot");
        if (!reused)
            append_event(record->events, {false, snapshot, !supported, uncalibrated});
        else if (!supported || uncalibrated) {
            record->events.back().incompatible_view = !supported;
            record->events.back().uncalibrated = uncalibrated;
            record->events.back().source = snapshot;
        }
        if (context->GetType() == D3D11_DEVICE_CONTEXT_IMMEDIATE)
            impl->apply(record->events.back());
    } catch (...) {
        note_failure("draw exception");
        impl->failed = true;
    }
}
void SceneTracker::clear(ID3D11DeviceContext* context, ID3D11DepthStencilView* depth,
                         unsigned flags) noexcept {
    if (!impl->owns(context) || !(flags & D3D11_CLEAR_DEPTH))
        return;
    try {
        Com<ID3D11Texture2D> source;
        std::uint32_t source_width = 0, source_height = 0;
        if (!impl->source(depth, source, &source_width, &source_height))
            return;
        std::lock_guard<std::mutex> lock(impl->mutex);
        impl->note_target(source.p);
        auto* record = impl->record(context);
        if (!record)
            return;
        auto snapshot = std::make_shared<SceneResources>();
        snapshot->depth.p = source.p;
        source.p->AddRef();
        snapshot->width = source_width;
        snapshot->height = source_height;
        append_event(record->events, {true, snapshot, false, false});
        if (context->GetType() == D3D11_DEVICE_CONTEXT_IMMEDIATE)
            impl->apply(record->events.back());
    } catch (...) {
        note_failure("clear exception");
        impl->failed = true;
    }
}
void SceneTracker::finish(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept {
    if (!impl->owns(context) || !list)
        return;
    try {
        std::lock_guard<std::mutex> lock(impl->mutex);
        CommandData data;
        data.epoch = impl->epoch;
        auto found = impl->contexts.find(context);
        if (found != impl->contexts.end()) {
            data.events = std::move(found->second.events);
            impl->contexts.erase(found);
        }
        auto* holder = new CommandHolder(std::move(data));
        const auto result = list->SetPrivateDataInterface(kCommandData, holder);
        holder->Release();
        if (FAILED(result)) {
            note_failure("command list tagging");
            impl->failed = true;
        }
    } catch (...) {
        note_failure("finish exception");
        impl->failed = true;
    }
}
void SceneTracker::execute(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept {
    if (!impl->owns(context) || !list)
        return;
    try {
        Com<IUnknown> holder;
        UINT size = sizeof(holder.p);
        Com<CommandInterface> commands;
        const bool known = SUCCEEDED(list->GetPrivateData(kCommandData, &size, &holder.p)) &&
                           size == sizeof(holder.p) && holder.p &&
                           SUCCEEDED(holder.p->QueryInterface(
                               kCommandType, reinterpret_cast<void**>(&commands.p))) &&
                           commands.p->data().epoch == impl->epoch;
        std::lock_guard<std::mutex> lock(impl->mutex);
        if (!known) {
            impl->frame = {SceneResult::untracked_commands, {}};
            return;
        }
        for (const auto& event : commands.p->data().events)
            impl->apply(event);
    } catch (...) {
        note_failure("execute exception");
        impl->failed = true;
    }
}
SceneFrame SceneTracker::consume(ID3D11DeviceContext* immediate) noexcept {
    if (!impl->owns(immediate) || immediate->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE) {
        gLastResult.store(static_cast<std::uint32_t>(SceneResult::failed),
                          std::memory_order_relaxed);
        return {SceneResult::failed, {}};
    }
    try {
        std::lock_guard<std::mutex> lock(impl->mutex);
        SceneFrame result = std::move(impl->frame);
        impl->frame = {};
        impl->first_source.reset();
        impl->first_width = impl->first_height = 0;
        impl->contexts.erase(immediate);
        gConsumedEvent.store(gLastEvent.load(std::memory_order_relaxed),
                             std::memory_order_relaxed);
        gConsumedTargets.store(static_cast<std::uint32_t>(impl->seen_count),
                               std::memory_order_relaxed);
        impl->seen_count = 0;
        // A frame boundary ends its failure history. Keeping an older frame's
        // reason would explain the next frame with stale evidence.
        {
            std::unique_lock<std::mutex> failure(gFailureMutex, std::try_to_lock);
            if (failure.owns_lock()) {
                std::snprintf(gConsumedFailure, sizeof(gConsumedFailure), "%s", gLastFailure);
                gLastFailure[0] = '\0';
            }
        }
        {
            std::unique_lock<std::mutex> rejected(gDiagnosticMutex, std::try_to_lock);
            if (rejected.owns_lock()) {
                std::snprintf(gConsumedRejected, sizeof(gConsumedRejected), "%s", gLastRejected);
                gLastRejected[0] = '\0';
            }
        }
        if (impl->failed) {
            gLastResult.store(static_cast<std::uint32_t>(SceneResult::failed),
                              std::memory_order_relaxed);
            return {SceneResult::failed, {}};
        }
        if (result.result == SceneResult::ready && result.width && result.height) {
            gSceneWidth.store(result.width, std::memory_order_relaxed);
            gSceneHeight.store(result.height, std::memory_order_relaxed);
        }
        gLastResult.store(static_cast<std::uint32_t>(result.result), std::memory_order_relaxed);
        return result;
    } catch (...) {
        note_failure("consume exception");
        impl->failed = true;
        gLastResult.store(static_cast<std::uint32_t>(SceneResult::failed),
                          std::memory_order_relaxed);
        return {SceneResult::failed, {}};
    }
}
const char* scene_diagnostic() noexcept {
    static thread_local char text[512]{};
    char rejected[128]{};
    char family[160]{};
    unsigned family_width = 0, family_height = 0;
    unsigned long long family_draws = 0;
    {
        std::unique_lock<std::mutex> lock(gDiagnosticMutex, std::try_to_lock);
        if (lock.owns_lock()) {
            std::snprintf(rejected, sizeof(rejected), "%s", gConsumedRejected);
            std::snprintf(family, sizeof(family), "%s", gFamilySeen);
            family_width = static_cast<unsigned>(gFamilyWidth);
            family_height = static_cast<unsigned>(gFamilyHeight);
            family_draws = static_cast<unsigned long long>(gFamilyDraws);
        }
    }
    char failure[128]{};
    {
        std::unique_lock<std::mutex> lock(gFailureMutex, std::try_to_lock);
        if (lock.owns_lock())
            std::snprintf(failure, sizeof(failure), "%s", gConsumedFailure);
    }
    char recording[32]{};
    std::snprintf(recording, sizeof(recording), "%ux%u",
                  static_cast<unsigned>(gRecordingWidth.load(std::memory_order_relaxed)),
                  static_cast<unsigned>(gRecordingHeight.load(std::memory_order_relaxed)));
    char scene[32]{};
    std::snprintf(scene, sizeof(scene), "%ux%u",
                  static_cast<unsigned>(gSceneWidth.load(std::memory_order_relaxed)),
                  static_cast<unsigned>(gSceneHeight.load(std::memory_order_relaxed)));
    // Observed identity comes before the counters so the cause survives a
    // bounded status message: the reviewed family size when one was seen, or
    // the rejected scene-target name when it was not.
    char observed[224]{};
    if (family[0])
        std::snprintf(observed, sizeof(observed), "%ux%u x%llu", family_width, family_height,
                      family_draws);
    else if (rejected[0])
        std::snprintf(observed, sizeof(observed), "%s", rejected);
    std::snprintf(text, sizeof(text),
                  "scene result=%s why=%s recording=%s scene=%s observed=%s event=%s targets=%u "
                  "draws=%llu matched=%llu hooks=%u",
                  scene_result_name(static_cast<SceneResult>(
                      gLastResult.load(std::memory_order_relaxed))),
                  failure[0] ? failure : "none", recording,
                  gSceneWidth.load(std::memory_order_relaxed) ? scene : "none",
                  observed[0] ? observed : "none",
                  event_kind_name(gConsumedEvent.load(std::memory_order_relaxed)),
                  static_cast<unsigned>(gConsumedTargets.load(std::memory_order_relaxed)),
                  static_cast<unsigned long long>(gDrawCalls.load(std::memory_order_relaxed)),
                  static_cast<unsigned long long>(gMatchedDraws.load(std::memory_order_relaxed)),
                  gHooksInstalled.load(std::memory_order_relaxed) ? 1u : 0u);
    return text;
}
void scene_note_hooks(bool installed) noexcept {
    gHooksInstalled.store(installed, std::memory_order_relaxed);
}
bool scene_target_name(const char* name, std::uint32_t width, std::uint32_t height) noexcept {
    return scene_name_matches(name, width, height);
}
}
