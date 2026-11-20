#include <gtest/gtest.h>
#include <cstdio>
#include <memory>
#include <system_error>

#include "Fixtures.hpp"
#include "logger/StreamOutput.hpp"

using fixtures::read_file;
using fixtures::ScratchFile;
using logger::StreamOutput;

namespace {

// Closes a stream that a test opened and kept.
struct StreamCloser {
    void operator()(std::FILE* stream) const noexcept {
        // This is the deleter of the std::unique_ptr that owns the stream.
        // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
        (void)std::fclose(stream);
    }
};

using Stream = std::unique_ptr<std::FILE, StreamCloser>;

// Opens the scratch file with mode, as fopen() takes it.
Stream open_stream(const ScratchFile& file, const char* mode) {
    // The stream goes straight into a std::unique_ptr, which closes it.
    // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
    return Stream(std::fopen(file.path().c_str(), mode));
}

} // namespace

TEST(StreamOutputTest, EachLineIsInTheFileOnceLogged) {
    const ScratchFile file;
    auto stream = open_stream(file, "we");
    ASSERT_NE(stream, nullptr);
    StreamOutput output(stream.release(), "scratch file", StreamOutput::Closing::Close);

    output.log("first\n");
    EXPECT_EQ(read_file(file.path()), "first\n");

    output.log("second\n");
    EXPECT_EQ(read_file(file.path()), "first\nsecond\n");
}

TEST(StreamOutputTest, StreamLeftOpenCanStillBeWritten) {
    const ScratchFile file;
    const auto stream = open_stream(file, "we");
    ASSERT_NE(stream, nullptr);

    {
        StreamOutput output(stream.get(), "scratch file", StreamOutput::Closing::LeaveOpen);
        output.log("from the output\n");
    }

    EXPECT_NE(std::fputs("after it\n", stream.get()), EOF);
    EXPECT_EQ(std::fflush(stream.get()), 0);
    EXPECT_EQ(read_file(file.path()), "from the output\nafter it\n");
}

TEST(StreamOutputTest, StreamThatCannotBeWrittenThrows) {
    const ScratchFile file;
    {
        const auto made = open_stream(file, "we");
        ASSERT_NE(made, nullptr);
    }
    auto stream = open_stream(file, "re");
    ASSERT_NE(stream, nullptr);
    StreamOutput output(stream.release(), "read-only file", StreamOutput::Closing::Close);

    EXPECT_THROW(output.log("refused\n"), std::system_error);
}
