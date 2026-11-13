#pragma once

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <initializer_list>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

namespace types {

class Json {
public:
    enum class Type {
        Null,
        Boolean,
        Integer,
        Real,
        String,
        Array,
        Object
    };

    using Array = std::vector<Json>;
    using Object = std::map<std::string, Json>;

    Json() noexcept;
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
    Json(std::initializer_list<Json> arguments);

    Json(const Json&) = default;
    Json(Json&&) noexcept = default;
    Json& operator=(const Json&) = default;
    Json& operator=(Json&&) noexcept = default;

    Type type() const;
    bool is_null() const noexcept;
    bool is_boolean() const noexcept;
    bool is_number() const noexcept;
    bool is_integer() const noexcept;
    bool is_real() const noexcept;
    bool is_string() const noexcept;
    bool is_array() const noexcept;
    bool is_object() const noexcept;

    bool boolean() const;
    std::int64_t integer() const;
    double real() const;
    const std::string& string() const;
    const Array& array() const;
    const Object& object() const;

    Array& array();
    Object& object();

    std::size_t size() const;
    bool empty() const;
    bool has(const std::string& key) const;

    Json& operator[](std::size_t index);
    const Json& operator[](std::size_t index) const;
    Json& operator[](const std::string& key);
    const Json& operator[](const std::string& key) const;

    const Json& at(std::size_t index) const;
    Json& at(std::size_t index);
    const Json& at(const std::string& key) const;
    Json& at(const std::string& key);

    std::string dump(int indent = -1) const;

    static Json parse(std::string_view input);

    friend bool operator==(const Json& left, const Json& right) noexcept;
    friend bool operator!=(const Json& left, const Json& right) noexcept;
    friend std::ostream& operator<<(std::ostream& out_stream, const Json& value);
    friend std::istream& operator>>(std::istream& in_stream, Json& value);

private:
    using VariantType = std::variant<
        std::nullptr_t,
        bool,
        std::int64_t,
        double,
        std::string,
        Array,
        Object
    >;

    VariantType m_data;

    class Parser {
    public:
        explicit Parser(std::string_view input) noexcept;
        Json parse();

    private:
        std::string_view m_input;
        std::size_t m_cursor = 0;

        void skip_whitespace() noexcept;
        char peek() const noexcept;
        char get();
        bool consume(char expected) noexcept;

        Json parse_value();
        Json parse_null();
        Json parse_boolean();
        Json parse_number();
        Json parse_string_value();
        std::string parse_raw_string();
        Json parse_array();
        Json parse_object();
    };

    static bool is_digit(const char character);
    static void serialize_array(const Array& arr, std::ostringstream& out_stream, int indent, int current_indent);
    static void serialize_object(const Object& obj, std::ostringstream& out_stream, int indent, int current_indent);
    static void serialize(const Json& json_value, std::ostringstream& out_stream,
                                                    int indent, int current_indent);
    static void escape_string(const std::string& str, std::ostringstream& out_stream);
};

inline Json::Json() noexcept
    : m_data(nullptr) {}

inline Json::Json(std::nullptr_t) noexcept
    : m_data(nullptr) {}

inline Json::Json(bool value) noexcept
    : m_data(value) {}

inline Json::Json(int value) noexcept
    : m_data(static_cast<std::int64_t>(value)) {}

inline Json::Json(long value) noexcept
    : m_data(static_cast<std::int64_t>(value)) {}

inline Json::Json(long long value) noexcept
    : m_data(static_cast<std::int64_t>(value)) {}

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
    if (arguments.size() <= 0) {
        return;
    }

    for (const auto& elem : arguments) {
        if (!elem.is_array()
                || elem.array().size() != 2
                || !elem.array()[0].is_string()) {
            m_data = Array(arguments.begin(), arguments.end());
            return;
        }
    }

    Object obj;

    for (const auto& elem : arguments) {
        const auto& pair_arr = elem.array();
        obj.emplace(pair_arr[0].string(), pair_arr[1]);
    }

    m_data = std::move(obj);
}

