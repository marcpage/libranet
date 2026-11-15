#include "IHasher.hpp"
#include "Sha256Macos.hpp"

#if __APPLE__
using Sha256Type = crypto::Sha256Macos;
#else
#include "Sha256OpenSsl.hpp"
using Sha256Type = crypto::Sha256OpenSsl;
#endif

namespace crypto {

IHasher::Ptr IHasher::Sha256() {
    return std::make_unique<Sha256Type>();
}

} // crypto
