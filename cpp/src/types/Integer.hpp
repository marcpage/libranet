#pragma once

#include <algorithm>
#include <cctype>
#include <climits>
#include <cstdint>
#include <iostream>
#include <iterator>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace types {

class Integer {
public:
#ifdef __SIZEOF_INT128__
    using word_t = std::uint64_t;
    __extension__ using double_word_t = unsigned __int128;
#else
    using word_t = std::uint32_t;
    using double_word_t = std::uint64_t;
#endif
    static constexpr unsigned word_bits = std::numeric_limits<word_t>::digits;

    Integer() = default;
    // Integer converts implicitly from the built-in integers, as they convert
    // to each other.
    // NOLINTNEXTLINE(*-explicit-constructor)
    Integer(long long value);
    explicit Integer(const std::string& text);
    Integer(const Integer&) = default;
    Integer(Integer&&) noexcept = default;
    Integer& operator=(const Integer&) = default;
    Integer& operator=(Integer&&) noexcept = default;

    [[nodiscard]] bool is_zero() const noexcept;
    [[nodiscard]] bool is_negative() const noexcept;
    [[nodiscard]] int sign() const noexcept;
    [[nodiscard]] std::string to_string() const;
    [[nodiscard]] std::size_t bit_length() const noexcept;

    Integer operator+() const;
    Integer operator-() const;
    Integer operator~() const;
    explicit operator bool() const noexcept;
    Integer& operator++();
    Integer operator++(int);
    Integer& operator--();
    Integer operator--(int);
    Integer& operator+=(const Integer& right);
    Integer& operator-=(const Integer& right);
    Integer& operator*=(const Integer& right);
    Integer& operator/=(const Integer& right);
    Integer& operator%=(const Integer& right);
    Integer& operator&=(const Integer& right);
    Integer& operator|=(const Integer& right);
    Integer& operator^=(const Integer& right);
    Integer& operator<<=(std::size_t shift);
    Integer& operator>>=(std::size_t shift);

    friend Integer operator+(Integer left, const Integer& right);
    friend Integer operator-(Integer left, const Integer& right);
    friend Integer operator*(Integer left, const Integer& right);
    friend Integer operator/(Integer left, const Integer& right);
    friend Integer operator%(Integer left, const Integer& right);
    friend Integer operator&(const Integer& left, const Integer& right);
    friend Integer operator|(const Integer& left, const Integer& right);
    friend Integer operator^(const Integer& left, const Integer& right);
    friend Integer operator<<(Integer left, std::size_t shift);
    friend Integer operator>>(Integer left, std::size_t shift);
    friend bool operator==(const Integer& left, const Integer& right) noexcept;
    friend bool operator!=(const Integer& left, const Integer& right) noexcept;
    friend bool operator<(const Integer& left, const Integer& right) noexcept;
    friend bool operator>(const Integer& left, const Integer& right) noexcept;
    friend bool operator<=(const Integer& left, const Integer& right) noexcept;
    friend bool operator>=(const Integer& left, const Integer& right) noexcept;
    friend std::ostream& operator<<(std::ostream& out_stream, const Integer& value);
    friend std::istream& operator>>(std::istream& in_stream, Integer& value);

private:
    bool m_negative = false;
    std::vector<word_t> m_magnitude; // little-endian, no zero padding
    static constexpr word_t decimal_base = 10;

    static_assert(sizeof(double_word_t) >= 2 * sizeof(word_t)
                      || (sizeof(double_word_t) > sizeof(word_t) && sizeof(word_t) >= sizeof(std::uint64_t)),
                  "double_word_t must be wide enough to hold product/sum carries between words.");
    static_assert(sizeof(double_word_t) >= sizeof(unsigned long long),
                  "double_word_t must be at least as large as unsigned long long for safe conversions.");

