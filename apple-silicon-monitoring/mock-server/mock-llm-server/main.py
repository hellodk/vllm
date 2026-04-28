#!/usr/bin/env python3
"""
Mock LLM Inference Server

Simulates an LLM inference server with OpenTelemetry instrumentation.
Generates realistic metrics including hallucination detection signals.
"""

import random
import math
import time
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

# Configuration
HTTP_PORT = int(os.environ.get('HTTP_PORT', '5901'))
OTEL_ENDPOINT = os.environ.get('OTEL_ENDPOINT', 'otel-agent:5911')
MODEL_NAME = os.environ.get('MODEL_NAME', 'llama-3-70b')
NODE_ID = int(os.environ.get('NODE_ID', '1'))

class MockLLMServer:
    def __init__(self, model_name: str, node_id: int):
        self.model_name = model_name
        self.node_id = node_id
        self.instance = f"mac-mini-mock-{node_id:03d}"
        self.start_time = time.time()
        
        # Initialize OpenTelemetry
        self._init_telemetry()
        
        # State
        self.model_loaded = True
        self.request_count = 0
        self.error_count = 0
        self.queue_depth = 0
        
        # Base metrics
        self.base_latency = random.uniform(1.5, 3.0)
        self.base_throughput = random.uniform(30, 60)
        
        # Start background metrics updater
        self.running = True
        self.updater_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.updater_thread.start()
    
    def _init_telemetry(self):
        """Initialize OpenTelemetry metrics"""
        resource = Resource.create({
            SERVICE_NAME: "mock-llm-server",
            "model": self.model_name,
            "instance": self.instance,
        })
        
        try:
            exporter = OTLPMetricExporter(
                endpoint=OTEL_ENDPOINT,
                insecure=True,
            )
            reader = PeriodicExportingMetricReader(
                exporter,
                export_interval_millis=5000,
            )
            provider = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(provider)
            print(f"OTLP exporter configured to send to {OTEL_ENDPOINT}")
        except Exception as e:
            print(f"Warning: Failed to configure OTLP exporter: {e}")
            provider = MeterProvider(resource=resource)
            metrics.set_meter_provider(provider)
        
        self.meter = metrics.get_meter("llm-inference", "1.0.0")
        
        # Create metrics
        self.inference_requests = self.meter.create_counter(
            "llm_inference_requests_total",
            description="Total inference requests",
        )
        
        self.inference_duration = self.meter.create_histogram(
            "llm_inference_duration_seconds",
            description="Inference latency by phase",
        )
        
        self.tokens_processed = self.meter.create_counter(
            "llm_tokens_processed_total",
            description="Total tokens processed",
        )
        
        self.tokens_per_second_gauge = self.meter.create_gauge(
            "llm_tokens_per_second",
            description="Current token throughput",
        )
        
        self.gpu_memory_gauge = self.meter.create_gauge(
            "llm_gpu_memory_allocated_bytes",
            description="GPU memory allocated",
        )
        
        self.kv_cache_gauge = self.meter.create_gauge(
            "llm_kv_cache_utilization",
            description="KV cache utilization",
        )
        
        self.queue_depth_gauge = self.meter.create_gauge(
            "llm_queue_depth",
            description="Pending requests",
        )
        
        self.errors_counter = self.meter.create_counter(
            "llm_error_total",
            description="Total errors",
        )
        
        self.model_loaded_gauge = self.meter.create_gauge(
            "llm_model_loaded",
            description="Model loaded status",
        )
        
        self.health_score_gauge = self.meter.create_gauge(
            "llm_model_health_score",
            description="Model health score",
        )
        
        # Hallucination metrics
        self.output_entropy = self.meter.create_histogram(
            "llm_output_entropy",
            description="Token probability entropy",
        )
        
        self.confidence_mean_gauge = self.meter.create_gauge(
            "llm_confidence_mean",
            description="Mean token confidence",
        )
        
        self.confidence_std_gauge = self.meter.create_gauge(
            "llm_confidence_std",
            description="Confidence std deviation",
        )
        
        self.repetition_score_gauge = self.meter.create_gauge(
            "llm_repetition_score",
            description="N-gram repetition score",
        )
        
        self.perplexity = self.meter.create_histogram(
            "llm_perplexity",
            description="Output perplexity",
        )
        
        self.refusal_rate_gauge = self.meter.create_gauge(
            "llm_refusal_rate",
            description="Refusal response rate",
        )
        
        self.batch_size_hist = self.meter.create_histogram(
            "llm_batch_size",
            description="Batch sizes",
        )
        
        self.context_length_hist = self.meter.create_histogram(
            "llm_context_length",
            description="Context lengths",
        )
    
    def _update_loop(self):
        """Background loop to update metrics"""
        while self.running:
            self._simulate_inference()
            time.sleep(random.uniform(0.5, 2.0))
    
    def _simulate_inference(self):
        """Simulate an inference request and update metrics"""
        t = time.time() - self.start_time
        
        # Workload pattern
        workload = 0.5 + 0.3 * math.sin(t / 120) + random.gauss(0, 0.1)
        workload = max(0.2, min(1.0, workload))
        
        labels = {
            "model": self.model_name,
            "instance": self.instance,
            "model_version": "1.0",
            "gpu": "0",
        }
        
        # Simulate error occasionally
        is_error = random.random() < 0.02
        
        if is_error:
            self.error_count += 1
            error_types = ["timeout", "out_of_memory", "invalid_input"]
            error_type = random.choice(error_types)
            self.errors_counter.add(1, {"error_type": error_type, **labels})
            self.inference_requests.add(1, {"status": "error", **labels})
            return
        
        self.request_count += 1
        self.inference_requests.add(1, {"status": "success", **labels})
        
        # Token counts
        prompt_tokens = random.randint(50, 500)
        output_tokens = random.randint(20, 200)
        
        self.tokens_processed.add(prompt_tokens, {"direction": "input", **labels})
        self.tokens_processed.add(output_tokens, {"direction": "output", **labels})
        
        # Latencies
        prompt_latency = self.base_latency * 0.2 * (1 + prompt_tokens / 200) * (1 + random.gauss(0, 0.1))
        gen_latency = self.base_latency * (output_tokens / 50) * (1 + random.gauss(0, 0.15))
        
        self.inference_duration.record(prompt_latency, {"phase": "prompt", **labels})
        self.inference_duration.record(gen_latency, {"phase": "generation", **labels})
        self.inference_duration.record(prompt_latency + gen_latency, {"phase": "total", **labels})
        
        # Throughput
        tps = output_tokens / gen_latency if gen_latency > 0 else 0
        self.tokens_per_second_gauge.set(tps, labels)
        
        # Batch and context
        batch_size = random.choice([1, 1, 1, 2, 4])
        context_length = prompt_tokens + output_tokens
        self.batch_size_hist.record(batch_size, labels)
        self.context_length_hist.record(context_length, labels)
        
        # GPU memory (8-14GB range)
        gpu_mem = int((8 + 6 * workload) * 1024 * 1024 * 1024 * (1 + random.gauss(0, 0.05)))
        self.gpu_memory_gauge.set(gpu_mem, labels)
        
        # KV cache
        kv_util = 0.3 + 0.5 * workload + random.gauss(0, 0.05)
        kv_util = max(0, min(1, kv_util))
        self.kv_cache_gauge.set(kv_util, labels)
        
        # Queue depth
        self.queue_depth = max(0, int(workload * 20 + random.gauss(0, 5)))
        self.queue_depth_gauge.set(self.queue_depth, labels)
        
        # Model loaded
        self.model_loaded_gauge.set(1 if self.model_loaded else 0, labels)
        
        # Hallucination signals
        # Entropy: normally 1-3, occasionally spike
        base_entropy = 1.5 + random.gauss(0, 0.5)
        if random.random() < 0.05:  # 5% chance of high entropy
            base_entropy = random.uniform(3.5, 5.0)
        entropy = max(0, base_entropy)
        self.output_entropy.record(entropy, labels)
        
        # Confidence: normally 0.7-0.9
        confidence_mean = 0.8 + random.gauss(0, 0.1)
        if entropy > 3.5:  # Low confidence when high entropy
            confidence_mean = 0.4 + random.gauss(0, 0.1)
        confidence_mean = max(0, min(1, confidence_mean))
        confidence_std = random.uniform(0.05, 0.15)
        self.confidence_mean_gauge.set(confidence_mean, labels)
        self.confidence_std_gauge.set(confidence_std, labels)
        
        # Repetition: normally low, occasionally spike
        repetition = random.uniform(0.01, 0.1)
        if random.random() < 0.03:  # 3% chance of high repetition
            repetition = random.uniform(0.25, 0.45)
        self.repetition_score_gauge.set(repetition, labels)
        
        # Perplexity: normally 5-20
        perplexity = 10 + random.gauss(0, 5)
        if entropy > 3.5:
            perplexity = 40 + random.gauss(0, 15)
        perplexity = max(1, perplexity)
        self.perplexity.record(perplexity, labels)
        
        # Refusal rate
        refusal = 1 if random.random() < 0.05 else 0
        self.refusal_rate_gauge.set(refusal, labels)
        
        # Health score
        health = 100
        error_rate = self.error_count / max(1, self.request_count)
        health -= min(50, error_rate * 500)
        health -= min(25, (entropy / 4) * 25)
        health = max(0, min(100, health))
        self.health_score_gauge.set(health, labels)
    
    def stop(self):
        self.running = False


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')
        elif self.path == '/ready':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "ready"}')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass


def main():
    print(f"Starting Mock LLM Server")
    print(f"  Model: {MODEL_NAME}")
    print(f"  Node ID: {NODE_ID}")
    print(f"  OTLP Endpoint: {OTEL_ENDPOINT}")
    print(f"  HTTP Port: {HTTP_PORT}")
    
    # Start mock server
    server = MockLLMServer(MODEL_NAME, NODE_ID)
    
    # Start HTTP health server
    http_server = HTTPServer(('0.0.0.0', HTTP_PORT), HealthHandler)
    print(f"Health endpoint available at http://localhost:{HTTP_PORT}/health")
    
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()
        http_server.shutdown()


if __name__ == '__main__':
    main()
