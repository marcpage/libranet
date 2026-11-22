#include <gtest/gtest.h>
#include <string>
#include <vector>

#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswered.hpp"

using storage::HashPrefix;
using storage::Identifier;
using storage::SearchAnswered;

namespace {

// "hello"'s SHA-256 digest, as `shasum -a 256` gives it, and a node's id:
// the SHA-256 of "abc" (FIPS 180-2 B.1).
constexpr auto hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr auto node_hash = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

} // namespace

TEST(SearchAnsweredTest, SaysWhatFromWhere) {
    const std::vector<Identifier> results{Identifier("sha256", hello_hash)};
    const SearchAnswered answered{
        HashPrefix::parse("2CF2"),
        Identifier("sha256", node_hash),
        results,
    };

    EXPECT_EQ(std::string(answered),
              "2cf2 from sha256/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad, "
              "1 results");
    EXPECT_EQ(std::string(SearchAnswered::name), "data.search_answered");
}
