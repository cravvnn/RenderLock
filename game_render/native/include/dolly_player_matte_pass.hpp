#pragma once
#include <d3d11.h>
#include <wrl/client.h>
namespace dolly::player_capture {
// Redirect exactly one original draw into a private unbound target. This intentionally
// changes that draw output/query coverage, but must not add an extra original call.
template <class Draw>
bool redirect_matte(ID3D11DeviceContext* c, ID3D11RenderTargetView* target,
                    ID3D11PixelShader* white, Draw replay, bool preserveDepth = false,
                    bool preserveMaterial = false, bool coverageAlpha = false) {
    if (!c || !target || (!white && !preserveMaterial) ||
        c->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE)
        return false;
    bool unsupported = false;
    ID3D11Predicate* predicate = nullptr;
    BOOL value = FALSE;
    c->GetPredication(&predicate, &value);
    if (predicate) {
        unsupported = true;
        predicate->Release();
    }
    ID3D11Buffer* outputs[4]{};
    c->SOGetTargets(4, outputs);
    for (auto* p : outputs)
        if (p) {
            unsupported = true;
            p->Release();
        }
    ID3D11UnorderedAccessView* uavs[8]{};
    c->OMGetRenderTargetsAndUnorderedAccessViews(0, nullptr, nullptr, 0, 8, uavs);
    for (auto* p : uavs)
        if (p) {
            unsupported = true;
            p->Release();
        }
    ID3D11GeometryShader* gs = nullptr;
    c->GSGetShader(&gs, nullptr, nullptr);
    if (gs) {
        unsupported = true;
        gs->Release();
    }
    ID3D11HullShader* hs = nullptr;
    c->HSGetShader(&hs, nullptr, nullptr);
    if (hs) {
        unsupported = true;
        hs->Release();
    }
    ID3D11DomainShader* ds = nullptr;
    c->DSGetShader(&ds, nullptr, nullptr);
    if (ds) {
        unsupported = true;
        ds->Release();
    }
    if (unsupported)
        return false;
    struct Restore {
        ID3D11DeviceContext* c;
        ID3D11RenderTargetView* targets[8]{};
        ID3D11DepthStencilView* depth = nullptr;
        ID3D11PixelShader* shader = nullptr;
        ID3D11ClassInstance* classes[256]{};
        UINT count = 256;
        ID3D11BlendState* blend = nullptr;
        FLOAT factors[4]{};
        UINT mask = 0;
        explicit Restore(ID3D11DeviceContext* context) : c(context) {
            c->OMGetRenderTargets(8, targets, &depth);
            c->PSGetShader(&shader, classes, &count);
            c->OMGetBlendState(&blend, factors, &mask);
        }
        ~Restore() {
            c->OMSetRenderTargets(8, targets, depth);
            c->PSSetShader(shader, classes, count);
            c->OMSetBlendState(blend, factors, mask);
            for (auto* p : targets)
                if (p)
                    p->Release();
            if (depth)
                depth->Release();
            if (shader)
                shader->Release();
            if (blend)
                blend->Release();
            for (UINT i = 0; i < count; ++i)
                if (classes[i])
                    classes[i]->Release();
        }
    } restore(c);
    // Preserve the original depth attachment exactly, including a null view
    // on late material passes. Do not impose a depth buffer they never used.
    Microsoft::WRL::ComPtr<ID3D11BlendState> coverageBlend;
    if (preserveMaterial && coverageAlpha && restore.blend) {
        D3D11_BLEND_DESC desc{};
        restore.blend->GetDesc(&desc);
        if (desc.RenderTarget[0].BlendEnable) {
            // Color modulation shades existing coverage; it must not create opacity.
            // Other supported materials accumulate premultiplied source-over coverage.
            const auto& rt = desc.RenderTarget[0];
            const bool modulation = rt.SrcBlend == D3D11_BLEND_ZERO &&
                                    rt.DestBlend == D3D11_BLEND_SRC_COLOR &&
                                    rt.BlendOp == D3D11_BLEND_OP_ADD;
            desc.RenderTarget[0].SrcBlendAlpha =
                modulation ? D3D11_BLEND_ZERO : D3D11_BLEND_INV_DEST_ALPHA;
            desc.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_ONE;
            desc.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
            Microsoft::WRL::ComPtr<ID3D11Device> device;
            c->GetDevice(&device);
            if (FAILED(device->CreateBlendState(&desc, &coverageBlend)))
                return false;
        }
    }
    c->OMSetRenderTargets(1, &target, preserveDepth ? restore.depth : nullptr);
    if (!preserveMaterial) {
        c->OMSetBlendState(nullptr, nullptr, 0xffffffff);
        c->PSSetShader(white, nullptr, 0);
    }
    if (coverageBlend)
        c->OMSetBlendState(coverageBlend.Get(), restore.factors, restore.mask);
    replay();
    return true;
}
}

namespace dolly::player_capture {
template <class Draw>
bool isolated_matte(ID3D11DeviceContext* c, ID3D11RenderTargetView* target,
                    ID3D11PixelShader* white, bool queriesKnownIdle, Draw replay) {
    return queriesKnownIdle && redirect_matte(c, target, white, replay);
}
}
