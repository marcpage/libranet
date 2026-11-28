#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using logger::ILogOutput;
using logger::Logger;
using protocol::NodeList;
using storage::Identifier;
using storage::Zlib;
using types::Converter;

namespace {

// The SHA-256 of "hello", as `shasum -a 256` gives it, and as a node's id.
constexpr std::string_view hello_hash =
    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr std::string_view hello_id =
    "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

// What Python's json.dumps(..., separators=(",", ":")) writes for a node list
// naming hello_id at http://localhost:8080 and http://203.0.113.42:4300, and
// zlib.compress() of it, at level 9, in hex.
constexpr std::string_view python_list =
    R"({"nodes":{"http://localhost:8080":"sha256/)"
    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    R"(","http://203.0.113.42:4300":"sha256/)"
    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    R"("}})";
constexpr std::string_view python_list_compressed =
    "78daadcdbb0dc3201400c05da823783f30b00de08728ac10093acbbba7ca06b905ee36ef79ea32f93663ef4f"
    "76ee9aad5c63ae9d234430d9ac51c80747ad939cb5f85ea13028058d5ca9345f935252ac18507dc35e0e21af"
    "078330074a1c6b8a24e6f51b08d88245642b9485e19fc9f37c01bf95399c";

// The text that lowercase hex names, a char for each byte.
std::string text_of_hex(std::string_view hex) {
    constexpr std::string_view digits = "0123456789abcdef";
    constexpr auto nibble_bits = 4U;
    std::string text;

    for (std::size_t index = 0; index + 1 < hex.size(); index += 2) {
        const auto high = digits.find(hex.at(index));
        const auto low = digits.find(hex.at(index + 1));

        text.push_back(static_cast<char>((high << nibble_bits) | low));
    }

    return text;
}

// text as one zlib stream.
std::string compressed(std::string_view text) {
    return Converter::text_of(Zlib::compress(Converter::bytes_of(text), Zlib::max_level));
}

// A node's id of its own for each number.
Identifier node(std::size_t number) {
    return Identifier::of(Converter::bytes_of("node " + std::to_string(number)));
}

// The endpoints list names, in order.
std::vector<std::string> endpoints_of(const NodeList& list) {
    std::vector<std::string> endpoints;

    std::transform(list.entries().begin(), list.entries().end(), std::back_inserter(endpoints),
                   [](const NodeList::Entry& entry) { return entry.endpoint; });
    return endpoints;
}

// The endpoints a list naming each of endpoints names once it is received
// from source_address.
std::vector<std::string> resolved(const std::vector<std::string>& endpoints,
                                  std::string_view source_address) {
    NodeList list;

    for (const auto& endpoint : endpoints) {
        list.add(endpoint, node(0));
    }

    return endpoints_of(list.resolved(source_address));
}

// Whether NodeList::from_body() turns down body as no node list, reading no
// more than decompressed_limit of it.
bool refused(std::string_view body,
             std::size_t decompressed_limit = types::max_decompressed_list_size) {
    try {
        (void)NodeList::from_body(body, decompressed_limit);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// Whether a list takes endpoint as one.
bool is_endpoint(const std::string& endpoint) {
    try {
        NodeList list;

        list.add(endpoint, node(0));
    } catch (const std::invalid_argument&) {
        return false;
    }

    return true;
}

// text, as a JSON string.
std::string json_string(std::string_view text) {
    return "\"" + std::string(text) + "\"";
}

// An entry of a node list's JSON, naming value, written as JSON, at endpoint.
std::string entry(std::string_view endpoint, std::string_view value) {
    return json_string(endpoint) + ":" + std::string(value);
}

// The JSON of a node list of entries.
std::string list_of(const std::vector<std::string>& entries) {
    std::string text = R"({"nodes":{)";

    for (std::size_t index = 0; index < entries.size(); ++index) {
        text += (0 == index ? "" : ",") + entries.at(index);
    }

    return text + "}}";
}

// The endpoints a node list of entries names, as read.
std::vector<std::string> endpoints_read(const std::vector<std::string>& entries) {
    return endpoints_of(NodeList::from_body(list_of(entries)));
}

// Keeps each line it is given, where a test can read them while the Logger
// owns it.
class KeepingOutput : public ILogOutput {
public:
    explicit KeepingOutput(std::shared_ptr<std::vector<std::string>> lines)
        : m_lines(std::move(lines)) {}

    void log(std::string_view line) override {
        m_lines->emplace_back(line);
    }

private:
    std::shared_ptr<std::vector<std::string>> m_lines;
};

// The warnings logged while this is in scope, and no others.
class KeptWarnings {
public:
    KeptWarnings() {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<KeepingOutput>(m_lines));
        Logger::reset_levels(Logger::Level::Warning);
    }

    ~KeptWarnings() {
        Logger::remove_all_outputs();
    }

    [[nodiscard]] const std::vector<std::string>& lines() const noexcept {
        return *m_lines;
    }

    KeptWarnings(const KeptWarnings&) = delete;
    KeptWarnings& operator=(const KeptWarnings&) = delete;
    KeptWarnings(KeptWarnings&&) = delete;
    KeptWarnings& operator=(KeptWarnings&&) = delete;

private:
    using Lines = std::vector<std::string>;

    std::shared_ptr<Lines> m_lines = std::make_shared<Lines>();
};

} // namespace

