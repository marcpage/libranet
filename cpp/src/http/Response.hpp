#pragma once

#include <cstddef>
#include <cstdint>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "Headers.hpp"
#include "ResponseCode.hpp"
#include "io/IReader.hpp"
#include "io/MeteredReader.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

namespace http {

class Response {
public:
    static constexpr std::size_t default_head_bytes = 64 * types::Kilobyte;
    static constexpr std::size_t default_body_bytes = 1 * types::Megabyte;

    struct Limits {
        // Line ends included.
        std::size_t head_bytes{default_head_bytes};
        // A larger body is not read.
        std::size_t body_bytes{default_body_bytes};
    };

    [[nodiscard]] static std::optional<Response> read(io::MeteredReader& reader,
                                                      const Limits& limits);

    [[nodiscard]] std::string_view version() const noexcept;
    [[nodiscard]] const ResponseCode& code() const noexcept;
    [[nodiscard]] std::uint16_t status_code() const noexcept;
    [[nodiscard]] std::string_view reason_phrase() const noexcept;

    [[nodiscard]] const Headers& headers() const noexcept;
    Headers& headers() noexcept;

    [[nodiscard]] std::string_view body() const noexcept;

    void set_version(std::string new_version);
    void set_status_code(std::uint16_t new_status_code);
    void set_status_code(ResponseCode::Status new_status_code);
    void set_reason_phrase(std::string new_reason_phrase);
    void set_body(std::string new_body);

    bool parse_status_line(std::string_view line);
    [[nodiscard]] std::string to_string() const;

    friend std::istream& operator>>(std::istream& in_stream, Response& response);

    Response() = default;

private:
    std::string m_version = "HTTP/1.1";
    ResponseCode m_status{ResponseCode::Status::Ok};
    Headers m_headers;
    std::string m_body;

