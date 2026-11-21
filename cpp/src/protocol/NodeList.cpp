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
#include "types/Json.hpp"

namespace {

using Data = protocol::NodeList::Data;
using Entries = protocol::NodeList::Entries;

// The host a node names itself by when it does not know the address it is
// reached at (HttpApi §10).
constexpr std::string_view localhost = "localhost";

// text, with its letters in lower case.
std::string lower_case(std::string_view text) {
    std::string lower(text);

    std::transform(lower.begin(), lower.end(), lower.begin(), [](char character) {
        return static_cast<char>(std::tolower(static_cast<unsigned char>(character)));
    });
    return lower;
}

// The bytes of text, a byte for each char, and back.
Data bytes_of(std::string_view text) {
    Data bytes(text.size());

    std::transform(text.begin(), text.end(), bytes.begin(),
                   [](char character) { return static_cast<std::byte>(character); });
    return bytes;
}

std::string text_of(const Data& bytes) {
    std::string text(bytes.size(), '\0');

    std::transform(bytes.begin(), bytes.end(), text.begin(),
                   [](std::byte byte) { return static_cast<char>(byte); });
    return text;
}

// Whether host, as http::Url reads it from an endpoint, is one: an IPv6
// address, which the endpoint names in brackets, or a name or IPv4 address,
// of the characters RFC 3986 §2.3 leaves unreserved.
bool is_host(std::string_view host) {
    constexpr std::string_view unreserved = "-._~";

    if (host.find(':') != std::string_view::npos) {
        return net::IpAddress::parse(host).has_value();
    }

    return !host.empty() && std::all_of(host.begin(), host.end(), [unreserved](char character) {
        return std::isalnum(static_cast<unsigned char>(character)) != 0
               || unreserved.find(character) != std::string_view::npos;
    });
}

// Whether url names only a scheme, http or https, a host, and a port if any
// (HttpApi §10.6). Url gives a URL with no path the path "/".
bool is_bare_endpoint(const http::Url& url) {
    const auto scheme = lower_case(url.scheme());

    return ("http" == scheme || "https" == scheme) && is_host(url.host())
           && url.username().empty() && url.password().empty() && "/" == url.path()
           && url.query().empty() && url.fragment().empty();
}

// The URL endpoint is, or nothing when it is not an endpoint: an http or
// https URL naming a host, and a port if any, and nothing more. Url reads a
// port it cannot use as no port, and "80a" as 80, so endpoint is one only as
// Url writes it back, which also refuses a path of "/" and an IPv6 address
// not in brackets.
std::optional<http::Url> endpoint_url(std::string_view endpoint) {
    auto url = http::Url::parse(endpoint);

    if (!url || !is_bare_endpoint(*url)) {
        return std::nullopt;
    }

    url->set_path("");

    if (url->to_string() != endpoint) {
        return std::nullopt;
    }

    return url;
}

// Whether host, in a node list received from source, names the sender's
// machine (HttpApi §10.2): localhost always does, and a loopback address
// unless source is one too, when the sender is on this machine, whose
// loopback address it is too.
bool names_sender(std::string_view host, const std::optional<net::IpAddress>& source) {
    if (localhost == lower_case(host)) {
        return true;
    }

    const auto address = net::IpAddress::parse(host);

    if (!address || !address->is_loopback()) {
        return false;
    }

    return !source || !source->is_loopback();
}

// endpoint, in a node list received from source, with its host replaced by
// source when it names the sender's machine; nothing when it does and source
// is no IP address.
std::optional<std::string> resolved_endpoint(const std::string& endpoint,
                                             const std::optional<net::IpAddress>& source) {
    auto url = endpoint_url(endpoint);

    // Every endpoint a list names is a URL, so the first test only guards
    // the second.
    if (!url || !names_sender(url->host(), source)) {
        return endpoint;
    }

    if (!source) {
        return std::nullopt;
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
    void add_unusable(std::string_view endpoint, std::string_view reason);
    void add_unknown_algorithm(std::string_view algorithm);
    void log() const;

private:
    std::size_t m_unusable = 0;
    std::string m_first_unusable;
    std::size_t m_unknown = 0;
    std::string m_first_unknown;
};

void LeftOut::add_unusable(std::string_view endpoint, std::string_view reason) {
    if (0 == m_unusable) {
        m_first_unusable = std::string(endpoint) + ": " + std::string(reason);
    }

    ++m_unusable;
}

void LeftOut::add_unknown_algorithm(std::string_view algorithm) {
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
void read_entry(protocol::NodeList& list, const std::string& endpoint, const types::Json& node_id,
                LeftOut& left_out) {
    if (!node_id.is_string()) {
        left_out.add_unusable(endpoint, "its node id is not a string");
        return;
    }

    // Exactly "{algorithm}/{hash}", as the Python node reads one.
    const auto& text = node_id.string();
    const auto slash = text.find('/');
    const auto has_one_slash = slash != std::string::npos
                               && std::string::npos == text.find('/', slash + 1);

    if (!has_one_slash || 0 == slash) {
        left_out.add_unusable(endpoint, "its node id is not an algorithm and a hash");
        return;
    }

    const auto algorithm = text.substr(0, slash);

    if (!storage::Identifier::is_supported(algorithm)) {
        left_out.add_unknown_algorithm(algorithm);
        return;
    }

    try {
        list.add(endpoint, storage::Identifier(algorithm, text.substr(slash + 1)));
    } catch (const std::invalid_argument& error) {
        left_out.add_unusable(endpoint, error.what());
    }
}

// The node list json holds. Throws std::invalid_argument when it is not an
// object holding a "nodes" object.
protocol::NodeList from_json(const types::Json& json) {
    if (!json.is_object() || !json.has("nodes") || !json.at("nodes").is_object()) {
        throw std::invalid_argument("A node list must be an object holding a \"nodes\" object");
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
std::optional<types::Json> json_in(std::string_view text) {
    try {
        return types::Json::parse(text);
    } catch (const std::invalid_argument&) {
        // Refused: nothing says text is not JSON.
        return std::nullopt;
    }
}

// The JSON body holds, as it is or once decompressed. Throws
// std::invalid_argument when it holds none either way.
types::Json json_in_body(const Data& body, std::size_t decompressed_limit) {
    if (auto json = json_in(text_of(body))) {
        return std::move(*json);
    }

    // Not JSON as it is, so it can only be compressed.
    const auto content = storage::Zlib::decompress(body, decompressed_limit);

    if (!content) {
        throw std::invalid_argument("A node list must be JSON, or a zlib stream of no more than "
                                    + std::to_string(decompressed_limit) + " bytes of JSON");
    }

    return types::Json::parse(text_of(*content));
}

// The JSON of the first count of entries, as compact as it is written:
// {"nodes":{"<endpoint>":"<node id>",...}}.
std::string json_of(const Entries& entries, std::size_t count) {
    std::string text = R"({"nodes":{)";

    for (std::size_t index = 0; index < count; ++index) {
        const auto& entry = entries.at(index);

        if (index > 0) {
            text += ',';
        }

        text += types::Json(entry.endpoint).dump();
        text += ':';
        text += types::Json(entry.node_id.to_string()).dump();
    }

    return text + "}}";
}

// The first count of entries as a node list is sent: their JSON, or when that
// is limit bytes or more, their JSON as a zlib stream; nothing when that is
// too.
std::optional<Data> encoded(const Entries& entries, std::size_t count, std::size_t limit) {
    auto json = bytes_of(json_of(entries, count));

    if (json.size() < limit) {
        return json;
    }

    auto compressed = storage::Zlib::compress(json, storage::Zlib::max_level);

    if (compressed.size() < limit) {
        return compressed;
    }

    return std::nullopt;
}

// The longest run of entries, from the first, that is sent in less than limit
// bytes, found by halving the counts between one that fits and one that does
// not. The whole list is known not to fit. Throws std::invalid_argument when
// not even a list that names no node fits.
Data longest_encoded(const Entries& entries, std::size_t limit) {
    auto fitting = encoded(entries, 0, limit);
    std::size_t fits = 0;
    auto too_many = entries.size();

    if (!fitting) {
        throw std::invalid_argument("A node list cannot be sent in less than "
                                    + std::to_string(limit) + " bytes");
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

NodeList NodeList::parse(std::string_view text) {
    return from_json(types::Json::parse(text));
}

NodeList NodeList::from_body(const Data& body, std::size_t decompressed_limit) {
    return from_json(json_in_body(body, decompressed_limit));
}

NodeList::NodeList(const storage::Identifier& own_id,
                   const std::vector<std::string>& own_endpoints) {
    for (const auto& endpoint : own_endpoints) {
        add(endpoint, own_id);
    }
}

void NodeList::add(std::string endpoint, storage::Identifier node_id) {
    if (!endpoint_url(endpoint)) {
        throw std::invalid_argument("Not an http or https URL naming a host, and a port: "
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

NodeList NodeList::resolved(std::string_view source_address) const {
    const auto source = net::IpAddress::parse(source_address);
    NodeList list;
    std::size_t left_out = 0;

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

std::string NodeList::to_string() const {
    return json_of(m_entries, m_entries.size());
}

NodeList::Data NodeList::body(std::size_t limit) const {
    if (auto whole = encoded(m_entries, m_entries.size(), limit)) {
        return std::move(*whole);
    }

    return longest_encoded(m_entries, limit);
}

} // namespace protocol
