cmake_minimum_required(VERSION 3.20)
project(recon_engine VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

include(FetchContent)

# Ubuntu noble's libpqxx package ships headers and library built with
# mismatched exception ABI; build a known-good version from source instead.
FetchContent_Declare(
    libpqxx
    GIT_REPOSITORY https://github.com/jtv/libpqxx.git
    GIT_TAG 7.9.2
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(libpqxx)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(
    -Wall -Wextra -Wpedantic
    -Wconversion -Wsign-conversion
    -Wshadow -Wnon-virtual-dtor
)

option(BUILD_TESTS "Build tests" ON)
option(ENABLE_OPENMP "Enable OpenMP for parallel algorithms" ON)

if(ENABLE_OPENMP)
    find_package(OpenMP REQUIRED)
    add_compile_options($<$<CXX_COMPILER_ID:GNU,Clang>:${OpenMP_CXX_FLAGS}>)
    add_link_options($<$<CXX_COMPILER_ID:GNU,Clang>:${OpenMP_CXX_FLAGS}>)
endif()

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)

add_executable(recon_engine
    src/main.cpp
    src/types.cpp
    src/file_parser.cpp
    src/matcher.cpp
    src/db_writer.cpp
)

target_link_libraries(recon_engine PRIVATE libpqxx::pqxx)
if(ENABLE_OPENMP)
    target_link_libraries(recon_engine PRIVATE OpenMP::OpenMP_CXX)
endif()

if(BUILD_TESTS)
    enable_testing()
    find_package(GTest REQUIRED)

    add_executable(recon_tests
        tests/test_types.cpp
        tests/test_file_parser.cpp
        tests/test_matcher.cpp
        src/types.cpp
        src/file_parser.cpp
        src/matcher.cpp
        src/db_writer.cpp
    )

    target_link_libraries(recon_tests PRIVATE GTest::GTest GTest::Main libpqxx::pqxx)
    if(ENABLE_OPENMP)
        target_link_libraries(recon_tests PRIVATE OpenMP::OpenMP_CXX)
    endif()

    add_test(NAME recon_tests COMMAND recon_tests)
endif()

install(TARGETS recon_engine DESTINATION bin)
