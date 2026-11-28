#include "MessageVerifier.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include "ContentDigest.hpp"
#include "KnownKeys.hpp"
#include "SignatureBase.hpp"
#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/StructuredField.hpp"
#include "storage/Identifier.hpp"
#include "types/Json.hpp"

using http::Headers;
using http::Request;
using http::Response;
using http::StructuredField;
using identity::ContentDigest;
using identity::KnownKeys;
using identity::MessageVerifier;
using identity::SignatureBase;
using std::get_if;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::string;
using std::string_view;
using std::vector;
using storage::Identifier;

namespace {

using Bytes = StructuredField::Bytes;
using InnerList = StructuredField::InnerList;
using Signer = MessageVerifier::Signer;
using Window = MessageVerifier::Window;
// A time, to the millisecond, which holds any time a structured field's
// integer gives in seconds (RFC 9651 §3.3.1), so that one far from now
// cannot overflow.
using Moment = std::chrono::time_point<MessageVerifier::Clock, MessageVerifier::Milliseconds>;

// A parameter a libranet signature may have besides its own (HTTP API
// §11.2), and the one algorithm a node's key signs with, which alg may name,
// though a node does not send it (RFC 9421 §3.3.6).
constexpr string_view alg_parameter = "alg";
constexpr string_view ed25519_algorithm = "ed25519";

// A libranet signature, as Signature-Input and Signature give it.
struct LibranetSignature {
    InnerList signature_params;
    Bytes signature;
};

// What a libranet signature says of itself (HTTP API §11.2).
struct Claims {
    Identifier node_id;
    Moment created;
    optional<Moment> expires;
};

// The member labelled libranet of the dictionary text, which must be its
// only one. Throws std::invalid_argument for text that is not a dictionary,
// or that has another member.
StructuredField::Member libranet_member(string_view text, string_view field_name) {
    const auto field = StructuredField::parse(text);
    const auto& members = field.members();

    if (1 != members.size() || SignatureBase::label != members.front().first) {
        throw invalid_argument(string(field_name) + " must give exactly one signature, labelled "
                               + string(SignatureBase::label));
    }

    return members.front().second;
}

// The covered components and parameters Signature-Input gives (RFC 9421
// §4.1).
InnerList signature_params_of(string_view signature_input) {
    const auto member = libranet_member(signature_input, SignatureBase::signature_input_field);
    const auto* const signature_params = get_if<InnerList>(&member);

    if (nullptr == signature_params) {
        throw invalid_argument("Signature-Input gives a signature's components as an inner list");
    }

    return *signature_params;
}

// The signature Signature gives (RFC 9421 §4.2).
Bytes signature_of(string_view signature) {
    const auto member = libranet_member(signature, SignatureBase::signature_field);
    const auto* const item = get_if<StructuredField::Item>(&member);
    const auto* const bytes = nullptr == item ? nullptr : get_if<Bytes>(&item->value);

    if (nullptr == bytes) {
        throw invalid_argument("Signature gives a signature as a byte sequence");
    }

    return *bytes;
}

// The signature a message's headers give: nothing when they give neither
// Signature-Input nor Signature. Throws std::invalid_argument when they give
// one without the other, or either is not as HTTP API §11.2 says.
optional<LibranetSignature> libranet_signature(const Headers& headers) {
    const auto signature_input = headers.get(SignatureBase::signature_input_field);
    const auto signature = headers.get(SignatureBase::signature_field);

    if (!signature_input && !signature) {
        return nullopt;
    }

    if (!signature_input || !signature) {
        throw invalid_argument("Signature-Input and Signature must be sent together");
    }

    return LibranetSignature{signature_params_of(*signature_input), signature_of(*signature)};
}

// Whether signature_params covers the component named name.
bool covers(const InnerList& signature_params, string_view name) {
    const auto& items = signature_params.items;

    return std::any_of(items.begin(), items.end(), [name](const StructuredField::Item& item) {
        const auto* const covered = get_if<string>(&item.value);

        return nullptr != covered && name == *covered;
    });
}

// The components a signature on message must cover: components, and
// content-digest when message has a body, or a Content-Digest that says it
// has none (HTTP API §11).
template<typename Message>
vector<string_view> must_cover(const Message& message, vector<string_view> components) {
    if (!message.body().empty() || message.headers().has(ContentDigest::field_name)) {
        components.emplace_back(SignatureBase::digest_component);
    }

    return components;
}

// Throws std::invalid_argument unless signature_params covers each of
// components.
void check_covers(const InnerList& signature_params, const vector<string_view>& components) {
    const auto missing = std::find_if(components.begin(), components.end(),
                                      [&signature_params](string_view component) {
        return !covers(signature_params, component);
    });

    if (missing != components.end()) {
        throw invalid_argument("A signature must cover " + string(*missing));
    }
}

// The value of the parameter named name, if signature_params has it.
optional<StructuredField::Value> parameter(const InnerList& signature_params, string_view name) {
    const auto& parameters = signature_params.parameters;
    const auto found = std::find_if(parameters.begin(), parameters.end(),
                                    [name](const auto& parameter) {
        return name == parameter.first;
    });

    if (found == parameters.end()) {
        return nullopt;
    }

    return found->second;
}

// The time the parameter named name gives, in whole seconds since the UNIX
// epoch (RFC 9421 §2.3), if signature_params has it. Throws
// std::invalid_argument for one that is not an integer.
optional<Moment> time_parameter(const InnerList& signature_params, string_view name) {
    const auto value = parameter(signature_params, name);

    if (!value) {
        return nullopt;
    }

    const auto* const seconds = get_if<std::int64_t>(&*value);

    if (nullptr == seconds) {
        throw invalid_argument("A signature gives its " + string(name) + " time as an integer");
    }

    return Moment(std::chrono::seconds(*seconds));
}

// The node id the keyid parameter names (HTTP API §11.2): "{algorithm}/
// {hash}", and nothing more. Throws std::invalid_argument when it names
// none, as when its algorithm is not one the node knows, which is warned of.
Identifier node_id_of(const InnerList& signature_params) {
    const auto keyid = parameter(signature_params, SignatureBase::keyid_parameter);
    const auto* const text = keyid ? get_if<string>(&*keyid) : nullptr;

    if (nullptr == text) {
        throw invalid_argument("A signature names its signer with a keyid string");
    }

    auto node_id = Identifier::from_json(types::Json(*text));

    if (!node_id) {
        throw invalid_argument("A signature's keyid is not a node id: " + *text);
    }

    return std::move(*node_id);
}

// Throws std::invalid_argument for an alg parameter that names an algorithm
// other than the one a node's key signs with (RFC 9421 §3.2).
void check_algorithm(const InnerList& signature_params) {
    const auto algorithm = parameter(signature_params, alg_parameter);
    const auto* const name = algorithm ? get_if<string>(&*algorithm) : nullptr;

    if (algorithm && (nullptr == name || ed25519_algorithm != *name)) {
        throw invalid_argument("A node signs with " + string(ed25519_algorithm) + " alone");
    }
}

// What signature_params says of the signature. Throws std::invalid_argument
// for a keyid that is not a node id, a created time that is missing, a time
// that is not an integer, or an alg other than ed25519.
Claims claims_of(const InnerList& signature_params) {
    const auto created = time_parameter(signature_params, SignatureBase::created_parameter);

    if (!created) {
        throw invalid_argument("A signature must say when it was created");
    }

    auto node_id = node_id_of(signature_params);
    const auto expires = time_parameter(signature_params, SignatureBase::expires_parameter);

    check_algorithm(signature_params);
    return Claims{std::move(node_id), *created, expires};
}

// Throws std::invalid_argument unless claims' times are within window of
// now (HandshakeProtocol §2). Each time is taken from now before it is
// compared, which a time far from now cannot overflow.
void check_fresh(const Claims& claims, Moment now, const Window& window) {
    const auto age = now - claims.created;

    if (age < -window.clock_skew) {
        throw invalid_argument("A signature was created ahead of the node's clock");
    }

    if (age > window.max_age + window.clock_skew) {
        throw invalid_argument("A signature is too old");
    }

    if (claims.expires && now - *claims.expires > window.clock_skew) {
        throw invalid_argument("A signature has expired");
    }
}

// Throws std::invalid_argument when signature_params covers content-digest,
// and headers give no Content-Digest, or one that is not body's (HTTP API
// §11.2).
void check_digest(const InnerList& signature_params, const Headers& headers, string_view body) {
    if (!covers(signature_params, SignatureBase::digest_component)) {
        return;
    }

    const auto digest = headers.get(ContentDigest::field_name);

    if (!digest) {
        throw invalid_argument("A signature covers a Content-Digest the message does not have");
    }

    if (!ContentDigest::parse(*digest).matches(body)) {
        throw invalid_argument("A Content-Digest is not that of the body");
    }
}

// The node claims names, and whether its key, if keys holds it, made
// signature of base. Throws std::invalid_argument when the key did not make
// it, or when what the store holds as the key is not one.
Signer signed_by(const Claims& claims, const SignatureBase& base, const Bytes& signature,
                 KnownKeys& keys) {
    const auto key = keys.key(claims.node_id);

    if (!key) {
        return Signer{claims.node_id, false};
    }

    if (!(*key)->verify(base.to_string(), signature)) {
        throw invalid_argument("A signature was not made by " + claims.node_id.to_string());
    }

    return Signer{claims.node_id, true};
}

// The node that signed message, with a signature that must cover components,
// and content-digest when it has a body; nothing when it is not signed.
// Everything the key is not needed for is checked before the key is looked
// up, so that a signer whose key is not held yet has sent a signature that
// only the key could find fault with.
template<typename Message>
optional<Signer> signer_of(const Message& message, vector<string_view> components,
                           KnownKeys& keys, const Window& window, Moment now) {
    const auto signature = libranet_signature(message.headers());

    if (!signature) {
        return nullopt;
    }

    check_covers(signature->signature_params, must_cover(message, std::move(components)));

    const auto claims = claims_of(signature->signature_params);

    check_fresh(claims, now, window);
    check_digest(signature->signature_params, message.headers(), message.body());

    const SignatureBase base(message, signature->signature_params);

    return signed_by(claims, base, signature->signature, keys);
}

} // namespace

