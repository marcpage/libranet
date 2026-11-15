#pragma once

#if __APPLE__

#include <CommonCrypto/CommonCryptor.h>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "IEncryptKey.hpp"

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

private:
    AesMacos(const AesMacos&) = delete;
    AesMacos& operator=(const AesMacos&) = delete;

    Data m_key;
    AesMode m_mode;
    AesPadding m_padding;

    [[nodiscard]] Data process(CCOperation op, std::string_view input,
                               const InitializationVector& vector) const;
    [[nodiscard]] std::size_t process_cbc(CCOperation op, std::string_view input,
                                          const InitializationVector& vector, Data& output) const;
    [[nodiscard]] std::size_t process_ctr(CCOperation op, std::string_view input,
                                          const InitializationVector& vector, Data& output) const;
    [[nodiscard]] std::size_t process_ctr_part(CCOperation op, std::string_view input,
                                               const InitializationVector& counter, Data& output,
                                               std::size_t offset) const;
    [[nodiscard]] static std::size_t unpadded_size(const Data& decrypted, std::size_t size);
    [[nodiscard]] static std::uint64_t low_count(const InitializationVector& counter);
    static void carry_count(InitializationVector& counter);
};

inline AesMacos::AesMacos(Data key, AesMode mode, AesPadding padding)
    : m_key(std::move(key))
    , m_mode(mode)
    , m_padding(padding) {

    if (m_key.size() != 16 && m_key.size() != 24 && m_key.size() != 32) {
        throw std::invalid_argument(
            "Invalid AES key size. Must be 128, 192, or 256 bits.");
    }
}

inline IEncryptKey::Data AesMacos::encrypt(std::string_view plaintext,
                                           const InitializationVector& vector) const {
    return process(kCCEncrypt, plaintext, vector);
}

inline std::string AesMacos::decrypt(const Data& ciphertext,
                                     const InitializationVector& vector) const {
    const Data raw_in(ciphertext.begin(), ciphertext.end());
    Data raw_out = process(
        kCCDecrypt,
        std::string_view(reinterpret_cast<const char*>(raw_in.data()), raw_in.size()),
        vector);

    return std::string(reinterpret_cast<char*>(raw_out.data()), raw_out.size());
}

inline IEncryptKey::Data AesMacos::process(CCOperation op, std::string_view input,
                                           const InitializationVector& vector) const {
    if (m_mode != AesMode::Cbc && m_mode != AesMode::Ctr) {
        throw std::runtime_error("Only Cbc and Ctr supported.");
    }

    const std::size_t buffer_size = input.size() + kCCBlockSizeAES128;
    Data output(buffer_size);
    const auto data_moved = AesMode::Cbc == m_mode
                                ? process_cbc(op, input, vector, output)
                                : process_ctr(op, input, vector, output);

    output.resize(data_moved);
    return output;
}

inline std::size_t AesMacos::process_cbc(CCOperation op, std::string_view input,
                                         const InitializationVector& vector, Data& output) const {
    // CommonCrypto removes padding without checking it, so it decrypts with
    // none, and the padding is checked and removed here.
    const bool padded = m_padding == AesPadding::Pkcs7;
    CCOptions options = (padded && op == kCCEncrypt) ? kCCOptionPKCS7Padding : 0;
    std::size_t data_moved = 0;

    CCCryptorStatus status = CCCrypt(
        op, kCCAlgorithmAES, options,
        m_key.data(), m_key.size(),
        vector.empty() ? nullptr : reinterpret_cast<const void*>(vector.data()),
        input.data(), input.size(),
        output.data(), output.size(), &data_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CBC operation failed with status: "
                                 + std::to_string(status));
    }

    return (padded && op == kCCDecrypt) ? unpadded_size(output, data_moved) : data_moved;
}

// How long the first size bytes of decrypted are without the PKCS #7 padding
// that ends them: n bytes, each n, for n from 1 to the block size. The whole
// last block is checked whatever n is, so that the time a check takes does
// not tell what it found.
inline std::size_t AesMacos::unpadded_size(const Data& decrypted, std::size_t size) {
    constexpr std::size_t block_size = kCCBlockSizeAES128;

    if (size < block_size) {
        throw std::runtime_error("AES-CBC decryption found no padding.");
    }

    const auto padding = std::to_integer<std::size_t>(decrypted.at(size - 1));
    auto bad = static_cast<std::size_t>(padding == 0) | static_cast<std::size_t>(padding > block_size);

    for (std::size_t index = 1; index <= block_size; ++index) {
        const auto in_padding = static_cast<std::size_t>(index <= padding);

        bad |= in_padding * (std::to_integer<std::size_t>(decrypted.at(size - index)) ^ padding);
    }

    if (bad != 0) {
        throw std::runtime_error("AES-CBC decryption found bad padding.");
    }

    return size - padding;
}

