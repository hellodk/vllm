#!/usr/bin/env python3
"""Dependency-light reverse-proxy + Prometheus exporter for OpenAI-style engines.

Why this exists
---------------
`mlx_lm.server` (and exo's API server) expose an OpenAI-compatible HTTP API but
NO Prometheus metrics. vLLM/vLLM-MLX expose rich `vllm:*` metrics natively; the
pure-MLX and exo paths expose nothing but crash counters. This sidecar sits in
front of the engine, forwards every request transparently (including SSE token
streaming), and derives the per-request observability that the engine omits.

Three opt-in layers:

  perf (always on)
    * time-to-first-token (TTFT)        — first streamed chunk vs request start
    * time-per-output-token (TPOT)      — (e2e - ttft) / (output_tokens - 1)
    * end-to-end request latency
    * prompt / generation token counters (throughput -> tokens/sec)
    * in-flight (running) and queued (waiting) request gauges
    * best-effort KV-cache usage ratio  — active tokens / (max_kv * concurrency)

  quality (--quality)  → lights up the dormant hallucination-detection alerts
    * llm_repetition_score, llm_refusal_total, llm_hedging_score (text only)
    * llm_output_entropy, llm_perplexity, llm_confidence_{mean,std}  (needs logprobs)
    * llm_hallucination_risk (composite). Scores only — raw text is never logged.

  tracing (--otlp-traces-endpoint)  → one OTLP/HTTP span per request to the
    local OTEL agent, so the existing Tempo pipeline finally has a source.

Metric names mirror vLLM semantics under a configurable prefix (default `mlx`)
so the hydra:llm:* normalization rules fold them in alongside vllm:/sglang:.
The quality metric names (llm_*) match apple-silicon-monitoring's hallucination
alert suite exactly.

Stdlib only + prometheus_client (already in the engine venv). No aiohttp/httpx/
opentelemetry — stays air-gap friendly (OTLP is hand-rolled OTLP/HTTP JSON).
"""
import argparse
import http.client
import json
import math
import os
import re
import threading
import time
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter as PromCounter,
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
_ENTROPY_BUCKETS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0)
_PERPLEXITY_BUCKETS = (2, 5, 10, 20, 30, 50, 75, 100, 200, 500)

_COMPLETION_SUFFIXES = ("/v1/completions", "/v1/chat/completions", "completions")

_REFUSAL_PATTERNS = [re.compile(p) for p in (
    r"i (?:don'?t|do not|cannot|can'?t) (?:know|answer|provide|help)",
    r"i'?m (?:not sure|uncertain|unable)",
    r"(?:sorry|apologies),? (?:but )?i (?:can'?t|cannot|don'?t)",
    r"i (?:don'?t|do not) have (?:enough )?(?:information|knowledge|data)",
    r"(?:as an ai|as a language model),? i (?:can'?t|cannot|don'?t)",
    r"(?:that'?s|this is) (?:beyond|outside) my (?:knowledge|capabilities)",
    r"i'?m not (?:able|capable|qualified) to",
)]
_HEDGING = ("maybe", "perhaps", "possibly", "probably", "might", "could",
            "seems", "appears", "likely", "unlikely", "uncertain", "unclear",
            "i think", "i believe", "it's possible", "it seems", "it appears")


# ── Vendored quality math (mirrors apple-silicon-monitoring .../detection.py) ──
def _entropy(probs, eps=1e-10):
    return -sum(p * math.log(p + eps) for p in probs if p > eps) if probs else 0.0


def _perplexity(probs, eps=1e-10):
    if not probs:
        return 1.0
    return math.exp(-sum(math.log(p + eps) for p in probs) / len(probs))


def _confidence(probs):
    if not probs:
        return 0.0, 0.0
    n = len(probs)
    mean = sum(probs) / n
    if n < 2:
        return mean, 0.0
    var = sum((p - mean) ** 2 for p in probs) / (n - 1)
    return mean, math.sqrt(var)


def _repetition(text, n=3):
    words = text.lower().split()
    if len(words) < n:
        return 0.0
    grams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
    counts = Counter(grams)
    repeated = sum(c - 1 for c in counts.values() if c > 1)
    return repeated / len(grams) if grams else 0.0


def _is_refusal(text):
    t = text.lower()
    return any(p.search(t) for p in _REFUSAL_PATTERNS)


