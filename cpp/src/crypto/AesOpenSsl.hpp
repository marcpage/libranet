#pragma once

#include <cstddef>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <openssl/crypto.h>
#include <openssl/evp.h>

#include "IEncryptKey.hpp"
#include "OpenSsl.hpp"

namespace crypto {

// AES in CBC or CTR mode, built on OpenSSL. As with Apple's CommonCrypto,
// an empty initialization vector is all zeros, and CTR counts up through
// the whole 16-byte block, big-endian.
class AesOpenSsl : public IEncryptKey {
public:
    AesOpenSsl(Data key, AesMode mode, AesPadding padding);
    ~AesOpenSsl() override;

    [[nodiscard]] Data encrypt(std::string_view plaintext,
                               const InitializationVector& vector) const override;
    [[nodiscard]] std::string decrypt(const Data& ciphertext,
                                      const InitializationVector& vector) const override;

    AesOpenSsl(AesOpenSsl&&) noexcept = default;
    AesOpenSsl& operator=(AesOpenSsl&&) noexcept = default;

    AesOpenSsl(const AesOpenSsl&) = delete;
    AesOpenSsl& operator=(const AesOpenSsl&) = delete;

private:
    using Context = OpenSslObject<EVP_CIPHER_CTX, EVP_CIPHER_CTX_free>;

    // AES's key sizes, and its block size, which is also an initialization
    // vector's, in bytes.
    static constexpr std::size_t aes_128_key_size = 16;
    static constexpr std::size_t aes_192_key_size = 24;
    static constexpr std::size_t aes_256_key_size = 32;
    static constexpr std::size_t aes_block_size = 16;

    Data m_key;
    AesPadding m_padding;
    const EVP_CIPHER* m_cipher;

    [[nodiscard]] static const EVP_CIPHER* _cipher(std::size_t key_size, AesMode mode);
    [[nodiscard]] Data _process(bool encrypting, const unsigned char* input, std::size_t size,
                                const InitializationVector& vector) const;
};

inline AesOpenSsl::AesOpenSsl(Data key, AesMode mode, AesPadding padding)
    : m_key(std::move(key)), m_padding(padding), m_cipher(_cipher(m_key.size(), mode)) {}

// The key is wiped from memory once it is no longer needed.
inline AesOpenSsl::~AesOpenSsl() {
    OPENSSL_cleanse(m_key.data(), m_key.size());
}

inline IEncryptKey::Data AesOpenSsl::encrypt(std::string_view plaintext,
                                             const InitializationVector& vector) const {
    return _process(true, OpenSslBytes::of(plaintext), plaintext.size(), vector);
}

inline std::string AesOpenSsl::decrypt(const Data& ciphertext,
                                       const InitializationVector& vector) const {
    const auto plaintext = _process(false, OpenSslBytes::of(ciphertext), ciphertext.size(), vector);
    std::string text(plaintext.size(), '\0');

    for (std::size_t index = 0; index < plaintext.size(); ++index) {
        text.at(index) = static_cast<char>(plaintext.at(index));
    }

    return text;
}

inline const EVP_CIPHER* AesOpenSsl::_cipher(std::size_t key_size, AesMode mode) {
    const auto cbc = AesMode::Cbc == mode;

    switch (key_size) {
        case aes_128_key_size: return cbc ? EVP_aes_128_cbc() : EVP_aes_128_ctr();
        case aes_192_key_size: return cbc ? EVP_aes_192_cbc() : EVP_aes_192_ctr();
        case aes_256_key_size: return cbc ? EVP_aes_256_cbc() : EVP_aes_256_ctr();
        default: throw std::invalid_argument("Invalid AES key size. Must be 128, 192, or 256 bits");
    }
}

inline IEncryptKey::Data AesOpenSsl::_process(bool encrypting, const unsigned char* input,
                                              std::size_t size,
                                              const InitializationVector& vector) const {
    if (!vector.empty() && vector.size() != aes_block_size) {
        throw std::invalid_argument("An AES initialization vector must be 16 bytes");
    }

    if (size > static_cast<std::size_t>(std::numeric_limits<int>::max()) - aes_block_size) {
        throw std::invalid_argument("Too much to encrypt or decrypt with AES at once");
    }

    const InitializationVector zeros(aes_block_size);
    const Context context(EVP_CIPHER_CTX_new());

    if (!context
        || EVP_CipherInit_ex2(context.get(), m_cipher, OpenSslBytes::of(m_key),
                              OpenSslBytes::of(vector.empty() ? zeros : vector),
                              encrypting ? 1 : 0, nullptr)
               != 1
        || EVP_CIPHER_CTX_set_padding(context.get(), AesPadding::Pkcs7 == m_padding ? 1 : 0)
               != 1) {
        throw OpenSslError("Failed to start AES");
    }

    // Padding adds at most a block.
    Data output(size + aes_block_size);
    int updated = 0;
    int finished = 0;

    if (EVP_CipherUpdate(context.get(), OpenSslBytes::of(output), &updated, input,
                         static_cast<int>(size))
            != 1
        || EVP_CipherFinal_ex(context.get(),
                              OpenSslBytes::of(output, static_cast<std::size_t>(updated)),
                              &finished)
               != 1) {
        throw OpenSslError(encrypting ? "AES encryption failed" : "AES decryption failed");
    }

    output.resize(static_cast<std::size_t>(updated) + static_cast<std::size_t>(finished));
    return output;
}

} // namespace crypto
