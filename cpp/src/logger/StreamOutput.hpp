#pragma once

#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>
#include <string_view>

#include "ILogOutput.hpp"

namespace logger {

class StreamOutput : public ILogOutput {
public:
    enum class Closing : std::uint8_t {
        Close,
        LeaveOpen,
    };

    StreamOutput(std::FILE* stream, std::string name, Closing closing);

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
