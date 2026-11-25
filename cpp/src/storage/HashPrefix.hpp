#pragma once

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "Identifier.hpp"
#include "types/Constants.hpp"

namespace storage {

// A prefix of a hash, in hex, as a search names one (HttpApi §6), kept in
// lower case. It is compared with a hash by its leading bits, not its digits.
class HashPrefix {
public:
    // In any case. Throws std::invalid_argument for text that is empty, is not
    // hex digits, or has more digits than the longest hash has.
    [[nodiscard]] static HashPrefix parse(std::string_view text);

    [[nodiscard]] std::size_t bits() const noexcept;
    // With identifier's hash, of whichever algorithm: no more than the shorter
    // of the two has.
    [[nodiscard]] std::size_t matching_bits(const Identifier& identifier) const;
    // No more than the shorter of the two has.
    [[nodiscard]] std::size_t matching_bits(const HashPrefix& other) const;
    [[nodiscard]] const std::string& to_string() const noexcept;

private:
    static constexpr unsigned top_bit = 0x8U;

    std::string m_hex;

    explicit HashPrefix(std::string hex);

    [[nodiscard]] std::size_t _matching_bits(std::string_view hash) const;
    [[nodiscard]] static unsigned _value(char digit);
    [[nodiscard]] static std::size_t _leading_clear_bits(unsigned digit);
};

inline HashPrefix HashPrefix::parse(std::string_view text) {
    return HashPrefix(std::string(text));
}

inline std::size_t HashPrefix::bits() const noexcept {
    return m_hex.size() * types::bits_per_hex_digit;
}

inline std::size_t HashPrefix::matching_bits(const Identifier& identifier) const {
    return _matching_bits(identifier.hash());
}

inline std::size_t HashPrefix::matching_bits(const HashPrefix& other) const {
    return _matching_bits(other.m_hex);
}

inline const std::string& HashPrefix::to_string() const noexcept {
    return m_hex;
}

inline HashPrefix::HashPrefix(std::string hex)
    : m_hex(std::move(hex)) {
    if (m_hex.empty() || m_hex.size() > Identifier::longest_hash_digits()
        || m_hex.find_first_not_of(types::hex_digits_either_case) != std::string::npos) {
        throw std::invalid_argument("Invalid hash prefix: not 1 to "
                                    + std::to_string(Identifier::longest_hash_digits())
                                    + " hex digits");
    }

    std::transform(m_hex.begin(), m_hex.end(), m_hex.begin(), [](char digit) {
        return static_cast<char>(std::tolower(static_cast<unsigned char>(digit)));
    });
}

// How many leading bits this shares with a hash in lower-case hex: four for
// each digit before the first that differs, then as many as the two digits
// there share at the top.
inline std::size_t HashPrefix::_matching_bits(std::string_view hash) const {
    const std::string_view prefix(m_hex);
    const auto differing = std::mismatch(prefix.begin(), prefix.end(), hash.begin(), hash.end());
    const auto digits = static_cast<std::size_t>(std::distance(prefix.begin(), differing.first));
    const auto shared_bits = digits * types::bits_per_hex_digit;

    if (differing.first == prefix.end() || differing.second == hash.end()) {
        return shared_bits;
    }

    return shared_bits + _leading_clear_bits(_value(*differing.first) ^ _value(*differing.second));
}

// The value of a lower-case hex digit.
inline unsigned HashPrefix::_value(char digit) {
    return static_cast<unsigned>(types::hex_digits.find(digit));
}

// How many of a digit's four bits are clear, from the top, before the first
// that is set.
inline std::size_t HashPrefix::_leading_clear_bits(unsigned digit) {
    std::size_t clear_bits = 0;

    for (unsigned mask = top_bit; clear_bits < types::bits_per_hex_digit && 0U == (digit & mask);
         mask >>= 1U) {
        ++clear_bits;
    }

    return clear_bits;
}

} // namespace storage
