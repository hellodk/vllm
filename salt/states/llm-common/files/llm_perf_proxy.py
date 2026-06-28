#!/usr/bin/env python3
"""Dependency-light reverse-proxy + Prometheus exporter for OpenAI-style engines.

Why this exists
---------------
`mlx_lm.server` (and exo's API server) expose an OpenAI-compatible HTTP API but
NO Prometheus metrics. vLLM/vLLM-MLX expose rich `vllm:*` metrics natively; the
pure-MLX and exo paths expose nothing but crash counters. This sidecar sits in
front of the engine, forwards every request transparently (including SSE token
streaming), and derives the per-request observability that the engine omits:

  * time-to-first-token (TTFT)        — first streamed chunk vs request start
  * time-per-output-token (TPOT)      — (e2e - ttft) / (output_tokens - 1)
  * end-to-end request latency
  * prompt / generation token counters (throughput -> tokens/sec)
  * in-flight (running) and queued (waiting) request gauges
  * best-effort KV-cache usage ratio  — active tokens / (max_kv * concurrency)

Metric names mirror vLLM semantics under a configurable prefix (default `mlx`)
so the hydra:llm:* normalization rules fold them in alongside vllm:/sglang:.

Stdlib only + prometheus_client (already in the engine venv). No aiohttp/httpx,
so it stays air-gap friendly.
"""
import argparse
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# TTFT/e2e buckets tuned for interactive LLM serving (10ms .. 60s).
_LAT_BUCKETS = (
    0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0,
    2.0, 5.0, 10.0, 20.0, 30.0, 60.0,
)
_TPOT_BUCKETS = (0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.15, 0.25, 0.5, 1.0)

_COMPLETION_SUFFIXES = ("/v1/completions", "/v1/chat/completions", "completions")


class Metrics:
    """Prometheus collectors for one engine instance (fixed engine+model labels)."""

    def __init__(self, prefix, engine, model):
        self.engine = engine
        self.model = model
        p = prefix
        self.ttft = Histogram(
            f"{p}:time_to_first_token_seconds",
            "Time to first token (proxy-measured)",
            ["engine", "model"], buckets=_LAT_BUCKETS,
        )
        self.tpot = Histogram(
            f"{p}:time_per_output_token_seconds",
            "Time per output token (proxy-measured)",
            ["engine", "model"], buckets=_TPOT_BUCKETS,
        )
        self.e2e = Histogram(
            f"{p}:e2e_request_latency_seconds",
            "End-to-end request latency (proxy-measured)",
            ["engine", "model"], buckets=_LAT_BUCKETS,
        )
        self.gen_tokens = Counter(
            f"{p}:generation_tokens_total",
            "Generated output tokens", ["engine", "model"],
        )
        self.prompt_tokens = Counter(
            f"{p}:prompt_tokens_total",
            "Prompt (input) tokens", ["engine", "model"],
        )
        self.requests = Counter(
            f"{p}:request_success_total",
            "Completed requests by finished_reason",
            ["engine", "model", "finished_reason"],
        )
        self.running = Gauge(
            f"{p}:num_requests_running",
            "Requests currently being served", ["engine", "model"],
        )
        self.waiting = Gauge(
            f"{p}:num_requests_waiting",
            "Requests queued behind the concurrency limit", ["engine", "model"],
        )
        self.kv = Gauge(
            f"{p}:gpu_cache_usage_perc",
            "Best-effort KV-cache fill ratio 0-1 (estimated for pure-MLX)",
            ["engine", "model"],
        )
        # Pre-create the fixed-label children so series exist before first request.
        self._lbl = {"engine": engine, "model": model}
        self.running.labels(**self._lbl).set(0)
        self.waiting.labels(**self._lbl).set(0)
        self.kv.labels(**self._lbl).set(0)

    def m(self, collector):
        return collector.labels(**self._lbl)

    def record_request(self, finished_reason):
        self.requests.labels(
            engine=self.engine, model=self.model, finished_reason=finished_reason
        ).inc()


class State:
    """Shared mutable counters for KV/queue estimation, guarded by a lock."""

    def __init__(self, max_kv, max_concurrency):
        self.lock = threading.Lock()
        self.active_tokens = 0
        self.running = 0
        self.max_kv = max(1, max_kv)
        self.max_concurrency = max(1, max_concurrency)


def _count_stream_tokens(chunk_text):
    """Count generation/prompt tokens in an SSE chunk (1 content delta ~= 1 token)."""
    gen = 0
    prompt = 0
    for line in chunk_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload in ("", "[DONE]"):
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        for choice in obj.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                gen += 1
        usage = obj.get("usage") or {}
        if usage:
            gen = usage.get("completion_tokens", gen) or gen
            prompt = usage.get("prompt_tokens", prompt) or prompt
    return gen, prompt