// MARK: Reading

TEST(NodeListTest, ReadsEachEntry) {
    const auto list = NodeList::from_body(python_list);

    ASSERT_EQ(list.entries().size(), 2U);
    EXPECT_EQ(list.entries().at(0).endpoint, "http://203.0.113.42:4300");
    EXPECT_EQ(list.entries().at(0).node_id.to_string(), hello_id);
    EXPECT_EQ(list.entries().at(1).endpoint, "http://localhost:8080");
    EXPECT_EQ(list.entries().at(1).node_id.to_string(), hello_id);
}

TEST(NodeListTest, ReadsBodyAsItIsOrCompressed) {
    const auto plain = NodeList::from_body(python_list);
    const auto unpacked = NodeList::from_body(text_of_hex(python_list_compressed));

    EXPECT_EQ(endpoints_of(plain),
              (std::vector<std::string>{"http://203.0.113.42:4300", "http://localhost:8080"}));
    EXPECT_EQ(unpacked.to_string(), plain.to_string());
}

// A node id may be in upper case, and is kept in lower case (HttpApi §5.4).
TEST(NodeListTest, NodeIdIsKeptInLowerCase) {
    const std::string upper = "2CF24DBA5FB0A30E26E83B2AC5B9E29E1B161E5C1FA7425E73043362938B9824";
    const auto upper_id = json_string("sha256/" + upper);

    const auto list = NodeList::from_body(list_of({entry("http://localhost:8080", upper_id)}));

    ASSERT_EQ(list.entries().size(), 1U);
    EXPECT_EQ(list.entries().at(0).node_id.to_string(), hello_id);
}

// What else the list holds, beside "nodes", is no matter.
TEST(NodeListTest, OtherMembersAreIgnored) {
    const auto list = NodeList::from_body(R"({"version":1,"nodes":{}})");

    EXPECT_TRUE(list.entries().empty());
}

