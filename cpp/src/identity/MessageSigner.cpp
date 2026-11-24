#include "MessageSigner.hpp"

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/StructuredField.hpp"
#include "identity/ContentDigest.hpp"
#include "identity/NodeIdentity.hpp"
#include "identity/SignatureBase.hpp"

using http::Request;
using http::Response;
using http::StructuredField;
using identity::ContentDigest;
using identity::NodeIdentity;
using identity::SignatureBase;
using std::invalid_argument;
using std::nullopt;
using std::string;
using std::string_view;

namespace {

// Adds to message the Content-Digest of its body, when it has one, and the
// signature identity makes of it at created. The digest goes first, as the
// signature covers it.
template<typename Message>
void sign_as(const NodeIdentity& identity, SignatureBase::Time created, Message& message) {
    if (!message.body().empty()) {
        message.headers().set(string(ContentDigest::field_name), ContentDigest::of(message.body()).to_string());
    }

    const auto base = SignatureBase::of(message, {created, identity.id().to_string(), nullopt});
    StructuredField signature_input;
    StructuredField signature;

    signature_input.set(string(SignatureBase::label), base.signature_params());
    signature.set(string(SignatureBase::label), StructuredField::Item{identity.sign(base.to_string()), {}});
    message.headers().set(string(SignatureBase::signature_input_field), signature_input.to_string());
    message.headers().set(string(SignatureBase::signature_field), signature.to_string());
}

} // namespace

namespace identity {

MessageSigner::MessageSigner(const NodeIdentity& identity, ClockFunction clock)
    : m_identity(&identity)
    , m_clock(std::move(clock)) {
    if (!m_clock) {
        throw invalid_argument("MessageSigner needs a clock");
    }
}

void MessageSigner::sign(Request& request) const {
    sign_as(*m_identity, m_clock(), request);
}

void MessageSigner::sign(Response& response) const {
    sign_as(*m_identity, m_clock(), response);
}

} // namespace identity
