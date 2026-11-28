#pragma once

#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

// What more than one of the crypto tests uses: bytes written in hex, and
// an RSA key, with what Python's cryptography package, as the Python node
// uses it, signed and encrypted with the key.
namespace fixtures {

// The bytes that lowercase hex names.
inline std::vector<std::byte> bytes(std::string_view text) {
    constexpr std::string_view digits = "0123456789abcdef";
    constexpr auto nibble_bits = 4U;
    std::vector<std::byte> data;

    for (std::size_t index = 0; index + 1 < text.size(); index += 2) {
        const auto high = digits.find(text.at(index));
        const auto low = digits.find(text.at(index + 1));

        data.push_back(static_cast<std::byte>((high << nibble_bits) | low));
    }

    return data;
}

// A 1024-bit RSA key, made by Python's cryptography package, in each of the
// forms it writes: PKCS #1 and PKCS #8 for the private key, and PKCS #1 and
// SubjectPublicKeyInfo for the public key.
inline constexpr auto rsa_pkcs1_private_pem =
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIICXQIBAAKBgQC7+O+PUOBzPr8TfkL9V8RnxVflX1/CIM9a0Vffex5uvAzU12I7\n"
    "y28auNs/EQqL/dmQaNGAhpA03jF8L8tV9tzVqDRjuTw7+zsjyfUs5HsgPswc7q5J\n"
    "vcuWiYedq96LOUe3pEK9yoHJuBsqjlFkdNYRl1iTIxiugnm47vzH2w9ooQIDAQAB\n"
    "AoGAFgiN/Byc0qVuPFi2fX/GM+u769UMInM6Gwki5b1GyfAdCO3eQ+gKs/UrLe9P\n"
    "by1oTTccJ5aeXLQW1Nm9Wew/4k/bTSnbNFVphefrh2n6fmW/iUH60YpEge4yFBCj\n"
    "HYTuCWAR1bkX1e8X/MW5Z//IogrRC7YhQNukbtLIeUNq0IkCQQDwQhWD56v4FCbT\n"
    "3Hhsg5BW5vSnZDwH7jkIfhh+6j1fj2lIPbM2gXMyzoDAIGaN+uXWRe23RAC3ikgx\n"
    "NEkR9OWPAkEAyEnZRFBsiMn36h5TYIJyX0QqbWj+rZk+ONsOlSeNXaI8KSA9u99P\n"
    "9UXeme6vWWe9d8n7F9IL8DdF2GZRu/iWzwJADt6IJlm4MupMXOBQM6cQz9ALZDC3\n"
    "5mW/8d6b9X7ZvYhKo8XtyXnJf2N4ZGuc7TyR7Oj3BViYEsPkI+dwfkQB0QJBALXQ\n"
    "c+5To3K5BNf+F2nwgKqCUpFnUml8RsON9zrm4Kky8LHUlrXJDeh1OJE73ummBpSL\n"
    "QehpwHSbeEfs+h1MmHkCQQDQkMSswBoXRgqgKHvLkWxEpBYZYx/IuBBMommJt3Ut\n"
    "mF0sIe6IFZlfdYDVQP8tpRgRfyetcX2uJKeBtVf9PY2g\n"
    "-----END RSA PRIVATE KEY-----\n";
inline constexpr auto rsa_pkcs8_private_pem =
    "-----BEGIN PRIVATE KEY-----\n"
    "MIICdwIBADANBgkqhkiG9w0BAQEFAASCAmEwggJdAgEAAoGBALv4749Q4HM+vxN+\n"
    "Qv1XxGfFV+VfX8Igz1rRV997Hm68DNTXYjvLbxq42z8RCov92ZBo0YCGkDTeMXwv\n"
    "y1X23NWoNGO5PDv7OyPJ9SzkeyA+zBzurkm9y5aJh52r3os5R7ekQr3Kgcm4GyqO\n"
    "UWR01hGXWJMjGK6Cebju/MfbD2ihAgMBAAECgYAWCI38HJzSpW48WLZ9f8Yz67vr\n"
    "1QwiczobCSLlvUbJ8B0I7d5D6Aqz9Sst709vLWhNNxwnlp5ctBbU2b1Z7D/iT9tN\n"
    "Kds0VWmF5+uHafp+Zb+JQfrRikSB7jIUEKMdhO4JYBHVuRfV7xf8xbln/8iiCtEL\n"
    "tiFA26Ru0sh5Q2rQiQJBAPBCFYPnq/gUJtPceGyDkFbm9KdkPAfuOQh+GH7qPV+P\n"
    "aUg9szaBczLOgMAgZo365dZF7bdEALeKSDE0SRH05Y8CQQDISdlEUGyIyffqHlNg\n"
    "gnJfRCptaP6tmT442w6VJ41dojwpID2730/1Rd6Z7q9ZZ713yfsX0gvwN0XYZlG7\n"
    "+JbPAkAO3ogmWbgy6kxc4FAzpxDP0AtkMLfmZb/x3pv1ftm9iEqjxe3Jecl/Y3hk\n"
    "a5ztPJHs6PcFWJgSw+Qj53B+RAHRAkEAtdBz7lOjcrkE1/4XafCAqoJSkWdSaXxG\n"
    "w433OubgqTLwsdSWtckN6HU4kTve6aYGlItB6GnAdJt4R+z6HUyYeQJBANCQxKzA\n"
    "GhdGCqAoe8uRbESkFhljH8i4EEyiaYm3dS2YXSwh7ogVmV91gNVA/y2lGBF/J61x\n"
    "fa4kp4G1V/09jaA=\n"
    "-----END PRIVATE KEY-----\n";
inline constexpr auto rsa_pkcs1_public_pem =
    "-----BEGIN RSA PUBLIC KEY-----\n"
    "MIGJAoGBALv4749Q4HM+vxN+Qv1XxGfFV+VfX8Igz1rRV997Hm68DNTXYjvLbxq4\n"
    "2z8RCov92ZBo0YCGkDTeMXwvy1X23NWoNGO5PDv7OyPJ9SzkeyA+zBzurkm9y5aJ\n"
    "h52r3os5R7ekQr3Kgcm4GyqOUWR01hGXWJMjGK6Cebju/MfbD2ihAgMBAAE=\n"
    "-----END RSA PUBLIC KEY-----\n";
inline constexpr auto rsa_spki_public_pem =
    "-----BEGIN PUBLIC KEY-----\n"
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC7+O+PUOBzPr8TfkL9V8RnxVfl\n"
    "X1/CIM9a0Vffex5uvAzU12I7y28auNs/EQqL/dmQaNGAhpA03jF8L8tV9tzVqDRj\n"
    "uTw7+zsjyfUs5HsgPswc7q5JvcuWiYedq96LOUe3pEK9yoHJuBsqjlFkdNYRl1iT\n"
    "Ixiugnm47vzH2w9ooQIDAQAB\n"
    "-----END PUBLIC KEY-----\n";

// A message Python signed with the key, with RSASSA-PSS over SHA-256, MGF1
// over SHA-256, and a 32-byte salt, and the signature.
inline constexpr auto rsa_message = "Libranet RSA test message";
inline constexpr auto rsa_pss_signature =
    "511bd66b5f59c5765581d5ccd5d6d0a6a542365e9afc23363be6f8506f6526da"
    "c1c159902f076485b9b96be65d60c4119879ba9db70f19f10038409c445a954e"
    "89fa56d892739375473326246b3de86e7f77ec8e78380e9fb81e2638bfe21e9e"
    "674097bfc789f6376a768500fcd55709ad940174e041c4369c3b0c6368387fb5";

// A message Python encrypted with the key, with RSAES-OAEP over SHA-256 and
// MGF1 over SHA-256, and the ciphertext.
inline constexpr auto rsa_oaep_plaintext = "Confidential Payload";
inline constexpr auto rsa_oaep_ciphertext =
    "3c86ed708ae534bc096425b1a24a5004e16291d51fcb4468aecd943f3b613a67"
    "95457dcc24b13aaf84ff0986af8df3a14e7853aa5bc219c5df69f8a446f38cbd"
    "208192375e5ef6e0b3a70bdade237c63c3f7649e45eb61d8c7621ba18a0e3de2"
    "409bdd92c60bcff1f2a2b4723096573f6ad551329c581102c5a13291df491ae6";

} // namespace fixtures
