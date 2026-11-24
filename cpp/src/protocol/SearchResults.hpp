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

#include "http/Response.hpp"
#include "logger/Logger.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"

namespace protocol {

// A search's answer (HttpApi §6.1), {"results": ["sha256/…", …]}: what was
// found, best match first. A POST /data/search/{prefix} naming what a peer
// holds has the same body (HttpApi §10.7.2). It is sent less than 1 MiB in
// size, naming as many of the results, from the first, as fit: as compact
// JSON, or, only when that would not fit, zlib-compressed at level 6, as the
// reference node does, and then with Content-Encoding: deflate.
class SearchResults {
public:
    using Identifiers = std::vector<storage::Identifier>;

    // An answer as it is sent.
    struct Body {
        std::string text;
        // Whether text is zlib-compressed, and so is sent with
        // Content-Encoding: deflate.
        bool compressed = false;
    };

    // Reads an answer as it was sent: JSON, or zlib-compressed JSON of no
    // more than decompressed_limit bytes. An entry that is not
    // "{algorithm}/{hash}" of a supported algorithm is left out, as a node
    // may name content this one cannot read. Throws std::invalid_argument
    // for a body that is neither, or that is not an object holding a
    // "results" array.
    [[nodiscard]] static SearchResults parse(std::string_view body,
                                             std::size_t decompressed_limit);

    // results, best match first.
    explicit SearchResults(Identifiers results);

    [[nodiscard]] const Identifiers& results() const noexcept;
    // The answer naming as many of the results, from the first, as fit.
    [[nodiscard]] Body body() const;
    // Sets the response's Content-Type, its Content-Encoding when the body
    // is compressed, and its body to this answer's, and leaves its status
    // and other headers as they are.
    void answer(http::Response& response) const;

private:
    // The answer naming the first count of the results, compressed.
    struct Attempt {
        std::size_t count = 0;
        std::string text;
    };

    // HttpApi §6.1: an answer is less than 1 MiB as sent, which for one sent
    // compressed is its compressed size.
    static constexpr std::size_t max_body_size = 1 * types::Megabyte;
    // The level an answer too large as it is is compressed at, as the
    // reference node does (HttpApi §6.1).
    static constexpr int compression_level = storage::Zlib::balance_level;
    static constexpr std::string_view results_member = "results";

    Identifiers m_results;