    void normalize();
    static int compare_magnitudes(const std::vector<word_t>& left,
                                  const std::vector<word_t>& right) noexcept;
    static void add_magnitudes(std::vector<word_t>& left,
                               const std::vector<word_t>& right);
    static void subtract_magnitudes(std::vector<word_t>& left,
                                    const std::vector<word_t>& right);
    static std::vector<word_t> multiply_magnitudes(const std::vector<word_t>& left,
                                                   const std::vector<word_t>& right);
    static void divide_modulo_magnitudes(const std::vector<word_t>& numerator,
                                         const std::vector<word_t>& denominator,
                                         std::vector<word_t>& quotient,
                                         std::vector<word_t>& remainder);
    static void shift_left_magnitude(std::vector<word_t>& value, unsigned shift);
    static void shift_right_magnitude(std::vector<word_t>& value, unsigned shift);
    static unsigned get_normalization_shift(word_t high_word) noexcept;
    static double_word_t calculate_estimated_quotient(
        const std::vector<word_t>& remainder,
        std::size_t divisor_size,
        std::size_t quotient_index,
        word_t divisor_high_word) noexcept;
    static void execute_long_division_loop(const std::vector<word_t>& normalized_divisor,
                                           std::size_t divisor_size,
                                           std::vector<word_t>& quotient,
                                           std::vector<word_t>& remainder);
};

inline void Integer::normalize() {
    while (!m_magnitude.empty() && m_magnitude.back() == 0) {
        m_magnitude.pop_back();
    }

    if (m_magnitude.empty()) {
        m_negative = false; // zero is never negative
    }
}

inline int Integer::compare_magnitudes(const std::vector<word_t>& left,
                                       const std::vector<word_t>& right) noexcept {
    if (left.size() != right.size()) {
        return left.size() < right.size() ? -1 : 1;
    }

    for (std::size_t word_index = left.size(); word_index-- > 0;) {
        if (left[word_index] != right[word_index]) {
            return left[word_index] < right[word_index] ? -1 : 1;
        }
    }

    return 0;
}

inline void Integer::add_magnitudes(std::vector<word_t>& left,
                                    const std::vector<word_t>& right) {
    const auto word_count = std::max(left.size(), right.size());

    left.resize(word_count, 0);
    word_t carry = 0;

    for (std::size_t word_index = 0; word_index < word_count; ++word_index) {
        const auto sum =
            static_cast<double_word_t>(left[word_index])
            + (word_index < right.size() ? right[word_index] : 0)
            + carry;

        left[word_index] = static_cast<word_t>(sum);
        carry = static_cast<word_t>(sum >> word_bits);
    }

    if (carry != 0U) {
        left.push_back(carry);
    }
}

inline void Integer::subtract_magnitudes(std::vector<word_t>& left,
                                         const std::vector<word_t>& right) {
    word_t borrow = 0;

    for (std::size_t word_index = 0; word_index < left.size(); ++word_index) {
        const auto temp =
            static_cast<double_word_t>(left[word_index]) - (word_index < right.size() ? right[word_index] : 0) - borrow;

        left[word_index] = static_cast<word_t>(temp);
        borrow = (temp >> word_bits) != 0U ? 1 : 0;
    }
}

inline std::vector<Integer::word_t>
Integer::multiply_magnitudes(const std::vector<word_t>& left,
                             const std::vector<word_t>& right) {
    if (left.empty() || right.empty()) {
        return {};
    }

    std::vector<word_t> result(left.size() + right.size(), 0);

    for (std::size_t left_index = 0; left_index < left.size(); ++left_index) {
        double_word_t carry = 0;

        for (std::size_t right_index = 0; right_index < right.size(); ++right_index) {
            carry += (static_cast<double_word_t>(left[left_index])
                      * right[right_index])
                     + result[left_index + right_index];
            result[left_index + right_index] = static_cast<word_t>(carry);
            carry >>= word_bits;
        }

        result[left_index + right.size()] = static_cast<word_t>(carry);
    }

    while (!result.empty() && result.back() == 0) {
        result.pop_back();
    }

    return result;
}


inline unsigned Integer::get_normalization_shift(word_t high_word) noexcept {
    unsigned shift = 0;

    while ((high_word & (static_cast<word_t>(1) << (word_bits - 1))) == 0) {
        high_word <<= 1U;
        ++shift;
    }

    return shift;
}

inline Integer::double_word_t Integer::calculate_estimated_quotient(
    const std::vector<word_t>& remainder,
    std::size_t divisor_size,
    std::size_t quotient_index,
    word_t divisor_high_word) noexcept {
    double_word_t estimated_quotient = 0;

    if (remainder.size() > divisor_size + quotient_index) {
        estimated_quotient = (static_cast<double_word_t>(remainder[divisor_size + quotient_index])
                              << word_bits)
                             | remainder[divisor_size + quotient_index - 1];
        estimated_quotient /= divisor_high_word;
    } else if (remainder.size() == divisor_size + quotient_index) {
        estimated_quotient = remainder[divisor_size + quotient_index - 1]
                             / divisor_high_word;
    }

    const auto max_word = static_cast<double_word_t>(std::numeric_limits<word_t>::max());

    estimated_quotient = std::min(estimated_quotient, max_word);

    return estimated_quotient;
}

