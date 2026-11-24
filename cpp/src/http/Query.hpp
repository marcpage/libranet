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
    [[nodiscard]] static Query parse(std::string_view query_string);

    [[nodiscard]] std::optional<std::string_view> first(std::string_view key) const;
    void set(std::string_view key, std::string_view value);

    std::string& operator[](std::string_view key);

    [[nodiscard]] std::vector<std::string_view> all(std::string_view key) const;
    void add(std::string_view key, std::string_view value);
    void remove(std::string_view key);
    void clear() noexcept;
    [[nodiscard]] bool empty() const noexcept;

    [[nodiscard]] const std::vector<std::pair<std::string, std::string>>& items() const;

    [[nodiscard]] std::string to_string() const;

    friend std::istream& operator>>(std::istream& in_stream, Query& query);
    friend std::ostream& operator<<(std::ostream& out_stream, const Query& query);

    Query() = default;
    ~Query() = default;
    Query(const Query&) = default;
    Query& operator=(const Query&) = default;
    Query(Query&&) noexcept = default;
    Query& operator=(Query&&) noexcept = default;

private:
    std::vector<std::pair<std::string, std::string>> m_parameters;
};

inline std::string& Query::operator[](std::string_view key) {
    for (auto& [name, value] : m_parameters) {
        if (name == key) {
            return value;
        }
    }

    m_parameters.emplace_back(std::string(key), std::string());
    return m_parameters.back().second;
}

inline const std::vector<std::pair<std::string, std::string>>& Query::items() const {
    return m_parameters;
}

inline std::vector<std::string_view> Query::all(std::string_view key) const {
    std::vector<std::string_view> values;

    for (const auto& [name, value] : m_parameters) {
        if (name == key) {
            values.push_back(value);
        }
    }

    return values;
}

inline std::optional<std::string_view> Query::first(std::string_view key) const {
    for (const auto& [name, value] : m_parameters) {
        if (name == key) {
            return value;
        }
    }

    return std::nullopt;
}

inline void Query::set(std::string_view key, std::string_view value) {
    bool found = false;

    for (auto entry = m_parameters.begin(); entry != m_parameters.end();) {
        if (entry->first == key) {
            if (!found) {
                entry->second = std::string(value);
                found = true;
                ++entry;
            } else { // remove duplicates
                entry = m_parameters.erase(entry);
            }
        } else {
            ++entry;
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
    for (auto entry = m_parameters.begin(); entry != m_parameters.end();) {
        if (entry->first == key) {
            entry = m_parameters.erase(entry);
        } else {
            ++entry;
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
    if (!query_string.empty() && '?' == query_string.front()) {
        query_string.remove_prefix(1);
    }

    std::string_view query = query_string;

    while (!query.empty()) {
        const auto ampersand_position = query.find('&');
        const auto pair = (std::string_view::npos == ampersand_position)
                              ? query
                              : query.substr(0, ampersand_position);

        if (!pair.empty()) {
            const auto equals_position = pair.find('=');

            if (std::string_view::npos == equals_position) {
                result.m_parameters.emplace_back(std::string(pair), std::string());
            } else {
                result.m_parameters.emplace_back(
                    std::string(pair.substr(0, equals_position)),
                    std::string(pair.substr(equals_position + 1)));
            }
        }

        if (std::string_view::npos == ampersand_position) {
            break;
        }

        query.remove_prefix(ampersand_position + 1);
    }

    return result;
}

inline std::string Query::to_string() const {
    std::string result;

    for (std::size_t parameter_index = 0; parameter_index < m_parameters.size();
         ++parameter_index) {
        if (parameter_index > 0) {
            result += '&';
        }

        result += m_parameters.at(parameter_index).first;

        // A name alone needs no '='; an empty name does, or it would be lost.
        if (!m_parameters.at(parameter_index).second.empty()
            || m_parameters.at(parameter_index).first.empty()) {
            result += "=" + m_parameters.at(parameter_index).second;
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
