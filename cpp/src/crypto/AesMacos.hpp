#pragma once

#if __APPLE__

#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#include <CommonCrypto/CommonCryptor.h>

#include "IEncryptKey.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

namespace crypto {

class AesMacos : public IEncryptKey {
public:
    AesMacos(Data key, AesMode mode, AesPadding padding);

    [[nodiscard]] Data encrypt(std::string_view plaintext,
                               const InitializationVector& vector) const override;
    [[nodiscard]] std::string decrypt(const Data& ciphertext,
                                      const InitializationVector& vector) const override;

    ~AesMacos() override = default;
    AesMacos(AesMacos&&) noexcept = default;
    AesMacos& operator=(AesMacos&&) noexcept = default;

    AesMacos(const AesMacos&) = delete;
    AesMacos& operator=(const AesMacos&) = delete;

private:
    // Releases a cryptor CommonCrypto made.
    struct CryptorRelease {
        void operator()(CCCryptorRef cryptor) const noexcept {
            CCCryptorRelease(cryptor);
        }
    };

    using Cryptor = std::unique_ptr<std::remove_pointer_t<CCCryptorRef>, CryptorRelease>;

    // AES's key sizes, in bytes.
    static constexpr std::size_t aes_128_key_bytes = 16;
    static constexpr std::size_t aes_192_key_bytes = 24;
    static constexpr std::size_t aes_256_key_bytes = 32;

    Data m_key;
    AesMode m_mode;
    AesPadding m_padding;

    [[nodiscard]] Data _process(CCOperation operation, std::string_view input,
                                const InitializationVector& vector) const;
    [[nodiscard]] std::size_t _process_cbc(CCOperation operation, std::string_view input,
                                           const InitializationVector& vector, Data& output) const;
    [[nodiscard]] std::size_t _process_ctr(CCOperation operation, std::string_view input,
                                           const InitializationVector& vector, Data& output) const;
    [[nodiscard]] std::size_t _process_ctr_part(CCOperation operation, std::string_view input,
                                                const InitializationVector& counter, Data& output,
                                                std::size_t offset) const;
    [[nodiscard]] static std::size_t _unpadded_size(const Data& decrypted, std::size_t size);
    [[nodiscard]] static std::uint64_t _low_count(const InitializationVector& counter);
    static void _carry_count(InitializationVector& counter);
};

inline AesMacos::AesMacos(Data key, AesMode mode, AesPadding padding)
    : m_key(std::move(key))
    , m_mode(mode)
    , m_padding(padding) {
    if (m_key.size() != aes_128_key_bytes && m_key.size() != aes_192_key_bytes
        && m_key.size() != aes_256_key_bytes) {
        throw std::invalid_argument(
            "Invalid AES key size. Must be 128, 192, or 256 bits");
    }
}

inline IEncryptKey::Data AesMacos::encrypt(std::string_view plaintext,
                                           const InitializationVector& vector) const {
    return _process(kCCEncrypt, plaintext, vector);
}

inline std::string AesMacos::decrypt(const Data& ciphertext,
                                     const InitializationVector& vector) const {
    const auto input = types::Converter::text_of(ciphertext);

    return types::Converter::text_of(_process(kCCDecrypt, input, vector));
}

inline IEncryptKey::Data AesMacos::_process(CCOperation operation, std::string_view input,
                                            const InitializationVector& vector) const {
    if (m_mode != AesMode::Cbc && m_mode != AesMode::Ctr) {
        throw std::runtime_error("Only Cbc and Ctr supported");
    }

    const std::size_t buffer_size = input.size() + kCCBlockSizeAES128;
    Data output(buffer_size);
    const auto data_moved = AesMode::Cbc == m_mode
                                ? _process_cbc(operation, input, vector, output)
                                : _process_ctr(operation, input, vector, output);

    output.resize(data_moved);
    return output;
}

inline std::size_t AesMacos::_process_cbc(CCOperation operation, std::string_view input,
                                          const InitializationVector& vector, Data& output) const {
    // CommonCrypto removes padding without checking it, so it decrypts with
    // none, and the padding is checked and removed here.
    const bool padded = AesPadding::Pkcs7 == m_padding;
    const CCOptions options = (padded && kCCEncrypt == operation) ? kCCOptionPKCS7Padding : 0;
    std::size_t data_moved = 0;

    const CCCryptorStatus status = CCCrypt(
        operation, kCCAlgorithmAES, options,
        m_key.data(), m_key.size(),
        vector.empty() ? nullptr : vector.data(),
        input.data(), input.size(),
        output.data(), output.size(), &data_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CBC operation failed with status: "
                                 + std::to_string(status));
    }

    return (padded && kCCDecrypt == operation) ? _unpadded_size(output, data_moved) : data_moved;
}

