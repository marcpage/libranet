#include "IEncryptKey.hpp"

#include <memory>

#if __APPLE__
#include "AesMacos.hpp"
using AesType = crypto::AesMacos;
#else
#include "AesOpenSsl.hpp"
using AesType = crypto::AesOpenSsl;
#endif

namespace crypto {

IEncryptKey::Ptr IEncryptKey::Aes(const Data& key, AesMode mode, AesPadding padding) {
    return std::make_unique<AesType>(key, mode, padding);
}

} // namespace crypto
