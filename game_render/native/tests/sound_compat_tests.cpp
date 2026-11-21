#include "dolly_sound_compat_generated.hpp"
#include "dolly_sound_capture.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <string>
#endif

using namespace dolly_sound_compat;
static void check(bool value) {
    if (!value) { std::fputs("sound compatibility test failed\n", stderr); std::exit(1); }
}

struct Fixture {
    const Profile& profile;
    std::vector<unsigned char> text;
    std::uint32_t text_rva = 0x20000, data_rva = 0x1000, data_size = 0x1000;
    std::size_t offsets[5]{};
    Fixture(bool exact = false, const Profile& selected = kProfiles[0]) : profile(selected) {
        text.resize(exact ? 0x200000 : 0x2000, 0xcc);
        if (exact) { text_rva = 0x1000; data_rva = profile.voice_table & ~0xfff; }
        for (std::size_t i = 0; i < 5; ++i) {
            const auto& s = profile.signatures[i];
            offsets[i] = exact ? s.reviewed_rva - text_rva : 0x100 + i * 0x300;
            std::memcpy(text.data() + offsets[i], s.bytes, s.size);
            if (!exact) {
                for (std::size_t j = 0; j < s.size; ++j)
                    if (!s.mask[j]) text[offsets[i] + j] ^= 0xa5;
            }
        }
        for (std::size_t i = 0; i < profile.reference_count; ++i) {
            const auto& ref = profile.references[i];
            const auto at = offsets[ref.symbol] + ref.offset;
            const std::int32_t displacement = static_cast<std::int32_t>(
                std::int64_t(exact ? profile.voice_table : data_rva + 0x80) - (text_rva + at + ref.size));
            std::memcpy(text.data() + at + ref.displacement, &displacement, 4);
        }
    }
    bool run(Resolution& out, bool exact = false) {
        return resolve(text.data(), text.size(), text_rva, data_rva, data_size,
                       profile.signatures, profile.signature_count, profile.references,
                       profile.reference_count, exact, profile.voice_table, out);
    }
};

