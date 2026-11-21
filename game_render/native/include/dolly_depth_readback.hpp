#pragma once
#include "dolly_depth.hpp"
#include <memory>
#include <vector>

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11Texture2D;
struct ID3D11Buffer;

namespace dolly::depth {
enum class ReadbackResult { ready, pending, full, empty, invalid, failed };
struct ProjectionBuffer {
    ID3D11Buffer* buffer = nullptr;
    std::uint32_t offset = 0, bytes = 0; // bytes=0 means remaining buffer bytes.
};
struct RawFrame {
    Frame frame;
    Format format = Format::d24s8;
    // Owned, tightly packed, top-down bytes. No GPU pointers cross to a writer.
    std::vector<unsigned char> pixels;
};
// One render-thread owner, serialized with resize. Three fixed-capacity slots;
// no waits, encoder calls or filesystem work. Source selection happens before
// enqueue: its metadata must describe the same scene sample as the color copy.
class Readback {
public:
    Readback();
    ~Readback();
    Readback(const Readback&) = delete;
    Readback& operator=(const Readback&) = delete;
    ReadbackResult enqueue(ID3D11Device* device, ID3D11DeviceContext* context,
                           ID3D11Texture2D* source, const Frame& frame,
                           const ProjectionBuffer* calibration = nullptr,
                           std::size_t calibration_count = 0) noexcept;
    ReadbackResult poll(ID3D11DeviceContext* context, RawFrame& output) noexcept;
    void reset() noexcept;
    std::size_t pending() const noexcept;
    long error() const noexcept;
    const char* diagnostic() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
}
