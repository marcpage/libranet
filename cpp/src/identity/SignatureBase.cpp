#include "SignatureBase.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/StructuredField.hpp"
#include "types/Converter.hpp"

using http::Headers;
using http::Request;
using http::Response;
using http::StructuredField;
using identity::SignatureBase;
using std::all_of;
using std::find;
using std::get_if;
using std::int64_t;
using std::invalid_argument;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using std::chrono::duration_cast;
using std::chrono::seconds;
using types::Converter;

namespace {

using InnerList = SignatureBase::InnerList;

// A component RFC 9421's own examples cover (§2.2), besides those a node's
// signature covers.
constexpr string_view authority_component = "@authority";

// A field's component name is its name, a token (RFC 9110 §5.6.2), in lower
// case (RFC 9421 §2.1).
constexpr string_view field_name_characters = "abcdefghijklmnopqrstuvwxyz0123456789!#$%&'*+-.^_`|~";

// A time as RFC 9421 §2.3 writes one: whole seconds since the UNIX epoch.
StructuredField::Value timestamp(SignatureBase::Time time) {
    return static_cast<int64_t>(duration_cast<seconds>(time.time_since_epoch()).count());
}

// The covered components and parameters of a libranet signature that covers
// components and says parameters of itself.
InnerList libranet_params(const vector<string_view>& components,
                          const SignatureBase::Parameters& parameters) {
    InnerList signature_params;

    for (const auto component : components) {
        signature_params.items.push_back(StructuredField::Item{string(component), {}});
    }

    signature_params.parameters.emplace_back(SignatureBase::created_parameter,
                                             timestamp(parameters.created));
    signature_params.parameters.emplace_back(SignatureBase::keyid_parameter, parameters.keyid);

    if (parameters.expires) {
        signature_params.parameters.emplace_back(SignatureBase::expires_parameter,
                                                 timestamp(*parameters.expires));
    }

    return signature_params;
}

// signature_params as RFC 9651 §4.1.1.1 writes an inner list: as a
// dictionary writes its member, less the member's key and "=". Throws
// std::invalid_argument for a list a structured field cannot hold.
string serialized(const InnerList& signature_params) {
    StructuredField field;

    field.set(string(SignatureBase::label), signature_params);
    return field.to_string().substr(SignatureBase::label.size() + 1);
}

// The name of the component item covers: a string, with no parameters of
// its own (RFC 9421 §2).
string_view component_name(const StructuredField::Item& item) {
    const auto* const name = get_if<string>(&item.value);

    if (nullptr == name) {
        throw invalid_argument("A signature names each component it covers with a string");
    }

    if (!item.parameters.empty()) {
        throw invalid_argument("A component with parameters is not taken: " + *name);
    }

    return *name;
}

// RFC 9421 §2.1: the value of the field named name, less the whitespace
// around it. Throws std::invalid_argument for a name that is not a field's
// in lower case, or a field the message does not have.
string field_value(const Headers& headers, string_view name) {
    if (name.empty() || string_view::npos != name.find_first_not_of(field_name_characters)) {
        throw invalid_argument("Not a component a signature can cover: " + string(name));
    }

    const auto value = headers.get(name);

    if (!value) {
        throw invalid_argument("A signature covers a field the message does not have: "
                               + string(name));
    }

    return string(Headers::trim_whitespace(*value));
}

// RFC 9421 §2.2.6: the path of a target in origin form, as sent, without
// its query.
string path_of(string_view target) {
    if (target.empty() || '/' != target.front()) {
        throw invalid_argument("A request's target has no path: " + string(target));
    }

    return string(target.substr(0, target.find('?')));
}

// RFC 9421 §2.2.3: the host the request is for, in lower case. A port is
// kept as sent, as whether it is the default depends on a scheme the request
// does not name.
string authority_of(const Headers& headers) {
    return Converter::lower_case(field_value(headers, "host"));
}

// RFC 9421 §2.2.9: the status code's three digits.
string status_of(const Response& response) {
    constexpr int least_status = 100;
    constexpr int most_status = 999;
    const auto status = response.status_code();

    if (status < least_status || status > most_status) {
        throw invalid_argument("A status code has three digits: " + to_string(status));
    }

    return to_string(status);
}

// The value of a request's component named name (RFC 9421 §2.1, §2.2).
string value_of(const Request& request, string_view name) {
    if (SignatureBase::method_component == name) {
        return string(request.method());
    }

    if (SignatureBase::path_component == name) {
        return path_of(request.uri());
    }

    if (authority_component == name) {
        return authority_of(request.headers());
    }

    return field_value(request.headers(), name);
}

// The value of an answer's component named name (RFC 9421 §2.1, §2.2).
string value_of(const Response& response, string_view name) {
    if (SignatureBase::status_component == name) {
        return status_of(response);
    }

    return field_value(response.headers(), name);
}

// Whether value can be on a line of a signature base: printable ASCII, and
// the tabs a field may hold, with no newline (RFC 9421 §2.5).
bool is_printable(string_view value) {
    return all_of(value.begin(), value.end(), [](char character) {
        return '\t' == character || (' ' <= character && character <= '~');
    });
}

// RFC 9421 §2.5: a line for each component signature_params covers, with
// its value in message, then the line of signature_params.
template<typename Message>
string base_of(const Message& message, const InnerList& signature_params) {
    const auto params_value = serialized(signature_params);
    vector<string_view> covered;
    string text;

    for (const auto& item : signature_params.items) {
        const auto name = component_name(item);

        if (find(covered.begin(), covered.end(), name) != covered.end()) {
            throw invalid_argument("A signature covers a component twice: " + string(name));
        }

        const auto value = value_of(message, name);

        if (!is_printable(value)) {
            throw invalid_argument("A signature covers a value that is not printable ASCII: "
                                   + string(name));
        }

        covered.push_back(name);
        text += '"' + string(name) + "\": " + value + '\n';
    }

    return text + "\"@signature-params\": " + params_value;
}

} // namespace

namespace identity {

SignatureBase SignatureBase::of(const Request& request, const Parameters& parameters) {
    vector<string_view> components{method_component, path_component};

    if (!request.body().empty()) {
        components.push_back(digest_component);
    }

    return SignatureBase(request, libranet_params(components, parameters));
}

SignatureBase SignatureBase::of(const Response& response, const Parameters& parameters) {
    vector<string_view> components{status_component};

    if (!response.body().empty()) {
        components.push_back(digest_component);
    }

    return SignatureBase(response, libranet_params(components, parameters));
}

SignatureBase::SignatureBase(const Request& request, InnerList signature_params)
    : m_signature_params(std::move(signature_params))
    , m_text(base_of(request, m_signature_params)) {}

SignatureBase::SignatureBase(const Response& response, InnerList signature_params)
    : m_signature_params(std::move(signature_params))
    , m_text(base_of(response, m_signature_params)) {}

const InnerList& SignatureBase::signature_params() const noexcept {
    return m_signature_params;
}

const string& SignatureBase::to_string() const noexcept {
    return m_text;
}

} // namespace identity
