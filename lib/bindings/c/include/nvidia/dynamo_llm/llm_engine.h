#ifndef __NVIDIA_DYNAMO_LLM_API__
#define __NVIDIA_DYNAMO_LLM_API__

#include <cstdarg>
#include <cstdint>
#include <cstdlib>
#include <ostream>
#include <new>

enum dynamo_llm_result_t : uint32_t {
  OK = 0,
  ERR = 1,
};

#endif  // __NVIDIA_DYNAMO_LLM_API__
