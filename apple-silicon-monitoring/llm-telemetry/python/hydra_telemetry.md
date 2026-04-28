# LLM Telemetry SDK for Python

Comprehensive, hardware-agnostic observability SDK for LLM inference workloads with built-in hallucination detection. Works on Apple Silicon, NVIDIA, AMD, and CPU-only setups.

## Features

- **Inference Metrics**: Latency (prompt/generation/total phases), throughput, token counts
- **GPU Monitoring**: Memory allocation, KV cache utilization
- **Hallucination Detection**: Entropy, repetition, perplexity, confidence analysis
- **Error Classification**: 15 categorized error types for debugging
- **Model Health Scoring**: Composite health score (0-100) from multiple weighted signals
- **Context Manager**: Automatic timing with `inference_span`

## Installation

```bash
pip install llm-telemetry
```

Or install from source:

```bash
git clone https://github.com/company/llm-telemetry
cd llm-telemetry/python
pip install -e .
```

## Quick Start

```python
from llm_telemetry import LLMTelemetry, init_telemetry

# Initialize OTLP exporter (connects to OTEL Collector)
init_telemetry(
    endpoint="localhost:4317",
    service_name="my-llm-service",
)

# Create telemetry instance for your model
telemetry = LLMTelemetry(model_name="llama-3-70b")

# Record inference metrics
telemetry.record_inference(
    prompt_tokens=100,
    output_tokens=50,
    prompt_latency_s=0.5,
    inference_latency_s=2.0,
    token_probs=[0.9, 0.85, 0.7, ...],  # Optional: for hallucination detection
    output_text="Generated response...",  # Optional: for text analysis
)

# Record GPU memory
telemetry.record_gpu_memory(allocated_bytes=8_000_000_000)

# Mark model as loaded
telemetry.set_model_loaded(True)
```

## Using the Context Manager

For automatic timing:

```python
with telemetry.inference_span(prompt_tokens=100) as span:
    # Process prompt
    result = model.prefill(prompt)
    span.mark_prompt_done()
    
    # Generate output
    output = model.generate(result)
    span.set_output(
        output_tokens=len(output.tokens),
        token_probs=output.probabilities,
        text=output.text,
    )
# Metrics are automatically recorded when the context exits
```

## Hallucination Detection

The SDK provides multiple signals for detecting potential hallucinations:

```python
from llm_telemetry import compute_entropy, compute_repetition_score, analyze_text_quality

# Compute individual signals
entropy = compute_entropy(token_probs)
repetition = compute_repetition_score(output_text)

# Or get a comprehensive analysis
analysis = analyze_text_quality(output_text, token_probs)
print(f"Hallucination risk: {analysis['hallucination_risk']:.2f}")
```

### Hallucination Signals

| Signal | Description | Threshold |
|--------|-------------|-----------|
| `entropy` | Token probability entropy (higher = uncertain) | > 4.0 |
| `perplexity` | Model uncertainty | > 50 |
| `repetition_score` | N-gram repetition (0-1) | > 0.3 |
| `confidence_mean` | Average token confidence | < 0.5 |
| `is_refusal` | Detected refusal response | True |

## Metrics Exposed

### Inference Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `llm_inference_requests_total` | Counter | Total requests by status |
| `llm_inference_duration_seconds` | Histogram | Latency by phase |
| `llm_tokens_processed_total` | Counter | Tokens by direction |
| `llm_tokens_per_second` | Gauge | Current throughput |

### Hallucination Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `llm_output_entropy` | Histogram | Token entropy distribution |
| `llm_confidence_mean` | Gauge | Mean token confidence |
| `llm_repetition_score` | Gauge | N-gram repetition |
| `llm_perplexity` | Histogram | Output perplexity |

### Resource Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `llm_gpu_memory_allocated_bytes` | Gauge | GPU memory used |
| `llm_kv_cache_utilization` | Gauge | KV cache usage (0-1) |
| `llm_queue_depth` | Gauge | Pending requests |

### Health Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `llm_model_loaded` | Gauge | Model availability |
| `llm_model_health_score` | Gauge | Composite health (0-100) |
| `llm_error_total` | Counter | Errors by type |

## Error Types

```python
from llm_telemetry import ErrorType

# Input errors
ErrorType.INVALID_INPUT
ErrorType.CONTEXT_TOO_LONG
ErrorType.EMPTY_PROMPT

# Resource errors
ErrorType.OUT_OF_MEMORY
ErrorType.GPU_MEMORY_EXHAUSTED

# Timeout errors
ErrorType.TIMEOUT
ErrorType.GENERATION_TIMEOUT

# Hardware errors
ErrorType.KERNEL_FAILURE
ErrorType.METAL_CRASH        # Apple Silicon specific
ErrorType.DRIVER_ERROR

# Model errors
ErrorType.MODEL_NOT_LOADED
ErrorType.MODEL_CORRUPTED
ErrorType.TOKENIZATION_ERROR

# Network errors
ErrorType.NETWORK_ERROR
ErrorType.CONNECTION_RESET

ErrorType.UNKNOWN
```

