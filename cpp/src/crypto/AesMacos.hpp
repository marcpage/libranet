#pragma once

#if __APPLE__

#include <vector>
#include <string>
#include <string_view>
#include <stdexcept>
#include <CommonCrypto/CommonCryptor.h>

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
        vector
    );

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
    CCOptions options = (m_padding == AesPadding::Pkcs7) ? kCCOptionPKCS7Padding : 0;
    std::size_t data_moved = 0;

    CCCryptorStatus status = CCCrypt(
        op, kCCAlgorithmAES, options,
        m_key.data(), m_key.size(),
        vector.empty() ? nullptr : reinterpret_cast<const void*>(vector.data()),
        input.data(), input.size(),
        output.data(), output.size(), &data_moved
    );

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CBC operation failed with status: "
                                                            + std::to_string(status));
    }

    return data_moved;
}

inline std::size_t AesMacos::process_ctr(CCOperation op, std::string_view input,
                                const InitializationVector& vector, Data& output) const {
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
        vector.empty() ? nullptr : reinterpret_cast<const void*>(vector.data()),
        m_key.data(), m_key.size(),
        nullptr, 0, 0, kCCModeOptionCTR_BE, &cryptor.ref
    );

    if (status != kCCSuccess) {
        throw std::runtime_error("Failed to create AES-CTR cryptor. Status: "
                                                                + std::to_string(status));
    }

    status = CCCryptorUpdate(cryptor.ref, input.data(), input.size(), output.data(),
                                                            output.size(), &update_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CTR update failed with status: "
                                                                + std::to_string(status));
    }

    status = CCCryptorFinal(cryptor.ref, output.data()
                            + update_moved, output.size() - update_moved, &final_moved);

    if (status != kCCSuccess) {
        throw std::runtime_error("AES-CTR final failed with status: "
                                                                + std::to_string(status));
    }

    return update_moved + final_moved;
}

} // namespace crypto

#endif // __APPLE__
