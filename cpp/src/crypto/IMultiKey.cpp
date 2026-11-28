#include "IMultiKey.hpp"

#include <cstddef>
#include <string_view>

#if __APPLE__
#include "RsaMacos.hpp"
using RsaType = crypto::RsaMacos;
#else
#include "RsaOpenSsl.hpp"
using RsaType = crypto::RsaOpenSsl;
#endif

using std::size_t;
using std::string_view;

namespace crypto {

IMultiKey::Ptr IMultiKey::Rsa(size_t key_bits) {
    return RsaType::for_bit_size(key_bits);
}

IMultiKey::Ptr IMultiKey::from_pem(string_view pem) {
    return RsaType::from_pem(pem);
}

} // namespace crypto
