"""
Main telemetry module for LLM observability
"""

import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from contextlib import contextmanager

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION

from .detection import (
    compute_entropy,
    compute_repetition_score,
    compute_perplexity,
    detect_refusal,
    compute_confidence_stats,
)
from .metrics import ErrorType

logger = logging.getLogger(__name__)

# Global meter provider
_meter_provider: Optional[MeterProvider] = None


def init_telemetry(
    endpoint: str = "localhost:4317",
    service_name: str = "llm-inference",
    service_version: str = "1.0.0",
    export_interval_ms: int = 10000,
    insecure: bool = True,
    headers: Optional[Dict[str, str]] = None,
) -> MeterProvider:
    """
    Initialize OpenTelemetry metrics with OTLP exporter.
    
    Args:
        endpoint: OTLP collector endpoint
        service_name: Name of the service
        service_version: Version of the service
        export_interval_ms: How often to export metrics
        insecure: Whether to use insecure connection
        headers: Optional headers for authentication
    
    Returns:
        Configured MeterProvider
    """
    global _meter_provider
    
    if _meter_provider is not None:
        logger.warning("Telemetry already initialized, returning existing provider")
        return _meter_provider
    
    # Create resource with service info
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "platform": "apple-silicon",
    })
    
    # Create OTLP exporter
    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=insecure,
        headers=headers or {},
    )
    
    # Create metric reader
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=export_interval_ms,
    )
    
    # Create and set meter provider
    _meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[reader],
    )
    metrics.set_meter_provider(_meter_provider)
    
    logger.info(f"Initialized LLM telemetry, exporting to {endpoint}")
    return _meter_provider


@dataclass
class InferenceResult:
    """Result of an inference operation"""
    prompt_tokens: int
    output_tokens: int
    prompt_latency_s: float
    inference_latency_s: float
    token_probs: Optional[List[float]] = None
    output_text: Optional[str] = None
    error: Optional[ErrorType] = None
    context_length: Optional[int] = None
    batch_size: int = 1


