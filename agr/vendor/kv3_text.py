"""Strict parser for Source 2 Viewer text KV3 dumps, including byte blobs."""
import re,json
TOKEN=re.compile(r'(?P<space>\s+|//[^\n]*|/\*.*?\*/)|(?P<string>"(?:\\.|[^"\\])*")|(?P<number>-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|(?P<word>[A-Za-z_][A-Za-z_0-9]*)|(?P<symbol>[{}\[\]=,:])',re.S)


def parse_kv3(text):
    marker=text.index('-->',text.index('<!-- kv3'))+3
    text=text[marker:];text=re.sub(r"#\[([0-9a-fA-F\s]*)\]",lambda m:json.dumps(list(bytes.fromhex(m[1]))),text);tokens=[];pos=0
    while pos<len(text):
        match=TOKEN.match(text,pos)
        if not match:raise ValueError('Unsupported KV3 token near '+repr(text[pos:pos+60]))
        pos=match.end()
        if match.lastgroup!='space':tokens.append((match.lastgroup,match.group()))
    cursor=0
    def take(expected=None):
        nonlocal cursor
        item=tokens[cursor];cursor+=1
        if expected is not None and item[1]!=expected:raise ValueError(f'Expected {expected}, got {item}')
        return item
    def value():
        nonlocal cursor
        kind,token=take()
        if token=='{':
            result={}
            while tokens[cursor][1]!='}':
                key_kind,key=take();key=json.loads(key) if key_kind=='string' else key
                take('=');result[key]=value()
                if tokens[cursor][1]==',':take(',')
            take('}');return result
        if token=='[':
            result=[]
            while tokens[cursor][1]!=']':
                result.append(value())
                if tokens[cursor][1]==',':take(',')
            take(']');return result
        if kind=='string':return json.loads(token)
        if kind=='number':return float(token) if any(c in token for c in '.eE') else int(token)
        if token in ('true','false','null'):return {'true':True,'false':False,'null':None}[token]
        if kind=='word' and tokens[cursor][1]==':':
            take(':');return value()  # resource/resource_name annotation; preserve value.
        raise ValueError('Unsupported KV3 value '+token)
    result=value()
    if cursor!=len(tokens):raise ValueError('Trailing KV3 tokens')
    return result

