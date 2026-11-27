#include "NodeList.hpp"

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "http/Url.hpp"
#include "logger/Logger.hpp"
#include "net/IpAddress.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"

using http::Url;
using net::IpAddress;
using std::all_of;
using std::invalid_argument;
using std::isalnum;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using storage::Identifier;
using storage::Zlib;
using types::Converter;
using types::Json;

namespace {

using Data = protocol::NodeList::Data;
using Entries = protocol::NodeList::Entries;

// The host a node names itself by when it does not know the address it is
// reached at (HttpApi §10).
constexpr string_view localhost = "localhost";

// Whether host, as Url reads it from an endpoint, is one: an IPv6
// address, which the endpoint names in brackets, or a name or IPv4 address,
// of the characters RFC 3986 §2.3 leaves unreserved.
bool is_host(string_view host) {
    if (host.find(':') != string_view::npos) {
        return IpAddress::parse(host).has_value();
    }

    return !host.empty() && all_of(host.begin(), host.end(), [](char character) {
        return isalnum(static_cast<unsigned char>(character)) != 0
               || Url::unreserved.find(character) != string_view::npos;
    });
}

// Whether url names only a scheme, http or https, a host, and a port if any
// (HttpApi §10.6). Url gives a URL with no path the path "/".
bool is_bare_endpoint(const Url& url) {
    const auto scheme = Converter::lower_case(url.scheme());

    return ("http" == scheme || "https" == scheme) && is_host(url.host())
           && url.username().empty() && url.password().empty() && "/" == url.path()
           && url.query().empty() && url.fragment().empty();
}

// The URL endpoint is, or nothing when it is not an endpoint: an http or
// https URL naming a host, and a port if any, and nothing more. Url reads a
// port it cannot use as no port, and "80a" as 80, so endpoint is one only as
// Url writes it back, which also refuses a path of "/" and an IPv6 address
// not in brackets.
optional<Url> endpoint_url(string_view endpoint) {
    auto url = Url::parse(endpoint);

    if (!url || !is_bare_endpoint(*url)) {
        return nullopt;
    }

    url->set_path("");

    if (url->to_string() != endpoint) {
        return nullopt;
    }

    return url;
}

// Whether host, in a node list received from source, names the sender's
// machine (HttpApi §10.2): localhost always does, and a loopback address
// unless source is one too, when the sender is on this machine, whose
// loopback address it is too.
bool names_sender(string_view host, const optional<IpAddress>& source) {
    if (localhost == Converter::lower_case(host)) {
        return true;
    }

    const auto address = IpAddress::parse(host);

    if (!address || !address->is_loopback()) {
        return false;
    }

    return !source || !source->is_loopback();
}

// endpoint, in a node list received from source, with its host replaced by
// source when it names the sender's machine; nothing when it does and source
// is no IP address.
optional<string> resolved_endpoint(const string& endpoint,
                                   const optional<IpAddress>& source) {
    auto url = endpoint_url(endpoint);

    // Every endpoint a list names is a URL, so the first test only guards
    // the second.
    if (!url || !names_sender(url->host(), source)) {
        return endpoint;
    }

    if (!source) {
        return nullopt;
    }

    url->set_host(source->url_host());
    return url->to_string();
}

// What reading a node list left out, logged once for the list: entries that
// name no endpoint or node id, and node ids under a hash algorithm the node
// does not know, which may mean it needs updating. Only the first of each is
// named, so that a list cannot fill the log.
class LeftOut {
public:
    void add_unusable(string_view endpoint, string_view reason);
    void add_unknown_algorithm(string_view algorithm);
    void log() const;

private:
    size_t m_unusable = 0;
    string m_first_unusable;
    size_t m_unknown = 0;
    string m_first_unknown;
};

void LeftOut::add_unusable(string_view endpoint, string_view reason) {
    if (0 == m_unusable) {
        m_first_unusable = string(endpoint) + ": " + string(reason);
    }

    ++m_unusable;
}

void LeftOut::add_unknown_algorithm(string_view algorithm) {
    if (0 == m_unknown) {
        m_first_unknown = algorithm;
    }

    ++m_unknown;
}

void LeftOut::log() const {
    if (m_unusable > 0) {
        lDebug << "Left out" << m_unusable
               << "entries of a node list that name no endpoint or node id, such as"
               << m_first_unusable;
    }

    if (m_unknown > 0) {
        lWarn << "The node may need updating: a node list named" << m_unknown
              << "node ids under hash algorithms it does not know, such as" << m_first_unknown;
    }
}

// Adds the entry naming node_id at endpoint, as a node list holds it, to
// list, or counts it in left_out when it names no endpoint or node id.
void read_entry(protocol::NodeList& list, const string& endpoint, const Json& node_id,
                LeftOut& left_out) {
    if (!node_id.is_string()) {
        left_out.add_unusable(endpoint, "its node id is not a string");
        return;
    }

    // Exactly "{algorithm}/{hash}", as the Python node reads one.
    const auto& text = node_id.string();
    const auto slash = text.find('/');
    const auto has_one_slash = slash != string::npos
                               && string::npos == text.find('/', slash + 1);

    if (!has_one_slash || 0 == slash) {
        left_out.add_unusable(endpoint, "its node id is not an algorithm and a hash");
        return;
    }

    const auto algorithm = text.substr(0, slash);

    if (!Identifier::is_supported(algorithm)) {
        left_out.add_unknown_algorithm(algorithm);
        return;
    }

    try {
        list.add(endpoint, Identifier(algorithm, text.substr(slash + 1)));
    } catch (const invalid_argument& error) {
        left_out.add_unusable(endpoint, error.what());
    }
}

// The node list json holds. Throws invalid_argument when it is not an
// object holding a "nodes" object.
protocol::NodeList from_json(const Json& json) {
    if (!json.is_object() || !json.has("nodes") || !json.at("nodes").is_object()) {
        throw invalid_argument("A node list must be an object holding a \"nodes\" object");
    }