TEST(NodeListTest, NotANodeListIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("not JSON"));
    EXPECT_TRUE(refused("null"));
    EXPECT_TRUE(refused("[]"));
    EXPECT_TRUE(refused("{}"));
    EXPECT_TRUE(refused(R"({"peers":{}})"));
    EXPECT_TRUE(refused(R"({"nodes":[]})"));
    EXPECT_TRUE(refused(R"({"nodes":"http://localhost:8080"})"));
    EXPECT_TRUE(refused(R"({"nodes":{}} trailing)"));
}

TEST(NodeListTest, CompressedBodyThatIsNotANodeListIsRefused) {
    const auto stream = text_of_hex(python_list_compressed);

    EXPECT_TRUE(refused(compressed("not JSON")));
    EXPECT_TRUE(refused(compressed("[]")));
    EXPECT_TRUE(refused(std::string_view(stream).substr(0, stream.size() - 1)));
}

// The protocol sets no limit on a list once decompressed, so the node does.
TEST(NodeListTest, CompressedBodyOverTheLimitIsRefused) {
    const auto stream = text_of_hex(python_list_compressed);

    EXPECT_FALSE(refused(stream, python_list.size()));
    EXPECT_TRUE(refused(stream, python_list.size() - 1));
}

// An entry that cannot be used costs its sender only that entry.
TEST(NodeListTest, EntryThatNamesNoNodeIdIsLeftOut) {
    const std::string hash(hello_hash);

    const auto endpoints = endpoints_read({
        entry("http://a:1", "5"),
        entry("http://b:2", "null"),
        entry("http://c:3", "{}"),
        entry("http://d:4", json_string(hash)),
        entry("http://e:5", json_string("/" + hash)),
        entry("http://f:6", json_string("sha256/")),
        entry("http://g:7", json_string("sha256/abc")),
        entry("http://h:8", json_string("sha256/" + hash + "/x")),
        entry("http://i:9", json_string("sha256//" + hash)),
        entry("http://kept:80", json_string(hello_id)),
    });

    EXPECT_EQ(endpoints, (std::vector<std::string>{"http://kept:80"}));
}

TEST(NodeListTest, EntryThatNamesNoEndpointIsLeftOut) {
    const auto endpoints = endpoints_read({
        entry("localhost:8080", json_string(hello_id)),
        entry("ftp://host:21", json_string(hello_id)),
        entry("http://host:80/path", json_string(hello_id)),
        entry("http://kept:80", json_string(hello_id)),
    });

    EXPECT_EQ(endpoints, (std::vector<std::string>{"http://kept:80"}));
}

// An endpoint is an http or https URL naming a host, and a port if any
// (HttpApi §10.6).
TEST(NodeListTest, EndpointsThatAreOne) {
    EXPECT_TRUE(is_endpoint("http://localhost:8080"));
    EXPECT_TRUE(is_endpoint("https://libranet.example"));
    EXPECT_TRUE(is_endpoint("http://203.0.113.42:4300"));
    EXPECT_TRUE(is_endpoint("http://[2001:db8::1]:8080"));
    EXPECT_TRUE(is_endpoint("http://[::ffff:203.0.113.42]"));
    EXPECT_TRUE(is_endpoint("HTTP://Example.COM:80"));
    EXPECT_TRUE(is_endpoint("http://a-b_c~d.e:0"));
    EXPECT_TRUE(is_endpoint("http://host:65535"));
}

TEST(NodeListTest, EndpointsThatAreNotOneAreRefused) {
    EXPECT_FALSE(is_endpoint(""));
    EXPECT_FALSE(is_endpoint("localhost:8080"));
    EXPECT_FALSE(is_endpoint("ftp://host:21"));
    EXPECT_FALSE(is_endpoint("http://"));
    EXPECT_FALSE(is_endpoint("http://:8080"));
    EXPECT_FALSE(is_endpoint("http://host:"));
    EXPECT_FALSE(is_endpoint("http://host:65536"));
    EXPECT_FALSE(is_endpoint("http://host:123456"));
    EXPECT_FALSE(is_endpoint("http://host:80a"));
    EXPECT_FALSE(is_endpoint("http://host:+80"));
    EXPECT_FALSE(is_endpoint("http://host:80:80"));
    EXPECT_FALSE(is_endpoint("http://host/"));
    EXPECT_FALSE(is_endpoint("http://host:80/path"));
    EXPECT_FALSE(is_endpoint("http://user@host:80"));
    EXPECT_FALSE(is_endpoint("http://host?query"));
    EXPECT_FALSE(is_endpoint("http://host#fragment"));
    EXPECT_FALSE(is_endpoint("http://ho st:80"));
    EXPECT_FALSE(is_endpoint(std::string("http://host\0:80", 15)));
}

TEST(NodeListTest, Ipv6EndpointsThatAreNotOneAreRefused) {
    EXPECT_FALSE(is_endpoint("http://::1:80"));
    EXPECT_FALSE(is_endpoint("http://[::1"));
    EXPECT_FALSE(is_endpoint("http://[]:80"));
    EXPECT_FALSE(is_endpoint("http://[zz::1]:80"));
    EXPECT_FALSE(is_endpoint("http://[203.0.113.42]:80"));
    EXPECT_FALSE(is_endpoint("http://[::1]x:80"));
    EXPECT_FALSE(is_endpoint(std::string("http://[::1\0]:80", 16)));
}

// Node ids under an algorithm the node does not know may mean it needs
// updating, so they are logged, but once for the list, however many it
// names, so that a list cannot fill the log.
TEST(NodeListTest, UnknownAlgorithmIsLoggedOnceForTheList) {
    const KeptWarnings warnings;

    const auto endpoints = endpoints_read({
        entry("http://a:1", json_string("md5/0123")),
        entry("http://b:2", json_string("md5/4567")),
        entry("http://c:3", json_string("sha512/89ab")),
        entry("http://kept:80", json_string(hello_id)),
    });
    (void)endpoints_read({entry("http://kept:80", json_string(hello_id))});
    (void)endpoints_read({entry("http://a:1", json_string("md5/0123"))});

    EXPECT_EQ(endpoints, (std::vector<std::string>{"http://kept:80"}));
    ASSERT_EQ(warnings.lines().size(), 2U);
    EXPECT_NE(warnings.lines().at(0).find("3 node ids"), std::string::npos);
    EXPECT_NE(warnings.lines().at(0).find("md5"), std::string::npos);
    EXPECT_NE(warnings.lines().at(1).find("1 node ids"), std::string::npos);
}

// MARK: Writing

// A node names its own endpoints first (HttpApi §10.1), then the others in
// the order added, best first.
TEST(NodeListTest, OwnEndpointsComeFirst) {
    const auto own = Identifier::parse(hello_id);
    NodeList list(own, {"http://localhost:4300", "http://localhost:8080"});

    list.add("http://198.51.100.7:8080", own);
    list.add("http://192.0.2.1:8080", own);

    EXPECT_EQ(endpoints_of(list),
              (std::vector<std::string>{"http://localhost:4300", "http://localhost:8080",
                                        "http://198.51.100.7:8080", "http://192.0.2.1:8080"}));
}

TEST(NodeListTest, WritesCompactJsonInOrder) {
    const auto own = Identifier::parse(hello_id);
    const NodeList list(own, {"http://localhost:8080", "http://203.0.113.42:4300"});

    EXPECT_EQ(list.to_string(),
              R"({"nodes":{"http://localhost:8080":")" + std::string(hello_id)
                  + R"(","http://203.0.113.42:4300":")" + std::string(hello_id) + R"("}})");
    EXPECT_EQ(NodeList().to_string(), R"({"nodes":{}})");
}

