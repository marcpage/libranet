#include "NodeIdentity.hpp"

#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <ios>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "crypto/ISignKey.hpp"
#include "logger/Logger.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using crypto::ISignKey;
using identity::NodeIdentity;
using std::generic_category;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::runtime_error;
using std::size_t;
using std::streamsize;
using std::string;
using std::string_view;
using std::system_error;
using std::uintmax_t;
using std::filesystem::path;
using std::filesystem::perms;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;

namespace {

// What ISignKey::id() gives for an Ed25519 key, the only kind a node's key
// may be (HTTP API §11.2).
constexpr string_view ed25519_id = "ed25519-key";

// A node's key is 119 bytes as PEM, so a file of more than this holds none,
// and is not read into memory.
constexpr uintmax_t most_key_file_size = 64UL * 1024UL;

// Only its owner may read or write a key file.
constexpr mode_t owner_only = S_IRUSR | S_IWUSR;

// A file beside a key file, for a new key to be written to before it is
// linked into place, so that no reader sees half a key. Only its owner may
// read or write it, from the moment it is made. It is closed and removed
// when this goes.
class PartialFile {
public:
    // Throws std::system_error when it cannot be made, as when the
    // directory key_file is to go in is not there.
    explicit PartialFile(const path& key_file);
    ~PartialFile();
    PartialFile(const PartialFile&) = delete;
    PartialFile& operator=(const PartialFile&) = delete;
    PartialFile(PartialFile&&) = delete;
    PartialFile& operator=(PartialFile&&) = delete;

    // Writes all of text, and waits until it is on disk, so that a crash
    // cannot leave the key file linked but empty. Throws std::system_error
    // when it cannot.
    void write_all(string_view text) const;
    // Links the file in as key_file, which must not be there, so that a key
    // kept already is never replaced. Throws
    // std::filesystem::filesystem_error when it cannot.
    void link_as(const path& key_file) const;

private:
    path m_path;
    int m_descriptor;
};

// Where a new key for key_file is written: beside it, so that the link does
// not cross filesystems, and named with a random number, so that it is not
// a file left by a node that stopped while writing one.
path partial_path(const path& key_file) {
    std::random_device random;

    return key_file.parent_path()
           / ("." + key_file.filename().string() + "." + std::to_string(random()) + ".partial");
}

// A new file at file_path, open to write, that only its owner may read or
// write. Throws std::system_error when it cannot be made, or is there
// already.
int make_private(const path& file_path) {
    // open() is the POSIX call that makes a file with the permissions it
    // starts with, and it takes variable arguments; there is no other form.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    const auto descriptor = ::open(file_path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
                                   owner_only);

    if (descriptor < 0) {
        throw system_error(errno, generic_category(), "open " + file_path.string());
    }

    return descriptor;
}

PartialFile::PartialFile(const path& key_file)
    : m_path(partial_path(key_file))
    , m_descriptor(make_private(m_path)) {}

PartialFile::~PartialFile() {
    std::error_code ignored;

    (void)::close(m_descriptor);
    (void)std::filesystem::remove(m_path, ignored);
}

void PartialFile::write_all(string_view text) const {
    auto remaining = text;

    while (!remaining.empty()) {
        const auto written = ::write(m_descriptor, remaining.data(), remaining.size());

        if (written < 0 && EINTR != errno) {
            throw system_error(errno, generic_category(), "write " + m_path.string());
        }

        if (written > 0) {
            remaining.remove_prefix(static_cast<size_t>(written));
        }
    }

    if (0 != ::fsync(m_descriptor)) {
        throw system_error(errno, generic_category(), "fsync " + m_path.string());
    }
}

void PartialFile::link_as(const path& key_file) const {
    std::filesystem::create_hard_link(m_path, key_file);
}

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

// Makes key_file readable and writable only by its owner, when others may
// read or write it, and warns of it, as others may have read the key
// already. Throws std::filesystem::filesystem_error when it cannot.
void restrict_to_owner(const path& key_file) {
    constexpr auto others = perms::group_all | perms::others_all;
    const auto permissions = std::filesystem::status(key_file).permissions();

    if (perms::none == (permissions & others)) {
        return;
    }

    lWarn << "Others could read the node's key file, which only its owner now may:"
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
        restrict_to_owner(key_file);
        return key_from(*pem, key_file);
    }

    auto key = ISignKey::Ed25519();
    const PartialFile partial(key_file);

    partial.write_all(key->private_pem());
    partial.link_as(key_file);
    lInfo << "Made the node's key:" << key_file.string();
    return key;
}

} // namespace

namespace identity {

NodeIdentity::NodeIdentity(const path& key_file, const IDataSource& store)
    : m_key(load_or_make(key_file))
    , m_public_pem(m_key->public_pem())
    , m_id(Identifier::from(Converter::bytes_of(m_public_pem))) {
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
