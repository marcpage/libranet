#pragma once

#include <filesystem>
#include <string_view>

namespace config {

// The /config credential (HTTP API §2.3.1, §2.3.2): the whole user:password
// as one Argon2id hash, in the PHC string format, which holds its salt and
// costs, so that the file names nobody. The file is read on every check, so
// removing it lets the next request capture a new credential. Any thread may
// use it.
class Credential {
public:
    // Kept in data_dir, in a file only its owner may read.
    explicit Credential(const std::filesystem::path& data_dir);

    // Removing it lets the next credential given be captured.
    [[nodiscard]] const std::filesystem::path& file() const noexcept;

    // Whether credentials, the user:password of an Authorization: Basic
    // header, may use /config, capturing them when none are kept yet. Throws
    // std::runtime_error when the credential cannot be read or written.
    [[nodiscard]] bool authenticate(std::string_view credentials) const;

private:
    std::filesystem::path m_file;
};

} // namespace config
