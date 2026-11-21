#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <d3d11sdklayers.h>
#include <wrl/client.h>
#include <cstdio>
#include <cstdlib>
#include "dolly_player_vertex_guard.hpp"
#include "dolly_player_snapshot.hpp"
#include <chrono>
#include <thread>
using Microsoft::WRL::ComPtr;
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"FAILED %d: %s\n",__LINE__,#x);std::exit(1);}}while(0)
int main() {
    ComPtr<ID3D11Device> d;
    ComPtr<ID3D11DeviceContext> c;
    CHECK(SUCCEEDED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
                                      D3D11_CREATE_DEVICE_DEBUG, nullptr, 0, D3D11_SDK_VERSION, &d,
                                      nullptr, &c)));
    ComPtr<ID3D11InfoQueue> info;
    CHECK(SUCCEEDED(d.As(&info)));
    const char source[] =
        R"(struct R{uint4 a;uint4 b;};StructuredBuffer<R> records:register(t1);struct O{float4 p:SV_Position;float4 c:COLOR0;};O main(uint id:TEXCOORD13,uint v:SV_VertexID){float2 p[3]={float2(-1,-1),float2(0,1),float2(1,-1)};O o;o.p=float4(p[v],.5,1);o.c=float4(records[id].a.y/255.,0,0,1);return o;})";
    ComPtr<ID3DBlob> code, err;
    CHECK(SUCCEEDED(D3DCompile(source, sizeof(source) - 1, nullptr, nullptr, nullptr, "main",
                               "vs_5_0", 0, 0, &code, &err)));
    ComPtr<ID3D11ShaderReflection> r;
    CHECK(SUCCEEDED(D3DReflect(code->GetBufferPointer(), code->GetBufferSize(),
                               __uuidof(ID3D11ShaderReflection),
                               reinterpret_cast<void**>(r.GetAddressOf()))));
    D3D11_SHADER_DESC desc{};
    r->GetDesc(&desc);
    unsigned selector = 99, position = 99;
    for (unsigned i = 0; i < desc.InputParameters; ++i) {
        D3D11_SIGNATURE_PARAMETER_DESC p{};
        r->GetInputParameterDesc(i, &p);
        if (p.SemanticIndex == 13 && !_stricmp(p.SemanticName, "TEXCOORD"))
            selector = p.Register;
    }
    for (unsigned i = 0; i < desc.OutputParameters; ++i) {
        D3D11_SIGNATURE_PARAMETER_DESC p{};
        r->GetOutputParameterDesc(i, &p);
        if (p.SystemValueType == D3D_NAME_POSITION)
            position = p.Register;
    }
    std::array<unsigned, 8> expected = {3, 255, 17, 23, 42, 13, 7, 0x3f800000};
    auto guarded = dolly::player_capture::vertex_record_guard(
        code->GetBufferPointer(), code->GetBufferSize(), selector, position, 0, expected);
    CHECK(!guarded.empty());
    ComPtr<ID3D11VertexShader> original, guard;
    CHECK(SUCCEEDED(d->CreateVertexShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr,
                                          &original)));
    CHECK(SUCCEEDED(d->CreateVertexShader(guarded.data(), guarded.size(), nullptr, &guard)));
    D3D11_INPUT_ELEMENT_DESC element = {
        "TEXCOORD", 13, DXGI_FORMAT_R32_UINT, 1, 0, D3D11_INPUT_PER_INSTANCE_DATA, 1};
    ComPtr<ID3D11InputLayout> layout;
    CHECK(SUCCEEDED(d->CreateInputLayout(&element, 1, code->GetBufferPointer(),
                                         code->GetBufferSize(), &layout)));
    c->IASetInputLayout(layout.Get());
    const char ps[] =
        "float4 main(float4 p:SV_Position,float4 color:COLOR0):SV_Target{return color;}";
    CHECK(SUCCEEDED(D3DCompile(ps, sizeof(ps) - 1, nullptr, nullptr, nullptr, "main", "ps_5_0", 0,
                               0, &code, &err)));
    ComPtr<ID3D11PixelShader> pixel;
    CHECK(SUCCEEDED(
        d->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &pixel)));
    c->PSSetShader(pixel.Get(), nullptr, 0);
    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = 64;
    bd.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = 32;
    ComPtr<ID3D11Buffer> buffer;
    CHECK(SUCCEEDED(d->CreateBuffer(&bd, nullptr, &buffer)));
    ComPtr<ID3D11ShaderResourceView> srv;
    CHECK(SUCCEEDED(d->CreateShaderResourceView(buffer.Get(), nullptr, &srv)));
    auto view = srv.Get();
    c->VSSetShaderResources(1, 1, &view);
    unsigned ids[] = {99, 99, 0, 1};
    bd = {};
    bd.ByteWidth = sizeof(ids);
    bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    D3D11_SUBRESOURCE_DATA data{ids, 0, 0};
    ComPtr<ID3D11Buffer> vb;
    CHECK(SUCCEEDED(d->CreateBuffer(&bd, &data, &vb)));
    auto rawVB = vb.Get();
    UINT stride = 4, offset = 4;
    c->IASetVertexBuffers(1, 1, &rawVB, &stride, &offset);
    D3D11_TEXTURE2D_DESC td{};
    td.Width = td.Height = 32;
    td.MipLevels = td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.BindFlags = D3D11_BIND_RENDER_TARGET;
    ComPtr<ID3D11Texture2D> target, stage;
    CHECK(SUCCEEDED(d->CreateTexture2D(&td, nullptr, &target)));
    ComPtr<ID3D11RenderTargetView> rt;
    CHECK(SUCCEEDED(d->CreateRenderTargetView(target.Get(), nullptr, &rt)));
    auto rawRT = rt.Get();
    c->OMSetRenderTargets(1, &rawRT, nullptr);
    td.BindFlags = 0;
    td.Usage = D3D11_USAGE_STAGING;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    CHECK(SUCCEEDED(d->CreateTexture2D(&td, nullptr, &stage)));
    D3D11_VIEWPORT vp{0, 0, 32, 32, 0, 1};
    c->RSSetViewports(1, &vp);
    D3D11_RASTERIZER_DESC rd{};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    ComPtr<ID3D11RasterizerState> rs;
    CHECK(SUCCEEDED(d->CreateRasterizerState(&rd, &rs)));
    c->RSSetState(rs.Get());
    c->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    auto render = [&](ID3D11VertexShader* vs, unsigned first) {
        float zero[4]{};
        c->ClearRenderTargetView(rt.Get(), zero);
        c->VSSetShader(vs, nullptr, 0);
        c->DrawInstanced(3, 1, 0, first);
        c->CopyResource(stage.Get(), target.Get());
        D3D11_MAPPED_SUBRESOURCE m{};
        CHECK(SUCCEEDED(c->Map(stage.Get(), 0, D3D11_MAP_READ, 0, &m)));
        std::vector<unsigned char> out(32 * 32 * 4);
        for (unsigned y = 0; y < 32; ++y)
            memcpy(out.data() + y * 128, static_cast<char*>(m.pData) + y * m.RowPitch, 128);
        c->Unmap(stage.Get(), 0);
        return out;
    };
    std::array<std::array<unsigned, 8>, 2> records = {expected, expected};
    c->UpdateSubresource(buffer.Get(), 0, nullptr, records.data(), 0, 0);
    auto baseline = render(original.Get(), 1);
    CHECK(std::count(baseline.begin(), baseline.end(), 255) > 0);
    CHECK(render(guard.Get(), 1) == baseline);
    for (unsigned word = 0; word < 8; ++word) {
        records[0] = expected;
        records[0][word] ^= 1;
        c->UpdateSubresource(buffer.Get(), 0, nullptr, records.data(), 0, 0);
        auto out = render(guard.Get(), 1);
        for (auto x : out)
            CHECK(x == 0);
    }
    records[0] = expected;
    c->UpdateSubresource(buffer.Get(), 0, nullptr, records.data(), 0, 0);
    auto wrongIdentity = render(guard.Get(), 2);
    for (auto x : wrongIdentity)
        CHECK(x == 0);
    // Three frame slots of 64 independent snapshots. Later uploads must not
    // overwrite earlier queued evidence. Live code never Flushes or waits.
    using dolly::player_capture::GpuSnapshot;
    std::array<GpuSnapshot, 3 * 64> snapshots;
    for (unsigned i = 0; i < snapshots.size(); ++i) {
        records[0] = expected;
        records[0][1] = i + 100;
        c->UpdateSubresource(buffer.Get(), 0, nullptr, records.data(), 0, 0);
        CHECK(snapshots[i].begin(c.Get(), buffer.Get(), vb.Get(), 8, 0));
    }
    CHECK(GpuSnapshot::pooled_bytes() == snapshots.size() * 36);
    c->Flush(); // WARP test submission only, never part of the game observer.
    const auto poll = [&](GpuSnapshot& snapshot) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        int result = 0;
        while (!(result = snapshot.poll(c.Get())) && std::chrono::steady_clock::now() < deadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        return result;
    };
    for (unsigned i = 0; i < snapshots.size(); ++i) {
        CHECK(poll(snapshots[i]) == 1);
        CHECK(snapshots[i].id == 0 && snapshots[i].record[1] == i + 100);
        snapshots[i].release();
    }
    CHECK(GpuSnapshot::pooled_bytes() == 0);
    GpuSnapshot mismatch;
    CHECK(mismatch.begin(c.Get(), buffer.Get(), vb.Get(), 8, 1));
    c->Flush();
    CHECK(poll(mismatch) == -1); // GPU identity zero cannot validate record one.
    CHECK(!mismatch.begin(c.Get(), buffer.Get(), vb.Get(), 8, 2));
    CHECK(!mismatch.begin(c.Get(), buffer.Get(), vb.Get(), 16, 0));
    for (UINT64 i = 0; i < info->GetNumStoredMessages(); ++i) {
        SIZE_T n = 0;
        info->GetMessage(i, nullptr, &n);
        std::vector<unsigned char> bytes(n);
        auto* m = reinterpret_cast<D3D11_MESSAGE*>(bytes.data());
        info->GetMessage(i, m, &n);
        CHECK(m->Severity > D3D11_MESSAGE_SEVERITY_WARNING);
    }
    c->ClearState();
    std::puts(
        "GPU exact-record guard passed: exact original pixels, each of eight changed words rejected, reused identity rejected, nonzero stream/start offsets.");
}
