#pragma once

#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace http {

class Query {
public:
    static Query parse(std::string_view query_string);

    Query() = default;
    ~Query() = default;
    Query(const Query&) = default;
    Query& operator=(const Query&) = default;
    Query(Query&&) noexcept = default;
    Query& operator=(Query&&) noexcept = default;

    std::optional<std::string_view> get_first(std::string_view key) const;
    void set(std::string_view key, std::string_view value);

    std::string& operator[](std::string_view key);

    std::vector<std::string_view> get_all(std::string_view key) const;
    void add(std::string_view key, std::string_view value);
    void remove(std::string_view key);
    void clear() noexcept;
    bool empty() const noexcept;

    std::vector<std::pair<std::string, std::string>> items() const;

    std::string to_string() const;

    friend std::istream& operator>>(std::istream& in_stream, Query& query);
    friend std::ostream& operator<<(std::ostream& out_stream, const Query& query);

private:
    std::vector<std::pair<std::string, std::string>> m_parameters;
};

inline std::string& Query::operator[](std::string_view key) {
    for (auto& [k, v] : m_parameters) {
        if (k == key) {
            return v;
        }
    }

    m_parameters.emplace_back(std::string(key), std::string());
    return m_parameters.back().second;
}

inline std::vector<std::pair<std::string, std::string>> Query::items() const {
    return m_parameters;
}

inline std::vector<std::string_view> Query::get_all(std::string_view key) const {
    std::vector<std::string_view> values;

    for (const auto& [k, v] : m_parameters) {
        if (k == key) {
            values.push_back(v);
        }
    }

    return values;
}

inline std::optional<std::string_view> Query::get_first(std::string_view key) const {
    for (const auto& [k, v] : m_parameters) {
        if (k == key) {
            return v;
        }
    }

    return std::nullopt;
}

inline void Query::set(std::string_view key, std::string_view value) {
    bool found = false;

    for (auto it = m_parameters.begin(); it != m_parameters.end();) {
        if (it->first == key) {
            if (!found) {
                it->second = std::string(value);
                found = true;
                ++it;
            } else { // remove duplicates
                it = m_parameters.erase(it);
            }
        } else {
            ++it;
        }
    }

    if (!found) {
        m_parameters.emplace_back(std::string(key), std::string(value));
    }
}

inline void Query::add(std::string_view key, std::string_view value) {
    m_parameters.emplace_back(std::string(key), std::string(value));
}

inline void Query::remove(std::string_view key) {
    for (auto it = m_parameters.begin(); it != m_parameters.end();) {
        if (it->first == key) {
            it = m_parameters.erase(it);
        } else {
            ++it;
        }
    }
}

inline void Query::clear() noexcept {
    m_parameters.clear();
}

inline bool Query::empty() const noexcept {
    return m_parameters.empty();
}

inline Query Query::parse(std::string_view query_string) {
    Query result;

    // Strip leading '?' if present
    if (!query_string.empty() && query_string.front() == '?') {
        query_string.remove_prefix(1);
    }

    std::string_view query = query_string;

    while (!query.empty()) {
        const auto amp_pos = query.find('&');
        const auto pair = (amp_pos == std::string_view::npos)
                              ? query
                              : query.substr(0, amp_pos);

        if (!pair.empty()) {
            const auto eq_pos = pair.find('=');

            if (eq_pos == std::string_view::npos) {
                result.m_parameters.emplace_back(std::string(pair), std::string());
            } else {
                result.m_parameters.emplace_back(
                    std::string(pair.substr(0, eq_pos)),
                    std::string(pair.substr(eq_pos + 1)));
            }
        }

        if (amp_pos == std::string_view::npos) {
            break;
        }

        query.remove_prefix(amp_pos + 1);
    }

    return result;
}

inline std::string Query::to_string() const {
    std::string result;

    for (std::size_t parameter_index = 0; parameter_index < m_parameters.size();
         ++parameter_index) {
        if (parameter_index > 0) {
            result += "&";
        }

        result += m_parameters[parameter_index].first;

        if (!m_parameters[parameter_index].second.empty()) {
            result += "=" + m_parameters[parameter_index].second;
        }
    }
    return result;
}

inline std::istream& operator>>(std::istream& in_stream, Query& query) {
    std::string line;

    if (!(in_stream >> line)) {
        return in_stream;
    }

    query = Query::parse(line);
    return in_stream;
}

inline std::ostream& operator<<(std::ostream& out_stream, const Query& query) {
    out_stream << query.to_string();
    return out_stream;
}

} // namespace http
