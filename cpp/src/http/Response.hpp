#pragma once

#include <algorithm>
#include <cstddef>
#include <iostream>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "Headers.hpp"
#include "ResponseCode.hpp"
#include "io/MeteredReader.hpp"
#include "types/Constants.hpp"

namespace http {

class Response {
public:
    // Each limit's default: as many bytes as the Python node takes in a
    // response's status line and headers, and the largest a block can be,
    // 1 MiB.
    static constexpr std::size_t default_head_bytes = 64 * types::Kilobyte;
    static constexpr std::size_t default_body_bytes = 1 * types::Megabyte;

    // How much reading a response takes.
    struct Limits {
        // The most bytes its status line and headers may take, line ends
        // included.
        std::size_t head_bytes{default_head_bytes};
        // The largest body it may have. A larger one is not read.
        std::size_t body_bytes{default_body_bytes};
    };

    Response() = default;

    // Reads the next response on a connection, as it arrives: its status
    // line, its headers, and a body of the length they give. A status that
    // has no body (RFC 9110 §6.4.1) has none, whatever length is given, and
    // without a length there is no body. The reader is kept for the
    // connection, over a BufferedReader over its socket, so that what
    // arrived after one response is read as the next. Returns nothing if
    // the connection ended before the response was all sent. Throws
    // io::LimitExceeded if the status line and headers take more than
    // limits.head_bytes, or the body is larger than limits.body_bytes,
    // before reading it; std::invalid_argument if the status line or the
    // length cannot be read, or the body is sent with a Transfer-Encoding,
    // which leaves where it ends unknown (RFC 9112 §6.1); and as Headers
    // does for a header line it cannot read. Each leaves where the next
    // response starts in doubt.
    [[nodiscard]] static std::optional<Response> read(io::MeteredReader& reader,
                                                      const Limits& limits);

    [[nodiscard]] std::string_view version() const noexcept;
    [[nodiscard]] const ResponseCode& code() const noexcept;
    [[nodiscard]] int status_code() const noexcept;
    [[nodiscard]] std::string reason_phrase() const;

    [[nodiscard]] const Headers& headers() const noexcept;
    Headers& headers() noexcept;

    [[nodiscard]] std::string_view body() const noexcept;

    void set_version(std::string new_version);
    void set_status_code(uint16_t new_status_code);
    void set_status_code(ResponseCode::Status new_status_code);
    void set_reason_phrase(const std::string& new_reason_phrase);
    void set_body(std::string new_body);

    bool parse_status_line(std::string_view line);
    [[nodiscard]] std::string to_string() const;

    friend std::istream& operator>>(std::istream& in_stream, Response& response);

private:
    std::string m_version = "HTTP/1.1";
    ResponseCode m_status{ResponseCode::Status::Ok};
    Headers m_headers;
    std::string m_body;

    [[nodiscard]] static bool _read_line(io::MeteredReader& reader, std::string& line);
    [[nodiscard]] std::size_t _body_length(const Limits& limits) const;
};

inline std::string_view Response::version() const noexcept {
    return m_version;
}

inline const ResponseCode& Response::code() const noexcept {
    return m_status;
}

inline int Response::status_code() const noexcept {
    return m_status.code();
}

inline std::string Response::reason_phrase() const {
    return std::string(m_status.reason_phrase());
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

inline void Response::set_status_code(uint16_t new_status_code) {
    m_status = ResponseCode(new_status_code);
}

inline void Response::set_status_code(ResponseCode::Status new_status_code) {
    set_status_code(static_cast<uint16_t>(new_status_code));
}

inline void Response::set_reason_phrase(const std::string& new_reason_phrase) {
    m_status = ResponseCode(m_status.code(), new_reason_phrase);
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

    const auto code_value = static_cast<uint16_t>(parsed_code);

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
    std::string line;
    std::string last_key;

    reader.reset(limits.head_bytes);

    if (!_read_line(reader, line)) {
        return std::nullopt;
    }

    if (!response.parse_status_line(line)) {
        throw std::invalid_argument("Not an HTTP status line: " + line);
    }

    while (true) {
        if (!_read_line(reader, line)) {
            return std::nullopt;
        }

        if (line.empty()) {
            break;
        }

        last_key = Headers::process_header_line(line, last_key, response.m_headers);
    }

    const auto length = response._body_length(limits);

    reader.reset(length);

    const auto body = reader.read_exact(length);

    if (body.size() != length) {
        return std::nullopt;
    }

    response.m_body.reserve(length);
    std::transform(body.begin(), body.end(), std::back_inserter(response.m_body),
                   [](std::byte byte) {
        return static_cast<char>(std::to_integer<unsigned char>(byte));
    });
    return response;
}

// Reads a line, up to its \n, leaving out the \n and a \r before it.
// Returns false if the connection ended before its end.
inline bool Response::_read_line(io::MeteredReader& reader, std::string& line) {
    line = reader.read_until('\n');

    if (line.empty() || '\n' != line.back()) {
        return false;
    }

    line.pop_back();
    line.resize(Headers::trim_trailing_cr(line).size());
    return true;
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

    if (value->empty() || std::string_view::npos != value->find_first_not_of("0123456789")) {
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
