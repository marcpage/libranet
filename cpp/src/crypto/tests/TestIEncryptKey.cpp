#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>

#include "Fixtures.hpp"
#include "crypto/IEncryptKey.hpp"

using crypto::IEncryptKey;
using AesMode = crypto::IEncryptKey::AesMode;
using AesPadding = crypto::IEncryptKey::AesPadding;

namespace {

// AES's key sizes, and its block size, which is an IV's size, in bytes.
constexpr auto aes_128_key_size = 16;
constexpr auto aes_192_key_size = 24;
constexpr auto aes_256_key_size = 32;
constexpr auto aes_block_size = 16;
constexpr auto invalid_key_size = 15;

// Messages encrypted by Python's cryptography package, with the keys and
// initialization vectors the tests give.
constexpr auto cbc_plaintext = "Hello, Libranet cryptographic library!";
constexpr auto cbc_256_ciphertext =
    "8b1a69208eb4ecfd19b3e3b9e26b8f3ec9ee04feaa977f47897ad2d9843e75fc"
    "68d4c91b3e7106c0db713f1f2d398f73";
constexpr auto cbc_256_zero_iv_ciphertext =
    "65914f757671467735d8f7c88aaa6306c45177bd3972bcfe7ae6f8b783115d9d"
    "89c9159886d91e590dd18c370219e738";
constexpr auto cbc_unpadded_plaintext = "Exactly thirty-two bytes long!!!";
constexpr auto cbc_128_unpadded_ciphertext =
    "e1970419a3d4296b2d8e915a5dc7f668c47e9f400b0f4916ae31314e52fcb098";
constexpr auto ctr_plaintext = "Stream cipher test payload using CTR mode.";
constexpr auto ctr_128_ciphertext =
    "4cc2ae2db0e769db7a0f346165e6b3c3be9211ebb2409d65833ec0052c7964251eda4e48862028cc70c0";
constexpr auto ctr_wrapping_plaintext = "Counter wraps past the top of the block.";
constexpr auto ctr_192_wrapping_ciphertext =
    "bb5761ba71afe28f512cf61159bb82eaa836d568ba6beaa6f1c03344698a3fed3448f9a47af2848d";
constexpr auto ctr_carrying_plaintext = "The low half wraps; the high half counts on.";
constexpr auto ctr_256_carrying_ciphertext =
    "0dc7407d4e09771090186b39cad904c4d2cf6ef52cb7c0229d9f6bec7a1cfdcf5d2a67a4488c7464eab176f0";

// Makes the AES a test runs on.
using MakeAes = IEncryptKey::Ptr (*)(const IEncryptKey::Data& key, AesMode mode,
                                     AesPadding padding);

#if __APPLE__
#include "crypto/AesOpenSsl.hpp"

IEncryptKey::Ptr make_open_ssl(const IEncryptKey::Data& key, AesMode mode, AesPadding padding) {
    return std::make_unique<crypto::AesOpenSsl>(key, mode, padding);
}
#endif

// Each test runs on what IEncryptKey::Aes() makes: Apple's on macOS, and
// OpenSSL's elsewhere. On macOS, each runs on OpenSSL's as well.
class IEncryptKeyTest : public testing::TestWithParam<MakeAes> {};

} // namespace

TEST_P(IEncryptKeyTest, AesKeyValidation) {
    const IEncryptKey::Data valid_key_128(aes_128_key_size, std::byte{0x01});
    const IEncryptKey::Data valid_key_192(aes_192_key_size, std::byte{0x02});
    const IEncryptKey::Data valid_key_256(aes_256_key_size, std::byte{0x03});
    const IEncryptKey::Data invalid_key(invalid_key_size, std::byte{0x00});
    const auto make = GetParam();

    EXPECT_NO_THROW(static_cast<void>(make(valid_key_128, AesMode::Cbc, AesPadding::Pkcs7)));
    EXPECT_NO_THROW(static_cast<void>(make(valid_key_192, AesMode::Ctr, AesPadding::None)));
    EXPECT_NO_THROW(static_cast<void>(make(valid_key_256, AesMode::Cbc, AesPadding::Pkcs7)));
    EXPECT_THROW(static_cast<void>(make(invalid_key, AesMode::Cbc, AesPadding::Pkcs7)),
                 std::invalid_argument);
}

TEST_P(IEncryptKeyTest, AesCbcPkcs7Roundtrip) {
    const IEncryptKey::Data key(aes_256_key_size, std::byte{0x42});
    const IEncryptKey::InitializationVector initialization_vector(aes_block_size, std::byte{0x13});
    const auto aes = GetParam()(key, AesMode::Cbc, AesPadding::Pkcs7);
    const auto ciphertext = aes->encrypt(cbc_plaintext, initialization_vector);

    EXPECT_EQ(fixtures::hex(ciphertext), cbc_256_ciphertext);
    EXPECT_EQ(aes->decrypt(ciphertext, initialization_vector), cbc_plaintext);
}

TEST_P(IEncryptKeyTest, AesCbcEmptyIvIsZeros) {
    const IEncryptKey::Data key(aes_256_key_size, std::byte{0x42});
    const auto aes = GetParam()(key, AesMode::Cbc, AesPadding::Pkcs7);
    const auto ciphertext = aes->encrypt(cbc_plaintext, {});

    EXPECT_EQ(fixtures::hex(ciphertext), cbc_256_zero_iv_ciphertext);
    EXPECT_EQ(aes->decrypt(ciphertext, {}), cbc_plaintext);
}

