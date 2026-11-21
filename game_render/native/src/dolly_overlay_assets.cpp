#include "dolly_overlay_assets.hpp"
#include <windows.h>
#include <d3d11.h>
#include <wincodec.h>
#include <vector>
#include <bcrypt.h>
#include <fstream>
#include <string>
#include <cstdint>

namespace dolly {
namespace {
float em_height(const void* memory, std::size_t length) {
    const auto* p = static_cast<const unsigned char*>(memory);
    const auto u16 = [&](std::size_t at) { return (unsigned(p[at]) << 8) | p[at + 1]; };
    const auto u32 = [&](std::size_t at) { return (std::uint32_t(u16(at)) << 16) | u16(at + 2); };
    if (length < 12)
        return 1;
    const auto count = u16(4);
    if (count > 64 || 12 + count * 16 > length)
        return 1;
    int asc = 0, desc = 0;
    unsigned upm = 0;
    for (unsigned i = 0; i < count; ++i) {
        const auto rec = 12 + i * 16, offset = u32(rec + 8);
        if (offset > length)
            continue;
        if (!memcmp(p + rec, "head", 4) && length - offset >= 20)
            upm = u16(offset + 18);
        if (!memcmp(p + rec, "hhea", 4) && length - offset >= 10) {
            asc = std::int16_t(u16(offset + 4));
            desc = std::int16_t(u16(offset + 6));
        }
    }
    const float ratio = upm ? float(asc - desc) / upm : 1;
    return ratio > .5f && ratio < 3 ? ratio : 1;
}
const void* resource(unsigned id, DWORD& size) {
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&load_rail_font), &module))
        return nullptr;
    const auto info = FindResourceW(module, MAKEINTRESOURCEW(id), MAKEINTRESOURCEW(10));
    if (!info)
        return nullptr;
    size = SizeofResource(module, info);
    return LockResource(LoadResource(module, info));
}
}
ImFont* load_rail_font(float size, bool heading) {
    DWORD length = 0;
    const void* data = resource(heading ? 102 : 101, length);
    if (!data || !length)
        return nullptr;
    ImFontConfig config;
    config.FontDataOwnedByAtlas = false;
    config.OversampleH = config.OversampleV = 2;
    return ImGui::GetIO().Fonts->AddFontFromMemoryTTF(const_cast<void*>(data), int(length),
                                                      size * em_height(data, length), &config);
}
ImFont* load_rail_medium(float size) {
    DWORD length = 0;
    const void* data = resource(104, length);
    if (!data || !length)
        return load_rail_font(size);
    ImFontConfig config;
    config.FontDataOwnedByAtlas = false;
    config.OversampleH = config.OversampleV = 2;
    return ImGui::GetIO().Fonts->AddFontFromMemoryTTF(const_cast<void*>(data), int(length),
                                                      size * em_height(data, length), &config);
}
ImFont* load_rail_title(float size) {
    // Oracle is supplied by the user's game, cached and verified by the desktop.
    // Never embed or redistribute Valve's private fonts in Dolly's DLL.
    constexpr char expected[] = "93ab96ee6775596bb196797fbe4bd2f86c7cf0264c11230be63d51cd9e223c34";
    wchar_t local[MAX_PATH]{};
    const auto length = GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH);
    if (!length || length >= MAX_PATH)
        return load_rail_font(size, true);
    std::wstring path = std::wstring(local) + L"\\DeadlockDolly\\ui-fonts-v1\\";
    for (char c : std::string(expected))
        path += wchar_t(c);
    path += L".ttf";
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file || file.tellg() != 155484)
        return load_rail_font(size, true);
    std::vector<unsigned char> bytes(155484);
    file.seekg(0);
    file.read(reinterpret_cast<char*>(bytes.data()), bytes.size());
    BCRYPT_ALG_HANDLE alg = nullptr;
    unsigned char digest[32]{};
    bool valid = false;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0) {
        valid = BCryptHash(alg, nullptr, 0, bytes.data(), ULONG(bytes.size()), digest, 32) >= 0;
        BCryptCloseAlgorithmProvider(alg, 0);
    }
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned i = 0; valid && i < 32; ++i)
        valid =
            hex[digest[i] >> 4] == expected[i * 2] && hex[digest[i] & 15] == expected[i * 2 + 1];
    if (!valid)
        return load_rail_font(size, true);
    void* owned = IM_ALLOC(bytes.size());
    memcpy(owned, bytes.data(), bytes.size());
    ImFontConfig config;
    config.OversampleH = config.OversampleV = 2;
    return ImGui::GetIO().Fonts->AddFontFromMemoryTTF(
        owned, int(bytes.size()), size * em_height(bytes.data(), bytes.size()), &config);
}
ID3D11ShaderResourceView* load_rail_background(ID3D11Device* device) noexcept {
    DWORD length = 0;
    const void* data = resource(103, length);
    if (!device || !data || !length)
        return nullptr;
    const HRESULT apartment = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    IWICImagingFactory* factory = nullptr;
    IWICStream* stream = nullptr;
    IWICBitmapDecoder* decoder = nullptr;
    IWICBitmapFrameDecode* frame = nullptr;
    IWICFormatConverter* converter = nullptr;
    ID3D11Texture2D* texture = nullptr;
    ID3D11ShaderResourceView* result = nullptr;
    try {
        HRESULT hr = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                      IID_PPV_ARGS(&factory));
        if (SUCCEEDED(hr))
            hr = factory->CreateStream(&stream);
        if (SUCCEEDED(hr))
            hr = stream->InitializeFromMemory(const_cast<BYTE*>(static_cast<const BYTE*>(data)),
                                              length);
        if (SUCCEEDED(hr))
            hr = factory->CreateDecoderFromStream(stream, nullptr, WICDecodeMetadataCacheOnLoad,
                                                  &decoder);
        if (SUCCEEDED(hr))
            hr = decoder->GetFrame(0, &frame);
        if (SUCCEEDED(hr))
            hr = factory->CreateFormatConverter(&converter);
        if (SUCCEEDED(hr))
            hr = converter->Initialize(frame, GUID_WICPixelFormat32bppRGBA, WICBitmapDitherTypeNone,
                                       nullptr, 0, WICBitmapPaletteTypeCustom);
        UINT width = 0, height = 0;
        if (SUCCEEDED(hr))
            hr = converter->GetSize(&width, &height);
        if (SUCCEEDED(hr) && width && height && width <= 4096 && height <= 4096) {
            std::vector<BYTE> pixels(std::size_t(width) * height * 4);
            hr = converter->CopyPixels(nullptr, width * 4, UINT(pixels.size()), pixels.data());
            D3D11_TEXTURE2D_DESC desc{};
            desc.Width = width;
            desc.Height = height;
            desc.MipLevels = desc.ArraySize = 1;
            desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
            desc.SampleDesc.Count = 1;
            desc.Usage = D3D11_USAGE_IMMUTABLE;
            desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
            D3D11_SUBRESOURCE_DATA initial{pixels.data(), width * 4, 0};
            if (SUCCEEDED(hr))
                hr = device->CreateTexture2D(&desc, &initial, &texture);
            if (SUCCEEDED(hr))
                device->CreateShaderResourceView(texture, nullptr, &result);
        }
    } catch (...) { /* Decoration failure leaves fully functional controls. */
    }
    if (texture)
        texture->Release();
    if (converter)
        converter->Release();
    if (frame)
        frame->Release();
    if (decoder)
        decoder->Release();
    if (stream)
        stream->Release();
    if (factory)
        factory->Release();
    if (SUCCEEDED(apartment))
        CoUninitialize();
    return result;
}
}
