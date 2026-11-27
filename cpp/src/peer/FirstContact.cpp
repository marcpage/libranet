#include "FirstContact.hpp"

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "PeerConnection.hpp"
#include "PeerSession.hpp"
#include "SeekFulfiller.hpp"
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
#include "types/Constants.hpp"
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
using peer::SeekFulfiller;
using protocol::NodeList;
using protocol::SeekList;
using stats::Recorder;
using std::invalid_argument;
using std::min;
using std::nullopt;
using std::optional;
using std::ptrdiff_t;
using std::set;
using std::shared_ptr;
using std::size_t;
using std::string;
using std::string_view;
using std::unique_ptr;
using std::vector;
using storage::DataStored;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using types::data_path_prefix;
using types::json_content_type;
using types::nodes_path;
using types::octet_stream_content_type;
using types::seek_path;
using Source = stats::NodeAddresses::Source;
using Status = http::ResponseCode::Status;
using Unauthenticated = PeerSession::Unauthenticated;

namespace {

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
    return string(data_path_prefix) + identifier.to_string();
}

// This node's key, as peers know it: as store holds it under identity's id,
// once it is shown to be the content the id names, whatever form
// public_pem() gives the key in now; it is sent as it is kept, as a peer
// checks either (HttpApi §7.1). Otherwise public_pem(), where store holds no
// such content.
string own_key(const NodeIdentity& identity, const IDataSource& store) {
    const auto& identifier = identity.id();

    if (const auto stored = store.get(identifier)) {
        if (identifier.content_of(*stored, KnownKeys::max_key_size)) {
            return Converter::text_of(*stored);
        }
    }

    return identity.public_pem();
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
    if (answer.code() != Status::Ok) {
        lDebug << address.to_string() << "sent no list for" << asked << "but answered"
               << answer.status_code();
        return nullopt;
    }

    return Converter::bytes_of(answer.body());
}

// The seek list answer carries, from address, decompressed to no more than
// list_bytes: none when it carries none that can be read.
SeekList seek_list_in(const Response& answer, const Address& address, size_t list_bytes) {
    const string asked = "GET " + string(seek_path);
    const auto body = list_body(answer, asked, address);

    if (!body) {
        return {};
    }

    try {
        return SeekList::from_body(*body, list_bytes);
    } catch (const invalid_argument& error) {
        lDebug << "Ignoring the seek list from" << address.to_string() + ":" << error.what();
        return {};
    }
}

