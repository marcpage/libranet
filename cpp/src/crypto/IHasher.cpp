#include "IHasher.hpp"

#include <memory>
#include <string>

#include "types/Converter.hpp"

#if __APPLE__
#include "Sha256Macos.hpp"
using Sha256Type = crypto::Sha256Macos;
#else
#include "Sha256OpenSsl.hpp"
using Sha256Type = crypto::Sha256OpenSsl;
#endif

using std::string;
using types::Converter;

namespace crypto {

IHasher::Ptr IHasher::Sha256() {
    return std::make_unique<Sha256Type>();
}

// The digest of what was added, with nothing more.
IHasher::Data IHasher::digest() {
    return digest(Data());
}

string IHasher::hex_digest(const Data& data) {
    return Converter::hex_of(digest(data));
}

string IHasher::hex_digest() {
    return hex_digest(Data());
}

} // namespace crypto