inline void Integer::execute_long_division_loop(
    const std::vector<word_t>& normalized_divisor,
    std::size_t divisor_size,
    std::vector<word_t>& quotient,
    std::vector<word_t>& remainder) {
    for (std::size_t quotient_index = quotient.size(); quotient_index-- > 0;) {
        auto estimated_quotient = calculate_estimated_quotient(
            remainder,
            divisor_size,
            quotient_index,
            normalized_divisor.back());
        auto product = multiply_magnitudes(normalized_divisor,
                                           {static_cast<word_t>(estimated_quotient)});

        product.insert(product.begin(), quotient_index, 0);

        // Adjust estimated_quotient if the trial product is too large
        while (compare_magnitudes(remainder, product) < 0) {
            --estimated_quotient;
            product = multiply_magnitudes(normalized_divisor,
                                          {static_cast<word_t>(estimated_quotient)});
            product.insert(product.begin(), quotient_index, 0);
        }

        subtract_magnitudes(remainder, product);

        while (!remainder.empty() && remainder.back() == 0) {
            remainder.pop_back();
        }

        quotient[quotient_index] = static_cast<word_t>(estimated_quotient);
    }
}

inline void Integer::divide_modulo_magnitudes(const std::vector<word_t>& numerator,
                                              const std::vector<word_t>& denominator,
                                              std::vector<word_t>& quotient,
                                              std::vector<word_t>& remainder) {
    if (denominator.empty()) {
        throw std::domain_error("Integer: division by zero");
    }

    if (compare_magnitudes(numerator, denominator) < 0) {
        quotient.clear();
        remainder = numerator;
        return;
    }

    remainder = numerator;
    quotient.assign(numerator.size() - denominator.size() + 1, 0);

    const auto divisor_size = denominator.size();

    // 1. Normalize divisor and remainder
    const auto shift = get_normalization_shift(denominator.back());
    auto normalized_divisor = denominator;

    shift_left_magnitude(remainder, shift);
    shift_left_magnitude(normalized_divisor, shift);

    // 2. Long division digit loop (delegated)
    execute_long_division_loop(normalized_divisor, divisor_size, quotient, remainder);

    // 3. Denormalize remainder and clean up trailing zeros
    shift_right_magnitude(remainder, shift);

    while (!quotient.empty() && quotient.back() == 0) {
        quotient.pop_back();
    }

    while (!remainder.empty() && remainder.back() == 0) {
        remainder.pop_back();
    }
}

inline void Integer::shift_left_magnitude(std::vector<word_t>& value, unsigned shift) {

    if (0 == shift || value.empty()) {
        return;
    }

    const unsigned word_shift = shift / word_bits;
    const unsigned bit_shift = shift % word_bits;

    if (word_shift != 0U) {
        value.insert(value.begin(), word_shift, 0);
    }

    if (bit_shift != 0U) {
        word_t carry = 0;

        for (word_t& word : value) {
            const word_t next = word >> (word_bits - bit_shift);

            word = (word << bit_shift) | carry;
            carry = next;
        }

        if (carry != 0U) {
            value.push_back(carry);
        }
    }
}

inline void Integer::shift_right_magnitude(std::vector<word_t>& value, unsigned shift) {
    if (0 == shift || value.empty()) {
        return;
    }

    const unsigned word_shift = shift / word_bits;
    const unsigned bit_shift = shift % word_bits;

    if (word_shift != 0U) {
        if (word_shift >= value.size()) {
            value.clear();
            return;
        }

        value.erase(value.begin(), value.begin() + word_shift);
    }

    if (bit_shift != 0U) {
        word_t carry = 0;

        for (std::size_t word_index = value.size(); word_index-- > 0;) {
            const word_t next = value[word_index] << (word_bits - bit_shift);

            value[word_index] = (value[word_index] >> bit_shift) | carry;
            carry = next;
        }
    }

    while (!value.empty() && value.back() == 0) {
        value.pop_back();
    }
}