    [[nodiscard]] Body _compressed_body() const;
    [[nodiscard]] static std::size_t _next_count(const Attempt& fitting,
                                                 const Attempt& overflowing);
    [[nodiscard]] Attempt _compressed(std::size_t count) const;
    [[nodiscard]] std::string _json_text(std::size_t count) const;
    [[nodiscard]] static types::Json _json(std::string_view body, std::size_t decompressed_limit);
    [[nodiscard]] static std::optional<types::Json> _parsed(std::string_view text);
    [[nodiscard]] static Identifiers _identifiers(const types::Json& value);
};

inline SearchResults SearchResults::parse(std::string_view body, std::size_t decompressed_limit) {
    return SearchResults(_identifiers(_json(body, decompressed_limit)));
}

inline SearchResults::SearchResults(Identifiers results)
    : m_results(std::move(results)) {}

inline const SearchResults::Identifiers& SearchResults::results() const noexcept {
    return m_results;
}

inline SearchResults::Body SearchResults::body() const {
    auto text = _json_text(m_results.size());

    if (text.size() < max_body_size) {
        return {std::move(text), false};
    }

    return _compressed_body();
}

inline void SearchResults::answer(http::Response& response) const {
    auto written = body();

    response.headers().set("Content-Type", std::string(types::json_content_type));

    if (written.compressed) {
        response.headers().set("Content-Encoding", "deflate");
    }

    response.set_body(std::move(written.text));
}

// The answer, compressed, naming all of the results if they fit, or else as
// many as do. Those are found by narrowing the range of how many it names,
// between as many as are known to fit, none at first, and as many as are
// known not to, until they are one apart.
inline SearchResults::Body SearchResults::_compressed_body() const {
    auto all = _compressed(m_results.size());

    if (all.text.size() < max_body_size) {
        return {std::move(all.text), true};
    }

    auto fitting = _compressed(0);
    auto overflowing = std::move(all);

    while (overflowing.count - fitting.count > 1) {
        auto attempt = _compressed(_next_count(fitting, overflowing));

        if (attempt.text.size() < max_body_size) {
            fitting = std::move(attempt);
        } else {
            overflowing = std::move(attempt);
        }
    }

    return {std::move(fitting.text), true};
}

// How many to name next, between as many as fit and as many as do not:
// where the limit falls if the size grows evenly from one to the other. It
// nearly does, since each identifier compresses about alike, so this takes
// a few attempts where halving the range would take one for each bit of
// its width. It is kept apart from both, so that the range narrows.
inline std::size_t SearchResults::_next_count(const Attempt& fitting, const Attempt& overflowing) {
    const auto counts = overflowing.count - fitting.count;
    const auto sizes = overflowing.text.size() - fitting.text.size();
    const auto room = max_body_size - fitting.text.size();
    const auto count = fitting.count + (counts * room / sizes);

    return std::clamp(count, fitting.count + 1, overflowing.count - 1);
}

// The answer naming the first count of the results, zlib-compressed.
inline SearchResults::Attempt SearchResults::_compressed(std::size_t count) const {
    const auto compressed = storage::Zlib::compress(types::Converter::bytes_of(_json_text(count)),
                                                    compression_level);

    return {count, types::Converter::text_of(compressed)};
}

// The answer naming the first count of the results, as compact JSON.
inline std::string SearchResults::_json_text(std::size_t count) const {
    const auto end = std::next(m_results.begin(), static_cast<std::ptrdiff_t>(count));
    types::Json::Array entries;
    types::Json::Object members;

    entries.reserve(count);
    std::transform(m_results.begin(), end, std::back_inserter(entries), [](const auto& identifier) {
        return types::Json(identifier.to_string());
    });
    members.emplace(results_member, std::move(entries));
    return types::Json(std::move(members)).dump();
}

// The JSON body holds, as it is or once decompressed. Throws
// std::invalid_argument for a body that is neither JSON nor one zlib stream
// of JSON of no more than decompressed_limit bytes.
inline types::Json SearchResults::_json(std::string_view body, std::size_t decompressed_limit) {
    auto value = _parsed(body);

    if (value) {
        return std::move(*value);
    }

    const auto decompressed = storage::Zlib::decompress(types::Converter::bytes_of(body),
                                                        decompressed_limit);

    if (!decompressed) {
        throw std::invalid_argument("A search answer must be JSON, or zlib-compressed JSON of "
                                    "no more than "
                                    + std::to_string(decompressed_limit) + " bytes");
    }

    return types::Json::parse(types::Converter::text_of(*decompressed));
}

// The JSON text holds, or nothing when it is not JSON.
inline std::optional<types::Json> SearchResults::_parsed(std::string_view text) {
    try {
        return types::Json::parse(text);
    } catch (const std::invalid_argument&) {
        // Not JSON as it is: nothing, so that it is read as compressed.
        return std::nullopt;
    }
}

// The identifiers an answer names, in its order, less its entries that name
// none. Throws std::invalid_argument for a value that is not an object
// holding a "results" array.
inline SearchResults::Identifiers SearchResults::_identifiers(const types::Json& value) {
    const std::string member(results_member);

    if (!value.is_object() || !value.has(member) || !value.at(member).is_array()) {
        throw std::invalid_argument("A search answer must be an object holding a \"results\" "
                                    "array");
    }

    const auto& entries = value.at(member).array();
    Identifiers identifiers;

    for (const auto& entry : entries) {
        auto identifier = storage::Identifier::from(entry);

        if (identifier) {
            identifiers.push_back(std::move(*identifier));
        }
    }

    if (identifiers.size() < entries.size()) {
        lDebug << "Leaving out" << entries.size() - identifiers.size()
               << "entries of a search answer that are not identifiers of a supported algorithm";
    }

    return identifiers;
}

} // namespace protocol
