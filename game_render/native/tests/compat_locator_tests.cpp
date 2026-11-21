// Portable tests for the compatibility locator algorithms.
//
// On non-Windows hosts the few Windows types the resolver needs are stubbed so
// the algorithms can run against a synthetic in-memory PE image. On Windows the
// real SDK types are used and the same synthetic image is exercised. The
// resolvers only read memory, so both paths run the production code.
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <vector>

#if defined(_WIN32) && !defined(DOLLY_COMPAT_TEST_STUBS)
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#else
using HMODULE = const unsigned char*;
struct IMAGE_DOS_HEADER {
    std::uint16_t e_magic;
    unsigned char e_pad[0x3A];
    std::int32_t e_lfanew;
};
struct IMAGE_FILE_HEADER {
    std::uint16_t Machine;
    std::uint16_t NumberOfSections;
    std::uint32_t TimeDateStamp;
    std::uint32_t PointerToSymbolTable;
    std::uint32_t NumberOfSymbols;
    std::uint16_t SizeOfOptionalHeader;
    std::uint16_t Characteristics;
};
struct IMAGE_DATA_DIRECTORY {
    std::uint32_t VirtualAddress;
    std::uint32_t Size;
};
struct IMAGE_OPTIONAL_HEADER64 {
    std::uint16_t Magic;
    std::uint8_t MajorLinkerVersion, MinorLinkerVersion;
    std::uint32_t SizeOfCode, SizeOfInitializedData, SizeOfUninitializedData;
    std::uint32_t AddressOfEntryPoint, BaseOfCode;
    std::uint64_t ImageBase;
    std::uint32_t SectionAlignment, FileAlignment;
    std::uint16_t MajorOperatingSystemVersion, MinorOperatingSystemVersion;
    std::uint16_t MajorImageVersion, MinorImageVersion;
    std::uint16_t MajorSubsystemVersion, MinorSubsystemVersion;
    std::uint32_t Win32VersionValue, SizeOfImage, SizeOfHeaders, CheckSum;
    std::uint16_t Subsystem, DllCharacteristics;
    std::uint64_t SizeOfStackReserve, SizeOfStackCommit, SizeOfHeapReserve, SizeOfHeapCommit;
    std::uint32_t LoaderFlags, NumberOfRvaAndSizes;
    IMAGE_DATA_DIRECTORY DataDirectory[16];
};
struct IMAGE_NT_HEADERS64 {
    std::uint32_t Signature;
    IMAGE_FILE_HEADER FileHeader;
    IMAGE_OPTIONAL_HEADER64 OptionalHeader;
};
union IMAGE_SECTION_MISC {
    std::uint32_t PhysicalAddress;
    std::uint32_t VirtualSize;
};
struct IMAGE_SECTION_HEADER {
    unsigned char Name[8];
    IMAGE_SECTION_MISC Misc;
    std::uint32_t VirtualAddress;
    std::uint32_t SizeOfRawData;
    std::uint32_t PointerToRawData;
    std::uint32_t PointerToRelocations;
    std::uint32_t PointerToLinenumbers;
    std::uint16_t NumberOfRelocations;
    std::uint16_t NumberOfLinenumbers;
    std::uint32_t Characteristics;
};
#define IMAGE_DOS_SIGNATURE 0x5A4D
#define IMAGE_NT_SIGNATURE 0x00004550
#define IMAGE_FIRST_SECTION(ntheader)                                                            \
    (reinterpret_cast<IMAGE_SECTION_HEADER*>(reinterpret_cast<unsigned char*>(ntheader) + 24 +   \
                                             (ntheader)->FileHeader.SizeOfOptionalHeader))
#endif

static bool module_matches(HMODULE, const char*, std::uint32_t) { return false; }
static const unsigned char kSetupPrologue[32] = {
    0x48, 0x8b, 0xc4, 0x48, 0x89, 0x58, 0x10, 0x55, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56,
    0x41, 0x57, 0x48, 0x81, 0xec, 0xc0, 0x08, 0x00, 0x00, 0x0f, 0x29, 0x70, 0xb8, 0x4c, 0x8b, 0xf9};

