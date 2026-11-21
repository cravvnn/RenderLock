#pragma once
#include <cstdint>
#include <cstring>
#include <cstddef>

namespace dolly {
constexpr std::size_t kReplayEventsOffset = 2 * 1024 * 1024 + 24576 + 4096;
constexpr unsigned kReplayKills = 3500, kReplayPlayers = 32;
struct ReplayKill { std::int32_t tick; std::uint16_t killer, victim; };
struct ReplayPlayer { std::uint32_t id; char name[64]; };
struct EditorReplayEvents {
    char magic[8];
    std::uint32_t sequence, abi, status, count, player_count, total_ticks;
    char message[128];
    ReplayKill kills[kReplayKills];
    ReplayPlayer players[kReplayPlayers];
};
static_assert(sizeof(EditorReplayEvents) == 30336, "Replay events wire size");
inline bool valid_replay_events(const EditorReplayEvents& value) noexcept {
    if (std::memcmp(value.magic, "DLYKILL1", 8) || value.abi != 1 || value.status > 3 ||
        value.count > kReplayKills || value.player_count > kReplayPlayers ||
        value.total_ticks > 2147483647 || value.message[127]) return false;
    for (unsigned p=0;p<value.player_count;++p) {
        if (value.players[p].id > 65535 || value.players[p].name[63]) return false;
        for (unsigned q=0;q<p;++q) if (value.players[q].id==value.players[p].id) return false;
    }
    for (unsigned i=0;i<value.count;++i) {
        const auto& kill=value.kills[i];
        if (kill.tick < 0 || unsigned(kill.tick) >= value.total_ticks ||
            (i && kill.tick < value.kills[i-1].tick)) return false;
        bool killer=false, victim=false;
        for (unsigned p=0;p<value.player_count;++p) {
            killer |= value.players[p].id==kill.killer;
            victim |= value.players[p].id==kill.victim;
        }
        if (!killer || !victim) return false;
    }
    return true;
}
inline const char* replay_player_name(const EditorReplayEvents& events, unsigned id) noexcept {
    for (unsigned p=0;p<events.player_count;++p)
        if (events.players[p].id==id) return events.players[p].name;
    return "Unknown player";
}
enum class ReplayEventKind { Hidden, Kill, Death };
inline ReplayEventKind replay_event_kind(const ReplayKill& kill, int player) noexcept {
    if (player < 0) return ReplayEventKind::Hidden;
    if (kill.victim == unsigned(player)) return ReplayEventKind::Death;
    if (kill.killer == unsigned(player)) return ReplayEventKind::Kill;
    return ReplayEventKind::Hidden;
}
inline bool kill_matches(const ReplayKill& kill, int player) noexcept {
    return replay_event_kind(kill,player) != ReplayEventKind::Hidden;
}
bool editor_replay_events(EditorReplayEvents& out) noexcept;
}
