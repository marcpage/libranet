#pragma once

#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

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
        Ok = 200,
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
        UriTooLong = 414,
        UnsupportedMediaType = 415,
        RangeNotSatisfiable = 416,
        ExpectationFailed = 417,
        ImATeapot = 418,
        UnprocessableEntity = 422,
        TooManyRequests = 429,
        RequestHeaderFieldsTooLarge = 431,

        // 5xx Server Errors
        InternalServerError = 500,
        NotImplemented = 501,
        BadGateway = 502,
        ServiceUnavailable = 503,
        GatewayTimeout = 504,
        HttpVersionNotSupported = 505,
        VariantAlsoNegotiates = 506,
        InsufficientStorage = 507,
        LoopDetected = 508,
        NotExtended = 510,
        NetworkAuthenticationRequired = 511,
    };

    // Implicit conversion from Status enum, with optional custom reason phrase
    // cppcheck-suppress noExplicitConstructor
    // NOLINTNEXTLINE(*-explicit-constructor)
    ResponseCode(Status status, std::optional<std::string> custom_reason = std::nullopt);

    explicit ResponseCode(std::uint16_t code,
                          std::optional<std::string> custom_reason = std::nullopt);

    [[nodiscard]] Status status() const noexcept;
    [[nodiscard]] std::uint16_t code() const noexcept;
    [[nodiscard]] std::string_view reason_phrase() const noexcept;

    [[nodiscard]] bool is_informational() const noexcept; // 1xx
    [[nodiscard]] bool is_success() const noexcept;       // 2xx
    [[nodiscard]] bool is_redirection() const noexcept;   // 3xx
    [[nodiscard]] bool is_client_error() const noexcept;  // 4xx
    [[nodiscard]] bool is_server_error() const noexcept;  // 5xx
    [[nodiscard]] bool is_error() const noexcept;         // 4xx or 5xx

    static std::string_view default_reason_phrase(Status status) noexcept;
    static std::string_view default_reason_phrase(std::uint16_t code) noexcept;

    friend bool operator==(const ResponseCode& left, const ResponseCode& right) noexcept;
    friend bool operator!=(const ResponseCode& left, const ResponseCode& right) noexcept;
    friend bool operator==(const ResponseCode& left, Status right) noexcept;
    friend bool operator!=(const ResponseCode& left, Status right) noexcept;

    friend std::ostream& operator<<(std::ostream& out_stream, const ResponseCode& response_code);

    ResponseCode() = default;
    ~ResponseCode() = default;
    ResponseCode(const ResponseCode&) = default;
    ResponseCode& operator=(const ResponseCode&) = default;
    ResponseCode(ResponseCode&&) noexcept = default;
    ResponseCode& operator=(ResponseCode&&) noexcept = default;

private:
    // Each class of codes runs up to the start of the next.
    static constexpr std::uint16_t informational_start = 100;
    static constexpr std::uint16_t success_start = 200;
    static constexpr std::uint16_t redirection_start = 300;
    static constexpr std::uint16_t client_error_start = 400;
    static constexpr std::uint16_t server_error_start = 500;
    static constexpr std::uint16_t server_error_end = 600;

    Status m_status = Status::Ok;
    std::optional<std::string> m_custom_reason;
};

inline ResponseCode::ResponseCode(Status status, std::optional<std::string> custom_reason)
    : m_status(status), m_custom_reason(std::move(custom_reason)) {}

// A code that Status does not name is kept as it is: HTTP allows any
// three-digit code, and one not known is taken by its class (RFC 9110,
// section 15), as the helpers do.
inline ResponseCode::ResponseCode(std::uint16_t code, std::optional<std::string> custom_reason)
    // NOLINTNEXTLINE(clang-analyzer-optin.core.EnumCastOutOfRange)
    : m_status(static_cast<Status>(code)), m_custom_reason(std::move(custom_reason)) {}

inline ResponseCode::Status ResponseCode::status() const noexcept {
    return m_status;
}

inline std::uint16_t ResponseCode::code() const noexcept {
    return static_cast<std::uint16_t>(m_status);
}

inline std::string_view ResponseCode::reason_phrase() const noexcept {
    if (m_custom_reason.has_value()) {
        return *m_custom_reason;
    }

    return default_reason_phrase(m_status);
}

inline bool ResponseCode::is_informational() const noexcept {
    const auto number = code();

    return number >= informational_start && number < success_start;
}

inline bool ResponseCode::is_success() const noexcept {
    const auto number = code();

    return number >= success_start && number < redirection_start;
}

inline bool ResponseCode::is_redirection() const noexcept {
    const auto number = code();

    return number >= redirection_start && number < client_error_start;
}

inline bool ResponseCode::is_client_error() const noexcept {
    const auto number = code();

    return number >= client_error_start && number < server_error_start;
}

inline bool ResponseCode::is_server_error() const noexcept {
    const auto number = code();

    return number >= server_error_start && number < server_error_end;
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
        case Status::Ok: return "OK";
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
        case Status::UriTooLong: return "URI Too Long";
        case Status::UnsupportedMediaType: return "Unsupported Media Type";
        case Status::RangeNotSatisfiable: return "Range Not Satisfiable";
        case Status::ExpectationFailed: return "Expectation Failed";
        case Status::ImATeapot: return "I'm a teapot";
        case Status::UnprocessableEntity: return "Unprocessable Entity";
        case Status::TooManyRequests: return "Too Many Requests";
        case Status::RequestHeaderFieldsTooLarge: return "Request Header Fields Too Large";

        // 5xx
        case Status::InternalServerError: return "Internal Server Error";
        case Status::NotImplemented: return "Not Implemented";
        case Status::BadGateway: return "Bad Gateway";
        case Status::ServiceUnavailable: return "Service Unavailable";
        case Status::GatewayTimeout: return "Gateway Timeout";
        case Status::HttpVersionNotSupported: return "HTTP Version Not Supported";
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

inline bool operator==(const ResponseCode& left, const ResponseCode& right) noexcept {
    return left.code() == right.code();
}

inline bool operator!=(const ResponseCode& left, const ResponseCode& right) noexcept {
    return !(left == right);
}

inline bool operator==(const ResponseCode& left, ResponseCode::Status right) noexcept {
    return left.status() == right;
}

inline bool operator!=(const ResponseCode& left, ResponseCode::Status right) noexcept {
    return !(left == right);
}

inline std::ostream& operator<<(std::ostream& out_stream, const ResponseCode& response_code) {
    return out_stream << response_code.code() << " " << response_code.reason_phrase();
}

} // namespace http
