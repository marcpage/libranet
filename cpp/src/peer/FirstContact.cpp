#include "FirstContact.hpp"

#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "PeerConnection.hpp"
#include "PeerSession.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "net/Address.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "stats/NodeAddresses.hpp"
#include "stats/Recorder.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::Response;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using msg::Channel;
using net::Address;
using peer::FirstContact;
using peer::PeerConnection;
using peer::PeerSession;
using protocol::NodeList;
using protocol::SeekList;
using stats::Recorder;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::set;
using std::string;
using std::string_view;
using std::unique_ptr;
using storage::DataStored;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using Source = stats::NodeAddresses::Source;
using Status = http::ResponseCode::Status;
using Unauthenticated = PeerSession::Unauthenticated;

namespace {

// Where a node's node list and seek list are (HttpApi §10.5, §10.7).
constexpr string_view nodes_path = "/data/nodes";
constexpr string_view seek_path = "/data/seek";

// The types of a key put as content, and of a list posted.
constexpr string_view octet_stream = "application/octet-stream";
constexpr string_view json = "application/json";

// A request of method for target, with body, of content_type when it has one.
Request request_of(string method, string target, string body = "",
                   string_view content_type = "") {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(target));

    if (!content_type.empty()) {
        request.headers().set("Content-Type", string(content_type));
    }

    request.set_body(std::move(body));
    return request;
}

// Where the content identifier names is (HttpApi §5).
string data_path(const Identifier& identifier) {
    return "/data/" + identifier.to_string();
}

// The request's method and target, as a message names the request.
string request_line(const Request& request) {
    return string(request.method()) + " " + string(request.uri());
}

// Why the answer to asked, from address, does not show which node it is
// from.
Unauthenticated unauthenticated(string_view asked, const Address& address, string_view why) {
    return Unauthenticated("The answer to " + string(asked) + " from " + address.to_string()
                           + " does not show which node it is from: " + string(why));
}

// The answer to request, signed with signer, sent on connection. Throws what
// PeerConnection::send() and its answer throw.
Response answer_to(PeerConnection& connection, const MessageSigner& signer, Request request) {
    signer.sign(request);
    return connection.send(std::move(request)).get();
}

// The node that signed answer, the answer to asked from address, as verifier
// finds. Throws Unauthenticated for an answer that is not signed, or whose
// signature fails.
MessageVerifier::Signer signer_of(const Response& answer, string_view asked,
                                  const Address& address, const MessageVerifier& verifier) {
    optional<MessageVerifier::Signer> signer;

    try {
        signer = verifier.verify(answer);
    } catch (const invalid_argument& error) {
        throw unauthenticated(asked, address, error.what());
    }

    if (!signer) {
        throw unauthenticated(asked, address, "It is not signed");
    }

    return *signer;
}

// The body of answer, the list asked asks for, from address: nothing when
// answer is not 200 OK, and so carries no list.
optional<Converter::Bytes> list_body(const Response& answer, string_view asked,
                                     const Address& address) {
    if (static_cast<int>(Status::Ok) != answer.status_code()) {
        lDebug << address.to_string() << "sent no list for" << asked << "but answered"
               << answer.status_code();
        return nullopt;
    }

    return Converter::bytes_of(answer.body());
}

// The seek list answer carries, from address: none when it carries none that
// can be read.
SeekList seek_list_in(const Response& answer, const Address& address) {
    const string asked = "GET " + string(seek_path);
    const auto body = list_body(answer, asked, address);

    if (!body) {
        return {};
    }

    try {
        return SeekList::read(*body, NodeList::max_decompressed_size);
    } catch (const invalid_argument& error) {
        lDebug << "Ignoring the seek list from" << address.to_string() + ":" << error.what();
        return {};
    }
}

// The node list answer carries, from address: nothing when it carries none
// that can be read.
optional<NodeList> node_list_in(const Response& answer, const Address& address) {
    const string asked = "GET " + string(nodes_path);
    const auto body = list_body(answer, asked, address);

    if (!body) {
        return nullopt;
    }

    try {
        return NodeList::from_body(*body);
    } catch (const invalid_argument& error) {
        lDebug << "Ignoring the node list from" << address.to_string() + ":" << error.what();
        return nullopt;
    }
}

// The endpoint of entry, from a list sent from source_address, resolved as
// NodeList::resolved() resolves it: nothing when it names the sender's
// machine and source_address is no IP address.
optional<string> resolved_endpoint(const NodeList::Entry& entry, string_view source_address) {
    NodeList alone;

    alone.add(entry.endpoint, entry.node_id);

    const auto resolved = alone.resolved(source_address);

    if (resolved.entries().empty()) {
        return nullopt;
    }

    return resolved.entries().front().endpoint;
}

