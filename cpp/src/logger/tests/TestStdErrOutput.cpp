#include <gtest/gtest.h>

#include "logger/StdErrOutput.hpp"

using logger::StdErrOutput;

TEST(StdErrOutputTest, WritesToStderr) {
    StdErrOutput output;

    testing::internal::CaptureStderr();
    output.log("to stderr\n");

    EXPECT_EQ(testing::internal::GetCapturedStderr(), "to stderr\n");
}
