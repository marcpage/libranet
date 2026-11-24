#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <stdexcept>
#include <vector>

#include "types/Constants.hpp"

namespace crypto {

// RSA keys in DER, between PKCS #1, the RSA key itself, and the standard
// forms that hold it: SubjectPublicKeyInfo for a public key, and PKCS #8 for
// a private key, which are what PEM's "PUBLIC KEY" and "PRIVATE KEY" labels
// name. Only DER is read, in which each value has exactly one encoding, so
// what is read is written back byte for byte.
class RsaDer {
public:
    using Data = std::vector<std::byte>;

    [[nodiscard]] static Data spki_from_pkcs1(const Data& pkcs1);
    [[nodiscard]] static Data pkcs8_from_pkcs1(const Data& pkcs1);
    // These throw std::invalid_argument for what is not an RSA key in DER
    // of that form.
    [[nodiscard]] static Data pkcs1_from_spki(const Data& spki);
    [[nodiscard]] static Data pkcs1_from_pkcs8(const Data& pkcs8);

    RsaDer() = delete;
    ~RsaDer() = delete;
    RsaDer(const RsaDer&) = delete;
    RsaDer& operator=(const RsaDer&) = delete;
    RsaDer(RsaDer&&) = delete;
    RsaDer& operator=(RsaDer&&) = delete;

private:
    // Where a DER element read from bytes is: from its tag, at begin, to
    // end, with its contents from start.
    struct Element {
        std::size_t begin;
        std::size_t start;
        std::size_t end;
    };

    static constexpr std::byte integer_tag{0x02};
    static constexpr std::byte bit_string_tag{0x03};
    static constexpr std::byte octet_string_tag{0x04};
    static constexpr std::byte sequence_tag{0x30};

    // A length under 0x80 is its one byte. Otherwise, that byte is 0x80 plus
    // how many bytes follow it, big-endian, with none to spare.
    static constexpr unsigned int long_length = 0x80U;
    static constexpr std::size_t most_length_bytes = 4;

    // The AlgorithmIdentifier of an RSA key: rsaEncryption,
    // 1.2.840.113549.1.1.1, with NULL parameters, as RFC 3279 requires.
    static constexpr std::array<std::uint8_t, 15> rsa_algorithm{
        0x30,
        0x0D,
        0x06,
        0x09,
        0x2A,
        0x86,
        0x48,
        0x86,
        0xF7,
        0x0D,
        0x01,
        0x01,
        0x01,
        0x05,
        0x00,
    };
    // PKCS #8's version: the INTEGER 0.
    static constexpr std::array<std::uint8_t, 3> pkcs8_version{0x02, 0x01, 0x00};

