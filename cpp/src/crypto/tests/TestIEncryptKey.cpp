#include <gtest/gtest.h>
#include <cstddef>
#include <stdexcept>
#include <string>

#include "crypto/IEncryptKey.hpp"

using crypto::IEncryptKey;

namespace {

// AES's key sizes, and its block size, which is an IV's size, in bytes.
constexpr auto aes_128_key_size = 16;
constexpr auto aes_192_key_size = 24;
constexpr auto aes_256_key_size = 32;
constexpr auto aes_block_size = 16;
constexpr auto invalid_key_size = 15;

} // namespace

TEST(IEncryptKeyTest, AesKeyValidation) {
    const IEncryptKey::Data valid_key_128(aes_128_key_size, std::byte{0x01});
    const IEncryptKey::Data valid_key_192(aes_192_key_size, std::byte{0x02});
    const IEncryptKey::Data valid_key_256(aes_256_key_size, std::byte{0x03});
    const IEncryptKey::Data invalid_key(invalid_key_size, std::byte{0x00});

    EXPECT_NO_THROW(static_cast<void>(IEncryptKey::Aes(valid_key_128, IEncryptKey::AesMode::Cbc,
                                                       IEncryptKey::AesPadding::Pkcs7)));
    EXPECT_NO_THROW(static_cast<void>(IEncryptKey::Aes(valid_key_192, IEncryptKey::AesMode::Ctr,
                                                       IEncryptKey::AesPadding::None)));
    EXPECT_NO_THROW(static_cast<void>(IEncryptKey::Aes(valid_key_256, IEncryptKey::AesMode::Cbc,
                                                       IEncryptKey::AesPadding::Pkcs7)));
    EXPECT_THROW(static_cast<void>(IEncryptKey::Aes(invalid_key, IEncryptKey::AesMode::Cbc,
                                                    IEncryptKey::AesPadding::Pkcs7)),
                 std::invalid_argument);
}

TEST(IEncryptKeyTest, AesCbcPkcs7Roundtrip) {
    const IEncryptKey::Data key(aes_256_key_size, std::byte{0x42});
    const IEncryptKey::InitializationVector initialization_vector(aes_block_size, std::byte{0x13});
    const auto aes = IEncryptKey::Aes(key, IEncryptKey::AesMode::Cbc, IEncryptKey::AesPadding::Pkcs7);
    const std::string plaintext = "Hello, Libranet cryptographic library!";
    const auto ciphertext = aes->encrypt(plaintext, initialization_vector);

    EXPECT_EQ(aes->decrypt(ciphertext, initialization_vector), plaintext);
}

TEST(IEncryptKeyTest, AesCtrNoPaddingRoundtrip) {
    const IEncryptKey::Data key(aes_128_key_size, std::byte{0x77});
    const IEncryptKey::InitializationVector initialization_vector(aes_block_size, std::byte{0x55});
    const auto aes = IEncryptKey::Aes(key, IEncryptKey::AesMode::Ctr, IEncryptKey::AesPadding::None);
    const std::string plaintext = "Stream cipher test payload using CTR mode.";
    const auto ciphertext = aes->encrypt(plaintext, initialization_vector);

    EXPECT_EQ(aes->decrypt(ciphertext, initialization_vector), plaintext);
}
