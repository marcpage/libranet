#include "NodeIdentity.hpp"

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <ios>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "crypto/ISignKey.hpp"
#include "io/SafeWriteFile.hpp"
#include "logger/Logger.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using crypto::ISignKey;
using identity::NodeIdentity;
using io::SafeWriteFile;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::runtime_error;
using std::size_t;
using std::streamsize;
using std::string;
using std::string_view;
using std::uintmax_t;
using std::filesystem::path;
using std::filesystem::perms;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using types::Kilobyte;

namespace {

// What ISignKey::id() gives for an Ed25519 key, the only kind a node's key
// may be (HTTP API §11.2).
constexpr string_view ed25519_id = "ed25519-key";

// A node's key is 119 bytes as PEM, so a file of more than this holds none,
// and is not read into memory.
constexpr uintmax_t most_key_file_size = 64 * Kilobyte;

// What key_file holds, or nothing when there is no file there. Throws
// std::invalid_argument when it holds far more than a key, and
// std::runtime_error when it cannot be read.
optional<string> read_key_file(const path& key_file) {
    if (!std::filesystem::exists(key_file)) {
        return nullopt;
    }

    const auto size = std::filesystem::file_size(key_file);

    if (size > most_key_file_size) {
        throw invalid_argument("The node's key file holds far more than a key: "
                               + key_file.string());
    }

    string text(static_cast<size_t>(size), '\0');
    std::ifstream file(key_file, std::ios::binary);

    if (!file.read(text.data(), static_cast<streamsize>(size))) {
        throw runtime_error("Cannot read the node's key file: " + key_file.string());
    }

    return text;
}

enum class Report : uint8_t {
    Permissions,
    DoNot,
};

// Makes key_file readable and writable only by its owner, when others may
// read or write it, and warns of it, as others may have read the key
// already. Throws std::filesystem::filesystem_error when it cannot.
void restrict_to_owner(const path& key_file, Report report) {
    constexpr auto others = perms::group_all | perms::others_all;
    const auto permissions = std::filesystem::status(key_file).permissions();

    if (perms::none == (permissions & others)) {
        return;
    }

    lWarnIf(Report::Permissions == report)
        << "Others could read the node's key file, which only its owner now may:"
        << key_file.string();
    std::filesystem::permissions(key_file, perms::owner_read | perms::owner_write);
}

// The node's key, from pem, which key_file held. Throws
// std::invalid_argument when it is not an Ed25519 private key.
ISignKey::Ptr key_from(string_view pem, const path& key_file) {
    ISignKey::Ptr key;

    try {
        key = ISignKey::from_pem(pem);
    } catch (const invalid_argument& error) {
        throw invalid_argument("Cannot read the node's key file: " + key_file.string() + ": "
                               + error.what());
    }

    if (ed25519_id != key->id() || !key->has_private_key()) {
        throw invalid_argument("The node's key file holds no Ed25519 private key: "
                               + key_file.string());
    }

    return key;
}

// The node's key, read from key_file, or, when there is no file there, made
// and written to one, in PKCS #8, as the Python node writes its key.
ISignKey::Ptr load_or_make(const path& key_file) {
    if (const auto pem = read_key_file(key_file)) {
        restrict_to_owner(key_file, Report::Permissions);
        return key_from(*pem, key_file);
    }

    auto key = ISignKey::Ed25519();
    SafeWriteFile final_key_file(key_file);

    final_key_file.write(key->private_pem());
    restrict_to_owner(key_file, Report::DoNot);
    lInfo << "Made the node's key:" << key_file.string();
    return key;
}

} // namespace

namespace identity {

NodeIdentity::NodeIdentity(const path& key_file, const IDataSource& store)
    : m_key(load_or_make(key_file))
    , m_public_pem(m_key->public_pem())
    , m_id(Identifier::of(Converter::bytes_of(m_public_pem))) {
    // Stored as content, so that other nodes can fetch the key by the
    // node's id (HTTP API §11.1).
    if (!store.has(m_id)) {
        store.put(m_id, Converter::bytes_of(m_public_pem));
    }
}

const Identifier& NodeIdentity::id() const noexcept {
    return m_id;
}

const string& NodeIdentity::public_pem() const noexcept {
    return m_public_pem;
}

NodeIdentity::Data NodeIdentity::sign(string_view data) const {
    return m_key->sign(data);
}

} // namespace identity