def _hedging(text):
    words = max(1, len(text.split()))
    hits = sum(1 for h in _HEDGING if h in text.lower())
    return min(1.0, (hits / (words / 100.0)) / 5.0)


def _risk(entropy, repetition, perplexity, conf_mean, is_refusal):
    return (min(1.0, entropy / 5.0) * 0.25
            + repetition * 0.25
            + min(1.0, (perplexity - 1) / 99.0) * 0.20
            + (1.0 - conf_mean) * 0.20
            + (1.0 if is_refusal else 0.0) * 0.10)


class Metrics:
    """Prometheus collectors for one engine instance (fixed engine+model labels)."""

    def __init__(self, prefix, engine, model, quality=False):
        self.engine = engine
        self.model = model
        self.quality_on = quality
        p = prefix
        self.ttft = Histogram(f"{p}:time_to_first_token_seconds",
                              "Time to first token (proxy-measured)",
                              ["engine", "model"], buckets=_LAT_BUCKETS)
        self.tpot = Histogram(f"{p}:time_per_output_token_seconds",
                              "Time per output token (proxy-measured)",
                              ["engine", "model"], buckets=_TPOT_BUCKETS)
        self.e2e = Histogram(f"{p}:e2e_request_latency_seconds",
                             "End-to-end request latency (proxy-measured)",
                             ["engine", "model"], buckets=_LAT_BUCKETS)
        self.gen_tokens = PromCounter(f"{p}:generation_tokens_total",
                                      "Generated output tokens", ["engine", "model"])
        self.prompt_tokens = PromCounter(f"{p}:prompt_tokens_total",
                                         "Prompt (input) tokens", ["engine", "model"])
        self.requests = PromCounter(f"{p}:request_success_total",
                                    "Completed requests by finished_reason",
                                    ["engine", "model", "finished_reason"])
        self.errors = PromCounter(f"{p}:errors_total",
                                  "Failed requests by error_type "
                                  "(timeout/connection/rate_limit/parse_error/...)",
                                  ["engine", "model", "error_type"])
        self.running = Gauge(f"{p}:num_requests_running",
                             "Requests currently being served", ["engine", "model"])
        self.waiting = Gauge(f"{p}:num_requests_waiting",
                             "Requests queued behind the concurrency limit",
                             ["engine", "model"])
        self.kv = Gauge(f"{p}:gpu_cache_usage_perc",
                        "Best-effort KV-cache fill ratio 0-1 (estimated for pure-MLX)",
                        ["engine", "model"])
        self._lbl = {"engine": engine, "model": model}
        self.running.labels(**self._lbl).set(0)
        self.waiting.labels(**self._lbl).set(0)
        self.kv.labels(**self._lbl).set(0)

        if quality:
            # Names match the apple-silicon-monitoring hallucination alert suite.
            self.q_entropy = Histogram("llm_output_entropy",
                                       "Output token entropy (nats)",
                                       ["engine", "model"], buckets=_ENTROPY_BUCKETS)
            self.q_perplexity = Histogram("llm_perplexity", "Output perplexity",
                                          ["engine", "model"], buckets=_PERPLEXITY_BUCKETS)
            self.q_repetition = Gauge("llm_repetition_score",
                                      "n-gram repetition score (EWMA)", ["engine", "model"])
            self.q_conf_mean = Gauge("llm_confidence_mean",
                                     "Mean token confidence (EWMA)", ["engine", "model"])
            self.q_conf_std = Gauge("llm_confidence_std",
                                    "Token confidence stddev (EWMA)", ["engine", "model"])
            self.q_hedging = Gauge("llm_hedging_score",
                                   "Hedging-language score (EWMA)", ["engine", "model"])
            self.q_risk = Gauge("llm_hallucination_risk",
                                "Composite hallucination risk 0-1 (EWMA)",
                                ["engine", "model"])
            self.q_refusal = PromCounter("llm_refusal_total",
                                         "Responses detected as refusals",
                                         ["engine", "model"])
            self.q_reqs = PromCounter("llm_quality_requests_total",
                                      "Requests scored for quality", ["engine", "model"])

    def m(self, collector):
        return collector.labels(**self._lbl)

    def record_request(self, finished_reason):
        self.requests.labels(engine=self.engine, model=self.model,
                             finished_reason=finished_reason).inc()

    def record_error(self, error_type):
        self.errors.labels(engine=self.engine, model=self.model,
                           error_type=error_type).inc()