    protocol::NodeList list;
    LeftOut left_out;

    for (const auto& [endpoint, node_id] : json.at("nodes").object()) {
        read_entry(list, endpoint, node_id, left_out);
    }

    left_out.log();
    return list;
}

// The JSON text holds, or nothing when it holds none.
optional<Json> json_in(string_view text) {
    try {
        return Json::parse(text);
    } catch (const invalid_argument&) {
        // Refused: nothing says text is not JSON.
        return nullopt;
    }
}

// The JSON body holds, as it is or once decompressed. Throws
// invalid_argument when it holds none either way.
Json json_in_body(const Data& body, size_t decompressed_limit) {
    if (auto json = json_in(Converter::text_of(body))) {
        return std::move(*json);
    }

    // Not JSON as it is, so it can only be compressed.
    const auto content = Zlib::decompress(body, decompressed_limit);

    if (!content) {
        throw invalid_argument("A node list must be JSON, or a zlib stream of no more than "
                               + to_string(decompressed_limit) + " bytes of JSON");
    }

    return Json::parse(Converter::text_of(*content));
}

// The JSON of the first count of entries, as compact as it is written:
// {"nodes":{"<endpoint>":"<node id>",...}}.
string json_of(const Entries& entries, size_t count) {
    string text = R"({"nodes":{)";

    for (size_t index = 0; index < count; ++index) {
        const auto& entry = entries.at(index);

        if (index > 0) {
            text += ',';
        }

        text += Json(entry.endpoint).dump();
        text += ':';
        text += Json(entry.node_id.to_string()).dump();
    }

    return text + "}}";
}

// The first count of entries as a node list is sent: their JSON, or when that
// is limit bytes or more, their JSON as a zlib stream; nothing when that is
// too.
optional<Data> encoded(const Entries& entries, size_t count, size_t limit) {
    auto json = Converter::bytes_of(json_of(entries, count));

    if (json.size() < limit) {
        return json;
    }

    auto compressed = Zlib::compress(json, Zlib::max_level);

    if (compressed.size() < limit) {
        return compressed;
    }

    return nullopt;
}

// The longest run of entries, from the first, that is sent in less than limit
// bytes, found by halving the counts between one that fits and one that does
// not. The whole list is known not to fit. Throws invalid_argument when
// not even a list that names no node fits.
Data longest_encoded(const Entries& entries, size_t limit) {
    auto fitting = encoded(entries, 0, limit);
    size_t fits = 0;
    auto too_many = entries.size();

    if (!fitting) {
        throw invalid_argument("A node list cannot be sent in less than "
                               + to_string(limit) + " bytes");
    }

    while (too_many - fits > 1) {
        const auto count = fits + ((too_many - fits) / 2);

        if (auto candidate = encoded(entries, count, limit)) {
            fits = count;
            fitting = std::move(candidate);
        } else {
            too_many = count;
        }
    }

    lDebug << "Left out" << entries.size() - fits << "entries of a node list, which did not fit in"
           << limit << "bytes";
    return std::move(*fitting);
}

} // namespace

namespace protocol {

NodeList NodeList::parse(string_view text) {
    return from_json(Json::parse(text));
}

NodeList NodeList::from_body(const Data& body, size_t decompressed_limit) {
    return from_json(json_in_body(body, decompressed_limit));
}

NodeList::NodeList(const Identifier& own_id,
                   const vector<string>& own_endpoints) {
    for (const auto& endpoint : own_endpoints) {
        add(endpoint, own_id);
    }
}

void NodeList::add(string endpoint, Identifier node_id) {
    if (!endpoint_url(endpoint)) {
        throw invalid_argument("Not an http or https URL naming a host, and a port: "
                               + endpoint);
    }

    if (!m_endpoints.insert(endpoint).second) {
        return;
    }

    m_entries.push_back({std::move(endpoint), std::move(node_id)});
}

const NodeList::Entries& NodeList::entries() const noexcept {
    return m_entries;
}

NodeList NodeList::resolved(string_view source_address) const {
    const auto source = IpAddress::parse(source_address);
    NodeList list;
    size_t left_out = 0;

    for (const auto& entry : m_entries) {
        auto endpoint = resolved_endpoint(entry.endpoint, source);

        if (!endpoint) {
            ++left_out;
            continue;
        }

        list.add(std::move(*endpoint), entry.node_id);
    }

    if (left_out > 0) {
        lDebug << "Left out" << left_out
               << "entries of a node list that name its sender's machine, as it came from no "
                  "IP address to put in their place:"
               << source_address;
    }

    return list;
}

string NodeList::to_string() const {
    return json_of(m_entries, m_entries.size());
}

NodeList::Data NodeList::body(size_t limit) const {
    if (auto whole = encoded(m_entries, m_entries.size(), limit)) {
        return std::move(*whole);
    }

    return longest_encoded(m_entries, limit);
}

} // namespace protocol
