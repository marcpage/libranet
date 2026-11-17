#pragma once

#if __APPLE__

#include <cstdint>
#include <utility>

#include <CoreFoundation/CoreFoundation.h>

namespace crypto {

template<typename CFType>
class CFObject {
public:
    enum class Policy : std::uint8_t {
        Retain,
        TakeOwnership,
    };

    constexpr CFObject() noexcept;
    explicit CFObject(CFType object, Policy policy) noexcept;

    // A CFObject passes as the object it holds to Core Foundation's calls,
    // which take the object itself.
    // NOLINTNEXTLINE(*-explicit-constructor)
    operator CFType() const noexcept;
    [[nodiscard]] CFType get() const noexcept;
    [[nodiscard]] CFType* put() noexcept;
    [[nodiscard]] CFType release() noexcept;
    void reset(CFType new_object, Policy policy) noexcept;
    [[nodiscard]] explicit operator bool() const noexcept;

    CFObject(const CFObject& other) noexcept;
    CFObject& operator=(const CFObject& other) noexcept;
    CFObject(CFObject&& other) noexcept;
    CFObject& operator=(CFObject&& other) noexcept;
    ~CFObject();

private:
    CFType m_object;
};

template<typename CFType>
constexpr CFObject<CFType>::CFObject() noexcept : m_object(nullptr) {}

template<typename CFType>
inline CFObject<CFType>::CFObject(CFType object, Policy policy) noexcept
    : m_object(object) {
    if (nullptr != m_object && policy == Policy::Retain) {
        CFRetain(m_object);
    }
}

template<typename CFType>
inline CFObject<CFType>::~CFObject() {
    reset(nullptr, Policy::TakeOwnership);
}

template<typename CFType>
inline CFObject<CFType>::CFObject(const CFObject& other) noexcept
    : m_object(other.m_object) {
    if (nullptr != m_object) {
        CFRetain(m_object);
    }
}

template<typename CFType>
inline CFObject<CFType>& CFObject<CFType>::operator=(const CFObject& other) noexcept {
    if (this != &other) {
        reset(other.m_object, Policy::Retain);
    }

    return *this;
}

template<typename CFType>
inline CFObject<CFType>::CFObject(CFObject&& other) noexcept : m_object(other.m_object) {
    other.m_object = nullptr;
}

template<typename CFType>
inline CFObject<CFType>& CFObject<CFType>::operator=(CFObject&& other) noexcept {
    if (this != &other) {
        reset(other.m_object, Policy::TakeOwnership);
        other.m_object = nullptr;
    }

    return *this;
}

template<typename CFType>
inline CFObject<CFType>::operator CFType() const noexcept {
    return m_object;
}

template<typename CFType>
inline CFType CFObject<CFType>::get() const noexcept {
    return m_object;
}

template<typename CFType>
inline CFType* CFObject<CFType>::put() noexcept {
    reset(nullptr, Policy::TakeOwnership); // Clean up any existing resource first
    return &m_object;
}

template<typename CFType>
inline CFType CFObject<CFType>::release() noexcept {
    CFType temp = m_object;

    m_object = nullptr;
    return temp;
}

template<typename CFType>
inline void CFObject<CFType>::reset(CFType new_object, Policy policy) noexcept {
    if (nullptr != new_object && policy == Policy::Retain) {
        CFRetain(new_object);
    }

    if (m_object) {
        CFRelease(m_object);
    }

    m_object = new_object;
}

template<typename CFType>
inline CFObject<CFType>::operator bool() const noexcept {
    return m_object != nullptr;
}

} // namespace crypto

#endif // __APPLE__
