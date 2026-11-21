#pragma once
#include <windows.h>
namespace dolly {
// A Source interface probe may release its LoadLibrary reference immediately.
// Keep worker code resident before creating any thread, not just before hooks.
inline bool pin_worker_module(const void* address) noexcept {
    HMODULE resident = nullptr;
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                              reinterpret_cast<LPCWSTR>(address), &resident) != 0;
}
}
