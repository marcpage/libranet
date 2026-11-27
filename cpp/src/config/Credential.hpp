#pragma once

#include <filesystem>
#include <string_view>

namespace config {

// The /config credential (HTTP API §2.3.1, §2.3.2), kept as an Argon2id
// hash of user:password, in the PHC string format.
class Credential {
public:
    explicit Credential(const std::filesystem::path& data_dir);

    // Removing it lets the next credential given be captured.
    [[nodiscard]] const std::filesystem::path& file() const noexcept;

    // Whether credentials, the user:password of Basic authentication, may use
    // /config, capturing them when none are kept yet. Throws
    // std::runtime_error when the credential cannot be read or written.
    [[nodiscard]] bool authenticate(std::string_view credentials) const;

private:
    std::filesystem::path m_file;
};

} // namespace config
