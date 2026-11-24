#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "io/BufferedReader.hpp"
#include "io/IReader.hpp"
#include "io/MeteredReader.hpp"
#include "io/SocketReader.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

using http::Response;
using http::ResponseCode;
using io::BufferedReader;
using io::IReader;
using io::LimitExceeded;
using io::MeteredReader;
using io::SocketReader;
using net::Address;
using net::ISocket;

namespace {

// A reader of the text it is given, at most max_chunk bytes a read, as a
// connection's bytes arrive a few at a time.
class StringReader final : public IReader {
public:
    static constexpr std::size_t chunk_size = 7;

    explicit StringReader(std::string data, std::size_t max_chunk = chunk_size)
        : m_data(std::move(data))
        , m_max_chunk(max_chunk) {}

    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data,
                                                       std::size_t size) override {
        if (m_position >= m_data.size()) {
            return std::nullopt;
        }

        const auto count = std::min({size, m_max_chunk, m_data.size() - m_position});
        const auto first = m_data.begin() + static_cast<std::string::difference_type>(m_position);

        std::transform(first, first + static_cast<std::string::difference_type>(count), data,
                       [](char single_byte) {
            return static_cast<std::byte>(single_byte);
        });
        m_position += count;
        return count;
    }

private:
    std::string m_data;
    std::size_t m_max_chunk;
    std::size_t m_position{0};
};

// A reader of the text as a connection's, as Response::read() is given one:
// over a buffer, over the bytes as they arrive.
MeteredReader connection_of(std::string text) {
    return MeteredReader(
        std::make_unique<BufferedReader>(std::make_unique<StringReader>(std::move(text))), 0);
}

} // namespace

TEST(ResponseTest, ConstructorsAndAccessors) {
    constexpr auto not_found_code = 404;

    // Default constructor state (HTTP/1.1 200 OK)
    Response response;
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.reason_phrase(), "OK");
    EXPECT_TRUE(response.body().empty());
    EXPECT_TRUE(response.headers().empty());

    // Setters and getters
    response.set_version("HTTP/1.0");
    response.set_status_code(not_found_code);
    response.set_reason_phrase("Not Found");
    response.set_body("Resource missing");

    EXPECT_EQ(response.version(), "HTTP/1.0");
    EXPECT_EQ(response.status_code(), not_found_code);
    EXPECT_EQ(response.reason_phrase(), "Not Found");
    EXPECT_EQ(response.body(), "Resource missing");

    // Modifying headers through composition
    response.headers().set("Content-Type", "text/plain");
    EXPECT_EQ(response.headers().get("content-type"), "text/plain");
}

TEST(ResponseTest, ParseStatusLine) {
    Response response;

    // Valid status line with reason phrase
    const bool success = response.parse_status_line("HTTP/1.1 200 OK\r\n");
    EXPECT_TRUE(success);
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.reason_phrase(), "OK");

    // Valid status line without reason phrase
    const bool success_no_reason = response.parse_status_line("HTTP/1.1 500");
    EXPECT_TRUE(success_no_reason);
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 500);
    EXPECT_TRUE(response.reason_phrase().empty());

    // Valid status line with extra spacing
    const bool success_spaced = response.parse_status_line("   HTTP/1.1   403   Forbidden   ");
    EXPECT_TRUE(success_spaced);
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 403);
    EXPECT_EQ(response.reason_phrase(), "Forbidden");

    // Malformed: non-numeric status code
    EXPECT_FALSE(response.parse_status_line("HTTP/1.1 ABC OK"));

    // Malformed: missing status code
    EXPECT_FALSE(response.parse_status_line("HTTP/1.1"));

    // Malformed: empty line
    EXPECT_FALSE(response.parse_status_line(""));
}

TEST(ResponseTest, StreamParsingAndBody) {
    std::istringstream in_stream(
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 12\r\n"
        "\r\n"
        "Hello World!");

    Response response;
    in_stream >> response;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.reason_phrase(), "OK");
    EXPECT_EQ(response.headers().get("content-type"), "text/plain");
    EXPECT_EQ(response.headers().get("content-length"), "12");
    EXPECT_EQ(response.body(), "Hello World!");
}

