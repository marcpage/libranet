#pragma once

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <iostream>
#include <iterator>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

namespace types {

// MARK: Interface
class Json {
public:
    enum class Type : std::uint8_t {
        Null,
        Boolean,
        Integer,
        Real,
        String,
        Array,
        Object,
    };

    using Array = std::vector<Json>;
    using Object = std::map<std::string, Json>;

    Json() noexcept;
    // Implicit, so that initializer lists such as {{"age", 30}, {"tags", {"a",
    // "b"}}} work. long and long long are taken by name, as std::int64_t is
    // one on Linux and the other on macOS.
    // NOLINTBEGIN(*-explicit-constructor,google-runtime-int)
    // cppcheck-suppress-begin noExplicitConstructor
    Json(std::nullptr_t) noexcept;
    Json(bool value) noexcept;
    Json(int value) noexcept;
    Json(long value) noexcept;
    Json(long long value) noexcept;
    Json(double value) noexcept;
    Json(const char* value);
    Json(std::string value);
    explicit Json(std::string_view value);
    Json(Array value);
    Json(Object value);
    // cppcheck-suppress-end noExplicitConstructor
    // NOLINTEND(*-explicit-constructor,google-runtime-int)
    Json(std::initializer_list<Json> arguments);

    Json(const Json&) = default;
    Json(Json&&) noexcept = default;
    Json& operator=(const Json&) = default;
    Json& operator=(Json&&) noexcept = default;
    ~Json() = default;

    [[nodiscard]] Type type() const;
    [[nodiscard]] bool is_null() const noexcept;
    [[nodiscard]] bool is_boolean() const noexcept;
    [[nodiscard]] bool is_number() const noexcept;
    [[nodiscard]] bool is_integer() const noexcept;
    [[nodiscard]] bool is_real() const noexcept;
    [[nodiscard]] bool is_string() const noexcept;
    [[nodiscard]] bool is_array() const noexcept;
    [[nodiscard]] bool is_object() const noexcept;

    [[nodiscard]] bool boolean() const;
    [[nodiscard]] std::int64_t integer() const;
    [[nodiscard]] double real() const;
    [[nodiscard]] const std::string& string() const;
    [[nodiscard]] const Array& array() const;
    [[nodiscard]] const Object& object() const;

    Array& array();
    Object& object();

    [[nodiscard]] std::size_t size() const;
    [[nodiscard]] bool empty() const;
    [[nodiscard]] bool has(const std::string& key) const;

    Json& operator[](std::size_t index);
    const Json& operator[](std::size_t index) const;
    Json& operator[](const std::string& key);
    const Json& operator[](const std::string& key) const;

    [[nodiscard]] const Json& at(std::size_t index) const;
    Json& at(std::size_t index);
    [[nodiscard]] const Json& at(const std::string& key) const;
    Json& at(const std::string& key);

    [[nodiscard]] std::string dump(int indent = -1) const;

    [[nodiscard]] static Json parse(std::string_view input);

    friend bool operator==(const Json& left, const Json& right);
    friend bool operator!=(const Json& left, const Json& right);
    friend std::ostream& operator<<(std::ostream& out_stream, const Json& value);
    friend std::istream& operator>>(std::istream& in_stream, Json& value);

    // MARK: Internals
private:
    using VariantType = std::variant<
        std::nullptr_t,
        bool,
        std::int64_t,
        double,
        std::string,
        Array,
        Object>;

    class Parser {
    public:
        explicit Parser(std::string_view input) noexcept;
        Json parse();

    private:
        // Arrays and objects may hold each other this deep, and no deeper:
        // each level parsed takes stack, and the input may come from anyone.
        static constexpr std::size_t max_depth = 128;

        std::string_view m_input;
        std::size_t m_cursor = 0;
        std::size_t m_depth = 0;

        void _skip_whitespace() noexcept;
        [[nodiscard]] char _peek() const noexcept;
        char _get();
        bool _consume(char expected) noexcept;
        bool _skip_digits() noexcept;
        std::uint32_t _parse_hex4();
        std::uint32_t _parse_code_point();
        static void _append_utf8(std::uint32_t code_point, std::string& text);

