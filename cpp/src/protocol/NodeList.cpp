#include "NodeList.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

#include "logger/Logger.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Json.hpp"

namespace {

using protocol::NodeList;
using Data = NodeList::Data;
using Entries = NodeList::Entries;

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

// An IP address, as inet_pton() reads it and inet_ntop() writes it: IPv4 in
// its first 4 bytes, and IPv6 in all 16.
class IpAddress {
public:
    // The address text names, with an IPv4-mapped IPv6 address read as the
    // IPv4 address it holds; nothing when text names no IP address.
    [[nodiscard]] static std::optional<IpAddress> parse(std::string_view text);

    // In 127.0.0.0/8, or ::1.
    [[nodiscard]] bool is_loopback() const;
    // As a URL names it as its host, IPv6 in brackets (RFC 3986 §3.2.2).
    [[nodiscard]] std::string url_host() const;

private:
    // As many bytes as an IPv6 address has.
    using Bytes = std::array<std::uint8_t, sizeof(in6_addr)>;

    // ::ffff:0:0/96, the IPv4-mapped addresses (RFC 4291 §2.5.5.2): their
    // first 12 bytes are these, and their last 4 the IPv4 address.
    static constexpr Bytes mapped_prefix{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0xFF, 0, 0, 0, 0};
    static constexpr std::ptrdiff_t mapped_prefix_size = 12;
    // ::1 (RFC 4291 §2.5.3).
    static constexpr Bytes ipv6_loopback{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1};
    // 127.0.0.0/8 (RFC 1122 §3.2.1.3), by its first byte.
    static constexpr std::uint8_t ipv4_loopback = 127;

    int m_family;
    Bytes m_bytes;

    IpAddress(int family, const Bytes& bytes);

