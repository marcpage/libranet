#include "Channel.hpp"

#include <algorithm>
#include <cstddef>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "IMessage.hpp"

namespace msg {

std::map<std::string, std::vector<Channel*>> Channel::m_channels;
std::mutex Channel::m_channel_lock;

Channel::Channel(const std::string& name)
    : Channel(std::vector<std::string>{name}) {}

Channel::Channel(std::vector<std::string> names)
    : m_names(std::move(names)) {
    if (m_names.empty()) {
        throw std::invalid_argument("A channel must listen on at least one name");
    }

    std::sort(m_names.begin(), m_names.end());
    m_names.erase(std::unique(m_names.begin(), m_names.end()), m_names.end());

    const std::scoped_lock lock(m_channel_lock);

    for (const auto& name : m_names) {
        m_channels[name].push_back(this);
    }
}

Channel::~Channel() {
    const std::scoped_lock lock(m_channel_lock);

    for (const auto& name : m_names) {
        const auto entry = m_channels.find(name);

        if (entry == m_channels.end()) {
            continue;
        }

        auto& channel_list = entry->second;

        channel_list.erase(std::remove(channel_list.begin(), channel_list.end(), this),
                           channel_list.end());

        if (channel_list.empty()) {
            m_channels.erase(entry);
        }
    }
}

bool Channel::empty() const {
    return m_message_queue.empty();
}

size_t Channel::size() const {
    return m_message_queue.size();
}

std::optional<IMessage::Ptr> Channel::next_message(Milliseconds timeout) {
    if (timeout <= wait_forever) {
        return m_message_queue.pop();
    }

    return m_message_queue.pop(timeout);
}

void Channel::post_message(const std::string& name, const IMessage::Ptr& message) {
    const std::scoped_lock lock(m_channel_lock);
    const auto entry = m_channels.find(name);

    if (entry == m_channels.end()) {
        return; // no subscribers
    }

    for (Channel* channel : entry->second) {
        channel->m_message_queue.push(message);
    }
}

} // namespace msg
