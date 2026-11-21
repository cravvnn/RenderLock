#include "dolly_effects.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <utility>
namespace dolly {
namespace {
struct Reader {
    const unsigned char* p;
    std::size_t remaining;
    unsigned u32() {
        unsigned n = 0;
        for (unsigned i = 0; i < 4; ++i)
            n |= unsigned(*p++) << (8 * i);
        remaining -= 4;
        return n;
    }
    double number() {
        std::uint64_t n = 0;
        for (unsigned i = 0; i < 8; ++i)
            n |= std::uint64_t(*p++) << (8 * i);
        double v;
        std::memcpy(&v, &n, 8);
        remaining -= 8;
        return v;
    }
};
bool valid(unsigned id, double v) {
    const auto& s = kEffects[id];
    return std::isfinite(v) && v >= s.minimum && v <= s.maximum &&
           (!s.discrete || v == std::floor(v));
}
}
bool EffectTrack::evaluate(double phase, double& value) const noexcept {
    if (!std::isfinite(phase))
        return false;
    if (phase <= first_time || segments.empty()) {
        value = first;
        return true;
    }
    if (phase >= last_time) {
        value = last;
        return true;
    }
    std::size_t low = 0, high = segments.size();
    while (low < high) {
        auto mid = low + (high - low) / 2;
        if (segments[mid].begin <= phase)
            low = mid + 1;
        else
            high = mid;
    }
    const auto& s = segments[low - 1];
    double u = (phase - s.begin) / (s.end - s.begin), u2 = u * u, u3 = u2 * u;
    value = s.left;
    if (s.kind == 1)
        value = (1 - u) * s.left + u * s.right;
    else if (s.kind == 2)
        value = (2 * u3 - 3 * u2 + 1) * s.left +
                (u3 - 2 * u2 + u) * (s.end - s.begin) * s.left_derivative +
                (-2 * u3 + 3 * u2) * s.right + (u3 - u2) * (s.end - s.begin) * s.right_derivative;
    value = std::clamp(value, std::min(s.left, s.right), std::max(s.left, s.right));
    return valid(id, value);
}
bool NativeShot::load(const void* data, std::size_t size, std::string& error) {
    auto fail = [&](const char* msg) {
        error = msg;
        return false;
    };
    error.clear();
    if (!data || size < 40 || size > 2 * 1024 * 1024 - 1024)
        return fail("Invalid native shot length");
    auto p = static_cast<const unsigned char*>(data);
    // DLYSHOT3 appends an attach block after the effect tracks; DLYSHOT2 stays
    // accepted for shots authored before attach keys existed.
    const bool attach_capable = !std::memcmp(p, "DLYSHOT3", 8);
    if (!attach_capable && std::memcmp(p, "DLYSHOT2", 8))
        return fail("Native shot protocol differs");
    Reader r{p + 8, size - 8};
    auto version = r.u32(), camera_bytes = r.u32(), effect_bytes = r.u32();
    std::uint32_t attach_bytes = 0;
    if (attach_capable)
        attach_bytes = r.u32();
    auto reserved = r.u32();
    if (version != 1 || reserved ||
        std::size_t(camera_bytes) + effect_bytes + attach_bytes != r.remaining ||
        effect_bytes < 16 || attach_bytes > AttachTrack::max_bytes3)
        return fail("Invalid native shot envelope");
    NativeShot candidate;
    if (!candidate.camera.load(r.p, camera_bytes, error))
        return false;
    r.p += camera_bytes;
    r.remaining -= camera_bytes;
    // Effect parsing is bounded to its own length so a trailing attach block
    // cannot be mistaken for effect bytes.
    Reader effects{r.p, effect_bytes};
    const bool vector_format = !std::memcmp(effects.p, "DLYEFX02", 8);
    if (!vector_format && std::memcmp(effects.p, "DLYEFX01", 8))
        return fail("Invalid effect format");
    effects.p += 8;
    effects.remaining -= 8;
    auto count = effects.u32();
    reserved = effects.u32();
    if (count > kEffects.size() + 3 || reserved)
        return fail("Invalid effect count");
    std::array<unsigned, kEffects.size()> used{}, restore_mask{};
    for (unsigned i = 0; i < count; ++i) {
        if (effects.remaining < 56)
            return fail("Truncated effect header");
        EffectTrack t;
        t.id = effects.u32();
        auto n = effects.u32(), override_value = effects.u32();
        t.component = effects.u32();
        if (t.id >= kEffects.size() || t.component >= kEffects[t.id].components ||
            (!vector_format && t.component) || used[t.id] & (1u << t.component) || n >= 4096 ||
            override_value > 1 || effects.remaining < 40 + std::size_t(n) * 56)
            return fail("Invalid effect track header");
        used[t.id] |= 1u << t.component;
        t.restore_override = override_value != 0;
        if (t.restore_override)
            restore_mask[t.id] |= 1u << t.component;
        t.first_time = effects.number();
        t.last_time = effects.number();
        t.first = effects.number();
        t.last = effects.number();
        t.restore = effects.number();
        if (!std::isfinite(t.first_time) || !std::isfinite(t.last_time) || t.first_time < 0 ||
            t.last_time < t.first_time || t.last_time > candidate.camera.duration() ||
            !valid(t.id, t.first) || !valid(t.id, t.last) || !std::isfinite(t.restore) ||
            (t.restore_override && !valid(t.id, t.restore)))
            return fail("Invalid effect endpoint or restore value");
        double previous_time = t.first_time, previous = t.first;
        for (unsigned j = 0; j < n; ++j) {
            EffectSegment s;
            s.begin = effects.number();
            s.end = effects.number();
            s.kind = effects.u32();
            s.flags = effects.u32();
            s.left = effects.number();
            s.right = effects.number();
            s.left_derivative = effects.number();
            s.right_derivative = effects.number();
            if (!std::isfinite(s.begin) || !std::isfinite(s.end) || s.begin != previous_time ||
                s.end <= s.begin || s.end > t.last_time || s.kind > 2 || s.flags != 1 ||
                (kEffects[t.id].discrete && s.kind != 0) || s.left != previous ||
                !valid(t.id, s.left) || !valid(t.id, s.right) ||
                !std::isfinite(s.left_derivative) || !std::isfinite(s.right_derivative))
                return fail("Invalid effect segment");
            t.segments.push_back(s);
            previous_time = s.end;
            previous = s.right;
        }
        if (previous_time != t.last_time || previous != t.last)
            return fail("Disconnected effect endpoint");
        candidate.effects.push_back(std::move(t));
    }
    for (unsigned id = 0; id < kEffects.size(); ++id) {
        const auto full = (1u << kEffects[id].components) - 1;
        if (used[id] && used[id] != full)
            return fail("Incomplete vector effect");
        if (restore_mask[id] && restore_mask[id] != full)
            return fail("Incomplete vector restore");
    }
    if (effects.remaining)
        return fail("Trailing native effect bytes");
    r.p += effect_bytes;
    r.remaining -= effect_bytes;
    if (attach_bytes) {
        if (!candidate.attach.load(r.p, attach_bytes, candidate.camera.duration(), error))
            return false;
        r.p += attach_bytes;
        r.remaining -= attach_bytes;
    }
    if (r.remaining)
        return fail("Trailing native shot bytes");
    *this = std::move(candidate);
    return true;
}
}
