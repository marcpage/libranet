#include "IMultiKey.hpp"
#include "RsaMacos.hpp"

#if __APPLE__
    using RsaType = crypto::RsaMacos;
#else
    #error "Not implemented for this platform"
#endif

namespace crypto {

IMultiKey::Ptr IMultiKey::Rsa(std::size_t key_bits) {
    return RsaType::for_bit_size(key_bits);
}

IMultiKey::Ptr IMultiKey::from_pem(std::string_view pem) {
    return RsaType::from_pem(pem);
}

} // namespace crypto
