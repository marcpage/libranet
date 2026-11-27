#pragma once

#include <string>
#include <string_view>

namespace protocol {

// {"address": "<address>"}: an http endpoint for the node to dial, as a node
// list names one (HttpApi §10.6).
class DialRequest {
public:
    // Of a request, and of its answer.
    static constexpr std::string_view address_key = "address";

    // Throws std::invalid_argument for text that is not a JSON object whose
    // "address" the constructor takes.
    [[nodiscard]] static DialRequest parse(std::string_view text);

    // Throws std::invalid_argument for an address that is not an http URL
    // naming a host.
    explicit DialRequest(std::string_view address);

    // http://{host}:{port}, however address was written.
    [[nodiscard]] const std::string& endpoint() const noexcept;
    // {"address":"<endpoint>"}
    [[nodiscard]] std::string to_string() const;

private:
    std::string m_endpoint;
};

} // namespace protocol
