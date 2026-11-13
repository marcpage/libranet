#pragma once

#include <string>
#include <type_traits>
#include <utility>

#include "IMessage.hpp"

namespace msg {

template<typename Type>
class Message : public IMessage {
public:
    explicit Message(Type value);
    ~Message() override = default;
    [[nodiscard]] std::string to_string() const override;
    [[nodiscard]] const Type& value() const;

private:
    Type m_value;
};

template<typename Type>
Message<Type>::Message(Type value)
    : m_value(std::move(value)) {}

template<typename Type>
std::string Message<Type>::to_string() const {
    if constexpr (std::is_constructible_v<std::string, const Type&>) {
        return std::string(m_value);
    } else if constexpr (std::is_arithmetic_v<Type>) {
        return std::to_string(m_value);
    } else { // fallback or static_assert
        static_assert(std::is_constructible_v<std::string, const Type&>,
                      "Type must be convertible to std::string");
        return {};
    }
}

template<typename Type>
const Type& Message<Type>::value() const {
    return m_value;
}

} // namespace msg
