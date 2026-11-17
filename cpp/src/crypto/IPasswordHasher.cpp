#include "IPasswordHasher.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>

#include "Argon2idOpenSsl.hpp"

using Argon2idType = crypto::Argon2idOpenSsl;

namespace crypto {

IPasswordHasher::Ptr IPasswordHasher::Argon2id(
    uint32_t memory_cost_kib,
    uint32_t iterations,
    uint32_t parallelism,
    std::size_t salt_length,
    std::size_t hash_length) {
    return std::make_unique<Argon2idType>(
        memory_cost_kib,
        iterations,
        parallelism,
        salt_length,
        hash_length);
}

} // namespace crypto
