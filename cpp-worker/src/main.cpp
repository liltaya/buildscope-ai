#include "buildscope_cpp/version.hpp"

#include <iostream>

int main()
{
    std::cout << "BuildScope C++ worker " << buildscope::version() << '\n';

    return 0;
}