inline Integer::Integer(long long value) {
    if (0 == value) {
        return;
    }

    if (value < 0) {
        // Careful with LLONG_MIN
        unsigned long long unsigned_value = static_cast<unsigned long long>(-(value + 1))
                                            + 1;
        m_negative = true;

        while (unsigned_value > 0) {
            m_magnitude.push_back(static_cast<word_t>(unsigned_value));

            if constexpr (word_bits < std::numeric_limits<decltype(unsigned_value)>::digits) {
                unsigned_value >>= word_bits;
            } else {
                break;
            }
        }
    } else {
        auto unsigned_value = static_cast<unsigned long long>(value);

        while (unsigned_value > 0) {
            m_magnitude.push_back(static_cast<word_t>(unsigned_value));

            if constexpr (word_bits < std::numeric_limits<decltype(unsigned_value)>::digits) {
                unsigned_value >>= word_bits;
            } else {
                break;
            }
        }
    }

    normalize();
}

inline Integer::Integer(const std::string& text) {
    if (text.empty()) {
        throw std::invalid_argument("Integer: empty string");
    }

    m_negative = text[0] == '-';

    const std::size_t numeric_start = m_negative || text[0] == '+' ? 1 : 0;

    if (numeric_start == text.size()) {
        throw std::invalid_argument("Integer: no digits");
    }

    for (auto position = numeric_start; position < text.size(); ++position) {
        const auto digit = text[position];

        if (std::isdigit(static_cast<unsigned char>(digit)) == 0) {
            throw std::invalid_argument("Integer: invalid character");
        }

        auto carry = static_cast<word_t>(digit - '0');

        for (word_t& word : m_magnitude) {
            const double_word_t temp =
                (static_cast<double_word_t>(word) * decimal_base) + carry;

            word = static_cast<word_t>(temp);
            carry = static_cast<word_t>(temp >> word_bits);
        }

        if (carry != 0U) {
            m_magnitude.push_back(carry);
        }
    }

    normalize();
}

inline Integer Integer::operator+() const {
    return *this;
}

inline Integer Integer::operator-() const {
    Integer value = *this;
    if (!value.is_zero()) {
        value.m_negative = !value.m_negative;
    }
    return value;
}

inline Integer Integer::operator~() const {
    // ~x == -x - 1
    return -(*this) - Integer(1);
}

inline Integer::operator bool() const noexcept {
    return !is_zero();
}

inline Integer& Integer::operator++() {
    *this += Integer(1);
    return *this;
}

inline Integer Integer::operator++(int) {
    Integer value = *this;
    ++*this;
    return value;
}

inline Integer& Integer::operator--() {
    *this -= Integer(1);
    return *this;
}

inline Integer Integer::operator--(int) {
    Integer value = *this;
    --*this;
    return value;
}

inline Integer& Integer::operator+=(const Integer& right) {
    if (right.is_zero()) {
        return *this;
    }

    if (is_zero()) {
        *this = right;
        return *this;
    }

    if (m_negative == right.m_negative) {
        add_magnitudes(m_magnitude, right.m_magnitude);
    } else {
        const auto comparison = compare_magnitudes(m_magnitude, right.m_magnitude);

        if (comparison == 0) {
            m_magnitude.clear();
            m_negative = false;
        } else if (comparison > 0) {
            subtract_magnitudes(m_magnitude, right.m_magnitude);
        } else {
            auto difference = right.m_magnitude;
            subtract_magnitudes(difference, m_magnitude);
            m_magnitude = std::move(difference);
            m_negative = right.m_negative;
        }
    }

    normalize();
    return *this;
}

inline Integer& Integer::operator-=(const Integer& right) {
    return *this += -right;
}

inline Integer& Integer::operator*=(const Integer& right) {
    if (is_zero() || right.is_zero()) {
        m_magnitude.clear();
        m_negative = false;
        return *this;
    }

    m_magnitude = multiply_magnitudes(m_magnitude, right.m_magnitude);
    m_negative = m_negative != right.m_negative;
    normalize();
    return *this;
}

inline Integer& Integer::operator/=(const Integer& right) {
    if (right.is_zero()) {
        throw std::domain_error("Integer: division by zero");
    }

    if (is_zero()) {
        return *this;
    }

    std::vector<word_t> quotient;
    std::vector<word_t> remainder;

    divide_modulo_magnitudes(m_magnitude, right.m_magnitude, quotient, remainder);
    m_magnitude = std::move(quotient);
    m_negative = m_negative != right.m_negative;
    normalize();
    return *this;
}

