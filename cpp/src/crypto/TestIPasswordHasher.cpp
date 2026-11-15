#include <iostream>
#include <cassert>
#include <string>

#include "IPasswordHasher.hpp"

using namespace crypto;

void test_argon2id_hashing_and_verification() {
    std::cout << "-> Testing Argon2id password hashing and verification... " << std::flush;

    // Instantiate Argon2id via the factory with standard parameters:
    // 64 MiB memory, 3 iterations, 1 parallelism, 16-byte salt, 32-byte hash length
    auto hasher = IPasswordHasher::Argon2id(65536, 3, 1, 16, 32);

    std::string password = "SecurePassword123!";
    std::string hash = hasher->hash_password(password);

    // Ensure hash is generated and follows the PHC string format for argon2id
    assert(!hash.empty());
    assert(hash.rfind("$argon2id$", 0) == 0);

    // Verify correct password succeeds
    assert(hasher->verify_password(password, hash));

    // Verify incorrect password fails
    assert(!hasher->verify_password("WrongPassword123!", hash));

    std::cout << "PASSED\n";
}

void test_unique_salts() {
    std::cout << "-> Testing unique salts produce distinct hashes for identical passwords... " << std::flush;

    auto hasher = IPasswordHasher::Argon2id(16384, 2, 1, 16, 32);

    std::string password = "SamePassword";
    std::string hash1 = hasher->hash_password(password);
    std::string hash2 = hasher->hash_password(password);

    // Hashes must be unique due to cryptographically secure random salting
    assert(hash1 != hash2);

    // Both hashes must verify successfully against the original password
    assert(hasher->verify_password(password, hash1));
    assert(hasher->verify_password(password, hash2));

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running IPasswordHasher Test Suite    \n";

    test_argon2id_hashing_and_verification();
    test_unique_salts();

    std::cout << "   All Tests Passed Successfully!   \n";
    return 0;
}
