#pragma once

#include <filesystem>
#include <optional>

namespace io {

class Directories {
public:
    [[nodiscard]] static std::optional<std::filesystem::path> user_app_data();
    [[nodiscard]] static std::optional<std::filesystem::path> user_config();
};

} // namespace io
