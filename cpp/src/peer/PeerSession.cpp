#include "PeerSession.hpp"

#include <cstddef>
#include <future>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "PeerConnection.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "net/Address.hpp"
#include "storage/Identifier.hpp"

using http::Request;
using http::Response;
using identity::MessageSigner;
using identity::MessageVerifier;
using net::Address;
using peer::PeerConnection;
using std::future;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::string;
using std::string_view;
using std::vector;
using storage::Identifier;

namespace {

// The request's method and target, as a message names the request.
string request_line(const Request& request) {
    return string(request.method()) + " " + string(request.uri());
}

// Why answer cannot be shown to come from the node node_id, as verifier
// finds: nothing when it can.
optional<string> fault_of(const Response& answer, const Identifier& node_id,
                          const MessageVerifier& verifier) {
    optional<MessageVerifier::Signer> signer;

    try {
        signer = verifier.verify(answer);
    } catch (const invalid_argument& error) {
        return string(error.what());
    }

    if (!signer) {
        return string("It is not signed");
    }

    if (signer->node_id.to_string() != node_id.to_string()) {
        return "It was signed by " + signer->node_id.to_string();
    }

    if (!signer->verified) {
        return string("The node's key is not held");
    }

    return nullopt;
}

} // namespace

namespace peer {

PeerSession::PeerSession(std::unique_ptr<PeerConnection> connection, Identifier node_id,
                         MessageSigner signer, MessageVerifier verifier)
    : m_connection(std::move(connection))
    , m_node_id(std::move(node_id))
    , m_signer(std::move(signer))
    , m_verifier(std::move(verifier)) {
    if (!m_connection) {
        throw invalid_argument("A session needs a connection to the node");
    }

    if (m_node_id.hash().empty()) {
        throw invalid_argument("A session needs the id of the node");
    }
}

vector<Response> PeerSession::exchange(vector<Request> requests) {
    vector<string> asked;
    vector<future<Response>> answers;
    vector<Response> checked;

    // All are signed first, so that one that cannot be leaves none sent.
    for (auto& request : requests) {
        m_signer.sign(request);
    }

    for (auto& request : requests) {
        asked.push_back(request_line(request));
        answers.push_back(m_connection->send(std::move(request)));
    }

    // Each is checked as it comes, while its signature is fresh.
    for (std::size_t index = 0; index < answers.size(); ++index) {
        auto answer = answers.at(index).get();

        _check(answer, asked.at(index));
        checked.push_back(std::move(answer));
    }

    return checked;
}

const Identifier& PeerSession::node_id() const noexcept {
    return m_node_id;
}

const Address& PeerSession::address() const noexcept {
    return m_connection->address();
}

bool PeerSession::is_open() const {
    return m_connection->is_open();
}

bool PeerSession::was_turned_away() const {
    return m_connection->was_turned_away();
}

void PeerSession::close() {
    m_connection->close();
}

// Ends the session, and throws Unauthenticated, unless answer is shown to
// come from the node. asked names its request.
void PeerSession::_check(const Response& answer, string_view asked) {
    const auto fault = fault_of(answer, m_node_id, m_verifier);

    if (!fault) {
        return;
    }

    close();
    throw Unauthenticated("The answer to " + string(asked) + " from "
                          + address().to_string() + " is not from "
                          + m_node_id.to_string() + ": " + *fault);
}

} // namespace peer
