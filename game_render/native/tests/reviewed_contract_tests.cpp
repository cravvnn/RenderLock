#include "dolly_runtime_generated.hpp"
#include "dolly_minimap_guard.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
namespace {
#include "dolly_follow_anchor_generated.hpp"
}

// Executed by the real reviewed-contract native target.
static void minimap_guard_cases() {
    using namespace dolly;
    std::array<unsigned char, 0x100> icon{};
    const std::uintptr_t expected = 0x123456;
    std::uintptr_t table = expected;
    bool readable = true, playing = true;
    unsigned replay_reads = 0;
    const auto self = reinterpret_cast<std::uintptr_t>(&table);
    const auto item = reinterpret_cast<std::uintptr_t>(icon.data());
    const MinimapIconLayout layout{expected, 4, 128};
    const auto read = [&](std::uintptr_t address, auto& value) noexcept {
        if (!readable)
            return false;
        std::memcpy(&value, reinterpret_cast<const void*>(address), sizeof(value));
        return true;
    };
    const auto replay = [&]() noexcept {
        ++replay_reads;
        return playing;
    };
    const auto skip = [&](bool caller = true) {
        return incomplete_minimap_icon(caller, layout, self, item, read, replay);
    };
    for (std::uint32_t type : {1u, 45u, 88u, 92u, 121u}) {
        std::memcpy(icon.data() + 4, &type, sizeof(type));
        std::uintptr_t range = 0;
        std::memcpy(icon.data() + 128, &range, sizeof(range));
        const auto before = icon;
        const bool affected = type == 45 || type == 88 || type == 92;
        replay_reads = 0;
        assert(skip() == affected);
        assert(replay_reads == unsigned(affected));
        assert(icon == before); // The guard never edits game/HUD objects.
        assert(!skip(false));
        playing = false;
        assert(!skip());
        playing = true;
        table = expected + 8;
        assert(!skip());
        table = expected;
        readable = false;
        assert(!skip());
        readable = true;
        range = 0xabcdef;
        std::memcpy(icon.data() + 128, &range, sizeof(range));
        replay_reads = 0;
        assert(!skip());
        assert(replay_reads == 0);
    }
    assert(!incomplete_minimap_icon(true, layout, 0, item, read, replay));
    assert(!incomplete_minimap_icon(true, layout, self, 0, read, replay));
}
int main() {
    minimap_guard_cases();
    using namespace dolly::reviewed;
    // Old correction profiles must never acquire the newer pawn member simply
    // because the current Python observer monitor was updated.
    static_assert(FollowTarget::OBSERVER_SERVICES_OFFSET == 0xe98);
    static_assert(sizeof(kFollowAnchorProfiles) / sizeof(*kFollowAnchorProfiles) == 31);
    unsigned historical = 0, reviewed_current = 0;
    for (const auto& profile : kFollowAnchorProfiles) {
        // Keep this reviewed correction independent of future camera-only reviews.
        if (!std::strcmp(profile.hash,"822c9422fde4d6b5becd0b94e21a45038d84405da659f82120c4b2835ae93c31")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x27a42f0);
        } else if (!std::strcmp(profile.hash,"4b24981cef32b0ef862aff29f77197ac7857b4dbe7bbfb839dee33b45a655375")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x27a42f0);
        } else if (!std::strcmp(profile.hash,"d0341ce77398614af905ef8669d328fd4d45958282acef66789a250492e466b5")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x27a5350);
        } else if (!std::strcmp(profile.hash,"77fb13f576d3ecabf4688d51a81b842c73c3b62131392da0b09a6ea04d1f0203") ||
            !std::strcmp(profile.hash,"21cd561a8818522756ef377ca67584bf8573c2cc63f348134703513c1dad443f") ||
            !std::strcmp(profile.hash,"2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x27a5340);
        } else if (!std::strcmp(profile.hash,"fa6a50168ac34e282ddb4e4b5e1a0fe0c67983b3779406cdf1a856008142210c")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x27a5340);
        } else if (!std::strcmp(profile.hash,"6a7c6d40ed70011884054405846e7a5259fff51578714ffa864cbe80ab527627")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279c300);
        } else if (!std::strcmp(profile.hash,"c8f6ba9a6d7ca6e53d589b4c2bfb2568eef908b33b4541a20c4736d6a477c0cb") ||
            !std::strcmp(profile.hash,"85fcdc6df9c944186417e75bb48af9be6466085055d793e312b1f15ac825bcae")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279c2e0);
        } else if (!std::strcmp(profile.hash,"802d2b73124af86936875b0ccdf4862d95281490faff5ff0b03deea02cf22b45")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279c320);
        } else if (!std::strcmp(profile.hash,
                         "a113c2562f521b230cb8e2a34adfb1ad5ae641603dbcfc7b64112fbb48fcd72a")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b320);
        } else if (!std::strcmp(profile.hash,
                         "929a0455e510ad8bb76c26737a9c84a58ed496267b89b243f94b0699bb10855a")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b2e0);
        } else if (!std::strcmp(profile.hash,
                         "3925911036c0126509bd58903a746e0cc36d368aeb25ec3044363edb48eb3f9d") ||
            !std::strcmp(profile.hash,"5cf74c6aebf435d3566a1402251ee0e7e6e67b57cc22c62bea31c93e61061625")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b2e0);
        } else if (!std::strcmp(profile.hash,
                         "9fd12e81f4587f0f42a99cb8668eafb3fa25f15dadfd49d7d502003aa3d01160")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "459e8a0f4ee93a8c30324c8229b8846b39b3e6647afd60c660e0743e23cc4773")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "27a04887068b431f57e93421beeb47b1bcf80d50817bbb226b72c12edcfecf49")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "87b822441e3f3edadf096f2f46dd485778ab8c7b7678d814d0d4e67332b37c67")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "576b3c06dec3cab3675521ffcc8931bf5f140145ccf848427957a0a13ef99b16")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279a180);
        } else if (!std::strcmp(profile.hash,
                         "8da25ddcd11c1146eeb9770bca24fb25c594c7abdb28947035a44ec8f2945843")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x276d3d8);
        } else if (!std::strcmp(profile.hash,
                         "d61348c564ce931e969996af18dbdec8544643810f28f3d330fb94b33231c460") ||
            !std::strcmp(profile.hash,
                         "8dff04c139d20b78d8d2c7a3039b691cfc221b8460438ec6fc6107c315741a5c") ||
            !std::strcmp(profile.hash,
                         "a7d0d9e88d11f09a36294569662718394cfa36d99286774ecf8c9fd6ea4f589e") ||
            !std::strcmp(profile.hash,
                         "96d84cb1120e05cb030a279801ea0e65b544ac93db1e6ce7022cefb65ee51843") ||
            !std::strcmp(profile.hash,
                         "ca6aee3b46fc63e84c89e34fbf9c51f3127abceaec17a1bc6aa9e9d1ff482b53") ||
            !std::strcmp(profile.hash,
                         "3a333e279f2fa4ad6270a3406f8039ce349f20ef787bae2e73c784de768c6817") ||
            !std::strcmp(profile.hash,
                         "96ee2a334a93a6a0f40ed56c0cedf441b882681b9605deeb46d83d62d4a3e4b9")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            const bool harrow_build = !std::strcmp(profile.hash,
                "a7d0d9e88d11f09a36294569662718394cfa36d99286774ecf8c9fd6ea4f589e") || !std::strcmp(profile.hash,
                "96d84cb1120e05cb030a279801ea0e65b544ac93db1e6ce7022cefb65ee51843") || !std::strcmp(profile.hash,
                "ca6aee3b46fc63e84c89e34fbf9c51f3127abceaec17a1bc6aa9e9d1ff482b53") || !std::strcmp(profile.hash,
                "3a333e279f2fa4ad6270a3406f8039ce349f20ef787bae2e73c784de768c6817") || !std::strcmp(profile.hash,
                "96ee2a334a93a6a0f40ed56c0cedf441b882681b9605deeb46d83d62d4a3e4b9");
            assert(profile.local_player_table == (harrow_build ? 0x276c3d8 : 0x2748d20));
        } else {
            ++historical;
            assert(profile.observer_services_offset == 0xe40);
            assert(profile.local_player_table == 0);
        }
        bool composition = false, blend = false;
        for (std::size_t i = 0; i < profile.count; ++i) {
            const auto& span = profile.spans[i];
            assert(span.rva + span.size <= profile.image_size);
            composition |= span.rva == profile.composition;
            blend |= span.rva == profile.blend;
        }
        assert(composition && blend);
    }
    assert(historical == 3 && reviewed_current == 28);
    // A network vector contains metadata between its float components. Verify
    // the shared decoder indices against a fixed layout fixture.
    unsigned char raw[36]{};
    const float expected[] = {12.f, -34.f, 56.f};
    for (unsigned i = 0; i < 3; ++i)
        std::memcpy(raw + 16 + i * 8, expected + i, 4);
    for (unsigned i = 0; i < 3; ++i) {
        float observed = 0;
        std::memcpy(&observed, raw + kViewOffsetComponents[i], 4);
        assert(observed == expected[i]);
    }
    static_assert(static_cast<unsigned>(AttachFieldIndex::scene_node) == 0);
    static_assert(static_cast<unsigned>(AttachFieldIndex::eye_angles) == 5);
    static_assert(static_cast<unsigned>(AttachFieldIndex::scene_sibling) == 7);
}
