#pragma once
#include <cstdint>

struct ID3D11DeviceContext;

namespace dolly::classify {
// Optional per-draw observation used to design layer filters. The probe only
// reads device state; it never changes rendering. Counters and the top
// signature table are published through the renderer diagnostics block.
void probe(bool enabled) noexcept;
bool probing() noexcept;
// Called by the D3D11 draw hooks before the original draw while probing.
// method: 0 indexed, 1 non-indexed, 2 indexed-instanced, 3 instanced,
// 4 automatic, 5/6 indirect.
void draw(ID3D11DeviceContext* context, unsigned method) noexcept;
// Compact, stable text for the graphics diagnostics (thread-safe copy).
void diagnostic(char* output, unsigned capacity) noexcept;
}
