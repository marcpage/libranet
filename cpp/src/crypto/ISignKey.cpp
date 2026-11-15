#include "ISignKey.hpp"
#include "Ed25519OpenSsl.hpp"
#include <stdexcept>

using Ed25519Key = crypto::Ed25519OpenSsl;

#if __APPLE__
#include "RsaSignKeyMacos.hpp"
using RsaSignKey = crypto::RsaSignKeyMacos;
#else
#include "RsaSignKeyOpenSsl.hpp"
using RsaSignKey = crypto::RsaSignKeyOpenSsl;
#endif

namespace crypto {

ISignKey::Ptr ISignKey::Ed25519() {
    return Ed25519Key::create();
}

ISignKey::Ptr ISignKey::from_pem(std::string_view pem) {
    try {
        return RsaSignKey::from_pem(pem);
    } catch (const std::exception&) {
        return Ed25519Key::from_pem(pem);
    }
}

} // namespace crypto
