#include "Credential.hpp"

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

#include "crypto/IPasswordHasher.hpp"
#include "logger/Logger.hpp"

using crypto::IPasswordHasher;
using std::error_code;
using std::nullopt;
using std::optional;
using std::random_device;
using std::runtime_error;
using std::size_t;
using std::streamsize;
using std::string;
using std::string_view;
using std::system_error;
using std::uint32_t;
using std::filesystem::filesystem_error;
using std::filesystem::path;

namespace {

constexpr string_view file_name = "config-credential";

// A hash under the costs password_hasher() sets is under 100 bytes, so a
// file of more than this holds none, and is not read into memory.
constexpr size_t most_file_size = 1024;

// Argon2id at 19 MiB and 2 passes, in one lane, the least OWASP's Password
// Storage Cheat Sheet asks of it, with the 16-byte salt and 32-byte hash of
// RFC 9106 §4. Any hash is checked at the costs it names, so these can be
// raised without stranding a credential captured under them.
IPasswordHasher::Ptr password_hasher() {
    constexpr uint32_t memory_cost_kib = 19U * 1024U;
    constexpr uint32_t passes = 2;
    constexpr uint32_t lanes = 1;
    constexpr size_t salt_length = 16;
    constexpr size_t hash_length = 32;

    return IPasswordHasher::Argon2id(memory_cost_kib, passes, lanes, salt_length, hash_length);
}

// The hash kept in file, or none when there is no file there. Throws
// std::runtime_error when it cannot be read, or holds far more than a hash.
optional<string> read_hash(const path& file) {
    // Looked for before it is opened: a file that fails to open may be one
    // another request has made since.
    if (!std::filesystem::exists(file)) {
        return nullopt;
    }

    std::ifstream stream(file, std::ios::binary);

    // Removed since it was looked for.
    if (!stream.is_open() && !std::filesystem::exists(file)) {
        return nullopt;
    }

    if (!stream.is_open()) {
        throw runtime_error("Cannot open the /config credential: " + file.string());
    }

    string hash(most_file_size + 1, '\0');

    stream.read(hash.data(), static_cast<streamsize>(hash.size()));

    if (stream.bad()) {
        throw runtime_error("Cannot read the /config credential: " + file.string());
    }

    if (static_cast<size_t>(stream.gcount()) > most_file_size) {
        throw runtime_error("The /config credential holds far more than a hash: "
                            + file.string());
    }

    hash.resize(static_cast<size_t>(stream.gcount()));
    return hash;
}

// A new file, beside the one it is to become, that only its owner may read
// or write. It is removed when this goes, whether or not it was linked into
// place.
class PartialFile {
public:
    // Throws std::system_error when it cannot be made.
    explicit PartialFile(const path& file);
    ~PartialFile();

    // And closes it. Throws std::system_error when it cannot.
    void write(string_view contents);

    // Whether it now is file too, which it is not when a file is there
    // already. Throws std::filesystem::filesystem_error when it cannot be.
    [[nodiscard]] bool link_as(const path& file) const;

    PartialFile(const PartialFile&) = delete;
    PartialFile& operator=(const PartialFile&) = delete;
    PartialFile(PartialFile&&) = delete;
    PartialFile& operator=(PartialFile&&) = delete;

private:
    path m_path;
    int m_descriptor;

    // Starts with a dot, so that it is not taken for the file, and ends as
    // io::SafeWriteFile's do, so that one a crash left behind can be told.
    // Named at random, so that two requests capturing at once do not
    // collide.
    [[nodiscard]] static path _beside(const path& file);
    [[nodiscard]] static int _open(const path& partial);
    void _close();
};

PartialFile::PartialFile(const path& file)
    : m_path(_beside(file))
    , m_descriptor(_open(m_path)) {}

// Nothing more can be done if it cannot be closed or removed.
PartialFile::~PartialFile() {
    error_code ignored;

    if (m_descriptor >= 0) {
        (void)::close(m_descriptor);
    }

    (void)std::filesystem::remove(m_path, ignored);
}

void PartialFile::write(string_view contents) {
    while (!contents.empty()) {
        const auto written = ::write(m_descriptor, contents.data(), contents.size());

        if (written < 0) {
            throw system_error(errno, std::generic_category(), "Cannot write " + m_path.string());
        }

        contents.remove_prefix(static_cast<size_t>(written));
    }

    _close();
}

bool PartialFile::link_as(const path& file) const {
    error_code error;

    std::filesystem::create_hard_link(m_path, file, error);

    if (std::errc::file_exists == error) {
        return false;
    }

    if (error) {
        throw filesystem_error("Cannot link the /config credential into place", m_path, file,
                               error);
    }

    return true;
}

path PartialFile::_beside(const path& file) {
    random_device random;

    return file.parent_path()
           / ("." + file.filename().string() + "." + std::to_string(random()) + ".partial");
}

// A program this one starts does not inherit it.
int PartialFile::_open(const path& partial) {
    constexpr int flags = O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC;
    constexpr mode_t owner_read_write = S_IRUSR | S_IWUSR;

    // open() is the POSIX call that sets the mode of a file as it makes it,
    // and it takes the mode as C's variable arguments.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    const int descriptor = ::open(partial.c_str(), flags, owner_read_write);

    if (descriptor < 0) {
        throw system_error(errno, std::generic_category(), "Cannot make " + partial.string());
    }

    return descriptor;
}

// Closed, so that all of it is in the file before it is linked into place,
// and a failure to write the last of it is known.
void PartialFile::_close() {
    const int result = ::close(m_descriptor);

    m_descriptor = -1;

    if (0 != result) {
        throw system_error(errno, std::generic_category(), "Cannot close " + m_path.string());
    }
}

// Keeps the hash of credentials in file, unless a file is there already.
// Whether it was kept comes back. It is written whole to a file of its own
// first, and linked into place, which fails when a file is there: so the
// file is never seen part written, and of two requests capturing at once,
// whichever links first wins (HTTP API §2.3.1).
bool capture(const path& file, string_view credentials) {
    const auto directory = file.parent_path();

    // A file named with no directory is in the current one, which is there,
    // and which create_directories() refuses to make.
    if (!directory.empty()) {
        std::filesystem::create_directories(directory);
    }

    PartialFile partial(file);

    partial.write(password_hasher()->hash_password(credentials));
    return partial.link_as(file);
}

} // namespace

namespace config {

Credential::Credential(const path& data_dir)
    : m_file(data_dir / file_name) {}

const path& Credential::file() const noexcept {
    return m_file;
}

bool Credential::authenticate(string_view credentials) const {
    if (const auto hash = read_hash(m_file)) {
        return password_hasher()->verify_password(credentials, *hash);
    }

    if (capture(m_file, credentials)) {
        lInfo << "Captured the /config credential:" << m_file.string();
        return true;
    }

    // Another request captured one first, so this one is checked against
    // that one (HTTP API §2.3.1).
    const auto winner = read_hash(m_file);

    if (!winner) {
        throw runtime_error("The /config credential was removed as it was captured: "
                            + m_file.string());
    }

    return password_hasher()->verify_password(credentials, *winner);
}

} // namespace config
