#include <gtest/gtest.h>
#include <cstddef>
#include <initializer_list>
#include <iterator>
#include <stdexcept>
#include <vector>

#include "Fixtures.hpp"
#include "crypto/Pem.hpp"
#include "crypto/RsaDer.hpp"

using crypto::Pem;
using crypto::RsaDer;

namespace {

// Where the fixture key's SubjectPublicKeyInfo has its BIT STRING's count
// of unused bits: after the outer SEQUENCE's 3-byte header, the 15-byte
// AlgorithmIdentifier, and the BIT STRING's 3-byte header.
constexpr std::size_t spki_unused_bits_offset = 21;
// Where the fixture key's PKCS #8 has its version: after the outer
// SEQUENCE's 4-byte header and the INTEGER's 2.
constexpr std::size_t pkcs8_version_offset = 6;
// Long enough that its DER length takes 3 bytes.
constexpr std::size_t long_key_size = 70000;
// A length of 0x80 alone: BER's indefinite length, which DER has not.
constexpr auto indefinite_length = std::byte{0x80};

RsaDer::Data der(const char* pem) {
    return Pem::unwrap(pem).der_bytes;
}

RsaDer::Data bytes(std::initializer_list<int> values) {
    RsaDer::Data data;

    for (const auto value : values) {
        data.push_back(static_cast<std::byte>(value));
    }

    return data;
}

} // namespace

TEST(RsaDerTest, WritesWhatPythonWrites) {
    EXPECT_EQ(RsaDer::spki_from_pkcs1(der(fixtures::rsa_pkcs1_public_pem)),
              der(fixtures::rsa_spki_public_pem));
    EXPECT_EQ(RsaDer::pkcs8_from_pkcs1(der(fixtures::rsa_pkcs1_private_pem)),
              der(fixtures::rsa_pkcs8_private_pem));
}

TEST(RsaDerTest, ReadsWhatPythonWrites) {
    EXPECT_EQ(RsaDer::pkcs1_from_spki(der(fixtures::rsa_spki_public_pem)),
              der(fixtures::rsa_pkcs1_public_pem));
    EXPECT_EQ(RsaDer::pkcs1_from_pkcs8(der(fixtures::rsa_pkcs8_private_pem)),
              der(fixtures::rsa_pkcs1_private_pem));
}

TEST(RsaDerTest, LengthsShortAndLong) {
    // Its length fits in a byte, and in the long form, in three.
    const auto short_key = bytes({0x30, 0x00});
    const RsaDer::Data long_key(long_key_size, std::byte{0x01});

    EXPECT_EQ(RsaDer::pkcs1_from_spki(RsaDer::spki_from_pkcs1(short_key)), short_key);
    EXPECT_EQ(RsaDer::pkcs1_from_pkcs8(RsaDer::pkcs8_from_pkcs1(short_key)), short_key);
    EXPECT_EQ(RsaDer::pkcs1_from_spki(RsaDer::spki_from_pkcs1(long_key)), long_key);
    EXPECT_EQ(RsaDer::pkcs1_from_pkcs8(RsaDer::pkcs8_from_pkcs1(long_key)), long_key);
}

// Each EXPECT_THROW counts toward the complexity, as branches would.
// NOLINTBEGIN(readability-function-cognitive-complexity)
TEST(RsaDerTest, RefusesWhatIsNoSubjectPublicKeyInfo) {
    const auto spki = der(fixtures::rsa_spki_public_pem);
    auto trailing = spki;
    auto cut_short = spki;
    auto unused_bits = spki;
    auto indefinite = spki;

    trailing.push_back(std::byte{0x00});
    cut_short.pop_back();
    unused_bits.at(spki_unused_bits_offset) = std::byte{0x01};
    indefinite.at(1) = indefinite_length;

    for (const auto& malformed : {
             RsaDer::Data(),
             trailing,
             cut_short,
             unused_bits,
             indefinite,
             // The key, but not in SubjectPublicKeyInfo.
             der(fixtures::rsa_pkcs1_public_pem),
             // An Ed25519 key, from RFC 8032.
             der("-----BEGIN PUBLIC KEY-----\n"
                 "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=\n"
                 "-----END PUBLIC KEY-----\n"),
             // Lengths in the long form with a byte to spare, or that fit
             // in the short form, and one longer than four bytes.
             bytes({0x30, 0x82, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00}),
             bytes({0x30, 0x81, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00}),
             bytes({0x30, 0x85, 0x01, 0x00, 0x00, 0x00, 0x00}),
         }) {
        EXPECT_THROW(static_cast<void>(RsaDer::pkcs1_from_spki(malformed)), std::invalid_argument)
            << fixtures::hex(malformed);
    }
}

TEST(RsaDerTest, RefusesWhatIsNoPkcs8) {
    const auto pkcs8 = der(fixtures::rsa_pkcs8_private_pem);
    // The key with an empty set of attributes, [0] {}, after it, which
    // makes the outer SEQUENCE 2 bytes longer.
    const auto attributes_header = bytes({0x30, 0x82, 0x02, 0x79});
    const auto empty_attributes = bytes({0xA0, 0x00});
    auto version_1 = pkcs8;
    auto cut_short = pkcs8;
    auto attributes = attributes_header;

    version_1.at(pkcs8_version_offset) = std::byte{0x01};
    cut_short.pop_back();
    attributes.insert(attributes.end(),
                      std::next(pkcs8.begin(), static_cast<std::ptrdiff_t>(attributes_header.size())),
                      pkcs8.end());
    attributes.insert(attributes.end(), empty_attributes.begin(), empty_attributes.end());

    for (const auto& malformed : {
             RsaDer::Data(),
             version_1,
             cut_short,
             attributes,
             // The key, but not in PKCS #8.
             der(fixtures::rsa_pkcs1_private_pem),
             // An Ed25519 key, from RFC 8032.
             der("-----BEGIN PRIVATE KEY-----\n"
                 "MC4CAQAwBQYDK2VwBCIEIJ1hsZ3v/VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g\n"
                 "-----END PRIVATE KEY-----\n"),
         }) {
        EXPECT_THROW(static_cast<void>(RsaDer::pkcs1_from_pkcs8(malformed)), std::invalid_argument)
            << fixtures::hex(malformed);
    }
}
// NOLINTEND(readability-function-cognitive-complexity)
