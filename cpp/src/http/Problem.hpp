#pragma once

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "Request.hpp"
#include "Response.hpp"
#include "ResponseCode.hpp"
#include "types/Json.hpp"

namespace http {

// Problem details (RFC 9457), the body of every error answer (HTTP API §17).
// One with no type is about:blank (RFC 9457 §4.2.1).
// cppcheck-suppress noConstructor
class Problem {
public:
    // The problem types HTTP API §17.2 defines.
    enum class Type : std::uint8_t {
        InvalidContentAddress,
        InvalidSearchPrefix,
        ContentUnavailable,
        ContentTooLarge,
        SignatureRequired,
        InvalidSignature,
        InvalidList,
        CredentialRequired,
        InvalidRequest,
        UnusableBundle,
        BundleAuthenticationRequired,
        SessionRequired,
        InvalidBundleSignature,
        WrongKey,
    };

    static constexpr std::string_view content_type = "application/problem+json";

    // Each throws std::invalid_argument for a status that is not an error, or
    // a type that names none.
    Problem(ResponseCode::Status status, std::string detail);
    Problem(Type type, ResponseCode::Status status, std::string detail);

    [[nodiscard]] std::optional<Type> type() const noexcept;
    [[nodiscard]] std::string type_uri() const;
    [[nodiscard]] std::string_view title() const;
    [[nodiscard]] ResponseCode::Status status() const noexcept;
    [[nodiscard]] const std::string& detail() const noexcept;

    [[nodiscard]] types::Json to_json() const;
    void answer(Response& response) const;

    // With 404, for a path no resource is at.
    static void answer_not_found(Response& response);
    // With 405, naming in Allow the methods the path does take, as "GET, PUT"
    // (HTTP API §4, RFC 9110 §15.5.6).
    static void answer_method_not_allowed(const Request& request, std::string_view allowed,
                                          Response& response);

private:
    // What HTTP API §17.2 says of a problem type.
    struct Definition {
        std::string_view name; // The last segment of its URI
        std::string_view title;
    };

    static constexpr std::string_view about_blank = "about:blank";
    static constexpr std::string_view type_base = "https://libranet.org/problems/";

    std::optional<Type> m_type;
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    ResponseCode::Status m_status;
    std::string m_detail;

    [[nodiscard]] static ResponseCode::Status _checked(ResponseCode::Status status);
    [[nodiscard]] static Type _checked(Type type);
    [[nodiscard]] static Definition _definition(Type type);
};

inline Problem::Problem(ResponseCode::Status status, std::string detail)
    : m_status(_checked(status))
    , m_detail(std::move(detail)) {}

inline Problem::Problem(Type type, ResponseCode::Status status, std::string detail)
    : m_type(_checked(type))
    , m_status(_checked(status))
    , m_detail(std::move(detail)) {}

inline std::optional<Problem::Type> Problem::type() const noexcept {
    return m_type;
}

inline std::string Problem::type_uri() const {
    if (!m_type) {
        return std::string(about_blank);
    }

    return std::string(type_base) + std::string(_definition(*m_type).name);
}

inline std::string_view Problem::title() const {
    if (!m_type) {
        return ResponseCode::default_reason_phrase(m_status);
    }

    return _definition(*m_type).title;
}

inline ResponseCode::Status Problem::status() const noexcept {
    return m_status;
}

inline const std::string& Problem::detail() const noexcept {
    return m_detail;
}

inline types::Json Problem::to_json() const {
    types::Json::Object members;

    members["type"] = type_uri();
    members["title"] = std::string(title());
    members["status"] = ResponseCode(m_status).code();
    members["detail"] = m_detail;
    return types::Json(std::move(members));
}

inline void Problem::answer(Response& response) const {
    response.set_status_code(m_status);
    response.headers().set("Content-Type", std::string(content_type));
    response.set_body(to_json().dump());
}

inline void Problem::answer_not_found(Response& response) {
    Problem(ResponseCode::Status::NotFound, "No resource exists at this path.").answer(response);
}

inline void Problem::answer_method_not_allowed(const Request& request, std::string_view allowed,
                                               Response& response) {
    const Problem problem(ResponseCode::Status::MethodNotAllowed,
                          std::string(request.method()) + " is not supported for this resource.");

    // Mistaken: problem keeps its own copy of the detail, not a view of the
    // temporary. Having lost the class's constructors (see m_status),
    // cppcheck takes the detail to be stored as a view.
    // cppcheck-suppress danglingTemporaryLifetime
    problem.answer(response);
    response.headers().set("Allow", std::string(allowed));
}

inline ResponseCode::Status Problem::_checked(ResponseCode::Status status) {
    const ResponseCode code(status);

    if (!code.is_error()) {
        throw std::invalid_argument("A problem's status must be an error: "
                                    + std::to_string(code.code()));
    }

    return status;
}

inline Problem::Type Problem::_checked(Type type) {
    (void)_definition(type);
    return type;
}

inline Problem::Definition Problem::_definition(Type type) {
    switch (type) {
        case Type::InvalidContentAddress:
            return {"invalid-content-address", "Invalid content address"};
        case Type::InvalidSearchPrefix: return {"invalid-search-prefix", "Invalid search prefix"};
        case Type::ContentUnavailable:
            return {"content-unavailable", "Content temporarily unavailable"};
        case Type::ContentTooLarge: return {"content-too-large", "Content too large"};
        case Type::SignatureRequired: return {"signature-required", "Signature required"};
        case Type::InvalidSignature: return {"invalid-signature", "Invalid signature"};
        case Type::InvalidList: return {"invalid-list", "Invalid list"};
        case Type::CredentialRequired: return {"credential-required", "Credential required"};
        case Type::InvalidRequest: return {"invalid-request", "Invalid request"};
        case Type::UnusableBundle: return {"unusable-bundle", "Unusable bundle"};
        case Type::BundleAuthenticationRequired:
            return {"bundle-authentication-required", "Authentication required"};
        case Type::SessionRequired: return {"session-required", "Session required"};
        case Type::InvalidBundleSignature:
            return {"invalid-bundle-signature", "Invalid bundle signature"};
        case Type::WrongKey: return {"wrong-key", "Wrong key"};
    }

    throw std::invalid_argument("Not a problem type: " + std::to_string(static_cast<int>(type)));
}

} // namespace http