TEST_P(IEncryptKeyTest, AesCbcWithoutPadding) {
    const IEncryptKey::Data key(aes_128_key_size, std::byte{0x01});
    const IEncryptKey::InitializationVector initialization_vector(aes_block_size, std::byte{0x02});
    const auto aes = GetParam()(key, AesMode::Cbc, AesPadding::None);
    const auto ciphertext = aes->encrypt(cbc_unpadded_plaintext, initialization_vector);

    EXPECT_EQ(fixtures::hex(ciphertext), cbc_128_unpadded_ciphertext);
    EXPECT_EQ(aes->decrypt(ciphertext, initialization_vector), cbc_unpadded_plaintext);
    // Without padding, CBC takes only whole blocks.
    EXPECT_THROW(static_cast<void>(aes->encrypt("Not a whole block", initialization_vector)),
                 std::runtime_error);
}

TEST_P(IEncryptKeyTest, AesCbcBadPadding) {
    // These 16 bytes decrypt to a block whose last byte, 0x42, is no padding.
    const IEncryptKey::Data key(aes_256_key_size, std::byte{0x42});
    const IEncryptKey::Data ciphertext(aes_block_size, std::byte{0x00});
    const auto aes = GetParam()(key, AesMode::Cbc, AesPadding::Pkcs7);

    EXPECT_THROW(static_cast<void>(aes->decrypt(ciphertext, {})), std::runtime_error);
}

TEST_P(IEncryptKeyTest, AesCbcPaddingChecked) {
    // Blocks encrypted without padding, then decrypted with it: only those
    // that end in n bytes of n, for n from 1 to 16, are padded plaintext.
    const IEncryptKey::Data key(aes_256_key_size, std::byte{0x42});
    const auto unpadded = GetParam()(key, AesMode::Cbc, AesPadding::None);
    const auto padded = GetParam()(key, AesMode::Cbc, AesPadding::Pkcs7);
    const auto decrypt = [&](const std::string& block) {
        return padded->decrypt(unpadded->encrypt(block, {}), {});
    };

    EXPECT_EQ(decrypt(std::string("fourteen bytes") + "\x02\x02"), "fourteen bytes");
    EXPECT_EQ(decrypt(std::string(aes_block_size, '\x10')), "");
    EXPECT_THROW(static_cast<void>(decrypt(std::string("fourteen bytes") + "\x01\x02")),
                 std::runtime_error);
    EXPECT_THROW(static_cast<void>(decrypt(std::string("fifteen bytes..") + '\0')),
                 std::runtime_error);
    EXPECT_THROW(static_cast<void>(decrypt(std::string("fifteen bytes..") + "\x11")),
                 std::runtime_error);
}

TEST_P(IEncryptKeyTest, AesCtrNoPaddingRoundtrip) {
    const IEncryptKey::Data key(aes_128_key_size, std::byte{0x77});
    const IEncryptKey::InitializationVector initialization_vector(aes_block_size, std::byte{0x55});
    const auto aes = GetParam()(key, AesMode::Ctr, AesPadding::None);
    const auto ciphertext = aes->encrypt(ctr_plaintext, initialization_vector);

    EXPECT_EQ(fixtures::hex(ciphertext), ctr_128_ciphertext);
    EXPECT_EQ(aes->decrypt(ciphertext, initialization_vector), ctr_plaintext);
}

TEST_P(IEncryptKeyTest, AesCtrCounterWraps) {
    // The counter starts at its highest value, so the second block's wraps
    // around to zero, carrying through all 16 bytes.
    const IEncryptKey::Data key(aes_192_key_size, std::byte{0x02});
    const IEncryptKey::InitializationVector initialization_vector(aes_block_size, std::byte{0xFF});
    const auto aes = GetParam()(key, AesMode::Ctr, AesPadding::None);
    const auto ciphertext = aes->encrypt(ctr_wrapping_plaintext, initialization_vector);

    EXPECT_EQ(fixtures::hex(ciphertext), ctr_192_wrapping_ciphertext);
    EXPECT_EQ(aes->decrypt(ciphertext, initialization_vector), ctr_wrapping_plaintext);
}

TEST_P(IEncryptKeyTest, AesCtrCarriesIntoHighHalf) {
    // The low 8 bytes of the counter start at their highest value, and the
    // high 8 at 1, so the second block's counter is 2 in the high 8 bytes
    // and 0 in the low.
    constexpr auto high_half_end = 7;
    const IEncryptKey::Data key(aes_256_key_size, std::byte{0x03});
    IEncryptKey::InitializationVector initialization_vector(aes_block_size, std::byte{0xFF});

    std::fill(initialization_vector.begin(), initialization_vector.begin() + high_half_end,
              std::byte{0x00});
    initialization_vector.at(high_half_end) = std::byte{0x01};

    const auto aes = GetParam()(key, AesMode::Ctr, AesPadding::None);
    const auto ciphertext = aes->encrypt(ctr_carrying_plaintext, initialization_vector);

    EXPECT_EQ(fixtures::hex(ciphertext), ctr_256_carrying_ciphertext);
    EXPECT_EQ(aes->decrypt(ciphertext, initialization_vector), ctr_carrying_plaintext);
}

TEST_P(IEncryptKeyTest, AesCbcEmptyCiphertext) {
    const IEncryptKey::Data key(aes_256_key_size, std::byte{0x42});
    const auto aes = GetParam()(key, AesMode::Cbc, AesPadding::Pkcs7);

    EXPECT_THROW(static_cast<void>(aes->decrypt({}, {})), std::runtime_error);
}

INSTANTIATE_TEST_SUITE_P(Factory, IEncryptKeyTest, testing::Values(&IEncryptKey::Aes));
#if __APPLE__
INSTANTIATE_TEST_SUITE_P(OpenSsl, IEncryptKeyTest, testing::Values(&make_open_ssl));
#endif
