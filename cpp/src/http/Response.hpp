#pragma once

#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>

#include "Headers.hpp"
#include "ResponseCode.hpp"

namespace http {

class Response {
public:
    Response() = default;

    [[nodiscard]] std::string_view version() const noexcept;
    [[nodiscard]] ResponseCode code() const noexcept;
    [[nodiscard]] int status_code() const noexcept;
    [[nodiscard]] std::string reason_phrase() const;

    [[nodiscard]] const Headers& headers() const noexcept;
    Headers& headers() noexcept;

    [[nodiscard]] std::string_view body() const noexcept;

    void set_version(std::string version);
    void set_status_code(uint16_t status_code);
    void set_reason_phrase(std::string reason_phrase);
    void set_body(std::string body);

    bool parse_status_line(std::string_view line);
    [[nodiscard]] std::string to_string() const;

    friend std::istream& operator>>(std::istream& in_stream, Response& response);

private:
    std::string m_version = "HTTP/1.1";
    ResponseCode m_status{ResponseCode::Status::OK};
    Headers m_headers;
    std::string m_body;
};

inline std::string_view Response::version() const noexcept {
    return m_version;
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

inline void Response::set_version(std::string version) {
    m_version = std::move(version);
}

inline void Response::set_status_code(uint16_t status_code) {
    m_status = ResponseCode(status_code);
}

inline void Response::set_reason_phrase(std::string reason_phrase) {
    m_status = ResponseCode(m_status.code(), reason_phrase);
}

inline void Response::set_body(std::string body) {
    m_body = std::move(body);
}

inline bool Response::parse_status_line(std::string_view line) {
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

    if (fields.size() < 2) {
        return false;
    }

    int code = 0;

    try {
        code = std::stoi(std::string(fields[1]));
    } catch (const std::invalid_argument&) {
        return false;
    } catch (const std::out_of_range&) {
        return false;
    }

    const auto code_value = static_cast<uint16_t>(code);

    m_version = std::string(fields[0]);
    m_status = ResponseCode(code_value, (fields.size() == 3)
                                            ? std::string(fields[2])
                                            : std::string());
    return true;
}

inline std::string Response::to_string() const {
    constexpr auto ResponseFormattingOverheadBytes = 64;

    std::string result;
    result.reserve(m_version.size()
                   + m_status.reason_phrase().size()
                   + m_body.size()
                   + ResponseFormattingOverheadBytes);

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

    const auto content_length_opt = response.m_headers.get("Content-Length");

    if (!content_length_opt.has_value()) {
        response.m_body.clear();
        return in_stream;
    }

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
        response.m_body.resize(body_length);
        in_stream.read(response.m_body.data(), static_cast<std::streamsize>(body_length));

        if (static_cast<std::size_t>(in_stream.gcount()) != body_length) {
            in_stream.setstate(std::ios_base::failbit);
        }
    } else {
        response.m_body.clear();
    }

    return in_stream;
}

} // namespace http