class State:
    """Shared mutable counters for KV/queue estimation + quality EWMA."""

    def __init__(self, max_kv, max_concurrency, ewma_alpha=0.2):
        self.lock = threading.Lock()
        self.active_tokens = 0
        self.running = 0
        self.max_kv = max(1, max_kv)
        self.max_concurrency = max(1, max_concurrency)
        self.alpha = ewma_alpha
        self.ewma = {}

    def ewma_update(self, key, value):
        with self.lock:
            prev = self.ewma.get(key)
            cur = value if prev is None else (self.alpha * value + (1 - self.alpha) * prev)
            self.ewma[key] = cur
            return cur


def _parse_sse_chunk(text, want_text=False):
    """Parse an SSE chunk → (gen_tokens, prompt_tokens, text_delta, token_probs)."""
    gen, prompt = 0, 0
    out, probs = [], []
    for line in text.splitlines():
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
            content = delta.get("content")
            if content:
                gen += 1
                if want_text:
                    out.append(content)
            lp = (choice.get("logprobs") or {}).get("content") or []
            for tok in lp:
                if "logprob" in tok:
                    probs.append(math.exp(tok["logprob"]))
        usage = obj.get("usage") or {}
        if usage:
            gen = usage.get("completion_tokens", gen) or gen
            prompt = usage.get("prompt_tokens", prompt) or prompt
    return gen, prompt, "".join(out), probs


