"""Optional append-only replay event sideband; existing Dolly offsets stay stable."""
import struct
from .replay_events import MAX_KILLS, MAX_PLAYERS

OFFSET = 2*1024*1024 + 24576 + 4096
MAPPING_BYTES = OFFSET + 32768
HEADER = struct.Struct('<8s6I128s')
KILL = struct.Struct('<iHH')
PLAYER = struct.Struct('<I64s')
WIRE_BYTES = HEADER.size + MAX_KILLS*KILL.size + MAX_PLAYERS*PLAYER.size


def text(value, size):
    return value.encode('utf-8', 'replace')[:size-1].decode('utf-8', 'ignore').encode('utf-8').ljust(size,b'\0')


def pack(sequence, index):
    if len(index.kills)>MAX_KILLS or len(index.players)>MAX_PLAYERS or index.status not in range(4):
        raise ValueError('Invalid replay event index')
    return (HEADER.pack(b'DLYKILL1', sequence, 1, index.status, len(index.kills),
                        len(index.players), index.total_ticks, text(index.message,128))
            + b''.join(KILL.pack(*kill) for kill in index.kills).ljust(MAX_KILLS*KILL.size,b'\0')
            + b''.join(PLAYER.pack(identifier,text(name,64)) for identifier,name in index.players)
                .ljust(MAX_PLAYERS*PLAYER.size,b'\0'))
