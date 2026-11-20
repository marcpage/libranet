#pragma once

#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>
#include <string_view>

#include "ILogOutput.hpp"

namespace logger {

// Writes each line to a C stream, and flushes it, so that a line is not lost
// if the program then stops.
class StreamOutput : public ILogOutput {
public:
    // Whether the output closes the stream when it is done with it: a file
    // opened for it is closed, and stdout and stderr are left open.
    enum class Closing : std::uint8_t {
        Close,
        LeaveOpen,
    };

    // name says which stream it is, in what is thrown.
    StreamOutput(std::FILE* stream, std::string name, Closing closing);

    // Throws std::system_error when the stream cannot be written.
    void log(std::string_view line) override;

private:
    // Closes the stream, unless it is to be left open.
    class Closer {
    public:
        explicit Closer(Closing closing) noexcept;

        void operator()(std::FILE* stream) const noexcept;

    private:
        Closing m_closing;
    };

    std::string m_name;
    std::unique_ptr<std::FILE, Closer> m_stream;
};

} // namespace logger
