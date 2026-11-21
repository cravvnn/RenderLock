#include "dolly_effects.hpp"
#include <fstream>
#include <iostream>
#include <iomanip>
#include <iterator>
#include <cstring>
#include <stdexcept>
using namespace dolly;
static void check(bool okay) {
    if (!okay)
        throw std::runtime_error("Native effect regression failed");
}
int main(int argc, char** argv) {
    try {
        if (argc == 1) {
            check(effect_cvar_type(0, true) == 0);
            check(effect_cvar_type(3, true) == 5);
            check(effect_cvar_type(7, true) == 9);
            check(effect_cvar_type(13, true) == 15);
            for (unsigned old_type : {0u, 3u, 7u, 13u})
                check(effect_cvar_type(old_type, false) == old_type);
            const std::array<double, 4> ranges{-150, 20, 250, 2500};
            check(effective_dof_ranges(ranges, true, false) == std::array<double, 4>{});
            check(effective_dof_ranges(ranges, false, false) == ranges);
            check(effective_dof_ranges(ranges, true, true) == ranges);
            check(effective_dof_ranges(ranges, false, true) == ranges);
            EffectTrack t;
            t.id = 1;
            t.first_time = 0;
            t.last_time = 2;
            t.first = 100;
            t.last = 300;
            t.segments.push_back({0, 2, 1, 1, 100, 300, 0, 0});
            double v = 0;
            check(t.evaluate(1, v) && v == 200);
            check(t.evaluate(-1, v) && v == 100);
            check(t.evaluate(3, v) && v == 300);
            t.segments[0].kind = 0;
            check(t.evaluate(1.99, v) && v == 100);
            check(t.evaluate(2, v) && v == 300);
            t.segments[0].kind = 2;
            t.segments[0].left_derivative = 10000;
            t.segments[0].right_derivative = -10000;
            check(t.evaluate(1, v) && v <= 300 && v >= 100);
            std::string error;
            NativeShot s;
            check(!s.load(nullptr, 0, error));
            std::cout << "Native effect evaluator checks passed\n";
            return 0;
        }
        std::ifstream input(argv[1], std::ios::binary);
        std::vector<unsigned char> data((std::istreambuf_iterator<char>(input)), {});
        NativeShot shot;
        std::string error;
        if (!shot.load(data.data(), data.size(), error)) {
            std::cerr << error;
            return 2;
        }
        std::cout << std::setprecision(17);
        for (int n = 2; n < argc; ++n) {
            double time = std::stod(argv[n]);
            CameraPose pose;
            check(shot.camera.evaluate(time, pose));
            for (double v : pose)
                std::cout << v << ' ';
            for (const auto& t : shot.effects) {
                double v;
                check(t.evaluate(time, v));
                std::cout << v << ' ';
            }
            std::cout << '\n';
        }
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what();
        return 1;
    }
}
