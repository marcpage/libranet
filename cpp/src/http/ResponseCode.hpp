#pragma once

#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>

namespace http {

class ResponseCode {
public:
    enum class Status : std::uint16_t {
        // 1xx Informational
        Continue = 100,
        SwitchingProtocols = 101,
        Processing = 102,
        EarlyHints = 103,

        // 2xx Success
        OK = 200,
        Created = 201,
        Accepted = 202,
        NonAuthoritativeInformation = 203,
        NoContent = 204,
        ResetContent = 205,
        PartialContent = 206,
        MultiStatus = 207,

        // 3xx Redirection
        MultipleChoices = 300,
        MovedPermanently = 301,
        Found = 302,
        SeeOther = 303,
        NotModified = 304,
        UseProxy = 305,
        TemporaryRedirect = 307,
        PermanentRedirect = 308,

        // 4xx Client Errors
        BadRequest = 400,
        Unauthorized = 401,
        PaymentRequired = 402,
        Forbidden = 403,
        NotFound = 404,
        MethodNotAllowed = 405,
        NotAcceptable = 406,
        ProxyAuthenticationRequired = 407,
        RequestTimeout = 408,
        Conflict = 409,
        Gone = 410,
        LengthRequired = 411,
        PreconditionFailed = 412,
        PayloadTooLarge = 413,
        URITooLong = 414,
        UnsupportedMediaType = 415,
        RangeNotSatisfiable = 416,
        ExpectationFailed = 417,
        ImATeapot = 418,
        UnprocessableEntity = 422,
        TooManyRequests = 429,

        // 5xx Server Errors
        InternalServerError = 500,
        NotImplemented = 501,
        BadGateway = 502,
        ServiceUnavailable = 503,
        GatewayTimeout = 504,
        HTTPVersionNotSupported = 505,
        VariantAlsoNegotiates = 506,
        InsufficientStorage = 507,
        LoopDetected = 508,
        NotExtended = 510,
        NetworkAuthenticationRequired = 511
    };

    // Implicit conversion from Status enum, with optional custom reason phrase
    ResponseCode(Status status, std::optional<std::string> custom_reason = std::nullopt);

    // Construct directly from raw integer (e.g., 200)
    explicit ResponseCode(std::uint16_t code, std::optional<std::string> custom_reason = std::nullopt);

    ResponseCode() = default;
    ~ResponseCode() = default;
    ResponseCode(const ResponseCode&) = default;
    ResponseCode& operator=(const ResponseCode&) = default;
    ResponseCode(ResponseCode&&) noexcept = default;
    ResponseCode& operator=(ResponseCode&&) noexcept = default;

    // Core Accessors
    Status status() const noexcept;
    std::uint16_t code() const noexcept;
    std::string_view reason_phrase() const noexcept;

    // Helpers
    bool is_informational() const noexcept; // 1xx
    bool is_success() const noexcept;       // 2xx
    bool is_redirection() const noexcept;   // 3xx
    bool is_client_error() const noexcept;  // 4xx
    bool is_server_error() const noexcept;  // 5xx
    bool is_error() const noexcept;         // 4xx or 5xx

    static std::string_view default_reason_phrase(Status status) noexcept;
    static std::string_view default_reason_phrase(std::uint16_t code) noexcept;

    // Comparisons
    friend bool operator==(const ResponseCode& lhs, const ResponseCode& rhs) noexcept;
    friend bool operator!=(const ResponseCode& lhs, const ResponseCode& rhs) noexcept;
    friend bool operator==(const ResponseCode& lhs, Status rhs) noexcept;
    friend bool operator!=(const ResponseCode& lhs, Status rhs) noexcept;

    friend std::ostream& operator<<(std::ostream& os, const ResponseCode& sc);

private:
    // Each class of codes runs up to the start of the next.
    static constexpr std::uint16_t informational_start = 100;
    static constexpr std::uint16_t success_start = 200;
    static constexpr std::uint16_t redirection_start = 300;
    static constexpr std::uint16_t client_error_start = 400;
    static constexpr std::uint16_t server_error_start = 500;
    static constexpr std::uint16_t server_error_end = 600;

