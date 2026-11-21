// Capture only the selected game's render streams with Windows process loopback.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <propidl.h>
#include <wrl/client.h>
#include <wrl/implements.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <string>
#include <thread>

using Microsoft::WRL::ComPtr;

class Activation final : public Microsoft::WRL::RuntimeClass<
        Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom>,
        Microsoft::WRL::FtmBase, IActivateAudioInterfaceCompletionHandler> {
public:
    Activation() : ready(CreateEventW(nullptr, FALSE, FALSE, nullptr)) {}
    ~Activation() { if (ready) CloseHandle(ready); }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation* operation) override {
        ComPtr<IUnknown> activated;
        HRESULT activation = E_FAIL;
        result = operation->GetActivateResult(&activation, activated.GetAddressOf());
        if (SUCCEEDED(result)) result = activation;
        if (SUCCEEDED(result)) result = activated.As(&client);
        SetEvent(ready);
        return S_OK;
    }
    HANDLE ready = nullptr;
    HRESULT result = E_FAIL;
    ComPtr<IAudioClient> client;
};

static void word(std::ofstream& out, std::uint16_t value) {
    out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}
static void dword(std::ofstream& out, std::uint32_t value) {
    out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}
static void header(std::ofstream& out, std::uint32_t bytes) {
    out.seekp(0);
    out.write("RIFF", 4); dword(out, bytes + 36);
    out.write("WAVEfmt ", 8); dword(out, 16);
    word(out, WAVE_FORMAT_IEEE_FLOAT); word(out, 2);
    dword(out, 48000); dword(out, 48000 * 2 * sizeof(float));
    word(out, 2 * sizeof(float)); word(out, 32);
    out.write("data", 4); dword(out, bytes);
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 3) return 2;
    const auto pid = static_cast<DWORD>(std::wcstoul(argv[1], nullptr, 10));
    if (!pid) return 2;
    const std::wstring path = argv[2];
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) return 3;
    auto activation = Microsoft::WRL::Make<Activation>();
    AUDIOCLIENT_ACTIVATION_PARAMS params{};
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    params.ProcessLoopbackParams.TargetProcessId = pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
    PROPVARIANT prop{};
    prop.vt = VT_BLOB;
    prop.blob.cbSize = sizeof(params);
    prop.blob.pBlobData = reinterpret_cast<BYTE*>(&params);
    ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
    hr = ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
             __uuidof(IAudioClient), &prop, activation.Get(), operation.GetAddressOf());
    if (SUCCEEDED(hr) && WaitForSingleObject(activation->ready, 10000) != WAIT_OBJECT_0)
        hr = HRESULT_FROM_WIN32(ERROR_TIMEOUT);
    if (SUCCEEDED(hr)) hr = activation->result;
    ComPtr<IAudioClient> client = activation->client;
    if (FAILED(hr)) { std::fprintf(stderr, "activate: %08lx\n", hr); CoUninitialize(); return 4; }
    WAVEFORMATEX format{};
    format.wFormatTag = WAVE_FORMAT_IEEE_FLOAT;
    format.nChannels = 2;
    format.nSamplesPerSec = 48000;
    format.wBitsPerSample = 32;
    format.nBlockAlign = 8;
    format.nAvgBytesPerSec = 48000 * 8;
    hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED,
             AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
             AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM, 0, 0, &format, nullptr);
    ComPtr<IAudioCaptureClient> capture;
    if (SUCCEEDED(hr)) hr = client->GetService(IID_PPV_ARGS(&capture));
    HANDLE samples = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (SUCCEEDED(hr)) hr = client->SetEventHandle(samples);
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (SUCCEEDED(hr) && !out) hr = E_FAIL;
    if (SUCCEEDED(hr)) { header(out, 0); hr = client->Start(); }
    if (FAILED(hr)) { std::fprintf(stderr, "start: %08lx\n", hr); CloseHandle(samples); CoUninitialize(); return 5; }
    std::fputs("READY\n", stdout); std::fflush(stdout);
    std::atomic<bool> stop{false};
    std::thread input([&] { (void)std::getchar(); stop = true; });
    std::uint64_t first_qpc_100ns = 0;
    std::uint64_t frames = 0;
    while (!stop) {
        WaitForSingleObject(samples, 100);
        UINT32 available = 0;
        while (SUCCEEDED(capture->GetNextPacketSize(&available)) && available) {
            BYTE* data = nullptr;
            UINT32 count = 0;
            DWORD flags = 0;
            UINT64 device_position = 0, qpc_100ns = 0;
            hr = capture->GetBuffer(&data, &count, &flags, &device_position, &qpc_100ns);
            if (FAILED(hr)) { stop = true; break; }
            if (!first_qpc_100ns) first_qpc_100ns = qpc_100ns;
            if (flags & AUDCLNT_BUFFERFLAGS_SILENT) {
                const float zero[2]{};
                for (UINT32 i = 0; i < count; ++i) out.write(reinterpret_cast<const char*>(zero), sizeof(zero));
            } else out.write(reinterpret_cast<const char*>(data), static_cast<std::streamsize>(count) * 8);
            frames += count;
            capture->ReleaseBuffer(count);
        }
    }
    if (input.joinable()) input.join();
    client->Stop();
    if (frames > UINT32_MAX / 8 || !first_qpc_100ns || !out) hr = E_FAIL;
    if (SUCCEEDED(hr)) { header(out, static_cast<std::uint32_t>(frames * 8)); out.flush(); }
    out.close();
    std::ofstream meta(path + L".clock.json", std::ios::trunc);
    meta << std::setprecision(15) << "{\"first_sample_qpc_seconds\":" <<
        static_cast<double>(first_qpc_100ns) / 10000000.0 <<
        ",\"frames\":" << frames << ",\"sample_rate\":48000}\n";
    CloseHandle(samples);
    CoUninitialize();
    if (FAILED(hr) || !meta) { std::fprintf(stderr, "capture: %08lx\n", hr); return 6; }
    return 0;
}
