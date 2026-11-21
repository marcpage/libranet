#include <gtest/gtest.h>
#include <string>

#include "storage/DataRequested.hpp"
#include "storage/Identifier.hpp"

using storage::DataRequested;
using storage::Identifier;

namespace {

// The SHA-256 digest of "hello", as `shasum -a 256` gives it.
constexpr auto hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

} // namespace

TEST(DataRequestedTest, SaysWhat) {
    const DataRequested requested{Identifier("sha256", hello_hash)};

    EXPECT_EQ(std::string(requested),
              "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    EXPECT_EQ(std::string(DataRequested::name), "data.requested");
}