Errors are automatically classified from exception type and message — e.g., a `MemoryError` maps to `OUT_OF_MEMORY`.

## Integration Examples

### With vLLM (NVIDIA or CPU)

```python
from vllm import LLM, SamplingParams
from llm_telemetry import LLMTelemetry, init_telemetry

init_telemetry(endpoint="localhost:4317")
telemetry = LLMTelemetry(model_name="llama-3-70b", gpu_id="gpu0")

llm = LLM(model="meta-llama/Llama-3-70B-Instruct")
telemetry.set_model_loaded(True)

def generate(prompts):
    sampling_params = SamplingParams(temperature=0.7, max_tokens=256)
    with telemetry.inference_span(prompt_tokens=sum(len(p.split()) for p in prompts)) as span:
        span.mark_prompt_done()
        outputs = llm.generate(prompts, sampling_params)
        total_tokens = sum(len(o.outputs[0].token_ids) for o in outputs)
        span.set_output(output_tokens=total_tokens)
    return outputs
```

### With llama.cpp Python bindings

```python
from llama_cpp import Llama
from llm_telemetry import LLMTelemetry, init_telemetry

init_telemetry(endpoint="localhost:4317")
telemetry = LLMTelemetry(model_name="llama-3-8b")

llm = Llama(model_path="./model.gguf")
telemetry.set_model_loaded(True)

def generate(prompt):
    with telemetry.inference_span(prompt_tokens=len(prompt.split())) as span:
        span.mark_prompt_done()
        
        output = llm(prompt, max_tokens=100)
        
        span.set_output(
            output_tokens=output["usage"]["completion_tokens"],
            text=output["choices"][0]["text"],
        )
    
    return output
```

### With MLX (Apple Silicon)

```python
import mlx.core as mx
from mlx_lm import load, generate
from llm_telemetry import LLMTelemetry, init_telemetry

init_telemetry(endpoint="localhost:4317")
telemetry = LLMTelemetry(model_name="mlx-llama")

model, tokenizer = load("mlx-community/Llama-3-8B-Instruct")
telemetry.set_model_loaded(True)
telemetry.record_gpu_memory(mx.metal.get_active_memory())

# Record after each generation
response = generate(model, tokenizer, prompt="Hello")
telemetry.record_inference(...)
```

### With Ollama (Any Platform)

```python
import requests
from llm_telemetry import LLMTelemetry, init_telemetry

init_telemetry(endpoint="localhost:4317")
telemetry = LLMTelemetry(model_name="llama3")
telemetry.set_model_loaded(True)

def generate(prompt):
    with telemetry.inference_span(prompt_tokens=len(prompt.split())) as span:
        span.mark_prompt_done()
        resp = requests.post("http://localhost:11434/api/generate", json={
            "model": "llama3", "prompt": prompt, "stream": False,
        })
        data = resp.json()
        span.set_output(
            output_tokens=data.get("eval_count", 0),
            text=data.get("response", ""),
        )
    return data["response"]
```

## Configuration

### Environment Variables

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
export OTEL_SERVICE_NAME=my-llm-service
export OTEL_SERVICE_VERSION=1.0.0
```

### Programmatic Configuration

```python
init_telemetry(
    endpoint="localhost:4317",
    service_name="my-llm-service",
    service_version="1.0.0",
    export_interval_ms=10000,  # Export every 10s
    insecure=True,  # Use insecure connection
    headers={"Authorization": "Bearer token"},  # Optional auth
)
```

## Health Scoring

The SDK computes a composite model health score (0-100) from weighted components:

| Component | Weight | Description |
|-----------|--------|-------------|
| Availability | 25% | Uptime ratio |
| Latency | 25% | P99 latency vs 5s threshold |
| Errors | 20% | Error rate vs 1% threshold |
| Quality | 15% | Hallucination risk (entropy, repetition, confidence) |
| Resources | 15% | GPU memory utilization vs 95% threshold |

A model is considered "healthy" if its score is >= 80 with at most 1 degraded component.

## Dependencies

- `opentelemetry-api` >= 1.20.0
- `opentelemetry-sdk` >= 1.20.0
- `opentelemetry-exporter-otlp-proto-grpc` >= 1.20.0
- Optional: `numpy` for faster numerical operations

## License

Apache 2.0
