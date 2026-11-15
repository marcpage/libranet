#pragma once

#include "IPasswordHasher.hpp"
#include "phc-winner-argon2/argon2.h"
#include <string>
#include <string_view>
#include <stdexcept>
#include <vector>
#include <random>
#include <cstring>

namespace crypto {

class Argon2id : public IPasswordHasher {
public:
    Argon2id(
        uint32_t memory_cost_kib,
        uint32_t iterations,
        uint32_t parallelism,
        std::size_t salt_length,
        std::size_t hash_length
    );

    ~Argon2id() override = default;

    Argon2id(const Argon2id&) = delete;
    Argon2id& operator=(const Argon2id&) = delete;
    Argon2id(Argon2id&&) noexcept = default;
    Argon2id& operator=(Argon2id&&) noexcept = default;

    [[nodiscard]] std::string hash_password(std::string_view password) const override;
    [[nodiscard]] bool verify_password(std::string_view password, std::string_view hash) const override;

private:
    uint32_t memory_cost_kib_;
    uint32_t iterations_;
    uint32_t parallelism_;
    std::size_t salt_length_;
    std::size_t hash_length_;
};

inline Argon2id::Argon2id(
    uint32_t memory_cost_kib,
    uint32_t iterations,
    uint32_t parallelism,
    std::size_t salt_length,
    std::size_t hash_length
)
    : memory_cost_kib_(memory_cost_kib)
    , iterations_(iterations)
    , parallelism_(parallelism)
    , salt_length_(salt_length)
    , hash_length_(hash_length) {}

inline std::string Argon2id::hash_password(std::string_view password) const {
    // 1. Generate a cryptographically secure random salt
    std::vector<unsigned char> salt(salt_length_);
    std::random_device rd;
    for (auto& b : salt) {
        b = static_cast<unsigned char>(rd());
    }

    // 2. Determine required encoded string length (explicitly cast sizes to uint32_t)
    std::string encoded(argon2_encodedlen(
        iterations_,
        memory_cost_kib_,
        parallelism_,
        static_cast<uint32_t>(salt_length_),
        static_cast<uint32_t>(hash_length_),
        Argon2_id
    ), '\0');

    // 3. Call argon2id_hash_encoded with exact parameters
    int result = argon2id_hash_encoded(
        iterations_,
        memory_cost_kib_,
        parallelism_,
        password.data(),
        password.size(),
        salt.data(),
        salt.size(),
        static_cast<uint32_t>(hash_length_),
        encoded.data(),
        encoded.size()
    );

    if (result != ARGON2_OK) {
        throw std::runtime_error(std::string("Failed to hash password: ") + argon2_error_message(result));
    }

    // Resize down to the null-terminated string length produced
    encoded.resize(std::strlen(encoded.c_str()));
    return encoded;
}

inline bool Argon2id::verify_password(std::string_view password, std::string_view hash) const {
    int result = argon2id_verify(
        hash.data(),
        password.data(),
        password.size()
    );

    return (result == ARGON2_OK);
}

} // namespace crypto
