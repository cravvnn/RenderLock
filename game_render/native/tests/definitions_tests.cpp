// Remote definitions parser: closed format, fail-closed on every malformed row.
#include "dolly_remote_definitions.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#define require(condition)                                                                      \
    do {                                                                                       \
        if (!(condition)) {                                                                    \
            std::fprintf(stderr, "Remote definitions regression failed at line %d: %s\n",       \
                         __LINE__, #condition);                                                \
            std::exit(1);                                                                      \
        }                                                                                      \
    } while (0)

static const char* kValid =
    "DOLLY_DEFINITIONS 1\n"
    "abi 4\n"
    "+layout 6728 0x5b92b0 0x4d7e88 0x4e2790 0x2d590 0x732c0 0x34ea0 0x28b40 0x34ee0 0x73310\n"
    "+layout legacy 0x61b618 0x535730 0x540128 0x2ec00 0x7bf30 0x368d0 0x2a240 0x36910 0x7bf80\n"
    "+cvar modern_6801 0x313208 0x20fea0 0x6c6b0 0x6c5f0 41 1\n"
    "+engine "
    "c09143cf9cd722909404c64e3ffa05157afc15e8a3519b49a1396a627af8ca85 0x43c0000 6728\n"
    "+tier0 "
    "666c9022739190d571a67161646d1a55511e130b62c3a93f8c95b47beaede433 0x401000 modern_6801\n"
    "+client "
    "c09143cf9cd722909404c64e3ffa05157afc15e8a3519b49a1396a627af8ca85 0x43c0000 "
    "0x19e7770 0x19e1194 0x2782d90 0x34331c0 0x3e600d8 0x0 0x1 0x5e2ce0 "
    "488bc44889581055565741544155415641574881ecc00800000f2970b84c8bf9"
    " - -\n"
    "end\n";

static const char* kPrologue =
    "488bc44889581055565741544155415641574881ecc00800000f2970b84c8bf9";

static bool parse(const std::string& text, dolly_remote::Definitions& data) {
    return dolly_remote::parse(text.data(), text.size(), data);
}

static void replace_once(std::string& text, const char* from, const char* to) {
    const std::size_t at = text.find(from);
    if (at == std::string::npos) {
        std::fputs("Fixture text is missing.\n", stderr);
        std::exit(1);
    }
    text.replace(at, std::strlen(from), to);
}

int main() {
    // The document Python emits for the published file parses exactly.
    dolly_remote::Definitions data;
    require(parse(kValid, data));
    require(data.loaded && data.abi == 4);
    require(data.client_count == 1 && data.engine_count == 1);
    require(data.tier0_count == 1 && data.engine_layout_count == 2 && data.cvar_layout_count == 1);
    require(std::strcmp(data.clients[0].sha256,
                        "c09143cf9cd722909404c64e3ffa05157afc15e8a3519b49a1396a627af8ca85") == 0);
    require(data.clients[0].image_size == 0x43c0000);
    require(data.clients[0].main_view_setup == 0x19e7770);
    require(data.clients[0].camera_layout_revision == 1);
    require(data.clients[0].view_setup_delegate == 0x5e2ce0);
    require(data.clients[0].prologue_size == 32);
    require(data.clients[0].signature_size == 0);
    const auto* engine = dolly_remote::detail::engine_layout(data, "6728");
    require(engine && engine->active == 0x732c0 && engine->filename == 0x73310);
    const auto* cvar = dolly_remote::detail::cvar_layout(data, "modern_6801");
    require(cvar && cvar->data_slot == 41 && cvar->extended && cvar->setter == 0x20fea0);
    require(dolly_remote::detail::engine_layout(data, "missing") == nullptr);

    // An empty document is valid and simply carries no entries.
    dolly_remote::Definitions empty;
    require(parse("DOLLY_DEFINITIONS 1\nabi 4\nend\n", empty));
    require(empty.loaded && empty.client_count == 0);

    // A signature row keeps both byte arrays and their mask.
    std::string signed_row = kValid;
    replace_once(signed_row, " - -\n", " 488bc448 01010101\n");
    dolly_remote::Definitions signed_data;
    require(parse(signed_row, signed_data));
    require(signed_data.clients[0].signature_size == 4);
    require(signed_data.clients[0].signature[0] == 0x48 && signed_data.clients[0].signature[3] == 0x48);
    require(signed_data.clients[0].signature_mask[0] == 1);

    std::string masked = kValid;
    replace_once(masked, " - -\n", " 488bc448 01010001\n");
    dolly_remote::Definitions masked_data;
    require(parse(masked, masked_data));
    require(masked_data.clients[0].signature_mask[2] == 0);

    std::string mismatched = kValid;
    replace_once(mismatched, " - -\n", " 488bc448 010101\n");
    require(!parse(mismatched, masked_data));

    // Every malformed shape is rejected whole, leaving the output untouched.
    const char* broken[] = {
        "",
        "DOLLY_DEFINITIONS 2\nabi 4\nend\n",
        "DOLLY_DEFINITIONS 1\nend\n",
        "DOLLY_DEFINITIONS 1\nabi 4\nabi 4\nend\n",
        "DOLLY_DEFINITIONS 1\nabi 4\n",
        "DOLLY_DEFINITIONS 1\nabi 4\nend\ntrailing\n",
        "DOLLY_DEFINITIONS 1\nabi 4\n+bogus 1\nend\n",
        "DOLLY_DEFINITIONS 1\nabi 4\n+layout 6728 0x1\nend\n",
        "DOLLY_DEFINITIONS 1\nabi 4\n+cvar x 0x1 0x2 0x3 0x4 42 1\nend\n",
        "DOLLY_DEFINITIONS 1\nabi 4\n+engine "
        "c09143cf9cd722909404c64e3ffa05157afc15e8a3519b49a1396a627af8ca85 0x43c0000 unknown\nend\n",
        "DOLLY_DEFINITIONS 1\nabi 4\n+tier0 "
        "c09143cf9cd722909404c64e3ffa05157afc15e8a3519b49a1396a627af8ca85 0x401000 unknown\nend\n",
    };
    for (const char* text : broken)
        require(!parse(text, empty));

    // A modified valid document with one bad field must fail, not silently drop it.
    std::string bad = kValid;
    replace_once(bad, "c09143cf", "C09143cf");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, "0x2782d90", "2782d90");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, "0x5b92b0", "0x1_5b92b0");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, "0x43c0000", "0x143c00000");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, " - -\n", " 0101 - \n");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, " - -\n", " - 0101\n");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, " - -\n", " 0101 0102\n");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, "modern_6801 0x313208", "Modern 0x313208");
    require(!parse(bad, empty));
    bad = kValid;
    replace_once(bad, " 41 1\n", " 43 2\n");
    require(!parse(bad, empty));

    // A short prologue is rejected: the hook gate needs the reviewed 32 bytes.
    std::string prologue = kValid;
    replace_once(prologue, kPrologue, "4889");
    require(!parse(prologue, empty));

    // Definitions are additive: an oversized row count is refused instead of
    // overwriting memory or silently truncating the table.
    std::string many = "DOLLY_DEFINITIONS 1\nabi 4\n";
    for (int index = 0; index < 17; ++index) {
        many += "+layout n0 0x1 0x1 0x1 0x1 0x1 0x1 0x1 0x1 0x1\n";
    }
    many += "end\n";
    require(!parse(many, empty));

    std::puts("Remote definitions tests passed.");
    return 0;
}