    Status m_status = Status::OK;
    std::optional<std::string> m_custom_reason;
};

inline ResponseCode::ResponseCode(Status status, std::optional<std::string> custom_reason)
    : m_status(status), m_custom_reason(std::move(custom_reason)) {}

inline ResponseCode::ResponseCode(std::uint16_t code, std::optional<std::string> custom_reason)
    : m_status(static_cast<Status>(code)), m_custom_reason(std::move(custom_reason)) {}

inline ResponseCode::Status ResponseCode::status() const noexcept {
    return m_status;
}

inline std::uint16_t ResponseCode::code() const noexcept {
    return static_cast<std::uint16_t>(m_status);
}

inline std::string_view ResponseCode::reason_phrase() const noexcept {
    if (m_custom_reason.has_value()) {
        return m_custom_reason.value();
    }

    return default_reason_phrase(m_status);
}

inline bool ResponseCode::is_informational() const noexcept {
    const auto c = code();

    return c >= informational_start && c < success_start;
}

inline bool ResponseCode::is_success() const noexcept {
    const auto c = code();

    return c >= success_start && c < redirection_start;
}

inline bool ResponseCode::is_redirection() const noexcept {
    const auto c = code();

    return c >= redirection_start && c < client_error_start;
}

inline bool ResponseCode::is_client_error() const noexcept {
    const auto c = code();

    return c >= client_error_start && c < server_error_start;
}

inline bool ResponseCode::is_server_error() const noexcept {
    const auto c = code();

    return c >= server_error_start && c < server_error_end;
}

inline bool ResponseCode::is_error() const noexcept {
    return is_client_error() || is_server_error();
}

inline std::string_view ResponseCode::default_reason_phrase(Status status) noexcept {
    switch (status) {
        // 1xx
        case Status::Continue: return "Continue";
        case Status::SwitchingProtocols: return "Switching Protocols";
        case Status::Processing: return "Processing";
        case Status::EarlyHints: return "Early Hints";

        // 2xx
        case Status::OK: return "OK";
        case Status::Created: return "Created";
        case Status::Accepted: return "Accepted";
        case Status::NonAuthoritativeInformation: return "Non-Authoritative Information";
        case Status::NoContent: return "No Content";
        case Status::ResetContent: return "Reset Content";
        case Status::PartialContent: return "Partial Content";
        case Status::MultiStatus: return "Multi-Status";

        // 3xx
        case Status::MultipleChoices: return "Multiple Choices";
        case Status::MovedPermanently: return "Moved Permanently";
        case Status::Found: return "Found";
        case Status::SeeOther: return "See Other";
        case Status::NotModified: return "Not Modified";
        case Status::UseProxy: return "Use Proxy";
        case Status::TemporaryRedirect: return "Temporary Redirect";
        case Status::PermanentRedirect: return "Permanent Redirect";

        // 4xx
        case Status::BadRequest: return "Bad Request";
        case Status::Unauthorized: return "Unauthorized";
        case Status::PaymentRequired: return "Payment Required";
        case Status::Forbidden: return "Forbidden";
        case Status::NotFound: return "Not Found";
        case Status::MethodNotAllowed: return "Method Not Allowed";
        case Status::NotAcceptable: return "Not Acceptable";
        case Status::ProxyAuthenticationRequired: return "Proxy Authentication Required";
        case Status::RequestTimeout: return "Request Timeout";
        case Status::Conflict: return "Conflict";
        case Status::Gone: return "Gone";
        case Status::LengthRequired: return "Length Required";
        case Status::PreconditionFailed: return "Precondition Failed";
        case Status::PayloadTooLarge: return "Payload Too Large";
        case Status::URITooLong: return "URI Too Long";
        case Status::UnsupportedMediaType: return "Unsupported Media Type";
        case Status::RangeNotSatisfiable: return "Range Not Satisfiable";
        case Status::ExpectationFailed: return "Expectation Failed";
        case Status::ImATeapot: return "I'm a teapot";
        case Status::UnprocessableEntity: return "Unprocessable Entity";
        case Status::TooManyRequests: return "Too Many Requests";

        // 5xx
        case Status::InternalServerError: return "Internal Server Error";
        case Status::NotImplemented: return "Not Implemented";
        case Status::BadGateway: return "Bad Gateway";
        case Status::ServiceUnavailable: return "Service Unavailable";
        case Status::GatewayTimeout: return "Gateway Timeout";
        case Status::HTTPVersionNotSupported: return "HTTP Version Not Supported";
        case Status::VariantAlsoNegotiates: return "Variant Also Negotiates";
        case Status::InsufficientStorage: return "Insufficient Storage";
        case Status::LoopDetected: return "Loop Detected";
        case Status::NotExtended: return "Not Extended";
        case Status::NetworkAuthenticationRequired: return "Network Authentication Required";

        default: return "Unknown Status Code";
    }
}

inline std::string_view ResponseCode::default_reason_phrase(std::uint16_t code) noexcept {
    return default_reason_phrase(static_cast<Status>(code));
}

inline bool operator==(const ResponseCode& lhs, const ResponseCode& rhs) noexcept {
    return lhs.code() == rhs.code();
}

inline bool operator!=(const ResponseCode& lhs, const ResponseCode& rhs) noexcept {
    return !(lhs == rhs);
}

inline bool operator==(const ResponseCode& lhs, ResponseCode::Status rhs) noexcept {
    return lhs.status() == rhs;
}

inline bool operator!=(const ResponseCode& lhs, ResponseCode::Status rhs) noexcept {
    return !(lhs == rhs);
}

inline std::ostream& operator<<(std::ostream& os, const ResponseCode& sc) {
    return os << sc.code() << " " << sc.reason_phrase();
}

} // namespace http
