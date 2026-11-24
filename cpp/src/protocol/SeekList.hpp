#pragma once

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "logger/Logger.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Json.hpp"

namespace protocol {

// What a node seeks and does not hold (HttpApi §10.7.1): content by
// identifier, as "data", and hash prefixes, as "search", each in the order
// given.
class SeekList {
public:
    using Data = std::vector<std::byte>;

    // As it is sent, compressed or not (HttpApi §10.7.1).
    static constexpr std::size_t max_bytes = 1 * types::Megabyte;

    // JSON, or one zlib stream of JSON of no more than decompressed_limit
    // bytes. An entry that is not valid is left out, and logged. Throws
    // std::invalid_argument for a body that is neither, or not a seek list.
    [[nodiscard]] static SeekList read(const Data& body, std::size_t decompressed_limit);

    SeekList() = default;
    SeekList(std::vector<storage::Identifier> data, std::vector<storage::HashPrefix> search);

    [[nodiscard]] const std::vector<storage::Identifier>& data() const noexcept;
    [[nodiscard]] const std::vector<storage::HashPrefix>& search() const noexcept;

    // Fewer than limit bytes, compressed only where the JSON would not fit,
    // leaving out from the end alike of each kind what does not fit even then.
    // Throws std::invalid_argument for a limit too small for an empty list.
    [[nodiscard]] Data write(std::size_t limit = max_bytes) const;

private:
    static constexpr std::string_view data_key = "data";
    static constexpr std::string_view search_key = "search";
    // HttpApi §10.7.1 leaves it to the node. A list may be compressed many
    // times to find how much fits, and is hardly smaller at a slower level.
    static constexpr int compression_level = storage::Zlib::balance_level;

    std::vector<storage::Identifier> m_data;
    std::vector<storage::HashPrefix> m_search;

