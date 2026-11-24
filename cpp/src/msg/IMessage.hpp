#pragma once

#include <string>

namespace msg {

class IMessage {
public:
    using Ptr = std::shared_ptr<IMessage>;

    [[nodiscard]] virtual std::string to_string() const = 0;

    virtual ~IMessage() = default;

protected:
    IMessage() = default;

    // A message is copied or moved only as its own type: through IMessage,
    // it would lose all but the IMessage part.
    IMessage(const IMessage&) = default;
    IMessage& operator=(const IMessage&) = default;
    IMessage(IMessage&&) noexcept = default;
    IMessage& operator=(IMessage&&) noexcept = default;
};

} // namespace msg
