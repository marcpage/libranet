#pragma once

#include <string>

namespace msg {

class IMessage {
public:
    using Ptr = std::shared_ptr<IMessage>;

    IMessage()=default;
    virtual ~IMessage()=default;

    [[nodiscard]] virtual std::string to_string() const=0;
};

} // namespace msg