// How long the first size bytes of decrypted are without the PKCS #7 padding
// that ends them: n bytes, each n, for n from 1 to the block size. The whole
// last block is checked whatever n is, so that the time a check takes does
// not tell what it found.
inline std::size_t AesMacos::_unpadded_size(const Data& decrypted, std::size_t size) {
    constexpr std::size_t block_size = kCCBlockSizeAES128;

    if (size < block_size) {
        throw std::runtime_error("AES-CBC decryption found no padding");
    }

    const auto padding = std::to_integer<std::size_t>(decrypted.at(size - 1));
    auto bad = static_cast<std::size_t>(0 == padding)
               | static_cast<std::size_t>(padding > block_size);

    for (std::size_t index = 1; index <= block_size; ++index) {
        const auto in_padding = static_cast<std::size_t>(index <= padding);

        bad |= in_padding * (std::to_integer<std::size_t>(decrypted.at(size - index)) ^ padding);
    }

    if (bad != 0) {
        throw std::runtime_error("AES-CBC decryption found bad padding");
    }

    return size - padding;
}

// CommonCrypto counts up through only the low 64 bits of the counter block,
// where OpenSSL and the Python node carry into the high 64 bits. So where
// the low 64 bits would wrap around, the rest is processed again from the
// counter that carries. No message is long enough to wrap them twice.
inline std::size_t AesMacos::_process_ctr(CCOperation operation, std::string_view input,
                                          const InitializationVector& vector, Data& output) const {
    constexpr std::size_t block_size = kCCBlockSizeAES128;
    // An empty initialization vector is all zeros, as CommonCrypto takes a
    // null one.
    InitializationVector counter = vector.empty() ? InitializationVector(block_size) : vector;

    if (counter.size() != block_size) {
        throw std::invalid_argument("An AES initialization vector must be 16 bytes");
    }

    const auto low = _low_count(counter);
    const std::uint64_t blocks_before_wrap = 0 - low;
    const auto blocks = (input.size() + block_size - 1) / block_size;

    if (0 == low || blocks <= blocks_before_wrap) {
        return _process_ctr_part(operation, input, counter, output, 0);
    }

    const auto first_size = static_cast<std::size_t>(blocks_before_wrap) * block_size;
    const auto first_moved = _process_ctr_part(operation, input.substr(0, first_size), counter,
                                               output, 0);

    _carry_count(counter);
    return first_moved
           + _process_ctr_part(operation, input.substr(first_size), counter, output, first_moved);
}

// The low 64 bits of the counter block, big-endian.
inline std::uint64_t AesMacos::_low_count(const InitializationVector& counter) {
    constexpr std::size_t half = kCCBlockSizeAES128 / 2;
    std::uint64_t low = 0;

    for (std::size_t index = half; index < kCCBlockSizeAES128; ++index) {
        low = (low << types::bits_per_byte) | std::to_integer<std::uint64_t>(counter.at(index));
    }

    return low;
}

// The counter block after the low 64 bits wrap around: those are zero, and
// the high 64 bits are one more, big-endian.
inline void AesMacos::_carry_count(InitializationVector& counter) {
    constexpr std::size_t half = kCCBlockSizeAES128 / 2;

    for (std::size_t index = half; index < kCCBlockSizeAES128; ++index) {
        counter.at(index) = std::byte{0};
    }

    for (std::size_t index = half; index-- > 0;) {
        const auto incremented = std::to_integer<unsigned int>(counter.at(index)) + 1U;

        counter.at(index) = static_cast<std::byte>(incremented);

        if (counter.at(index) != std::byte{0}) {
            break;
        }
    }
}

// The output is written from offset, which is before its end: it has room
// for a block more than the whole input.
inline std::size_t AesMacos::_process_ctr_part(CCOperation operation, std::string_view input,
                                               const InitializationVector& counter, Data& output,
                                               std::size_t offset) const {
    std::size_t update_moved = 0;
    std::size_t final_moved = 0;
    CCCryptorRef created = nullptr;
    CCCryptorStatus status = CCCryptorCreateWithMode(
        operation, kCCModeCTR, kCCAlgorithmAES, ccNoPadding,
        counter.data(),
        m_key.data(), m_key.size(),
        nullptr, 0, 0, kCCModeOptionCTR_BE, &created);
    const Cryptor cryptor(created);

    if (status != kCCSuccess) {
        throw std::runtime_error("Failed to create AES-CTR cryptor. Status: "
                                 + std::to_string(status));
    }

    status = CCCryptorUpdate(cryptor.get(), input.data(), input.size(), &output.at(offset),
                             output.size() - offset, &update_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CTR update failed with status: "
                                 + std::to_string(status));
    }

    status = CCCryptorFinal(cryptor.get(), &output.at(offset + update_moved),
                            output.size() - offset - update_moved, &final_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CTR final failed with status: "
                                 + std::to_string(status));
    }

    return update_moved + final_moved;
}

} // namespace crypto

#endif // __APPLE__