// The node list answer carries, from address, decompressed to no more than
// list_bytes: nothing when it carries none that can be read.
optional<NodeList> node_list_in(const Response& answer, const Address& address,
                                size_t list_bytes) {
    const string asked = "GET " + string(nodes_path);
    const auto body = list_body(answer, asked, address);

    if (!body) {
        return nullopt;
    }

    try {
        return NodeList::from_body(*body, list_bytes);
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

// Keeps in store what the node node_id sent in answer to a request for
// identifier, as it was sent, and posts it as data.stored, from that node,
// once it is shown to be the content, decompressing to no more than
// content_bytes, unless store holds it already. What is not kept is logged.
void keep(const Identifier& identifier, const Response& answer, const Identifier& node_id,
          const IDataSource& store, size_t content_bytes) {
    if (answer.code() != Status::Ok) {
        lDebug << node_id.to_string() << "did not send" << identifier.to_string()
               << "but answered" << answer.status_code();
        return;
    }

    const auto body = Converter::bytes_of(answer.body());

    if (!identifier.content_of(body, content_bytes)) {
        lInfo << node_id.to_string() << "sent what is not" << identifier.to_string();
        return;
    }

    if (store.has(identifier)) {
        return;
    }

    store.put(identifier, body);
    Channel::post(DataStored::name, DataStored{identifier, node_id, body.size()});
}

// Step 7: asks the node of session for each of sought that store does not
// hold, in runs of depth, and keeps what it sends that is the content, no
// more than content_bytes once decompressed.
void ask_for(PeerSession& session, const vector<Identifier>& sought, const IDataSource& store,
             size_t depth, size_t content_bytes) {
    vector<Identifier> lacked;

    std::copy_if(sought.begin(), sought.end(), std::back_inserter(lacked),
                 [&store](const Identifier& identifier) { return !store.has(identifier); });

    for (size_t start = 0; start < lacked.size(); start += depth) {
        const auto first = std::next(lacked.begin(), static_cast<ptrdiff_t>(start));
        const auto count = min(depth, lacked.size() - start);
        vector<Request> gets;

        std::transform(first, std::next(first, static_cast<ptrdiff_t>(count)),
                       std::back_inserter(gets),
                       [](const Identifier& identifier) {
            return request_of("GET", data_path(identifier));
        });

        const auto answers = session.exchange(std::move(gets));

        for (size_t index = 0; index < answers.size(); ++index) {
            keep(lacked.at(start + index), answers.at(index), session.node_id(), store,
                 content_bytes);
        }
    }
}

// Steps 6 and 7, unless the node of session has turned this node away, as
// it may in any answer, and so takes no more requests: fulfills seek, the
// list it sent, with fulfiller, then asks it for what own_seek names by
// identifier, keeping what decompresses to no more than content_bytes.
// Throws what PeerSession::exchange() throws, but for the connection having
// closed as the node turned this node away.
void trade(PeerSession& session, const SeekList& seek, const SeekList& own_seek,
           SeekFulfiller& fulfiller, const IDataSource& store, size_t content_bytes) {
    try {
        if (session.is_open()) {
            fulfiller.fulfill(session, seek);
        }

        if (session.is_open()) {
            ask_for(session, own_seek.data(), store, fulfiller.pipeline_depth(), content_bytes);
        }
    } catch (const PeerConnection::Closed&) {
        if (!session.was_turned_away()) {
            throw;
        }
    }
}

// Step 2: fetches the key of node_id, which signed the first answer on
// connection, and keeps it, once it is shown to be the key, and to have
// signed the answer that brought it, in store. Requests are signed with
// signer, and answers checked with verifier. Throws Unauthenticated, keeping
// nothing, where it is not.
void hold_key(const IDataSource& store, const MessageSigner& signer,
              const MessageVerifier& verifier, PeerConnection& connection,
              const Identifier& node_id) {
    auto get = request_of("GET", data_path(node_id));
    const auto asked = request_line(get);
    const auto& address = connection.address();
    const auto answer = answer_to(connection, signer, std::move(get));
    const auto key = Converter::bytes_of(answer.body());

    if (answer.code() != Status::Ok
        || !node_id.content_of(key, KnownKeys::max_key_size)) {
        throw unauthenticated(asked, address, "It did not send the key of " + node_id.to_string());
    }

    // The store holds no key for node_id, or the first answer could have
    // been checked with it, so removing what is put here leaves it as it was.
    store.put(node_id, key);

    try {
        const auto answered_by = signer_of(answer, asked, address, verifier);

        if (answered_by.node_id.to_string() != node_id.to_string()) {
            throw unauthenticated(asked, address,
                                  "It was signed by " + answered_by.node_id.to_string()
                                      + ", where the answer before was signed by "
                                      + node_id.to_string());
        }

        if (!answered_by.verified) {
            throw unauthenticated(asked, address, "The node's key is not held");
        }
    } catch (const Unauthenticated&) {
        (void)store.remove(node_id);
        throw;
    }

    Channel::post(DataStored::name, DataStored{node_id, node_id, key.size()});
}

// Steps 1 and, where it is needed, 2: the node at the other end of
// connection, once it has been given this node's key, identity's, and has
// shown it holds the key of the node it names, which is fetched into store
// when it is not held. Requests are signed with signer, and answers checked
// with verifier. Throws Unauthenticated where it has not.
Identifier identify(const NodeIdentity& identity, const IDataSource& store,
                    const MessageSigner& signer, const MessageVerifier& verifier,
                    PeerConnection& connection) {
    auto put = request_of("PUT", data_path(identity.id()), own_key(identity, store),
                          octet_stream_content_type);
    const auto asked = request_line(put);
    const auto answer = answer_to(connection, signer, std::move(put));
    const auto answered_by = signer_of(answer, asked, connection.address(), verifier);

    if (!answered_by.verified) {
        hold_key(store, signer, verifier, connection, answered_by.node_id);
    }

    return answered_by.node_id;
}

} // namespace

namespace peer {

FirstContact::FirstContact(const NodeIdentity& identity, IDataSource::Ptr store,
                           MessageVerifier verifier, shared_ptr<SeekFulfiller> fulfiller,
                           size_t content_bytes)
    : m_identity(&identity)
    , m_store(std::move(store))
    , m_signer(identity)
    , m_verifier(std::move(verifier))
    , m_fulfiller(std::move(fulfiller))
    , m_content_bytes(content_bytes) {
    if (!m_store) {
        throw invalid_argument("First contact needs a store to keep peers' keys in");
    }

    if (!m_fulfiller) {
        throw invalid_argument("First contact needs a seek fulfiller for what peers seek");
    }
}

FirstContact::Contact FirstContact::make(unique_ptr<PeerConnection> connection,
                                         const NodeList& own_list,
                                         const SeekList& own_seek) const {
    if (!connection) {
        throw invalid_argument("First contact needs a connection to the node");
    }

    const auto node_id = identify(*m_identity, *m_store, m_signer, m_verifier, *connection);
    PeerSession session(std::move(connection), node_id, m_signer, m_verifier);

    // Steps 3 to 5. The peer's answer to the list posted tells nothing that
    // first contact needs.
    const auto answers = session.exchange({
        request_of("POST", string(nodes_path), Converter::text_of(own_list.body()),
                   json_content_type),
        request_of("GET", string(seek_path)),
        request_of("GET", string(nodes_path)),
    });

    const auto list_bytes = m_fulfiller->list_bytes();

    if (const auto nodes = node_list_in(answers.at(2), session.address(), list_bytes)) {
        auto received =
            received_from(*nodes, node_id, m_identity->id(), session.address().host());

        if (!received.nodes.entries().empty()) {
            Channel::post(Recorder::NodesReceived::name, std::move(received));
        }
    }

    auto seek = seek_list_in(answers.at(1), session.address(), list_bytes);

    trade(session, seek, own_seek, *m_fulfiller, *m_store, m_content_bytes);
    return {std::move(session), std::move(seek)};
}

} // namespace peer
