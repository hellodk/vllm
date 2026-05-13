                Developers
          (VS Code / Cursor / JetBrains)
                       │
                       │ OpenAI Compatible API
                       ▼
                API Gateway Layer
              (LiteLLM / FastAPI)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   Routing Layer   Telemetry      Auth Layer
   (model select)  (Prometheus)   (OAuth/JWT)
        │
        ▼
  Inference Cluster
 ┌───────────┬───────────┬───────────┐
 ▼           ▼           ▼
GPU Nodes   Mac Mini     CPU Nodes
(vLLM)      llama.cpp    fallback
        │
        ▼
  Model Storage
 (Artifactory / S3 / HF mirror)



Continue.dev (VS Code)
        │
LiteLLM Gateway
        │
NGINX
        │
Mac Mini LLM farm
(llama.cpp / vLLM)


Model storage → JFrog Artifactory

LiteLLM
It acts as:
OpenAI proxy
rate limiter
model router
caching layer

LiteLLM routing example:
model_list:
  - model_name: coder
    litellm_params:
      model: deepseek-coder
      api_base: http://macmini01:8080

  - model_name: chat
    litellm_params:
      model: llama3
      api_base: http://gpu01:8000



Security
For enterprise environments:
OAuth2
JWT tokens
API keys
prompt logging
PII filtering

Future Optimization
Speculative decoding
KV cache sharing
Mixture-of-Experts routing
prompt compression
These can improve performance 3–6×

