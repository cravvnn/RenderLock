#include <windows.h>
#include "dolly_module_residency.hpp"
static DWORD WINAPI held_worker(void* gate) {
    return WaitForSingleObject(static_cast<HANDLE>(gate), 3000) == WAIT_OBJECT_0 ? 42 : 1;
}
extern "C" __declspec(dllexport) HANDLE StartHeldWorker(HANDLE gate) {
    if (!dolly::pin_worker_module(reinterpret_cast<const void*>(&held_worker))) return nullptr;
    return CreateThread(nullptr,0,held_worker,gate,0,nullptr);
}
