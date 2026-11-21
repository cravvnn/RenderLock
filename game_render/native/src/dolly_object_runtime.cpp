#include "dolly_object_runtime.hpp"
#include <cmath>

namespace dolly::object_runtime {
namespace {
using Vec3 = std::array<double, 3>;

// Library index -> proxy shape and default half-extent (world units). Mirrors
// dolly/object_library.py append-only order:
// marker, arrow, sphere, crate, barrel, pillar, poster, flat_light, point_light.
struct Entry {
    Shape shape;
    float half_x, half_y, half_z;
};
constexpr std::array<Entry, 9> kLibrary{{
    {Shape::Box, 8.0f, 8.0f, 8.0f},         // marker
    {Shape::Arrow, 12.0f, 4.0f, 4.0f},      // arrow
    {Shape::Sphere, 16.0f, 16.0f, 16.0f},   // sphere
    {Shape::Box, 24.0f, 24.0f, 24.0f},      // crate
    {Shape::Cylinder, 16.0f, 16.0f, 28.0f}, // barrel
    {Shape::Cylinder, 12.0f, 12.0f, 80.0f}, // pillar
    {Shape::Box, 4.0f, 32.0f, 48.0f},       // poster
    {Shape::Box, 32.0f, 32.0f, 2.0f},       // flat_light
    {Shape::Sphere, 8.0f, 8.0f, 8.0f},      // point_light
}};

bool finite3(const float* v) noexcept {
    return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]);
}

// Authored angles -> orthonormal basis (Source convention, matching the
// placement math in dolly_object_picker.cpp).
struct Basis {
    Vec3 x{}, y{}, z{};
};
Basis angles_to_basis(const float* a) noexcept {
    constexpr double r = 3.14159265358979323846 / 180.0;
    const double p = a[0] * r, y = a[1] * r, rl = a[2] * r;
    const double sp = std::sin(p), cp = std::cos(p), sy = std::sin(y), cy = std::cos(y);
    const double sr = std::sin(rl), cr = std::cos(rl);
    return {{cp * cy, cp * sy, -sp},
            {-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp},
            {cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp}};
}

Vec3 to_world(const Basis& b, const float* origin, double lx, double ly, double lz) noexcept {
    return {origin[0] + b.x[0] * lx + b.y[0] * ly + b.z[0] * lz,
            origin[1] + b.x[1] * lx + b.y[1] * ly + b.z[1] * lz,
            origin[2] + b.x[2] * lx + b.y[2] * ly + b.z[2] * lz};
}

struct Writer {
    ObjectSegment* out;
    std::size_t capacity, written = 0;
    void line(const Basis& b, const float* origin,
              double ax, double ay, double az, double bx, double by, double bz) noexcept {
        if (written >= capacity)
            return;
        out[written++] = ObjectSegment{to_world(b, origin, ax, ay, az),
                                 to_world(b, origin, bx, by, bz)};
    }
};
} // namespace

Shape shape_for_index(std::uint32_t library_index) noexcept {
    return library_index < kLibrary.size() ? kLibrary[library_index].shape : Shape::Invalid;
}

bool build_proxy(const EditorObjectItem& item, ObjectSegment* out, std::size_t capacity,
                 std::size_t& written) noexcept {
    written = 0;
    if (!out || capacity == 0)
        return false;
    const Shape shape = shape_for_index(item.shape);
    if (shape == Shape::Invalid || !finite3(item.position) || !finite3(item.angles))
        return false;
    if (!std::isfinite(item.scale) || item.scale < 0.05f || item.scale > 20.0f)
        return false;
    const Entry e = kLibrary[item.shape];
    const Basis b = angles_to_basis(item.angles);
    const double hx = double(e.half_x) * item.scale, hy = double(e.half_y) * item.scale,
                 hz = double(e.half_z) * item.scale;
    Writer w{out, capacity};
    switch (shape) {
    case Shape::Box: {
        // 3 pairs of axis planes -> 12 edges.
        const double xs[2] = {-hx, hx}, ys[2] = {-hy, hy}, zs[2] = {-hz, hz};
        for (int i = 0; i < 2; ++i)
            for (int j = 0; j < 2; ++j) {
                w.line(b, item.position, xs[i], ys[j], zs[0], xs[i], ys[j], zs[1]);
                w.line(b, item.position, xs[i], ys[0], zs[j], xs[i], ys[1], zs[j]);
                w.line(b, item.position, xs[0], ys[i], zs[j], xs[1], ys[i], zs[j]);
            }
        break;
    }
    case Shape::Sphere: {
        // Three orthogonal great-circle rings.
        constexpr int steps = 12;
        for (int ring = 0; ring < 3 && w.written < capacity; ++ring) {
            double px = 0, py = 0, pz = 0;
            for (int s = 0; s <= steps; ++s) {
                const double t = 2.0 * 3.14159265358979323846 * s / steps;
                double x = 0, y = 0, z = 0;
                if (ring == 0) { x = std::cos(t) * hx; y = std::sin(t) * hy; }
                else if (ring == 1) { x = std::cos(t) * hx; z = std::sin(t) * hz; }
                else { y = std::cos(t) * hy; z = std::sin(t) * hz; }
                if (s)
                    w.line(b, item.position, px, py, pz, x, y, z);
                px = x; py = y; pz = z;
            }
        }
        break;
    }
    case Shape::Cylinder: {
        constexpr int steps = 12;
        double px = 0, py = 0;
        for (int s = 0; s <= steps && w.written < capacity; ++s) {
            const double t = 2.0 * 3.14159265358979323846 * s / steps;
            const double x = std::cos(t) * hx, y = std::sin(t) * hy;
            if (s) {
                w.line(b, item.position, px, py, -hz, x, y, -hz);
                w.line(b, item.position, px, py, hz, x, y, hz);
            }
            if (s % 3 == 0)
                w.line(b, item.position, x, y, -hz, x, y, hz);
            px = x; py = y;
        }
        break;
    }
    case Shape::Arrow: {
        w.line(b, item.position, -hx, 0, 0, hx, 0, 0);
        w.line(b, item.position, hx, 0, 0, hx - hy, hy, 0);
        w.line(b, item.position, hx, 0, 0, hx - hy, -hy, 0);
        w.line(b, item.position, hx, 0, 0, hx - hy, 0, hy);
        w.line(b, item.position, hx, 0, 0, hx - hy, 0, -hy);
        break;
    }
    case Shape::Invalid:
        return false;
    }
    written = w.written;
    return written > 0;
}

} // namespace dolly::object_runtime
