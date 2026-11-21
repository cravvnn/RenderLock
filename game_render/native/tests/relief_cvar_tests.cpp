#include "dolly_effects.hpp"
#include <array>
#include <climits>
#include <cstring>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#ifndef _MSC_VER
#define __fastcall
#endif
using namespace dolly;
struct CvarRef {
    std::uint64_t id = 0xffffffff;
    std::uintptr_t data = 0;
};
using FindCvarFn = void*(__fastcall*)(void*, std::uint64_t*, const char*, int);
using CvarDataFn = std::uintptr_t(__fastcall*)(void*, std::uint64_t);
using SetCvarFn = void(__fastcall*)(CvarRef*, int, const void*, void*, void*);
std::uintptr_t gCvar = 1, gTier0 = 0;
std::array<unsigned char, 0x80> storage{};
unsigned writes = 0;
static void* __fastcall find_cvar(void*, std::uint64_t* id, const char*, int) {
    *id = 1;
    return nullptr;
}
static std::uintptr_t __fastcall get_data(void*, std::uint64_t) {
    return reinterpret_cast<std::uintptr_t>(storage.data());
}
static void __fastcall set_cvar(CvarRef*, int, const void* value, void* current, void*) {
    ++writes;
    std::memcpy(current, value, 4);
}
FindCvarFn gFindCvar = find_cvar;
CvarDataFn gGetCvarData = get_data;
SetCvarFn gSetCvar = set_cvar;
static bool read_memory(std::uintptr_t at, void* out, std::size_t size) {
    std::memcpy(out, reinterpret_cast<const void*>(at), size);
    return true;
}
template <class T> static bool read_value(std::uintptr_t at, T& out) {
    return read_memory(at, &out, sizeof(out));
}
#include "../src/native_relief_win.hpp"
static void check(bool value) {
    if (!value)
        throw std::runtime_error("relief cvar owner contract failed");
}
static void prepare(unsigned type, unsigned setter) {
    storage = {};
    writes = 0;
    gTier0 = setter ? reinterpret_cast<std::uintptr_t>(gSetCvar) - setter : 0;
    const auto name = reinterpret_cast<std::uintptr_t>("r_wait_on_present");
    std::memcpy(storage.data(), &name, 8);
    const auto t = static_cast<std::uint16_t>(type);
    std::memcpy(storage.data() + 0x28, &t, 2);
}
int main() {
    try {
        for (unsigned setter : {0u, 0x20ffa0u, 0x20fe50u})
            for (unsigned kind : {0u, 3u, 2u}) {
                const unsigned runtime = setter ? (kind == 3 ? 5 : kind == 2 ? 9 : 0) : kind;
                prepare(runtime, setter);
                if (kind == 0)
                    storage[0x58] = 1;
                else if (kind == 3) {
                    const std::int32_t value = 37;
                    std::memcpy(storage.data() + 0x58, &value, 4);
                } else {
                    const float value = .375f;
                    std::memcpy(storage.data() + 0x58, &value, 4);
                }
                const auto original = storage;
                ReliefCvar owner;
                check(owner.bind("r_wait_on_present", 0));
                double before = 0;
                check(owner.read(before));
                check(before == (kind == 0 ? 1 : kind == 3 ? 37 : .375));
                check(owner.write(0));
                double during = 1;
                check(owner.read(during) && during == 0);
                check(owner.write(before));
                check(storage == original && writes == 2);
            }
        for (unsigned bad : {2u, 3u, 7u, 15u, 0xffffu}) {
            prepare(bad, 0x20fe50);
            ReliefCvar owner;
            check(!owner.bind("r_wait_on_present", 0));
            check(writes == 0 && !owner.bound);
        }
        prepare(5, 0x20fe50);
        storage[0x30] = 4;
        ReliefCvar blocked;
        check(!blocked.bind("r_wait_on_present", 0) && writes == 0);
        prepare(5, 0x20fe50);
        ReliefCvar wrong;
        check(!wrong.bind("another_setting", 0) && writes == 0);
        prepare(0, 0x20fe50);
        storage[0x58] = 2;
        ReliefCvar invalid_bool;
        check(invalid_bool.bind("r_wait_on_present", 0));
        double value = 0;
        check(!invalid_bool.read(value) && writes == 0);
        prepare(9, 0x20fe50);
        const float nan = std::numeric_limits<float>::quiet_NaN();
        std::memcpy(storage.data() + 0x58, &nan, 4);
        ReliefCvar invalid_float;
        check(invalid_float.bind("r_wait_on_present", 0));
        check(!invalid_float.read(value) && writes == 0);
        std::cout << "relief cvar owner type, write, restoration and rejection tests passed\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
