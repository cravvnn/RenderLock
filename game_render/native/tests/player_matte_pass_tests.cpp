#include "dolly_player_material_matte.hpp"
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <d3d11sdklayers.h>
#include <wrl/client.h>
#include <vector>
#include <algorithm>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include "dolly_player_matte_pass.hpp"
#include "dolly_player_matte_capture.hpp"
using Microsoft::WRL::ComPtr;
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"FAILED line %d: %s\n",__LINE__,#x);std::exit(1);}}while(0)
ComPtr<ID3DBlob> compile(const char* code, const char* profile) {
    ComPtr<ID3DBlob> b, e;
    CHECK(SUCCEEDED(
        D3DCompile(code, strlen(code), nullptr, nullptr, nullptr, "main", profile, 0, 0, &b, &e)));
    return b;
}
int main() {
    ComPtr<ID3D11Device> d;
    ComPtr<ID3D11DeviceContext> c;
    CHECK(SUCCEEDED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
                                      D3D11_CREATE_DEVICE_DEBUG, nullptr, 0, D3D11_SDK_VERSION, &d,
                                      nullptr, &c)));
    ComPtr<ID3D11InfoQueue> info;
    CHECK(SUCCEEDED(d.As(&info)));
    auto code = compile(
        "float4 main(uint i:SV_VertexID):SV_Position{float2 v[3]={float2(-1,-1),float2(0,1),float2(1,-1)};return float4(v[i],0.5,1);}",
        "vs_5_0");
    ComPtr<ID3D11VertexShader> vs;
    CHECK(SUCCEEDED(
        d->CreateVertexShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &vs)));
    ComPtr<ID3D11PixelShader> red, white;
    code = compile("float4 main():SV_Target{return float4(1,0,0,1);}", "ps_5_0");
    CHECK(SUCCEEDED(
        d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &red)));
    code = compile("float4 main():SV_Target{return 1;}", "ps_5_0");
    CHECK(SUCCEEDED(
        d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &white)));
    D3D11_TEXTURE2D_DESC td{};
    td.Width = 64;
    td.Height = 32;
    td.MipLevels = td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.BindFlags = D3D11_BIND_RENDER_TARGET;
    ComPtr<ID3D11Texture2D> color, matte, staging;
    ComPtr<ID3D11RenderTargetView> colorView, matteView;
    CHECK(SUCCEEDED(d->CreateTexture2D(&td, nullptr, &color)));
    CHECK(SUCCEEDED(d->CreateTexture2D(&td, nullptr, &matte)));
    CHECK(SUCCEEDED(d->CreateRenderTargetView(color.Get(), nullptr, &colorView)));
    CHECK(SUCCEEDED(d->CreateRenderTargetView(matte.Get(), nullptr, &matteView)));
    td.Usage = D3D11_USAGE_STAGING;
    td.BindFlags = 0;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    CHECK(SUCCEEDED(d->CreateTexture2D(&td, nullptr, &staging)));
    D3D11_VIEWPORT viewport{0, 0, 64, 32, 0, 1};
    c->RSSetViewports(1, &viewport);
    D3D11_RASTERIZER_DESC rd{};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    ComPtr<ID3D11RasterizerState> rs;
    CHECK(SUCCEEDED(d->CreateRasterizerState(&rd, &rs)));
    c->RSSetState(rs.Get());
    c->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    c->VSSetShader(vs.Get(), nullptr, 0);
    c->PSSetShader(red.Get(), nullptr, 0);
    auto rt = colorView.Get();
    c->OMSetRenderTargets(1, &rt, nullptr);
    FLOAT factors[4] = {0.1f, 0.2f, 0.3f, 0.4f};
    c->OMSetBlendState(nullptr, factors, 0xffffffff);
    FLOAT black[4]{};
    c->ClearRenderTargetView(colorView.Get(), black);
    c->ClearRenderTargetView(matteView.Get(), black);
    unsigned calls = 0;
    auto replay = [&] {
        ++calls;
        c->Draw(3, 0);
    };
    CHECK(!dolly::player_capture::isolated_matte(c.Get(), matteView.Get(), white.Get(), false,
                                                 replay));
    CHECK(calls == 0);
    CHECK(
        dolly::player_capture::isolated_matte(c.Get(), matteView.Get(), white.Get(), true, replay));
    CHECK(calls == 1);
    auto checkState = [&] {
        ComPtr<ID3D11RenderTargetView> out;
        ComPtr<ID3D11PixelShader> ps;
        c->OMGetRenderTargets(1, &out, nullptr);
        c->PSGetShader(&ps, nullptr, nullptr);
        CHECK(out.Get() == colorView.Get() && ps.Get() == red.Get());
        FLOAT current[4]{};
        UINT mask = 0;
        ComPtr<ID3D11BlendState> blend;
        c->OMGetBlendState(&blend, current, &mask);
        CHECK(std::memcmp(current, factors, sizeof(factors)) == 0 && mask == 0xffffffff);
    };
    checkState();
    try {
        dolly::player_capture::isolated_matte(c.Get(), matteView.Get(), white.Get(), true,
                                              [] { throw std::runtime_error("injected"); });
        CHECK(false);
    } catch (const std::runtime_error&) {
    }
    checkState();
    auto pixels = [&](ID3D11Texture2D* source) {
        c->CopyResource(staging.Get(), source);
        D3D11_MAPPED_SUBRESOURCE m{};
        CHECK(SUCCEEDED(c->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &m)));
        std::vector<unsigned char> p(64 * 32 * 4);
        for (unsigned y = 0; y < 32; ++y)
            memcpy(p.data() + y * 64 * 4, static_cast<char*>(m.pData) + y * m.RowPitch, 64 * 4);
        c->Unmap(staging.Get(), 0);
        return p;
    };
    auto unchanged = pixels(color.Get());
    for (auto v : unchanged)
        CHECK(v == 0);
    auto mask = pixels(matte.Get());
    unsigned covered = 0, empty = 0;
    for (unsigned i = 0; i < mask.size(); i += 4) {
        if (mask[i + 3]) {
            ++covered;
            CHECK(mask[i] == 255 && mask[i + 1] == 255 && mask[i + 2] == 255 && mask[i + 3] == 255);
        } else
            ++empty;
    }
    CHECK(covered && empty);
    c->Draw(3, 0);
    auto restored = pixels(color.Get());
    for (unsigned i = 0; i < mask.size(); i += 4) {
        CHECK(restored[i] == mask[i] && restored[i + 1] == 0 && restored[i + 2] == 0 &&
              restored[i + 3] == mask[i + 3]);
    }
    dolly::player_capture::MatteCapture capture;
    unsigned originalCalls = 0;
    CHECK(capture.begin(c.Get(), 64, 32, [&] {
        ++originalCalls;
        c->Draw(3, 0);
    }));
    CHECK(originalCalls == 1);
    checkState();
    c->Flush();
    int captured = 0;
    const auto deadline = GetTickCount64() + 2000;
    while (!captured && GetTickCount64() < deadline) {
        captured = capture.poll(c.Get());
        if (!captured)
            Sleep(1);
    }
    CHECK(captured == 1 && capture.pixels == mask);
    checkState();
    // Preserve original clip/discard instructions in a patched real pixel shader.
    code = compile(
        "float4 main(float4 p:SV_Position):SV_Target{clip(p.x-32);return float4(.2,.4,.8,1);}",
        "ps_5_0");
    auto patched =
        dolly::player_capture::material_white(code->GetBufferPointer(), code->GetBufferSize());
    CHECK(!patched.empty());
    auto corrupt = patched;
    corrupt[4] ^= 1;
    CHECK(dolly::player_capture::material_white(corrupt.data(), corrupt.size()).empty());
    CHECK(dolly::player_capture::material_white(patched.data(), 24).empty());
    ComPtr<ID3D11PixelShader> material, materialWhite;
    CHECK(SUCCEEDED(
        d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &material)));
    CHECK(SUCCEEDED(d->CreatePixelShader(patched.data(), patched.size(), nullptr, &materialWhite)));
    c->PSSetShader(material.Get(), nullptr, 0);
    c->ClearRenderTargetView(colorView.Get(), black);
    c->Draw(3, 0);
    auto materialPixels = pixels(color.Get());
    c->ClearRenderTargetView(matteView.Get(), black);
    CHECK(dolly::player_capture::redirect_matte(c.Get(), matteView.Get(), materialWhite.Get(),
                                                [&] { c->Draw(3, 0); }));
    auto materialMask = pixels(matte.Get());
    unsigned kept = 0;
    for (unsigned i = 0; i < materialMask.size(); i += 4) {
        CHECK(materialMask[i + 3] == materialPixels[i + 3]);
        kept += materialMask[i + 3] != 0;
        if (materialMask[i + 3])
            CHECK(materialMask[i] == 255 && materialMask[i + 1] == 255 &&
                  materialMask[i + 2] == 255);
    }
    CHECK(kept > 0 && kept < covered);
    ComPtr<ID3D11PixelShader> restoredMaterial;
    c->PSGetShader(&restoredMaterial, nullptr, nullptr);
    CHECK(restoredMaterial.Get() == material.Get());
    // Original scene depth must hide geometry behind scenery, with DSV restoration.
    D3D11_TEXTURE2D_DESC depthDesc{};
    depthDesc.Width = 64;
    depthDesc.Height = 32;
    depthDesc.MipLevels = depthDesc.ArraySize = 1;
    depthDesc.Format = DXGI_FORMAT_D32_FLOAT;
    depthDesc.SampleDesc.Count = 1;
    depthDesc.BindFlags = D3D11_BIND_DEPTH_STENCIL;
    ComPtr<ID3D11Texture2D> depthTexture;
    ComPtr<ID3D11DepthStencilView> depthView;
    CHECK(SUCCEEDED(d->CreateTexture2D(&depthDesc, nullptr, &depthTexture)));
    CHECK(SUCCEEDED(d->CreateDepthStencilView(depthTexture.Get(), nullptr, &depthView)));
    D3D11_DEPTH_STENCIL_DESC dd{};
    dd.DepthEnable = TRUE;
    dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
    dd.DepthFunc = D3D11_COMPARISON_LESS;
    ComPtr<ID3D11DepthStencilState> depthState;
    CHECK(SUCCEEDED(d->CreateDepthStencilState(&dd, &depthState)));
    c->OMSetDepthStencilState(depthState.Get(), 0);
    c->OMSetRenderTargets(1, &rt, depthView.Get());
    c->ClearDepthStencilView(depthView.Get(), D3D11_CLEAR_DEPTH, .25f, 0);
    c->ClearRenderTargetView(matteView.Get(), black);
    CHECK(dolly::player_capture::redirect_matte(
        c.Get(), matteView.Get(), materialWhite.Get(), [&] { c->Draw(3, 0); }, true));
    auto occluded = pixels(matte.Get());
    for (auto x : occluded)
        CHECK(x == 0);
    ComPtr<ID3D11DepthStencilView> restoredDepth;
    c->OMGetRenderTargets(0, nullptr, &restoredDepth);
    CHECK(restoredDepth.Get() == depthView.Get());
    c->ClearDepthStencilView(depthView.Get(), D3D11_CLEAR_DEPTH, 1.f, 0);
    CHECK(dolly::player_capture::redirect_matte(
        c.Get(), matteView.Get(), materialWhite.Get(), [&] { c->Draw(3, 0); }, true));
    CHECK(pixels(matte.Get()) == materialMask);
    // Preserve a late material pass that originally has no depth attachment.
    c->OMSetRenderTargets(1, &rt, nullptr);
    c->ClearRenderTargetView(matteView.Get(), black);
    unsigned noDepthCalls = 0;
    CHECK(dolly::player_capture::redirect_matte(
        c.Get(), matteView.Get(), materialWhite.Get(),
        [&] {
            ++noDepthCalls;
            c->Draw(3, 0);
        },
        true));
    CHECK(noDepthCalls == 1 && pixels(matte.Get()) == materialMask);
    ComPtr<ID3D11DepthStencilView> absentDepth;
    c->OMGetRenderTargets(0, nullptr, &absentDepth);
    CHECK(!absentDepth);
    ComPtr<ID3D11RenderTargetView> restoredTarget;
    c->OMGetRenderTargets(1, &restoredTarget, nullptr);
    CHECK(restoredTarget.Get() == rt);
    c->OMSetRenderTargets(1, &rt, depthView.Get());
    // Color output retains HDR RGB while adding explicit opaque coverage alpha.
    code = compile("float4 main():SV_Target{return float4(2,.5,.25,.2);}", "ps_5_0");
    auto colorCode = dolly::player_capture::material_white(code->GetBufferPointer(),
                                                           code->GetBufferSize(), false);
    CHECK(!colorCode.empty());
    ComPtr<ID3D11PixelShader> hdrPS;
    CHECK(SUCCEEDED(d->CreatePixelShader(colorCode.data(), colorCode.size(), nullptr, &hdrPS)));
    dolly::player_capture::MatteCapture hdrCapture;
    CHECK(hdrCapture.begin(c.Get(), 64, 32, [&] { c->Draw(3, 0); }, hdrPS.Get(), false, true));
    c->Flush();
    int hdrResult = 0;
    const auto hdrDeadline = GetTickCount64() + 2000;
    while (!hdrResult && GetTickCount64() < hdrDeadline) {
        hdrResult = hdrCapture.poll(c.Get());
        if (!hdrResult)
            Sleep(1);
    }
    CHECK(hdrResult == 1 && hdrCapture.bytesPerPixel == 8);
    unsigned hdrCovered = 0;
    for (std::size_t i = 0; i < hdrCapture.pixels.size(); i += 8) {
        std::uint16_t v[4]{};
        memcpy(v, hdrCapture.pixels.data() + i, 8);
        if (v[3]) {
            CHECK(v[0] == 0x4000 && v[1] == 0x3800 && v[2] == 0x3400 && v[3] == 0x3c00);
            ++hdrCovered;
        } else
            CHECK(!v[0] && !v[1] && !v[2]);
    }
    CHECK(hdrCovered == covered);
    // Accumulate original premultiplied material and multiplicative darkening in one HDR target.
    code = compile("float4 main():SV_Target{return float4(.25,0,0,.5);}", "ps_5_0");
    ComPtr<ID3D11PixelShader> premul;
    CHECK(SUCCEEDED(
        d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &premul)));
    c->PSSetShader(premul.Get(), nullptr, 0);
    D3D11_BLEND_DESC blendDesc{};
    auto& br = blendDesc.RenderTarget[0];
    br.BlendEnable = TRUE;
    br.SrcBlend = D3D11_BLEND_ONE;
    br.DestBlend = D3D11_BLEND_INV_SRC_ALPHA;
    br.BlendOp = D3D11_BLEND_OP_ADD;
    br.SrcBlendAlpha = D3D11_BLEND_INV_DEST_ALPHA;
    br.DestBlendAlpha = D3D11_BLEND_ONE;
    br.BlendOpAlpha = D3D11_BLEND_OP_ADD;
    br.RenderTargetWriteMask = 15;
    ComPtr<ID3D11BlendState> overBlend;
    CHECK(SUCCEEDED(d->CreateBlendState(&blendDesc, &overBlend)));
    c->OMSetBlendState(overBlend.Get(), nullptr, 0xffffffff);
    dolly::player_capture::MatteCapture aggregate;
    CHECK(aggregate.prepare(c.Get(), 64, 32, nullptr, true, true));
    unsigned appended = 0;
    auto drawOriginal = [&] {
        ++appended;
        c->Draw(3, 0);
    };
    CHECK(aggregate.append(c.Get(), drawOriginal, false, true, true));
    CHECK(aggregate.append(c.Get(), drawOriginal, false, true, true));
    code = compile("float4 main():SV_Target{return float4(.5,.5,.5,1);}", "ps_5_0");
    ComPtr<ID3D11PixelShader> darken;
    CHECK(SUCCEEDED(
        d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &darken)));
    c->PSSetShader(darken.Get(), nullptr, 0);
    br.SrcBlend = D3D11_BLEND_ZERO;
    br.DestBlend = D3D11_BLEND_SRC_COLOR;
    br.SrcBlendAlpha = D3D11_BLEND_ONE;
    ComPtr<ID3D11BlendState> darkBlend;
    CHECK(SUCCEEDED(d->CreateBlendState(&blendDesc, &darkBlend)));
    c->OMSetBlendState(darkBlend.Get(), nullptr, 0xffffffff);
    CHECK(aggregate.append(c.Get(), drawOriginal, false, true, true));
    CHECK(appended == 3);
    CHECK(aggregate.finish(c.Get()));
    CHECK(!aggregate.append(c.Get(), drawOriginal, false, true, true) && appended == 3);
    c->Flush();
    int aggregateResult = 0;
    const auto aggregateDeadline = GetTickCount64() + 2000;
    while (!aggregateResult && GetTickCount64() < aggregateDeadline) {
        aggregateResult = aggregate.poll(c.Get());
        if (!aggregateResult)
            Sleep(1);
    }
    CHECK(aggregateResult == 1);
    unsigned aggregateCovered = 0;
    for (std::size_t i = 0; i < aggregate.pixels.size(); i += 8) {
        std::uint16_t v[4]{};
        memcpy(v, aggregate.pixels.data() + i, 8);
        if (v[3]) {
            CHECK(v[0] == 0x3200 && v[1] == 0 && v[2] == 0 && v[3] == 0x3a00);
            ++aggregateCovered;
        } else
            CHECK(!v[0] && !v[1] && !v[2]);
    }
    CHECK(aggregateCovered == covered);
    // A color-only modulation draw on transparent pixels must not invent a black silhouette.
    dolly::player_capture::MatteCapture darkOnly;
    CHECK(darkOnly.prepare(c.Get(), 64, 32, nullptr, true, true));
    CHECK(darkOnly.append(c.Get(), [&] { c->Draw(3, 0); }, false, true, true));
    CHECK(darkOnly.finish(c.Get()));
    c->Flush();
    int darkResult = 0;
    const auto darkDeadline = GetTickCount64() + 2000;
    while (!darkResult && GetTickCount64() < darkDeadline) {
        darkResult = darkOnly.poll(c.Get());
        if (!darkResult)
            Sleep(1);
    }
    CHECK(darkResult == 1);
    for (auto b : darkOnly.pixels)
        CHECK(b == 0);
    darkOnly.release_gpu();
    auto retainedPixels = aggregate.pixels;
    aggregate.release_gpu();
    CHECK(aggregate.complete && aggregate.pixels == retainedPixels && !aggregate.pending);
    CHECK(!aggregate.finish(c.Get()));
    ComPtr<ID3D11BlendState> restoredBlend;
    c->OMGetBlendState(&restoredBlend, nullptr, nullptr);
    CHECK(restoredBlend.Get() == darkBlend.Get());
    // Frame-slot reuse: consecutive frames share one capture object and must not
    // retain pixels from the previous frame; a pending slot refuses recycling.
    c->OMSetBlendState(overBlend.Get(), nullptr, 0xffffffff);
    auto single = [&](ID3D11PixelShader* ps) {
        dolly::player_capture::MatteCapture capture;
        CHECK(capture.prepare(c.Get(), 64, 32, nullptr, true, true));
        c->PSSetShader(ps, nullptr, 0);
        CHECK(capture.append(c.Get(), [&] { c->Draw(3, 0); }, false, true, true));
        CHECK(capture.finish(c.Get()));
        c->Flush();
        int result = 0;
        const auto deadline = GetTickCount64() + 2000;
        while (!result && GetTickCount64() < deadline) {
            result = capture.poll(c.Get());
            if (!result)
                Sleep(1);
        }
        CHECK(result == 1);
        capture.release_gpu();
        return capture.pixels;
    };
    code = compile("float4 main():SV_Target{return float4(.25,0,0,.5);}", "ps_5_0");
    ComPtr<ID3D11PixelShader> redFrame;
    CHECK(SUCCEEDED(
        d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &redFrame)));
    code = compile("float4 main():SV_Target{return float4(0,.25,0,.5);}", "ps_5_0");
    ComPtr<ID3D11PixelShader> greenFrame;
    CHECK(SUCCEEDED(d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr,
                                         &greenFrame)));
    const auto expectedRed = single(redFrame.Get());
    const auto expectedGreen = single(greenFrame.Get());
    CHECK(expectedRed != expectedGreen);
    dolly::player_capture::MatteCapture reuse;
    CHECK(reuse.prepare(c.Get(), 64, 32, nullptr, true, true));
    c->PSSetShader(redFrame.Get(), nullptr, 0);
    CHECK(reuse.append(c.Get(), [&] { c->Draw(3, 0); }, false, true, true));
    CHECK(reuse.finish(c.Get()));
    c->Flush();
    int reuseResult = 0;
    const auto reuseDeadline = GetTickCount64() + 2000;
    while (!reuseResult && GetTickCount64() < reuseDeadline) {
        reuseResult = reuse.poll(c.Get());
        if (!reuseResult)
            Sleep(1);
    }
    CHECK(reuseResult == 1);
    CHECK(reuse.pixels == expectedRed);
    CHECK(reuse.recycle(c.Get()));
    // A moving shot can leave every player behind and later see one again.
    // Seal the actual cleared scene image without any selected draw between.
    CHECK(reuse.finish(c.Get()));
    c->Flush();
    reuseResult = 0;
    const auto emptyDeadline = GetTickCount64() + 2000;
    while (!reuseResult && GetTickCount64() < emptyDeadline) {
        reuseResult = reuse.poll(c.Get());
        if (!reuseResult)
            Sleep(1);
    }
    CHECK(reuseResult == 1);
    CHECK(std::all_of(reuse.pixels.begin(), reuse.pixels.end(), [](auto x) { return x == 0; }));
    CHECK(reuse.recycle(c.Get()));
    c->PSSetShader(greenFrame.Get(), nullptr, 0);
    CHECK(reuse.append(c.Get(), [&] { c->Draw(3, 0); }, false, true, true));
    CHECK(reuse.finish(c.Get()));
    CHECK(!reuse.recycle(c.Get()));
    c->Flush();
    reuseResult = 0;
    const auto reuseDeadline2 = GetTickCount64() + 2000;
    while (!reuseResult && GetTickCount64() < reuseDeadline2) {
        reuseResult = reuse.poll(c.Get());
        if (!reuseResult)
            Sleep(1);
    }
    CHECK(reuseResult == 1);
    CHECK(reuse.pixels == expectedGreen);
    CHECK(reuse.recycle(c.Get()));
    reuse.release_gpu();
    // Upscalers may keep a full-sized scene allocation but rasterize into a
    // smaller viewport. The Players image must fill the same output canvas,
    // without changing the original draw/depth/viewport or copying its padding.
    for (const auto scaledViewport : {D3D11_VIEWPORT{0, 0, 32, 16, 0, 1},
                                      D3D11_VIEWPORT{0, 0, 40, 20, 0, 1},
                                      D3D11_VIEWPORT{8, 4, 40, 20, 0, 1}}) {
        c->RSSetViewports(1, &scaledViewport);
        c->OMSetRenderTargets(1, &rt, depthView.Get());
        c->OMSetDepthStencilState(depthState.Get(), 0);
        c->OMSetBlendState(nullptr, nullptr, 0xffffffff);
        c->PSSetShader(red.Get(), nullptr, 0);
        c->ClearRenderTargetView(colorView.Get(), black);
        const auto beforeScaled = pixels(color.Get());
        for (bool hidden : {false, true}) {
            c->ClearDepthStencilView(depthView.Get(), D3D11_CLEAR_DEPTH, hidden ? .25f : 1.f, 0);
            dolly::player_capture::MatteCapture scaled;
            CHECK(scaled.prepare(c.Get(), 64, 32, nullptr, true, true));
            unsigned draws = 0;
            CHECK(scaled.append(c.Get(), [&] { ++draws; c->Draw(3, 0); }, true, true, true));
            CHECK(draws == 1 && scaled.finish(c.Get()));
            c->Flush();
            int result = 0;
            const auto limit = GetTickCount64() + 2000;
            while (!result && GetTickCount64() < limit) {
                result = scaled.poll(c.Get());
                if (!result) Sleep(1);
            }
            CHECK(result == 1 && scaled.width == 64 && scaled.height == 32);
            const auto alphaAt = [&](unsigned x, unsigned y) {
                std::uint16_t alpha = 0;
                std::memcpy(&alpha, scaled.pixels.data() + (y * 64 + x) * 8 + 6, 2);
                return alpha;
            };
            CHECK(alphaAt(32, 8) == (hidden ? 0 : 0x3c00));
            CHECK(alphaAt(20, 24) == (hidden ? 0 : 0x3c00));
            CHECK(alphaAt(0, 0) == 0);
            CHECK(pixels(color.Get()) == beforeScaled);
            D3D11_VIEWPORT after{};
            UINT viewportCount = 1;
            c->RSGetViewports(&viewportCount, &after);
            CHECK(viewportCount == 1 && std::memcmp(&after, &scaledViewport, sizeof(after)) == 0);
            ComPtr<ID3D11DepthStencilView> originalDepth;
            c->OMGetRenderTargets(0, nullptr, &originalDepth);
            CHECK(originalDepth.Get() == depthView.Get());
            CHECK(scaled.recycle(c.Get()));
            auto changed = scaledViewport;
            changed.Width -= 1;
            c->RSSetViewports(1, &changed);
            CHECK(!scaled.append(c.Get(), [&] { ++draws; }, true, true, true));
            CHECK(draws == 1);
            c->RSSetViewports(1, &scaledViewport);
        }
    }
    // An unsupported viewport must not be guessed into a plausible frame.
    const D3D11_VIEWPORT outside{0, 0, 65, 32, 0, 1};
    c->RSSetViewports(1, &outside);
    dolly::player_capture::MatteCapture invalidViewport;
    CHECK(!invalidViewport.prepare(c.Get(), 64, 32, nullptr, true, true));
    const D3D11_VIEWPORT multiple[]{viewport, viewport};
    c->RSSetViewports(2, multiple);
    dolly::player_capture::MatteCapture multipleViewports;
    CHECK(!multipleViewports.prepare(c.Get(), 64, 32, nullptr, true, true));
    c->RSSetViewports(1, &viewport);
    for (UINT64 i = 0; i < info->GetNumStoredMessages(); ++i) {
        SIZE_T n = 0;
        info->GetMessage(i, nullptr, &n);
        std::vector<unsigned char> bytes(n);
        auto* m = reinterpret_cast<D3D11_MESSAGE*>(bytes.data());
        info->GetMessage(i, m, &n);
        CHECK(m->Severity > D3D11_MESSAGE_SEVERITY_WARNING);
    }
    c->ClearState();
    std::printf(
        "Matte pass passed: %u covered / %u transparent pixels, original target unchanged, restored red draw exact, exception restoration, unknown query state rejected\n",
        covered, empty);
}