TEST(ResponseTest, ContentLengthBeyondStream) {
    // A length far past what arrives fails the stream, without first taking
    // that much memory.
    std::istringstream in_stream(
        "HTTP/1.1 200 OK\r\n"
        "Content-Length: 99999999999\r\n"
        "\r\n"
        "partial");

    Response response;
    in_stream >> response;

    EXPECT_TRUE(in_stream.fail());
    EXPECT_EQ(response.body(), "partial");
}

TEST(ResponseTest, BodyLongerThanOnePiece) {
    constexpr std::size_t body_bytes = 100'000;
    const std::string body(body_bytes, 'x');
    std::istringstream in_stream(
        std::string("HTTP/1.1 200 OK\r\n")
        + "Content-Length: " + std::to_string(body_bytes) + "\r\n"
        + "\r\n"
        + body);

    Response response;
    in_stream >> response;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(response.body(), body);
}

TEST(ResponseTest, Serialization) {
    constexpr auto created_code = 201;

    Response response;
    response.set_version("HTTP/1.1");
    response.set_status_code(created_code);
    response.set_reason_phrase("Created");
    response.headers().set("Connection", "close");

    const std::string serialized = response.to_string();

    // Status line + headers + blank line + body (empty)
    const std::string expected =
        "HTTP/1.1 201 Created\r\n"
        "Connection: close\r\n"
        "\r\n";

    EXPECT_EQ(serialized, expected);
}

// A status line without a reason phrase is written without one.
TEST(ResponseTest, SerializationWithoutAReasonPhrase) {
    Response response;

    ASSERT_TRUE(response.parse_status_line("HTTP/1.1 299"));
    EXPECT_EQ(response.to_string(), "HTTP/1.1 299\r\n\r\n");
}

TEST(ResponseTest, StatusCodeFromAStatus) {
    Response response;

    response.set_status_code(ResponseCode::Status::NotFound);

    EXPECT_EQ(response.status_code(), 404);
    EXPECT_EQ(response.reason_phrase(), "Not Found");
    EXPECT_EQ(response.code(), ResponseCode::Status::NotFound);
}

TEST(ResponseTest, StatusCodeOutOfRangeIsRefused) {
    Response response;

    EXPECT_FALSE(response.parse_status_line("HTTP/1.1 99999999999999999999 OK"));
}

TEST(ResponseTest, StreamWithoutAStatusLineFails) {
    std::istringstream empty_stream("");
    std::istringstream bad_line_stream("HTTP/1.1\r\n\r\n");
    Response response;

    empty_stream >> response;
    bad_line_stream >> response;

    EXPECT_TRUE(empty_stream.fail());
    EXPECT_TRUE(bad_line_stream.fail());
}

// The headers end with an empty line, which this stream never reaches.
TEST(ResponseTest, StreamEndingInTheHeadersFails) {
    std::istringstream in_stream("HTTP/1.1 200 OK\r\nConnection: close\r\n");
    Response response;

    in_stream >> response;

    EXPECT_TRUE(in_stream.fail());
}

TEST(ResponseTest, StreamWithContentLengthNotANumberFails) {
    std::istringstream in_stream("HTTP/1.1 200 OK\r\nContent-Length: five\r\n\r\nhello");
    Response response;

    in_stream >> response;

    EXPECT_TRUE(in_stream.fail());
}

TEST(ResponseTest, StreamWithContentLengthOutOfRangeFails) {
    std::istringstream in_stream(
        "HTTP/1.1 200 OK\r\nContent-Length: 99999999999999999999999\r\n\r\nhello");
    Response response;

    in_stream >> response;

    EXPECT_TRUE(in_stream.fail());
}

// With no Content-Length, nothing after the headers is the body.
TEST(ResponseTest, StreamWithoutContentLengthHasNoBody) {
    std::istringstream in_stream("HTTP/1.1 200 OK\r\n\r\nnot the body");
    Response response;
    response.set_body("old body");

    in_stream >> response;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(response.body(), "");
}

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

