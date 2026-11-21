#include "dolly_replay_events.hpp"
#include <stdexcept>
int main() {
    dolly::EditorReplayEvents e{};
    std::memcpy(e.magic,"DLYKILL1",8);e.abi=1;e.status=2;e.total_ticks=1000;
    e.player_count=2;e.players[0].id=1;e.players[1].id=2;
    std::strcpy(e.players[0].name,"Ivy");std::strcpy(e.players[1].name,"Abrams");
    e.count=3;e.kills[0]={100,1,2};e.kills[1]={200,2,1};e.kills[2]={250,1,2};
    if(!dolly::valid_replay_events(e))throw std::runtime_error("Valid event packet rejected");
    if(!dolly::kill_matches(e.kills[0],1) || dolly::kill_matches(e.kills[0],3) ||
       std::strcmp(dolly::replay_player_name(e,1),"Ivy"))throw std::runtime_error("Player highlight/name mismatch");
    if(!dolly::kill_matches(e.kills[0],2) || dolly::kill_matches(e.kills[0],-1))
        throw std::runtime_error("Selected player's death is missing or unselected events are visible");
    unsigned ivy_kills=0, abrams_kills=0;
    for(unsigned i=0;i<e.count;++i) {
        ivy_kills+=dolly::kill_matches(e.kills[i],1);
        abrams_kills+=dolly::kill_matches(e.kills[i],2);
    }
    if(ivy_kills!=3 || abrams_kills!=3)
        throw std::runtime_error("Selected player's kills and deaths must both be included");
    if(dolly::replay_event_kind(e.kills[0],1)!=dolly::ReplayEventKind::Kill ||
       dolly::replay_event_kind(e.kills[0],2)!=dolly::ReplayEventKind::Death ||
       dolly::replay_event_kind(e.kills[0],3)!=dolly::ReplayEventKind::Hidden ||
       dolly::replay_event_kind(e.kills[0],-1)!=dolly::ReplayEventKind::Hidden)
        throw std::runtime_error("Selected player kill/death classification mismatch");
    e.kills[1].tick=1000;if(dolly::valid_replay_events(e))throw std::runtime_error("Out-of-range tick accepted");
    e.kills[1].tick=200;e.kills[0].killer=3;
    if(dolly::valid_replay_events(e))throw std::runtime_error("Unknown player accepted");
}
