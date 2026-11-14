#pragma once

#include <chrono>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <queue>
#include <utility>

namespace types {

template<typename T>
class BlockingQueue {
public:
    using Milliseconds = std::chrono::milliseconds;
    static constexpr size_t Unbounded = 0;

    explicit BlockingQueue(size_t capacity = Unbounded);
    ~BlockingQueue() = default;

    // non-copyable, movable
    BlockingQueue(const BlockingQueue&) = delete;
    BlockingQueue& operator=(const BlockingQueue&) = delete;
    BlockingQueue(BlockingQueue&&) = default;
    BlockingQueue& operator=(BlockingQueue&&) = default;

    void push(const T& item);
    void push(T&& item);

    [[nodiscard]] bool push_ms_timeout(const T& item, Milliseconds timeout);
    [[nodiscard]] bool push_ms_timeout(T&& item, Milliseconds timeout);

    T pop();
    [[nodiscard]] std::optional<T> pop_ms_timeout(Milliseconds timeout);

    [[nodiscard]] size_t size() const;
    [[nodiscard]] bool empty() const;

private:
    template<typename U>
    void core_push(U&& item);

    template<typename U>
    bool core_push_ms_timeout(U&& item, Milliseconds timeout);

    std::queue<T> m_queue;
    mutable std::mutex m_mutex;
    std::condition_variable m_notEmpty;
    std::condition_variable m_notFull;
    const size_t m_capacity;
};

template<typename T>
BlockingQueue<T>::BlockingQueue(size_t capacity)
    : m_capacity(capacity) {}

template<typename T>
template<typename U>
void BlockingQueue<T>::core_push(U&& item) {
    std::unique_lock lock(m_mutex);

    m_notFull.wait(lock, [this] {
        return m_capacity == 0 || m_queue.size() < m_capacity;
    });

    m_queue.push(std::forward<U>(item));
    lock.unlock();
    m_notEmpty.notify_one();
}

template<typename T>
void BlockingQueue<T>::push(const T& item) {
    core_push(item);
}

template<typename T>
void BlockingQueue<T>::push(T&& item) {
    core_push(std::move(item));
}

template<typename T>
template<typename U>
bool BlockingQueue<T>::core_push_ms_timeout(U&& item, Milliseconds timeout) {
    std::unique_lock lock(m_mutex);

    if (!m_notFull.wait_for(lock, timeout, [this] {
        return m_capacity == 0 || m_queue.size() < m_capacity;
    })) {
        return false;
    }

    m_queue.push(std::forward<U>(item));
    lock.unlock();
    m_notEmpty.notify_one();
    return true;
}

template<typename T>
bool BlockingQueue<T>::push_ms_timeout(const T& item, Milliseconds timeout) {
    return core_push_ms_timeout(item, timeout);
}

template<typename T>
bool BlockingQueue<T>::push_ms_timeout(T&& item, Milliseconds timeout) {
    return core_push_ms_timeout(std::move(item), timeout);
}

template<typename T>
T BlockingQueue<T>::pop() {
    std::unique_lock lock(m_mutex);

    m_notEmpty.wait(lock, [this] { return !m_queue.empty(); });

    T item = std::move(m_queue.front());
    m_queue.pop();
    lock.unlock();
    m_notFull.notify_one();
    return item;
}

template<typename T>
std::optional<T> BlockingQueue<T>::pop_ms_timeout(Milliseconds timeout) {
    std::unique_lock lock(m_mutex);

    if (!m_notEmpty.wait_for(lock, timeout, [this] { return !m_queue.empty(); })) {
        return std::nullopt;
    }

    T item = std::move(m_queue.front());
    m_queue.pop();
    lock.unlock();
    m_notFull.notify_one();
    return item;
}

template<typename T>
size_t BlockingQueue<T>::size() const {
    const std::scoped_lock lock(m_mutex);
    return m_queue.size();
}

template<typename T>
bool BlockingQueue<T>::empty() const {
    const std::scoped_lock lock(m_mutex);
    return m_queue.empty();
}

} // namespace types
