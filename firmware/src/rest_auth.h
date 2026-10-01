#pragma once
namespace ems {
// Empty key supports local development; deployments configure EMS_API_KEY in secrets.h.
template <typename HttpRequest>
void addApiKeyHeader(HttpRequest& request, const char* api_key) {
    if (api_key && api_key[0]) request.addHeader("X-API-Key", api_key);
}
}