def make_proxy_handler(upstream_host, upstream_port, metrics, state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            return

        def _is_completion(self):
            return self.path.rstrip("/").endswith(_COMPLETION_SUFFIXES)

        def _enter(self, est_prompt):
            with state.lock:
                state.running += 1
                state.active_tokens += est_prompt
                running, waiting = state.running, max(0, state.running - state.max_concurrency)
            metrics.m(metrics.running).set(running)
            metrics.m(metrics.waiting).set(waiting)

        def _exit(self, prompt_tokens, gen_tokens):
            with state.lock:
                state.running = max(0, state.running - 1)
                state.active_tokens = max(0, state.active_tokens - prompt_tokens - gen_tokens)
                running = state.running
                waiting = max(0, state.running - state.max_concurrency)
                kv = min(1.0, state.active_tokens / float(state.max_kv * state.max_concurrency))
            metrics.m(metrics.running).set(running)
            metrics.m(metrics.waiting).set(waiting)
            metrics.m(metrics.kv).set(kv)

        def _proxy(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            track = self._is_completion()

            est_prompt = 0
            if track and body:
                try:
                    req = json.loads(body)
                    txt = json.dumps(req.get("messages", req.get("prompt", "")))
                    est_prompt = max(1, len(txt) // 4)  # ~4 chars/token heuristic
                except ValueError:
                    pass

            start = time.monotonic()
            ttft = None
            gen_tokens = 0
            prompt_tokens = est_prompt
            finished = "stop"
            if track:
                self._enter(est_prompt)

            conn = http.client.HTTPConnection(upstream_host, upstream_port, timeout=600)
            try:
                fwd = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length")}
                conn.request(self.command, self.path, body=body, headers=fwd)
                resp = conn.getresponse()
                self.send_response(resp.status)
                ctype = resp.getheader("Content-Type", "")
                streaming = "text/event-stream" in ctype
                for k, v in resp.getheaders():
                    if k.lower() in ("transfer-encoding", "content-length", "connection"):
                        continue
                    self.send_header(k, v)

                if streaming:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        buf = resp.read(1024)
                        if not buf:
                            break
                        if track and ttft is None:
                            ttft = time.monotonic() - start
                            metrics.m(metrics.ttft).observe(ttft)
                        g, p = _count_stream_tokens(buf.decode("utf-8", "ignore"))
                        gen_tokens += g
                        if p:
                            prompt_tokens = p
                        self.wfile.write(b"%X\r\n" % len(buf) + buf + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    payload = resp.read()
                    if track and ttft is None:
                        ttft = time.monotonic() - start
                        metrics.m(metrics.ttft).observe(ttft)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    if track:
                        try:
                            obj = json.loads(payload)
                            usage = obj.get("usage") or {}
                            gen_tokens = usage.get("completion_tokens", 0) or gen_tokens
                            prompt_tokens = usage.get("prompt_tokens", prompt_tokens) \
                                or prompt_tokens
                            choice = (obj.get("choices") or [{}])[0]
                            finished = choice.get("finish_reason") or finished
                        except ValueError:
                            pass
            except Exception:
                finished = "abort"
                try:
                    self.send_response(502)
                    self.end_headers()
                except Exception:
                    pass
            finally:
                conn.close()
                if track:
                    e2e = time.monotonic() - start
                    metrics.m(metrics.e2e).observe(e2e)
                    if gen_tokens > 0:
                        metrics.m(metrics.gen_tokens).inc(gen_tokens)
                        if ttft is not None and gen_tokens > 1:
                            metrics.m(metrics.tpot).observe(
                                max(e2e - ttft, 0.0) / (gen_tokens - 1))
                    if prompt_tokens > 0:
                        metrics.m(metrics.prompt_tokens).inc(prompt_tokens)
                    metrics.record_request(finished)
                    self._exit(prompt_tokens, gen_tokens)

        do_GET = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_DELETE = _proxy

    return Handler


def _serve_metrics(host, port):
    class MetricsHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            return

        def do_GET(self):
            if self.path.rstrip("/") not in ("/metrics", ""):
                self.send_response(404)
                self.end_headers()
                return
            out = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer((host, port), MetricsHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen-host", default="0.0.0.0")
    ap.add_argument("--listen-port", type=int, required=True,
                    help="public port clients connect to")
    ap.add_argument("--upstream-host", default="127.0.0.1")
    ap.add_argument("--upstream-port", type=int, required=True,
                    help="loopback port the real engine listens on")
    ap.add_argument("--metrics-port", type=int, required=True)
    ap.add_argument("--engine-name", default="mlx_lm")
    ap.add_argument("--metric-prefix", default="mlx")
    ap.add_argument("--model", default="unknown")
    ap.add_argument("--max-kv", type=int, default=4096)
    ap.add_argument("--max-concurrency", type=int, default=8)
    args = ap.parse_args()

    metrics = Metrics(args.metric_prefix, args.engine_name, args.model)
    state = State(args.max_kv, args.max_concurrency)

    _serve_metrics(args.listen_host, args.metrics_port)
    ThreadingHTTPServer(
        (args.listen_host, args.listen_port),
        make_proxy_handler(args.upstream_host, args.upstream_port, metrics, state),
    ).serve_forever()


if __name__ == "__main__":
    main()
