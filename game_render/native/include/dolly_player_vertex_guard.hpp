#pragma once
#include "dolly_player_material_matte.hpp"
#include <initializer_list>
namespace dolly::player_capture {
inline std::vector<unsigned char> vertex_record_guard(const void* data,std::size_t size,unsigned selector,unsigned position,unsigned identity,const std::array<std::uint32_t,8>& expected) {
    if(!data || size<36 || size>1024*1024 || selector>31 || position>31)return {};
    const auto* b=static_cast<const unsigned char*>(data);
    if(std::memcmp(b,"DXBC",4) || word(b+24)!=size)return {};
    const auto hash=dxbc_hash(b,size);if(std::memcmp(hash.data(),b+4,16))return {};
    const auto count=word(b+28);if(!count || count>64 || 32+count*4>size)return {};
    std::vector<std::vector<unsigned char>> chunks;unsigned patched=0;
    for(unsigned i=0;i<count;++i){const auto off=word(b+32+4*i);
        if(off<32+count*4 || off>size-8)return {};const auto len=word(b+off+4);if(len>size-off-8)return {};
        std::vector<unsigned char> chunk(b+off,b+off+8+len);
        if(!std::memcmp(b+off,"SHEX",4) || !std::memcmp(b+off,"SHDR",4)){
            if(len<12 || len%4 || word(b+off+8)!=0x10050 || word(b+off+12)!=len/4)return {};
            std::vector<std::uint32_t> t(len/4);std::memcpy(t.data(),b+off+8,len);
            unsigned returns=0,temp=4096;for(std::size_t j=2;j<t.size();){const auto op=t[j]&0x7ff;
                if(op==53 && j+1>=t.size())return {};const auto length=op==53?t[j+1]:(t[j]>>24)&0x7f;
                if(!length || length>t.size()-j)return {};
                if(op==104){if(length!=2 || temp!=4096 || t[j+1]>=4095)return {};temp=t[j+1]++;}
                if(op==62 || op==63){if(j!=t.size()-1 || t[j]!=(1u<<24|62))return {};++returns;}j+=length;
            }if(returns!=1 || temp>=4095)return {};
            std::vector<std::uint32_t> extra;
            auto ins=[&](unsigned op,std::initializer_list<std::uint32_t> args){extra.push_back(op|(static_cast<unsigned>(args.size()+1)<<24));extra.insert(extra.end(),args);};
            auto dst=[](unsigned kind,unsigned mask){return 1u<<20|kind<<12|mask<<4|2;};
            auto src=[](unsigned kind,unsigned component){return 1u<<20|kind<<12|component<<4|10;};
            constexpr unsigned lit=4u<<12|1;
            ins(32,{dst(0,4),temp,src(1,0),selector,lit,identity});
            for(unsigned j=0;j<8;++j){
                ins(167,{dst(0,1),temp,src(1,0),selector,lit,j*4,src(7,0),1});
                ins(32,{dst(0,2),temp,src(0,0),temp,lit,expected[j]});
                ins(1,{dst(0,4),temp,src(0,2),temp,src(0,1),temp});
            }
            ins(31,{src(0,2),temp}); // IF zero: reject any identity or record-word mismatch.
            ins(54,{dst(2,15),position,lit,0});ins(21,{});
            t.insert(t.end()-1,extra.begin(),extra.end());t[1]=static_cast<std::uint32_t>(t.size());
            chunk.resize(8+t.size()*4);const auto newlen=static_cast<std::uint32_t>(t.size()*4);
            std::memcpy(chunk.data()+4,&newlen,4);std::memcpy(chunk.data()+8,t.data(),newlen);++patched;
        }chunks.push_back(std::move(chunk));
    }if(patched!=1)return {};
    std::vector<unsigned char> out(b,b+32+count*4);for(unsigned i=0;i<count;++i){const auto off=static_cast<std::uint32_t>(out.size());std::memcpy(out.data()+32+4*i,&off,4);out.insert(out.end(),chunks[i].begin(),chunks[i].end());}
    const auto total=static_cast<std::uint32_t>(out.size());std::memcpy(out.data()+24,&total,4);const auto newhash=dxbc_hash(out.data(),out.size());std::memcpy(out.data()+4,newhash.data(),16);return out;
}
}
