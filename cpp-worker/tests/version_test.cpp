#include "buildscope_cpp/version.hpp"

#include <gtest/gtest.h>

#include <string_view>

TEST(VersionTest, ReturnsCurrentVersion)
{
    EXPECT_EQ(buildscope::version(), std::string_view{"0.1.0"});
}

TEST(VersionTest, VersionIsNotEmpty)
{
    EXPECT_FALSE(buildscope::version().empty());
}

TEST(VersionTest, ConstantMatchesFunction)
{
    EXPECT_EQ(buildscope::version(), buildscope::kVersion);
}