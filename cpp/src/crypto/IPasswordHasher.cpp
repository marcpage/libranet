#include "IPasswordHasher.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>

#include "Argon2idOpenSsl.hpp"

using Argon2idType = crypto::Argon2idOpenSsl;

using std::size_t;
using std::uint32_t;

namespace crypto {

IPasswordHasher::Ptr IPasswordHasher::Argon2id(
    uint32_t memory_cost_kib,
    uint32_t iterations,
    uint32_t parallelism,
    size_t salt_length,
    size_t hash_length) {
    return std::make_unique<Argon2idType>(
        memory_cost_kib,
        iterations,
        parallelism,
        salt_length,
        hash_length);
}

} // namespace crypto
