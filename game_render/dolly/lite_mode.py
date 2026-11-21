from __future__ import annotations
import math
import struct
import subprocess
import shlex
from .replays import parse_launch_options

LITE_FLAG = '-dolly-lite'
REPLAY_INFO_OFFSET = 2 * 1024 * 1024 + 27712
REPLAY_INFO = struct.Struct('<8s6Iqqd32q')

def launch_options_for_mode(options: str, lite: bool) -> str:
    tokens = shlex.split(options, posix=True)
    args = parse_launch_options(subprocess.list2cmdline([arg for arg in tokens if arg.casefold() != LITE_FLAG]))
    if lite:
        args.append(LITE_FLAG)
    return subprocess.list2cmdline(args)

def pack_replay_info(sequence, project, revision, first=0, total_ticks=None):
    from .editor_wire import pack_camera_list
    pack_camera_list(sequence, project, revision, first)
    keys = project.keyframes[first:first+32]
    start, rate = project.start_tick, project.tick_rate
    known = (not isinstance(start, bool) and isinstance(start, int) and start >= 0
             and not isinstance(rate, bool) and isinstance(rate, (int,float))
             and math.isfinite(rate) and rate > 0)
    total_known = (not isinstance(total_ticks,bool) and isinstance(total_ticks,int)
                   and 0 <= total_ticks <= 2147483647)
    ticks = [round(start + key.time*rate) if known else -1 for key in keys]
    if any(tick > 2147483647 or tick < -1 for tick in ticks):
        raise ValueError('Camera replay ticks exceed the supported range')
    return REPLAY_INFO.pack(b'DLYLITE1',sequence,1,revision,first,len(keys),
                           int(known)|int(total_known)*2,start if known else -1,
                           total_ticks if total_known else -1,rate if known else 0,
                           *(ticks+[-1]*(32-len(ticks))))