    [[nodiscard]] std::size_t _body_length(const Limits& limits) const;
};

inline std::string_view Response::version() const noexcept {
    return m_version;
}

inline const ResponseCode& Response::code() const noexcept {
    return m_status;
}

inline std::uint16_t Response::status_code() const noexcept {
    return m_status.code();
}

inline std::string_view Response::reason_phrase() const noexcept {
    return m_status.reason_phrase();
}

inline const Headers& Response::headers() const noexcept {
    return m_headers;
}

inline Headers& Response::headers() noexcept {
    return m_headers;
}

inline std::string_view Response::body() const noexcept {
    return m_body;
}

inline void Response::set_version(std::string new_version) {
    m_version = std::move(new_version);
}

inline void Response::set_status_code(std::uint16_t new_status_code) {
    m_status = ResponseCode(new_status_code);
}

inline void Response::set_status_code(ResponseCode::Status new_status_code) {
    set_status_code(static_cast<std::uint16_t>(new_status_code));
}

inline void Response::set_reason_phrase(std::string new_reason_phrase) {
    m_status = ResponseCode(m_status.code(), std::move(new_reason_phrase));
}

inline void Response::set_body(std::string new_body) {
    m_body = std::move(new_body);
}

inline bool Response::parse_status_line(std::string_view line) {
    line = Headers::trim_trailing_cr(line);

    if (!line.empty() && '\n' == line.back()) {
        line.remove_suffix(1);
        line = Headers::trim_trailing_cr(line);
    }

    line = Headers::trim_whitespace(line);

    if (line.empty()) {
        return false;
    }

    const auto fields = Headers::split_spaces(line, 3);

    if (fields.size() < 2) {
        return false;
    }

    int parsed_code = 0;

    try {
        parsed_code = std::stoi(std::string(fields.at(1)));
    } catch (const std::invalid_argument&) {
        return false;
    } catch (const std::out_of_range&) {
        return false;
    }

    const auto code_value = static_cast<std::uint16_t>(parsed_code);

    m_version = std::string(fields.at(0));
    m_status = ResponseCode(code_value, (3 == fields.size())
                                            ? std::string(fields.at(2))
                                            : std::string());
    return true;
}

inline std::string Response::to_string() const {
    constexpr auto formatting_overhead_bytes = 64;

    std::string result;
    result.reserve(m_version.size()
                   + m_status.reason_phrase().size()
                   + m_body.size()
                   + formatting_overhead_bytes);

    result += m_version;
    result += ' ';
    result += std::to_string(m_status.code());

    if (!m_status.reason_phrase().empty()) {
        result += ' ';
        result += std::string(m_status.reason_phrase());
    }

    result += "\r\n";

    result += m_headers.to_string(); // Already ends with \r\n\r\n
    result += m_body;

    return result;
}

inline std::optional<Response> Response::read(io::MeteredReader& reader, const Limits& limits) {
    Response response;
    std::string last_key;

    reader.reset(limits.head_bytes);

    const auto status_line = reader.read_line(io::IReader::EolAction::MustHaveEol);

    if (!status_line) {
        return std::nullopt;
    }

    if (!response.parse_status_line(*status_line)) {
        throw std::invalid_argument("Not an HTTP status line: " + *status_line);
    }

    while (true) {
        const auto line = reader.read_line(io::IReader::EolAction::MustHaveEol);

        if (!line) {
            return std::nullopt;
        }

        if (line->empty()) {
            break;
        }

        last_key = Headers::process_header_line(*line, last_key, response.m_headers);
    }

    const auto length = response._body_length(limits);

    reader.reset(length);

    const auto body = reader.read_exact(length);

    if (body.size() != length) {
        return std::nullopt;
    }

    response.m_body = types::Converter::text_of(body);
    return response;
}

// The length of the body the headers give: none for a status that has no
// body, nor without a Content-Length. A length is digits alone (RFC 9110
// §8.6), and no larger than limits.body_bytes.
inline std::size_t Response::_body_length(const Limits& limits) const {
    if (m_status.is_informational() || m_status == ResponseCode::Status::NoContent
        || m_status == ResponseCode::Status::NotModified) {
        return 0;
    }

    if (m_headers.has("Transfer-Encoding")) {
        throw std::invalid_argument("A body sent with a Transfer-Encoding cannot be read.");
    }

    const auto value = m_headers.get("Content-Length");

    if (!value.has_value()) {
        return 0;
    }

    if (value->empty()
        || std::string_view::npos != value->find_first_not_of(types::decimal_digits)) {
        throw std::invalid_argument("Content-Length is not a length: " + std::string(*value));
    }

    std::size_t length = 0;
    bool fits = true;

    try {
        length = std::stoull(std::string(*value));
    } catch (const std::out_of_range&) {
        fits = false;
    }

    if (!fits || length > limits.body_bytes) {
        throw io::LimitExceeded("The response's body is larger than "
                                + std::to_string(limits.body_bytes) + " bytes.");
    }

    return length;
}

inline std::istream& operator>>(std::istream& in_stream, Response& response) {
    std::string raw_line;

    if (!std::getline(in_stream, raw_line)
        || !response.parse_status_line(raw_line)) {
        in_stream.setstate(std::ios_base::failbit);
        return in_stream;
    }

    if (!(in_stream >> response.m_headers)) {
        return in_stream;
    }

    const auto content_length = response.m_headers.get("Content-Length");

    if (!content_length.has_value()) {
        response.m_body.clear();
        return in_stream;
    }

    std::size_t body_length = 0;

    try {
        body_length = std::stoull(std::string(content_length.value()));
    } catch (const std::invalid_argument&) {
        in_stream.setstate(std::ios_base::failbit);
        return in_stream;
    } catch (const std::out_of_range&) {
        in_stream.setstate(std::ios_base::failbit);
        return in_stream;
    }

    Headers::read_body(in_stream, body_length, response.m_body);

    return in_stream;
}

} // namespace http
