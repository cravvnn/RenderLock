#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace dolly {
// Independent, optional status block. Existing control/editor ABIs stay intact.
constexpr std::size_t kCapabilitiesOffset = 2 * 1024 * 1024 + 23144;
constexpr std::uint32_t kCapabilitiesAbi = 1;
enum class Capability : std::uint32_t { CameraCore, CameraEffects, FollowAnchor, Count };
enum class CapabilityState : std::uint32_t { Unchecked, Ready, NotRequired, Unavailable };
enum class CapabilityReason : std::uint32_t {
    None,
    CoreAdmission,
    SignatureMismatch,
    HookInstallation,
    InitializationException
};
struct CapabilityResult {
    CapabilityState state = CapabilityState::Unchecked;
    CapabilityReason reason = CapabilityReason::None;
};
constexpr bool usable(CapabilityResult result) noexcept {
    return result.state == CapabilityState::Ready || result.state == CapabilityState::NotRequired;
}
struct CapabilitySnapshot {
    char magic[8] = {'D', 'L', 'Y', 'C', 'A', 'P', '0', '1'};
    std::uint32_t sequence = 0, abi = kCapabilitiesAbi, game_pid = 0;
    std::uint32_t count = static_cast<std::uint32_t>(Capability::Count);
    std::array<CapabilityResult, static_cast<std::size_t>(Capability::Count)> results{};

    CapabilityResult& at(Capability feature) noexcept {
        return results[static_cast<std::size_t>(feature)];
    }
    const CapabilityResult& at(Capability feature) const noexcept {
        return results[static_cast<std::size_t>(feature)];
    }
    bool camera_available() const noexcept {
        return at(Capability::CameraCore).state == CapabilityState::Ready &&
               usable(at(Capability::CameraEffects));
    }
    bool follow_available() const noexcept {
        return camera_available() && usable(at(Capability::FollowAnchor));
    }
};
static_assert(sizeof(CapabilityResult) == 8, "Fixed capability result wire layout");
static_assert(sizeof(CapabilitySnapshot) == 48, "Python capability wire layout");
static_assert(offsetof(CapabilitySnapshot, sequence) == 8, "Aligned sequence location");
static_assert(kCapabilitiesOffset + sizeof(CapabilitySnapshot) <= 2 * 1024 * 1024 + 23360,
              "Capability report must not overlap the existing camera-list block");
}