inline Json::Type Json::type() const {
    return std::visit([](auto&& value) -> Type {
        using T = std::decay_t<decltype(value)>;

        if constexpr (std::is_same_v<T, std::nullptr_t>) {
            return Type::Null;
        } else if constexpr (std::is_same_v<T, bool>) {
            return Type::Boolean;
        } else if constexpr (std::is_same_v<T, int64_t>) {
            return Type::Integer;
        } else if constexpr (std::is_same_v<T, double>) {
            return Type::Real;
        } else if constexpr (std::is_same_v<T, std::string>) {
            return Type::String;
        } else if constexpr (std::is_same_v<T, Array>) {
            return Type::Array;
        } else if constexpr (std::is_same_v<T, Object>) {
            return Type::Object;
        } else {
            throw std::logic_error("Json: invalid internal variant state");
        }
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
    if (const auto* arr = std::get_if<Array>(&m_data)) {
        return arr->size();
    }

    if (const auto* obj = std::get_if<Object>(&m_data)) {
        return obj->size();
    }

    throw std::domain_error("Json: size() called on non-container type");
}

inline bool Json::empty() const {
    if (const auto* arr = std::get_if<Array>(&m_data)) {
        return arr->empty();
    }

    if (const auto* obj = std::get_if<Object>(&m_data)) {
        return obj->empty();
    }

    throw std::domain_error("Json: empty() called on non-container type");
}

inline bool Json::has(const std::string& key) const {
    if (const auto* obj = std::get_if<Object>(&m_data)) {
        return obj->find(key) != obj->end();
    }

    return false;
}

inline Json& Json::operator[](std::size_t index) {
    if (is_null()) {
        m_data = Array{};
    }

    auto& arr = array();

    if (index >= arr.size()) {
        arr.resize(index + 1);
    }

    return arr[index];
}

inline const Json& Json::operator[](std::size_t index) const {
    const auto& arr = array();

    if (index >= arr.size()) {
        throw std::out_of_range("Json: array index out of bounds");
    }

    return arr[index];
}

inline Json& Json::operator[](const std::string& key) {
    if (is_null()) {
        m_data = Object{};
    }

    return object()[key];
}

inline const Json& Json::operator[](const std::string& key) const {
    const auto& obj = object();
    const auto it = obj.find(key);

    if (it == obj.end()) {
        throw std::out_of_range("Json: key not found: " + key);
    }

    return it->second;
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

    const auto& obj = object();
    const auto it = obj.find(key);

    if (it == obj.end()) {
        throw std::out_of_range("Json: key not found");
    }

    return it->second;
}

inline Json& Json::at(const std::string& key) {
    if (!is_object()) {
        throw std::domain_error("Json: not an object");
    }

    auto& obj = object();
    const auto it = obj.find(key);

    if (it == obj.end()) {
        throw std::out_of_range("Json: key not found");
    }

    return it->second;
}

inline void Json::escape_string(const std::string& str, std::ostringstream& out_stream) {
    out_stream << '"';

    for (const auto character : str) {
        switch (character) {
            case '"':  out_stream << "\\\""; break;
            case '\\': out_stream << "\\\\"; break;
            case '\b': out_stream << "\\b";  break;
            case '\f': out_stream << "\\f";  break;
            case '\n': out_stream << "\\n";  break;
            case '\r': out_stream << "\\r";  break;
            case '\t': out_stream << "\\t";  break;
            default:
                if (static_cast<unsigned char>(character) < 0x20) {
                    out_stream << "\\u00";

                    const char hex[] = "0123456789abcdef";

                    out_stream << hex[(character >> 4) & 0xF] << hex[character & 0xF];
                } else {
                    out_stream << character;
                }
                break;
        }
    }

    out_stream << '"';
}

inline void Json::serialize(const Json& json_value, std::ostringstream& out_stream,
                                        int indent, int current_indent) {
    std::visit([&](auto&& value) {
        using T = std::decay_t<decltype(value)>;

        if constexpr (std::is_same_v<T, std::nullptr_t>) {
            out_stream << "null";
        } else if constexpr (std::is_same_v<T, bool>) {
            out_stream << (value ? "true" : "false");
        } else if constexpr (std::is_same_v<T, std::int64_t>
                                || std::is_same_v<T, double>) {
            if (!std::isfinite(value)) {
                throw std::domain_error("Json: non-finite double values (NaN or Infinity)"
                                            " cannot be serialized to JSON");
            }
            out_stream << value;
        } else if constexpr (std::is_same_v<T, std::string>) {
            escape_string(value, out_stream);
        } else if constexpr (std::is_same_v<T, Array>) {
            serialize_array(value, out_stream, indent, current_indent);
        } else if constexpr (std::is_same_v<T, Object>) {
            serialize_object(value, out_stream, indent, current_indent);
        }
    }, json_value.m_data);
}

inline bool Json::is_digit(const char character) {
    return std::isdigit(static_cast<unsigned char>(character));
}

inline void Json::serialize_array(const Array& the_array, std::ostringstream& out_stream,
                                                int indent, int current_indent) {
    if (the_array.empty()) {
        out_stream << "[]";
        return;
    }

    const bool pretty = indent >= 0;
    const auto current = static_cast<std::string::size_type>(current_indent);
    const auto indented = static_cast<std::string::size_type>(current_indent + indent);
    const auto indent_str = pretty ? std::string(current, ' ') : std::string();
    const auto next_indent_str = pretty ? std::string(indented, ' ') : std::string();
    const auto newline = pretty ? "\n" : "";

    out_stream << "[" << newline;

    for (std::size_t arg_index = 0; arg_index < the_array.size(); ++arg_index) {
        out_stream << next_indent_str;
        serialize(the_array[arg_index], out_stream, indent, current_indent + indent);

        if (arg_index + 1 < the_array.size()) {
            out_stream << ",";
        }

        out_stream << newline;
    }

    out_stream << indent_str << "]";
}

inline void Json::serialize_object(const Object& the_object, std::ostringstream& out_stream, int indent, int current_indent) {
    if (the_object.empty()) {
        out_stream << "{}";
        return;
    }

    const bool pretty = indent >= 0;
    const std::string indent_str = pretty ? std::string(static_cast<std::string::size_type>(current_indent), ' ') : "";
    const std::string next_indent_str = pretty ? std::string(static_cast<std::string::size_type>(current_indent + indent), ' ') : "";
    const std::string newline = pretty ? "\n" : "";
    const std::string space = pretty ? " " : "";

    out_stream << "{" << newline;

    std::size_t arg_index = 0;

    for (const auto& [key, value] : the_object) {
        out_stream << next_indent_str;
        escape_string(key, out_stream);
        out_stream << ":" << space;
        serialize(value, out_stream, indent, current_indent + indent);

        if (++arg_index < the_object.size()) {
            out_stream << ",";
        }

        out_stream << newline;
    }

    out_stream << indent_str << "}";
}

inline std::string Json::dump(int indent) const {
    std::ostringstream out_stream;

    serialize(*this, out_stream, indent, 0);
    return out_stream.str();
}

inline Json Json::parse(std::string_view input) {
    Parser parser(input);

    return parser.parse();
}

inline Json::Parser::Parser(std::string_view input) noexcept
    : m_input(input) {}

inline void Json::Parser::skip_whitespace() noexcept {
    while (m_cursor < m_input.size()) {
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

inline char Json::Parser::peek() const noexcept {
    return m_cursor < m_input.size() ? m_input[m_cursor] : '\0';
}

inline char Json::Parser::get() {
    if (m_cursor >= m_input.size()) {
        throw std::invalid_argument("Json::parse: unexpected end of input");
    }

    return m_input[m_cursor++];
}

inline bool Json::Parser::consume(char expected) noexcept {
    if (peek() == expected) {
        ++m_cursor;
        return true;
    }

    return false;
}

inline Json Json::Parser::parse() {
    skip_whitespace();

    Json result = parse_value();

    skip_whitespace();

    if (m_cursor < m_input.size()) {
        throw std::invalid_argument("Json::parse: trailing character at position " + std::to_string(m_cursor));
    }

    return result;
}

inline Json Json::Parser::parse_value() {
    skip_whitespace();

    const auto character = peek();

    switch(character) {
        case 'n': return parse_null();
        case 't': return parse_boolean();
        case 'f': return parse_boolean();
        case '"': return parse_string_value();
        case '[': return parse_array();
        case '{': return parse_object();
        case '-': return parse_number();
        default: if(is_digit(character)) {
            return parse_number();
        } else {
            throw std::invalid_argument(std::string("Json::parse: unexpected character '")
                                        + character
                                        + "'");
        }
    }
}

inline Json Json::Parser::parse_null() {
    if (m_input.substr(m_cursor, 4) == "null") {
        m_cursor += 4;
        return Json(nullptr);
    }

    throw std::invalid_argument("Json::parse: expected 'null'");
}

inline Json Json::Parser::parse_boolean() {
    if (m_input.substr(m_cursor, 4) == "true") {
        m_cursor += 4;
        return Json(true);
    }

    if (m_input.substr(m_cursor, 5) == "false") {
        m_cursor += 5;
        return Json(false);
    }

    throw std::invalid_argument("Json::parse: expected boolean");
}

inline Json Json::Parser::parse_number() {
    std::size_t start = m_cursor;

    if (peek() == '-') {
        ++m_cursor;
    }

    while (is_digit(peek())) {
        ++m_cursor;
    }

    bool is_float_num = false;

    if (peek() == '.') {
        is_float_num = true;

        ++m_cursor;

        while (is_digit(peek())) {
            ++m_cursor;
        }
    }

    if (peek() == 'e' || peek() == 'E') {
        is_float_num = true;
        ++m_cursor;

        if (peek() == '+' || peek() == '-') {
            ++m_cursor;
        }

        while (is_digit(peek())) {
            ++m_cursor;
        }
    }

    std::string num_str(m_input.substr(start, m_cursor - start));

    if (is_float_num) {
        return Json(std::stod(num_str));
    }

    return Json(static_cast<std::int64_t>(std::stoll(num_str)));
}

inline std::string Json::Parser::parse_raw_string() {
    get(); // consume opening '"'

    std::string result;

    while (m_cursor < m_input.size()) {
        char character = get();

        if (character == '"') {
            return result;
        }

        if (character == '\\') {
            char esc = get();

            switch (esc) {
                case '"':  result += '"';  break;
                case '\\': result += '\\'; break;
                case '/':  result += '/';  break;
                case 'b':  result += '\b'; break;
                case 'f':  result += '\f'; break;
                case 'n':  result += '\n'; break;
                case 'r':  result += '\r'; break;
                case 't':  result += '\t'; break;
                default:
                    result += esc;
                    break;
            }
        } else {
            result += character;
        }
    }

    throw std::invalid_argument("Json::parse: unterminated string");
}

inline Json Json::Parser::parse_string_value() {
    return Json(parse_raw_string());
}

inline Json Json::Parser::parse_array() {
    get(); // consume '['

    Array array;

    skip_whitespace();

    if (consume(']')) {
        return Json(array);
    }

    while (true) {
        array.push_back(parse_value());
        skip_whitespace();

        if (consume(']')) {
            break;
        }

        if (!consume(',')) {
            throw std::invalid_argument("Json::parse: expected ',' or ']' in array");
        }
    }

    return Json(array);
}

inline Json Json::Parser::parse_object() {
    get(); // consume '{'

    Object object;

    skip_whitespace();

    if (consume('}')) {
        return Json(object);
    }

    while (true) {
        skip_whitespace();

        if (peek() != '"') {
            throw std::invalid_argument("Json::parse: expected string key in object");
        }

        const auto key = parse_raw_string();

        skip_whitespace();

        if (!consume(':')) {
            throw std::invalid_argument("Json::parse: expected ':' after key in object");
        }

        object[key] = parse_value();
        skip_whitespace();

        if (consume('}')) {
            break;
        }

        if (!consume(',')) {
            throw std::invalid_argument("Json::parse: expected ',' or '}' in object");
        }
    }

    return Json(object);
}

inline bool operator==(const Json& left, const Json& right) noexcept {
    return left.m_data == right.m_data;
}

inline bool operator!=(const Json& left, const Json& right) noexcept {
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

inline Json operator""_json(const char* str, std::size_t len) {
    return Json::parse(std::string_view(str, len));
}

} // namespace types