namespace identity {

MessageVerifier::MessageVerifier(std::shared_ptr<KnownKeys> keys, Window window,
                                 ClockFunction clock)
    : m_keys(std::move(keys))
    , m_window(window)
    , m_clock(std::move(clock)) {
    if (!m_keys) {
        throw invalid_argument("Signatures are checked with the keys of other nodes, "
                               "and none were given");
    }

    if (m_window.max_age <= Milliseconds::zero()) {
        throw invalid_argument("identity.signature_max_age_seconds must be more than 0");
    }

    if (m_window.clock_skew < Milliseconds::zero()) {
        throw invalid_argument("identity.signature_clock_skew_seconds cannot be negative");
    }

    if (!m_clock) {
        throw invalid_argument("MessageVerifier needs a clock");
    }
}

optional<MessageVerifier::Signer> MessageVerifier::verify(const Request& request) const {
    return signer_of(request, {SignatureBase::method_component, SignatureBase::path_component},
                     *m_keys, m_window, std::chrono::time_point_cast<Milliseconds>(m_clock()));
}

optional<MessageVerifier::Signer> MessageVerifier::verify(const Response& response) const {
    return signer_of(response, {SignatureBase::status_component}, *m_keys, m_window,
                     std::chrono::time_point_cast<Milliseconds>(m_clock()));
}

} // namespace identity
