#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <numeric>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

#include "crypto/Base64.hpp"

namespace http {

// A structured field value (RFC 8941) that is a dictionary, as
// Signature-Input, Signature, and Content-Digest are: keys, each naming an
// item or an inner list of items, with parameters. An item is an integer, a
// string, a token, a byte sequence, or a boolean; decimals are not read or
// written. It holds as many members, inner list items, and parameters as RFC
// 8941 asks a parser to take, and no more, so that the time spent looking up
// keys stays in proportion to the text read (§3.1.1, §3.1.2, §3.2).
class StructuredField {
public:
    // Text written bare, where a string is quoted (RFC 8941 §3.3.4).
    struct Token {
        std::string text;

        friend bool operator==(const Token& left, const Token& right);
        friend bool operator!=(const Token& left, const Token& right);
    };

    using Bytes = std::vector<std::byte>;
    // An item's value (RFC 8941 §3.3).
    using Value = std::variant<std::int64_t, std::string, Token, Bytes, bool>;
    // The parameters of an item or an inner list, in order (RFC 8941 §3.1.2).
    using Parameters = std::vector<std::pair<std::string, Value>>;

    // A value, with its parameters (RFC 8941 §3.3).
    struct Item {
        Value value;
        Parameters parameters;

        friend bool operator==(const Item& left, const Item& right);
        friend bool operator!=(const Item& left, const Item& right);
    };

    // Items in parentheses, with parameters of the list's own (RFC 8941
    // §3.1.1).
    struct InnerList {
        std::vector<Item> items;
        Parameters parameters;

        friend bool operator==(const InnerList& left, const InnerList& right);
        friend bool operator!=(const InnerList& left, const InnerList& right);
    };

    // What a key of the dictionary names (RFC 8941 §3.2).
    using Member = std::variant<Item, InnerList>;
    using Members = std::vector<std::pair<std::string, Member>>;

    // Reads a field's value, its lines joined with commas, as RFC 8941 §4.2
    // reads a dictionary. A key given twice keeps its first place and its
    // last member, and so does a parameter's. Throws std::invalid_argument
    // for text that is not a dictionary this can hold.
    [[nodiscard]] static StructuredField parse(std::string_view text);

    [[nodiscard]] const Members& members() const noexcept;
    // What key names, if the dictionary has it.
    [[nodiscard]] std::optional<Member> member(std::string_view key) const;
    // Sets key to name value, in the place key has, or after the other keys.
    // Throws std::invalid_argument for a key, a parameter's key, or a value
    // that RFC 8941 cannot write, for a parameter's key given twice, or for
    // more members, items, or parameters than this holds.
    void set(std::string key, Member value);

    // The dictionary as RFC 8941 §4.1.2 writes it: empty, with no members,
    // when the field is not to be sent at all.
    [[nodiscard]] std::string to_string() const;

    friend bool operator==(const StructuredField& left, const StructuredField& right);
    friend bool operator!=(const StructuredField& left, const StructuredField& right);

private:
    // How a key or a token is spelled: the characters it may start with, and
    // those that may follow.
    struct Spelling {
        std::string_view first;
        std::string_view rest;
    };

    // Reads the dictionary RFC 8941 §4.2.2 gives, from the start of a field's
    // value.
    class Parser {
    public:
        explicit Parser(std::string_view input) noexcept;
        [[nodiscard]] StructuredField parse();

    private:
        std::string_view m_input;
        std::size_t m_cursor = 0;

        [[nodiscard]] bool _at_end() const noexcept;
        [[nodiscard]] char _peek() const;
        char _get();
        bool _consume(char expected);
        void _expect(char expected);
        void _skip_spaces();
        void _skip_whitespace();
        std::string_view _take_while(std::string_view characters);

        std::string _parse_key();
        Member _parse_member();
        InnerList _parse_inner_list();
        Item _parse_item();
        Parameters _parse_parameters();
        Value _parse_bare_item();
        std::int64_t _parse_integer();
        std::string _parse_string();
        Token _parse_token();
        Bytes _parse_bytes();
        bool _parse_boolean();
    };

