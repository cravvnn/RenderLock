#pragma once
#include <cstdint>
#include "dolly_player_scene_generated.hpp"
struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11CommandList;
struct ID3D11Resource;
struct ID3D11Buffer;
struct D3D11_BOX;
namespace dolly::player_capture {
using Producer = int(__fastcall*)(std::uintptr_t, void*, void*, void*, void*, std::uint32_t,
                                  unsigned char*, std::uint32_t*, std::uint32_t*);
using ProducerSeptember = int(__fastcall*)(std::uintptr_t, void*, void*, void*, void*,
                                           std::uint32_t, unsigned char*, std::uint32_t*,
                                           std::uint32_t*, std::uint32_t*);
// Preserve the tenth caller-owned output added in build 6712. Each ABI has its
// own entry hook; forwarding never reads a nonexistent legacy stack argument.
inline int forward_producer(Producer legacy, ProducerSeptember september, std::uintptr_t a,
                            void* object, void* mesh, void* opaque, void* params,
                            std::uint32_t mode, unsigned char* flag, std::uint32_t* outA,
                            std::uint32_t* outB, std::uint32_t* outC) {
    return september ? september(a, object, mesh, opaque, params, mode, flag, outA, outB, outC)
                     : legacy(a, object, mesh, opaque, params, mode, flag, outA, outB);
}
// A null profile keeps capture unavailable. The bridge selects one complete
// reviewed scene/renderer pair; ABI, producer and layout cannot be mixed.
void configure(std::uintptr_t scene, const SceneProfile* profile) noexcept;
void tick() noexcept;
// Optional read-only observer at the reviewed mesh producer. Installation is
// requested through the existing overlay worker, never from the view callback.
using PoseObserver = void (*)(std::uint32_t owner, std::uint32_t scene_frame) noexcept;
void set_pose_observer(PoseObserver) noexcept;
std::uint32_t pose_scene_frame() noexcept;
void present_boundary(ID3D11DeviceContext*) noexcept;
bool layout_hook_requested() noexcept;
bool install_layout_hook(ID3D11Device*) noexcept;
// Observe COM resource creation before client/module validation can stall.
// This never installs scene/draw hooks or admits a capture; configure() still
// requires the complete reviewed scene/renderer profile.
bool prime_creation_metadata() noexcept;
bool draw_hooks_requested() noexcept;
void draw_hooks_result(bool installed) noexcept;
// Invalidate submitted ownership when an unsupported writable Map succeeds.
void mapped_upload(ID3D11DeviceContext*, ID3D11Resource*, unsigned sub, unsigned map_type,
                   void* data) noexcept;
// Body hiding (attach POV): while a handle is set, player-owned draws whose
// owner matches are skipped. `allow_draw` is consulted before the original
// draw (false = skip); `producer_hook_requested`/`install_producer_hook` let
// the overlay provide the instance -> owner table when no capture is arming.
void set_hidden_handle(std::uint32_t handle, unsigned site = 0) noexcept;
void set_hidden_handles(std::uint32_t first, std::uint32_t second, unsigned site = 0) noexcept;
std::uint32_t hidden_handle() noexcept;
bool producer_hook_requested() noexcept;
bool install_producer_hook() noexcept;
bool allow_draw(ID3D11DeviceContext*, unsigned instances, unsigned first) noexcept;
void draw(ID3D11DeviceContext*, unsigned method, unsigned vertices, unsigned instances,
          unsigned first) noexcept;
void command(ID3D11DeviceContext*, ID3D11CommandList*, unsigned kind) noexcept;
using IndexedOriginal = void(__stdcall*)(ID3D11DeviceContext*, unsigned, unsigned, unsigned, int,
                                         unsigned);
void redirect_indexed(ID3D11DeviceContext*, unsigned, unsigned, unsigned, int, unsigned,
                      IndexedOriginal) noexcept;
using UpdateOriginal = void(__stdcall*)(ID3D11DeviceContext*, ID3D11Resource*, unsigned,
                                        const D3D11_BOX*, const void*, unsigned, unsigned);
void update(ID3D11DeviceContext*, ID3D11Resource*, unsigned, const D3D11_BOX*, const void*,
            unsigned, unsigned, UpdateOriginal) noexcept;
// Authored-path replay time in seconds, published by the bridge; negative when
// no path is playing. Used to pair player frames with the recorder's take.
void publish_replay_time(double seconds, bool completed = false) noexcept;
double current_replay_time() noexcept;
}
