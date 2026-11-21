#include <windows.h>
#include <cstdio>
int main(int argc,char** argv) {
    if(argc!=2)return 2;
    auto module=LoadLibraryA(argv[1]);
    if(!module)return 3;
    auto start=reinterpret_cast<HANDLE(*)(HANDLE)>(GetProcAddress(module,"StartHeldWorker"));
    if(!start)return 4;
    auto gate=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    auto thread=start(gate);
    if(!thread)return 5;
    FreeLibrary(module);
    HMODULE resident=nullptr;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                          reinterpret_cast<LPCWSTR>(start),&resident)) {
        std::puts("Worker module unloaded before its background thread completed.");
        return 1;
    }
    SetEvent(gate);
    if(WaitForSingleObject(thread,2000)!=WAIT_OBJECT_0)return 6;
    DWORD code=0;GetExitCodeThread(thread,&code);
    CloseHandle(thread);CloseHandle(gate);
    if(code!=42)return 7;
    std::puts("Engine-style FreeLibrary cannot unload the running worker module.");
    return 0;
}