    [[nodiscard]] static Data _element(std::byte tag, const Data& octets);
    template<std::size_t size>
    static void _append(Data& der, const std::array<std::uint8_t, size>& bytes);
    [[nodiscard]] static Element _read(const Data& der, std::size_t& offset, std::size_t limit,
                                       std::byte tag);
    template<std::size_t size>
    [[nodiscard]] static bool _holds(const Data& der, const Element& found,
                                     const std::array<std::uint8_t, size>& bytes);
    [[nodiscard]] static Data _contents(const Data& der, std::size_t start, std::size_t end);
};

inline RsaDer::Data RsaDer::spki_from_pkcs1(const Data& pkcs1) {
    Data bits{std::byte{0x00}}; // No bits of the last byte go unused.
    Data info;

    bits.insert(bits.end(), pkcs1.begin(), pkcs1.end());
    _append(info, rsa_algorithm);

    const auto bit_string = _element(bit_string_tag, bits);

    info.insert(info.end(), bit_string.begin(), bit_string.end());
    return _element(sequence_tag, info);
}

inline RsaDer::Data RsaDer::pkcs8_from_pkcs1(const Data& pkcs1) {
    Data info;

    _append(info, pkcs8_version);
    _append(info, rsa_algorithm);

    const auto octet_string = _element(octet_string_tag, pkcs1);

    info.insert(info.end(), octet_string.begin(), octet_string.end());
    return _element(sequence_tag, info);
}

inline RsaDer::Data RsaDer::pkcs1_from_spki(const Data& spki) {
    std::size_t offset = 0;
    const auto info = _read(spki, offset, spki.size(), sequence_tag);

    if (offset != spki.size()) {
        throw std::invalid_argument("Bytes after the SubjectPublicKeyInfo");
    }

    offset = info.start;

    const auto algorithm = _read(spki, offset, info.end, sequence_tag);
    const auto bits = _read(spki, offset, info.end, bit_string_tag);

    if (!_holds(spki, algorithm, rsa_algorithm)) {
        throw std::invalid_argument("Not an RSA public key");
    }

    if (offset != info.end || bits.start == bits.end || spki.at(bits.start) != std::byte{0x00}) {
        throw std::invalid_argument("Not a SubjectPublicKeyInfo public key");
    }

    return _contents(spki, bits.start + 1, bits.end);
}

inline RsaDer::Data RsaDer::pkcs1_from_pkcs8(const Data& pkcs8) {
    std::size_t offset = 0;
    const auto info = _read(pkcs8, offset, pkcs8.size(), sequence_tag);

    if (offset != pkcs8.size()) {
        throw std::invalid_argument("Bytes after the PKCS #8 private key");
    }

    offset = info.start;

    const auto version = _read(pkcs8, offset, info.end, integer_tag);
    const auto algorithm = _read(pkcs8, offset, info.end, sequence_tag);
    const auto key = _read(pkcs8, offset, info.end, octet_string_tag);

    if (!_holds(pkcs8, algorithm, rsa_algorithm)) {
        throw std::invalid_argument("Not an RSA private key");
    }

    // Attributes, after the key, are refused: nothing here writes them.
    if (!_holds(pkcs8, version, pkcs8_version)
        || offset != info.end) {
        throw std::invalid_argument("Not a PKCS #8 private key");
    }

    return _contents(pkcs8, key.start, key.end);
}

// The DER element of tag around octets, its contents, with its length as short
// as it can be.
inline RsaDer::Data RsaDer::_element(std::byte tag, const Data& octets) {
    Data der{tag};
    const auto size = octets.size();

    if (size < long_length) {
        der.push_back(static_cast<std::byte>(size));
    } else {
        std::size_t length_bytes = 0;

        for (auto rest = size; rest > 0; rest >>= types::bits_per_byte) {
            ++length_bytes;
        }

        der.push_back(static_cast<std::byte>(long_length | length_bytes));

        for (auto index = length_bytes; index-- > 0;) {
            der.push_back(static_cast<std::byte>(size >> (index * types::bits_per_byte)));
        }
    }

    der.insert(der.end(), octets.begin(), octets.end());
    return der;
}

template<std::size_t size>
inline void RsaDer::_append(Data& der, const std::array<std::uint8_t, size>& bytes) {
    std::transform(bytes.begin(), bytes.end(), std::back_inserter(der),
                   [](std::uint8_t byte) { return static_cast<std::byte>(byte); });
}

// The element at offset, which must have tag and end by limit, and moves
// offset past it.
inline RsaDer::Element RsaDer::_read(const Data& der, std::size_t& offset, std::size_t limit,
                                     std::byte tag) {
    const auto begin = offset;

    if (limit - offset < 2 || der.at(offset) != tag) {
        throw std::invalid_argument("Not the RSA key's DER");
    }

    const auto first = std::to_integer<std::size_t>(der.at(offset + 1));
    std::size_t size = first;

    offset += 2;

    if (first >= long_length) {
        const auto length_bytes = first - long_length;

        // 0x80 alone is BER's indefinite length, which DER has not. The
        // length must need every byte given, and the long form at all.
        if (0 == length_bytes || length_bytes > most_length_bytes
            || limit - offset < length_bytes || der.at(offset) == std::byte{0x00}) {
            throw std::invalid_argument("Not a DER length in the RSA key");
        }

        size = 0;

        for (std::size_t index = 0; index < length_bytes; ++index) {
            size = (size << types::bits_per_byte) | std::to_integer<std::size_t>(der.at(offset + index));
        }

        offset += length_bytes;

        if (size < long_length) {
            throw std::invalid_argument("Not a DER length in the RSA key");
        }
    }

    if (limit - offset < size) {
        throw std::invalid_argument("The RSA key's DER is cut short");
    }

    const auto start = offset;

    offset += size;
    return Element{begin, start, offset};
}

// Whether the element found is exactly the bytes given.
template<std::size_t size>
inline bool RsaDer::_holds(const Data& der, const Element& found,
                           const std::array<std::uint8_t, size>& bytes) {
    if (found.end - found.begin != size) {
        return false;
    }

    for (std::size_t index = 0; index < size; ++index) {
        if (der.at(found.begin + index) != static_cast<std::byte>(bytes.at(index))) {
            return false;
        }
    }

    return true;
}

inline RsaDer::Data RsaDer::_contents(const Data& der, std::size_t start, std::size_t end) {
    return {std::next(der.begin(), static_cast<std::ptrdiff_t>(start)),
            std::next(der.begin(), static_cast<std::ptrdiff_t>(end))};
}

} // namespace crypto
