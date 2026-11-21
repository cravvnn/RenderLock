#pragma once
#include "imgui.h"
namespace dolly::rail_fonts {
enum class Role { Body, BodyMedium, Small, Chip, Title, Count };
ImFont* Get(Role role);
float PixelSize(Role role);
float BaselineOffset(Role role, float line_height);
}
