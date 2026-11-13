#include "Channel.hpp"

namespace msg {

std::map<std::string, std::vector<Channel*>> Channel::m_channels;
std::mutex Channel::m_channel_lock;

Channel::Channel(const std::string &name) {
    std::lock_guard lock(m_channel_lock);

    m_channels[name].push_back(this);
}

Channel::~Channel() {
    std::lock_guard lock(m_channel_lock);

    for (auto& [name, channelList] : m_channels) {
        auto it = std::find(channelList.begin(), channelList.end(), this);

        if (it != channelList.end()) {
            channelList.erase(it);

            if (channelList.empty()) {
                m_channels.erase(name);
            }

            return;
        }
    }
}

bool Channel::empty() const {
    return m_message_queue.empty();
}

size_t Channel::size() const {
    return m_message_queue.size();
}

std::optional<IMessage::Ptr>  Channel::next_message(Milliseconds timeout) {
    if (timeout <= WaitForever) {
        return m_message_queue.pop();
    }

    return m_message_queue.pop_ms_timeout(timeout);
}

void Channel::post_message(const std::string &name, IMessage::Ptr message) {
    std::lock_guard lock(m_channel_lock);
    auto it = m_channels.find(name);

    if (it == m_channels.end()) {
        return;   // no subscribers
    }

    for (Channel* channel : it->second) {
        channel->m_message_queue.push(message);
    }
}

} // namespace msg
