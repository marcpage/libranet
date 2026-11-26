#include <gtest/gtest.h>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "crypto/Base64.hpp"
#include "http/BasicCredentials.hpp"

using http::BasicCredentials;

namespace {

// RFC 7617 §2's example, of Aladdin's password, "open sesame".
constexpr std::string_view aladdin = "Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==";

// An Authorization field giving user_pass in the Basic scheme.
std::string basic(std::string_view user_pass) {
    return "Basic " + crypto::Base64::encode(user_pass);
}

// The username and password, joined by a ':', that text gives, if it gives
// any.
std::optional<std::string> user_pass_of(std::string_view text) {
    const auto credentials = BasicCredentials::parse(text);

    if (!credentials) {
        return std::nullopt;
    }

    return credentials->user_pass();
}

// Whether BasicCredentials::parse() gives no credentials for text.
bool refused(std::string_view text) {
    return !BasicCredentials::parse(text).has_value();
}

// Whether BasicCredentials turns down user_pass as invalid.
bool refused_to_make(std::string_view user_pass) {
    try {
        (void)BasicCredentials(user_pass);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST(BasicCredentialsTest, ReadsRfc7617Example) {
    const auto credentials = BasicCredentials::parse(aladdin);

    ASSERT_TRUE(credentials.has_value());
    EXPECT_EQ(credentials->username(), "Aladdin");
    EXPECT_EQ(credentials->password(), "open sesame");
    EXPECT_EQ(credentials->user_pass(), "Aladdin:open sesame");
}

// RFC 7617 §2.1's example, of test's password, "123£".
TEST(BasicCredentialsTest, ReadsUtf8) {
    const auto credentials = BasicCredentials::parse("Basic dGVzdDoxMjPCow==");

    ASSERT_TRUE(credentials.has_value());
    EXPECT_EQ(credentials->username(), "test");
    EXPECT_EQ(credentials->password(), "123\xC2\xA3");
}

// NOLINTEND(bugprone-unchecked-optional-access)

TEST(BasicCredentialsTest, SchemeIsReadWhateverItsCase) {
    EXPECT_EQ(user_pass_of("basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="), "Aladdin:open sesame");
    EXPECT_EQ(user_pass_of("BASIC QWxhZGRpbjpvcGVuIHNlc2FtZQ=="), "Aladdin:open sesame");
    EXPECT_EQ(user_pass_of("bAsIc QWxhZGRpbjpvcGVuIHNlc2FtZQ=="), "Aladdin:open sesame");
}

TEST(BasicCredentialsTest, OtherSchemesGiveNone) {
    EXPECT_TRUE(refused("Bearer QWxhZGRpbjpvcGVuIHNlc2FtZQ=="));
    EXPECT_TRUE(refused("Basically QWxhZGRpbjpvcGVuIHNlc2FtZQ=="));
    EXPECT_TRUE(refused("Basi QWxhZGRpbjpvcGVuIHNlc2FtZQ=="));
    EXPECT_TRUE(refused("QWxhZGRpbjpvcGVuIHNlc2FtZQ=="));
    EXPECT_TRUE(refused(" Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="));
    // RFC 9110 §11.4 parts a scheme from what follows with spaces alone.
    EXPECT_TRUE(refused("Basic\tQWxhZGRpbjpvcGVuIHNlc2FtZQ=="));
}

TEST(BasicCredentialsTest, NoCredentialsGiveNone) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("Basic"));
    EXPECT_TRUE(refused("Basic "));
    EXPECT_TRUE(refused(basic("")));
}

TEST(BasicCredentialsTest, WhitespaceAroundCredentialsIsPassedOver) {
    EXPECT_EQ(user_pass_of("Basic   QWxhZGRpbjpvcGVuIHNlc2FtZQ=="), "Aladdin:open sesame");
    EXPECT_EQ(user_pass_of("Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ== \t"), "Aladdin:open sesame");
}

TEST(BasicCredentialsTest, NotBase64GivesNone) {
    // Without its padding.
    EXPECT_TRUE(refused("Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ"));
    EXPECT_TRUE(refused("Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=!"));
    EXPECT_TRUE(refused("Basic QWxhZGRp bjpvcGVuIHNlc2FtZQ=="));
    // Base64 for URLs (RFC 4648 §5), of "a:>?".
    EXPECT_TRUE(refused("Basic YTo-Pw=="));
    // Padding before the end.
    EXPECT_TRUE(refused("Basic YTpi=Q=="));
    EXPECT_TRUE(refused("Basic YQ==OmI="));
}

// "YTpiYw==" is "a:bc". The bits after its last whole byte are zeros, and a
// decoder may refuse other bits there (RFC 4648 §3.5).
TEST(BasicCredentialsTest, Base64WithBitsLeftOverGivesNone) {
    EXPECT_EQ(user_pass_of("Basic YTpiYw=="), "a:bc");
    EXPECT_TRUE(refused("Basic YTpiYx=="));
}

TEST(BasicCredentialsTest, NotUtf8GivesNone) {
    EXPECT_TRUE(refused(basic("user:\xFF")));
    EXPECT_TRUE(refused(basic("\xFF:password")));
    // "/" written in two bytes, where one does.
    EXPECT_TRUE(refused(basic("user:\xC0\xAF")));
    // A surrogate, U+D800.
    EXPECT_TRUE(refused(basic("user:\xED\xA0\x80")));
    // U+110000, past the last character there is.
    EXPECT_TRUE(refused(basic("user:\xF4\x90\x80\x80")));
    // "€" cut short.
    EXPECT_TRUE(refused(basic("user:\xE2\x82")));
    EXPECT_TRUE(refused(basic("user:\x80")));
}

TEST(BasicCredentialsTest, EachLengthOfUtf8IsRead) {
    // "é", "€", and "🔑".
    EXPECT_EQ(user_pass_of(basic("\xC3\xA9:\xE2\x82\xAC\xF0\x9F\x94\x91")),
              "\xC3\xA9:\xE2\x82\xAC\xF0\x9F\x94\x91");
}

TEST(BasicCredentialsTest, NoColonGivesNone) {
    EXPECT_TRUE(refused(basic("Aladdin")));
    EXPECT_TRUE(refused("Basic QWxhZGRpbg=="));
}

TEST(BasicCredentialsTest, UsernameEndsAtFirstColon) {
    const BasicCredentials credentials("user:pass:word");

    EXPECT_EQ(credentials.username(), "user");
    EXPECT_EQ(credentials.password(), "pass:word");
    EXPECT_EQ(credentials.user_pass(), "user:pass:word");
}

TEST(BasicCredentialsTest, UsernameAndPasswordMayBeEmpty) {
    const BasicCredentials credentials(":");

    EXPECT_TRUE(credentials.username().empty());
    EXPECT_TRUE(credentials.password().empty());
    EXPECT_EQ(user_pass_of(basic(":")), ":");
    EXPECT_EQ(user_pass_of(basic("user:")), "user:");
    EXPECT_EQ(user_pass_of(basic(":password")), ":password");
}

TEST(BasicCredentialsTest, WritesRfc7617Example) {
    EXPECT_EQ(BasicCredentials("Aladdin:open sesame").to_string(), aladdin);
    EXPECT_EQ(BasicCredentials("test:123\xC2\xA3").to_string(), "Basic dGVzdDoxMjPCow==");
}

TEST(BasicCredentialsTest, ReadsWhatItWrites) {
    EXPECT_EQ(user_pass_of(BasicCredentials("Aladdin:open sesame").to_string()),
              "Aladdin:open sesame");
    EXPECT_EQ(user_pass_of(BasicCredentials(":").to_string()), ":");
    EXPECT_EQ(user_pass_of(BasicCredentials("a:b:c").to_string()), "a:b:c");
}

TEST(BasicCredentialsTest, UserPassWithNoColonOrNotUtf8IsRefused) {
    EXPECT_TRUE(refused_to_make(""));
    EXPECT_TRUE(refused_to_make("Aladdin"));
    EXPECT_TRUE(refused_to_make("user:\xFF"));
    EXPECT_TRUE(refused_to_make("\xC0\xAF:password"));
    EXPECT_FALSE(refused_to_make("Aladdin:open sesame"));
}

TEST(BasicCredentialsTest, MovedCredentialsAreKept) {
    BasicCredentials credentials("Aladdin:open sesame");
    BasicCredentials moved(std::move(credentials));

    EXPECT_EQ(moved.username(), "Aladdin");
    EXPECT_EQ(moved.password(), "open sesame");

    credentials = std::move(moved);

    EXPECT_EQ(credentials.user_pass(), "Aladdin:open sesame");
}
