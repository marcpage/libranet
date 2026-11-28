#include "Credential.hpp"

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "crypto/IPasswordHasher.hpp"
#include "io/FileReader.hpp"
#include "io/SafeWriteFile.hpp"
#include "logger/Logger.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using crypto::IPasswordHasher;
using io::FileReader;
using io::SafeWriteFile;
using std::byte;
using std::nullopt;
using std::optional;
using std::runtime_error;
using std::size_t;
using std::string;
using std::string_view;
using std::uint32_t;
using std::vector;
using std::filesystem::path;
using types::Converter;
using types::Kilobyte;

namespace {

constexpr string_view file_name = "config-credential";

// A hash under the costs password_hasher() sets is under 100 bytes, so a
// file of more than this holds none, and is not read into memory.
constexpr size_t most_file_size = 1 * Kilobyte;

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

    vector<byte> hash;

    try {
        hash = FileReader(file).read_exact(most_file_size + 1);
    } catch (const runtime_error&) {
        // Removed since it was looked for.
        if (!std::filesystem::exists(file)) {
            return nullopt;
        }

        throw;
    }

    if (hash.size() > most_file_size) {
        throw runtime_error("The /config credential holds far more than a hash: "
                            + file.string());
    }

    return Converter::text_of(hash);
}

// Keeps the hash of credentials in file, unless a file is there already.
// Whether it was kept comes back. It is written whole to a file of its own
// first, and linked into place, which fails when a file is there: so the
// file is never seen part written, and of two requests capturing at once,
// whichever links first wins (HTTP API §2.3.1).
bool capture(const path& file, string_view credentials) {
    SafeWriteFile partial(file, SafeWriteFile::Access::Owner);

    return partial.write_if_absent(password_hasher()->hash_password(credentials));
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
