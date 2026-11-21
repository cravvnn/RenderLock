#include "dolly_lite.hpp"
#include <cassert>
#include <cmath>
int main(){
    assert(dolly::valid_lite_seek_tick(3470,146926));
    assert(!dolly::valid_lite_seek_tick(-1,146926));
    assert(!dolly::valid_lite_seek_tick(146926,146926));
    assert(!dolly::valid_lite_seek_tick(2.5,146926));
    assert(std::abs(dolly::lite_fov_factor(90,90)-1)<1e-9);
    assert(std::abs(dolly::lite_fov_factor(60,90)-std::sqrt(3.0))<1e-9);
    assert(!std::isfinite(dolly::lite_fov_factor(0,90)));
    dolly::EditorLiteReplayInfo info{};
    assert(!dolly::valid_lite_replay_info(info));
    std::memcpy(info.magic,"DLYLITE1",8);info.abi=1;info.revision=7;info.flags=3;info.start_tick=3389;info.total_ticks=146926;info.tick_rate=64;info.count=2;info.ticks[0]=3389;info.ticks[1]=3517;
    assert(dolly::valid_lite_replay_info(info));
    info.ticks[1]=-2;assert(!dolly::valid_lite_replay_info(info));
}