    [[nodiscard]] static types::Json _decode(const Data& body, std::size_t decompressed_limit);
    [[nodiscard]] static std::optional<types::Json> _parsed(std::string_view text);
    template<typename Entry, typename Parse>
    [[nodiscard]] static std::vector<Entry> _entries(const types::Json& list,
                                                     std::string_view key, Parse parse);
    template<typename Entry, typename Parse>
    [[nodiscard]] static std::optional<Entry> _entry(const types::Json& value, Parse parse);
    [[nodiscard]] static storage::Identifier _identifier(std::string_view text);
    [[nodiscard]] Data _longest_within(std::size_t too_many, std::size_t limit) const;
    [[nodiscard]] Data _body(std::size_t count, std::size_t limit) const;
    [[nodiscard]] types::Json _json(std::size_t count) const;
    template<typename Entry>
    [[nodiscard]] static types::Json::Array _texts(const std::vector<Entry>& entries,
                                                   std::size_t count);
    [[nodiscard]] static Data _bytes(std::string_view text);
    [[nodiscard]] static std::string _text(const Data& body);
};

inline SeekList SeekList::read(const Data& body, std::size_t decompressed_limit) {
    const auto list = _decode(body, decompressed_limit);

    if (!list.is_object()) {
        throw std::invalid_argument("A seek list must be a JSON object");
    }

    auto data = _entries<storage::Identifier>(list, data_key, &SeekList::_identifier);
    auto search = _entries<storage::HashPrefix>(list, search_key, &storage::HashPrefix::parse);

    return SeekList(std::move(data), std::move(search));
}

inline SeekList::SeekList(std::vector<storage::Identifier> data,
                          std::vector<storage::HashPrefix> search)
    : m_data(std::move(data))
    , m_search(std::move(search)) {
}

inline const std::vector<storage::Identifier>& SeekList::data() const noexcept {
    return m_data;
}

inline const std::vector<storage::HashPrefix>& SeekList::search() const noexcept {
    return m_search;
}

inline SeekList::Data SeekList::write(std::size_t limit) const {
    const auto all = std::max(m_data.size(), m_search.size());
    auto body = _body(all, limit);

    if (body.size() < limit) {
        return body;
    }

    return _longest_within(all, limit);
}

// The JSON body holds, as it is or zlib-compressed. Throws
// std::invalid_argument for a body that is neither JSON nor one zlib stream
// of JSON within decompressed_limit.
inline types::Json SeekList::_decode(const Data& body, std::size_t decompressed_limit) {
    if (auto json = _parsed(_text(body))) {
        return std::move(*json);
    }

    const auto decompressed = storage::Zlib::decompress(body, decompressed_limit);

    if (!decompressed) {
        throw std::invalid_argument(
            "A seek list must be JSON, or one zlib stream of JSON of no more than "
            + std::to_string(decompressed_limit) + " bytes");
    }

    return types::Json::parse(_text(*decompressed));
}

// The JSON text is, or nothing when it is not JSON, as a compressed list is
// not.
inline std::optional<types::Json> SeekList::_parsed(std::string_view text) {
    try {
        return types::Json::parse(text);
    } catch (const std::invalid_argument&) {
        return std::nullopt;
    }
}

// The entries of list's member key, each read with parse, or none when list
// has no such member. Those parse cannot read are left out, and logged
// together. Throws std::invalid_argument for a member that is not an array.
template<typename Entry, typename Parse>
inline std::vector<Entry> SeekList::_entries(const types::Json& list, std::string_view key,
                                             Parse parse) {
    const std::string name(key);
    std::vector<Entry> entries;

    if (!list.has(name)) {
        return entries;
    }

    const auto& values = list.at(name);

    if (!values.is_array()) {
        throw std::invalid_argument("A seek list's \"" + name + "\" must be an array");
    }

    for (const auto& value : values.array()) {
        if (auto entry = _entry<Entry>(value, parse)) {
            entries.push_back(std::move(*entry));
        }
    }

    if (entries.size() < values.size()) {
        lDebug << "Left out" << values.size() - entries.size() << "of the" << values.size()
               << "entries of a seek list's" << name << "that cannot be read";
    }

    return entries;
}

// The entry value names, read with parse, or nothing when value is not text
// that parse can read.
template<typename Entry, typename Parse>
inline std::optional<Entry> SeekList::_entry(const types::Json& value, Parse parse) {
    if (!value.is_string()) {
        return std::nullopt;
    }

    try {
        return parse(value.string());
    } catch (const std::invalid_argument&) {
        return std::nullopt;
    }
}

// The identifier text names, as "{algorithm}/{hash}" and nothing more.
// Throws std::invalid_argument for text that is not one.
inline storage::Identifier SeekList::_identifier(std::string_view text) {
    const auto slash = text.find('/');

    if (0 == slash || std::string_view::npos == slash) {
        throw std::invalid_argument("An identifier must be an algorithm and a hash: "
                                    + std::string(text));
    }

    return storage::Identifier(std::string(text.substr(0, slash)),
                               std::string(text.substr(slash + 1)));
}

// The body of as many of each kind as fit in fewer than limit bytes, fewer
// than too_many, which do not fit. The range between as many as fit and as
// many as do not is halved until they are one apart.
inline SeekList::Data SeekList::_longest_within(std::size_t too_many, std::size_t limit) const {
    std::size_t fitting = 0;
    auto body = _body(fitting, limit);

    if (body.size() >= limit) {
        throw std::invalid_argument("A seek list cannot be written in fewer than "
                                    + std::to_string(limit) + " bytes");
    }

    while (too_many - fitting > 1) {
        const auto count = fitting + ((too_many - fitting) / 2);
        auto candidate = _body(count, limit);

        if (candidate.size() < limit) {
            fitting = count;
            body = std::move(candidate);
        } else {
            too_many = count;
        }
    }

    return body;
}

// The first count of each kind, as compact JSON, or zlib-compressed where the
// JSON is not fewer than limit bytes.
inline SeekList::Data SeekList::_body(std::size_t count, std::size_t limit) const {
    auto json = _bytes(_json(count).dump());

    if (json.size() < limit) {
        return json;
    }

    return storage::Zlib::compress(json, compression_level);
}

// The first count of each kind, as HttpApi §10.7.1 lists them.
inline types::Json SeekList::_json(std::size_t count) const {
    return types::Json(types::Json::Object{
        {std::string(data_key), _texts(m_data, count)},
        {std::string(search_key), _texts(m_search, count)},
    });
}

// The text of each of the first count entries.
template<typename Entry>
inline types::Json::Array SeekList::_texts(const std::vector<Entry>& entries,
                                           std::size_t count) {
    const auto kept = std::next(entries.begin(),
                                static_cast<std::ptrdiff_t>(std::min(count, entries.size())));
    types::Json::Array texts;

    std::transform(entries.begin(), kept, std::back_inserter(texts),
                   [](const Entry& entry) { return types::Json(entry.to_string()); });
    return texts;
}

// The bytes of text, a byte for each char.
inline SeekList::Data SeekList::_bytes(std::string_view text) {
    Data bytes(text.size());

    std::transform(text.begin(), text.end(), bytes.begin(),
                   [](char character) { return static_cast<std::byte>(character); });
    return bytes;
}

// The text of body, a char for each byte.
inline std::string SeekList::_text(const Data& body) {
    std::string text(body.size(), '\0');

    std::transform(body.begin(), body.end(), text.begin(),
                   [](std::byte byte) { return static_cast<char>(byte); });
    return text;
}

} // namespace protocol