// Responses sent one after another, without waiting, are read one at a
// time, each with the body its length gives, and once the connection ends,
// nothing more is.
TEST(ResponseTest, ReadTakesOneResponseAtATime) {
    auto reader = connection_of(
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 12\r\n"
        "\r\n"
        "Hello World!"
        "HTTP/1.1 404 Not Found\r\n"
        "Content-Length: 4\r\n"
        "\r\n"
        "gone");
    const Response::Limits limits{};

    const auto first = Response::read(reader, limits);
    const auto second = Response::read(reader, limits);

    ASSERT_TRUE(first.has_value());
    EXPECT_EQ(first->version(), "HTTP/1.1");
    EXPECT_EQ(first->status_code(), 200);
    EXPECT_EQ(first->reason_phrase(), "OK");
    EXPECT_EQ(first->headers().get("content-type"), "text/plain");
    EXPECT_EQ(first->body(), "Hello World!");
    ASSERT_TRUE(second.has_value());
    EXPECT_EQ(second->status_code(), 404);
    EXPECT_EQ(second->reason_phrase(), "Not Found");
    EXPECT_EQ(second->body(), "gone");
    EXPECT_FALSE(Response::read(reader, limits).has_value());
}

// A response is read off a socket as it arrives, and what arrived after it
// is read as the next.
TEST(ResponseTest, ReadFromASocket) {
    constexpr auto timeout = std::chrono::seconds(5);
    auto listener = ISocket::bind(Address("127.0.0.1", 0));

    ASSERT_NE(listener, nullptr);
    listener->listen(1);

    auto client = ISocket::connect(listener->local_address());
    auto server = listener->accept();

    ASSERT_NE(client, nullptr);
    ASSERT_NE(server, nullptr);
    client->set_receive_timeout(timeout);
    server->send_all(
        "HTTP/1.1 201 Created\r\n"
        "Content-Length: 3\r\n"
        "\r\n"
        "new"
        "HTTP/1.1 204 No Content\r\n"
        "\r\n");
    server->shutdown();

    MeteredReader reader(std::make_unique<BufferedReader>(std::make_unique<SocketReader>(*client)),
                         0);
    const Response::Limits limits{};

    const auto created = Response::read(reader, limits);
    const auto no_content = Response::read(reader, limits);

    ASSERT_TRUE(created.has_value());
    EXPECT_EQ(created->status_code(), 201);
    EXPECT_EQ(created->body(), "new");
    ASSERT_TRUE(no_content.has_value());
    EXPECT_EQ(no_content->status_code(), 204);
    EXPECT_EQ(no_content->body(), "");
    EXPECT_FALSE(Response::read(reader, limits).has_value());
}

// Lines may end with \n alone, and a header may go on to the next line.
TEST(ResponseTest, ReadTakesBareLineFeedsAndFoldedHeaders) {
    auto reader = connection_of(
        "HTTP/1.1 200 OK\n"
        "X-Folded: one\r\n"
        "  two\n"
        "Content-Length: 2\n"
        "\n"
        "hi");

    const auto response = Response::read(reader, Response::Limits{});

    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->headers().get("X-Folded"), "one two");
    EXPECT_EQ(response->body(), "hi");
}

// Without a length, the body is empty, and what follows the headers is the
// next response.
TEST(ResponseTest, ReadWithoutContentLengthHasNoBody) {
    auto reader = connection_of(
        "HTTP/1.1 200 OK\r\n"
        "\r\n"
        "HTTP/1.1 202 Accepted\r\n"
        "\r\n");
    const Response::Limits limits{};

    const auto first = Response::read(reader, limits);
    const auto second = Response::read(reader, limits);

    ASSERT_TRUE(first.has_value());
    EXPECT_EQ(first->body(), "");
    ASSERT_TRUE(second.has_value());
    EXPECT_EQ(second->status_code(), 202);
}

// A status that has no body has none, whatever length its headers give, so
// that the next response is read where it starts.
TEST(ResponseTest, ReadOfABodilessStatusIgnoresItsLength) {
    auto reader = connection_of(
        "HTTP/1.1 100 Continue\r\n"
        "Content-Length: 5\r\n"
        "\r\n"
        "HTTP/1.1 304 Not Modified\r\n"
        "Content-Length: 5\r\n"
        "Transfer-Encoding: chunked\r\n"
        "\r\n"
        "HTTP/1.1 200 OK\r\n"
        "Content-Length: 5\r\n"
        "\r\n"
        "hello");
    const Response::Limits limits{};

    const auto informational = Response::read(reader, limits);
    const auto not_modified = Response::read(reader, limits);
    const auto with_body = Response::read(reader, limits);

    ASSERT_TRUE(informational.has_value());
    EXPECT_EQ(informational->status_code(), 100);
    EXPECT_EQ(informational->body(), "");
    ASSERT_TRUE(not_modified.has_value());
    EXPECT_EQ(not_modified->status_code(), 304);
    EXPECT_EQ(not_modified->body(), "");
    ASSERT_TRUE(with_body.has_value());
    EXPECT_EQ(with_body->body(), "hello");
}