TEST(NodeListTest, ReadsWhatItWrites) {
    NodeList list(node(0), {"http://localhost:8080"});

    list.add("https://[2001:db8::1]:443", node(1));
    list.add("HTTP://Example.COM", node(2));

    const auto read = NodeList::from_body(list.to_string());

    EXPECT_EQ(NodeList::from_body(read.to_string()).to_string(), read.to_string());
    EXPECT_EQ(read.entries().size(), 3U);
}

// An endpoint named again keeps the entry it was first named in, the better.
TEST(NodeListTest, EndpointNamedAgainKeepsItsFirstEntry) {
    NodeList list(node(0), {"http://localhost:8080"});

    list.add("http://localhost:8080", node(1));
    list.add("http://198.51.100.7:8080", node(2));
    list.add("http://198.51.100.7:8080", node(3));

    ASSERT_EQ(list.entries().size(), 2U);
    EXPECT_EQ(list.entries().at(0).node_id.to_string(), node(0).to_string());
    EXPECT_EQ(list.entries().at(1).node_id.to_string(), node(2).to_string());
}

TEST(NodeListTest, OwnEndpointThatIsNotOneIsRefused) {
    EXPECT_THROW((void)NodeList(node(0), {"localhost:8080"}), std::invalid_argument);
}

TEST(NodeListTest, SmallListIsSentAsJson) {
    const NodeList list(node(0), {"http://localhost:8080"});

    const auto body = list.body();

    EXPECT_EQ(body.text, list.to_string());
    EXPECT_FALSE(body.compressed);
}

// A list is sent in less than 1 MiB, compressed when it would not fit
// otherwise (HttpApi §10.6).
TEST(NodeListTest, ListOverOneMibIsSentCompressed) {
    constexpr std::size_t entry_count = 12'000;
    NodeList list;

    for (std::size_t index = 0; index < entry_count; ++index) {
        list.add("http://node-" + std::to_string(index) + ".example:8080", node(0));
    }

    const auto body = list.body();

    ASSERT_FALSE(body.text.empty());
    EXPECT_TRUE(body.compressed);
    EXPECT_GE(list.to_string().size(), NodeList::max_size);
    EXPECT_LT(body.text.size(), NodeList::max_size);
    EXPECT_EQ(body.text.front(), '\x78'); // A zlib stream's first byte
    EXPECT_EQ(NodeList::from_body(body.text).entries().size(), entry_count);
}

// What does not fit even compressed is left out from the end, where the
// entries least worth naming are.
TEST(NodeListTest, WhatDoesNotFitIsLeftOutFromTheEnd) {
    constexpr std::size_t entry_count = 200;
    constexpr std::size_t limit = 4'096;
    NodeList list(node(0), {"http://localhost:8080", "http://localhost:4300"});

    for (std::size_t index = 1; index <= entry_count; ++index) {
        list.add("http://198.51.100.7:" + std::to_string(index), node(index));
    }

    const auto body = list.body(limit);
    auto sent = endpoints_of(NodeList::from_body(body.text));
    auto first = endpoints_of(list);

    first.resize(sent.size());
    std::sort(sent.begin(), sent.end());
    std::sort(first.begin(), first.end());

    EXPECT_LT(body.text.size(), limit);
    EXPECT_GT(sent.size(), 2U);
    EXPECT_LT(sent.size(), entry_count);
    EXPECT_EQ(sent, first);
}