class LLMTelemetry:
    """
    Telemetry collector for LLM inference workloads.
    
    Provides metrics for:
    - Request throughput and latency
    - Token processing statistics
    - GPU memory allocation
    - Hallucination detection signals
    - Error classification
    - Model health scoring
    """
    
    def __init__(
        self,
        model_name: str,
        model_version: str = "unknown",
        gpu_id: str = "0",
    ):
        """
        Initialize telemetry for a specific model.
        
        Args:
            model_name: Name of the model (e.g., "llama-3-70b")
            model_version: Version of the model
            gpu_id: GPU identifier
        """
        self.model_name = model_name
        self.model_version = model_version
        self.gpu_id = gpu_id
        self.labels = {
            "model": model_name,
            "model_version": model_version,
            "gpu": gpu_id,
        }
        
        # Get meter
        self.meter = metrics.get_meter("llm-inference", "1.0.0")
        
        # Initialize metrics
        self._init_metrics()
        
        # State tracking
        self._request_count = 0
        self._error_count = 0
        self._last_entropy_values: List[float] = []
        
    def _init_metrics(self):
        """Initialize all metric instruments"""
        
        # Request metrics
        self.inference_requests = self.meter.create_counter(
            "llm_inference_requests_total",
            description="Total number of inference requests",
            unit="1",
        )
        
        self.inference_duration = self.meter.create_histogram(
            "llm_inference_duration_seconds",
            description="Inference latency by phase (prompt/generation)",
            unit="s",
        )
        
        # Token metrics
        self.tokens_processed = self.meter.create_counter(
            "llm_tokens_processed_total",
            description="Total tokens processed (input/output)",
            unit="1",
        )
        
        self.tokens_per_second = self.meter.create_gauge(
            "llm_tokens_per_second",
            description="Current token generation throughput",
            unit="1/s",
        )
        
        # Batch and context
        self.batch_size_hist = self.meter.create_histogram(
            "llm_batch_size",
            description="Batch sizes processed",
            unit="1",
        )
        
        self.context_length_hist = self.meter.create_histogram(
            "llm_context_length",
            description="Context window utilization",
            unit="1",
        )
        
        # GPU memory
        self.gpu_memory_allocated = self.meter.create_gauge(
            "llm_gpu_memory_allocated_bytes",
            description="GPU memory allocated for model",
            unit="By",
        )
        
        # KV cache
        self.kv_cache_utilization = self.meter.create_gauge(
            "llm_kv_cache_utilization",
            description="KV cache utilization (0-1)",
            unit="1",
        )
        
        # Queue depth
        self.queue_depth = self.meter.create_gauge(
            "llm_queue_depth",
            description="Number of pending requests",
            unit="1",
        )
        
        # Errors
        self.errors_total = self.meter.create_counter(
            "llm_error_total",
            description="Total errors by type",
            unit="1",
        )
        
        # Model status
        self.model_loaded = self.meter.create_gauge(
            "llm_model_loaded",
            description="Whether the model is loaded and ready",
            unit="1",
        )
        
        # Hallucination detection metrics
        self.output_entropy = self.meter.create_histogram(
            "llm_output_entropy",
            description="Token probability entropy (higher = more uncertain)",
            unit="1",
        )
        
        self.confidence_mean = self.meter.create_gauge(
            "llm_confidence_mean",
            description="Mean token confidence score",
            unit="1",
        )
        
        self.confidence_std = self.meter.create_gauge(
            "llm_confidence_std",
            description="Standard deviation of token confidence",
            unit="1",
        )
        
        self.repetition_score = self.meter.create_gauge(
            "llm_repetition_score",
            description="N-gram repetition score (0-1)",
            unit="1",
        )
        
        self.perplexity = self.meter.create_histogram(
            "llm_perplexity",
            description="Output perplexity",
            unit="1",
        )
        
        self.refusal_rate = self.meter.create_gauge(
            "llm_refusal_rate",
            description="Rate of refusal responses",
            unit="1",
        )
        
        # Health score
        self.model_health_score = self.meter.create_gauge(
            "llm_model_health_score",
            description="Composite model health score (0-100)",
            unit="1",
        )
    
    def record_inference(
        self,
        prompt_tokens: int,
        output_tokens: int,
        prompt_latency_s: float,
        inference_latency_s: float,
        token_probs: Optional[List[float]] = None,
        output_text: Optional[str] = None,
        error: Optional[ErrorType] = None,
        context_length: Optional[int] = None,
        batch_size: int = 1,
    ):
        """
        Record metrics for a completed inference.
        
        Args:
            prompt_tokens: Number of tokens in the prompt
            output_tokens: Number of generated tokens
            prompt_latency_s: Time to process prompt (prefill)
            inference_latency_s: Time to generate output (decode)
            token_probs: Optional list of token probabilities for hallucination detection
            output_text: Optional generated text for analysis
            error: Optional error type if inference failed
            context_length: Optional total context length
            batch_size: Number of requests in batch
        """
        base_labels = self.labels.copy()
        
        # Request count
        status = "error" if error else "success"
        self.inference_requests.add(1, {**base_labels, "status": status})
        self._request_count += 1
        
        # Error handling
        if error:
            self.errors_total.add(1, {**base_labels, "error_type": error.value})
            self._error_count += 1
            return
        
        # Latency metrics
        self.inference_duration.record(prompt_latency_s, {**base_labels, "phase": "prompt"})
        self.inference_duration.record(inference_latency_s, {**base_labels, "phase": "generation"})
        
        total_latency = prompt_latency_s + inference_latency_s
        self.inference_duration.record(total_latency, {**base_labels, "phase": "total"})
        
        # Token metrics
        self.tokens_processed.add(prompt_tokens, {**base_labels, "direction": "input"})
        self.tokens_processed.add(output_tokens, {**base_labels, "direction": "output"})
        
        # Throughput
        if inference_latency_s > 0:
            tps = output_tokens / inference_latency_s
            self.tokens_per_second.set(tps, base_labels)
        
        # Batch and context
        self.batch_size_hist.record(batch_size, base_labels)
        if context_length:
            self.context_length_hist.record(context_length, base_labels)
        
        # Hallucination detection
        if token_probs:
            self._record_hallucination_metrics(token_probs, output_text, base_labels)
    
    def _record_hallucination_metrics(
        self,
        token_probs: List[float],
        output_text: Optional[str],
        labels: Dict[str, str],
    ):
        """Record hallucination detection metrics"""
        
        # Entropy
        entropy = compute_entropy(token_probs)
        self.output_entropy.record(entropy, labels)
        self._last_entropy_values.append(entropy)
        if len(self._last_entropy_values) > 100:
            self._last_entropy_values.pop(0)
        
        # Confidence statistics
        mean_conf, std_conf = compute_confidence_stats(token_probs)
        self.confidence_mean.set(mean_conf, labels)
        self.confidence_std.set(std_conf, labels)
        
        # Perplexity
        ppl = compute_perplexity(token_probs)
        self.perplexity.record(ppl, labels)
        
        # Text-based metrics
        if output_text:
            # Repetition
            rep_score = compute_repetition_score(output_text, n=3)
            self.repetition_score.set(rep_score, labels)
            
            # Refusal detection
            is_refusal = detect_refusal(output_text)
            # Update running refusal rate
            # This is simplified; in production use a proper windowed rate
            self.refusal_rate.set(1.0 if is_refusal else 0.0, labels)
    
    def record_gpu_memory(self, allocated_bytes: int):
        """Record current GPU memory allocation"""
        self.gpu_memory_allocated.set(allocated_bytes, self.labels)
    
    def record_kv_cache(self, utilization: float):
        """Record KV cache utilization (0-1)"""
        self.kv_cache_utilization.set(utilization, self.labels)
    
    def record_queue_depth(self, depth: int):
        """Record current queue depth"""
        self.queue_depth.set(depth, self.labels)
    
    def set_model_loaded(self, loaded: bool):
        """Set model loaded status"""
        self.model_loaded.set(1.0 if loaded else 0.0, self.labels)
    
    def compute_health_score(self) -> float:
        """
        Compute a composite health score (0-100) based on recent metrics.
        
        Factors:
        - Error rate (lower is better)
        - Average entropy (lower is better)
        - Repetition score (lower is better)
        
        Returns:
            Health score from 0 to 100
        """
        score = 100.0
        
        # Error rate penalty (up to 50 points)
        if self._request_count > 0:
            error_rate = self._error_count / self._request_count
            score -= min(50, error_rate * 100)
        
        # Entropy penalty (up to 25 points)
        if self._last_entropy_values:
            avg_entropy = sum(self._last_entropy_values) / len(self._last_entropy_values)
            # Normalize: entropy > 4 is concerning
            entropy_penalty = min(25, (avg_entropy / 4) * 25)
            score -= entropy_penalty
        
        # Ensure score is in valid range
        score = max(0, min(100, score))
        
        # Record the score
        self.model_health_score.set(score, self.labels)
        
        return score
    
    @contextmanager
    def inference_span(self, prompt_tokens: int, batch_size: int = 1):
        """
        Context manager for timing an inference operation.
        
        Usage:
            with telemetry.inference_span(prompt_tokens=100) as span:
                # Prompt processing
                span.mark_prompt_done()
                # Generation
                output = generate(...)
                span.set_output(output_tokens=50, token_probs=[...], text="...")
        """
        span = InferenceSpan(self, prompt_tokens, batch_size)
        try:
            yield span
        except Exception as e:
            span.set_error(ErrorType.from_exception(e))
            raise
        finally:
            span.finish()


