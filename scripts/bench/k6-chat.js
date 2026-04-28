/**
 * k6-chat.js — k6 load test for Hydra LLM chat endpoint
 *
 * Targets the LiteLLM gateway (OpenAI-compatible /chat/completions).
 * Measures TTFT (time to first chunk), tokens/sec, and request duration
 * via server-sent events (streaming).
 *
 * Usage:
 *   k6 run scripts/bench/k6-chat.js
 *
 * Environment variables:
 *   TARGET_URL   Base URL of the LiteLLM gateway (default: http://localhost:4000/v1)
 *   MODEL        Model name/tag to use (default: llama3:8b)
 *   API_KEY      Bearer token if required (default: empty / "hydra-bench")
 *   MAX_TOKENS   Max tokens to generate (default: 256)
 *   STREAM       Enable streaming for TTFT measurement (default: true)
 *
 * Example:
 *   k6 run -e TARGET_URL=http://10.0.0.5:4000/v1 \
 *          -e MODEL=qwen2.5:14b \
 *          -e MAX_TOKENS=128 \
 *          scripts/bench/k6-chat.js
 *
 * Ramp schedule (total ~30 min):
 *   Stage 1 :  1 VU  →  5 VUs  over 5 min
 *   Stage 2 :  5 VUs hold      for  5 min
 *   Stage 3 :  5 VUs → 10 VUs  over 5 min
 *   Stage 4 : 10 VUs hold      for  5 min
 *   Stage 5 : 10 VUs → 20 VUs  over 5 min
 *   Stage 6 : 20 VUs hold      for  5 min
 *   Stage 7 : 20 VUs → 50 VUs  over 5 min
 *   Stage 8 : 50 VUs hold      for  5 min
 *
 * Thresholds:
 *   http_req_duration p(95) < 5000 ms
 *   error rate < 5 %
 *   TTFT p(95) < 3000 ms (custom metric)
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------
const ttftTrend = new Trend("llm_ttft_ms", true);          // time to first token (ms)
const tokensPerSecTrend = new Trend("llm_tokens_per_sec"); // generation throughput
const errorRate = new Rate("llm_error_rate");
const totalTokensGen = new Counter("llm_tokens_generated");

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const TARGET_URL = __ENV.TARGET_URL || "http://localhost:4000/v1";
const MODEL = __ENV.MODEL || "llama3:8b";
const API_KEY = __ENV.API_KEY || "hydra-bench";
const MAX_TOKENS = parseInt(__ENV.MAX_TOKENS || "256");
const USE_STREAMING = (__ENV.STREAM || "true").toLowerCase() !== "false";

// 512-token synthetic user message (~512 words at ~1 tok/word)
// Using a realistic chat scenario so KV cache behaviour mirrors production.
const SYSTEM_PROMPT =
  "You are a helpful assistant. Answer concisely and accurately.";

const USER_MESSAGE =
  "I need your help understanding a few technical topics in depth. " +
  "First, explain how transformer-based large language models work at a high level, " +
  "covering the attention mechanism, feed-forward layers, and how token generation " +
  "happens step by step. Second, describe what quantization does to a neural network " +
  "model — specifically the difference between Q4_K_M and Q8_0 formats used in the " +
  "GGUF file format for llama.cpp. Third, briefly explain what a KV cache is and why " +
  "it matters for inference throughput and memory usage. Fourth, compare the trade-offs " +
  "of running a 7B parameter model versus a 14B parameter model on a machine with 16 GB " +
  "of unified RAM. Finally, give me a short summary of the token-generation bottleneck " +
  "on Apple Silicon M-series chips versus discrete NVIDIA GPUs. Please be thorough but " +
  "stay focused — I will ask follow-up questions after your response. Let me know if any " +
  "part of the question is unclear before you answer. I want to make sure we are aligned " +
  "on the scope of each sub-question so that your response is maximally useful to me. " +
  "Thank you in advance for the detailed explanation. I appreciate your time and effort.";

// ---------------------------------------------------------------------------
// k6 options — ramp schedule + thresholds
// ---------------------------------------------------------------------------
export const options = {
  stages: [
    { duration: "5m", target: 5 },   // ramp 1 → 5 VUs
    { duration: "5m", target: 5 },   // hold 5 VUs
    { duration: "5m", target: 10 },  // ramp 5 → 10 VUs
    { duration: "5m", target: 10 },  // hold 10 VUs
    { duration: "5m", target: 20 },  // ramp 10 → 20 VUs
    { duration: "5m", target: 20 },  // hold 20 VUs
    { duration: "5m", target: 50 },  // ramp 20 → 50 VUs
    { duration: "5m", target: 50 },  // hold 50 VUs
  ],
  thresholds: {
    http_req_duration:  ["p(95)<5000"],  // 5 s p95 end-to-end
    llm_error_rate:     ["rate<0.05"],   // < 5% error rate
    llm_ttft_ms:        ["p(95)<3000"],  // 3 s p95 TTFT
  },
};

// ---------------------------------------------------------------------------
// Request helpers
// ---------------------------------------------------------------------------
function buildPayload(stream) {
  return JSON.stringify({
    model: MODEL,
    stream: stream,
    max_tokens: MAX_TOKENS,
    temperature: 0.7,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user",   content: USER_MESSAGE },
    ],
  });
}

function makeHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${API_KEY}`,
  };
}

// ---------------------------------------------------------------------------
// Streaming request — measures TTFT by reading the first SSE chunk
// ---------------------------------------------------------------------------
function runStreamingRequest() {
  const url = `${TARGET_URL}/chat/completions`;
  const payload = buildPayload(true);
  const headers = makeHeaders();

  const startMs = Date.now();

  const res = http.post(url, payload, {
    headers: headers,
    timeout: "120s",
    // responseType must be "text" to read the raw SSE body
    responseType: "text",
  });

  const reqDurationMs = Date.now() - startMs;

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "has body":   (r) => r.body && r.body.length > 0,
  });

  if (!ok || res.status !== 200) {
    errorRate.add(1);
    return;
  }
  errorRate.add(0);

  // Parse SSE body to find TTFT and count tokens
  const body = res.body;
  let ttftMs = null;
  let tokenCount = 0;
  let firstChunkFound = false;
  let completionTokens = 0;

  // Walk SSE lines
  const lines = body.split("\n");
  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const data = line.slice(6).trim();
    if (data === "[DONE]") break;

    try {
      const chunk = JSON.parse(data);

      // TTFT: first chunk with actual content
      if (!firstChunkFound) {
        const delta = chunk.choices && chunk.choices[0] && chunk.choices[0].delta;
        if (delta && delta.content && delta.content.length > 0) {
          // Approximate TTFT: we measure total req duration as proxy since k6
          // doesn't expose streaming-byte-level timing. For TTFT we use the
          // time from send to receiving the first non-empty content chunk.
          // Because k6 buffers the full response before returning, ttftMs here
          // represents an upper-bound estimate. Use a real streaming client
          // (curl --no-buffer or wrk2) for precise TTFT measurement.
          ttftMs = reqDurationMs; // conservative upper bound
          firstChunkFound = true;
        }
      }

      // Count generated tokens via usage field (final chunk)
      if (chunk.usage && chunk.usage.completion_tokens) {
        completionTokens = chunk.usage.completion_tokens;
      }

      // Alternatively count delta tokens by character proxy
      if (chunk.choices && chunk.choices[0] && chunk.choices[0].delta && chunk.choices[0].delta.content) {
        tokenCount++;
      }
    } catch (_) {
      // Ignore malformed SSE chunks
    }
  }

  if (ttftMs !== null) {
    ttftTrend.add(ttftMs);
  }

  const effectiveTokens = completionTokens > 0 ? completionTokens : tokenCount;
  if (effectiveTokens > 0 && reqDurationMs > 0) {
    const tps = (effectiveTokens / reqDurationMs) * 1000; // tok/s
    tokensPerSecTrend.add(tps);
    totalTokensGen.add(effectiveTokens);
  }
}

// ---------------------------------------------------------------------------
// Non-streaming request fallback
// ---------------------------------------------------------------------------
function runNonStreamingRequest() {
  const url = `${TARGET_URL}/chat/completions`;
  const payload = buildPayload(false);
  const headers = makeHeaders();

  const startMs = Date.now();

  const res = http.post(url, payload, {
    headers: headers,
    timeout: "120s",
  });

  const reqDurationMs = Date.now() - startMs;

  const ok = check(res, {
    "status 200":    (r) => r.status === 200,
    "has choices":   (r) => {
      try {
        return JSON.parse(r.body).choices.length > 0;
      } catch (_) {
        return false;
      }
    },
  });

  if (!ok || res.status !== 200) {
    errorRate.add(1);
    return;
  }
  errorRate.add(0);

  try {
    const body = JSON.parse(res.body);
    const completionTokens =
      (body.usage && body.usage.completion_tokens) || 0;

    // For non-streaming, TTFT ~ e2e duration (no first-chunk signal)
    ttftTrend.add(reqDurationMs);

    if (completionTokens > 0 && reqDurationMs > 0) {
      const tps = (completionTokens / reqDurationMs) * 1000;
      tokensPerSecTrend.add(tps);
      totalTokensGen.add(completionTokens);
    }
  } catch (_) {
    errorRate.add(1);
  }
}

// ---------------------------------------------------------------------------
// Default function — called once per VU iteration
// ---------------------------------------------------------------------------
export default function () {
  if (USE_STREAMING) {
    runStreamingRequest();
  } else {
    runNonStreamingRequest();
  }

  // Small think-time between requests to avoid hammering without pause
  sleep(Math.random() * 2 + 1); // 1–3 s jitter
}

// ---------------------------------------------------------------------------
// Summary output
// ---------------------------------------------------------------------------
export function handleSummary(data) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const filename = `results/k6-chat_${MODEL.replace(/[:/]/g, "-")}_${ts}.json`;

  return {
    stdout: textSummary(data),
    [filename]: JSON.stringify(data, null, 2),
  };
}

// Inline minimal text summary (avoids needing k6/x/textSummary extension)
function textSummary(data) {
  const m = data.metrics;
  const lines = [
    "",
    "=== Hydra k6 Chat Load Test Summary ===",
    `  Model       : ${MODEL}`,
    `  Target URL  : ${TARGET_URL}`,
    `  Streaming   : ${USE_STREAMING}`,
    "",
    "  http_req_duration",
    `    p50  : ${fmt(m.http_req_duration, "p(50)")} ms`,
    `    p95  : ${fmt(m.http_req_duration, "p(95)")} ms`,
    `    p99  : ${fmt(m.http_req_duration, "p(99)")} ms`,
    "",
    "  TTFT (llm_ttft_ms)",
    `    p50  : ${fmt(m.llm_ttft_ms, "p(50)")} ms`,
    `    p95  : ${fmt(m.llm_ttft_ms, "p(95)")} ms`,
    "",
    "  Tokens/sec (llm_tokens_per_sec)",
    `    avg  : ${fmt(m.llm_tokens_per_sec, "avg")} tok/s`,
    `    p95  : ${fmt(m.llm_tokens_per_sec, "p(95)")} tok/s`,
    "",
    `  Error rate  : ${fmtRate(m.llm_error_rate)} %`,
    `  Tokens gen  : ${fmtCount(m.llm_tokens_generated)}`,
    "=========================================",
    "",
  ];
  return lines.join("\n");
}

function fmt(metric, key) {
  if (!metric || !metric.values) return "N/A";
  const v = metric.values[key];
  return v !== undefined ? v.toFixed(1) : "N/A";
}

function fmtRate(metric) {
  if (!metric || !metric.values) return "N/A";
  return ((metric.values.rate || 0) * 100).toFixed(2);
}

function fmtCount(metric) {
  if (!metric || !metric.values) return "N/A";
  return metric.values.count !== undefined ? metric.values.count : "N/A";
}