#include "dolly_compat_runtime.hpp"

static int failures = 0;
static void check(bool condition, const char* message) {
    if (!condition) {
        std::printf("FAIL: %s\n", message);
        ++failures;
    }
}

static constexpr std::uint32_t kText = 0x1000, kRdata = 0x2000, kData = 0x3000;
static constexpr std::uint32_t kSetup = 0x1100;
static constexpr std::uint32_t kCallerReturn = 0x1015;

struct Image {
    std::vector<unsigned char> buffer = std::vector<unsigned char>(0x5000);

    unsigned char* at(std::uint32_t va) { return buffer.data() + va; }

    template <typename T> void put(std::uint32_t va, T value) {
        std::memcpy(at(va), &value, sizeof(value));
    }
    void bytes(std::uint32_t va, std::initializer_list<unsigned char> values) {
        std::size_t index = 0;
        for (unsigned char value : values)
            at(va)[index++] = value;
    }
    std::uint32_t displacement(std::uint32_t instruction, std::uint32_t operand_end,
                               std::uint32_t target) {
        return static_cast<std::uint32_t>(static_cast<std::int64_t>(target) -
                                          static_cast<std::int64_t>(instruction + operand_end));
    }

    Image() {
        IMAGE_DOS_HEADER dos{};
        dos.e_magic = IMAGE_DOS_SIGNATURE;
        dos.e_lfanew = 0x40;
        std::memcpy(at(0), &dos, sizeof(dos));
        IMAGE_NT_HEADERS64 nt{};
        nt.Signature = IMAGE_NT_SIGNATURE;
        nt.FileHeader.NumberOfSections = 3;
        nt.FileHeader.SizeOfOptionalHeader = sizeof(IMAGE_OPTIONAL_HEADER64);
        std::memcpy(at(0x40), &nt, sizeof(nt));
        auto section = IMAGE_FIRST_SECTION(
            reinterpret_cast<IMAGE_NT_HEADERS64*>(at(0x40)));
        const char* names[3] = {".text", ".rdata", ".data"};
        const std::uint32_t base[3] = {kText, kRdata, kData};
        for (int i = 0; i < 3; ++i) {
            std::memcpy(section[i].Name, names[i], std::strlen(names[i]));
            section[i].Misc.VirtualSize = 0x1000;
            section[i].VirtualAddress = base[i];
        }

        // main view setup + its unique direct caller
        std::memcpy(at(kSetup), kSetupPrologue, sizeof(kSetupPrologue));
        bytes(kText + 0x10, {0xE8, 0xEB, 0x00, 0x00, 0x00});

        // SetGlobals: lea r8,[rip+fallback]; ... mov [rip+pointer], rdx
        bytes(0x11F0, {0x4C, 0x8D, 0x05,
                       static_cast<unsigned char>(displacement(0x11F0, 7, 0x3200) & 0xFF),
                       static_cast<unsigned char>((displacement(0x11F0, 7, 0x3200) >> 8) & 0xFF),
                       static_cast<unsigned char>((displacement(0x11F0, 7, 0x3200) >> 16) & 0xFF),
                       static_cast<unsigned char>((displacement(0x11F0, 7, 0x3200) >> 24) & 0xFF)});
        bytes(0x1200, {0xC7, 0x05, 0x00, 0x00, 0x00, 0x00, 0x89, 0x88, 0x88, 0x3C});
        std::uint32_t pointer_disp = displacement(0x1210, 7, 0x3100);
        bytes(0x1210, {0x48, 0x89, 0x15,
                       static_cast<unsigned char>(pointer_disp & 0xFF),
                       static_cast<unsigned char>((pointer_disp >> 8) & 0xFF),
                       static_cast<unsigned char>((pointer_disp >> 16) & 0xFF),
                       static_cast<unsigned char>((pointer_disp >> 24) & 0xFF)});

        // aspect source: mov rcx,[rip+engine client]; mov rax,[rcx]; call [rax+0x2b0]
        std::uint32_t client_disp = displacement(0x1400, 7, 0x3300);
        bytes(0x1400, {0x48, 0x8B, 0x0D,
                       static_cast<unsigned char>(client_disp & 0xFF),
                       static_cast<unsigned char>((client_disp >> 8) & 0xFF),
                       static_cast<unsigned char>((client_disp >> 16) & 0xFF),
                       static_cast<unsigned char>((client_disp >> 24) & 0xFF)});
        bytes(0x1410, {0x48, 0x8B, 0x01, 0xFF, 0x90, 0xB0, 0x02, 0x00, 0x00});

        // RTTI: mangled name -> type descriptor -> complete object locator -> vtable
        const char mangled[] = ".?AVCViewRender@@";
        std::memcpy(at(0x2100), mangled, sizeof(mangled));
        auto base_address = reinterpret_cast<std::uint64_t>(buffer.data());
        std::uint32_t descriptor_rva = 0x2100 - 0x10;
        std::uint32_t col_rva = 0x2200;
        put<std::uint32_t>(col_rva + 0, 1);
        put<std::uint32_t>(col_rva + 4, 0);
        put<std::uint32_t>(col_rva + 8, 0);
        put<std::uint32_t>(col_rva + 0xC, descriptor_rva);
        put<std::uint64_t>(0x3400, base_address + col_rva);
    }
};

