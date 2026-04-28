"""
LLM Telemetry SDK for Python

Provides comprehensive instrumentation for LLM inference workloads including:
- Inference latency and throughput metrics
- Token processing statistics
- GPU memory tracking
- Hallucination detection signals (entropy, repetition, confidence)
- Error classification and model health scoring

Usage:
    from llm_telemetry import LLMTelemetry, init_telemetry
    
    # Initialize OTLP exporter
    init_telemetry(endpoint="localhost:4317", service_name="my-llm-service")
    
    # Create telemetry instance for your model
    telemetry = LLMTelemetry(model_name="llama-3-70b")
    
    # Record inference metrics
    telemetry.record_inference(
        prompt_tokens=100,
        output_tokens=50,
        prompt_latency_s=0.5,
        inference_latency_s=2.0,
        token_probs=[0.9, 0.8, 0.7, ...],
        output_text="Generated response..."
    )
"""

from .telemetry import LLMTelemetry, init_telemetry
from .metrics import (
    LLMMetrics,
    HallucinationMetrics,
    ErrorType,
    ModelHealthScore,
)
from .detection import (
    compute_entropy,
    compute_repetition_score,
    compute_perplexity,
    detect_refusal,
)

__version__ = "1.0.0"
__all__ = [
    "LLMTelemetry",
    "init_telemetry",
    "LLMMetrics",
    "HallucinationMetrics",
    "ErrorType",
    "ModelHealthScore",
    "compute_entropy",
    "compute_repetition_score",
    "compute_perplexity",
    "detect_refusal",
]