inline Integer& Integer::operator%=(const Integer& right) {
    if (right.is_zero()) {
        throw std::domain_error("Integer: division by zero");
    }

    if (is_zero()) {
        return *this;
    }

    std::vector<word_t> quotient;
    std::vector<word_t> remainder;

    divide_modulo_magnitudes(m_magnitude, right.m_magnitude, quotient, remainder);
    m_magnitude = std::move(remainder);
    // Remainder keeps the sign of the dividend (C++ toward-zero rule)
    normalize();
    return *this;
}

inline Integer& Integer::operator&=(const Integer& right) {
    *this = *this & right;
    return *this;
}

inline Integer& Integer::operator|=(const Integer& right) {
    *this = *this | right;
    return *this;
}

inline Integer& Integer::operator^=(const Integer& right) {
    *this = *this ^ right;
    return *this;
}

inline Integer& Integer::operator<<=(std::size_t shift) {
    if (is_zero() || shift == 0) {
        return *this;
    }

    const auto word_shift = shift / word_bits;
    const auto bit_shift = static_cast<unsigned>(shift % word_bits);

    if (word_shift != 0U) {
        m_magnitude.insert(m_magnitude.begin(), word_shift, 0);
    }

    if (bit_shift != 0U) {
        word_t carry = 0;

        for (word_t& word : m_magnitude) {
            const auto next = word >> (word_bits - bit_shift);

            word = (word << bit_shift) | carry;
            carry = next;
        }

        if (carry != 0U) {
            m_magnitude.push_back(carry);
        }
    }

    normalize();
    return *this;
}

inline Integer& Integer::operator>>=(std::size_t shift) {
    if (is_zero() || shift == 0) {
        return *this;
    }

    const auto word_shift = shift / word_bits;
    const auto bit_shift = static_cast<unsigned>(shift % word_bits);

    if (word_shift >= m_magnitude.size()) {
        m_magnitude.clear();
        m_negative = false;
        return *this;
    }

    if (word_shift != 0U) {
        auto end_it = m_magnitude.begin();

        std::advance(end_it, word_shift);
        m_magnitude.erase(m_magnitude.begin(), end_it);
    }

    if (bit_shift != 0U) {
        word_t carry = 0;

        for (auto word_index = m_magnitude.size(); word_index-- > 0;) {
            const auto next = m_magnitude[word_index] << (word_bits - bit_shift);

            m_magnitude[word_index] = (m_magnitude[word_index] >> bit_shift) | carry;
            carry = next;
        }
    }

    normalize();
    // Note: this is left logical right shift of the magnitude.
    // A full arithmetic right shift for negative values is not implemented.
    return *this;
}

inline Integer operator+(Integer left, const Integer& right) {
    left += right;
    return left;
}

inline Integer operator-(Integer left, const Integer& right) {
    left -= right;
    return left;
}

inline Integer operator*(Integer left, const Integer& right) {
    left *= right;
    return left;
}

inline Integer operator/(Integer left, const Integer& right) {
    left /= right;
    return left;
}

inline Integer operator%(Integer left, const Integer& right) {
    left %= right;
    return left;
}

inline Integer operator&(const Integer& left, const Integer& right) {
    if (left.m_negative || right.m_negative) {
        return ~(~left | ~right); // left & right = ~(~left | ~right)
    }

    Integer result;

    const auto word_count = std::min(left.m_magnitude.size(),
                                     right.m_magnitude.size());

    result.m_magnitude.resize(word_count);

    for (std::size_t word_index = 0; word_index < word_count; ++word_index) {
        result.m_magnitude[word_index] = left.m_magnitude[word_index]
                                         & right.m_magnitude[word_index];
    }

    result.normalize();
    return result;
}

inline Integer operator|(const Integer& left, const Integer& right) {
    if (left.m_negative || right.m_negative) {
        return ~(~left & ~right);
    }

    Integer result;

    const auto word_count = std::max(left.m_magnitude.size(),
                                     right.m_magnitude.size());

    result.m_magnitude.resize(word_count, 0);

    for (std::size_t word_index = 0; word_index < word_count; ++word_index) {
        const auto left_value = word_index < left.m_magnitude.size()
                                    ? left.m_magnitude[word_index]
                                    : static_cast<Integer::word_t>(0);
        const auto right_value = word_index < right.m_magnitude.size()
                                     ? right.m_magnitude[word_index]
                                     : static_cast<Integer::word_t>(0);

        result.m_magnitude[word_index] = left_value | right_value;
    }

    result.normalize();
    return result;
}

