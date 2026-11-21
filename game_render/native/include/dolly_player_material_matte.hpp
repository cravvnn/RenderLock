#pragma once
// Private port of the previously tested offline DXBC final-output patch.
#include <array>
#include <vector>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <algorithm>
namespace dolly::player_capture {
inline std::uint32_t word(const unsigned char* p){std::uint32_t x;std::memcpy(&x,p,4);return x;}
inline void putword(std::vector<unsigned char>& v,std::uint32_t x){const auto n=v.size();v.resize(n+4);std::memcpy(v.data()+n,&x,4);}
inline std::array<unsigned char,16> dxbc_hash(const unsigned char* blob,std::size_t size) {
    std::array<unsigned char,16> result{};if(size<20 || size>1024*1024)return result;
    const auto bytes=size-20,tail=bytes%64;const auto bits=static_cast<std::uint32_t>(bytes*8);
    std::vector<unsigned char> padded(blob+20,blob+20+bytes-tail);
    if(tail>=56){padded.insert(padded.end(),blob+size-tail,blob+size);padded.push_back(128);padded.insert(padded.end(),63-tail,0);putword(padded,bits);padded.insert(padded.end(),56,0);}
    else {putword(padded,bits);padded.insert(padded.end(),blob+size-tail,blob+size);padded.push_back(128);padded.insert(padded.end(),55-tail,0);}
    putword(padded,(bits>>2)|1);
    std::uint32_t state[4]={0x67452301,0xefcdab89,0x98badcfe,0x10325476};
    const unsigned shifts[16]={7,12,17,22,5,9,14,20,4,11,16,23,6,10,15,21};
    for(std::size_t off=0;off<padded.size();off+=64){
        auto a=state[0],b=state[1],c=state[2],d=state[3];
        for(unsigned i=0;i<64;++i){std::uint32_t f,g;
            if(i<16){f=(b&c)|(~b&d);g=i;}else if(i<32){f=(d&b)|(~d&c);g=(5*i+1)%16;}
            else if(i<48){f=b^c^d;g=(3*i+5)%16;}else{f=c^(b|~d);g=(7*i)%16;}
            const auto k=static_cast<std::uint32_t>(std::abs(std::sin(double(i+1)))*4294967296.0);
            const auto v=a+f+k+word(padded.data()+off+g*4);const auto s=shifts[(i/16)*4+i%4];
            a=d;d=c;c=b;b+=((v<<s)|(v>>(32-s)));
        }state[0]+=a;state[1]+=b;state[2]+=c;state[3]+=d;
    }std::memcpy(result.data(),state,16);return result;
}
inline std::vector<unsigned char> material_white(const void* data,std::size_t size,bool whiteOutput=true) {
    if(!data || size<36 || size>1024*1024)return {};
    const auto* b=static_cast<const unsigned char*>(data);
    if(std::memcmp(b,"DXBC",4) || word(b+24)!=size)return {};
    const auto hash=dxbc_hash(b,size);if(std::memcmp(hash.data(),b+4,16))return {};
    const auto count=word(b+28);if(!count || count>64 || 32+count*4>size)return {};
    std::vector<std::vector<unsigned char>> chunks;unsigned patched=0;
    for(unsigned i=0;i<count;++i){const auto off=word(b+32+4*i);
        if(off<32+count*4 || off>size-8)return {};const auto len=word(b+off+4);if(len>size-off-8)return {};
        std::vector<unsigned char> chunk(b+off,b+off+8+len);
        if(!std::memcmp(b+off,"SHEX",4) || !std::memcmp(b+off,"SHDR",4)){
            if(len<12 || len%4 || word(b+off+8)!=0x50 || word(b+off+12)!=len/4)return {};
            std::vector<std::uint32_t> tokens(len/4);std::memcpy(tokens.data(),b+off+8,len);
            unsigned returns=0;for(std::size_t j=2;j<tokens.size();){const auto op=tokens[j]&0x7ff;
                if(op==53 && j+1>=tokens.size())return {};
                const auto length=op==53?tokens[j+1]:(tokens[j]>>24)&0x7f;
                if(!length || length>tokens.size()-j)return {};
                if(op==62 || op==63){if(j!=tokens.size()-1 || tokens[j]!=(1u<<24|62))return {};++returns;}j+=length;
            }if(returns!=1)return {};
            // mov o0.xyzw, l(1.0), immediately before sole final ret.
            const std::uint32_t extra[]={5u<<24|54,1u<<20|2u<<12|(whiteOutput?15u:8u)<<4|2,0,4u<<12|1,0x3f800000};
            tokens.insert(tokens.end()-1,std::begin(extra),std::end(extra));tokens[1]=static_cast<std::uint32_t>(tokens.size());
            chunk.resize(8+tokens.size()*4);const auto newlen=static_cast<std::uint32_t>(tokens.size()*4);
            std::memcpy(chunk.data()+4,&newlen,4);std::memcpy(chunk.data()+8,tokens.data(),newlen);++patched;
        }chunks.push_back(std::move(chunk));
    }if(patched!=1)return {};
    std::vector<unsigned char> out(b,b+32+count*4);for(unsigned i=0;i<count;++i){const auto off=static_cast<std::uint32_t>(out.size());std::memcpy(out.data()+32+4*i,&off,4);out.insert(out.end(),chunks[i].begin(),chunks[i].end());}
    const auto total=static_cast<std::uint32_t>(out.size());std::memcpy(out.data()+24,&total,4);const auto newhash=dxbc_hash(out.data(),out.size());std::memcpy(out.data()+4,newhash.data(),16);return out;
}
}
