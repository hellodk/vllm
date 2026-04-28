"""
Metric types and data structures for LLM telemetry.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List
from datetime import datetime


class ErrorType(Enum):
    """Classification of LLM inference errors"""
    
    # Request errors
    INVALID_INPUT = "invalid_input"
    CONTEXT_TOO_LONG = "context_too_long"
    EMPTY_PROMPT = "empty_prompt"
    
    # Resource errors
    OUT_OF_MEMORY = "out_of_memory"
    GPU_MEMORY_EXHAUSTED = "gpu_memory_exhausted"
    
    # Timeout errors
    TIMEOUT = "timeout"
    GENERATION_TIMEOUT = "generation_timeout"
    
    # System errors
    KERNEL_FAILURE = "kernel_failure"
    METAL_CRASH = "metal_crash"
    DRIVER_ERROR = "driver_error"
    
    # Model errors
    MODEL_NOT_LOADED = "model_not_loaded"
    MODEL_CORRUPTED = "model_corrupted"
    TOKENIZATION_ERROR = "tokenization_error"
    
    # Network errors (for distributed inference)
    NETWORK_ERROR = "network_error"
    CONNECTION_RESET = "connection_reset"
    
    # Unknown
    UNKNOWN = "unknown"
    
    @classmethod
    def from_exception(cls, exc: Exception) -> "ErrorType":
        """Map an exception to an error type"""
        exc_name = type(exc).__name__.lower()
        exc_msg = str(exc).lower()
        
        if "memory" in exc_msg or "oom" in exc_msg:
            return cls.OUT_OF_MEMORY
        elif "timeout" in exc_name or "timeout" in exc_msg:
            return cls.TIMEOUT
        elif "metal" in exc_msg:
            return cls.METAL_CRASH
        elif "kernel" in exc_msg:
            return cls.KERNEL_FAILURE
        elif "connection" in exc_msg or "network" in exc_msg:
            return cls.NETWORK_ERROR
        elif "context" in exc_msg and "long" in exc_msg:
            return cls.CONTEXT_TOO_LONG
        elif "tokeniz" in exc_msg:
            return cls.TOKENIZATION_ERROR
        else:
            return cls.UNKNOWN


@dataclass
class LLMMetrics:
    """Container for LLM inference metrics"""
    
    # Request info
    request_id: str = ""
    model_name: str = ""
    model_version: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Token counts
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    # Latencies (seconds)
    prompt_latency: float = 0.0
    generation_latency: float = 0.0
    total_latency: float = 0.0
    time_to_first_token: float = 0.0
    
    # Throughput
    tokens_per_second: float = 0.0
    
    # Batch info
    batch_size: int = 1
    context_length: int = 0
    
    # Resource usage
    gpu_memory_used: int = 0
    gpu_utilization: float = 0.0
    kv_cache_utilization: float = 0.0
    
    # Status
    success: bool = True
    error_type: Optional[ErrorType] = None
    error_message: str = ""
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "request_id": self.request_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "timestamp": self.timestamp.isoformat(),
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "prompt_latency": self.prompt_latency,
            "generation_latency": self.generation_latency,
            "total_latency": self.total_latency,
            "time_to_first_token": self.time_to_first_token,
            "tokens_per_second": self.tokens_per_second,
            "batch_size": self.batch_size,
            "context_length": self.context_length,
            "gpu_memory_used": self.gpu_memory_used,
            "gpu_utilization": self.gpu_utilization,
            "kv_cache_utilization": self.kv_cache_utilization,
            "success": self.success,
            "error_type": self.error_type.value if self.error_type else None,
            "error_message": self.error_message,
        }


@dataclass
class HallucinationMetrics:
    """Metrics for hallucination detection"""
    
    # Probability-based metrics
    entropy: float = 0.0
    perplexity: float = 0.0
    confidence_mean: float = 0.0
    confidence_std: float = 0.0
    confidence_min: float = 0.0
    
    # Text-based metrics
    repetition_score: float = 0.0
    hedging_score: float = 0.0
    is_refusal: bool = False
    
    # Composite scores
    hallucination_risk: float = 0.0
    
    # Analysis results
    flagged_tokens: List[int] = field(default_factory=list)  # Indices of low-confidence tokens
    repeated_phrases: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "entropy": self.entropy,
            "perplexity": self.perplexity,
            "confidence_mean": self.confidence_mean,
            "confidence_std": self.confidence_std,
            "confidence_min": self.confidence_min,
            "repetition_score": self.repetition_score,
            "hedging_score": self.hedging_score,
            "is_refusal": self.is_refusal,
            "hallucination_risk": self.hallucination_risk,
            "flagged_tokens_count": len(self.flagged_tokens),
            "repeated_phrases_count": len(self.repeated_phrases),
        }


@dataclass
class ModelHealthScore:
    """Composite model health score"""
    
    # Overall score (0-100)
    score: float = 100.0
    
    # Component scores
    availability_score: float = 100.0  # Model uptime and load success
    latency_score: float = 100.0       # P99 latency health
    error_score: float = 100.0         # Error rate health
    quality_score: float = 100.0       # Output quality (hallucination metrics)
    resource_score: float = 100.0      # GPU/memory utilization health
    
    # Thresholds
    latency_p99_threshold_ms: float = 5000.0
    error_rate_threshold: float = 0.01
    hallucination_risk_threshold: float = 0.3
    gpu_memory_threshold: float = 0.95
    
    # Status
    is_healthy: bool = True
    degraded_components: List[str] = field(default_factory=list)
    
    @classmethod
    def compute(
        cls,
        availability: float,  # 0-1 uptime ratio
        latency_p99_ms: float,
        error_rate: float,
        hallucination_risk: float,
        gpu_memory_utilization: float,
        latency_threshold_ms: float = 5000.0,
        error_threshold: float = 0.01,
        hallucination_threshold: float = 0.3,
        gpu_threshold: float = 0.95,
    ) -> "ModelHealthScore":
        """Compute health score from metrics"""
        
        health = cls(
            latency_p99_threshold_ms=latency_threshold_ms,
            error_rate_threshold=error_threshold,
            hallucination_risk_threshold=hallucination_threshold,
            gpu_memory_threshold=gpu_threshold,
        )
        
        # Availability score (0-100)
        health.availability_score = availability * 100
        if availability < 0.99:
            health.degraded_components.append("availability")
        
        # Latency score
        if latency_p99_ms <= latency_threshold_ms:
            health.latency_score = 100.0
        else:
            # Degrade linearly above threshold
            overage_ratio = (latency_p99_ms - latency_threshold_ms) / latency_threshold_ms
            health.latency_score = max(0, 100 - (overage_ratio * 50))
            health.degraded_components.append("latency")
        
        # Error score
        if error_rate <= error_threshold:
            health.error_score = 100.0
        else:
            # Degrade based on error rate
            health.error_score = max(0, 100 - (error_rate * 1000))
            health.degraded_components.append("errors")
        
        # Quality score (based on hallucination risk)
        if hallucination_risk <= hallucination_threshold:
            health.quality_score = 100 - (hallucination_risk * 100)
        else:
            health.quality_score = max(0, 70 - ((hallucination_risk - hallucination_threshold) * 200))
            health.degraded_components.append("quality")
        
        # Resource score
        if gpu_memory_utilization <= gpu_threshold:
            health.resource_score = 100 - (gpu_memory_utilization * 20)  # Slight penalty for high util
        else:
            health.resource_score = max(0, 80 - ((gpu_memory_utilization - gpu_threshold) * 500))
            health.degraded_components.append("resources")
        
        # Weighted average for overall score
        weights = {
            "availability": 0.25,
            "latency": 0.25,
            "error": 0.20,
            "quality": 0.15,
            "resource": 0.15,
        }
        
        health.score = (
            weights["availability"] * health.availability_score +
            weights["latency"] * health.latency_score +
            weights["error"] * health.error_score +
            weights["quality"] * health.quality_score +
            weights["resource"] * health.resource_score
        )
        
        # Determine overall health
        health.is_healthy = (
            health.score >= 80 and
            len(health.degraded_components) <= 1
        )
        
        return health
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "score": self.score,
            "is_healthy": self.is_healthy,
            "availability_score": self.availability_score,
            "latency_score": self.latency_score,
            "error_score": self.error_score,
            "quality_score": self.quality_score,
            "resource_score": self.resource_score,
            "degraded_components": self.degraded_components,
        }