// sent, the node list the node peer_id sent from source_address, as
// nodes.received posts it: each endpoint resolved by source_address (HttpApi
// §10.2), and those naming own_id, this node, left out. Each of the peer's
// own entries was observed where it was resolved, and advertised where it
// was not; the others were relayed.
Recorder::NodesReceived received_from(const NodeList& sent, const Identifier& peer_id,
                                      const Identifier& own_id, string_view source_address) {
    Recorder::NodesReceived received;
    set<string> named;

    for (const auto& entry : sent.entries()) {
        const auto node = entry.node_id.to_string();

        if (node == own_id.to_string()) {
            continue;
        }

        auto endpoint = resolved_endpoint(entry, source_address);

        // An endpoint named again keeps its first entry, as NodeList does.
        if (!endpoint || !named.insert(*endpoint).second) {
            continue;
        }

        if (node == peer_id.to_string()) {
            received.sources[*endpoint] =
                *endpoint == entry.endpoint ? Source::Advertised : Source::Observed;
        }

        received.nodes.add(std::move(*endpoint), entry.node_id);
    }

    return received;
}

} // namespace

namespace peer {

FirstContact::FirstContact(const NodeIdentity& identity, IDataSource::Ptr store,
                           MessageVerifier verifier)
    : m_identity(&identity)
    , m_store(std::move(store))
    , m_signer(identity)
    , m_verifier(std::move(verifier)) {
    if (!m_store) {
        throw invalid_argument("First contact needs a store to keep peers' keys in");
    }
}

FirstContact::Contact FirstContact::make(unique_ptr<PeerConnection> connection,
                                         const NodeList& own_list) const {
    if (!connection) {
        throw invalid_argument("First contact needs a connection to the node");
    }

    const auto node_id = _identify(*connection);
    PeerSession session(std::move(connection), node_id, m_signer, m_verifier);

    // Steps 3 to 5. The peer's answer to the list posted tells nothing that
    // first contact needs.
    const auto answers = session.exchange({
        request_of("POST", string(nodes_path), Converter::text_of(own_list.body()), json),
        request_of("GET", string(seek_path)),
        request_of("GET", string(nodes_path)),
    });

    if (const auto nodes = node_list_in(answers.at(2), session.address())) {
        auto received =
            received_from(*nodes, node_id, m_identity->id(), session.address().host());

        if (!received.nodes.entries().empty()) {
            Channel::post(Recorder::NodesReceived::name, std::move(received));
        }
    }

    auto seek = seek_list_in(answers.at(1), session.address());

    return {std::move(session), std::move(seek)};
}

// Steps 1 and, where it is needed, 2: the node at the other end of
// connection, once it has been given this node's key, and has shown it holds
// the key of the node it names, which is fetched when it is not held. Throws
// Unauthenticated where it has not.
Identifier FirstContact::_identify(PeerConnection& connection) const {
    auto put = request_of("PUT", data_path(m_identity->id()), m_identity->public_pem(),
                          octet_stream);
    const auto asked = request_line(put);
    const auto answer = answer_to(connection, m_signer, std::move(put));
    const auto signer = signer_of(answer, asked, connection.address(), m_verifier);

    if (!signer.verified) {
        _hold_key(connection, signer.node_id);
    }

    return signer.node_id;
}

// Step 2: fetches the key of node_id, which signed the first answer on
// connection, and keeps it, once it is shown to be the key, and to have
// signed the answer that brought it. Throws Unauthenticated, keeping nothing,
// where it is not.
void FirstContact::_hold_key(PeerConnection& connection, const Identifier& node_id) const {
    auto get = request_of("GET", data_path(node_id));
    const auto asked = request_line(get);
    const auto& address = connection.address();
    const auto answer = answer_to(connection, m_signer, std::move(get));
    const auto key = Converter::bytes_of(answer.body());

    if (static_cast<int>(Status::Ok) != answer.status_code()
        || !node_id.content_of(key, KnownKeys::max_key_size)) {
        throw unauthenticated(asked, address, "It did not send the key of " + node_id.to_string());
    }

    // The store holds no key for node_id, or the first answer could have
    // been checked with it, so removing what is put here leaves it as it was.
    m_store->put(node_id, key);

    try {
        const auto signer = signer_of(answer, asked, address, m_verifier);

        if (signer.node_id.to_string() != node_id.to_string()) {
            throw unauthenticated(asked, address,
                                  "It was signed by " + signer.node_id.to_string()
                                      + ", where the answer before was signed by "
                                      + node_id.to_string());
        }

        if (!signer.verified) {
            throw unauthenticated(asked, address, "The node's key is not held");
        }
    } catch (const Unauthenticated&) {
        (void)m_store->remove(node_id);
        throw;
    }

    Channel::post(DataStored::name, DataStored{node_id, node_id, key.size()});
}

} // namespace peer
