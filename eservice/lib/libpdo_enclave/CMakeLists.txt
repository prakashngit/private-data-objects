# Copyright 2018 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

CMAKE_MINIMUM_REQUIRED(VERSION 3.2 FATAL_ERROR)
PROJECT(libpdo-enclave C CXX)

# XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
# Source Code
# XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
FILE(GLOB PROJECT_HEADERS *.h state/*.h ${PDO_TOP_DIR}/common/*.h)
FILE(GLOB PROJECT_SOURCES *.cpp *.c state/*.cpp)
FILE(GLOB PROJECT_EDL enclave.edl)
FILE(GLOB PROJECT_CONFIG *.xml)
SOURCE_GROUP("Source" FILES ${PROJECT_HEADERS} ${PROJECT_SOURCES} ${PROJECT_EDL})

SET (LIBPDO_ENCLAVE_EDL ${PROJECT_EDL} PARENT_SCOPE)
SET (LIBPDO_ENCLAVE_INCLUDE ${CMAKE_CURRENT_SOURCE_DIR} PARENT_SCOPE)

INCLUDE_DIRECTORIES(/opt/intel/sgxssl/include)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/cdi)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/crypto)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/state)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/interpreter)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/packages/parson)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/packages/base64)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/packages/tinyscheme)
INCLUDE_DIRECTORIES(${PDO_TOP_DIR}/common/packages/block_store)

# If are compiling for Linux, then we have special linking for the trusted
# runtime service
IF(${SGX_USE_SIMULATOR})
    SET(TRTS_LIBRARY_NAME "sgx_trts_sim")
    SET(SERVICE_LIBRARY_NAME "sgx_tservice_sim")
ELSE()
    SET(TRTS_LIBRARY_NAME "sgx_trts")
    SET(SERVICE_LIBRARY_NAME "sgx_tservice")
ENDIF()

SGX_EDGE_TRUSTED(${PROJECT_EDL} PROJECT_EDGE_SOURCES)

SOURCE_GROUP("Generated" FILES ${PROJECT_EDGE_SOURCES})

# These compiler and linker flags for g++/gcc were derived from the make
# files provided with the SGX sample enclave
# To generate a proper enclave, it is recommended to follow below guideline
# to link the trusted libraries:
#    1. Link sgx_trts with the `--whole-archive' and `--no-whole-archive'
#       options, so that the whole content of trts is included in the
#       enclave.
#    2. For other libraries, you just need to pull the required symbols.
#       Use `--start-group' and `--end-group' to link these libraries.
# Do NOT move the libraries linked with `--start-group' and `--end-group'
# within `--whole-archive' and `--no-whole-archive' options.  Otherwise,
# you may get some undesirable errors.

SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -m64")
IF($ENV{PDO_DEBUG_BUILD})
  SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -g")
  SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Og")
  ADD_DEFINITIONS(-DPDO_DEBUG_BUILD=1)
ELSE()
  SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2")
  ADD_DEFINITIONS(-DPDO_DEBUG_BUILD=0)
ENDIF()

IF ($ENV{PDO_INTERPRETER} STREQUAL "gipsy")
  ADD_DEFINITIONS(-DUSE_GIPSY_INTERPRETER)
  SET(INTERPRETER_LIBS "-lgipsy -ltinyscheme")
ELSEIF ($ENV{PDO_INTERPRETER} STREQUAL "wawaka" OR
    $ENV{PDO_INTERPRETER} STREQUAL "wawaka-opt" OR
    $ENV{PDO_INTERPRETER} STREQUAL "wawaka-aot")
  SET(INTERPRETER_LIBS "-liwasm -lwwasm")
ELSE()
  MESSAGE(FATAL_ERROR "Unknown PDO_INTERPRETER, $ENV{PDO_INTERPRETER}!")
ENDIF()

SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -nostdinc")
SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -fvisibility=hidden")
SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -fpie")
SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -fstack-protector")
SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++11")
SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -nostdinc++")

SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -m64")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -O2")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,--no-undefined")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -nostdlib")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -nodefaultlibs")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -nostartfiles")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-Bstatic")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-Bsymbolic")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,--no-undefined")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-pie,-eenclave_entry")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,--export-dynamic")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,--defsym,__ImageBase=0")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/pdo_enclave.lds")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-L,$ENV{SGX_SDK}/lib64")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-L,$ENV{SGX_SSL}/lib64")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-L,$ENV{SGX_SSL}/lib64/release")
SET(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-L,${PDO_TOP_DIR}/common/build")

# Remove all the default windows libraries (kernel32, etc)
SET(CMAKE_C_STANDARD_LIBRARIES_INIT "")
SET(CMAKE_C_STANDARD_LIBRARIES "")
SET(CMAKE_CXX_STANDARD_LIBRARIES "")

# XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
# Compile targets
# XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

# CMake likes to prepend "lib" to project name when creating library...since we
# already have lib in the project name, liblibpdo-enclave is stupid.  Stop
# CMake from doing it.
SET(CMAKE_SHARED_LIBRARY_PREFIX "")

ADD_LIBRARY(${PROJECT_NAME} SHARED  ${PROJECT_HEADERS} ${PROJECT_SOURCES} ${PROJECT_EDGE_SOURCES} ${PROJECT_EDL})
SGX_SIGN_ENCLAVE(${PROJECT_NAME} ${PDO_ENCLAVE_CODE_SIGN_PEM} ${PROJECT_CONFIG})

TARGET_LINK_LIBRARIES(${PROJECT_NAME} "${PDO_TOP_DIR}/common/build/libtpdo-common.a")
TARGET_LINK_LIBRARIES(${PROJECT_NAME} "${PDO_TOP_DIR}/common/build/libpdo_contracts.a")
IF ($ENV{PDO_INTERPRETER} STREQUAL "gipsy")
  TARGET_LINK_LIBRARIES(${PROJECT_NAME} "${PDO_TOP_DIR}/common/build/libgipsy.a")
  TARGET_LINK_LIBRARIES(${PROJECT_NAME} "${PDO_TOP_DIR}/common/build/libtinyscheme.a")
ELSEIF ($ENV{PDO_INTERPRETER} STREQUAL "wawaka" OR
        $ENV{PDO_INTERPRETER} STREQUAL "wawaka-opt" OR
        $ENV{PDO_INTERPRETER} STREQUAL "wawaka-aot")
  TARGET_LINK_LIBRARIES(${PROJECT_NAME} "${PDO_TOP_DIR}/common/build/libwwasm.a")
  TARGET_LINK_LIBRARIES(${PROJECT_NAME} "${PDO_TOP_DIR}/common/build/libiwasm.a")
ENDIF()

TARGET_LINK_LIBRARIES(${PROJECT_NAME} -Wl,--whole-archive -lsgx_tsgxssl -Wl,--no-whole-archive)
TARGET_LINK_LIBRARIES(${PROJECT_NAME} -Wl,--whole-archive -l${TRTS_LIBRARY_NAME} -Wl,--no-whole-archive)
TARGET_LINK_LIBRARIES(${PROJECT_NAME} -Wl,--start-group ${INTERPRETER_LIBS} -lpdo_contracts -Wl,--end-group)
TARGET_LINK_LIBRARIES(${PROJECT_NAME} -Wl,--start-group -ltpdo-crypto -lsgx_tsgxssl_crypto -lsgx_tstdc -lsgx_tcxx -lsgx_tcrypto -l${SERVICE_LIBRARY_NAME} -Wl,--end-group)

TARGET_INCLUDE_DIRECTORIES( ${PROJECT_NAME} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR} ${SGX_ENCLAVE_INCLUDE})

ADD_CUSTOM_COMMAND(
    TARGET ${PROJECT_NAME}
    POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "${DEPS_DIR}/bin"
    COMMAND ${CMAKE_COMMAND} -E copy "${SIGNED_ENCLAVE}" "${DEPS_DIR}/bin"
)
