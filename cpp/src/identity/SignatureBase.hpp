#pragma once

#include <chrono>
#include <optional>
#include <string>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/StructuredField.hpp"

namespace identity {

// The signature base of RFC 9421 §2.5: the text a signature on a message
// signs. Components with parameters of their own are not taken.
class SignatureBase {
public:
    using Time = std::chrono::system_clock::time_point;
    using InnerList = http::StructuredField::InnerList;

    // HTTP API §11.2, written in this order, as the Python node writes them,
    // each in whole seconds since the UNIX epoch (RFC 9421 §2.3).
    struct Parameters {
        Time created;
        std::string keyid;
        std::optional<Time> expires;
    };

    // The base of a node's signature on a request (HTTP API §11). Throws
    // std::invalid_argument as the constructor does.
    [[nodiscard]] static SignatureBase of(const http::Request& request,
                                          const Parameters& parameters);
    // The base of a node's signature on an answer.
    [[nodiscard]] static SignatureBase of(const http::Response& response,
                                          const Parameters& parameters);

    // signature_params is its entry in Signature-Input (RFC 9421 §4.1). Throws
    // std::invalid_argument where RFC 9421 §2.5 fails.
    SignatureBase(const http::Request& request, InnerList signature_params);
    SignatureBase(const http::Response& response, InnerList signature_params);

    // As its entry in Signature-Input gives them.
    [[nodiscard]] const InnerList& signature_params() const noexcept;
    // Ends with its "@signature-params" line, and no newline.
    [[nodiscard]] const std::string& to_string() const noexcept;

private:
    InnerList m_signature_params;
    std::string m_text;
};

} // namespace identity
