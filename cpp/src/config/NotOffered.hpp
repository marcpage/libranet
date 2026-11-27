#pragma once

#include <array>
#include <string_view>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"

namespace config {

// Answers a path beneath /config/api that the shared /config page uses, and
// that a later phase brings, whatever the method: 404, about:blank, with a
// detail naming what the node does not offer yet, such as "This node does not
// offer backups yet.", which the page shows in that section. GET /config/api
// lists none of them, and each phase that brings one takes it off features.
// Safe to use from the threads serving connections, several at once.
class NotOffered {
public:
    // What the node does not offer yet, and where the page asks for it.
    struct Feature {
        std::string_view path; // Where the page asks for it
        std::string_view name; // As it is named in the detail, such as "backups"
        bool beneath = false;  // Whether each path beneath path is it too
    };

    // Each the page asks for that a later phase brings.
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

    // To be registered with the server at feature's path.
    explicit NotOffered(const Feature& feature) noexcept;

    // Answers a request for the feature's path, whatever its query, or for a
    // path beneath it when that is the feature's too. Any other path is no
    // resource.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    Feature m_feature;

    [[nodiscard]] bool _has(std::string_view path) const;
};

} // namespace config
