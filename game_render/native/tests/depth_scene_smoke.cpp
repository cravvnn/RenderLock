#include "dolly_depth_scene.hpp"
#include "dolly_player_capture.hpp"
#include <d3d11.h>
#include <d3dcompiler.h>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {
using namespace dolly::depth;
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
template <class T> struct Com {
    T* p = nullptr;
    ~Com() {
        if (p)
            p->Release();
    }
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
};
void check_early_shader_metadata(D3D_DRIVER_TYPE driver) {
    Com<ID3D11Device> rendering;
    // Exercise the actual startup entry point before client validation or any
    // reviewed scene configuration. It must collect metadata, not arm capture.
    require(dolly::player_capture::prime_creation_metadata(),
            "Prime early resource metadata failed");
    require(dolly::player_capture::prime_creation_metadata(),
            "Priming resource metadata is not idempotent");
    require(!dolly::player_capture::draw_hooks_requested() &&
                !dolly::player_capture::producer_hook_requested(),
            "Passive metadata bootstrap armed game draw or producer hooks");
    require(SUCCEEDED(D3D11CreateDevice(nullptr, driver, nullptr, 0, nullptr, 0,
                                        D3D11_SDK_VERSION, &rendering.p, nullptr, nullptr)),
            "Create separate rendering device failed");
    // Real shader/layout creation before any swapchain or Present. Discovery's
    // temporary device must not restrict metadata to its own resources.
    const char* source = "StructuredBuffer<float4> records:register(t1);"
                         "float4 main(uint id:TEXCOORD13):SV_Position{return records[id];}";
    Com<ID3DBlob> code, errors;
    require(SUCCEEDED(D3DCompile(source, std::strlen(source), nullptr, nullptr, nullptr,
                                 "main", "vs_5_0", 0, 0, &code.p, &errors.p)),
            "Compile player metadata shader failed");
    Com<ID3D11VertexShader> shader;
    require(SUCCEEDED(rendering.p->CreateVertexShader(code.p->GetBufferPointer(),
                  code.p->GetBufferSize(), nullptr, &shader.p)), "Create early player shader failed");
    const GUID shader_key = {0xb1a1d260,0xa73c,0x46ae,{0x82,0x49,0x4e,0x4a,0x27,0xfa,0x61,0xd4}};
    std::uint64_t info[8]{};
    UINT size = sizeof(info);
    require(SUCCEEDED(shader.p->GetPrivateData(shader_key, &size, info)) && size == sizeof(info),
            "Shader created before first Present lost metadata on rendering device");
    require(info[0] == 1 && info[1] == 0 && info[2] == 1 && (info[4] & 1) &&
            info[5] == 1 && info[6] == D3D_SIT_STRUCTURED && info[7] == 1,
            "Early shader identity/reflection metadata incorrect");
    const GUID bytes_key = {0x369a0c21,0xef10,0x4a0c,{0xac,0xa5,0x92,0x79,0x37,0x21,0xab,0x08}};
    std::vector<unsigned char> bytes(code.p->GetBufferSize());
    size = static_cast<UINT>(bytes.size());
    require(SUCCEEDED(shader.p->GetPrivateData(bytes_key, &size, bytes.data())) &&
            size == bytes.size() && !std::memcmp(bytes.data(), code.p->GetBufferPointer(), size),
            "Early vertex bytecode was not preserved exactly");
    D3D11_INPUT_ELEMENT_DESC element{"TEXCOORD",13,DXGI_FORMAT_R32_UINT,1,0,
                                      D3D11_INPUT_PER_INSTANCE_DATA,1};
    Com<ID3D11InputLayout> layout;
    require(SUCCEEDED(rendering.p->CreateInputLayout(&element,1,code.p->GetBufferPointer(),
                        code.p->GetBufferSize(),&layout.p)), "Create early identity layout failed");
    const GUID layout_key = {0x7c6ea90b,0xc290,0x4f0c,{0xa7,0x76,0x9b,0xc9,0x2e,0x51,0xd4,0x06}};
    size = sizeof(info);
    require(SUCCEEDED(layout.p->GetPrivateData(layout_key,&size,info)) && info[0] == 1 &&
            info[1] == 1 && info[2] == 1 && info[3] == DXGI_FORMAT_R32_UINT && info[5] == 1,
            "Early identity layout metadata incorrect");
    const char* pixel_source = "float4 main():SV_Target{return float4(1,1,1,1);}";
    Com<ID3DBlob> pixel_code, pixel_errors;
    require(SUCCEEDED(D3DCompile(pixel_source,std::strlen(pixel_source),nullptr,nullptr,nullptr,
                "main","ps_5_0",0,0,&pixel_code.p,&pixel_errors.p)), "Compile early pixel shader failed");
    Com<ID3D11PixelShader> pixel;
    require(SUCCEEDED(rendering.p->CreatePixelShader(pixel_code.p->GetBufferPointer(),
                pixel_code.p->GetBufferSize(),nullptr,&pixel.p)), "Create early pixel shader failed");
    const GUID pixel_key = {0x4fa14e83,0xd077,0x4773,{0x9d,0x31,0x31,0x63,0xd1,0x5f,0x17,0x82}};
    bytes.resize(pixel_code.p->GetBufferSize());
    size = static_cast<UINT>(bytes.size());
    require(SUCCEEDED(pixel.p->GetPrivateData(pixel_key,&size,bytes.data())) && size == bytes.size() &&
            !std::memcmp(bytes.data(),pixel_code.p->GetBufferPointer(),size),
            "Early pixel bytecode was not preserved exactly");
}
struct Test {
    Com<ID3D11Device> device;
    Com<ID3D11DeviceContext> context, deferred;
    Com<ID3D11VertexShader> vs;
    Com<ID3D11DepthStencilState> state;
    Com<ID3D11DepthStencilState> equal_state;
    Com<ID3D11RasterizerState> raster;
    Com<ID3D11Buffer> cb, indices, args, indexed_args;
    std::shared_ptr<SceneTracker> tracker;
    explicit Test(D3D_DRIVER_TYPE driver) {
        D3D_FEATURE_LEVEL level;
        require(SUCCEEDED(D3D11CreateDevice(nullptr, driver, nullptr, 0, nullptr, 0,
                                            D3D11_SDK_VERSION, &device.p, &level, &context.p)),
                "Create device failed");
        require(SUCCEEDED(device.p->CreateDeferredContext(0, &deferred.p)),
                "Create deferred failed");
        require(install_scene_hooks(device.p, context.p), "Install DX11 observation hooks failed");
        require(install_scene_hooks(device.p, context.p), "Hook installation not idempotent");
        require(!install_scene_hooks(device.p, deferred.p),
                "Deferred context accepted as immediate");
        const char* shader =
            "float4 main(uint id:SV_VertexID):SV_Position {"
            "float2 p=float2((id<<1)&2,id&2); return float4(p*float2(2,-2)+float2(-1,1),0.5,1);}";
        Com<ID3DBlob> code, errors;
        require(SUCCEEDED(D3DCompile(shader, std::strlen(shader), nullptr, nullptr, nullptr, "main",
                                     "vs_5_0", 0, 0, &code.p, &errors.p)),
                "Compile triangle shader failed");
        require(SUCCEEDED(device.p->CreateVertexShader(code.p->GetBufferPointer(),
                                                       code.p->GetBufferSize(), nullptr, &vs.p)),
                "Create vertex shader failed");
        D3D11_DEPTH_STENCIL_DESC ds{};
        ds.DepthEnable = true;
        ds.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        ds.DepthFunc = D3D11_COMPARISON_GREATER_EQUAL;
        require(SUCCEEDED(device.p->CreateDepthStencilState(&ds, &state.p)),
                "Create depth state failed");
        ds.DepthFunc = D3D11_COMPARISON_EQUAL;
        require(SUCCEEDED(device.p->CreateDepthStencilState(&ds, &equal_state.p)),
                "Create EQUAL depth state failed");
        D3D11_RASTERIZER_DESC rs{};
        rs.FillMode = D3D11_FILL_SOLID;
        rs.CullMode = D3D11_CULL_NONE;
        rs.DepthClipEnable = true;
        require(SUCCEEDED(device.p->CreateRasterizerState(&rs, &raster.p)),
                "Create raster state failed");
        D3D11_BUFFER_DESC bd{};
        bd.ByteWidth = 640;
        bd.Usage = D3D11_USAGE_DEFAULT;
        bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        require(SUCCEEDED(device.p->CreateBuffer(&bd, nullptr, &cb.p)),
                "Create per-view buffer failed");
        const std::array<UINT, 3> index_data{0, 1, 2};
        D3D11_SUBRESOURCE_DATA initial{};
        initial.pSysMem = index_data.data();
        bd.ByteWidth = sizeof(index_data);
        bd.BindFlags = D3D11_BIND_INDEX_BUFFER;
        require(SUCCEEDED(device.p->CreateBuffer(&bd, &initial, &indices.p)),
                "Create indices failed");
        const std::array<UINT, 5> arg_data{3, 1, 0, 0, 0};
        initial.pSysMem = arg_data.data();
        bd.ByteWidth = 16;
        bd.BindFlags = 0;
        bd.MiscFlags = D3D11_RESOURCE_MISC_DRAWINDIRECT_ARGS;
        require(SUCCEEDED(device.p->CreateBuffer(&bd, &initial, &args.p)),
                "Create arguments failed");
        bd.ByteWidth = 20;
        require(SUCCEEDED(device.p->CreateBuffer(&bd, &initial, &indexed_args.p)),
                "Create indexed arguments failed");
        tracker = std::make_shared<SceneTracker>(device.p, 17, 3);
        set_scene_tracker(tracker);
    }
    ~Test() { set_scene_tracker(nullptr); }
    void calibration(float near_clip) {
        std::array<unsigned char, 640> bytes{};
        const auto put = [&bytes](unsigned offset, float value) {
            std::memcpy(bytes.data() + offset, &value, 4);
        };
        for (unsigned offset : {128u, 148u, 168u, 188u, 192u, 212u, 372u, 436u})
            put(offset, 1);
        put(236, near_clip);
        put(248, -1);
        put(260, -1);
        put(264, 1 / near_clip);
        put(328, 17);
        put(332, 3);
        put(336, 1.0f / 17);
        put(340, 1.0f / 3);
        put(376, near_clip);
        put(380, std::numeric_limits<float>::infinity());
        put(456, -1);
        context.p->UpdateSubresource(cb.p, 0, nullptr, bytes.data(), 0, 0);
    }
    void bind(ID3D11DeviceContext* c, ID3D11DepthStencilView* dsv, unsigned slot = 1,
              UINT width = 17, UINT height = 3) {
        const std::array<ID3D11Buffer*, 14> empty{};
        c->VSSetConstantBuffers(0, 14, empty.data());
        c->VSSetConstantBuffers(slot, 1, &cb.p);
        c->VSSetShader(vs.p, nullptr, 0);
        c->PSSetShader(nullptr, nullptr, 0);
        c->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        c->IASetIndexBuffer(indices.p, DXGI_FORMAT_R32_UINT, 0);
        c->OMSetRenderTargets(0, nullptr, dsv);
        c->OMSetDepthStencilState(state.p, 0);
        c->RSSetState(raster.p);
        const D3D11_VIEWPORT viewport{0, 0, float(width), float(height), 0, 1};
        c->RSSetViewports(1, &viewport);
    }
    void read(const SceneFrame& scene, float expected) {
        require(scene.result == SceneResult::ready, "Scene tracker did not select a draw");
        ProjectionBuffer bindings[14]{};
        const auto count = scene.calibration(bindings, 14);
        require(count > 0, "Scene calibration snapshot missing");
        Readback queue;
        Frame frame{17, 3, 456, 12.5, {}};
        require(queue.enqueue(device.p, context.p, scene.texture(), frame, bindings, count) ==
                    ReadbackResult::ready,
                "Selected scene readback failed");
        context.p->Flush(); // Test harness only; production never waits or flushes.
        RawFrame raw;
        auto result = ReadbackResult::pending;
        const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (result == ReadbackResult::pending && std::chrono::steady_clock::now() < end) {
            result = queue.poll(context.p, raw);
            if (result == ReadbackResult::pending)
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        require(result == ReadbackResult::ready, "Selected scene calibration could not be decoded");
        std::array<float, 51> distances{};
        require(convert(raw.pixels.data(), raw.pixels.size(), 17 * 4, 17, 3, raw.format,
                        raw.frame.projection, distances.data(), distances.size()),
                "Scene conversion failed");
        for (float z : distances)
            require(std::abs(z - expected) < .001f,
                    "Scene pixels and draw-time calibration disagree");
    }
};
struct Surface {
    Com<ID3D11Texture2D> texture;
    Com<ID3D11DepthStencilView> view;
    Surface(ID3D11Device* device, const char* name, UINT width = 17, UINT height = 3) {
        D3D11_TEXTURE2D_DESC desc{};
        desc.Width = width;
        desc.Height = height;
        desc.MipLevels = desc.ArraySize = desc.SampleDesc.Count = 1;
        desc.Format = DXGI_FORMAT_R24G8_TYPELESS;
        desc.Usage = D3D11_USAGE_DEFAULT;
        desc.BindFlags = D3D11_BIND_DEPTH_STENCIL;
        require(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &texture.p)),
                "Create depth failed");
        require(SUCCEEDED(texture.p->SetPrivateData(WKPDID_D3DDebugObjectName,
                                                    static_cast<UINT>(std::strlen(name)), name)),
                "Set depth identity failed");
        D3D11_DEPTH_STENCIL_VIEW_DESC vd{};
        vd.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        vd.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
        require(SUCCEEDED(device->CreateDepthStencilView(texture.p, &vd, &view.p)),
                "Create depth view failed");
    }
};
void check_upload_forwarding(Test& t) {
    Com<ID3D11Buffer> dynamic, staging, destination;
    D3D11_BUFFER_DESC desc{};
    desc.ByteWidth = 64;
    desc.Usage = D3D11_USAGE_DYNAMIC;
    desc.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    require(SUCCEEDED(t.device.p->CreateBuffer(&desc, nullptr, &dynamic.p)), "Dynamic buffer");
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.CPUAccessFlags = 0;
    require(SUCCEEDED(t.device.p->CreateBuffer(&desc, nullptr, &destination.p)), "Default buffer");
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    require(SUCCEEDED(t.device.p->CreateBuffer(&desc, nullptr, &staging.p)), "Staging buffer");
    const auto verify = [&](ID3D11Buffer* source, unsigned char expected) {
        t.context.p->CopyResource(staging.p, source);
        D3D11_MAPPED_SUBRESOURCE mapped{};
        require(SUCCEEDED(t.context.p->Map(staging.p, 0, D3D11_MAP_READ, 0, &mapped)),
                "Read upload");
        bool same = true;
        for (unsigned i = 0; i < 64; ++i)
            same &= static_cast<const unsigned char*>(mapped.pData)[i] == expected;
        t.context.p->Unmap(staging.p, 0);
        require(same, "Upload hooks changed data or lost the original call");
    };
    for (auto* context : {t.context.p, t.deferred.p}) {
        D3D11_MAPPED_SUBRESOURCE mapped{};
        require(SUCCEEDED(context->Map(dynamic.p, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)),
                "Map upload");
        const unsigned char value = context == t.context.p ? 0x37 : 0xa5;
        std::memset(mapped.pData, value, 64);
        context->Unmap(dynamic.p, 0);
        std::array<unsigned char, 64> bytes{};
        bytes.fill(value);
        context->UpdateSubresource(destination.p, 0, nullptr, bytes.data(), 0, 0);
        if (context == t.deferred.p) {
            Com<ID3D11CommandList> list;
            require(SUCCEEDED(context->FinishCommandList(FALSE, &list.p)), "Finish upload");
            t.context.p->ExecuteCommandList(list.p, TRUE);
        }
        verify(dynamic.p, value);
        verify(destination.p, value);
    }
}
void check(D3D_DRIVER_TYPE driver) {
    Test t(driver);
    check_upload_forwarding(t);
    require(scene_target_name("scratchrendertarget_1118301577_17x3_17_1.vtex", 17, 3),
            "Reviewed scene target name no longer matches");
    require(scene_target_name("scratchrendertarget_222_17x3_3_9.vtex", 17, 3),
            "A same-family scene target variant was rejected");
    require(!scene_target_name("scratchrendertarget_222_17x4_3_9.vtex", 17, 3),
            "Wrong-resolution scene target was accepted");
    require(!scene_target_name("ui_depth", 17, 3), "Unrelated depth target was accepted");
    const char* scene_name = "scratchrendertarget_1118301577_17x3_17_1.vtex";
    Surface scene(t.device.p, scene_name), ui(t.device.p, "ui_depth"),
        other(t.device.p, scene_name);
    t.calibration(7);
    t.bind(t.context.p, scene.view.p);
    t.context.p->ClearDepthStencilView(scene.view.p, D3D11_CLEAR_DEPTH, 0, 0);
    t.context.p->Draw(3, 0);
    // Change the source constants after the draw. The selected projection must
    // come from the GPU copy at that draw, not from this later binding value.
    t.calibration(99);
    t.bind(t.context.p, ui.view.p);
    t.context.p->ClearDepthStencilView(ui.view.p, D3D11_CLEAR_DEPTH, 0, 0);
    t.context.p->Draw(3, 0);
    auto selected = t.tracker->consume(t.context.p);
    require(selected.texture() == scene.texture.p, "Late UI depth replaced the scene");
    t.read(selected, 14);
    Com<ID3D11DepthStencilView> still_bound;
    t.context.p->OMGetRenderTargets(0, nullptr, &still_bound.p);
    require(still_bound.p == ui.view.p, "Observation changed the game's render target");
    require(t.tracker->consume(t.context.p).result == SceneResult::missing,
            "Consumed frame reused stale depth");
    // Upscaling or resolution scaling renders the scene below the recording
    // size: a reviewed target at its own resolution with a full-target viewport
    // is verified, and its size is what the paired depth is captured at.
    Surface scaled(t.device.p, "scratchrendertarget_1118301577_19x5_1_1.vtex", 19, 5);
    t.calibration(7);
    t.bind(t.context.p, scaled.view.p, 1, 19, 5);
    t.context.p->ClearDepthStencilView(scaled.view.p, D3D11_CLEAR_DEPTH, 0, 0);
    t.context.p->Draw(3, 0);
    auto scaled_frame = t.tracker->consume(t.context.p);
    require(scaled_frame.result == SceneResult::ready, "A scaled scene target was rejected");
    require(scaled_frame.width == 19 && scaled_frame.height == 5,
            "Verified scene target size was not reported");
    require(std::strstr(scene_diagnostic(), "scene=19x5") != nullptr,
            "Verified scene size missing from the diagnostic");
    // The largest reviewed target wins within a frame whatever the draw order;
    // a smaller reviewed target never replaces the chosen scene.
    t.bind(t.context.p, other.view.p);
    t.context.p->Draw(3, 0);
    t.bind(t.context.p, scaled.view.p, 1, 19, 5);
    t.context.p->Draw(3, 0);
    auto upgraded = t.tracker->consume(t.context.p);
    require(upgraded.result == SceneResult::ready && upgraded.width == 19 && upgraded.height == 5,
            "A larger reviewed scene target did not supersede a smaller one");
    t.bind(t.context.p, scaled.view.p, 1, 19, 5);
    t.context.p->Draw(3, 0);
    t.bind(t.context.p, other.view.p);
    t.context.p->Draw(3, 0);
    auto kept = t.tracker->consume(t.context.p);
    require(kept.result == SceneResult::ready && kept.width == 19 && kept.height == 5,
            "A smaller reviewed target replaced the chosen scene");
    // A reviewed name whose resolution does not describe its texture stays
    // rejected instead of being trusted by family alone.
    Surface misnamed(t.device.p, "scratchrendertarget_1118301577_19x5_1_1.vtex", 17, 3);
    t.bind(t.context.p, misnamed.view.p);
    t.context.p->Draw(3, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::missing,
            "A scene target with a mismatched name resolution was accepted");
    // Exercise all explicit/indirect triangle entry points with slot zero.
    for (unsigned method = 0; method < 6; ++method) {
        t.calibration(7 + static_cast<float>(method));
        t.bind(t.context.p, scene.view.p, 0);
        t.context.p->ClearDepthStencilView(scene.view.p, D3D11_CLEAR_DEPTH, 0, 0);
        switch (method) {
        case 0:
            t.context.p->Draw(3, 0);
            break;
        case 1:
            t.context.p->DrawIndexed(3, 0, 0);
            break;
        case 2:
            t.context.p->DrawInstanced(3, 1, 0, 0);
            break;
        case 3:
            t.context.p->DrawIndexedInstanced(3, 1, 0, 0, 0);
            break;
        case 4:
            t.context.p->DrawInstancedIndirect(t.args.p, 0);
            break;
        case 5:
            t.context.p->DrawIndexedInstancedIndirect(t.indexed_args.p, 0);
            break;
        }
        t.read(t.tracker->consume(t.context.p), 2 * (7 + static_cast<float>(method)));
    }
    t.context.p->Draw(3, 0);
    t.context.p->ClearDepthStencilView(scene.view.p, D3D11_CLEAR_DEPTH, 0, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::cleared,
            "Late scene clear accepted as scene depth");
    t.context.p->Draw(3, 0);
    const D3D11_VIEWPORT subview{0, 0, 8, 3, 0, 1};
    t.context.p->RSSetViewports(1, &subview);
    t.context.p->Draw(3, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::incompatible_view,
            "Partial-viewport depth overwrite silently accepted");
    t.bind(t.context.p, scene.view.p);
    t.context.p->Draw(3, 0);
    t.context.p->OMSetDepthStencilState(nullptr, 0);
    t.context.p->Draw(3, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::incompatible_view,
            "Forward-Z depth overwrite silently accepted");
    require(std::strstr(scene_diagnostic(), "depth function=LESS") != nullptr,
            "Forward-Z rejection did not report the observed depth comparison");
    // A later full supported scene draw rewrites the target, so an earlier
    // unsupported pass on the same source must not poison the frame forever.
    t.bind(t.context.p, scene.view.p);
    t.context.p->Draw(3, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::ready,
            "A full scene pass did not recover from an incompatible draw");
    // EQUAL depth writes can only re-store values they compared equal to, so
    // they must not invalidate the verified scene sample or its calibration.
    t.bind(t.context.p, scene.view.p);
    t.calibration(17);
    t.context.p->Draw(3, 0);
    t.context.p->OMSetDepthStencilState(t.equal_state.p, 0);
    t.context.p->Draw(3, 0);
    t.read(t.tracker->consume(t.context.p), 34);
    require(std::strstr(scene_diagnostic(), "depth function=EQUAL") == nullptr,
            "An EQUAL depth write was treated as an incompatible overwrite");
    // An EQUAL-only frame has no verified pass to offer and stays missing.
    t.context.p->OMSetDepthStencilState(t.equal_state.p, 0);
    t.context.p->Draw(3, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::missing,
            "An EQUAL-only frame was accepted without a supported scene pass");
    // A supported draw we cannot calibrate (no per-view constants) used the
    // frame's camera, so it keeps the frame's verified calibration instead of
    // failing the whole frame.
    t.bind(t.context.p, scene.view.p);
    t.calibration(13);
    t.context.p->Draw(3, 0);
    {
        const std::array<ID3D11Buffer*, 14> empty{};
        t.context.p->VSSetConstantBuffers(0, 14, empty.data());
    }
    t.context.p->Draw(3, 0);
    t.read(t.tracker->consume(t.context.p), 26);
    t.bind(t.context.p, scene.view.p);
    t.calibration(7);
    t.context.p->Draw(3, 0);
    t.read(t.tracker->consume(t.context.p), 14);
    // An unsupported draw on a different family target never invalidates the
    // chosen scene; only supported draws from a second target are ambiguous.
    t.bind(t.context.p, scene.view.p);
    t.context.p->Draw(3, 0);
    t.bind(t.context.p, other.view.p);
    t.context.p->RSSetViewports(1, &subview);
    t.context.p->Draw(3, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::ready,
            "An unsupported draw on another family target poisoned the scene");
    t.bind(t.context.p, scene.view.p);
    t.context.p->Draw(3, 0);
    t.bind(t.context.p, other.view.p);
    t.context.p->Draw(3, 0);
    require(t.tracker->consume(t.context.p).result == SceneResult::ambiguous,
            "Duplicate scene identities silently selected");
    // Exceeding the per-frame observation budget must keep the newest state
    // instead of failing the tracker forever.
    for (int i = 0; i < 40; ++i) {
        t.bind(t.context.p, scene.view.p);
        t.context.p->Draw(3, 0);
        t.bind(t.context.p, other.view.p);
        t.context.p->Draw(3, 0);
    }
    require(t.tracker->consume(t.context.p).result == SceneResult::ambiguous,
            "Observation budget overflow poisoned the tracker");
    // Hundreds of calibration transitions on one target must compact instead of
    // overflowing the per-frame history. The newest state and a clean
    // diagnostic prove that no observation was silently dropped.
    for (int i = 0; i < 300; ++i) {
        t.bind(t.context.p, scene.view.p);
        t.calibration(9);
        t.context.p->Draw(3, 0);
        const std::array<ID3D11Buffer*, 14> empty{};
        t.context.p->VSSetConstantBuffers(0, 14, empty.data());
        t.context.p->Draw(3, 0);
    }
    t.bind(t.context.p, scene.view.p);
    t.calibration(15);
    t.context.p->Draw(3, 0);
    t.read(t.tracker->consume(t.context.p), 30);
    require(std::strstr(scene_diagnostic(), "why=event budget") == nullptr,
            "Compacted observation history still overflowed");
    require(std::strstr(scene_diagnostic(), "event=") != nullptr &&
                std::strstr(scene_diagnostic(), "targets=") != nullptr,
            "Scene diagnostic lacks event/target details");
    t.bind(t.context.p, scene.view.p);
    t.calibration(15);
    t.context.p->Draw(3, 0);
    t.read(t.tracker->consume(t.context.p), 30);
    t.calibration(11);
    t.bind(t.deferred.p, scene.view.p);
    t.deferred.p->ClearDepthStencilView(scene.view.p, D3D11_CLEAR_DEPTH, 0, 0);
    t.deferred.p->Draw(3, 0);
    Com<ID3D11CommandList> list;
    require(SUCCEEDED(t.deferred.p->FinishCommandList(FALSE, &list.p)), "Finish draw list failed");
    require(t.tracker->consume(t.context.p).result == SceneResult::missing,
            "Unexecuted command list selected");
    t.context.p->ExecuteCommandList(list.p, TRUE);
    t.read(t.tracker->consume(t.context.p), 22);
    t.calibration(13);
    t.context.p->ExecuteCommandList(list.p, TRUE);
    t.read(t.tracker->consume(t.context.p), 26);
    Com<ID3D11CommandList> clear_list;
    t.deferred.p->ClearDepthStencilView(scene.view.p, D3D11_CLEAR_DEPTH, 0, 0);
    require(SUCCEEDED(t.deferred.p->FinishCommandList(FALSE, &clear_list.p)),
            "Finish clear list failed");
    t.context.p->ExecuteCommandList(list.p, TRUE);
    t.context.p->ExecuteCommandList(clear_list.p, TRUE);
    require(t.tracker->consume(t.context.p).result == SceneResult::cleared,
            "Deferred late clear accepted");
    set_scene_tracker(nullptr);
    Com<ID3D11CommandList> unknown;
    t.deferred.p->ClearDepthStencilView(scene.view.p, D3D11_CLEAR_DEPTH, 0, 0);
    require(SUCCEEDED(t.deferred.p->FinishCommandList(FALSE, &unknown.p)),
            "Finish untracked list failed");
    set_scene_tracker(t.tracker);
    t.context.p->ExecuteCommandList(list.p, TRUE);
    t.context.p->ExecuteCommandList(unknown.p, TRUE);
    require(t.tracker->consume(t.context.p).result == SceneResult::untracked_commands,
            "Untracked command list silently accepted");
    t.tracker = std::make_shared<SceneTracker>(t.device.p, 17, 3);
    set_scene_tracker(t.tracker);
    t.context.p->ExecuteCommandList(list.p, TRUE);
    require(t.tracker->consume(t.context.p).result == SceneResult::untracked_commands,
            "Prior session command metadata reused");
}
}
int main(int argc, char**) {
    try {
        check_early_shader_metadata(argc > 1 ? D3D_DRIVER_TYPE_HARDWARE : D3D_DRIVER_TYPE_WARP);
        check(argc > 1 ? D3D_DRIVER_TYPE_HARDWARE : D3D_DRIVER_TYPE_WARP);
        std::puts(
            "DX11 scene draw hooks, deferred replay, calibration snapshots and late-clear rejection passed.");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
