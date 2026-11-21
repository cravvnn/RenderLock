#pragma once
#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <vector>
#include <cstring>
#include <cmath>
#include "dolly_player_matte_pass.hpp"
namespace dolly::player_capture {
class MatteCapture {
    template<class T> using Com=Microsoft::WRL::ComPtr<T>;
    Com<ID3D11Texture2D> target,staging;Com<ID3D11RenderTargetView> view;
    Com<ID3D11PixelShader> white;Com<ID3D11Query> query;Com<ID3D11DeviceContext> context;
    bool normalizeViewport=false;
    D3D11_VIEWPORT captureViewport{};
    std::vector<unsigned> sourceColumns;
    static bool read_viewport(ID3D11DeviceContext* c,unsigned w,unsigned h,D3D11_VIEWPORT& result) noexcept {
        D3D11_VIEWPORT views[D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE]{};
        UINT count=D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE;
        c->RSGetViewports(&count,views);
        if(count!=1)return false;
        const auto& v=views[0];
        for(float value:{v.TopLeftX,v.TopLeftY,v.Width,v.Height})
            if(!std::isfinite(value) || value<0 || std::abs(value-std::round(value))>.001f)return false;
        if(v.Width<1 || v.Height<1 || double(v.TopLeftX)+v.Width>w || double(v.TopLeftY)+v.Height>h)return false;
        result=v;return true;
    }
    bool matches_viewport(ID3D11DeviceContext* c) const noexcept {
        D3D11_VIEWPORT current{};
        return !normalizeViewport || (read_viewport(c,width,height,current) &&
            std::memcmp(&current,&captureViewport,sizeof(current))==0);
    }
public:
    unsigned width=0,height=0,bytesPerPixel=4;bool pending=false,complete=false;HRESULT failure=S_OK;
    std::vector<unsigned char> pixels;
    bool prepare(ID3D11DeviceContext* c,unsigned w,unsigned h,ID3D11PixelShader* material=nullptr,bool hdr=false,bool preserveMaterial=false) {
        if(context || !c || c->GetType()!=D3D11_DEVICE_CONTEXT_IMMEDIATE || !w || !h || w>8192 || h>8192)return false;
        // Preserve the original rasterization and depth coordinates. Normalize
        // only the readback: upscalers can use a smaller viewport inside a
        // full-resolution scene allocation, whose unused pixels are padding.
        normalizeViewport=preserveMaterial;
        if(normalizeViewport && !read_viewport(c,w,h,captureViewport)) {failure=E_INVALIDARG;return false;}
        Com<ID3D11Device> device;c->GetDevice(&device);width=w;height=h;bytesPerPixel=hdr?8:4;
        if(normalizeViewport) {
            sourceColumns.resize(w);
            for(unsigned x=0;x<w;++x)
                sourceColumns[x]=static_cast<unsigned>(captureViewport.TopLeftX+
                    (double(x)+.5)*captureViewport.Width/w)*bytesPerPixel;
        }
        if(material)white=material;
        else if(!preserveMaterial) {
            Com<ID3DBlob> code,error;
            const char source[]="float4 main():SV_Target{return 1;}";
            failure=D3DCompile(source,sizeof(source)-1,nullptr,nullptr,nullptr,"main","ps_5_0",0,0,&code,&error);
            if(FAILED(failure))return false;
            failure=device->CreatePixelShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&white);if(FAILED(failure))return false;
        }
        D3D11_TEXTURE2D_DESC d{};d.Width=w;d.Height=h;d.MipLevels=d.ArraySize=1;d.Format=hdr?DXGI_FORMAT_R16G16B16A16_FLOAT:DXGI_FORMAT_R8G8B8A8_UNORM;d.SampleDesc.Count=1;d.BindFlags=D3D11_BIND_RENDER_TARGET;
        failure=device->CreateTexture2D(&d,nullptr,&target);if(FAILED(failure))return false;
        failure=device->CreateRenderTargetView(target.Get(),nullptr,&view);if(FAILED(failure))return false;
        d.BindFlags=0;d.Usage=D3D11_USAGE_STAGING;d.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
        failure=device->CreateTexture2D(&d,nullptr,&staging);if(FAILED(failure))return false;
        D3D11_QUERY_DESC q{D3D11_QUERY_EVENT,0};failure=device->CreateQuery(&q,&query);if(FAILED(failure))return false;
        pixels.resize(std::size_t(w)*h*bytesPerPixel); // Allocate before changing state or calling the original.
        const FLOAT black[4]{};c->ClearRenderTargetView(view.Get(),black);
        context=c;return true;
    }
    template<class Draw> bool append(ID3D11DeviceContext* c,Draw original,bool preserveDepth=false,bool preserveMaterial=false,bool coverageAlpha=false) {
        return context.Get()==c && !pending && !complete && matches_viewport(c) && redirect_matte(c,view.Get(),white.Get(),original,preserveDepth,preserveMaterial,coverageAlpha);
    }
    bool finish(ID3D11DeviceContext* c) {
        if(!context || context.Get()!=c || pending || complete)return false;
        c->CopyResource(staging.Get(),target.Get());c->End(query.Get());pending=true;return true;
    }
    template<class Draw> bool begin(ID3D11DeviceContext* c,unsigned w,unsigned h,Draw original,ID3D11PixelShader* material=nullptr,bool preserveDepth=false,bool hdr=false) {
        return prepare(c,w,h,material,hdr) && append(c,original,preserveDepth) && finish(c);
    }
    // Called only after capture admission is closed and writers have drained.
    // Keep CPU pixels for diagnostics; release COM resources before DLL teardown.
    void release_gpu() noexcept {
        query.Reset();view.Reset();white.Reset();staging.Reset();target.Reset();context.Reset();pending=false;
    }
    // Reuse one completed slot for the next frame of a bounded sequence. The
    // target is cleared again so no pixel from the previous frame survives.
    bool recycle(ID3D11DeviceContext* c) noexcept {
        if(!context || context.Get()!=c || pending || !complete)return false;
        const FLOAT black[4]{};c->ClearRenderTargetView(view.Get(),black);
        complete=false;failure=S_OK;return true;
    }
    // Diagnostic control: paint the target so a readback path can prove it is
    // not blind before an empty capture is blamed on the redirected draw.
    void clear(ID3D11DeviceContext* c,const FLOAT rgba[4]) noexcept {
        if(context.Get()==c && view)c->ClearRenderTargetView(view.Get(),rgba);
    }
    // Bounded diagnostic read: copy a small centre region of the target and
    // count non-zero bytes, so a redirect that writes nothing is visible draw
    // by draw instead of only after the frame seals.
    unsigned probe_content(ID3D11DeviceContext* c) noexcept {
        if(!context || context.Get()!=c || !target || !staging || pending || width<256 || height<256)return 0;
        D3D11_BOX box{};
        box.left=width/2-128;box.right=width/2+128;box.top=height/2-128;box.bottom=height/2+128;box.front=0;box.back=1;
        c->CopySubresourceRegion(staging.Get(),0,0,0,0,target.Get(),0,&box);
        D3D11_MAPPED_SUBRESOURCE m{};
        if(FAILED(c->Map(staging.Get(),0,D3D11_MAP_READ,0,&m)))return 0;
        unsigned nonzero=0;
        for(unsigned y=0;y<256;++y) {
            const auto* row=static_cast<const unsigned char*>(m.pData)+std::size_t(y)*m.RowPitch;
            for(unsigned i=0;i<256*bytesPerPixel;++i)if(row[i])++nonzero;
        }
        c->Unmap(staging.Get(),0);
        return nonzero;
    }
    // A recycled slot keeps its target; callers must not prepare it again.
    bool reusable_for(ID3D11DeviceContext* c,unsigned w,unsigned h) const noexcept {
        return context.Get()==c && width==w && height==h && !pending && !complete && matches_viewport(c);
    }
    int poll(ID3D11DeviceContext* c) noexcept {
        if(!pending || c!=context.Get())return 0;
        BOOL done=FALSE;failure=c->GetData(query.Get(),&done,sizeof(done),D3D11_ASYNC_GETDATA_DONOTFLUSH);
        if(failure==S_FALSE || (SUCCEEDED(failure)&&!done))return 0;
        if(FAILED(failure)){pending=false;return -1;}
        D3D11_MAPPED_SUBRESOURCE m{};failure=c->Map(staging.Get(),0,D3D11_MAP_READ,D3D11_MAP_FLAG_DO_NOT_WAIT,&m);
        if(failure==DXGI_ERROR_WAS_STILL_DRAWING)return 0;
        if(FAILED(failure)){pending=false;return -1;}
        const bool scaled=normalizeViewport && (captureViewport.Width!=width || captureViewport.Height!=height);
        unsigned previousRow=~0u;
        for(unsigned y=0;y<height;++y) {
            auto* output=pixels.data()+std::size_t(y)*width*bytesPerPixel;
            if(!scaled) {
                std::memcpy(output,static_cast<const unsigned char*>(m.pData)+std::size_t(y)*m.RowPitch,width*bytesPerPixel);
                continue;
            }
            const unsigned sourceY=static_cast<unsigned>(captureViewport.TopLeftY+
                (double(y)+.5)*captureViewport.Height/height);
            if(sourceY==previousRow) {
                std::memcpy(output,output-std::size_t(width)*bytesPerPixel,width*bytesPerPixel);
            } else {
                const auto* source=static_cast<const unsigned char*>(m.pData)+std::size_t(sourceY)*m.RowPitch;
                for(unsigned x=0;x<width;++x)
                    std::memcpy(output+std::size_t(x)*bytesPerPixel,source+sourceColumns[x],bytesPerPixel);
            }
            previousRow=sourceY;
        }
        c->Unmap(staging.Get(),0);pending=false;complete=true;return 1;
    }
};
}
