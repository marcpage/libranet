#include <gtest/gtest.h>
#include <cstddef>
#include <optional>
#include <string>

#include "storage/DataStored.hpp"
#include "storage/Identifier.hpp"

using storage::DataStored;
using storage::Identifier;

namespace {

// "hello", and its SHA-256 digest, as `shasum -a 256` gives it, and a
// node's id: the SHA-256 of "abc" (FIPS 180-2 B.1).
constexpr auto hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr auto node_hash = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
constexpr std::size_t hello_size = 5;

} // namespace

TEST(DataStoredTest, SaysWhatAndFromWhere) {
    const Identifier node("sha256", node_hash);
    const DataStored stored{Identifier("sha256", hello_hash), node, hello_size};

    EXPECT_EQ(std::string(stored),
              "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824, 5 bytes, "
              "from sha256/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    EXPECT_EQ(std::string(DataStored::name), "data.stored");
}

TEST(DataStoredTest, FromNoNode) {
    const DataStored stored{Identifier("sha256", hello_hash), std::nullopt, hello_size};

    EXPECT_EQ(std::string(stored),
              "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824, 5 bytes, "
              "from no node");
}
