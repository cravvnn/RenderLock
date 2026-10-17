"""Source 2 resource-name hash candidate, validated against supplied RERL pairs.

MurmurHash64B over lower-case resource path, seed 0xEDABCDEF.
"""
def resource_id(path):
    data=path.lower().replace('\\','/').encode('utf8');seed=0xedabcdef
    m=0x5bd1e995;mask=0xffffffff;h1=seed^len(data);h2=0;p=0
    def mix(k):
        k=k*m&mask;k^=k>>24;return k*m&mask
    while len(data)-p>=8:
        h1=(h1*m&mask)^mix(int.from_bytes(data[p:p+4],'little'));p+=4
        h2=(h2*m&mask)^mix(int.from_bytes(data[p:p+4],'little'));p+=4
    if len(data)-p>=4:
        h1=(h1*m&mask)^mix(int.from_bytes(data[p:p+4],'little'));p+=4
    if p<len(data):h2^=int.from_bytes(data[p:],'little');h2=h2*m&mask
    h1^=h2>>18;h1=h1*m&mask;h2^=h1>>22;h2=h2*m&mask
    h1^=h2>>17;h1=h1*m&mask;h2^=h1>>19;h2=h2*m&mask
    return h1<<32|h2