    // RFC 8941 §3.1.2: lcalpha or "*", then lcalpha, DIGIT, "_", "-", ".",
    // or "*".
    static constexpr Spelling key_spelling{
        "abcdefghijklmnopqrstuvwxyz*",
        "abcdefghijklmnopqrstuvwxyz0123456789_-.*",
    };
    // RFC 8941 §3.3.4: ALPHA or "*", then tchar (RFC 9110 §5.6.2), ":", or
    // "/".
    static constexpr Spelling token_spelling{
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz*",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&'*+-.^_`|~:/",
    };
    static constexpr std::string_view digits = "0123456789";
    // RFC 8941 §3.3.5: base64's alphabet, with its padding.
    static constexpr std::string_view base64_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                                                          "abcdefghijklmnopqrstuvwxyz0123456789+/=";
    // Base64 is written in groups of 4 characters, which RFC 8941 §4.2.7 lets
    // a byte sequence leave unpadded.
    static constexpr std::size_t base64_group_size = 4;

    // RFC 8941 §3.3.1: an integer has at most 15 digits.
    static constexpr std::size_t max_integer_digits = 15;
    static constexpr std::int64_t max_integer = 999'999'999'999'999;
    static constexpr std::int64_t decimal_base = 10;
    // The fewest RFC 8941 lets a parser take (§3.2, §3.1.1, §3.1.2).
    static constexpr std::size_t max_members = 1024;
    static constexpr std::size_t max_inner_list_items = 256;
    static constexpr std::size_t max_parameters = 256;

    Members m_members;

    [[nodiscard]] static bool _is_one_of(std::string_view characters, char character) noexcept;
    [[nodiscard]] static bool _is_spelled(std::string_view text, const Spelling& spelling);
    [[nodiscard]] static bool _is_printable(char character) noexcept;
    [[nodiscard]] static bool _is_true(const Value& value) noexcept;
    [[nodiscard]] static Bytes _decode(std::string_view base64);

    template<typename Type>
    static void _put(std::vector<std::pair<std::string, Type>>& entries, std::string key,
                     Type value, std::size_t max_entries);

    static void _check_key(std::string_view key);
    static void _check_value(const Value& value);
    static void _check_parameters(const Parameters& parameters);
    static void _check_item(const Item& item);
    static void _check_member(const Member& value);

