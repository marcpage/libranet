#pragma once

#include <filesystem>
#include <optional>

namespace io {

class Directories {
public:
    [[nodiscard]] static std::optional<std::filesystem::path> user_app_data();
    [[nodiscard]] static std::optional<std::filesystem::path> user_config();

    Directories() = delete;
    ~Directories() = delete;
    Directories(const Directories&) = delete;
    Directories& operator=(const Directories&) = delete;
    Directories(Directories&&) = delete;
    Directories& operator=(Directories&&) = delete;
};

} // namespace io
