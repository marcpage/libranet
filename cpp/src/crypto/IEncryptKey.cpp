#include "IEncryptKey.hpp"
#include "AesMacos.hpp"

#if __APPLE__
using AesType = crypto::AesMacos;
#else
#error "Not implemented"
#endif

namespace crypto {

IEncryptKey::Ptr IEncryptKey::Aes(const Data& key, AesMode mode, AesPadding padding) {
    return std::make_unique<AesType>(key, mode, padding);
}

} // namespace crypto
