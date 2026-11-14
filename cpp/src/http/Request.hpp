#pragma once

#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>

#include "Headers.hpp"

namespace http {

class Request {
public:
    Request() = default;

    [[nodiscard]] std::string_view method() const noexcept;
    [[nodiscard]] std::string_view uri() const noexcept;
    [[nodiscard]] std::string_view version() const noexcept;

    [[nodiscard]] const Headers& headers() const noexcept;
    Headers& headers() noexcept;

    [[nodiscard]] std::string_view body() const noexcept;

    void set_method(std::string method);
    void set_uri(std::string uri);
    void set_version(std::string version);
    void set_body(std::string body);

    bool parse_request_line(std::string_view line);
    [[nodiscard]] std::string to_string() const;

    friend std::istream& operator>>(std::istream& in_stream, Request& request);

private:
    std::string m_method;
    std::string m_uri;
    std::string m_version = "HTTP/1.1";
    Headers m_headers;
    std::string m_body;
};

// --- Inline Member Function Implementations ---

inline std::string_view Request::method() const noexcept {
    return m_method;
}

inline std::string_view Request::uri() const noexcept {
    return m_uri;
}

inline std::string_view Request::version() const noexcept {
    return m_version;
}

inline const Headers& Request::headers() const noexcept {
    return m_headers;
}

inline Headers& Request::headers() noexcept {
    return m_headers;
}

inline std::string_view Request::body() const noexcept {
    return m_body;
}

inline void Request::set_method(std::string method) {
    m_method = std::move(method);
}

inline void Request::set_uri(std::string uri) {
    m_uri = std::move(uri);
}

inline void Request::set_version(std::string version) {
    m_version = std::move(version);
}

inline void Request::set_body(std::string body) {
    m_body = std::move(body);
}

inline bool Request::parse_request_line(std::string_view line) {
    line = Headers::trim_trailing_cr(line);

    if (!line.empty() && line.back() == '\n') {
        line.remove_suffix(1);
        line = Headers::trim_trailing_cr(line);
    }

    line = Headers::trim_whitespace(line);

    if (line.empty()) {
        return false;
    }

    const auto fields = Headers::split_spaces(line, 3);

    if (fields.size() != 3) {
        return false;
    }

    m_method = std::string(fields[0]);
    m_uri = std::string(fields[1]);
    m_version = std::string(fields[2]);
    return true;
}

inline std::string Request::to_string() const {
    constexpr auto RequestFormattingOverheadBytes = 64;
    std::string result;
    result.reserve(m_method.size()
                   + m_uri.size()
                   + m_version.size()
                   + m_body.size()
                   + RequestFormattingOverheadBytes);

    result += m_method;
    result += ' ';
    result += m_uri;
    result += ' ';
    result += m_version;
    result += "\r\n";

    result += m_headers.to_string(); // Already ends with \r\n\r\n
    result += m_body;

    return result;
}

inline std::istream& operator>>(std::istream& in_stream, Request& request) {
    std::string raw_line;

    if (!std::getline(in_stream, raw_line)
        || !request.parse_request_line(raw_line)) {
        in_stream.setstate(std::ios_base::failbit);
        return in_stream;
    }

    if (!(in_stream >> request.m_headers)) {
        return in_stream;
    }

    const auto content_length_opt = request.m_headers.get("Content-Length");

    if (content_length_opt.has_value()) {
        std::size_t body_length = 0;

        try {
            body_length = std::stoull(std::string(content_length_opt.value()));
        } catch (const std::invalid_argument&) {
            in_stream.setstate(std::ios_base::failbit);
            return in_stream;
        } catch (const std::out_of_range&) {
            in_stream.setstate(std::ios_base::failbit);
            return in_stream;
        }

        if (body_length > 0) {
            request.m_body.resize(body_length);
            in_stream.read(request.m_body.data(), static_cast<std::streamsize>(body_length));

            if (static_cast<std::size_t>(in_stream.gcount()) != body_length) {
                in_stream.setstate(std::ios_base::failbit);
            }
        } else {
            request.m_body.clear();
        }
    } else {
        request.m_body.clear();
    }

    return in_stream;
}

} // namespace http