// A connection that ends before the response is all sent leaves nothing
// read.
TEST(ResponseTest, ReadOfAResponseCutShortIsNothing) {
    for (const auto* const text : {
             "",
             "HTTP/1.1 200 OK",
             "HTTP/1.1 200 OK\r\nContent-Length: 5\r\n",
             "HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhel",
         }) {
        auto reader = connection_of(text);

        EXPECT_FALSE(Response::read(reader, Response::Limits{}).has_value()) << text;
    }
}

// Each EXPECT_THROW counts toward the complexity, as branches would.
// NOLINTBEGIN(readability-function-cognitive-complexity)
TEST(ResponseTest, ReadOfABadStatusLineThrows) {
    for (const auto* const text : {"\r\n\r\n", "HTTP/1.1\r\n\r\n", "HTTP/1.1 OK 200\r\n\r\n"}) {
        auto reader = connection_of(text);

        EXPECT_THROW((void)Response::read(reader, Response::Limits{}), std::invalid_argument)
            << text;
    }
}

// A length is digits alone (RFC 9110 §8.6), as anything else leaves where
// the body ends in doubt.
TEST(ResponseTest, ReadOfALengthNotDigitsThrows) {
    for (const auto* const length : {"", "five", "-5", "+5", "5 5", "5, 5", "0x5"}) {
        auto reader = connection_of(std::string("HTTP/1.1 200 OK\r\nContent-Length: ") + length
                                    + "\r\n\r\nhello");

        EXPECT_THROW((void)Response::read(reader, Response::Limits{}), std::invalid_argument)
            << length;
    }
}
// NOLINTEND(readability-function-cognitive-complexity)

// A body sent in chunks, or otherwise encoded, is not read, as where it ends
// would not be known.
TEST(ResponseTest, ReadOfATransferEncodingThrows) {
    auto reader = connection_of(
        "HTTP/1.1 200 OK\r\n"
        "Transfer-Encoding: chunked\r\n"
        "\r\n"
        "5\r\nhello\r\n0\r\n\r\n");

    EXPECT_THROW((void)Response::read(reader, Response::Limits{}), std::invalid_argument);
}

// A header line is turned down as Headers turns it down.
TEST(ResponseTest, ReadOfABadHeaderLineThrows) {
    auto reader = connection_of("HTTP/1.1 200 OK\r\nno colon here\r\n\r\n");

    EXPECT_THROW((void)Response::read(reader, Response::Limits{}), std::runtime_error);
}

// The status line and headers may take as many bytes as the limit allows,
// line ends included, and no more.
TEST(ResponseTest, ReadOfAHeadLongerThanTheLimitThrows) {
    const std::string head = "HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n";
    auto fits = connection_of(head);
    auto too_long = connection_of(head);
    auto line_too_long = connection_of(head);

    EXPECT_TRUE(Response::read(fits, Response::Limits{head.size(), 0}).has_value());
    EXPECT_THROW((void)Response::read(too_long, Response::Limits{head.size() - 1, 0}),
                 LimitExceeded);
    EXPECT_THROW((void)Response::read(line_too_long, Response::Limits{4, 0}), LimitExceeded);
}

// A body larger than the limit is turned down before any of it is read.
TEST(ResponseTest, ReadOfABodyLargerThanTheLimitThrowsBeforeReadingIt) {
    constexpr std::size_t body_bytes = 6;
    const std::string head = "HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\n";
    auto fits = connection_of(head + "hello!");
    auto too_large = connection_of(head + "hello!");

    const auto response = Response::read(fits, Response::Limits{head.size(), body_bytes});

    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->body(), "hello!");
    EXPECT_THROW((void)Response::read(too_large, Response::Limits{head.size(), body_bytes - 1}),
                 LimitExceeded);
    EXPECT_EQ(too_large.bytes_read(), head.size());
}

// A length too large to hold is larger than any limit.
TEST(ResponseTest, ReadOfALengthOutOfRangeThrows) {
    auto reader = connection_of(
        "HTTP/1.1 200 OK\r\nContent-Length: 99999999999999999999999\r\n\r\nhello");

    EXPECT_THROW((void)Response::read(reader, Response::Limits{}), LimitExceeded);
}

// NOLINTEND(bugprone-unchecked-optional-access)
