/**
 * k6-autocomplete.js — k6 load test for Hydra LLM autocomplete endpoint
 *
 * Targets the LiteLLM gateway (OpenAI-compatible /chat/completions).
 * Short prompt (64 tokens), short output (max 64 tokens), no streaming.
 * Stricter latency thresholds: p95 < 1500 ms.
 *
 * Usage:
 *   k6 run scripts/bench/k6-autocomplete.js
 *
 * Environment variables:
 *   TARGET_URL   Base URL of the LiteLLM gateway (default: http://localhost:4000/v1)
 *   MODEL        Model name/tag to use (default: llama3:8b)
 *   API_KEY      Bearer token if required (default: "hydra-bench")
 *   MAX_TOKENS   Max tokens to generate (default: 64)
 *
 * Example:
 *   k6 run -e TARGET_URL=http://10.0.0.5:4000/v1 \
 *          -e MODEL=qwen2.5:7b \
 *          scripts/bench/k6-autocomplete.js
 *
 * Ramp schedule (total ~30 min):
 *   Stage 1 :  1 VU  →  10 VUs  over 5 min
 *   Stage 2 : 10 VUs hold       for  5 min
 *   Stage 3 : 10 VUs →  20 VUs  over 5 min
 *   Stage 4 : 20 VUs hold       for  5 min
 *   Stage 5 : 20 VUs →  50 VUs  over 5 min
 *   Stage 6 : 50 VUs hold       for  5 min
 *   Stage 7 : 50 VUs → 100 VUs  over 5 min
 *   Stage 8 :100 VUs hold       for  5 min
 *
 * Thresholds (FastPool SLA — benchmarkings.md Section 8):
 *   http_req_duration p(95) < 1500 ms
 *   http_req_duration p(50) <  500 ms  (TTFT p50 proxy)
 *   error rate < 5 %
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------
const ttftTrend = new Trend("llm_ttft_ms", true);
const tokensPerSecTrend = new Trend("llm_tokens_per_sec");
const errorRate = new Rate("llm_error_rate");
const totalTokensGen = new Counter("llm_tokens_generated");

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const TARGET_URL = __ENV.TARGET_URL || "http://localhost:4000/v1";
const MODEL = __ENV.MODEL || "llama3:8b";
const API_KEY = __ENV.API_KEY || "hydra-bench";
const MAX_TOKENS = parseInt(__ENV.MAX_TOKENS || "64");

// 64-token autocomplete prompt — realistic inline code/text completion scenario
const AUTOCOMPLETE_PROMPTS = [
  "def calculate_fibonacci(n):\n    \"\"\"Return the nth Fibonacci number.\"\"\"\n    if n <= 1:\n        return n\n    return calculate_fibonacci(n - 1) +",
  "SELECT users.id, users.name, orders.total FROM users INNER JOIN orders ON users.id = orders.user_id WHERE orders.created_at >",
  "The primary benefit of using a transformer architecture for language modeling is that it allows the model to",
  "async function fetchUserProfile(userId: string): Promise<UserProfile> {\n  const response = await fetch(`/api/users/${userId}`);\n  if (!response.ok) {",
  "In a distributed system, the CAP theorem states that you can only guarantee two of the three properties: Consistency, Availability, and",
  "class BinarySearchTree:\n    def __init__(self):\n        self.root = None\n\n    def insert(self, value):\n        if self.root is None:\n            self.root =",
  "The gradient descent optimization algorithm works by computing the partial derivatives of the loss function with respect to each weight, then",
  "FROM python:3.11-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\n",
];

// ---------------------------------------------------------------------------
// k6 options — ramp schedule + thresholds (FastPool SLA)
// ---------------------------------------------------------------------------
export const options = {
  stages: [
    { duration: "5m", target: 10  },  // ramp 1 → 10 VUs
    { duration: "5m", target: 10  },  // hold 10 VUs
    { duration: "5m", target: 20  },  // ramp 10 → 20 VUs
    { duration: "5m", target: 20  },  // hold 20 VUs
    { duration: "5m", target: 50  },  // ramp 20 → 50 VUs
    { duration: "5m", target: 50  },  // hold 50 VUs
    { duration: "5m", target: 100 },  // ramp 50 → 100 VUs
    { duration: "5m", target: 100 },  // hold 100 VUs
  ],
  thresholds: {
    // FastPool SLA: benchmarkings.md Section 9 / Appendix A
    http_req_duration: [
      "p(95)<1500",  // 1.5 s p95 hard limit
      "p(50)<500",   // 500 ms p50 TTFT proxy target
    ],
    llm_error_rate: ["rate<0.05"],   // < 5 % error rate
    llm_ttft_ms:    ["p(95)<1500"],  // explicit TTFT threshold
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function buildPayload(prompt) {
  return JSON.stringify({
    model: MODEL,
    stream: false,    // autocomplete: no streaming, minimal latency overhead
    max_tokens: MAX_TOKENS,
    temperature: 0.2, // low temperature for deterministic completions
    stop: ["\n\n", "```"],
    messages: [
      {
        role: "system",
        content:
          "You are a code completion assistant. Complete the given code or text naturally. " +
          "Output only the completion, no explanations.",
      },
      { role: "user", content: prompt },
    ],
  });
}

function makeHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${API_KEY}`,
  };
}

function randomPrompt() {
  return AUTOCOMPLETE_PROMPTS[
    Math.floor(Math.random() * AUTOCOMPLETE_PROMPTS.length)
  ];
}

// ---------------------------------------------------------------------------
// Default function — called once per VU iteration
// ---------------------------------------------------------------------------
export default function () {
  const url = `${TARGET_URL}/chat/completions`;
  const prompt = randomPrompt();
  const payload = buildPayload(prompt);
  const headers = makeHeaders();

  const startMs = Date.now();

  const res = http.post(url, payload, {
    headers: headers,
    timeout: "30s",  // autocomplete must respond quickly; fail fast
  });

  const reqDurationMs = Date.now() - startMs;

  // Validate response
  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "has choices": (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.choices) && body.choices.length > 0;
      } catch (_) {
        return false;
      }
    },
    "under 1500ms": (r) => reqDurationMs < 1500,
  });

  if (!ok || res.status !== 200) {
    errorRate.add(1);
    return;
  }
  errorRate.add(0);

  // Record metrics
  try {
    const body = JSON.parse(res.body);
    const completionTokens =
      (body.usage && body.usage.completion_tokens) || 0;

    // For non-streaming, full e2e duration is TTFT upper bound
    ttftTrend.add(reqDurationMs);

    if (completionTokens > 0 && reqDurationMs > 0) {
      const tps = (completionTokens / reqDurationMs) * 1000;
      tokensPerSecTrend.add(tps);
      totalTokensGen.add(completionTokens);
    }
  } catch (_) {
    errorRate.add(1);
  }

  // Very short think-time — autocomplete is bursty/interactive
  sleep(Math.random() * 0.5); // 0–500 ms jitter
}

// ---------------------------------------------------------------------------
// Summary output
// ---------------------------------------------------------------------------
export function handleSummary(data) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const filename = `results/k6-autocomplete_${MODEL.replace(/[:/]/g, "-")}_${ts}.json`;

  return {
    stdout: textSummary(data),
    [filename]: JSON.stringify(data, null, 2),
  };
}

function textSummary(data) {
  const m = data.metrics;
  const lines = [
    "",
    "=== Hydra k6 Autocomplete Load Test Summary ===",
    `  Model       : ${MODEL}`,
    `  Target URL  : ${TARGET_URL}`,
    `  Max tokens  : ${MAX_TOKENS}`,
    "",
    "  http_req_duration",
    `    p50  : ${fmt(m.http_req_duration, "p(50)")} ms   (SLA target: < 500 ms)`,
    `    p95  : ${fmt(m.http_req_duration, "p(95)")} ms   (SLA target: < 1500 ms)`,
    `    p99  : ${fmt(m.http_req_duration, "p(99)")} ms`,
    "",
    "  TTFT proxy (llm_ttft_ms)",
    `    p50  : ${fmt(m.llm_ttft_ms, "p(50)")} ms`,
    `    p95  : ${fmt(m.llm_ttft_ms, "p(95)")} ms`,
    "",
    "  Tokens/sec (llm_tokens_per_sec)",
    `    avg  : ${fmt(m.llm_tokens_per_sec, "avg")} tok/s`,
    `    p95  : ${fmt(m.llm_tokens_per_sec, "p(95)")} tok/s`,
    "",
    `  Error rate  : ${fmtRate(m.llm_error_rate)} %`,
    `  Tokens gen  : ${fmtCount(m.llm_tokens_generated)}`,
    "===============================================",
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
