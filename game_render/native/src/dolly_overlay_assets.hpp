#pragma once
#include "imgui.h"
struct ID3D11Device;
struct ID3D11ShaderResourceView;
namespace dolly {
ImFont* load_rail_font(float size, bool heading = false);
ImFont* load_rail_medium(float size);
ImFont* load_rail_title(float size);
ID3D11ShaderResourceView* load_rail_background(ID3D11Device* device) noexcept;
}