class InferenceSpan:
    """Helper class for timing inference operations"""
    
    def __init__(self, telemetry: LLMTelemetry, prompt_tokens: int, batch_size: int):
        self.telemetry = telemetry
        self.prompt_tokens = prompt_tokens
        self.batch_size = batch_size
        self.start_time = time.perf_counter()
        self.prompt_end_time: Optional[float] = None
        self.output_tokens: int = 0
        self.token_probs: Optional[List[float]] = None
        self.output_text: Optional[str] = None
        self.error: Optional[ErrorType] = None
        self.context_length: Optional[int] = None
    
    def mark_prompt_done(self):
        """Mark the end of prompt processing"""
        self.prompt_end_time = time.perf_counter()
    
    def set_output(
        self,
        output_tokens: int,
        token_probs: Optional[List[float]] = None,
        text: Optional[str] = None,
        context_length: Optional[int] = None,
    ):
        """Set output information"""
        self.output_tokens = output_tokens
        self.token_probs = token_probs
        self.output_text = text
        self.context_length = context_length
    
    def set_error(self, error: ErrorType):
        """Set error information"""
        self.error = error
    
    def finish(self):
        """Finish the span and record metrics"""
        end_time = time.perf_counter()
        
        if self.prompt_end_time:
            prompt_latency = self.prompt_end_time - self.start_time
            inference_latency = end_time - self.prompt_end_time
        else:
            # No prompt marker, split time proportionally
            total_time = end_time - self.start_time
            prompt_latency = total_time * 0.1  # Estimate
            inference_latency = total_time * 0.9
        
        self.telemetry.record_inference(
            prompt_tokens=self.prompt_tokens,
            output_tokens=self.output_tokens,
            prompt_latency_s=prompt_latency,
            inference_latency_s=inference_latency,
            token_probs=self.token_probs,
            output_text=self.output_text,
            error=self.error,
            context_length=self.context_length,
            batch_size=self.batch_size,
        )
