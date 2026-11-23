#include "dolly_confetti.hpp"

#include <Windows.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <mutex>

namespace dolly::confetti {
namespace {

using Vec3 = Camera::Vec3;
using Angles = Camera::Angles;
constexpr float kPi = 3.14159265358979323846f;
// A real rewind is seconds; the published tick+fraction clock can wobble by
// about one replay tick (worst case a few tens of milliseconds). Keep the
// reset guard well above the wobble and well below a genuine seek back.
constexpr double kRewindTolerance = 0.5;
// Camera-following coverage and its density budget. The asset maps CP2 into a
// forward-biased local-space box (min = {2%, -100%, 5%}, max = {100%, +100%,
// 100%}), so depth also scales the FOV-limited lateral extent. Close coverage
// doubles both horizontal axes and the emission, keeping the same particle
// density: 120/s x 8 s = 960 live per effect, below the 1024-per-effect cap,
// 5,760 particles across the six colours. The above-250 regime already
// saturates its cap (~85/s effective with a 12 s lifetime), so its emission
// stays at 600/s total and the coverage grows 1.5x instead of 2x.
// Per-preset tuning now lives in kPresets below; the confetti row keeps these
// reviewed values exactly (720/600/720 close, 1200/900/600 far).
// Preset library. Ids are protocol-stable: append only, never reorder, because
// the editor sends the index. The tuning mirrors the original confetti model
// (emission per second, forward depth, lateral spread, lifetime, size hint and
// the downward origin shift of the close regime); snow entries start from the
// same model and are tuned live. Despawn-on-ground uses the contact table when
// a preset has one, and falls back to the persistent table otherwise.
struct Preset {
    const char* id;
    std::array<const char*, 6> persistent;
    std::array<const char*, 6> contact;
    std::size_t persistent_count;
    std::size_t contact_count;
    float close_depth, close_spread, close_emission, close_duration, close_size, close_drop;
    float far_depth, far_spread, far_emission, far_duration, far_size, far_drop;
};
constexpr std::array<Preset, kPresetCount> kPresets{{
    {"confetti",
     {"particles/deadlock_cine/confetti_red.vpcf", "particles/deadlock_cine/confetti_orange.vpcf",
      "particles/deadlock_cine/confetti_yellow.vpcf", "particles/deadlock_cine/confetti_green.vpcf",
      "particles/deadlock_cine/confetti_blue.vpcf", "particles/deadlock_cine/confetti_purple.vpcf"},
     {"particles/deadlock_cine/confetti_red_contact.vpcf",
      "particles/deadlock_cine/confetti_orange_contact.vpcf",
      "particles/deadlock_cine/confetti_yellow_contact.vpcf",
      "particles/deadlock_cine/confetti_green_contact.vpcf",
      "particles/deadlock_cine/confetti_blue_contact.vpcf",
      "particles/deadlock_cine/confetti_purple_contact.vpcf"},
     6,
     6,
     720.0f,
     600.0f,
     720.0f,
     8.0f,
     7.0f,
     0.4f,
     1200.0f,
     900.0f,
     600.0f,
     12.0f,
     5.0f,
     0.0f},
    {"snow_flakes",
     {"particles/deadlock_cine/snow_light.vpcf"},
     {"particles/deadlock_cine/snow_light_contact.vpcf"},
     1,
     1,
     720.0f,
     600.0f,
     480.0f,
     8.0f,
     6.0f,
     0.3f,
     1200.0f,
     900.0f,
     360.0f,
     12.0f,
     5.0f,
     0.0f},
    {"snow_long_fall",
     {"particles/deadlock_cine/snow_long_fall.vpcf"},
     {"particles/deadlock_cine/snow_long_fall_contact.vpcf"},
     1,
     1,
     720.0f,
     600.0f,
     260.0f,
     10.0f,
     6.0f,
     0.3f,
     1200.0f,
     900.0f,
     200.0f,
     14.0f,
     5.0f,
     0.0f},
    {"snow_heavy",
     {"particles/deadlock_cine/snow_heavy.vpcf"},
     {"particles/deadlock_cine/snow_heavy_contact.vpcf"},
     1,
     1,
     900.0f,
     750.0f,
     640.0f,
     10.0f,
     7.0f,
     0.3f,
     1400.0f,
     1000.0f,
     480.0f,
     14.0f,
     6.0f,
     0.0f},
    {"snow_machine",
     {"particles/deadlock_cine/snow_machine.vpcf"},
     {"particles/deadlock_cine/snow_machine_contact.vpcf"},
     1,
     1,
     600.0f,
     500.0f,
     360.0f,
     8.0f,
     5.0f,
     0.2f,
     1000.0f,
     800.0f,
     300.0f,
     10.0f,
     4.0f,
     0.0f},
    {"frozen_flakes",
     {"particles/deadlock_cine/frozen_flakes.vpcf"},
     {"particles/deadlock_cine/frozen_flakes_contact.vpcf"},
     1,
     1,
     600.0f,
     500.0f,
     260.0f,
     8.0f,
     5.0f,
     0.2f,
     1000.0f,
     800.0f,
     200.0f,
     10.0f,
     4.0f,
     0.0f},
    {"rain",
     {"particles/deadlock_cine/rain.vpcf"},
     {"particles/deadlock_cine/rain_contact.vpcf"},
     1,
     1,
     720.0f,
     600.0f,
     600.0f,
     8.0f,
     5.0f,
     0.35f,
     1200.0f,
     900.0f,
     500.0f,
     12.0f,
     4.0f,
     0.0f},
    {"frozen_classic",
     {"particles/deadlock_cine/frozen_classic.vpcf"},
     {"particles/deadlock_cine/frozen_classic_contact.vpcf"},
     1,
     1,
     600.0f,
     500.0f,
     260.0f,
     8.0f,
     5.0f,
     0.2f,
     1000.0f,
     800.0f,
     200.0f,
     10.0f,
     4.0f,
     0.0f},
}};

struct BuildOffsets {
    std::uintptr_t manager, create, control, transform, destroy, release;
    std::array<unsigned char, 8> manager_bytes;
    std::array<unsigned char, 16> release_bytes;
};
constexpr std::array<BuildOffsets, 43> kBuilds{{
    {0x1491620,
     0xb047f0,
     0xb3a4b0,
     0xb3a650,
     0xb054b0,
     0xb2baf0,
     {0x48, 0x8b, 0x05, 0xe9, 0x6b, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x23, 0x5b, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
    {0x148cec0,
     0xb04830,
     0xb3a4f0,
     0xb3a690,
     0xb054f0,
     0xb2bb30,
     {0x48, 0x8b, 0x05, 0x59, 0x62, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x23, 0x5b, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
    {0x1494580,
     0xb07340,
     0xb3d000,
     0xb3d1a0,
     0xb08000,
     0xb2e640,
     {0x48, 0x8b, 0x05, 0x09, 0x90, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x33, 0x5f, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
    {0x14945c0,
     0xb07400,
     0xb3d0c0,
     0xb3d260,
     0xb080c0,
     0xb2e700,
     {0x48, 0x8b, 0x05, 0x19, 0x8f, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xb3, 0x5e, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
    // Build 6712: reviewed complete API wrappers retain Dolly's argument ABI.
    // The game constructs its expanded create descriptor inside the wrapper.
    {0x1658dd0,
     0xbed040,
     0xc252e0,
     0xc25480,
     0xbed9a0,
     0xc16470,
     {0x48, 0x8b, 0x05, 0x41, 0xf8, 0xbc, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x53, 0x29, 0xa4, 0x00, 0x48, 0x85,
      0xc0}},
    // Version 6722: same wrapper ABI, reviewed relocated calls/globals.
    {0x1658e50,
     0xbed0b0,
     0xc25350,
     0xc254f0,
     0xbeda10,
     0xc164e0,
     {0x48, 0x8b, 0x05, 0xc1, 0xf7, 0xbc, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x63, 0x29, 0xa4, 0x00, 0x48, 0x85,
      0xc0}},
    // Version6723: reviewed identical particle wrapper ABI.
    {0x16590e0,
     0xbed1b0,
     0xc25450,
     0xc255f0,
     0xbedb10,
     0xc165e0,
     {0x48, 0x8b, 0x05, 0x41, 0xf5, 0xbc, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xf3, 0x2a, 0xa4, 0x00, 0x48, 0x85,
      0xc0}},
    {0x165c0c0,
     0xbf02a0,
     0xc28540,
     0xc286e0,
     0xbf0c00,
     0xc196d0,
     {0x48, 0x8b, 0x05, 0xf1, 0xf7, 0xbc, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xe3, 0x29, 0xa4, 0x00, 0x48, 0x85,
      0xc0}},
    // Build 6731: relocated particle wrappers only; normalized instructions
    // match the reviewed 6723/6730 versions and the shared ABI prefixes.
    {0x165d530,
     0xbf1320,
     0xc295c0,
     0xc29760,
     0xbf1c80,
     0xc1a750,
     {0x48, 0x8b, 0x05, 0xe1, 0x14, 0xbd, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xd3, 0x2d, 0xa4, 0x00, 0x48, 0x85,
      0xc0}},
    // Build 6739: relocated particle wrappers only; the reviewed signatures
    // match the shared ABI prefixes and the 6731 wrapper bodies.
    {0x1683130,
     0xbf3010,
     0xc2b2b0,
     0xc2b450,
     0xbf3970,
     0xc1c440,
     {0x48, 0x8b, 0x05, 0xc1, 0x73, 0xbe, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xe3, 0x6c, 0xa6, 0x00, 0x48, 0x85,
      0xc0}},
    // Build 6742: the October 2 hotfix relocates the manager wrapper only; the
    // reviewed signatures match the shared ABI prefixes and 6739 bodies.
    {0x1683190,
     0xbf3010,
     0xc2b2b0,
     0xc2b450,
     0xbf3970,
     0xc1c440,
     {0x48, 0x8b, 0x05, 0x41, 0x73, 0xbe, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x43, 0x6d, 0xa6, 0x00, 0x48, 0x85,
      0xc0}},
    // Build 6745: manager wrapper relocates again; the release thunk target
    // shifts too. The reviewed signatures match the shared ABI prefixes.
    {0x16831b0,
     0xbf3010,
     0xc2b2b0,
     0xc2b450,
     0xbf3970,
     0xc1c440,
     {0x48, 0x8b, 0x05, 0x41, 0x73, 0xbe, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x63, 0x6d, 0xa6, 0x00, 0x48, 0x85,
      0xc0}},
    // Build 6774: the October 9 update relocates the whole particle wrapper
    // cluster. Found offline with analysis/object-picker-20261010/ghidra/
    // find_confetti_offsets.py (create/destroy +0x9070, control/transform/
    // release +0x90a0, manager +0x6c5f0 from the 6745 row); the reviewed
    // signatures match the shared ABI prefixes and the 6745 wrapper bodies.
    {0x16ef7a0,
      0xbfc080,
      0xc34350,
      0xc344f0,
      0xbfc9e0,
      0xc254e0,
      {0x48, 0x8b, 0x05, 0x11, 0x83, 0xc1, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xb3, 0xa2, 0xac, 0x00, 0x48, 0x85,
       0xc0}},
    {0x1724460,
      0xbfe1b0,
      0xc36480,
      0xc36620,
      0xbfeb10,
      0xc27610,
      {0x48, 0x8b, 0x05, 0x91, 0xc4, 0xc2, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x43, 0xce, 0xaf, 0x00, 0x48, 0x85, 0xc0}},
    {0x1724440,
      0xbfe1c0,
      0xc36490,
      0xc36630,
      0xbfeb20,
      0xc27620,
      {0x48, 0x8b, 0x05, 0x81, 0xc4, 0xc2, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x13, 0xce, 0xaf, 0x00, 0x48, 0x85, 0xc0}},
    {0x1724c00,
      0xbfe1e0,
      0xc364b0,
      0xc36650,
      0xbfeb40,
      0xc27640,
      {0x48, 0x8b, 0x05, 0x21, 0xcd, 0xc2, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xb3, 0xd5, 0xaf, 0x00, 0x48, 0x85, 0xc0}},
    {0x178abf0,
      0xc07090,
      0xc3f360,
      0xc3f500,
      0xc079f0,
      0xc304f0,
      {0x48, 0x8b, 0x05, 0x51, 0x60, 0xc6, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xf3, 0xa6, 0xb5, 0x00, 0x48, 0x85, 0xc0}},
    // Build 6801: complete wrappers, callees and named manager lifecycle reviewed.
    {0x17a9d50,
     0xc07800,
     0xc3fad0,
     0xc3fc70,
     0xc08160,
     0xc30c60,
     {0x48, 0x8b, 0x05, 0x91, 0xf0, 0xc7, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xe3, 0x90, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    // Build6806: complete relocated wrappers/callees and named manager lifecycle.
    {0x17a9cd0, 0xc07780, 0xc3fa50, 0xc3fbf0, 0xc080e0, 0xc30be0,
     {0x48, 0x8b, 0x05, 0x11, 0xf1, 0xc7, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xe3, 0x90, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    // Build6807: complete relocated wrappers/callees and named manager lifecycle.
    {0x17a9ce0, 0xc07780, 0xc3fa50, 0xc3fbf0, 0xc080e0, 0xc30be0,
     {0x48, 0x8b, 0x05, 0xc1, 0xf0, 0xc7, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xf3, 0x90, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    // Build6810: complete relocated wrappers/callees and named manager lifecycle.
    {0x17a9cc0,
      0xc07780,
      0xc3fa50,
      0xc3fbf0,
      0xc080e0,
      0xc30be0,
      {0x48, 0x8b, 0x05, 0x81, 0xf0, 0xc7, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xd3, 0x90, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    // Build6812: complete relocated wrappers/callees and named manager lifecycle.
    {0x17ab830,
      0xc07780,
      0xc3fa50,
      0xc3fbf0,
      0xc080e0,
      0xc30be0,
      {0x48, 0x8b, 0x05, 0x21, 0xe5, 0xc7, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x43, 0xac, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    // Build6814: complete relocated wrappers/callees and named manager lifecycle.
    {0x17ab8d0,
      0xc07820,
      0xc3faf0,
      0xc3fc90,
      0xc08180,
      0xc30c80,
      {0x48, 0x8b, 0x05, 0xa1, 0xe4, 0xc7, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x43, 0xac, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    // Build6817: manager wrapper relocated +0x30; remaining wrappers unchanged.
    {0x17ab900,
      0xc07820,
      0xc3faf0,
      0xc3fc90,
      0xc08180,
      0xc30c80,
      {0x48, 0x8b, 0x05, 0x81, 0xe4, 0xc7, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x73, 0xac, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    // Build6819: manager wrapper relocated +0xA0; remaining wrappers unchanged.
    {0x17ab9a0,
      0xc07820,
      0xc3faf0,
      0xc3fc90,
      0xc08180,
      0xc30c80,
      {0x48, 0x8b, 0x05, 0xf1, 0xe3, 0xc7, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x13, 0xad, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    {0x17ab9b0,
      0xc077e0,
      0xc3fab0,
      0xc3fc50,
      0xc08140,
      0xc30c40,
      {0x48, 0x8b, 0x05, 0x81, 0xe3, 0xc7, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x63, 0xad, 0xb7, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cf520,
      0xc1c560,
      0xc54830,
      0xc549d0,
      0xc1cec0,
      0xc459c0,
      {0x48, 0x8b, 0x05, 0x01, 0x3d, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x53, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cf920,
      0xc1c970,
      0xc54c40,
      0xc54de0,
      0xc1d2d0,
      0xc45dd0,
      {0x48, 0x8b, 0x05, 0x01, 0x5a, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x43, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cfa90,
      0xc1cae0,
      0xc54db0,
      0xc54f50,
      0xc1d440,
      0xc45f40,
      {0x48, 0x8b, 0x05, 0x91, 0x58, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x43, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cfab0,
      0xc1cb00,
      0xc54dd0,
      0xc54f70,
      0xc1d460,
      0xc45f60,
      {0x48, 0x8b, 0x05, 0x71, 0x58, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x43, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cf980,
      0xc1c9c0,
      0xc54c90,
      0xc54e30,
      0xc1d320,
      0xc45e20,
      {0x48, 0x8b, 0x05, 0x61, 0x5a, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x53, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cf9f0,
      0xc1ca30,
      0xc54d00,
      0xc54ea0,
      0xc1d390,
      0xc45e90,
      {0x48, 0x8b, 0x05, 0xc1, 0x59, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x53, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cfad0,
      0xc1cae0,
      0xc54db0,
      0xc54f50,
      0xc1d440,
      0xc45f40,
      {0x48, 0x8b, 0x05, 0x51, 0x59, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x83, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17cfb30,
      0xc1cac0,
      0xc54d90,
      0xc54f30,
      0xc1d420,
      0xc45f20,
      {0x48, 0x8b, 0x05, 0x01, 0x59, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x03, 0x9c, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    // Build6844: six independently reviewed particle wrappers.
    {0x17cfac0, 0xc1cac0, 0xc54d90, 0xc54f30, 0xc1d420, 0xc45f20,
      {0x48, 0x8b, 0x05, 0x31, 0x69, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x93, 0x9b, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    // Build6848: six independently reviewed particle wrappers.
    {0x17cfde0, 0xc1cad0, 0xc54da0, 0xc54f40, 0xc1d430, 0xc45f30,
        {0x48, 0x8b, 0x05, 0x51, 0x66, 0xc9, 0x01, 0xc3},
        {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xa3, 0x9e, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17d02b0,
      0xc1cfa0,
      0xc55270,
      0xc55410,
      0xc1d900,
      0xc46400,
      {0x48, 0x8b, 0x05, 0xe1, 0x62, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xa3, 0x9e, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17d6100,
      0xc22790,
      0xc5aa60,
      0xc5ac00,
      0xc230f0,
      0xc4bbf0,
      {0x48, 0x8b, 0x05, 0x11, 0xd8, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x03, 0xa5, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17d6180,
      0xc22790,
      0xc5aa60,
      0xc5ac00,
      0xc230f0,
      0xc4bbf0,
      {0x48, 0x8b, 0x05, 0x91, 0xd7, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x83, 0xa5, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17d64e0,
      0xc22790,
      0xc5aa60,
      0xc5ac00,
      0xc230f0,
      0xc4bbf0,
      {0x48, 0x8b, 0x05, 0x61, 0xd4, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xe3, 0xa8, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17d63a0,
      0xc226d0,
      0xc5a990,
      0xc5ab30,
      0xc23030,
      0xc4bb20,
      {0x48, 0x8b, 0x05, 0xc1, 0x35, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x73, 0xa8, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17d6420,
      0xc226b0,
      0xc5a970,
      0xc5ab10,
      0xc23010,
      0xc4bb00,
      {0x48, 0x8b, 0x05, 0x31, 0x25, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x13, 0xa9, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
    {0x17d6410,
      0xc226a0,
      0xc5a960,
      0xc5ab00,
      0xc23000,
      0xc4baf0,
      {0x48, 0x8b, 0x05, 0x51, 0x25, 0xc9, 0x01, 0xc3},
      {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x13, 0xa9, 0xb8, 0x00, 0x48, 0x85, 0xc0}},
}};
constexpr std::array<unsigned char, 16> kCreateBytes{
    0x40, 0x57, 0x41, 0x56, 0x41, 0x57, 0x48, 0x81, 0xec, 0x90, 0x00, 0x00, 0x00, 0x45, 0x8b, 0xf1};
constexpr std::array<unsigned char, 16> kControlBytes{
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x57, 0x48, 0x83, 0xec, 0x30, 0x49};
constexpr std::array<unsigned char, 16> kTransformBytes{
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x57, 0x48, 0x83, 0xec, 0x50, 0x49};
constexpr std::array<unsigned char, 16> kDestroyBytes{
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x20, 0x41, 0x0f, 0xb6, 0xf8, 0x8b, 0xda};

enum class State {
    unavailable,
    ready,
    configured,
    running,
    waiting,
    seeking,
    manager_unavailable,
    create_failed
};
// Reason a live effect was released; published in diagnostics order.
enum class StopReason {
    disabled,
    reconfigured,
    seek,
    backward,
    invalid_config,
    shutdown,
    create_failed
};
struct StopCounts {
    std::uint32_t disabled{}, reconfigured{}, seek{}, backward{}, invalid_config{}, shutdown{},
        create_failed{};
};
struct Counts {
    StopCounts stops;
    std::uint32_t starts{}, start_failures{}, frames{}, running_frames{}, resets{}, state_changes{};
    double max_backward_delta{};
};
std::mutex mutex;
std::uintptr_t client{}, manager_offset{}, create_offset{}, control_offset{};
std::uintptr_t transform_offset{}, destroy_offset{}, release_offset{};
std::array<int, 6> particles{-1, -1, -1, -1, -1, -1};
bool active{}, despawn{}, have_time{};
float height{250.0f};
int preset_index{0};
float intensity{1.0f};
double last_time{};
State state{State::unavailable};
Counts counts;

std::uint32_t& stopCount(StopReason reason) {
    switch (reason) {
    case StopReason::disabled:
        return counts.stops.disabled;
    case StopReason::reconfigured:
        return counts.stops.reconfigured;
    case StopReason::seek:
        return counts.stops.seek;
    case StopReason::backward:
        return counts.stops.backward;
    case StopReason::invalid_config:
        return counts.stops.invalid_config;
    case StopReason::shutdown:
        return counts.stops.shutdown;
    case StopReason::create_failed:
        return counts.stops.create_failed;
    }
    return counts.stops.disabled;
}
void setStateLocked(State next) {
    if (state == next)
        return;
    state = next;
    ++counts.state_changes;
}

template <std::size_t N>
bool matches(std::uintptr_t address, const std::array<unsigned char, N>& expected) {
    std::array<unsigned char, N> actual{};
    SIZE_T read{};
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(address),
                             actual.data(), N, &read) &&
           read == N && actual == expected;
}
bool matches(const BuildOffsets& build) {
    return matches(client + build.manager, build.manager_bytes) &&
           matches(client + build.create, kCreateBytes) &&
           matches(client + build.control, kControlBytes) &&
           matches(client + build.transform, kTransformBytes) &&
           matches(client + build.destroy, kDestroyBytes) &&
           matches(client + build.release, build.release_bytes);
}
void* manager() {
    using Fn = void*(__fastcall*)();
    return reinterpret_cast<Fn>(client + manager_offset)();
}
int create(const char* effect) {
    using Fn = int*(__fastcall*)(void*, int*, const char*, int, void*);
    int particle = -1;
    reinterpret_cast<Fn>(client + create_offset)(nullptr, &particle, effect, 8, nullptr);
    return particle;
}
void setControl(int particle, int control, const Vec3& value) {
    using Fn = void(__fastcall*)(void*, int, int, const Vec3*);
    reinterpret_cast<Fn>(client + control_offset)(nullptr, particle, control, &value);
}
void setTransform(int particle, const Vec3& origin, const Angles& angles) {
    using Fn = void(__fastcall*)(void*, int, int, const Vec3*, const Angles*);
    reinterpret_cast<Fn>(client + transform_offset)(nullptr, particle, 0, &origin, &angles);
}
bool stopLocked(StopReason reason) {
    using Destroy = void(__fastcall*)(void*, int, bool);
    using Release = void(__fastcall*)(void*, int);
    bool released = false;
    for (int& particle : particles) {
        if (particle < 0)
            continue;
        reinterpret_cast<Destroy>(client + destroy_offset)(nullptr, particle, true);
        reinterpret_cast<Release>(client + release_offset)(nullptr, particle);
        particle = -1;
        released = true;
    }
    if (released) {
        ++counts.resets;
        ++stopCount(reason);
    }
    return released;
}
float seededUnit(std::uint32_t& value) {
    value = value * 1664525u + 1013904223u;
    return static_cast<float>(value >> 8) / 16777216.0f;
}
void updateVolumeLocked(const Camera& camera) {
    const Preset& preset = kPresets[preset_index];
    const bool close = height <= 250.0f;
    const float duration = close ? preset.close_duration : preset.far_duration;
    const float depth = close ? preset.close_depth : preset.far_depth;
    const float spread = close ? preset.close_spread : preset.far_spread;
    constexpr float overscan = 64.0f;
    const float half_fov = std::clamp(camera.fov, 1.0f, 120.0f) * kPi / 360.0f;
    const Vec3 extent{depth + overscan, std::max(spread, depth * std::tan(half_fov)) + overscan,
                      height};
    const Angles orientation{0.0f, camera.angles.yaw, 0.0f};
    std::uint32_t random = 42u ^ 0x9e3779b9u;
    const float forward_phase = (seededUnit(random) - 0.5f) * overscan;
    const float right_phase = (seededUnit(random) - 0.5f) * overscan;
    const float yaw = camera.angles.yaw * kPi / 180.0f;
    const Vec3 forward{std::cos(yaw), std::sin(yaw), 0.0f};
    const Vec3 right{-std::sin(yaw), std::cos(yaw), 0.0f};
    Vec3 origin{camera.origin.x + forward.x * forward_phase + right.x * right_phase,
                camera.origin.y + forward.y * forward_phase + right.y * right_phase,
                camera.origin.z};
    if (close)
        origin.z -= height * preset.close_drop;
    // Packaged effects spawn in a forward-biased local box; pull the origin
    // back by half the depth so the volume straddles the camera instead of
    // sitting in front of it (a 180-degree turn still has particles in frame).
    origin.x -= forward.x * (depth + overscan) * 0.5f;
    origin.y -= forward.y * (depth + overscan) * 0.5f;
    for (int particle : particles) {
        if (particle < 0)
            continue;
        setTransform(particle, origin, orientation);
        setControl(particle, 2, extent);
        setControl(particle, 3, {duration, 0.0f, 0.0f});
    }
}
bool startLocked() {
    const Preset& preset = kPresets[preset_index];
    const bool close = height <= 250.0f;
    const bool contact = despawn && preset.contact_count > 0;
    const auto& effects = contact ? preset.contact : preset.persistent;
    const std::size_t count = contact ? preset.contact_count : preset.persistent_count;
    const float emission = (close ? preset.close_emission : preset.far_emission) * intensity;
    const Vec3 parameters{0.0f, emission / static_cast<float>(count),
                          close ? preset.close_size : preset.far_size};
    for (std::size_t index = 0; index < count; ++index) {
        particles[index] = create(effects[index]);
        if (particles[index] < 0) {
            stopLocked(StopReason::create_failed);
            ++counts.start_failures;
            return false;
        }
        setControl(particles[index], 1, parameters);
    }
    ++counts.starts;
    return true;
}
const char* stateText(State value) {
    switch (value) {
    case State::ready:
        return "native Source 2 confetti ready";
    case State::configured:
        return "native Source 2 confetti configured";
    case State::running:
        return "native Source 2 confetti active with scene occlusion";
    case State::waiting:
        return "native Source 2 confetti waiting for replay view";
    case State::seeking:
        return "native Source 2 confetti reset for replay seek";
    case State::manager_unavailable:
        return "Source 2 particle manager unavailable";
    case State::create_failed:
        return "Source 2 rejected the packaged confetti effect";
    default:
        return "native Source 2 confetti unavailable for this game build";
    }
}
} // namespace

bool initialize(std::uintptr_t client_base) noexcept {
    std::scoped_lock lock(mutex);
    client = client_base;
    for (const auto& build : kBuilds) {
        if (!client || !matches(build))
            continue;
        manager_offset = build.manager;
        create_offset = build.create;
        control_offset = build.control;
        transform_offset = build.transform;
        destroy_offset = build.destroy;
        release_offset = build.release;
        setStateLocked(State::ready);
        return true;
    }
    client = 0;
    setStateLocked(State::unavailable);
    return false;
}
void disable() noexcept {
    std::scoped_lock lock(mutex);
    if (client)
        stopLocked(StopReason::shutdown);
    active = have_time = false;
    setStateLocked(client ? State::ready : State::unavailable);
}
void on_frame(double replay_time, bool replay_active, bool seeking, const Camera* camera,
              bool requested, float requested_height, bool requested_despawn, int requested_preset,
              float requested_intensity) noexcept {
    std::scoped_lock lock(mutex);
    ++counts.frames;
    if (!client) {
        setStateLocked(State::unavailable);
        return;
    }
    if (!requested) {
        if (active)
            stopLocked(StopReason::disabled);
        active = have_time = false;
        setStateLocked(State::ready);
        return;
    }
    if (!std::isfinite(requested_height) || requested_height < 100.0f ||
        requested_height > 1500.0f || requested_preset < 0 ||
        requested_preset >= static_cast<int>(kPresets.size()) ||
        !std::isfinite(requested_intensity) || requested_intensity < 0.1f ||
        requested_intensity > 4.0f) {
        stopLocked(StopReason::invalid_config);
        active = have_time = false;
        setStateLocked(State::unavailable);
        return;
    }
    if (!active || height != requested_height || despawn != requested_despawn ||
        preset_index != requested_preset || intensity != requested_intensity) {
        stopLocked(StopReason::reconfigured);
        active = true;
        height = requested_height;
        despawn = requested_despawn;
        preset_index = requested_preset;
        intensity = requested_intensity;
        have_time = false;
        setStateLocked(State::configured);
    }
    const bool valid_time = std::isfinite(replay_time) && replay_time >= 0.0;
    if (have_time && valid_time) {
        // The published replay clock is rebuilt from the current tick and the
        // renderer's interpolation fraction, so a fraction wobble can step the
        // value back by up to a tick. Only a real rewind may reset the effects;
        // every observed backward delta is kept for diagnostics.
        const double backward = last_time - replay_time;
        if (backward > counts.max_backward_delta)
            counts.max_backward_delta = backward;
        if (backward > kRewindTolerance)
            stopLocked(StopReason::backward);
    }
    if (valid_time) {
        last_time = replay_time;
        have_time = true;
    }
    if (seeking) {
        stopLocked(StopReason::seek);
        setStateLocked(State::seeking);
        return;
    }
    if (!valid_time || !replay_active || !camera) {
        setStateLocked(State::waiting);
        return;
    }
    if (!manager()) {
        setStateLocked(State::manager_unavailable);
        return;
    }
    if (particles[0] < 0 && !startLocked()) {
        setStateLocked(State::create_failed);
        return;
    }
    updateVolumeLocked(*camera);
    ++counts.running_frames;
    setStateLocked(State::running);
}
const char* status() noexcept {
    std::scoped_lock lock(mutex);
    return stateText(state);
}
Diagnostics diagnostics() noexcept {
    std::scoped_lock lock(mutex);
    Diagnostics result;
    result.state = static_cast<std::uint32_t>(state);
    result.handles = static_cast<std::uint32_t>(std::count_if(
        particles.begin(), particles.end(), [](int particle) { return particle >= 0; }));
    result.starts = counts.starts;
    result.start_failures = counts.start_failures;
    result.frames = counts.frames;
    result.running_frames = counts.running_frames;
    result.resets = counts.resets;
    result.stop_disabled = counts.stops.disabled;
    result.stop_reconfigured = counts.stops.reconfigured;
    result.stop_seek = counts.stops.seek;
    result.stop_backward = counts.stops.backward;
    result.stop_invalid = counts.stops.invalid_config;
    result.stop_shutdown = counts.stops.shutdown;
    result.stop_create_failed = counts.stops.create_failed;
    result.state_changes = counts.state_changes;
    result.max_backward_delta = counts.max_backward_delta;
    return result;
}

} // namespace dolly::confetti
