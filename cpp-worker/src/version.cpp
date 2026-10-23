#include "buildscope_cpp/version.hpp"

namespace buildscope
{

std::string_view version() noexcept
{
    return kVersion;
}

} // namespace buildscope
