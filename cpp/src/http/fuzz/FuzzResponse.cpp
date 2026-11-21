// libFuzzer calls this with inputs it makes up, as a response read from a
// stream, and as responses read one after another off a connection.
// Reading from a stream turns down a response it cannot read by failing the
// stream, and Response::read() by throwing std::invalid_argument, or
// io::LimitExceeded for one larger than its limits; both turn down a header
// line with std::runtime_error or std::invalid_argument. A connection that
// ends first leaves Response::read() nothing to read. Anything else it does
// with an input, such as crash, hang, or throw something else, is a fault.
// A response either reads is written back out as text it reads back as the
// same response.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

#include "http/Response.hpp"
#include "io/BufferedReader.hpp"
#include "io/IReader.hpp"
#include "io/MeteredReader.hpp"

namespace {

// Limits small enough for made-up inputs to pass, so that the fuzzer reaches
// the code that turns them down, and a buffer small enough that lines and
// bodies are read across refills of it.
constexpr std::size_t small_bytes = 256;
constexpr http::Response::Limits small_limits{small_bytes, small_bytes};
constexpr std::size_t small_buffer = 16;

// A reader of the text it is given, as a connection's bytes.
class TextReader final : public io::IReader {
public:
    explicit TextReader(std::string text)
        : m_text(std::move(text)) {}

    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data,
                                                       std::size_t size) override {
        if (m_position >= m_text.size()) {
            return std::nullopt;
        }

        const auto count = std::min(size, m_text.size() - m_position);
        const auto first = m_text.begin() + static_cast<std::string::difference_type>(m_position);

        std::transform(first, first + static_cast<std::string::difference_type>(count), data,
                       [](char single_byte) {
            return static_cast<std::byte>(single_byte);
        });
        m_position += count;
        return count;
    }

private:
    std::string m_text;
    std::size_t m_position{0};
};

// A reader of the text as a connection's, as Response::read() is given one.
io::MeteredReader connection_of(std::string text) {
    return io::MeteredReader(std::make_unique<io::BufferedReader>(
                                 std::make_unique<TextReader>(std::move(text)), small_buffer),
                             0);
}

// The next response on the connection, or nothing once it ends or a
// response is turned down, which leaves nothing after it to read.
std::optional<http::Response> next(io::MeteredReader& reader) {
    try {
        return http::Response::read(reader, small_limits);
    } catch (const std::runtime_error&) { // io::LimitExceeded among them
        return std::nullopt;
    } catch (const std::invalid_argument&) {
        return std::nullopt;
    }
}

void read_from_a_stream(const std::string& input) {
    std::istringstream stream(input);
    http::Response response;

    try {
        if (!(stream >> response)) {
            return;
        }
    } catch (const std::runtime_error&) {
        return;
    } catch (const std::invalid_argument&) {
        return;
    }

    const std::string text = response.to_string();
    std::istringstream text_stream(text);
    http::Response reread;

    if (!(text_stream >> reread) || reread.to_string() != text) {
        std::cerr << "Response did not read back what it wrote: " << text << '\n';
        std::abort();
    }
}

// Each response read takes at least its status line, so the input runs out.
// What one is written as is read back with the default limits, as writing
// may lengthen its lines.
void read_from_a_connection(const std::string& input) {
    auto reader = connection_of(input);

    while (const auto response = next(reader)) {
        const std::string text = response->to_string();
        auto text_reader = connection_of(text);
        const auto reread = http::Response::read(text_reader, http::Response::Limits{});

        if (!reread || reread->to_string() != text) {
            std::cerr << "Response read off a connection did not read back what it wrote: "
                      << text << '\n';
            std::abort();
        }
    }
}

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    read_from_a_stream(input);
    read_from_a_connection(input);
    return 0;
}