inline Integer operator^(const Integer& left, const Integer& right) {
    if (left.m_negative || right.m_negative) {
        return (left | right) & ~(left & right);
    }

    Integer result;
    const auto word_count = std::max(left.m_magnitude.size(), right.m_magnitude.size());

    result.m_magnitude.resize(word_count, 0);

    for (std::size_t word_index = 0; word_index < word_count; ++word_index) {
        const auto left_value = word_index < left.m_magnitude.size()
                                    ? left.m_magnitude[word_index]
                                    : static_cast<Integer::word_t>(0);
        const auto right_value = word_index < right.m_magnitude.size()
                                     ? right.m_magnitude[word_index]
                                     : static_cast<Integer::word_t>(0);

        result.m_magnitude[word_index] = left_value ^ right_value;
    }

    result.normalize();
    return result;
}

inline Integer operator<<(Integer left, std::size_t shift) {
    left <<= shift;
    return left;
}

inline Integer operator>>(Integer left, std::size_t shift) {
    left >>= shift;
    return left;
}

inline bool operator==(const Integer& left, const Integer& right) noexcept {
    return left.m_negative == right.m_negative && left.m_magnitude == right.m_magnitude;
}

inline bool operator!=(const Integer& left, const Integer& right) noexcept {
    return !(left == right);
}

inline bool operator<(const Integer& left, const Integer& right) noexcept {
    if (left.m_negative != right.m_negative) {
        return left.m_negative;
    }

    const auto comparison = Integer::compare_magnitudes(left.m_magnitude, right.m_magnitude);

    return left.m_negative ? (comparison > 0) : (comparison < 0);
}

inline bool operator>(const Integer& left, const Integer& right) noexcept {
    return right < left;
}

inline bool operator<=(const Integer& left, const Integer& right) noexcept {
    return !(right < left);
}

inline bool operator>=(const Integer& left, const Integer& right) noexcept {
    return !(left < right);
}

inline std::ostream& operator<<(std::ostream& out_stream, const Integer& value) {
    if (value.is_zero()) {
        return out_stream << '0';
    }

    if (value.m_negative) {
        out_stream << '-';
    }

    auto temp = value.m_magnitude;
    std::string digits;

    while (!temp.empty()) {
        Integer::word_t remainder = 0;

        for (auto word_index = temp.size(); word_index-- > 0;) {
            const Integer::double_word_t cur =
                (static_cast<Integer::double_word_t>(remainder) << Integer::word_bits) | temp[word_index];

            temp[word_index] = static_cast<Integer::word_t>(cur / Integer::decimal_base);
            remainder = static_cast<Integer::word_t>(cur % Integer::decimal_base);
        }

        digits.push_back(static_cast<char>('0' + remainder));

        while (!temp.empty() && temp.back() == 0) {
            temp.pop_back();
        }
    }

    std::reverse(digits.begin(), digits.end());
    return out_stream << digits;
}

inline std::istream& operator>>(std::istream& in_stream, Integer& value) {
    std::string text;

    in_stream >> text;

    if (in_stream) {
        try {
            value = Integer(text);
        } catch (const std::domain_error&) {
            in_stream.setstate(std::ios_base::failbit);
        } catch (const std::invalid_argument&) {
            in_stream.setstate(std::ios_base::failbit);
        }
    }

    return in_stream;
}

inline bool Integer::is_zero() const noexcept {
    return m_magnitude.empty();
}

inline bool Integer::is_negative() const noexcept {
    return m_negative;
}

inline int Integer::sign() const noexcept {
    if (is_zero()) {
        return 0;
    }

    return m_negative ? -1 : 1;
}

inline std::string Integer::to_string() const {
    std::ostringstream output_stream;

    output_stream << *this;
    return output_stream.str();
}

inline std::size_t Integer::bit_length() const noexcept {
    if (is_zero()) {
        return 0;
    }

    auto bits = (m_magnitude.size() - 1) * word_bits;
    auto high = m_magnitude.back();

    while (high != 0U) {
        high >>= 1U;
        ++bits;
    }

    return bits;
}

inline Integer operator""_i(const char* str, std::size_t /*unused*/) {
    return Integer(str);
}

} // namespace types
