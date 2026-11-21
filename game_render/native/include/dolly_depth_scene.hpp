#pragma once
#include "dolly_depth_readback.hpp"
#include <memory>

struct ID3D11DepthStencilView;
struct ID3D11CommandList;

namespace dolly::depth {
struct SceneResources;
enum class SceneResult {
    ready,
    missing,
    cleared,
    ambiguous,
    incompatible_view,
    untracked_commands,
    failed
};
struct SceneFrame {
    SceneResult result = SceneResult::missing;
    std::shared_ptr<SceneResources> resources;
    // Size of the verified scene target. It can be below the recording size
    // when the game renders the scene at an internal resolution (upscaling or
    // resolution scaling) and is what the paired depth data is captured at.
    std::uint32_t width = 0, height = 0;
    ID3D11Texture2D* texture() const noexcept;
    std::size_t calibration(ProjectionBuffer* output, std::size_t capacity) const noexcept;
};
// Construct only for a verified Dolly game device/profile. The selector uses
// the reviewed scene texture name family, a full-target viewport and reversed
// depth writes. The target may be smaller than the recording (the game can
// render the scene at an internal resolution with upscaling enabled); the
// largest reviewed target in a frame wins, and the verified size is reported
// through SceneFrame. Constant-buffer contents are separately validated after
// GPU readback.
class SceneTracker {
public:
    SceneTracker(ID3D11Device* device, std::uint32_t width, std::uint32_t height);
    ~SceneTracker();
    SceneTracker(const SceneTracker&) = delete;
    SceneTracker& operator=(const SceneTracker&) = delete;
    void draw(ID3D11DeviceContext* context) noexcept;
    void clear(ID3D11DeviceContext* context, ID3D11DepthStencilView* depth,
               unsigned flags) noexcept;
    void finish(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept;
    void execute(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept;
    SceneFrame consume(ID3D11DeviceContext* immediate) noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
// Hook code stays resident. Null disables observation without changing the
// game's graphics state. Call install only after the existing native gates.
bool install_scene_hooks(ID3D11Device* device, ID3D11DeviceContext* immediate) noexcept;
void set_scene_tracker(std::shared_ptr<SceneTracker> tracker) noexcept;
// Matte pass support: while enabled every ClearRenderTargetView is forced to
// white so the layer take records against a white background. The black/white
// pair lets the desktop derive a real alpha channel. Cleared for normal frames.
void set_white_clear(bool enabled) noexcept;

// Compact, single-line diagnostic for the last observation: draws seen, how
// many matched the reviewed scene target, hook installation state and the most
// recent rejected texture name. Diagnostic only; never changes behavior.
const char* scene_diagnostic() noexcept;
void scene_note_hooks(bool installed) noexcept;

// Whether a texture debug name belongs to the reviewed full-resolution scene
// target family: scratchrendertarget_<id>_<w>x<h>_<a>_<b>.vtex. The numeric ID
// and trailing pair vary per session; the family and resolution identify it.
bool scene_target_name(const char* name, std::uint32_t width, std::uint32_t height) noexcept;
}