    // This address, or the IPv4 address it holds when it is IPv4-mapped.
    [[nodiscard]] IpAddress _unmapped() const;
};

std::optional<IpAddress> IpAddress::parse(std::string_view text) {
    // inet_pton() reads text up to a NUL, which would let what follows one
    // through unread.
    if (text.find('\0') != std::string_view::npos) {
        return std::nullopt;
    }

    const std::string terminated(text);
    Bytes bytes{};

    if (1 == ::inet_pton(AF_INET, terminated.c_str(), bytes.data())) {
        return IpAddress(AF_INET, bytes);
    }

    if (1 == ::inet_pton(AF_INET6, terminated.c_str(), bytes.data())) {
        return IpAddress(AF_INET6, bytes)._unmapped();
    }

    return std::nullopt;
}

IpAddress::IpAddress(int family, const Bytes& bytes)
    : m_family(family)
    , m_bytes(bytes) {}

bool IpAddress::is_loopback() const {
    if (AF_INET == m_family) {
        return ipv4_loopback == m_bytes.at(0);
    }

    return ipv6_loopback == m_bytes;
}

std::string IpAddress::url_host() const {
    std::array<char, INET6_ADDRSTRLEN> text{};

    if (nullptr
        == ::inet_ntop(m_family, m_bytes.data(), text.data(),
                       static_cast<socklen_t>(text.size()))) {
        throw std::system_error(errno, std::generic_category(), "inet_ntop");
    }

    if (AF_INET6 == m_family) {
        return "[" + std::string(text.data()) + "]";
    }

    return {text.data()};
}

IpAddress IpAddress::_unmapped() const {
    if (!std::equal(mapped_prefix.begin(), std::next(mapped_prefix.begin(), mapped_prefix_size),
                    m_bytes.begin())) {
        return *this;
    }

    Bytes ipv4{};

    std::copy(std::next(m_bytes.begin(), mapped_prefix_size), m_bytes.end(), ipv4.begin());
    return {AF_INET, ipv4};
}

// The parts of an endpoint (HttpApi §10.6): its scheme, in lower case; its
// host, without the brackets around an IPv6 address; and its port, as
// written, or empty when it names none.
struct EndpointParts {
    std::string scheme;
    std::string_view host;
    std::string_view port;
};

// Whether text is a host as an endpoint names one: an IPv6 address in
// brackets, or a name or IPv4 address, of the characters RFC 3986 §2.3 leaves
// unreserved.
bool is_host(std::string_view text) {
    constexpr std::string_view unreserved = "-._~";

    if (text.size() >= 2 && '[' == text.front() && ']' == text.back()) {
        const auto address = text.substr(1, text.size() - 2);

        // With a colon, as an IPv4 address has none.
        return address.find(':') != std::string_view::npos
               && IpAddress::parse(address).has_value();
    }

    return !text.empty() && std::all_of(text.begin(), text.end(), [unreserved](char character) {
        return std::isalnum(static_cast<unsigned char>(character)) != 0
               || unreserved.find(character) != std::string_view::npos;
    });
}

// Whether text is a port: decimal digits, no more than 65535.
bool is_port(std::string_view text) {
    constexpr std::string_view highest = "65535";
    const auto is_digit = [](char character) { return character >= '0' && character <= '9'; };

    if (text.empty() || text.size() > highest.size()
        || !std::all_of(text.begin(), text.end(), is_digit)) {
        return false;
    }

    // As many digits as the highest, compared as text, as numbers compare.
    return text.size() < highest.size() || text <= highest;
}

// The parts of endpoint, or nothing when it is not an http or https URL
// naming a host, and a port if any, and nothing more: no user, path, query,
// or fragment.
std::optional<EndpointParts> split_endpoint(std::string_view endpoint) {
    constexpr std::string_view separator = "://";
    const auto scheme_end = endpoint.find(separator);

    if (std::string_view::npos == scheme_end) {
        return std::nullopt;
    }

    EndpointParts parts{lower_case(endpoint.substr(0, scheme_end)), {}, {}};
    const auto authority = endpoint.substr(scheme_end + separator.size());
    // The colon before the port follows an IPv6 address's closing bracket.
    const auto bracket = authority.rfind(']');
    const auto port_colon = authority.find(':', std::string_view::npos == bracket ? 0 : bracket);
    const auto has_port = port_colon != std::string_view::npos;

    parts.host = authority.substr(0, port_colon);
    parts.port = has_port ? authority.substr(port_colon + 1) : std::string_view();

    if (("http" != parts.scheme && "https" != parts.scheme) || !is_host(parts.host)
        || (has_port && !is_port(parts.port))) {
        return std::nullopt;
    }

    if ('[' == parts.host.front()) {
        parts.host = parts.host.substr(1, parts.host.size() - 2);
    }

    return parts;
}

// Whether host, in a node list received from source, names the sender's
// machine (HttpApi §10.2): localhost always does, and a loopback address
// unless source is one too, when the sender is on this machine, whose
// loopback address it is too.
bool names_sender(std::string_view host, const std::optional<IpAddress>& source) {
    if (localhost == lower_case(host)) {
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
std::optional<std::string> resolved_endpoint(const std::string& endpoint,
                                             const std::optional<IpAddress>& source) {
    const auto parts = split_endpoint(endpoint);

    // Every endpoint a list names has parts, so the first test only guards
    // the second.
    if (!parts || !names_sender(parts->host, source)) {
        return endpoint;
    }

    if (!source) {
        return std::nullopt;
    }

    auto resolved = parts->scheme + "://" + source->url_host();

    if (!parts->port.empty()) {
        resolved += ':';
        resolved += parts->port;
    }

    return resolved;
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
void read_entry(NodeList& list, const std::string& endpoint, const types::Json& node_id,
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
NodeList from_json(const types::Json& json) {
    if (!json.is_object() || !json.has("nodes") || !json.at("nodes").is_object()) {
        throw std::invalid_argument("A node list must be an object holding a \"nodes\" object");
    }

    NodeList list;
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
    if (!split_endpoint(endpoint)) {
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
    const auto source = IpAddress::parse(source_address);
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
