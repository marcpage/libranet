// Core Foundation is Apple's, so these tests are built only on macOS.
#if __APPLE__

#include <gtest/gtest.h>

#include <CoreFoundation/CFBase.h>
#include <CoreFoundation/CFData.h>

#include "crypto/CFObject.hpp"

using crypto::CFObject;

namespace {

using Data = CFObject<CFMutableDataRef>;

// New data, with one reference, which the caller holds.
CFMutableDataRef new_data() {
    return CFDataCreateMutable(kCFAllocatorDefault, 0);
}

} // namespace

TEST(CFObjectTest, RetainAddsAReference) {
    auto* const data = new_data();

    {
        const Data held(data, Data::Policy::Retain);

        EXPECT_EQ(CFGetRetainCount(data), 2);
    }

    EXPECT_EQ(CFGetRetainCount(data), 1);
    CFRelease(data);
}

TEST(CFObjectTest, TakeOwnershipTakesTheReference) {
    auto* const data = new_data();

    // A second reference, to look at the data with once it is let go.
    CFRetain(data);

    {
        const Data held(data, Data::Policy::TakeOwnership);

        EXPECT_EQ(CFGetRetainCount(data), 2);
    }

    EXPECT_EQ(CFGetRetainCount(data), 1);
    CFRelease(data);
}

TEST(CFObjectTest, ReleaseHandsTheReferenceBack) {
    Data held(new_data(), Data::Policy::TakeOwnership);

    const auto* const data = held.release();

    EXPECT_FALSE(held);
    EXPECT_EQ(CFGetRetainCount(data), 1);
    CFRelease(data);
}

#endif // __APPLE__
