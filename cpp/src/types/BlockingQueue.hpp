#pragma once

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <optional>
#include <queue>
#include <utility>

namespace types {

template<typename Type>
class BlockingQueue {
public:
    using Milliseconds = std::chrono::milliseconds;
    static constexpr std::size_t unbounded = 0;

    explicit BlockingQueue(std::size_t capacity = unbounded);

    void push(const Type& item);
    void push(Type&& item);

    [[nodiscard]] bool push_ms_timeout(const Type& item, Milliseconds timeout);
    [[nodiscard]] bool push_ms_timeout(Type&& item, Milliseconds timeout);

    Type pop();
    [[nodiscard]] std::optional<Type> pop_ms_timeout(Milliseconds timeout);

    [[nodiscard]] std::size_t size() const;
    [[nodiscard]] bool empty() const;

    ~BlockingQueue() = default;

    BlockingQueue(const BlockingQueue&) = delete;
    BlockingQueue& operator=(const BlockingQueue&) = delete;
    BlockingQueue(BlockingQueue&&) = delete;
    BlockingQueue& operator=(BlockingQueue&&) = delete;

private:
    std::queue<Type> m_queue;
    mutable std::mutex m_mutex;
    std::condition_variable m_not_empty;
    std::condition_variable m_not_full;
    // Set once, as the queue is made; its mutex and condition variables
    // already keep it from being copied or assigned.
    // NOLINTNEXTLINE(cppcoreguidelines-avoid-const-or-ref-data-members)
    const std::size_t m_capacity;

    template<typename Item>
    void _core_push(Item&& item);

    template<typename Item>
    bool _core_push_ms_timeout(Item&& item, Milliseconds timeout);
};

template<typename Type>
BlockingQueue<Type>::BlockingQueue(std::size_t capacity)
    : m_capacity(capacity) {}

template<typename Type>
template<typename Item>
void BlockingQueue<Type>::_core_push(Item&& item) {
    std::unique_lock lock(m_mutex);

    m_not_full.wait(lock, [this] {
        return 0 == m_capacity || m_queue.size() < m_capacity;
    });

    m_queue.push(std::forward<Item>(item));
    lock.unlock();
    m_not_empty.notify_one();
}

template<typename Type>
void BlockingQueue<Type>::push(const Type& item) {
    _core_push(item);
}

template<typename Type>
void BlockingQueue<Type>::push(Type&& item) {
    _core_push(std::move(item));
}

template<typename Type>
template<typename Item>
bool BlockingQueue<Type>::_core_push_ms_timeout(Item&& item, Milliseconds timeout) {
    std::unique_lock lock(m_mutex);

    if (!m_not_full.wait_for(lock, timeout, [this] {
        return 0 == m_capacity || m_queue.size() < m_capacity;
    })) {
        return false;
    }

    m_queue.push(std::forward<Item>(item));
    lock.unlock();
    m_not_empty.notify_one();
    return true;
}

template<typename Type>
bool BlockingQueue<Type>::push_ms_timeout(const Type& item, Milliseconds timeout) {
    return _core_push_ms_timeout(item, timeout);
}

template<typename Type>
bool BlockingQueue<Type>::push_ms_timeout(Type&& item, Milliseconds timeout) {
    return _core_push_ms_timeout(std::move(item), timeout);
}

template<typename Type>
Type BlockingQueue<Type>::pop() {
    std::unique_lock lock(m_mutex);

    m_not_empty.wait(lock, [this] { return !m_queue.empty(); });

    Type item = std::move(m_queue.front());
    m_queue.pop();
    lock.unlock();
    m_not_full.notify_one();
    return item;
}

template<typename Type>
std::optional<Type> BlockingQueue<Type>::pop_ms_timeout(Milliseconds timeout) {
    std::unique_lock lock(m_mutex);

    if (!m_not_empty.wait_for(lock, timeout, [this] { return !m_queue.empty(); })) {
        return std::nullopt;
    }

    Type item = std::move(m_queue.front());
    m_queue.pop();
    lock.unlock();
    m_not_full.notify_one();
    return item;
}

template<typename Type>
std::size_t BlockingQueue<Type>::size() const {
    const std::scoped_lock lock(m_mutex);
    return m_queue.size();
}

template<typename Type>
bool BlockingQueue<Type>::empty() const {
    const std::scoped_lock lock(m_mutex);
    return m_queue.empty();
}

} // namespace types
