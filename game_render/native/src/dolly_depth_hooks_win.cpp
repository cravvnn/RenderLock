#include "dolly_depth_scene.hpp"
#include "dolly_render_class.hpp"
#include "dolly_player_capture.hpp"
#include <d3d11.h>
#include <MinHook.h>
#include <array>
#include <atomic>
#include <mutex>
#include <vector>

namespace dolly::depth {
namespace {
std::shared_ptr<SceneTracker> active;
std::mutex install_mutex;
// Public ID3D11DeviceContext ABI, shared by Context1/2/3/4. Both immediate
// and deferred implementations are installed; their entry points may differ.
constexpr std::array<unsigned, 13> slots{12, 13, 20, 21, 38, 39, 40, 50, 53, 58, 114, 14, 48};
std::array<std::array<void*, 13>, 2> originals{}, targets{};
bool installed = false;
// Matte passes force the scene's color clear to white so the black/white pair
// can be turned into a real alpha channel after the take. Clears of the
// backbuffer or HUD targets would veil the whole frame, so the scene color
// target is learned from draws that bind the reviewed scene depth family.
// Several full-resolution targets share that depth family (g-buffer,
// visibility), and forcing those white breaks culling and reprojection, so
// candidates keep their format and draw counts and only the busiest HDR scene
// color (R16G16B16A16 family) is forced white.
std::atomic<bool> gWhiteClear{false};
struct WhiteTarget {
    std::atomic<ID3D11RenderTargetView*> view{nullptr};
    std::atomic<std::uint64_t> draws{0};
    std::atomic<unsigned> format{0};
};
std::array<WhiteTarget, 4> gSceneTargets{};
std::atomic<ID3D11RenderTargetView*> gWhiteTarget{nullptr};
// A target learned during a dense scene pass is reused by later sparse passes
// (an effects layer renders almost no geometry). The reference keeps the
// identity valid even if the engine releases its own handle.
std::atomic<ID3D11RenderTargetView*> gRememberedTarget{nullptr};
// The scene color target is the HDR (R16G16B16A16) target cleared by the
// engine. Other full-resolution targets share the scene depth (g-buffer,
// visibility) and clearing those breaks culling or reprojection.
bool preferred_target_format(unsigned format) noexcept {
    return format >= 9 && format <= 14;
}
bool fallback_target_format(unsigned format) noexcept {
    switch (format) {
    case 1:
    case 2:
    case 3:
    case 4: // R32G32B32A32 family
    case 5:
    case 6:
    case 7:
    case 8:  // R32G32B32 family
    case 26: // R11G11B10_FLOAT
    case 27:
    case 28:
    case 29:
    case 30:
    case 31:
    case 32: // R8G8B8A8 family
        return true;
    default:
        return false;
    }
}
void note_scene_color_target(ID3D11DeviceContext* context) noexcept {
    try {
        ID3D11DepthStencilView* dsv = nullptr;
        context->OMGetRenderTargets(0, nullptr, &dsv);
        if (!dsv)
            return;
        ID3D11Resource* resource = nullptr;
        dsv->GetResource(&resource);
        dsv->Release();
        if (!resource)
            return;
        ID3D11Texture2D* texture = nullptr;
        const bool is_texture = SUCCEEDED(resource->QueryInterface(
            __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&texture)));
        resource->Release();
        if (!is_texture)
            return;
        D3D11_TEXTURE2D_DESC desc{};
        texture->GetDesc(&desc);
        char name[160]{};
        UINT size = sizeof(name) - 1;
        const bool named =
            SUCCEEDED(texture->GetPrivateData(WKPDID_D3DDebugObjectName, &size, name));
        texture->Release();
        name[159] = 0;
        if (!named || !scene_target_name(name, desc.Width, desc.Height))
            return;
        ID3D11RenderTargetView* view = nullptr;
        context->OMGetRenderTargets(1, &view, nullptr);
        if (!view)
            return;
        for (auto& slot : gSceneTargets) {
            if (slot.view.load(std::memory_order_relaxed) == view) {
                slot.draws.fetch_add(1, std::memory_order_relaxed);
                view->Release();
                return;
            }
        }
        unsigned format = 0;
        ID3D11Resource* target_resource = nullptr;
        view->GetResource(&target_resource);
        if (target_resource) {
            ID3D11Texture2D* target_texture = nullptr;
            if (SUCCEEDED(target_resource->QueryInterface(
                    __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&target_texture))) &&
                target_texture) {
                D3D11_TEXTURE2D_DESC target_desc{};
                target_texture->GetDesc(&target_desc);
                format = unsigned(target_desc.Format);
                target_texture->Release();
            }
            target_resource->Release();
        }
        for (auto& slot : gSceneTargets) {
            ID3D11RenderTargetView* expected = nullptr;
            if (slot.view.compare_exchange_strong(expected, view, std::memory_order_relaxed)) {
                slot.draws.store(1, std::memory_order_relaxed);
                slot.format.store(format, std::memory_order_relaxed);
                view->Release();
                return;
            }
        }
        view->Release();
    } catch (...) {
    }
}
void select_white_target() noexcept {
    ID3D11RenderTargetView* chosen = nullptr;
    // The players pass fills the HDR target quickly, but a layer with only
    // particles can stay sparse, so the preferred target locks early.
    std::uint64_t best = 3;
    for (auto& slot : gSceneTargets) {
        const auto count = slot.draws.load(std::memory_order_relaxed);
        if (count <= best || !preferred_target_format(slot.format.load(std::memory_order_relaxed)))
            continue;
        best = count;
        chosen = slot.view.load(std::memory_order_relaxed);
    }
    if (!chosen) {
        best = 31;
        for (auto& slot : gSceneTargets) {
            const auto count = slot.draws.load(std::memory_order_relaxed);
            if (count <= best ||
                !fallback_target_format(slot.format.load(std::memory_order_relaxed)))
                continue;
            best = count;
            chosen = slot.view.load(std::memory_order_relaxed);
        }
    }
    if (!chosen)
        return;
    // Hold a reference so the identity stays unique even after the engine
    // releases its own handle, then publish it for this and later passes.
    chosen->AddRef();
    gWhiteTarget.store(chosen, std::memory_order_relaxed);
    ID3D11RenderTargetView* expected = nullptr;
    gRememberedTarget.compare_exchange_strong(expected, chosen, std::memory_order_relaxed);
}
void observe_draw(ID3D11DeviceContext* context) noexcept {
    if (!gWhiteTarget.load(std::memory_order_relaxed)) {
        note_scene_color_target(context);
        select_white_target();
    }
    if (auto tracker = std::atomic_load(&active))
        tracker->draw(context);
}
template <unsigned I>
void STDMETHODCALLTYPE indexed(ID3D11DeviceContext* c, UINT n, UINT start, INT base) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, INT);
    const bool keep = !n || player_capture::allow_draw(c, 1, 0);
    if (n && keep)
        player_capture::draw(c, 0, n, 1, 0);
    if (n && keep)
        classify::draw(c, 0);
    if (!keep)
        return;
    reinterpret_cast<Fn>(originals[I][0])(c, n, start, base);
    if (n)
        observe_draw(c);
}
template <unsigned I> void STDMETHODCALLTYPE draw(ID3D11DeviceContext* c, UINT n, UINT start) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT);
    const bool keep = !n || player_capture::allow_draw(c, 1, 0);
    if (n && keep)
        player_capture::draw(c, 1, n, 1, 0);
    if (n && keep)
        classify::draw(c, 1);
    if (!keep)
        return;
    reinterpret_cast<Fn>(originals[I][1])(c, n, start);
    if (n)
        observe_draw(c);
}
template <unsigned I>
void STDMETHODCALLTYPE indexed_instanced(ID3D11DeviceContext* c, UINT n, UINT instances, UINT start,
                                         INT base, UINT first) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, UINT, INT, UINT);
    const bool keep = !(n && instances) || player_capture::allow_draw(c, instances, first);
    if (!keep)
        return;
    if (n && instances)
        player_capture::draw(c, 2, n, instances, first);
    if (n && instances)
        classify::draw(c, 2);
    player_capture::redirect_indexed(c, n, instances, start, base, first,
                                     reinterpret_cast<Fn>(originals[I][2]));
    if (n && instances)
        observe_draw(c);
}
template <unsigned I>
void STDMETHODCALLTYPE instanced(ID3D11DeviceContext* c, UINT n, UINT instances, UINT start,
                                 UINT first) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, UINT, UINT);
    const bool keep = !(n && instances) || player_capture::allow_draw(c, instances, first);
    if (n && instances && keep)
        player_capture::draw(c, 3, n, instances, first);
    if (n && instances && keep)
        classify::draw(c, 3);
    if (!keep)
        return;
    reinterpret_cast<Fn>(originals[I][3])(c, n, instances, start, first);
    if (n && instances)
        observe_draw(c);
}
template <unsigned I> void STDMETHODCALLTYPE automatic(ID3D11DeviceContext* c) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*);
    player_capture::draw(c, 4, 0, 0, 0);
    classify::draw(c, 4);
    reinterpret_cast<Fn>(originals[I][4])(c);
    observe_draw(c);
}
template <unsigned I, unsigned M>
void STDMETHODCALLTYPE indirect(ID3D11DeviceContext* c, ID3D11Buffer* args, UINT offset) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11Buffer*, UINT);
    player_capture::draw(c, M, 0, 0, 0);
    classify::draw(c, M);
    reinterpret_cast<Fn>(originals[I][M])(c, args, offset);
    observe_draw(c);
}
template <unsigned I>
void STDMETHODCALLTYPE clear_rt(ID3D11DeviceContext* c, ID3D11RenderTargetView* view,
                                const FLOAT color[4]) {
    using Fn =
        void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11RenderTargetView*, const FLOAT*);
    static const FLOAT white[4] = {1, 1, 1, 1};
    const FLOAT* use = color;
    if (gWhiteClear.load(std::memory_order_relaxed) &&
        view == gWhiteTarget.load(std::memory_order_relaxed))
        use = white;
    reinterpret_cast<Fn>(originals[I][7])(c, view, use);
}
template <unsigned I>
void STDMETHODCALLTYPE clear(ID3D11DeviceContext* c, ID3D11DepthStencilView* dsv, UINT flags,
                             FLOAT depth, UINT8 stencil) {
    using Fn =
        void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11DepthStencilView*, UINT, FLOAT, UINT8);
    reinterpret_cast<Fn>(originals[I][8])(c, dsv, flags, depth, stencil);
    if (auto tracker = std::atomic_load(&active))
        tracker->clear(c, dsv, flags);
}
template <unsigned I>
void STDMETHODCALLTYPE execute(ID3D11DeviceContext* c, ID3D11CommandList* list, BOOL restore) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11CommandList*, BOOL);
    player_capture::command(c, list, 4);
    reinterpret_cast<Fn>(originals[I][9])(c, list, restore);
    player_capture::command(c, list, 5);
    if (auto tracker = std::atomic_load(&active))
        tracker->execute(c, list);
}
template <unsigned I>
HRESULT STDMETHODCALLTYPE finish(ID3D11DeviceContext* c, BOOL restore, ID3D11CommandList** list) {
    using Fn = HRESULT(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, BOOL, ID3D11CommandList**);
    const auto result = reinterpret_cast<Fn>(originals[I][10])(c, restore, list);
    if (SUCCEEDED(result) && list && *list)
        player_capture::command(c, *list, 3);
    if (SUCCEEDED(result) && list && *list)
        if (auto tracker = std::atomic_load(&active))
            tracker->finish(c, *list);
    return result;
}
template <unsigned I>
HRESULT STDMETHODCALLTYPE map_upload(ID3D11DeviceContext* c, ID3D11Resource* resource, UINT sub,
                                     D3D11_MAP kind, UINT flags, D3D11_MAPPED_SUBRESOURCE* result) {
    using Fn = HRESULT(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11Resource*, UINT, D3D11_MAP,
                                           UINT, D3D11_MAPPED_SUBRESOURCE*);
    const auto hr = reinterpret_cast<Fn>(originals[I][11])(c, resource, sub, kind, flags, result);
    if (SUCCEEDED(hr) && result)
        player_capture::mapped_upload(c, resource, sub, kind, result->pData);
    return hr;
}
template <unsigned I>
void STDMETHODCALLTYPE update_upload(ID3D11DeviceContext* c, ID3D11Resource* resource, UINT sub,
                                     const D3D11_BOX* box, const void* source, UINT row,
                                     UINT depth) {
    player_capture::update(c, resource, sub, box, source, row, depth,
                           reinterpret_cast<player_capture::UpdateOriginal>(originals[I][12]));
}
template <unsigned I> std::array<void*, 13> detours() {
    return {reinterpret_cast<void*>(&indexed<I>),
            reinterpret_cast<void*>(&draw<I>),
            reinterpret_cast<void*>(&indexed_instanced<I>),
            reinterpret_cast<void*>(&instanced<I>),
            reinterpret_cast<void*>(&automatic<I>),
            reinterpret_cast<void*>(&indirect<I, 5>),
            reinterpret_cast<void*>(&indirect<I, 6>),
            reinterpret_cast<void*>(&clear_rt<I>),
            reinterpret_cast<void*>(&clear<I>),
            reinterpret_cast<void*>(&execute<I>),
            reinterpret_cast<void*>(&finish<I>),
            reinterpret_cast<void*>(&map_upload<I>),

            reinterpret_cast<void*>(&update_upload<I>)};
}
}
void set_white_clear(bool enabled) noexcept {
    if (enabled) {
        if (!gWhiteClear.exchange(true, std::memory_order_acq_rel)) {
            // New matte pass: forget this pass's candidates but reuse a target
            // learned during a denser pass (an effects layer renders almost no
            // geometry, so it cannot learn the scene color on its own).
            gWhiteTarget.store(gRememberedTarget.load(std::memory_order_relaxed),
                               std::memory_order_relaxed);
            for (auto& slot : gSceneTargets) {
                slot.view.store(nullptr, std::memory_order_relaxed);
                slot.draws.store(0, std::memory_order_relaxed);
            }
        }
    } else {
        gWhiteClear.store(false, std::memory_order_relaxed);
    }
}
void set_scene_tracker(std::shared_ptr<SceneTracker> tracker) noexcept {
    std::atomic_store(&active, std::move(tracker));
}
bool install_scene_hooks(ID3D11Device* device, ID3D11DeviceContext* immediate) noexcept {
    if (!device || !immediate || immediate->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE)
        return false;
    ID3D11Device* owner = nullptr;
    immediate->GetDevice(&owner);
    const bool owned = owner == device;
    if (owner)
        owner->Release();
    if (!owned)
        return false;
    ID3D11DeviceContext* deferred = nullptr;
    if (FAILED(device->CreateDeferredContext(0, &deferred)))
        return false;
    std::array<std::array<void*, 13>, 2> found{};
    auto** a = *reinterpret_cast<void***>(immediate);
    auto** b = *reinterpret_cast<void***>(deferred);
    for (unsigned m = 0; m < slots.size(); ++m) {
        found[0][m] = a[slots[m]];
        found[1][m] = b[slots[m]];
    }
    deferred->Release();
    try {
        std::lock_guard<std::mutex> lock(install_mutex);
        if (installed)
            return found == targets;
        const auto init = MH_Initialize();
        if (init != MH_OK && init != MH_ERROR_ALREADY_INITIALIZED)
            return false;
        const std::array<std::array<void*, 13>, 2> callbacks{detours<0>(), detours<1>()};
        std::vector<void*> created;
        created.reserve(26);
        const auto rollback = [&created]() {
            for (auto* target : created) {
                MH_DisableHook(target);
                MH_RemoveHook(target);
            }
        };
        for (unsigned i = 0; i < 2; ++i) {
            for (unsigned m = 0; m < slots.size(); ++m) {
                if (i && found[i][m] == found[0][m]) {
                    originals[i][m] = originals[0][m];
                    continue;
                }
                if (MH_CreateHook(found[i][m], callbacks[i][m], &originals[i][m]) != MH_OK) {
                    rollback();
                    return false;
                }
                created.push_back(found[i][m]);
            }
        }
        // Enable only this module's hooks. Do not affect the overlay or any
        // other MinHook client sharing the process.
        for (auto* target : created) {
            if (MH_EnableHook(target) != MH_OK) {
                rollback();
                return false;
            }
        }
        targets = found;
        installed = true;
        return true;
    } catch (...) {
        return false;
    }
}
}
