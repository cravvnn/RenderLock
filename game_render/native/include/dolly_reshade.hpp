#pragma once
#include <cstdint>

#ifdef _WIN32
struct IDXGISwapChain;
struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11Texture2D;

namespace dolly {
// This adapter owns a manually-created ReShade runtime. It does not install a
// second graphics hook or change the game's camera callback.
struct ReShadeStatus {
    bool configured = false, loaded = false, available = false;
    bool overlay_open = false, overlay_pending = false, enabled = false;
    bool loading = false, failed = false;
    std::uint64_t rendered_frames = 0, missing_clean_frames = 0;
    char message[192]{};
};

// Worker thread only; launch must already set RESHADE_DISABLE_GRAPHICS_HOOK=1.
// Paths must be absolute. The configuration belongs to Dolly, not the game's
// normal ReShade installation. DLLs remain loaded until the game process exits.
// Repeating the same initialization is safe; changing DLL/config needs a restart.
bool reshade_initialize(const wchar_t* library_path, const wchar_t* config_path) noexcept;
// Starts the potentially slow DLL load on a dedicated thread. True means the
// request was accepted, not that the runtime is ready. See loading/failed status.
bool reshade_initialize_async(const wchar_t* library_path, const wchar_t* config_path) noexcept;
void reshade_set_enabled(bool enabled) noexcept;
bool reshade_enabled() noexcept;
ReShadeStatus reshade_status() noexcept;

// These functions only queue requests/read atomics and are safe on input/worker
// threads. Pending state ends when the render thread confirms the menu request.
bool reshade_request_overlay(bool open) noexcept;
bool reshade_overlay_open() noexcept;
bool reshade_overlay_pending() noexcept;
bool reshade_available() noexcept;

// ReShade can consume Win32 messages before Dolly's window procedure sees
// them. Forward its observed down/up edges to the editor worker while its menu
// owns input, so the configured return keys and F7 remain reachable.
struct ReShadeInputEvent {
    std::uint16_t vk = 0, modifiers = 0;
    bool down = false;
};
bool reshade_pop_input(ReShadeInputEvent& event) noexcept;

using ReShadeCleanFrame = void (*)(IDXGISwapChain*, ID3D11Device*, ID3D11DeviceContext*, void*);

// Call once per real game Present, before Dolly draws its editor/guides, under
// the overlay's render lock. The callback runs after effects and before ANY
// ReShade UI, including splash/FPS. Returns true if ReShade handled this frame.
// If true but no clean callback was delivered, do not capture a fallback image:
// it may contain ReShade UI. The missing frame is reported in status instead.
// If false, the caller may capture the ordinary backbuffer directly.
bool reshade_render(IDXGISwapChain* chain, ID3D11Device* device, ID3D11DeviceContext* context,
                    ReShadeCleanFrame clean_frame = nullptr, void* user_data = nullptr) noexcept;

// Render lock must be held. Publishes the verified game scene depth captured
// for this Present so depth-semantic effects sample it instead of ReShade's
// absent automatic depth source. Passing nullptr clears the binding. The
// texture is reference-counted until it is replaced or the device is released.
void reshade_set_scene_depth(ID3D11Texture2D* texture) noexcept;

// TEMPORARY depth-feed diagnostic: appends a deduplicated state line to
// %TEMP%\dolly_depth_debug.log. Remove with the depth investigation.
void reshade_depth_debug(const char* text) noexcept;

// Render lock must be held, all calls to reshade_render stopped. Call before
// ResizeBuffers, device removal, or overlay device teardown. This releases all
// runtime references to the backbuffer; a later Present can create a new runtime.
void reshade_release_device() noexcept;
}
#endif