// {"nodes":{}}, a list that names no node, is 12 bytes, and is sent once
// no other fits.
TEST(NodeListTest, LimitThatHoldsNoListIsRefused) {
    constexpr std::size_t empty_size = 12;
    const NodeList list(node(0), {"http://localhost:8080"});

    EXPECT_EQ(list.body(empty_size + 1).text, R"({"nodes":{}})");
    EXPECT_THROW((void)list.body(empty_size), std::invalid_argument);
}

// MARK: Resolving

// HttpApi §10.2: localhost is the address the list came from.
TEST(NodeListTest, LocalhostIsTheAddressTheListCameFrom) {
    EXPECT_EQ(resolved({"http://localhost:4300", "https://LOCALHOST:443", "http://localhost"},
                       "203.0.113.42"),
              (std::vector<std::string>{"http://203.0.113.42:4300", "https://203.0.113.42:443",
                                        "http://203.0.113.42"}));
}

// A loopback address names the sender's machine as localhost does.
TEST(NodeListTest, LoopbackIsTheAddressTheListCameFrom) {
    EXPECT_EQ(resolved({"http://127.0.0.1:8080", "http://127.1.2.3:8081", "http://[::1]:8082",
                        "http://[::ffff:127.0.0.1]:8083"},
                       "203.0.113.42"),
              (std::vector<std::string>{"http://203.0.113.42:8080", "http://203.0.113.42:8081",
                                        "http://203.0.113.42:8082", "http://203.0.113.42:8083"}));
}

// A list from this machine names this machine by its loopback addresses too,
// so they are kept as sent, as their family may be the only one the node
// there listens at.
TEST(NodeListTest, LoopbackFromThisMachineIsKeptAsSent) {
    const std::vector<std::string> endpoints{
        "http://127.0.0.1:8080",
        "http://[::1]:8082",
        "http://localhost:4300",
    };

    EXPECT_EQ(resolved(endpoints, "127.0.0.1"),
              (std::vector<std::string>{"http://127.0.0.1:8080", "http://[::1]:8082",
                                        "http://127.0.0.1:4300"}));
    EXPECT_EQ(resolved(endpoints, "::1"),
              (std::vector<std::string>{"http://127.0.0.1:8080", "http://[::1]:8082",
                                        "http://[::1]:4300"}));
    EXPECT_EQ(resolved(endpoints, "::ffff:127.0.0.1"),
              (std::vector<std::string>{"http://127.0.0.1:8080", "http://[::1]:8082",
                                        "http://127.0.0.1:4300"}));
}

// A dual-stack listener reports an IPv4 client as an IPv4-mapped IPv6
// address, which is put in place as the IPv4 address, so that nodes without
// IPv6 can reach it too.
TEST(NodeListTest, Ipv4MappedSourceIsItsIpv4Address) {
    EXPECT_EQ(resolved({"http://localhost:4300"}, "::ffff:203.0.113.42"),
              (std::vector<std::string>{"http://203.0.113.42:4300"}));
}

TEST(NodeListTest, Ipv6SourceIsInBrackets) {
    EXPECT_EQ(resolved({"http://localhost:4300"}, "2001:DB8::1"),
              (std::vector<std::string>{"http://[2001:db8::1]:4300"}));
}

// An endpoint another node resolved is not resolved again.
TEST(NodeListTest, OtherHostsAreKeptAsSent) {
    const std::vector<std::string> endpoints{
        "http://198.51.100.7:8080",
        "https://itsme.duckdns.org:4300",
        "http://[2001:db8::7]:8080",
        "HTTP://Example.COM",
    };

    EXPECT_EQ(resolved(endpoints, "203.0.113.42"), endpoints);
}

TEST(NodeListTest, SourceThatIsNoIpAddressLeavesOutWhatNamesTheSender) {
    const std::vector<std::string> endpoints{
        "http://localhost:4300",
        "http://127.0.0.1:8080",
        "http://198.51.100.7:8080",
    };

    EXPECT_EQ(resolved(endpoints, "node.example"),
              (std::vector<std::string>{"http://198.51.100.7:8080"}));
    EXPECT_EQ(resolved(endpoints, std::string("203.0.113.42\0", 13)),
              (std::vector<std::string>{"http://198.51.100.7:8080"}));
}

TEST(NodeListTest, EndpointsResolvedAlikeKeepTheFirst) {
    NodeList list;

    list.add("http://localhost:8080", node(1));
    list.add("http://127.0.0.1:8080", node(2));

    const auto received = list.resolved("203.0.113.42");

    ASSERT_EQ(received.entries().size(), 1U);
    EXPECT_EQ(received.entries().at(0).endpoint, "http://203.0.113.42:8080");
    EXPECT_EQ(received.entries().at(0).node_id.to_string(), node(1).to_string());
}
