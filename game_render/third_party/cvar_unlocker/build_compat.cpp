#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <string>
#include <vector>
#include "icvar.h"
extern ICvar* cvar;
#include "build_compat.hpp"
#include "cvar_adapter_core.hpp"
#include "cvar_thunks.inc"
#pragma comment(lib, "bcrypt.lib")
static_assert(sizeof(ConVarRef)==8 && sizeof(ConCommandRef)==8);
static_assert(offsetof(ConCommandCreation_t, m_pszName)==0);
static_assert(offsetof(ConCommandCreation_t, m_pszHelpString)==8);
static_assert(offsetof(ConCommandCreation_t, m_nFlags)==0x10);
static_assert(offsetof(ConCommandCreation_t, m_CBInfo)==0x18);
static_assert(offsetof(ConCommandCreation_t, m_CompletionCBInfo)==0x28);
static_assert(offsetof(ConCommandData, m_pszHelpString)==8);
static_assert(offsetof(ConCommandData, m_nFlags)==0x10);
// The reviewed new tier0 removed this optional debug database export. Some
// pinned SDK static-library objects still reference its import pointer. Resolve
// that pointer locally so the Windows loader never requires a removed export.
// Legacy builds recover the original flag; absent export means no debug writes.
static bool g_NoTokenDatabase=false;
extern "C" { bool* __imp_g_bUpdateStringTokenDatabase=&g_NoTokenDatabase; }
static bool g_new_build=false;
static bool g_hotfix_server=false;
static bool g_latest_server=false;
static bool g_6726_server=false;
static bool g_6728_server=false;
static bool g_6730_server=false;
static bool g_6731_server=false;
static bool g_6739_server=false;
static bool g_6742_server=false;
static bool g_6745_server=false;
static bool g_6746_server=false;
static bool g_6753_server=false;
static bool g_6757_server=false;
static bool g_6759_server=false;
static bool g_6762_server=false;
static bool g_6763_server=false;
static bool g_6765_server=false;
static bool g_6766_server=false;
static bool g_6769_server=false;
static bool g_6774_server=false;
static bool g_6793_server=false;
static bool g_6801_server=false;
static bool g_6805_server=false;
static bool g_6806_server=false;
static bool g_6807_server=false;
static bool g_6810_server=false;
static bool g_6812_server=false;
static bool g_6813_server=false;
static bool g_6814_server=false;
static bool g_6815_server=false;
static bool g_6817_server=false;
static bool g_6819_server=false;
static bool g_6821_server=false;
static bool g_6826_server=false;
static bool g_6827_server=false;
static bool g_6828_server=false;
static bool g_6829_server=false;
static bool g_6831_server=false;
static bool g_6835_server=false;
static bool g_6837_server=false;
static bool g_6844_server=false;
static bool g_6848_server=false;
static bool g_6851_server=false;
static bool g_6853_server=false;
static bool g_6865_server=false;
static bool g_6864_server=false;
static bool g_6861_server=false;
static bool g_6860_server=false;
static bool g_6842_server=false;
static bool g_6839_server=false;
static bool g_6779_server=false;
static HMODULE g_real_server=nullptr;
static DollyCvarProxy g_proxy;
static constexpr DollyCvarSlot kSlots[]={
#include "cvar_slots.inc"
};
static constexpr DollyCvarSlot kSlots6861[]={
#include "cvar_slots_6861.inc"
};
static constexpr DollyCvarSlot kSlots6864[]={
#include "cvar_slots_6864.inc"
};
static std::string ModuleHash(HMODULE module) {
    wchar_t path[32768];
    DWORD length=module?GetModuleFileNameW(module,path,32768):0;
    if(!length || length>=32768) return {};
    HANDLE file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
    if(file==INVALID_HANDLE_VALUE) return {};
    BCRYPT_ALG_HANDLE alg=nullptr; BCRYPT_HASH_HANDLE hash=nullptr;
    DWORD object_size=0,received=0; bool ok=false;
    std::array<unsigned char,32> digest{};
    do {
        if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0) break;
        if(BCryptGetProperty(alg,BCRYPT_OBJECT_LENGTH,reinterpret_cast<PUCHAR>(&object_size),sizeof(object_size),&received,0)<0) break;
        std::vector<unsigned char> object(object_size);
        if(BCryptCreateHash(alg,&hash,object.data(),object_size,nullptr,0,0)<0) break;
        std::array<unsigned char,65536> data; bool read_ok=true;
        for(;;) {
            if(!ReadFile(file,data.data(),static_cast<DWORD>(data.size()),&received,nullptr)) { read_ok=false;break; }
            if(!received) break;
            if(BCryptHashData(hash,data.data(),received,0)<0) {read_ok=false;break;}
        }
        if(read_ok && BCryptFinishHash(hash,digest.data(),static_cast<ULONG>(digest.size()),0)>=0) ok=true;
        // The caller-provided object buffer must outlive the BCrypt handle.
        BCryptDestroyHash(hash);hash=nullptr;
    } while(false);
    if(hash) BCryptDestroyHash(hash);
    if(alg) BCryptCloseAlgorithmProvider(alg,0);
    CloseHandle(file);
    if(!ok) return {};
    const char* hex="0123456789abcdef";std::string result;
    for(auto v:digest){result+=hex[v>>4];result+=hex[v&15];}return result;
}
static void UnsupportedCvarMethod() { Plat_FatalError("Unreviewed ICvar method requested by Dolly unlocker."); }
bool DollyVerifyUnlockerBuild(void* real_server_module) {
    auto tier=GetModuleHandleW(L"tier0.dll");
    const auto hash=ModuleHash(tier);
    const bool tier6864=hash=="112d97c1380cff76a80d28eb8c2259fa2006113835b82e62ff948a6b26f0014c";
    const bool tier6861=hash=="29eae7e1e78245c64da8cb8ee7102df05cfc9c0c3181105d65c547f66e48bdf2";
    const bool tier6853=tier6864 || tier6861 || hash=="3eb86a30eba4e33140232a536555ae846cdba1ba45068635f3b6b33c49c10313";
    const bool tier6801=hash=="666c9022739190d571a67161646d1a55511e130b62c3a93f8c95b47beaede433";
    const bool tier6793=hash=="ec99a83124781f051cfdc74f536a6462d92cb1fb5d3b7053fe7d1427fa01d45b";
    const bool tier6779=hash=="784e692b1391809ba31c8fd8fddc121ad1950c36241411d198c2f1cbbb2c2eb6";
    const bool tier6726=tier6853 || tier6801 || tier6793 || tier6779 || hash=="6793cc7306ff40283ba038a784fe96f99f6d38f93c825571ee93ab6b9d9d5332";
    g_new_build=tier6726 || hash=="493bf3ca610bac0eec1369d19aa67f4c279c6ae1c8f1243b3bad0974c6a8db82";
    // Existing reviewed legacy builds continue to use the original SDK directly.
    if(!g_new_build && hash!="b4300eb0abfe73e1e877516ab6b8bdd1a1bdb4ffc47c7515a349d0623b852f69" &&
       hash!="b3192eac3cb8c54ac3f9c7aaf7c725ddfcc2dc46d99ba13d16177b6ebf736ebc") return false;
    if (!g_new_build) {
        auto flag=reinterpret_cast<bool*>(GetProcAddress(tier,"g_bUpdateStringTokenDatabase"));
        if (!flag) return false;
        __imp_g_bUpdateStringTokenDatabase=flag;
    }
    g_real_server=static_cast<HMODULE>(real_server_module);
    if (!g_new_build) return true;
    const auto server_hash=ModuleHash(g_real_server);
    g_hotfix_server=server_hash=="d8039b48395c79a5332939bfa25038732b14ab6fce541eefebb82f05c4a9abe0";
    g_latest_server=server_hash=="afd91d195ebcba6ecd8dd13aaf1f80973f58136d4b01365f92cf10b3d3fa1ccb";
    g_6726_server=server_hash=="1c8bd4615c494796b05826bd5fc55fed78cfcfc7a0b75a571145f6b34e6c3065";
    g_6728_server=server_hash=="28e1b1f66d07449e7a72015f7b7571ee8628195b99bd92fd5486942c5503d0ec";
    g_6730_server=server_hash=="81cac6d66cefa0467c040d68e62f579252448c8a8971f15a753696a1502ca966";
    g_6731_server=server_hash=="5226b3df209e197476b3f80944fc28f4eec0c0cb9b73b15eec11757a0917bcbe";
    g_6739_server=server_hash=="0c0b622de4913190a2c739cdd3eef4abb2336724f4314795554d82defb5e298e";
    g_6742_server=server_hash=="c57de2e3687a9d8b47216e583d0b91dafa2ce23f2ad14e364a7042f131cfed6d";
    g_6745_server=server_hash=="fbb80c06626074474b35add70ebeadb561199d6cecffaf1f907dd8bbef3620c6";
    g_6746_server=server_hash=="f568c9ba151794f2ee585c754147fdfb6d10e12e92f62a9ed1b275a341f9a80a";
    g_6753_server=server_hash=="df75c14dcd565bc7414d291859e7615f667401fdf85c45ad503f293233c42f08";
    g_6757_server=server_hash=="f79ce8db746c7b1894666cd42f4a2e992cc46ae84386ea2ef592e3bb7393ba3d";
    g_6759_server=server_hash=="40a89bbeec5bef8d9029c5f42e8a9dbfa017a230818bdf9c0ee60e312eeea38c";
    g_6762_server=server_hash=="af555c0f54c184b50ba66b492ffed342ba59d0836c1eeb81141180b3de6beaa6";
    g_6763_server=server_hash=="7f6b64356c5c82da36f02f98127a498fdd3fd33149bd9f355ac0eaddbdaa5134";
    g_6765_server=server_hash=="f304c46b52a414802ffdeb4e7bf1c9e303a4e764ecf3ff8a6e1d4aaf16d0e6dc";
    g_6766_server=server_hash=="72b843001bafdc45fa4e20547c6368f83cb9ab34da452bbfe37aa71695173c50";
    g_6769_server=server_hash=="4a7ec82fbabeb6632c68a54dc4f332f87f7eefd39164236baf8674506ddeb3a2";
    g_6774_server=server_hash=="9a97830b33a1b53dd766902c185d83e53c44924a5da60732331433c28dba0ba2";
    g_6779_server=server_hash=="10b577ce6eb0bc6df849d121dd39d51e5f594c2e61abf9b457562faf0e2cdf3f";
    g_6793_server=server_hash=="deba4a4f7742839763b2877ee8d490a0cadb4b626e4af7c85c3ac7ba4bc61a66";
    g_6801_server=server_hash=="402e32de2e702973eb92920261ecde5c850930dcc06c10d5bbd429a69cc92788";
    g_6805_server=server_hash=="350169906b5fdc5d0c3669468532cccd43dc59f099be5766752a13fd726f6939";
    g_6806_server=server_hash=="b6599285293acef26d0f827ca6dfc7e3743640b81313c25582cbd35c298f7c85";
    g_6807_server=server_hash=="5197333b5da46d3825c1cd80d66963eb2143d3b08acd962299cf20da1e25ca3f";
    g_6810_server=server_hash=="a5fe0b3d3735de7a892027d432956b04edeb29ed277ef0cc120ecbfcf03eb658";
    g_6812_server=server_hash=="44cc89e23aea9c7686730260a65cb3744a6294cc4b993b7507645f8130041312";
    g_6813_server=server_hash=="5bb81f638097cdcde8b1c870b80f3faf66faa933fbd3b84d665dc063dc5c8055";
    g_6814_server=server_hash=="1f5644e3ed72db1995b8165c64f4943df2c36d6210f0150f3977747e567772ab";
    g_6815_server=server_hash=="12c54351d5116abae35abca674956775b7f4dd9331105e314c75fcee62740dc4";
    g_6817_server=server_hash=="135e2bfe9a704278228d4729e382f299fc846ae35ffa36e5336ba4dc22ce3fee";
    g_6819_server=server_hash=="4855ef0712962f43aa4d9f623b258ada286d3e6f8fd2a2c8272984bfecf777e8";
    g_6821_server=server_hash=="1cc0a966a89dac2bf93c78315b2202a9ebb81c371fac7746162d8c345a786696";
    g_6826_server=server_hash=="1a13fb67ebd7fe46d6e037ee94c2316cdc2a72b3666b592c37df9777c59dbf8e";
    g_6827_server=server_hash=="c72236c180fb4cff5792ec48d279fa8ef366f2ed163b4478c90f0d47ce1b6b29";
    g_6828_server=server_hash=="e2ef2100d8fd63b4ef2096c721c1aa8c076cf9681da63d3c05c004c6052677e7";
    g_6829_server=server_hash=="768af979b130524acd7951d1be6ab2612611ef285559cb491704a0c8595ef4e3";
    g_6831_server=server_hash=="a5dbb635c06a254ae70be2c8c2398aafc2ef38a51556043b557ff5add3861dcd";
    g_6835_server=server_hash=="2eb67ece03fb8322732b6bd1b7c33f9980f644e1d50c6498af50d3c683883d22";
    g_6837_server=server_hash=="2da2f96df238b968ab8d6b5d5e327810a49b604bdeca5a893d967292556db42d";
    g_6848_server=server_hash=="cce86de118afc09868165f5c0e8bf1c8b23307db6fed52e8a5e4ad67f93d9009";
    g_6851_server=server_hash=="dd191d3ceafa49f5e92c3706d82c78f337c65ae176074e65c1ff8e9a83004407";
    // Build 6858 retains the reviewed 6853 Config001 layout and Tier0 ABI.
    g_6853_server=server_hash=="004e9bb537eb081e7383715400c41cf3899b23af4733697c1963daec7eb9516f" || server_hash=="a6d7a488bde35b02f600178b5da0d4924e74bae9918c07a84c5119d0bebf3866";
    g_6865_server=server_hash=="719a032ce107ed44f0f6fdd8b6279c720afa35af9bf943bc589573f32833c14b";
    g_6864_server=server_hash=="a091ffbc7f1f885674000d3f2353eecee62dc702e5a0475d5c81d47e25b2f7c7";
    if(tier6864 != (g_6865_server || g_6864_server)) return false;
    g_6861_server=server_hash=="86eefc584d5fd7fb17e8ee59d35061b47efc7cbe8c8ecee6b27490625fe241ed";
    if(tier6861 != g_6861_server) return false;
    g_6860_server=server_hash=="1415c83dadf8f8b0a72fc253df728804188b13c1ea22a877d43b98b1081cfd3b";
    if (tier6853 != (g_6853_server || g_6860_server || g_6861_server || g_6864_server || g_6865_server)) return false;
    g_6844_server=server_hash=="fbeec5547ac12562ec3d8216592e95797c7572278cbad18c469aacbc129829e4";
    g_6842_server=server_hash=="fb0de9a1d2789934c1310bdf56c6b8d762f1a864d806c0c5668dff4c02f220bb";
    g_6839_server=server_hash=="e7db4fca4c87f2994c6e5454dfcbf7c6d055b99b82c2c0d6ee727fc05113d324";
    if (tier6726 != (g_6865_server || g_6864_server || g_6861_server || g_6860_server || g_6853_server || g_6726_server || g_6728_server || g_6730_server || g_6731_server || g_6739_server || g_6742_server || g_6745_server || g_6746_server || g_6753_server || g_6757_server || g_6759_server || g_6762_server || g_6763_server || g_6765_server || g_6766_server || g_6769_server || g_6774_server || g_6779_server || g_6793_server || g_6801_server || g_6805_server || g_6806_server || g_6807_server || g_6810_server || g_6812_server || g_6813_server || g_6814_server || g_6815_server || g_6817_server || g_6819_server || g_6821_server || g_6826_server || g_6827_server || g_6828_server || g_6829_server || g_6831_server || g_6835_server || g_6837_server || g_6844_server || g_6848_server || g_6851_server || g_6842_server || g_6839_server)) return false;
    if (tier6793 != g_6793_server) return false;
    if ((tier6801 || tier6853) != (g_6865_server || g_6864_server || g_6861_server || g_6860_server || g_6853_server || g_6801_server || g_6805_server || g_6806_server || g_6807_server || g_6810_server || g_6812_server || g_6813_server || g_6814_server || g_6815_server || g_6817_server || g_6819_server || g_6821_server || g_6826_server || g_6827_server || g_6828_server || g_6829_server || g_6831_server || g_6835_server || g_6837_server || g_6844_server || g_6848_server || g_6851_server || g_6842_server || g_6839_server)) return false;
    if (tier6779 != g_6779_server) return false;
    return g_6865_server || g_6864_server || g_6861_server || g_6860_server || g_6853_server || g_6851_server || g_6848_server || g_6844_server || g_6842_server || g_6839_server || g_6837_server || g_6835_server || g_6831_server || g_6829_server || g_6828_server || g_6827_server || g_6826_server || g_6821_server || g_6819_server || g_6817_server || g_6815_server || g_6814_server || g_6813_server || g_6812_server || g_6810_server || g_6807_server || g_6806_server || g_6805_server || g_6801_server || g_6793_server || g_6779_server || g_6774_server || g_6769_server || g_6766_server || g_6765_server || g_6763_server || g_6762_server || g_6759_server || g_6757_server || g_6753_server || g_6746_server || g_6745_server || g_6742_server || g_6739_server || g_6731_server || g_6730_server || g_6728_server || g_6726_server || g_latest_server || g_hotfix_server || server_hash=="cb480d4091735abe7b74695c8c8077a882528aaaaa9f457d81fbca886b6c9bd8";
}
bool DollyVerifyServerConfig(void* config) {
    if(!config) return false;
    if(!g_new_build) return true;
    auto base=reinterpret_cast<std::uintptr_t>(g_real_server);
    auto table=*static_cast<std::uintptr_t**>(config);
    if (g_6865_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b9f178 && table[0]==base+0x9abdc4 && table[1]==base+0x9abdd0 && table[13]==base+0x9b18b0;
    if (g_6864_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b9f068 && table[0]==base+0x9ab904 && table[1]==base+0x9ab910 && table[13]==base+0x9b13f0;
    if (g_6861_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b9ff78 && table[0]==base+0x9ab994 && table[1]==base+0x9ab9a0 && table[13]==base+0x9b1480;
    if (g_6860_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b9ff68 && table[0]==base+0x9ab9d4 && table[1]==base+0x9ab9e0 && table[13]==base+0x9b14c0;
    if (g_6853_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b9ff78 && table[0]==base+0x9ab9c4 && table[1]==base+0x9ab9d0 && table[13]==base+0x9b14b0;
    if (g_6851_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b98978 && table[0]==base+0x9b39b4 && table[1]==base+0x9b39c0 && table[13]==base+0x9b0380;
    if (g_6848_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b98948 && table[0]==base+0x9b3454 && table[1]==base+0x9b3460 && table[13]==base+0x9afe20;
    if (g_6844_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b98938 && table[0]==base+0x9b3464 && table[1]==base+0x9b3470 && table[13]==base+0x9afe30;
    if (g_6842_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b98938 && table[0]==base+0x9b3464 && table[1]==base+0x9b3470 && table[13]==base+0x9afe30;
    if (g_6839_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b97908 && table[0]==base+0x9b34a4 && table[1]==base+0x9b34b0 && table[13]==base+0x9afe70;
    if (g_6837_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b978b8 && table[0]==base+0x9b33c4 && table[1]==base+0x9b33d0 && table[13]==base+0x9afd90;
    if (g_6835_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b978a8 && table[0]==base+0x9b3154 && table[1]==base+0x9b3160 && table[13]==base+0x9afb20;
    if (g_6831_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b97910 && table[0]==base+0x9b2fb4 && table[1]==base+0x9b2fc0 && table[13]==base+0x9af980;
    if (g_6829_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b97910 && table[0]==base+0x9b2fb4 && table[1]==base+0x9b2fc0 && table[13]==base+0x9af980;
    if (g_6828_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b97910 && table[0]==base+0x9b2fb4 && table[1]==base+0x9b2fc0 && table[13]==base+0x9af980;
    if (g_6827_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b97910 && table[0]==base+0x9b2fb4 && table[1]==base+0x9b2fc0 && table[13]==base+0x9af980;
    if (g_6826_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b95730 && table[0]==base+0x9b2ab4 && table[1]==base+0x9b2ac0 && table[13]==base+0x9af480;
    if (g_6821_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b58150 && table[0]==base+0x99c1a4 && table[1]==base+0x99c1b0 && table[13]==base+0x999300;
    if (g_6819_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b5a1b0 && table[0]==base+0x99d9e4 && table[1]==base+0x99d9f0 && table[13]==base+0x99ab40;
    if (g_6817_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b5a1b0 && table[0]==base+0x99d9e4 && table[1]==base+0x99d9f0 && table[13]==base+0x99ab40;
    if (g_6815_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b5a1b0 && table[0]==base+0x99d7e4 && table[1]==base+0x99d7f0 && table[13]==base+0x99a940;
    if (g_6814_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b5a1b0 && table[0]==base+0x99d7e4 && table[1]==base+0x99d7f0 && table[13]==base+0x99a940;
    if (g_6813_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b591b0 && table[0]==base+0x99d7e4 && table[1]==base+0x99d7f0 && table[13]==base+0x99a940;
    if (g_6812_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b591b0 && table[0]==base+0x99d7d4 && table[1]==base+0x99d7e0 && table[13]==base+0x99a930;
    if (g_6810_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b581a0 && table[0]==base+0x99d7d4 && table[1]==base+0x99d7e0 && table[13]==base+0x99a930;
    if (g_6801_server || g_6805_server || g_6806_server || g_6807_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b58210 && table[0]==base+0x99d7e4 && table[1]==base+0x99d7f0 && table[13]==base+0x99a940;
    if (g_6793_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2b215e8 && table[0]==base+0x995a64 && table[1]==base+0x995a70 && table[13]==base+0x995f00;
    if (g_6779_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a9e040 && table[0]==base+0x98b784 && table[1]==base+0x98b790 && table[13]==base+0x98bc20;
    if (g_6774_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a5efc0 && table[0]==base+0x989e24 && table[1]==base+0x989e30 && table[13]==base+0x98a2c0;
    if (g_6769_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a5efc0 && table[0]==base+0x989e24 && table[1]==base+0x989e30 && table[13]==base+0x98a2c0;
    if (g_6766_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a1ae90 && table[0]==base+0x987724 && table[1]==base+0x987730 && table[13]==base+0x987bc0;
    if (g_6765_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a1ae80 && table[0]==base+0x987744 && table[1]==base+0x987750 && table[13]==base+0x987be0;
    if (g_6763_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a1aeb8 && table[0]==base+0x987704 && table[1]==base+0x987710 && table[13]==base+0x987ba0;
    if (g_6762_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a1aeb8 && table[0]==base+0x987704 && table[1]==base+0x987710 && table[13]==base+0x987ba0;
    if (g_6759_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a1be98 && table[0]==base+0x9876c4 && table[1]==base+0x9876d0 && table[13]==base+0x987b60;
    if (g_6757_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2a1be98 && table[0]==base+0x9876c4 && table[1]==base+0x9876d0 && table[13]==base+0x987b60;
    if (g_6753_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x29d6348 && table[0]==base+0x978460 && table[1]==base+0x97846c && table[13]==base+0x97a020;
    if (g_6746_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x29d1f08 && table[0]==base+0x976900 && table[1]==base+0x97690c && table[13]==base+0x978380;
    if (g_6745_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x29d0f08 && table[0]==base+0x976900 && table[1]==base+0x97690c && table[13]==base+0x978380;
    if (g_6742_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x29d0f28 && table[0]==base+0x976890 && table[1]==base+0x97689c && table[13]==base+0x978310;
    if (g_6739_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x29d0f28 && table[0]==base+0x976890 && table[1]==base+0x97689c && table[13]==base+0x978310;
    if (g_6731_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x299b5f0 && table[0]==base+0x97cd2c && table[1]==base+0x97cd38 && table[13]==base+0x97f420;
    if (g_6730_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x299b360 && table[0]==base+0x97ccdc && table[1]==base+0x97cce8 && table[13]==base+0x97f3c0;
    if (g_6728_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x299b370 && table[0]==base+0x97cd1c && table[1]==base+0x97cd28 && table[13]==base+0x97f400;
    if (g_6726_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x299b388 && table[0]==base+0x97c91c && table[1]==base+0x97c928 && table[13]==base+0x97f000;
    if (g_latest_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2995cc0 &&
               table[0]==base+0x97ba84 && table[1]==base+0x97ba90 && table[13]==base+0x97c570;
    if (g_hotfix_server)
        return reinterpret_cast<std::uintptr_t>(table)==base+0x2994d20 &&
               table[0]==base+0x97b5e4 && table[1]==base+0x97b5f0 && table[13]==base+0x97c0d0;
    return reinterpret_cast<std::uintptr_t>(table)==base+0x2994b70 &&
           table[0]==base+0x97b5f4 && table[1]==base+0x97b600 && table[13]==base+0x97c0e0;
}
bool DollyAdaptCvarInterface() {
    if(!g_new_build) return g_pCVar!=nullptr;
    if(!g_pCVar || cvar!=g_pCVar) return false;
    const auto base=reinterpret_cast<std::uintptr_t>(GetModuleHandleW(L"tier0.dll"));
    if(!DollyConfigureCvarProxy(g_proxy,g_pCVar,reinterpret_cast<void**>(base+((g_6865_server || g_6864_server) ? 0x313208 : g_6861_server ? 0x313208 : 0x313208)),base,
        (g_6865_server || g_6864_server) ? kSlots6864 : g_6861_server ? kSlots6861 : kSlots,sizeof(kSlots)/sizeof(kSlots[0]),kDollyThunks,
        reinterpret_cast<void*>(&UnsupportedCvarMethod),reinterpret_cast<void*>(&DollyNewMaxSplitScreenSlots))) return false;
    g_pCVar=cvar=reinterpret_cast<ICvar*>(&g_proxy);
    return true;
}