int main() {
    Image image;
    HMODULE module = reinterpret_cast<HMODULE>(image.buffer.data());

    std::uintptr_t start = 0;
    std::size_t size = 0;
    check(compat_detail::section_range(module, ".text", start, size) && size == 0x1000,
          "section_range finds .text");
    check(start == reinterpret_cast<std::uintptr_t>(image.buffer.data()) + kText,
          "section_range maps .text to its virtual address");
    check(compat_detail::section_range(module, ".missing", start, size) == false,
          "section_range rejects a missing section");

    bool unique = false;
    auto caller = compat_detail::locate_caller(
        reinterpret_cast<std::uintptr_t>(module) + kText, 0x1000,
        reinterpret_cast<std::uintptr_t>(module) + kSetup, unique);
    check(unique, "locate_caller finds a unique direct call");
    check(caller == reinterpret_cast<std::uintptr_t>(module) + kCallerReturn,
          "locate_caller returns the call return address");

    std::uintptr_t fallback = 0;
    auto globals = compat_detail::locate_globals(
        reinterpret_cast<std::uintptr_t>(module) + kText, 0x1000, fallback);
    check(globals == reinterpret_cast<std::uintptr_t>(module) + 0x3100,
          "locate_globals finds the SetGlobals store target");
    check(fallback == reinterpret_cast<std::uintptr_t>(module) + 0x3200,
          "locate_globals finds the fallback pointer");

    auto engine_client = compat_detail::locate_engine_client(
        reinterpret_cast<std::uintptr_t>(module) + kSetup, 0x860);
    check(engine_client == reinterpret_cast<std::uintptr_t>(module) + 0x3300,
          "locate_engine_client finds the aspect source pointer");

    auto vtable = compat_detail::locate_vtable(module, "CViewRender");
    check(vtable == reinterpret_cast<std::uintptr_t>(module) + 0x3408,
          "locate_vtable resolves the vtable through RTTI");

    CompatResolution unresolved = resolve_client_profile(module, true);
    check(!unresolved.resolved, "resolve_client_profile fails closed with no matching profile");

    if (failures) {
        std::printf("%d compatibility locator test(s) failed\n", failures);
        return 1;
    }
    std::printf("compatibility locator tests passed\n");
    return 0;
}
