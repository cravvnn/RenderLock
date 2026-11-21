#pragma once
#include "dolly_attach.hpp"
#include "dolly_visualization.hpp"
#include "dolly_hero_portrait.hpp"
#include <array>
#include <memory>
#include <vector>

namespace dolly {
constexpr std::size_t kPickerMaxBones = 4096;
struct PickerBone {
    std::uint32_t source_index = 0;
    char name[64]{};
};
struct PickerCatalog {
    std::uint32_t sequence = 0, handle = 0, entity = 0, total = 0;
    std::uint64_t model = 0;
    std::vector<PickerBone> bones;
    HeroPortrait portrait;
    // Resolver error for an empty catalog, so the picker can explain why no
    // joints were found instead of showing an empty list.
    char error[128]{};
};
struct PickerSample {
    // One bulk read of the reviewed pose buffer, indexed by SOURCE index.
    std::array<std::array<float, 8>, kPickerMaxBones> transforms{};
    std::uint32_t count = 0;
    std::array<double, 3> aim{};
    double facing_yaw = 0;
};
struct PickerFrame {
    std::shared_ptr<const PickerCatalog> catalog;
    PickerSample sample;
    CameraPose view{}, original{};
    double fov = 0;
    std::uint64_t stamp = 0;
    int selected = -1;
    bool active = false, ready = false, preview = false, finishing = false;
    char error[128]{};
};
// Optional result/status tail. All existing wire offsets stay unchanged.
constexpr std::size_t kPickerResultOffset = 2 * 1024 * 1024 + 22528;
#pragma pack(push, 1)
struct PickerResult {
    char magic[8];
    std::uint32_t sequence, abi, flags, request, handle, entity;
    std::uint64_t model;
    std::int32_t selected;
    std::uint32_t total;
    char name[64];
    double original[7];
};
#pragma pack(pop)
static_assert(sizeof(PickerResult) == 168, "Optional picker result layout");
static_assert(kPickerResultOffset + sizeof(PickerResult) <= 2 * 1024 * 1024 + 24576,
              "Picker result fits the existing mapping");

const char* picker_friendly_name(const char* name) noexcept;
bool picker_position(const PickerSample&, const PickerBone&, std::array<double, 3>&) noexcept;
bool picker_front_view(const PickerCatalog&, const PickerSample&, double fov, CameraPose& pose,
                       std::array<double, 3>* center = nullptr) noexcept;
bool picker_matches(const PickerBone&, const char* search, bool common_only) noexcept;
// Hysteresis for paused overview markers only; the actual bone pose remains live.
void picker_stabilize_marker(float raw_x, float raw_y, float scale, float& shown_x, float& shown_y,
                             bool& initialized) noexcept;
using PickerSampler = bool (*)(void*, PickerSample&, const char*&) noexcept;
// Worker owns catalog construction. Camera and Present use only try-locks and
// preallocated pose storage: neither waits on the other or allocates a catalog.
void picker_publish(std::shared_ptr<const PickerCatalog>) noexcept;
bool picker_camera(std::uint32_t request, std::uint32_t command, bool requested, bool permitted,
                   CameraPose& pose, double fov, const std::array<double, 6>& offsets,
                   PickerSampler sample, void* context, bool* attached_preview = nullptr,
                   bool auto_clearance = false, int paused_tick = 0,
                   bool rigid_orientation = false, bool lock_vertical_aim = false) noexcept;
bool picker_snapshot(PickerFrame&) noexcept;
bool picker_select(std::uint32_t request, int index) noexcept;
bool picker_preview(std::uint32_t request, bool preview) noexcept;
// Queue input for the next camera view; Present never advances marker projection early.
bool picker_orbit(std::uint32_t request, double yaw, double pitch) noexcept;
bool picker_finish(std::uint32_t request, int index) noexcept;
void picker_result(PickerResult&) noexcept;
void picker_abandon() noexcept;
std::uint64_t picker_now() noexcept;
} // namespace dolly