        Json _parse_value();
        Json _parse_null();
        Json _parse_boolean();
        Json _parse_number();
        Json _parse_string_value();
        std::string _parse_raw_string();
        Json _parse_array();
        Json _parse_object();
    };

    VariantType m_data;

    static bool _is_digit(char character);
    static void _serialize_array(const Array& the_array, std::ostringstream& out_stream,
                                 int indent, int current_indent);
    static void _serialize_object(const Object& the_object, std::ostringstream& out_stream,
                                  int indent, int current_indent);
    static void _serialize(const Json& json_value, std::ostringstream& out_stream,
                           int indent, int current_indent);
    static void _escape_string(const std::string& text, std::ostringstream& out_stream);
    static void _write_real(double value, std::ostringstream& out_stream);
};

// MARK: Implementation

inline Json::Json() noexcept
    : m_data(nullptr) {}

inline Json::Json(std::nullptr_t) noexcept
    : m_data(nullptr) {}

inline Json::Json(bool value) noexcept
    : m_data(value) {}

inline Json::Json(int value) noexcept
    : m_data(static_cast<std::int64_t>(value)) {}

// NOLINTBEGIN(google-runtime-int): long and long long by name, as declared
inline Json::Json(long value) noexcept
    : m_data(static_cast<std::int64_t>(value)) {}

inline Json::Json(long long value) noexcept
    : m_data(static_cast<std::int64_t>(value)) {}
// NOLINTEND(google-runtime-int)

inline Json::Json(double value) noexcept
    : m_data(value) {}

inline Json::Json(const char* value)
    : m_data(std::string(value)) {}

inline Json::Json(std::string value)
    : m_data(std::move(value)) {}

inline Json::Json(std::string_view value)
    : m_data(std::string(value)) {}

inline Json::Json(Array value)
    : m_data(std::move(value)) {}

inline Json::Json(Object value)
    : m_data(std::move(value)) {}

inline Json::Json(std::initializer_list<Json> arguments) {
    if (0 == arguments.size()) {
        return;
    }

    // Unless every argument is a [key, value] pair, the Json is an array.
    const auto is_member = [](const Json& element) {
        return element.is_array() && 2 == element.array().size()
               && element.array().at(0).is_string();
    };

    if (!std::all_of(arguments.begin(), arguments.end(), is_member)) {
        m_data = Array(arguments.begin(), arguments.end());
        return;
    }

    Object members;

    for (const auto& element : arguments) {
        const auto& key_and_value = element.array();
        members.emplace(key_and_value.at(0).string(), key_and_value.at(1));
    }

    m_data = std::move(members);
}

inline Json::Type Json::type() const {
    return std::visit([](auto&& value) -> Type {
        using T = std::decay_t<decltype(value)>;

        if constexpr (std::is_same_v<T, std::nullptr_t>) {
            return Type::Null;
        }

        if constexpr (std::is_same_v<T, bool>) {
            return Type::Boolean;
        }

        if constexpr (std::is_same_v<T, int64_t>) {
            return Type::Integer;
        }

        if constexpr (std::is_same_v<T, double>) {
            return Type::Real;
        }

        if constexpr (std::is_same_v<T, std::string>) {
            return Type::String;
        }

        if constexpr (std::is_same_v<T, Array>) {
            return Type::Array;
        }

        if constexpr (std::is_same_v<T, Object>) {
            return Type::Object;
        }

        throw std::logic_error("Json: invalid internal variant state");
    }, m_data);
}

inline bool Json::is_null() const noexcept {
    return std::holds_alternative<std::nullptr_t>(m_data);
}

inline bool Json::is_boolean() const noexcept {
    return std::holds_alternative<bool>(m_data);
}

inline bool Json::is_number() const noexcept {
    return is_integer() || is_real();
}

inline bool Json::is_integer() const noexcept {
    return std::holds_alternative<std::int64_t>(m_data);
}

inline bool Json::is_real() const noexcept {
    return std::holds_alternative<double>(m_data);
}

inline bool Json::is_string() const noexcept {
    return std::holds_alternative<std::string>(m_data);
}

inline bool Json::is_array() const noexcept {
    return std::holds_alternative<Array>(m_data);
}

inline bool Json::is_object() const noexcept {
    return std::holds_alternative<Object>(m_data);
}

inline bool Json::boolean() const {
    if (const auto* value = std::get_if<bool>(&m_data)) {
        return *value;
    }

    throw std::domain_error("Json: value is not a boolean");
}

inline std::int64_t Json::integer() const {
    if (const auto* value = std::get_if<std::int64_t>(&m_data)) {
        return *value;
    }

    throw std::domain_error("Json: value is not a integer");
}

inline double Json::real() const {
    if (const auto* value = std::get_if<double>(&m_data)) {
        return *value;
    }

    if (const auto* value = std::get_if<std::int64_t>(&m_data)) {
        return static_cast<double>(*value);
    }

    throw std::domain_error("Json: value is not a number");
}

inline const std::string& Json::string() const {
    if (const auto* value = std::get_if<std::string>(&m_data)) {
        return *value;
    }

    throw std::domain_error("Json: value is not a string");
}

inline const Json::Array& Json::array() const {
    if (const auto* value = std::get_if<Array>(&m_data)) {
        return *value;
    }

    throw std::domain_error("Json: value is not an array");
}

inline const Json::Object& Json::object() const {
    if (const auto* value = std::get_if<Object>(&m_data)) {
        return *value;
    }

    throw std::domain_error("Json: value is not an object");
}

inline Json::Array& Json::array() {
    if (auto* value = std::get_if<Array>(&m_data)) {
        return *value;
    }

    throw std::domain_error("Json: value is not an array");
}

inline Json::Object& Json::object() {
    if (auto* value = std::get_if<Object>(&m_data)) {
        return *value;
    }

    throw std::domain_error("Json: value is not an object");
}

inline std::size_t Json::size() const {
    if (const auto* elements = std::get_if<Array>(&m_data)) {
        return elements->size();
    }

    if (const auto* members = std::get_if<Object>(&m_data)) {
        return members->size();
    }

    throw std::domain_error("Json: size() called on non-container type");
}

inline bool Json::empty() const {
    if (const auto* elements = std::get_if<Array>(&m_data)) {
        return elements->empty();
    }

    if (const auto* members = std::get_if<Object>(&m_data)) {
        return members->empty();
    }

    throw std::domain_error("Json: empty() called on non-container type");
}

inline bool Json::has(const std::string& key) const {
    if (const auto* members = std::get_if<Object>(&m_data)) {
        return members->find(key) != members->end();
    }

    return false;
}

inline Json& Json::operator[](std::size_t index) {
    if (is_null()) {
        m_data = Array{};
    }

    auto& elements = array();

    if (index >= elements.size()) {
        elements.resize(index + 1);
    }

    return elements.at(index);
}

inline const Json& Json::operator[](std::size_t index) const {
    const auto& elements = array();

    if (index >= elements.size()) {
        throw std::out_of_range("Json: array index out of bounds");
    }

    return elements.at(index);
}

inline Json& Json::operator[](const std::string& key) {
    if (is_null()) {
        m_data = Object{};
    }

    return object()[key];
}

inline const Json& Json::operator[](const std::string& key) const {
    const auto& members = object();
    const auto member = members.find(key);

    if (member == members.end()) {
        throw std::out_of_range("Json: key not found: " + key);
    }

    return member->second;
}

inline const Json& Json::at(std::size_t index) const {
    if (!is_array()) {
        throw std::domain_error("Json: not an array");
    }

    return array().at(index);
}

inline Json& Json::at(std::size_t index) {
    if (!is_array()) {
        throw std::domain_error("Json: not an array");
    }

    return array().at(index);
}

inline const Json& Json::at(const std::string& key) const {
    if (!is_object()) {
        throw std::domain_error("Json: not an object");
    }

    const auto& members = object();
    const auto member = members.find(key);

    if (member == members.end()) {
        throw std::out_of_range("Json: key not found");
    }

    return member->second;
}

inline Json& Json::at(const std::string& key) {
    if (!is_object()) {
        throw std::domain_error("Json: not an object");
    }

    auto& members = object();
    const auto member = members.find(key);

    if (member == members.end()) {
        throw std::out_of_range("Json: key not found");
    }

    return member->second;
}

inline void Json::_escape_string(const std::string& text, std::ostringstream& out_stream) {
    out_stream << '"';

    for (const auto character : text) {
        switch (character) {
            case '"': out_stream << "\\\""; break;
            case '\\': out_stream << "\\\\"; break;
            case '\b': out_stream << "\\b"; break;
            case '\f': out_stream << "\\f"; break;
            case '\n': out_stream << "\\n"; break;
            case '\r': out_stream << "\\r"; break;
            case '\t': out_stream << "\\t"; break;
            default:
                // Control characters, those below a space, are escaped.
                constexpr unsigned first_unescaped = 0x20;
                const unsigned code = static_cast<unsigned char>(character);

                if (code < first_unescaped) {
                    out_stream << "\\u00";

                    constexpr std::string_view hex = "0123456789abcdef";
                    constexpr auto nibble_bits = 4U;
                    constexpr auto nibble_mask = 0xFU;

                    out_stream << hex.at((code >> nibble_bits) & nibble_mask)
                               << hex.at(code & nibble_mask);
                } else {
                    out_stream << character;
                }

                break;
        }
    }

    out_stream << '"';
}

inline void Json::_serialize(const Json& json_value, std::ostringstream& out_stream,
                             int indent, int current_indent) {
    std::visit([&](auto&& value) {
        using T = std::decay_t<decltype(value)>;

        if constexpr (std::is_same_v<T, std::nullptr_t>) {
            out_stream << "null";
        } else if constexpr (std::is_same_v<T, bool>) {
            out_stream << (value ? "true" : "false");
        } else if constexpr (std::is_same_v<T, std::int64_t>) {
            out_stream << value;
        } else if constexpr (std::is_same_v<T, double>) {
            if (!std::isfinite(value)) {
                throw std::domain_error("Json: non-finite double values (NaN or Infinity)"
                                        " cannot be serialized to JSON");
            }

            _write_real(value, out_stream);
        } else if constexpr (std::is_same_v<T, std::string>) {
            _escape_string(value, out_stream);
        } else if constexpr (std::is_same_v<T, Array>) {
            _serialize_array(value, out_stream, indent, current_indent);
        } else if constexpr (std::is_same_v<T, Object>) {
            _serialize_object(value, out_stream, indent, current_indent);
        }
    }, json_value.m_data);
}

// The fewest digits that read back as the same double, with ".0" when they
// have no '.' or exponent, so that they read back as a real number rather
// than as an integer.
inline void Json::_write_real(double value, std::ostringstream& out_stream) {
    // A double's shortest text is at most 24 characters.
    constexpr std::size_t text_size = 32;
    std::array<char, text_size> text{};
    // std::to_chars takes the end of its buffer as a pointer.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-bounds-pointer-arithmetic)
    auto* const end = text.data() + text.size();
    const std::to_chars_result result = std::to_chars(text.data(), end, value);

    if (result.ec != std::errc()) {
        throw std::logic_error("Json: a double did not fit its text");
    }

    const std::string_view written(
        text.data(), static_cast<std::size_t>(std::distance(text.data(), result.ptr)));
    out_stream << written;

    if (std::string_view::npos == written.find_first_of(".e")) {
        out_stream << ".0";
    }
}

inline bool Json::_is_digit(const char character) {
    return std::isdigit(static_cast<unsigned char>(character)) != 0;
}

inline void Json::_serialize_array(const Array& the_array, std::ostringstream& out_stream,
                                   int indent, int current_indent) {
    if (the_array.empty()) {
        out_stream << "[]";
        return;
    }

    const bool pretty = indent >= 0;
    const auto current = static_cast<std::string::size_type>(current_indent);
    const auto indented = current + static_cast<std::string::size_type>(indent);
    const auto indentation = pretty ? std::string(current, ' ') : std::string();
    const auto next_indentation = pretty ? std::string(indented, ' ') : std::string();
    const auto* const newline = pretty ? "\n" : "";

    out_stream << "[" << newline;

    for (std::size_t index = 0; index < the_array.size(); ++index) {
        out_stream << next_indentation;
        _serialize(the_array.at(index), out_stream, indent, current_indent + indent);

        if (index + 1 < the_array.size()) {
            out_stream << ",";
        }

        out_stream << newline;
    }

    out_stream << indentation << "]";
}

inline void Json::_serialize_object(const Object& the_object, std::ostringstream& out_stream,
                                    int indent, int current_indent) {
    if (the_object.empty()) {
        out_stream << "{}";
        return;
    }

    const bool pretty = indent >= 0;
    const std::string indentation =
        pretty ? std::string(static_cast<std::string::size_type>(current_indent), ' ') : "";
    const std::string next_indentation =
        pretty ? std::string(static_cast<std::string::size_type>(current_indent + indent), ' ')
               : "";
    const std::string newline = pretty ? "\n" : "";
    const std::string space = pretty ? " " : "";

    out_stream << "{" << newline;

    std::size_t index = 0;

    for (const auto& [key, value] : the_object) {
        out_stream << next_indentation;
        _escape_string(key, out_stream);
        out_stream << ":" << space;
        _serialize(value, out_stream, indent, current_indent + indent);

        if (++index < the_object.size()) {
            out_stream << ",";
        }

        out_stream << newline;
    }

    out_stream << indentation << "}";
}

inline std::string Json::dump(int indent) const {
    std::ostringstream out_stream;

    _serialize(*this, out_stream, indent, 0);
    return out_stream.str();
}

inline Json Json::parse(std::string_view input) {
    Parser parser(input);

    return parser.parse();
}

inline Json::Parser::Parser(std::string_view input) noexcept
    : m_input(input) {}

inline void Json::Parser::_skip_whitespace() noexcept {
    while (m_cursor < m_input.size()) {
        // Within bounds, as the loop checks; at() could only end the
        // program, from a function that cannot throw.
        // NOLINTNEXTLINE(cppcoreguidelines-pro-bounds-avoid-unchecked-container-access)
        const auto character = m_input[m_cursor];

        if (character != ' '
            && character != '\t'
            && character != '\n'
            && character != '\r') {
            break;
        }

        ++m_cursor;
    }
}

inline char Json::Parser::_peek() const noexcept {
    // Within bounds, as checked; at() could only end the program, from a
    // function that cannot throw.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-bounds-avoid-unchecked-container-access)
    return m_cursor < m_input.size() ? m_input[m_cursor] : '\0';
}

inline char Json::Parser::_get() {
    if (m_cursor >= m_input.size()) {
        throw std::invalid_argument("Json::parse: unexpected end of input");
    }

    return m_input.at(m_cursor++);
}

inline bool Json::Parser::_consume(char expected) noexcept {
    if (_peek() == expected) {
        ++m_cursor;
        return true;
    }

    return false;
}

inline Json Json::Parser::parse() {
    _skip_whitespace();

    Json result = _parse_value();

    _skip_whitespace();

    if (m_cursor < m_input.size()) {
        throw std::invalid_argument("Json::parse: trailing character at position "
                                    + std::to_string(m_cursor));
    }

    return result;
}

inline Json Json::Parser::_parse_value() {
    _skip_whitespace();

    const auto character = _peek();

    switch (character) {
        case 'n': return _parse_null();
        case 't':
        case 'f': return _parse_boolean();
        case '"': return _parse_string_value();
        case '[':
        case '{': {
            if (m_depth >= max_depth) {
                throw std::invalid_argument("Json::parse: arrays and objects nested too deeply");
            }

            ++m_depth;
            Json value = '[' == character ? _parse_array() : _parse_object();
            --m_depth;
            return value;
        }
        case '-': return _parse_number();
        default:
            if (_is_digit(character)) {
                return _parse_number();
            }

            throw std::invalid_argument(std::string("Json::parse: unexpected character '")
                                        + character
                                        + "'");
    }
}

inline Json Json::Parser::_parse_null() {
    constexpr std::string_view null_text = "null";

    if (null_text == m_input.substr(m_cursor, null_text.size())) {
        m_cursor += null_text.size();
        return Json(nullptr);
    }

    throw std::invalid_argument("Json::parse: expected 'null'");
}

inline Json Json::Parser::_parse_boolean() {
    constexpr std::string_view true_text = "true";

    if (true_text == m_input.substr(m_cursor, true_text.size())) {
        m_cursor += true_text.size();
        return Json(true);
    }

    constexpr std::string_view false_text = "false";

    if (false_text == m_input.substr(m_cursor, false_text.size())) {
        m_cursor += false_text.size();
        return Json(false);
    }

    throw std::invalid_argument("Json::parse: expected boolean");
}

inline bool Json::Parser::_skip_digits() noexcept {
    const std::size_t first = m_cursor;

    while (_is_digit(_peek())) {
        ++m_cursor;
    }

    return m_cursor > first;
}

inline Json Json::Parser::_parse_number() {
    const std::size_t start = m_cursor;

    // A number has digits before any '.', after it, and after any exponent
    // (RFC 8259, section 6).
    if ('-' == _peek()) {
        ++m_cursor;
    }

    if (!_skip_digits()) {
        throw std::invalid_argument("Json::parse: a number needs digits");
    }

    bool is_real_number = false;

    if ('.' == _peek()) {
        is_real_number = true;

        ++m_cursor;

        if (!_skip_digits()) {
            throw std::invalid_argument("Json::parse: a number needs digits after its '.'");
        }
    }

    if ('e' == _peek() || 'E' == _peek()) {
        is_real_number = true;
        ++m_cursor;

        if ('+' == _peek() || '-' == _peek()) {
            ++m_cursor;
        }

        if (!_skip_digits()) {
            throw std::invalid_argument("Json::parse: a number needs digits after its exponent");
        }
    }

    const std::string number_text(m_input.substr(start, m_cursor - start));

    try {
        if (is_real_number) {
            return Json(std::stod(number_text));
        }

        return Json(static_cast<std::int64_t>(std::stoll(number_text)));
    } catch (const std::out_of_range&) {
        throw std::invalid_argument("Json::parse: number out of range");
    }
}

inline std::string Json::Parser::_parse_raw_string() {
    _get(); // consume opening '"'

    std::string result;

    while (m_cursor < m_input.size()) {
        const char character = _get();

        if ('"' == character) {
            return result;
        }

        if ('\\' == character) {
            const char escape = _get();

            switch (escape) {
                case '"': result += '"'; break;
                case '\\': result += '\\'; break;
                case '/': result += '/'; break;
                case 'b': result += '\b'; break;
                case 'f': result += '\f'; break;
                case 'n': result += '\n'; break;
                case 'r': result += '\r'; break;
                case 't': result += '\t'; break;
                case 'u': _append_utf8(_parse_code_point(), result); break;
                default:
                    result += escape;
                    break;
            }
        } else {
            result += character;
        }
    }

    throw std::invalid_argument("Json::parse: unterminated string");
}

// The four hex digits of a \u escape.
inline std::uint32_t Json::Parser::_parse_hex4() {
    constexpr int hex_digits = 4;
    constexpr std::uint32_t hex_base = 16;
    constexpr int letter_values_from = 10;
    std::uint32_t value = 0;

    for (int digit = 0; digit < hex_digits; ++digit) {
        const auto character = static_cast<unsigned char>(_get());

        if (0 == std::isxdigit(character)) {
            throw std::invalid_argument("Json::parse: a \\u escape needs four hex digits");
        }

        const int lower = std::tolower(character);
        const int digit_value = std::isdigit(lower) != 0 ? lower - '0'
                                                         : lower - 'a' + letter_values_from;
        value = (value * hex_base) + static_cast<std::uint32_t>(digit_value);
    }

    return value;
}

// The character a \u escape names: four hex digits, or, for one beyond
// U+FFFF, the two escapes of a UTF-16 surrogate pair (RFC 8259, section 7).
inline std::uint32_t Json::Parser::_parse_code_point() {
    constexpr std::uint32_t high_first = 0xD800;
    constexpr std::uint32_t low_first = 0xDC00;
    constexpr std::uint32_t low_end = 0xE000;
    constexpr std::uint32_t pair_base = 0x10000;
    constexpr unsigned pair_shift = 10;

    const std::uint32_t first = _parse_hex4();

    if (first < high_first || first >= low_end) {
        return first;
    }

    if (first >= low_first || _get() != '\\' || _get() != 'u') {
        throw std::invalid_argument("Json::parse: a \\u escape names half a surrogate pair");
    }

    const std::uint32_t second = _parse_hex4();

    if (second < low_first || second >= low_end) {
        throw std::invalid_argument("Json::parse: a \\u escape names half a surrogate pair");
    }

    return pair_base + ((first - high_first) << pair_shift) + (second - low_first);
}

// Appends code_point to text, in UTF-8.
inline void Json::Parser::_append_utf8(std::uint32_t code_point, std::string& text) {
    constexpr std::uint32_t one_byte_end = 0x80;
    constexpr std::uint32_t two_byte_end = 0x800;
    constexpr std::uint32_t three_byte_end = 0x10000;
    constexpr std::uint32_t two_byte_lead = 0xC0;
    constexpr std::uint32_t three_byte_lead = 0xE0;
    constexpr std::uint32_t four_byte_lead = 0xF0;
    constexpr std::uint32_t continuation_lead = 0x80;
    constexpr std::uint32_t continuation_mask = 0x3F;
    constexpr unsigned continuation_bits = 6;

    const auto byte = [&text](std::uint32_t value) { text += static_cast<char>(value); };
    const auto continuation = [&byte, code_point](unsigned shift) {
        byte(continuation_lead | ((code_point >> shift) & continuation_mask));
    };

    if (code_point < one_byte_end) {
        byte(code_point);
    } else if (code_point < two_byte_end) {
        byte(two_byte_lead | (code_point >> continuation_bits));
        continuation(0);
    } else if (code_point < three_byte_end) {
        byte(three_byte_lead | (code_point >> (2 * continuation_bits)));
        continuation(continuation_bits);
        continuation(0);
    } else {
        byte(four_byte_lead | (code_point >> (3 * continuation_bits)));
        continuation(2 * continuation_bits);
        continuation(continuation_bits);
        continuation(0);
    }
}

inline Json Json::Parser::_parse_string_value() {
    // cppcheck-suppress returnDanglingLifetime
    return Json(_parse_raw_string());
}

inline Json Json::Parser::_parse_array() {
    _get(); // consume '['

    Array elements;

    _skip_whitespace();

    if (_consume(']')) {
        // cppcheck-suppress returnDanglingLifetime
        return Json(elements);
    }

    while (true) {
        elements.push_back(_parse_value());
        _skip_whitespace();

        if (_consume(']')) {
            break;
        }

        if (!_consume(',')) {
            throw std::invalid_argument("Json::parse: expected ',' or ']' in array");
        }
    }

    // cppcheck-suppress returnDanglingLifetime
    return Json(elements);
}

inline Json Json::Parser::_parse_object() {
    _get(); // consume '{'

    Object members;

    _skip_whitespace();

    if (_consume('}')) {
        // cppcheck-suppress returnDanglingLifetime
        return Json(members);
    }

    while (true) {
        _skip_whitespace();

        if (_peek() != '"') {
            throw std::invalid_argument("Json::parse: expected string key in object");
        }

        const auto key = _parse_raw_string();

        _skip_whitespace();

        if (!_consume(':')) {
            throw std::invalid_argument("Json::parse: expected ':' after key in object");
        }

        members[key] = _parse_value();
        _skip_whitespace();

        if (_consume('}')) {
            break;
        }

        if (!_consume(',')) {
            throw std::invalid_argument("Json::parse: expected ',' or '}' in object");
        }
    }

    // cppcheck-suppress returnDanglingLifetime
    return Json(members);
}

inline bool operator==(const Json& left, const Json& right) {
    return left.m_data == right.m_data;
}

inline bool operator!=(const Json& left, const Json& right) {
    return !(left == right);
}

inline std::ostream& operator<<(std::ostream& out_stream, const Json& value) {
    return out_stream << value.dump();
}

inline std::istream& operator>>(std::istream& in_stream, Json& value) {
    const std::string text((std::istreambuf_iterator<char>(in_stream)),
                           std::istreambuf_iterator<char>());

    try {
        value = Json::parse(text);
    } catch (const std::invalid_argument&) {
        in_stream.setstate(std::ios_base::failbit);
    }

    return in_stream;
}

inline Json operator""_json(const char* text, std::size_t length) {
    return Json::parse(std::string_view(text, length));
}

} // namespace types
