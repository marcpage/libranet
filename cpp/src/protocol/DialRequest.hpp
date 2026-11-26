#pragma once

#include <string>
#include <string_view>

namespace protocol {

// The address a request asks the node to dial, {"address": "<address>"}: an
// http endpoint naming a host, as a node list names one (HttpApi §10.6),
// written http://{host}:{port} as it is dialed, so that the node learns it
// once however it was typed.
class DialRequest {
public:
    // Of a request, and of its answer.
    static constexpr std::string_view address_key = "address";

    // Throws std::invalid_argument, saying why, for text that is not a JSON
    // object whose "address" is a string the constructor takes.
    [[nodiscard]] static DialRequest parse(std::string_view text);

    // address, an http URL naming a host, however it is written: its host in
    // lower case, an IPv6 address in brackets, and its port, 80 if it names
    // none, without what follows them. Throws std::invalid_argument, saying
    // why, for an address that is not one.
    explicit DialRequest(std::string_view address);

    [[nodiscard]] const std::string& endpoint() const noexcept;
    // As JSON: {"address":"<endpoint>"}.
    [[nodiscard]] std::string to_string() const;

private:
    std::string m_endpoint;
};

} // namespace protocol