int main(int argc, char** argv) {
    const auto old_movie = dolly::movie_audio_layout(false);
    const auto new_movie = dolly::movie_audio_layout(true);
    check(old_movie.table == 0x4f6890 && old_movie.host_cvar == 0x8b4dd8);
    check(old_movie.recorder == 0x5ba5c0 && old_movie.audio_table == 0x4f6920);
    check(new_movie.table == 0x4f6950 && new_movie.host_cvar == 0x8b4f18);
    check(new_movie.recorder == 0x5ba740 && new_movie.audio_table == 0x4f6900);
    const auto latest_movie = dolly::movie_audio_layout(false, true);
    check(latest_movie.table == 0x4f6950 && latest_movie.host_cvar == 0x8b3f18);
    check(latest_movie.recorder == 0x5b9740 && latest_movie.audio_table == 0x4f6900);
    check(latest_movie.float_accessor == 0x3bb30 && new_movie.float_accessor == 0x3bb60);
    const auto movie6864 = dolly::movie_audio_layout(false, false, true);
    check(movie6864.table == 0x4f68f8 && movie6864.host_cvar == 0x8b3f58);
    check(movie6864.recorder == 0x5b9740 && movie6864.audio_table == 0x4f6988);
    check(movie6864.float_accessor == 0x3bb30);
    Resolution out{};
    check(kProfiles[0].parameter_volume_offset == 0x20 && kProfiles[0].parameter_rate_offset == 0x2c);
    check(kProfiles[1].parameter_volume_offset == 0x24 && kProfiles[1].parameter_rate_offset == 0x30);
    for (const auto& p : kProfiles) {
        for (bool mode : {false, true}) {
            Fixture f(mode, p);
            check(resolve_profiles(f.text.data(), f.text.size(), f.text_rva, f.data_rva,
                                   f.data_size, p.image_size, kProfiles, std::size(kProfiles),
                                   mode, out));
            check(out.voice_map_offset == p.voice_map_offset);
            check(out.parameter_volume_offset == p.parameter_volume_offset);
            check(out.parameter_rate_offset == p.parameter_rate_offset);
            check(out.voice_table == (mode ? p.voice_table : f.data_rva + 0x80));
            const Profile ambiguous[] = {p, p};
            check(!resolve_profiles(f.text.data(), f.text.size(), f.text_rva, f.data_rva,
                                    f.data_size, p.image_size, ambiguous, 2, mode, out));
            check(out.voice_table == 0 && out.voice_map_offset == 0);
            check(out.parameter_volume_offset == 0 && out.parameter_rate_offset == 0);
            Profile missing = p;
            missing.parameter_volume_offset = 0;
            check(!resolve_profiles(f.text.data(), f.text.size(), f.text_rva, f.data_rva,
                                    f.data_size, p.image_size, &missing, 1, mode, out));
            missing = p;
            missing.parameter_rate_offset = 0;
            check(!resolve_profiles(f.text.data(), f.text.size(), f.text_rva, f.data_rva,
                                    f.data_size, p.image_size, &missing, 1, mode, out));
        }
    }
    // The preceding reviewed build keeps the same ABI/signature shape. Its
    // actual RIP table is derived by fallback, rather than listed twice and
    // becoming ambiguous with the new profile.
    const Profile prior{previous::kImageSize, previous::kSignatures,
                        std::size(previous::kSignatures), previous::kTableReferences,
                        std::size(previous::kTableReferences), previous::kVoiceTable,
                        previous::kVoiceMapOffset, previous::kParameterVolumeOffset,
                        previous::kParameterRateOffset};
    Fixture previous_build(true, prior);
    check(resolve_profiles(previous_build.text.data(), previous_build.text.size(),
                           previous_build.text_rva, previous_build.data_rva,
                           previous_build.data_size, prior.image_size, kProfiles,
                           std::size(kProfiles), false, out));
    check(out.voice_table == previous::kVoiceTable);
    check(out.parameter_volume_offset == 0x20 && out.parameter_rate_offset == 0x2c);
    check(!resolve_profiles(previous_build.text.data(), previous_build.text.size(),
                            previous_build.text_rva, previous_build.data_rva,
                            previous_build.data_size, prior.image_size, kProfiles,
                            std::size(kProfiles), true, out));
    check(out.voice_table == 0 && out.parameter_volume_offset == 0 && out.parameter_rate_offset == 0);
    Fixture exact(true);
    check(exact.run(out, true) && out.voice_table == kVoiceTable);
    check(exact.run(out));
    Fixture relocated;
    check(relocated.run(out) && out.voice_table == relocated.data_rva + 0x80);
    for (std::size_t i = 0; i < 5; ++i)
        check(out.functions[i] == relocated.text_rva + relocated.offsets[i]);
    check(!relocated.run(out, true) && out.voice_table == 0);
    for (std::size_t i = 0; i < 5; ++i) {
        Fixture changed;
        changed.text[changed.offsets[i]] ^= 1;
        check(!changed.run(out) && out.functions[0] == 0);
        Fixture duplicate;
        std::memcpy(duplicate.text.data() + 0x1800,
                    duplicate.text.data() + duplicate.offsets[i], kSignatures[i].size);
        check(!duplicate.run(out));
    }
    Fixture disagree;
    const auto ref = kTableReferences[0];
    const auto operand = disagree.offsets[ref.symbol] + ref.offset + ref.displacement;
    disagree.text[operand] ^= 8;
    check(!disagree.run(out));
    Fixture outside;
    outside.data_rva += 0x1000;
    check(!outside.run(out));
    Fixture unaligned;
    for (const auto& r : kTableReferences)
        unaligned.text[unaligned.offsets[r.symbol] + r.offset + r.displacement] ^= 1;
    check(!unaligned.run(out));
    Fixture truncated;
    truncated.text.resize(truncated.offsets[4] + kSignatures[4].size - 1);
    check(!truncated.run(out));
    exact.text[exact.offsets[4]] ^= 1;
    check(!exact.run(out, true));

    // Optional offline .text snapshot (four uint32 fields then section bytes).
    // No DLL is loaded or executed. Generated locally from the reviewed PE.
    if (argc >= 2) {
        const auto index = argc == 3 ? std::strtoul(argv[2], nullptr, 10) : 0;
        check(index < std::size(kProfiles));
        const auto& profile = kProfiles[index];
        std::ifstream stream(argv[1], std::ios::binary);
        check(bool(stream));
        std::uint32_t header[4]{};
        stream.read(reinterpret_cast<char*>(header), sizeof(header));
        std::vector<unsigned char> text((std::istreambuf_iterator<char>(stream)), {});
        check(text.size() == header[1]);
        for (bool mode : {false, true}) {
            check(resolve_profiles(text.data(), text.size(), header[0], header[2], header[3],
                                   profile.image_size, kProfiles, std::size(kProfiles), mode, out));
            check(out.voice_table == profile.voice_table);
            check(out.voice_map_offset == profile.voice_map_offset);
            check(out.parameter_volume_offset == profile.parameter_volume_offset);
            check(out.parameter_rate_offset == profile.parameter_rate_offset);
            for (std::size_t i = 0; i < 5; ++i)
                check(out.functions[i] == profile.signatures[i].reviewed_rva);
        }
    }
    // Interface-voice silence rule: any voice started inside the armed window is
    // declined, and nothing is declined once the window closes. The cue has no
    // single stable asset path, so the window itself is the rule. Tested as a
    // pure decision so the audio-thread hook needs no game process here.
    using dolly::ui_voice_silenced;
    check(ui_voice_silenced(100, 200));
    check(ui_voice_silenced(199, 200));
    check(!ui_voice_silenced(200, 200));
    check(!ui_voice_silenced(201, 200));
    check(!ui_voice_silenced(100, 0));
    // The audio stream uses movie-frame/fps, even when PCM delivery is
    // quantized to512sample blocks. Fractional sample frames share endpoints.
    dolly::MovieAudioSample audio;
    check(dolly::movie_audio_frame(123, 175, 60, audio));
    check(audio.first == 128625 && audio.end == 129360 && audio.rate == 44100);
    check(dolly::movie_audio_frame(123, 0, 120, audio));
    check(audio.first == 0 && audio.end == 368);
    check(dolly::movie_audio_frame(123, 1, 120, audio));
    check(audio.first == 368 && audio.end == 735);
    check(!dolly::movie_audio_frame(0, 0, 60, audio));
    check(!dolly::movie_audio_frame(123, 0, 0, audio));
    check(!dolly::movie_audio_frame(123, UINT64_MAX, 60, audio));
#ifdef _WIN32
    {
        // A source-audio start that cannot create its state CSV must not leave
        // the event CSV behind; a retry on the same take path then succeeds.
        wchar_t temp[MAX_PATH]{};
        check(GetTempPathW(MAX_PATH, temp) != 0);
        const std::wstring csv = std::wstring(temp) + L"DollySoundOutputs-" +
                                 std::to_wstring(GetCurrentProcessId()) + L".csv";
        const std::wstring state = csv + L".state.csv";
        check(GetFileAttributesW(csv.c_str()) == INVALID_FILE_ATTRIBUTES &&
              GetFileAttributesW(state.c_str()) == INVALID_FILE_ATTRIBUTES);
        HANDLE blocker = CreateFileW(state.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                     FILE_ATTRIBUTE_NORMAL, nullptr);
        check(blocker != INVALID_HANDLE_VALUE);
        CloseHandle(blocker);
        void* events = nullptr;
        void* states = nullptr;
        const bool refused = !dolly::sound_capture_open_outputs(csv.c_str(), events, states);
        const bool left = GetFileAttributesW(csv.c_str()) != INVALID_FILE_ATTRIBUTES;
        const bool kept = GetFileAttributesW(state.c_str()) != INVALID_FILE_ATTRIBUTES;
        DeleteFileW(state.c_str()); // Exact fixture created above.
        if (left)
            std::fputs("failed source-audio start left its event CSV\n", stderr);
        if (left)
            DeleteFileW(csv.c_str());
        check(refused && !left && kept && !events && !states);
        check(dolly::sound_capture_open_outputs(csv.c_str(), events, states));
        CloseHandle(static_cast<HANDLE>(events));
        CloseHandle(static_cast<HANDLE>(states));
        DeleteFileW(state.c_str());
        DeleteFileW(csv.c_str());
    }
    {
        // soundsystem.dll can load after the movie accessor was redirected. A
        // later call with the reviewed pair must still publish the sample map.
        const std::size_t size = 0x8b5000;
        auto* image = static_cast<unsigned char*>(
            VirtualAlloc(nullptr, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
        check(image != nullptr);
        const auto layout = dolly::movie_audio_layout(false);
        const unsigned char getter[] = {0xF3, 0x0F, 0x10, 0x81, 0xF4, 0x00, 0x00, 0x00, 0xC3};
        const unsigned char frame_getter[] = {0x48, 0x8b, 0x81, 0xd8, 0, 0, 0, 0xc3};
        std::memcpy(image + 0xfc7c0, getter, sizeof(getter));
        std::memcpy(image + 0xfc7b0, frame_getter, sizeof(frame_getter));
        const auto pointer = [&](std::size_t at, std::size_t to) {
            void* value = image + to;
            std::memcpy(image + at, &value, sizeof(value));
        };
        pointer(layout.table + 0x38, 0xfc7c0);
        pointer(layout.table + 6 * 8, 0xfc7b0);
        pointer(layout.audio_table, 0xfe5bc);
        wchar_t temp[MAX_PATH]{};
        check(GetTempPathW(MAX_PATH, temp) != 0);
        const std::wstring marker = std::wstring(temp) + L"dolly_movie_audio_" +
                                    std::to_wstring(GetCurrentProcessId()) + L".ready";
        const auto marker_text = [&] {
            std::ifstream in(marker, std::ios::binary);
            return std::string(std::istreambuf_iterator<char>(in), {});
        };
        check(dolly::movie_audio_install(image) && dolly::movie_audio_available());
        check(marker_text().empty());
        int sound = 0;
        const bool later = dolly::movie_audio_install(image, &sound, true);
        const auto text = marker_text();
        DeleteFileW(marker.c_str());
        if (text != "pcm-sample-map-v1")
            std::fputs("a late soundsystem module left the fixed-step sample map off\n", stderr);
        check(later && text == "pcm-sample-map-v1");
        VirtualFree(image, 0, MEM_RELEASE);
    }
#endif
    std::puts("sound compatibility tests passed");
}
