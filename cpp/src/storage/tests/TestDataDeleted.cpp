#include <gtest/gtest.h>
#include <cstddef>
#include <string>

#include "storage/DataDeleted.hpp"
#include "storage/Identifier.hpp"

using storage::DataDeleted;
using storage::Identifier;

namespace {

// "hello", and its SHA-256 digest, as `shasum -a 256` gives it.
constexpr auto hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr std::size_t hello_size = 5;

} // namespace

TEST(DataDeletedTest, SaysWhatAndHowLarge) {
    const DataDeleted deleted{Identifier("sha256", hello_hash), hello_size};

    EXPECT_EQ(std::string(deleted),
              "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824, 5 bytes");
    EXPECT_EQ(std::string(DataDeleted::name), "data.deleted");
}
