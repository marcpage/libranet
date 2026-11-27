#pragma once

#include <array>
#include <string_view>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"

namespace config {

// 404 for a path the /config page uses that a later phase brings, naming
// what the node does not offer yet.
class NotOffered {
public:
    struct Feature {
        std::string_view path;
        std::string_view name; // As the detail names it: "backups"
        bool beneath = false;  // Whether each path beneath path is it too
    };

    // Each phase that brings one takes it off.
    static constexpr std::array features{
        Feature{"/config/api/metadata", "metadata", false},
        Feature{"/config/api/trust", "trust lists", true},
        Feature{"/config/api/connecting", "connecting only to the nodes it trusts", false},
        Feature{"/config/api/karma", "Karma", true},
        Feature{"/config/api/applications", "applications", true},
        Feature{"/config/api/users", "users", false},
        Feature{"/config/api/builds", "builds", false},
        Feature{"/config/api/exports", "exports", false},
        Feature{"/config/api/backups", "backups", true},
        Feature{"/config/api/restores", "restores", false},
    };

    explicit NotOffered(const Feature& feature) noexcept;

    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    Feature m_feature;

    [[nodiscard]] bool _has(std::string_view path) const;
};

} // namespace config
