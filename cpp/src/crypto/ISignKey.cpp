#include "ISignKey.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <vector>

#include "Ed25519OpenSsl.hpp"
#include "Pem.hpp"
#include "types/Constants.hpp"

using Ed25519Key = crypto::Ed25519OpenSsl;

#if __APPLE__
#include "RsaSignKeyMacos.hpp"
using RsaSignKey = crypto::RsaSignKeyMacos;
#else
#include "RsaSignKeyOpenSsl.hpp"
using RsaSignKey = crypto::RsaSignKeyOpenSsl;
#endif

using crypto::Pem;
using std::array;
using std::byte;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::ptrdiff_t;
using std::size_t;
using std::string_view;
using std::uint8_t;
using std::vector;
using types::bits_per_byte;

namespace {

// Where a DER element's contents start and end, in the bytes it was read
// from.
struct Contents {
    size_t start;
    size_t end;
};

constexpr byte integer_tag{0x02};
constexpr byte object_identifier_tag{0x06};
constexpr byte sequence_tag{0x30};

// The object identifiers of the algorithms, as DER encodes them:
// rsaEncryption, 1.2.840.113549.1.1.1, from RFC 8017 Appendix A.1, and
// id-Ed25519, 1.3.101.112, from RFC 8410 §3.
constexpr array<uint8_t, 9> rsa_encryption{
    0x2A,
    0x86,
    0x48,
    0x86,
    0xF7,
    0x0D,
    0x01,
    0x01,
    0x01,
};
constexpr array<uint8_t, 3> ed25519{0x2B, 0x65, 0x70};

// The contents of the DER element at offset in der, if it has tag and ends
// by limit.
optional<Contents> contents_at(const vector<byte>& der, size_t offset,
                               size_t limit, byte tag) {
    // A first length byte from 0x80 up says how many bytes of length follow
    // it, big-endian; a key's lengths take no more than four.
    constexpr size_t long_length = 0x80;
    constexpr size_t most_length_bytes = 4;

    if (limit > der.size() || offset >= limit || limit - offset < 2 || der.at(offset) != tag) {
        return nullopt;
    }

    auto size = std::to_integer<size_t>(der.at(offset + 1));
    auto start = offset + 2;

    if (size >= long_length) {
        const auto length_bytes = size - long_length;

        if (0 == length_bytes || length_bytes > most_length_bytes
            || limit - start < length_bytes) {
            return nullopt;
        }

        size = 0;

        for (size_t index = 0; index < length_bytes; ++index) {
            size = (size << bits_per_byte)
                   | std::to_integer<size_t>(der.at(start + index));
        }

        start += length_bytes;
    }

    if (limit - start < size) {
        return nullopt;
    }

    return Contents{start, start + size};
}

// The object identifier of the algorithm a key is for, in a PEM labeled
// "PUBLIC KEY", which holds a SubjectPublicKeyInfo, or "PRIVATE KEY", which
// holds a PKCS #8 private key. Either is a SEQUENCE that holds an
// AlgorithmIdentifier, a SEQUENCE that starts with the identifier; in
// PKCS #8, after an INTEGER, the version. There is none in a PEM of another
// label, or in DER of another shape.
optional<Contents> algorithm(const Pem::Unwrapped& pem) {
    const auto& der = pem.der_bytes;
    const auto info = contents_at(der, 0, der.size(), sequence_tag);

    if (!info || (pem.label != "PUBLIC KEY" && pem.label != "PRIVATE KEY")) {
        return nullopt;
    }

    auto offset = info->start;

    if ("PRIVATE KEY" == pem.label) {
        const auto version = contents_at(der, offset, info->end, integer_tag);

        if (!version) {
            return nullopt;
        }

        offset = version->end;
    }

    const auto identifier = contents_at(der, offset, info->end, sequence_tag);

    if (!identifier) {
        return nullopt;
    }

    return contents_at(der, identifier->start, identifier->end, object_identifier_tag);
}

// Whether the key in the PEM is for the algorithm whose object identifier,
// as DER encodes it, is expected.
template<size_t size>
bool is_for(const Pem::Unwrapped& pem, const array<uint8_t, size>& expected) {
    const auto found = algorithm(pem);

    if (!found || found->end - found->start != size) {
        return false;
    }

    return std::equal(expected.begin(), expected.end(),
                      std::next(pem.der_bytes.begin(), static_cast<ptrdiff_t>(found->start)),
                      [](uint8_t wanted, byte held) {
        return byte{wanted} == held;
    });
}

// Whether the PEM holds an RSA key: in PKCS #1, labeled "RSA PRIVATE KEY" or
// "RSA PUBLIC KEY", or in PKCS #8 or SubjectPublicKeyInfo, as rsaEncryption.
bool is_rsa(const Pem::Unwrapped& pem) {
    return "RSA PRIVATE KEY" == pem.label || "RSA PUBLIC KEY" == pem.label
           || is_for(pem, rsa_encryption);
}

// Whether the PEM holds an Ed25519 key, in PKCS #8 or SubjectPublicKeyInfo.
bool is_ed25519(const Pem::Unwrapped& pem) {
    return is_for(pem, ed25519);
}

} // namespace

namespace crypto {

ISignKey::Ptr ISignKey::Ed25519() {
    return Ed25519Key::create();
}

// The key is read by the reader for its algorithm alone, so that what is
// wrong with a key comes from the reader that knows it.
ISignKey::Ptr ISignKey::from_pem(string_view pem) {
    const auto unwrapped = Pem::unwrap(pem);

    if (is_rsa(unwrapped)) {
        return RsaSignKey::from_pem(pem);
    }

    if (is_ed25519(unwrapped)) {
        return Ed25519Key::from_pem(pem);
    }

    throw invalid_argument("Neither an RSA nor an Ed25519 key, in a PEM labeled "
                           + unwrapped.label);
}

} // namespace crypto
