#pragma once

#include <chrono>
#include <optional>
#include <string>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/StructuredField.hpp"

namespace identity {

// The signature base of RFC 9421 §2.5: the text a signature on a message
// signs, a line for each component it covers, in order, then a line of its
// parameters. A request's components may be @method, @path, and
// @authority, and an answer's @status, besides the header fields of
// either, named in lower case. A component with parameters of its own, such
// as a field read as a structured field or a component of the request an
// answer is to, is not taken.
class SignatureBase {
public:
    using Time = std::chrono::system_clock::time_point;
    using InnerList = http::StructuredField::InnerList;

    // What a libranet signature says of itself (HTTP API §11.2): when it was
    // made, the signer's node id, and when it expires, if it does. They are
    // written in this order, as the Python node writes them, each time in
    // whole seconds since the UNIX epoch (RFC 9421 §2.3).
    struct Parameters {
        Time created;
        std::string keyid;
        std::optional<Time> expires;
    };

    // The base of the signature a node puts on a request (HTTP API §11): it
    // covers @method and @path, and content-digest when the request has a
    // body. Throws std::invalid_argument as the constructor does.
    [[nodiscard]] static SignatureBase of(const http::Request& request,
                                          const Parameters& parameters);
    // The base of the signature a node puts on an answer: it covers @status,
    // and content-digest when the answer has a body.
    [[nodiscard]] static SignatureBase of(const http::Response& response,
                                          const Parameters& parameters);

    // The base of the signature that signature_params describes, as its
    // entry in Signature-Input gives it (RFC 9421 §4.1). Throws
    // std::invalid_argument where RFC 9421 §2.5 fails: for a component
    // named twice, one this does not take, a field the message does not
    // have, or a value that is not printable ASCII.
    SignatureBase(const http::Request& request, InnerList signature_params);
    SignatureBase(const http::Response& response, InnerList signature_params);

    // The components covered, and the signature's parameters, as its entry
    // in Signature-Input gives them.
    [[nodiscard]] const InnerList& signature_params() const noexcept;
    // The text signed, which ends with its "@signature-params" line, and no
    // newline.
    [[nodiscard]] const std::string& to_string() const noexcept;

private:
    InnerList m_signature_params;
    std::string m_text;
};

} // namespace identity