// CommonCrypto counts up through only the low 64 bits of the counter block,
// where OpenSSL and the Python node carry into the high 64 bits. So where
// the low 64 bits would wrap around, the rest is processed again from the
// counter that carries. No message is long enough to wrap them twice.
inline std::size_t AesMacos::process_ctr(CCOperation op, std::string_view input,
                                         const InitializationVector& vector, Data& output) const {
    constexpr std::size_t block_size = kCCBlockSizeAES128;
    // An empty initialization vector is all zeros, as CommonCrypto takes a
    // null one.
    InitializationVector counter = vector.empty() ? InitializationVector(block_size) : vector;

    if (counter.size() != block_size) {
        throw std::invalid_argument("An AES initialization vector must be 16 bytes.");
    }

    const auto low = low_count(counter);
    const std::uint64_t blocks_before_wrap = 0 - low;
    const auto blocks = (input.size() + block_size - 1) / block_size;

    if (low == 0 || blocks <= blocks_before_wrap) {
        return process_ctr_part(op, input, counter, output, 0);
    }

    const auto first_size = static_cast<std::size_t>(blocks_before_wrap) * block_size;
    const auto first_moved = process_ctr_part(op, input.substr(0, first_size), counter, output, 0);

    carry_count(counter);
    return first_moved + process_ctr_part(op, input.substr(first_size), counter, output, first_moved);
}

// The low 64 bits of the counter block, big-endian.
inline std::uint64_t AesMacos::low_count(const InitializationVector& counter) {
    constexpr std::size_t half = kCCBlockSizeAES128 / 2;
    constexpr auto byte_bits = 8U;
    std::uint64_t low = 0;

    for (std::size_t index = half; index < kCCBlockSizeAES128; ++index) {
        low = (low << byte_bits) | std::to_integer<std::uint64_t>(counter.at(index));
    }

    return low;
}

// The counter block after the low 64 bits wrap around: those are zero, and
// the high 64 bits are one more, big-endian.
inline void AesMacos::carry_count(InitializationVector& counter) {
    constexpr std::size_t half = kCCBlockSizeAES128 / 2;

    for (std::size_t index = half; index < kCCBlockSizeAES128; ++index) {
        counter.at(index) = std::byte{0};
    }

    for (std::size_t index = half; index-- > 0;) {
        counter.at(index) = static_cast<std::byte>(std::to_integer<unsigned int>(counter.at(index)) + 1U);

        if (counter.at(index) != std::byte{0}) {
            break;
        }
    }
}

inline std::size_t AesMacos::process_ctr_part(CCOperation op, std::string_view input,
                                              const InitializationVector& counter, Data& output,
                                              std::size_t offset) const {
    std::size_t update_moved = 0;
    std::size_t final_moved = 0;
    struct CryptorGuard {
        CCCryptorRef ref{nullptr};
        ~CryptorGuard() {
            if (ref) {
                CCCryptorRelease(ref);
            }
        }
    } cryptor;
    CCCryptorStatus status = CCCryptorCreateWithMode(
        op, kCCModeCTR, kCCAlgorithmAES, ccNoPadding,
        reinterpret_cast<const void*>(counter.data()),
        m_key.data(), m_key.size(),
        nullptr, 0, 0, kCCModeOptionCTR_BE, &cryptor.ref);

    if (status != kCCSuccess) {
        throw std::runtime_error("Failed to create AES-CTR cryptor. Status: "
                                 + std::to_string(status));
    }

    status = CCCryptorUpdate(cryptor.ref, input.data(), input.size(), output.data() + offset,
                             output.size() - offset, &update_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CTR update failed with status: "
                                 + std::to_string(status));
    }

    status = CCCryptorFinal(cryptor.ref, output.data() + offset + update_moved,
                            output.size() - offset - update_moved, &final_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CTR final failed with status: "
                                 + std::to_string(status));
    }

    return update_moved + final_moved;
}

} // namespace crypto

#endif // __APPLE__
