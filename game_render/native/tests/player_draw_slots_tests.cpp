// Exercise the actual private capture owner, not a parallel queue implementation.
// No configure(), hooks, game, scene memory or installed DLL is used.
#include "../src/dolly_player_capture.cpp"
#include <d3d11sdklayers.h>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <filesystem>
using namespace dolly::player_capture;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); std::exit(1); } } while(0)
int main() {
    Microsoft::WRL::ComPtr<ID3D11Device> device;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> context;
    CHECK(SUCCEEDED(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,
        D3D11_CREATE_DEVICE_DEBUG,nullptr,0,D3D11_SDK_VERSION,&device,nullptr,&context)));
    Microsoft::WRL::ComPtr<ID3D11InfoQueue> info;
    CHECK(SUCCEEDED(device.As(&info)));
    D3D11_BUFFER_DESC desc{}; desc.ByteWidth=32; desc.StructureByteStride=32;
    desc.MiscFlags=D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;desc.BindFlags=D3D11_BIND_SHADER_RESOURCE;
    Microsoft::WRL::ComPtr<ID3D11Buffer> records,ids;
    CHECK(SUCCEEDED(device->CreateBuffer(&desc,nullptr,&records)));
    unsigned id=0; D3D11_SUBRESOURCE_DATA init{&id,0,0};
    desc={};desc.ByteWidth=4;desc.BindFlags=D3D11_BIND_VERTEX_BUFFER;
    CHECK(SUCCEEDED(device->CreateBuffer(&desc,&init,&ids)));
    sequenceV3=sequenceV2=aggregateMode=true;
    captureOwnership.open();armed=true;
    auto submit=[&](unsigned serial,bool redirect=true,bool corrupt=false) {
        auto* slot=available_draw_slot(0,AggregateDrawLimit); CHECK(slot);
        Write draw(2),start(8); CHECK(draw.event && start.event);
        slot->used=true;slot->drawEvent=draw.event-events.data()+1;slot->startEvent=start.event-events.data()+1;
        eventSlots.retain(unsigned(slot->drawEvent-1));eventSlots.retain(unsigned(slot->startEvent-1));
        unsigned words[8]={serial,1,2,3,4,5,6,7};
        start.event->w[8]=0;
        for(unsigned n=0;n<8;++n)start.event->w[18+n]=words[n];
        if(corrupt)++words[7];
        context->UpdateSubresource(records.Get(),0,nullptr,words,0,0);
        slot->pending=slot->snapshot.begin(context.Get(),records.Get(),ids.Get(),0,0);
        CHECK(slot->pending);slot->redirected=redirect;
        ++colorFrames[0].draws;
        return slot;
    };
    auto poll=[&] {
        context->Flush(); // Test-owned WARP only; production stays nonblocking.
        auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
        while(ring_snapshots_pending(0) && std::chrono::steady_clock::now()<deadline) {
            gpu_poll(context.Get());std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        CHECK(!ring_snapshots_pending(0));
    };
    for(unsigned n=0;n<AggregateDrawLimit;++n)submit(n,false);
    CHECK(!available_draw_slot(0,AggregateDrawLimit)); // pending proof stays owned
    poll();CHECK(recordValid==AggregateDrawLimit);
    CHECK(!available_draw_slot(0,AggregateDrawLimit)); // GPU done, redirect still owns events
    for(unsigned n=0;n<AggregateDrawLimit;++n)gpuSlots[n].redirected=true;
    sequenceV3=false;CHECK(!available_draw_slot(0,AggregateDrawLimit));sequenceV3=true;
    // Continue one image far beyond the old limit, retaining all validated draws.
    for(unsigned batch=1;batch<8;++batch) {
        for(unsigned n=0;n<AggregateDrawLimit;++n)submit(batch*AggregateDrawLimit+n);
        poll();
    }
    CHECK(colorFrames[0].draws==1024 && recordValid==1024 && recordInvalid==0);
    CHECK(GpuSnapshot::pooled_bytes()==AggregateDrawLimit*36);
    auto* invalid=submit(1024,true,true);poll();
    CHECK(invalid->mismatch && !invalid->verified);
    // No rejected proof may be recycled or erased by successful neighbours.
    for(unsigned n=0;n<AggregateDrawLimit;++n)if(&gpuSlots[n]!=invalid)gpuSlots[n].redirected=false;
    CHECK(!available_draw_slot(0,AggregateDrawLimit));CHECK(invalid->mismatch);
    // Failed image ownership must retire as soon as GPU reads finish, even
    // when no successful seal/write can ever follow. An admitted callback
    // still prevents close, and failure diagnostics remain intact.
    colorFrames[0].prepared = true;
    colorFrames[0].truncated = true;
    captureOwnership.retain_prepared();
    sequenceStall = true;
    captureOwnership.begin_drain();
    {
        CaptureCallbackPin callback(captureOwnership, 12);
        CHECK(callback);
        CHECK(!captureOwnership.try_close(false));
        invalid->pending = true;
        colorFrames[0].capture.pending = true;
        retire_failed_images();
        CHECK(captureOwnership.prepared_pending());
        colorFrames[0].capture.pending = false;
        retire_failed_images();
        CHECK(captureOwnership.prepared_pending()); // snapshot still owns ring
        invalid->pending = false;
        colorFrames[0].capture.pending = true;
        retire_failed_images();
        CHECK(captureOwnership.prepared_pending()); // image still owns readback
        colorFrames[0].capture.pending = false;
        captureActive = true;
        lastTimeline = GetTickCount64();
        aggregateMode = false; // no new scene/image; completion-only Present
        present_boundary(context.Get());
        aggregateMode = true;
        captureActive = false;
        CHECK(!captureOwnership.prepared_pending());
        CHECK(!colorFrames[0].prepared && colorFrames[0].truncated && invalid->mismatch);
        CHECK(!captureOwnership.try_close(false));
        gpu_poll(context.Get()); // no double-retirement / counter underflow
        CHECK(!captureOwnership.prepared_pending());
    }
    sequenceWriterReady = true;
    framesSealed = 2; framesWritten = 1;
    CHECK(sequence_readbacks_drained()); // rejected sealed image need not be written
    sequenceStall = false;
    CHECK(!sequence_readbacks_drained()); // healthy capture still requires all images
    sequenceStall = true;
    CHECK(captureOwnership.try_close(false));
    CHECK(captureOwnership.finish_close());
    release_frame_slots(0);
    for(auto& slot:gpuSlots)slot.snapshot.release();
    CHECK(GpuSnapshot::pooled_bytes()==0);
    CHECK(!capture_sequence_complete(true,true,116,0,116,302,0,false,true,false));
    for(UINT64 n=0;n<info->GetNumStoredMessages();++n) {
        SIZE_T bytes=0;info->GetMessage(n,nullptr,&bytes);std::vector<unsigned char> storage(bytes);
        auto* message=reinterpret_cast<D3D11_MESSAGE*>(storage.data());info->GetMessage(n,message,&bytes);
        CHECK(message->Severity>D3D11_MESSAGE_SEVERITY_WARNING);
    }
    // Real pacing-file transport: terminal publication must bypass the 250ms
    // throttle after a progress report, even when cleanup finishes immediately.
    const auto output = std::filesystem::temp_directory_path() /
        (L"DollyPacingTerminal-" + std::to_wstring(GetCurrentProcessId()));
    CHECK(std::filesystem::create_directory(output));
    directory = output.wstring() + L"\\";
    captureRequest = "0123456789abcdef0123456789abcdef";
    captured = false; sequenceStall = false;
    publish_pacing();
    auto readPacing = [&] {
        std::ifstream input(output / "dolly_owner_pacing.txt");
        return std::string(std::istreambuf_iterator<char>(input), {});
    };
    CHECK(readPacing().find("\"error\":0") != std::string::npos);
    sequenceStall = true; captured = true;
    publish_pacing();
    CHECK(readPacing().find("\"error\":1") != std::string::npos);
    CHECK(readPacing().find("\"stage\":\"complete\"") != std::string::npos);
    CHECK(std::filesystem::remove(output / "dolly_owner_pacing.txt"));
    CHECK(std::filesystem::remove(output));
    armed=false;
    std::puts("PASS actual capture owner: 1024 draws/128 proof slots, exact GPU verification, redirect barrier, mismatch retained, pool released.");
}
