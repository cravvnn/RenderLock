// The generated header substitutes only std::thread construction. All writer
// admission, output handles, rollback, retry and flush use the production owner.
#include <thread>
#include <system_error>
#include <filesystem>
#include <iostream>
namespace writer_faults {
bool refuse = true;
template<class Function> std::thread start(Function function) {
    if (refuse)
        throw std::system_error(std::make_error_code(std::errc::resource_unavailable_try_again));
    return std::thread(function);
}
}
#include "sequence_writer_fault.hpp"

#define CHECK(x) do { if (!(x)) { std::cerr << "Failed line " << __LINE__ << '\n'; return 1; } } while (0)
int main() {
    namespace fs = std::filesystem;
    auto root = fs::temp_directory_path() / ("dolly-writer-start-" + std::to_string(GetCurrentProcessId()));
    CHECK(!fs::exists(root));
    CHECK(fs::create_directory(root));
    struct Cleanup { fs::path root; ~Cleanup() { std::error_code error; fs::remove_all(root, error); } } cleanup{root};
    for (bool exclusive : {false, true}) {
        dolly::player_capture::SequenceWriter writer;
        const auto target = root / (exclusive ? L"exclusive.bin" : L"stream.bin");
        writer_faults::refuse = true;
        try { CHECK(!writer.open(target.wstring(), 2, exclusive)); }
        catch (const std::system_error&) { std::cerr << "worker refusal escaped owner\n"; return 1; }
        auto moved = target; moved += L".moved";
        CHECK(MoveFileW(target.c_str(), moved.c_str())); // Fails if the provisional handle is still open.
        writer_faults::refuse = false;
        const auto retry = root / (exclusive ? L"exclusive-retry.bin" : L"stream-retry.bin");
        CHECK(writer.open(retry.wstring(), 2, exclusive));
        std::uint64_t header[8]{};
        dolly::player_capture::SequenceWriter::Buffer bytes(8, 77);
        CHECK(writer.submit(header, std::move(bytes)) == dolly::player_capture::SequenceWriter::Submit::accepted);
        CHECK(writer.finish());
        CHECK(writer.written() == 1 && fs::file_size(retry) == 72);
    }
    std::cout << "Refused stream/handle workers retired; retries flushed\n";
}