    static void _write_member(const Member& value, std::string& text);
    static void _write_inner_list(const InnerList& list, std::string& text);
    static void _write_item(const Item& item, std::string& text);
    static void _write_parameters(const Parameters& parameters, std::string& text);
    static void _write_value(const Value& value, std::string& text);
    static void _write_string(std::string_view value, std::string& text);
};

inline bool operator==(const StructuredField::Token& left, const StructuredField::Token& right) {
    return left.text == right.text;
}

inline bool operator==(const StructuredField::Item& left, const StructuredField::Item& right) {
    return left.value == right.value && left.parameters == right.parameters;
}

inline bool operator==(const StructuredField::InnerList& left,
                       const StructuredField::InnerList& right) {
    return left.items == right.items && left.parameters == right.parameters;
}

inline bool operator!=(const StructuredField::Token& left, const StructuredField::Token& right) {
    return !(left == right);
}

inline bool operator!=(const StructuredField::Item& left, const StructuredField::Item& right) {
    return !(left == right);
}

inline bool operator!=(const StructuredField::InnerList& left,
                       const StructuredField::InnerList& right) {
    return !(left == right);
}

inline StructuredField StructuredField::parse(std::string_view text) {
    Parser parser(text);

    return parser.parse();
}

inline const StructuredField::Members& StructuredField::members() const noexcept {
    return m_members;
}

inline std::optional<StructuredField::Member> StructuredField::member(std::string_view key) const {
    for (const auto& [name, value] : m_members) {
        if (name == key) {
            return value;
        }
    }

    return std::nullopt;
}

inline void StructuredField::set(std::string key, Member value) {
    _check_key(key);
    _check_member(value);
    _put(m_members, std::move(key), std::move(value), max_members);
}

inline std::string StructuredField::to_string() const {
    std::string text;

    for (const auto& [key, value] : m_members) {
        if (!text.empty()) {
            text += ", ";
        }

        text += key;
        _write_member(value, text);
    }

    return text;
}

inline bool operator==(const StructuredField& left, const StructuredField& right) {
    return left.m_members == right.m_members;
}

inline bool operator!=(const StructuredField& left, const StructuredField& right) {
    return !(left == right);
}

inline bool StructuredField::_is_one_of(std::string_view characters, char character) noexcept {
    return std::string_view::npos != characters.find(character);
}

inline bool StructuredField::_is_spelled(std::string_view text, const Spelling& spelling) {
    const auto may_follow = [&spelling](char character) {
        return _is_one_of(spelling.rest, character);
    };

    return !text.empty()
           && _is_one_of(spelling.first, text.front())
           && std::all_of(text.begin(), text.end(), may_follow);
}

// RFC 8941 §3.3.3: a string holds only %x20-7E.
inline bool StructuredField::_is_printable(char character) noexcept {
    return ' ' <= character && character <= '~';
}

// Whether value is the boolean true, which a member or a parameter writes as
// its key alone (RFC 8941 §4.1.2, §4.1.1.2).
inline bool StructuredField::_is_true(const Value& value) noexcept {
    const auto* const flag = std::get_if<bool>(&value);

    return nullptr != flag && *flag;
}

// The bytes base64 gives, padded if it is not. Throws std::invalid_argument
// for padding with more after it, which crypto::Base64 would take as data.
inline StructuredField::Bytes StructuredField::_decode(std::string_view base64) {
    const auto padding = base64.find('=');

    if (std::string_view::npos != padding
        && std::string_view::npos != base64.find_first_not_of('=', padding)) {
        throw std::invalid_argument("A structured field byte sequence is padded only at its end");
    }

    std::string padded(base64);

    while (padded.size() % base64_group_size != 0) {
        padded += '=';
    }

    return crypto::Base64::decode(padded);
}

// Sets key's value in entries, in the place key has, or after the others, as
// RFC 8941 §4.2.2 and §4.2.3.2 read a key given twice. Throws
// std::invalid_argument for one entry more than max_entries.
template<typename Type>
inline void StructuredField::_put(std::vector<std::pair<std::string, Type>>& entries,
                                  std::string key, Type value, std::size_t max_entries) {
    for (auto& [name, existing] : entries) {
        if (name == key) {
            existing = std::move(value);
            return;
        }
    }

    if (entries.size() >= max_entries) {
        throw std::invalid_argument("A structured field holds at most "
                                    + std::to_string(max_entries)
                                    + " members, items, or parameters in one place");
    }

    entries.emplace_back(std::move(key), std::move(value));
}

inline void StructuredField::_check_key(std::string_view key) {
    if (!_is_spelled(key, key_spelling)) {
        throw std::invalid_argument("Not a structured field key: " + std::string(key));
    }
}

inline void StructuredField::_check_value(const Value& value) {
    const auto check = [](const auto& kind) {
        using Kind = std::decay_t<decltype(kind)>;

        if constexpr (std::is_same_v<Kind, std::int64_t>) {
            if (kind < -max_integer || kind > max_integer) {
                throw std::invalid_argument("A structured field integer has at most 15 digits: "
                                            + std::to_string(kind));
            }
        } else if constexpr (std::is_same_v<Kind, std::string>) {
            if (!std::all_of(kind.begin(), kind.end(), _is_printable)) {
                throw std::invalid_argument(
                    "A structured field string holds only printable ASCII");
            }
        } else if constexpr (std::is_same_v<Kind, Token>) {
            if (!_is_spelled(kind.text, token_spelling)) {
                throw std::invalid_argument("Not a structured field token: " + kind.text);
            }
        } else {
            // Any bytes, and either boolean, can be written.
            static_assert(std::is_same_v<Kind, Bytes> || std::is_same_v<Kind, bool>);
        }
    };

    std::visit(check, value);
}

inline void StructuredField::_check_parameters(const Parameters& parameters) {
    if (parameters.size() > max_parameters) {
        throw std::invalid_argument("A structured field item or inner list has at most "
                                    + std::to_string(max_parameters)
                                    + " parameters");
    }

    for (auto parameter = parameters.begin(); parameter != parameters.end(); ++parameter) {
        _check_key(parameter->first);
        _check_value(parameter->second);

        const auto is_same_key = [&parameter](const auto& earlier) {
            return earlier.first == parameter->first;
        };

        if (std::any_of(parameters.begin(), parameter, is_same_key)) {
            throw std::invalid_argument("A structured field parameter is given twice: "
                                        + parameter->first);
        }
    }
}

inline void StructuredField::_check_item(const Item& item) {
    _check_value(item.value);
    _check_parameters(item.parameters);
}

inline void StructuredField::_check_member(const Member& value) {
    const auto check = [](const auto& kind) {
        using Kind = std::decay_t<decltype(kind)>;

        if constexpr (std::is_same_v<Kind, Item>) {
            _check_item(kind);
        } else {
            static_assert(std::is_same_v<Kind, InnerList>);

            if (kind.items.size() > max_inner_list_items) {
                throw std::invalid_argument("A structured field inner list has at most "
                                            + std::to_string(max_inner_list_items)
                                            + " items");
            }

            std::for_each(kind.items.begin(), kind.items.end(), _check_item);
            _check_parameters(kind.parameters);
        }
    };

    std::visit(check, value);
}

inline void StructuredField::_write_member(const Member& value, std::string& text) {
    const auto write = [&text](const auto& kind) {
        using Kind = std::decay_t<decltype(kind)>;

        if constexpr (std::is_same_v<Kind, Item>) {
            if (_is_true(kind.value)) {
                _write_parameters(kind.parameters, text);
            } else {
                text += '=';
                _write_item(kind, text);
            }
        } else {
            static_assert(std::is_same_v<Kind, InnerList>);
            text += '=';
            _write_inner_list(kind, text);
        }
    };

    std::visit(write, value);
}

inline void StructuredField::_write_inner_list(const InnerList& list, std::string& text) {
    text += '(';

    for (std::size_t index = 0; index < list.items.size(); ++index) {
        if (index > 0) {
            text += ' ';
        }

        _write_item(list.items.at(index), text);
    }

    text += ')';
    _write_parameters(list.parameters, text);
}

inline void StructuredField::_write_item(const Item& item, std::string& text) {
    _write_value(item.value, text);
    _write_parameters(item.parameters, text);
}

inline void StructuredField::_write_parameters(const Parameters& parameters, std::string& text) {
    for (const auto& [key, value] : parameters) {
        text += ';';
        text += key;

        if (!_is_true(value)) {
            text += '=';
            _write_value(value, text);
        }
    }
}

inline void StructuredField::_write_value(const Value& value, std::string& text) {
    const auto write = [&text](const auto& kind) {
        using Kind = std::decay_t<decltype(kind)>;

        if constexpr (std::is_same_v<Kind, std::int64_t>) {
            text += std::to_string(kind);
        } else if constexpr (std::is_same_v<Kind, std::string>) {
            _write_string(kind, text);
        } else if constexpr (std::is_same_v<Kind, Token>) {
            text += kind.text;
        } else if constexpr (std::is_same_v<Kind, Bytes>) {
            text += ':' + crypto::Base64::encode(kind) + ':';
        } else {
            static_assert(std::is_same_v<Kind, bool>);
            text += kind ? "?1" : "?0";
        }
    };

    std::visit(write, value);
}

// RFC 8941 §4.1.6: in quotes, with a backslash before each quote and
// backslash.
inline void StructuredField::_write_string(std::string_view value, std::string& text) {
    text += '"';

    for (const char character : value) {
        if ('"' == character || '\\' == character) {
            text += '\\';
        }

        text += character;
    }

    text += '"';
}

// MARK: Parsing

inline StructuredField::Parser::Parser(std::string_view input) noexcept
    : m_input(input) {}

inline StructuredField StructuredField::Parser::parse() {
    StructuredField dictionary;

    _skip_spaces();

    while (!_at_end()) {
        auto key = _parse_key();
        auto value = _parse_member();

        dictionary.set(std::move(key), std::move(value));
        _skip_whitespace();

        if (_at_end()) {
            break;
        }

        _expect(',');
        _skip_whitespace();

        if (_at_end()) {
            throw std::invalid_argument("A structured field cannot end with a comma");
        }
    }

    return dictionary;
}

inline bool StructuredField::Parser::_at_end() const noexcept {
    return m_cursor >= m_input.size();
}

// The next character, or '\0' at the end, which nothing reads as a
// character it takes.
inline char StructuredField::Parser::_peek() const {
    return _at_end() ? '\0' : m_input.at(m_cursor);
}

inline char StructuredField::Parser::_get() {
    if (_at_end()) {
        throw std::invalid_argument("A structured field ends too soon");
    }

    return m_input.at(m_cursor++);
}

inline bool StructuredField::Parser::_consume(char expected) {
    if (!_at_end() && expected == m_input.at(m_cursor)) {
        ++m_cursor;
        return true;
    }

    return false;
}

inline void StructuredField::Parser::_expect(char expected) {
    if (!_consume(expected)) {
        throw std::invalid_argument(std::string("Expected '") + expected
                                    + "' in a structured field at "
                                    + std::to_string(m_cursor));
    }
}

inline void StructuredField::Parser::_skip_spaces() {
    while (' ' == _peek()) {
        ++m_cursor;
    }
}

// RFC 8941 §4.2.2 takes OWS, spaces and tabs, around a dictionary's commas.
inline void StructuredField::Parser::_skip_whitespace() {
    while (' ' == _peek() || '\t' == _peek()) {
        ++m_cursor;
    }
}

inline std::string_view StructuredField::Parser::_take_while(std::string_view characters) {
    const auto start = m_cursor;

    while (!_at_end() && _is_one_of(characters, m_input.at(m_cursor))) {
        ++m_cursor;
    }

    return m_input.substr(start, m_cursor - start);
}

inline std::string StructuredField::Parser::_parse_key() {
    if (!_is_one_of(key_spelling.first, _peek())) {
        throw std::invalid_argument("Expected a key in a structured field at "
                                    + std::to_string(m_cursor));
    }

    return std::string(_take_while(key_spelling.rest));
}

// What follows a key: "=" and an item or inner list, or, for an item that is
// true, its parameters alone (RFC 8941 §4.2.2).
inline StructuredField::Member StructuredField::Parser::_parse_member() {
    if (!_consume('=')) {
        auto parameters = _parse_parameters();

        return Item{true, std::move(parameters)};
    }

    if ('(' == _peek()) {
        return _parse_inner_list();
    }

    return _parse_item();
}

// RFC 8941 §4.2.1.2: items, one or more spaces between each.
inline StructuredField::InnerList StructuredField::Parser::_parse_inner_list() {
    InnerList list;

    _expect('(');
    _skip_spaces();

    while (!_consume(')')) {
        list.items.push_back(_parse_item());

        if (' ' != _peek() && ')' != _peek()) {
            throw std::invalid_argument("Expected ' ' or ')' in a structured field at "
                                        + std::to_string(m_cursor));
        }

        _skip_spaces();
    }

    list.parameters = _parse_parameters();
    return list;
}

inline StructuredField::Item StructuredField::Parser::_parse_item() {
    auto value = _parse_bare_item();
    auto parameters = _parse_parameters();

    return Item{std::move(value), std::move(parameters)};
}

// RFC 8941 §4.2.3.2: each ";", then a key, and "=" and its value if it is not
// true.
inline StructuredField::Parameters StructuredField::Parser::_parse_parameters() {
    Parameters parameters;

    while (_consume(';')) {
        _skip_spaces();

        auto key = _parse_key();
        auto value = _consume('=') ? _parse_bare_item() : Value(true);

        _put(parameters, std::move(key), std::move(value), max_parameters);
    }

    return parameters;
}

// RFC 8941 §4.2.3.1, but for a decimal, whose '.' is left unread, and so
// refused by what reads after it.
inline StructuredField::Value StructuredField::Parser::_parse_bare_item() {
    const auto character = _peek();

    if ('-' == character || _is_one_of(digits, character)) {
        return _parse_integer();
    }

    if ('"' == character) {
        return _parse_string();
    }

    if (':' == character) {
        return _parse_bytes();
    }

    if ('?' == character) {
        return _parse_boolean();
    }

    if (_is_one_of(token_spelling.first, character)) {
        return _parse_token();
    }

    throw std::invalid_argument("Expected an item in a structured field at "
                                + std::to_string(m_cursor));
}

// RFC 8941 §4.2.4, for an integer.
inline std::int64_t StructuredField::Parser::_parse_integer() {
    const bool is_negative = _consume('-');
    const auto number = _take_while(digits);

    if (number.empty() || number.size() > max_integer_digits) {
        throw std::invalid_argument("A structured field integer has 1 to 15 digits: "
                                    + std::string(number));
    }

    const auto add_digit = [](std::int64_t sum, char digit) {
        return (sum * decimal_base) + (digit - '0');
    };
    const auto magnitude = std::accumulate(number.begin(), number.end(), std::int64_t{0},
                                           add_digit);

    return is_negative ? -magnitude : magnitude;
}

// RFC 8941 §4.2.5.
inline std::string StructuredField::Parser::_parse_string() {
    std::string text;

    _expect('"');

    while (true) {
        const char character = _get();

        if ('"' == character) {
            return text;
        }

        if (!_is_printable(character)) {
            throw std::invalid_argument("A structured field string holds only printable ASCII");
        }

        if ('\\' == character) {
            const char escaped = _get();

            if ('"' != escaped && '\\' != escaped) {
                throw std::invalid_argument("A structured field string escapes only '\"' and '\\'");
            }

            text += escaped;
        } else {
            text += character;
        }
    }
}

// RFC 8941 §4.2.6. What reads a bare item has seen that it starts as a token
// does.
inline StructuredField::Token StructuredField::Parser::_parse_token() {
    return Token{std::string(_take_while(token_spelling.rest))};
}

// RFC 8941 §4.2.7: base64 between colons.
inline StructuredField::Bytes StructuredField::Parser::_parse_bytes() {
    _expect(':');

    const auto end = m_input.find(':', m_cursor);

    if (std::string_view::npos == end) {
        throw std::invalid_argument("A structured field byte sequence needs a closing ':'");
    }

    const auto base64 = m_input.substr(m_cursor, end - m_cursor);
    const auto is_base64 = [](char character) { return _is_one_of(base64_characters, character); };

    m_cursor = end + 1;

    if (!std::all_of(base64.begin(), base64.end(), is_base64)) {
        throw std::invalid_argument("A structured field byte sequence holds only base64");
    }

    return _decode(base64);
}

// RFC 8941 §4.2.8.
inline bool StructuredField::Parser::_parse_boolean() {
    _expect('?');

    if (_consume('1')) {
        return true;
    }

    if (_consume('0')) {
        return false;
    }

    throw std::invalid_argument("A structured field boolean is ?1 or ?0");
}

} // namespace http