def _emit_otlp_span(endpoint, engine, model, start_ns, end_ns, attrs, ok):
    """Best-effort OTLP/HTTP JSON span to the local OTEL agent. Never raises."""
    def _kv(k, v):
        if isinstance(v, bool):
            val = {"boolValue": v}
        elif isinstance(v, int):
            val = {"intValue": str(v)}
        elif isinstance(v, float):
            val = {"doubleValue": v}
        else:
            val = {"stringValue": str(v)}
        return {"key": k, "value": val}

    body = {
        "resourceSpans": [{
            "resource": {"attributes": [_kv("service.name", engine)]},
            "scopeSpans": [{
                "scope": {"name": "llm_perf_proxy"},
                "spans": [{
                    "traceId": os.urandom(16).hex(),
                    "spanId": os.urandom(8).hex(),
                    "name": "llm.completion",
                    "kind": 2,
                    "startTimeUnixNano": str(start_ns),
                    "endTimeUnixNano": str(end_ns),
                    "attributes": [_kv("gen_ai.system", engine),
                                   _kv("gen_ai.request.model", model)]
                                  + [_kv(k, v) for k, v in attrs.items()],
                    "status": {"code": 1 if ok else 2},
                }],
            }],
        }],
    }
    try:
        req = urllib.request.Request(
            endpoint.rstrip("/") + "/v1/traces",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def make_proxy_handler(upstream_host, upstream_port, metrics, state, traces_endpoint):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            return

        def _is_completion(self):
            return self.path.rstrip("/").endswith(_COMPLETION_SUFFIXES)

        def _fail_502(self):
            # Must terminate the response explicitly: with HTTP/1.1 keep-alive a
            # client would otherwise hang waiting for a body that never comes.
            try:
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
            except Exception:
                pass

        def _enter(self, est_prompt):
            with state.lock:
                state.running += 1
                state.active_tokens += est_prompt
                running = state.running
                waiting = max(0, state.running - state.max_concurrency)
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

        def _score_quality(self, text, probs):
            if not metrics.quality_on or not text:
                return
            rep = _repetition(text)
            hedge = _hedging(text)
            refusal = _is_refusal(text)
            metrics.m(metrics.q_reqs).inc()
            if refusal:
                metrics.m(metrics.q_refusal).inc()
            metrics.m(metrics.q_repetition).set(state.ewma_update("rep", rep))
            metrics.m(metrics.q_hedging).set(state.ewma_update("hedge", hedge))
            entropy = conf_mean = 0.0
            perplexity = 1.0
            if probs:
                entropy = _entropy(probs)
                perplexity = _perplexity(probs)
                conf_mean, conf_std = _confidence(probs)
                metrics.m(metrics.q_entropy).observe(entropy)
                metrics.m(metrics.q_perplexity).observe(perplexity)
                metrics.m(metrics.q_conf_mean).set(state.ewma_update("cm", conf_mean))
                metrics.m(metrics.q_conf_std).set(state.ewma_update("cs", conf_std))
            risk = _risk(entropy, rep, perplexity, conf_mean or 1.0, refusal)
            metrics.m(metrics.q_risk).set(state.ewma_update("risk", risk))

        def _proxy(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            track = self._is_completion()
            want_text = track and (metrics.quality_on or bool(traces_endpoint))

            est_prompt = 0
            if track and body:
                try:
                    req = json.loads(body)
                    txt = json.dumps(req.get("messages", req.get("prompt", "")))
                    est_prompt = max(1, len(txt) // 4)  # ~4 chars/token heuristic
                except ValueError:
                    pass

            start = time.monotonic()
            start_ns = time.time_ns()
            ttft = None
            gen_tokens = 0
            prompt_tokens = est_prompt
            finished = "stop"
            error_type = None
            resp_text_parts = []
            token_probs = []
            if track:
                self._enter(est_prompt)

            conn = http.client.HTTPConnection(upstream_host, upstream_port, timeout=600)
            try:
                fwd = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length")}
                conn.request(self.command, self.path, body=body, headers=fwd)
                resp = conn.getresponse()
                if resp.status == 429:
                    error_type = "rate_limit"
                elif resp.status >= 500:
                    error_type = "upstream_5xx"
                elif resp.status >= 400:
                    error_type = "client_4xx"
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
                        g, p, txt, probs = _parse_sse_chunk(
                            buf.decode("utf-8", "ignore"), want_text)
                        gen_tokens += g
                        if p:
                            prompt_tokens = p
                        if want_text:
                            resp_text_parts.append(txt)
                            token_probs.extend(probs)
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
                            if want_text:
                                msg = choice.get("message") or {}
                                resp_text_parts.append(
                                    msg.get("content") or choice.get("text") or "")
                                lp = (choice.get("logprobs") or {}).get("content") or []
                                token_probs.extend(
                                    math.exp(t["logprob"]) for t in lp if "logprob" in t)
                        except ValueError:
                            error_type = error_type or "parse_error"
            except TimeoutError:
                finished, error_type = "abort", "timeout"
                self._fail_502()
            except (ConnectionError, ConnectionRefusedError) as exc:
                finished = "abort"
                error_type = ("connection_refused"
                              if isinstance(exc, ConnectionRefusedError)
                              else "connection")
                self._fail_502()
            except OSError:
                finished, error_type = "abort", "connection"
                self._fail_502()
            except Exception:
                finished, error_type = "abort", error_type or "proxy_error"
                self._fail_502()
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
                    if error_type:
                        metrics.record_error(error_type)
                    self._exit(prompt_tokens, gen_tokens)
                    if metrics.quality_on:
                        self._score_quality("".join(resp_text_parts), token_probs)
                    if traces_endpoint:
                        threading.Thread(
                            target=_emit_otlp_span,
                            args=(traces_endpoint, metrics.engine, metrics.model,
                                  start_ns, time.time_ns(),
                                  {"llm.ttft_seconds": ttft or 0.0,
                                   "llm.e2e_seconds": e2e,
                                   "llm.output_tokens": gen_tokens,
                                   "llm.prompt_tokens": prompt_tokens,
                                   "llm.finished_reason": finished},
                                  finished != "abort"),
                            daemon=True).start()

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
    ap.add_argument("--quality", action="store_true",
                    help="score responses for hallucination/quality signals")
    ap.add_argument("--otlp-traces-endpoint", default="",
                    help="OTLP/HTTP base URL (e.g. http://127.0.0.1:4318) for per-request spans")
    args = ap.parse_args()

    metrics = Metrics(args.metric_prefix, args.engine_name, args.model, quality=args.quality)
    state = State(args.max_kv, args.max_concurrency)

    _serve_metrics(args.listen_host, args.metrics_port)
    ThreadingHTTPServer(
        (args.listen_host, args.listen_port),
        make_proxy_handler(args.upstream_host, args.upstream_port, metrics, state,
                           args.otlp_traces_endpoint),
    ).serve_forever()


if __name__ == "__main__":
    main()
