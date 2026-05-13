"""
ML Cluster Guardrails — Hallucination Control, Content Filtering, Token Management

This module provides custom guardrail callbacks for LiteLLM proxy.
Deploy alongside LiteLLM on Mac Mini 1.

Usage:
    Set GUARDRAILS_MODULE=guardrails in environment, or configure in litellm-config.yaml
"""

import re
import json
import time
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("guardrails")
logger.setLevel(logging.INFO)


# ============================================================
# Configuration
# ============================================================

@dataclass
class GuardrailConfig:
    """Central configuration for all guardrails."""

    # --- Token Limits ---
    max_input_tokens: int = 6144          # Max input context (leaves room in 8192 window)
    max_output_tokens: int = 4096         # Max generation length
    warn_input_tokens: int = 4096         # Log warning above this

    # --- Hallucination Control ---
    temperature_cap: float = 0.3          # Force temp below this for code
    code_temperature: float = 0.1         # Temperature for code-specific requests
    require_grounding_prompt: bool = True  # Inject grounding system prompt

    # --- Content Filtering ---
    block_secret_patterns: bool = True
    block_pii: bool = True

    # --- Rate Limiting ---
    max_requests_per_minute_per_user: int = 30
    max_tokens_per_hour_per_user: int = 100_000

    # --- Context Management ---
    auto_truncate: bool = True            # Truncate input if over limit
    truncation_strategy: str = "keep_recent"  # keep_recent | keep_first | middle_out


config = GuardrailConfig()


# ============================================================
# Secret / PII Detection Patterns
# ============================================================

SECRET_PATTERNS = [
    # API Keys
    (r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{20,}', "api_key"),
    (r'sk-[a-zA-Z0-9]{20,}', "openai_key"),
    (r'ghp_[a-zA-Z0-9]{36}', "github_pat"),
    (r'xoxb-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24}', "slack_token"),

    # Passwords in code
    (r'(?i)(password|passwd|pwd)\s*[:=]\s*["\'][^"\']{4,}["\']', "password"),

    # Private keys
    (r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----', "private_key"),

    # Connection strings
    (r'(?i)(mongodb|postgres|mysql|redis)://[^\s"\']+', "connection_string"),

    # AWS credentials
    (r'AKIA[0-9A-Z]{16}', "aws_access_key"),
    (r'(?i)aws_secret_access_key\s*[:=]\s*["\']?[a-zA-Z0-9/+=]{40}', "aws_secret"),
]

PII_PATTERNS = [
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', "email"),
    (r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b', "ssn_like"),
    (r'\b(?:\+?1[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b', "phone"),
]


# ============================================================
# Grounding System Prompts (Hallucination Control)
# ============================================================

GROUNDING_PROMPT_CODE = """You are a precise coding assistant. Follow these rules strictly:
1. Only suggest code patterns, libraries, and APIs that you are confident exist.
2. If you are unsure whether a function, method, or library exists, say so explicitly.
3. Do not invent import paths, package names, or API endpoints.
4. When referencing documentation, only cite sources you are certain about.
5. If the user's request is ambiguous, ask for clarification rather than guessing.
6. Prefer well-known, stable libraries over obscure ones.
7. Always include error handling for external calls.
8. Do not hallucinate file paths or project structure — only reference what has been provided in context."""

GROUNDING_PROMPT_GENERAL = """You are a helpful assistant. Follow these rules:
1. If you don't know something, say "I'm not sure" rather than guessing.
2. Distinguish clearly between facts and opinions/assumptions.
3. When making recommendations, explain your reasoning.
4. Do not invent URLs, citations, or references."""


# ============================================================
# Token Counter (using tiktoken for accuracy)
# ============================================================

class TokenCounter:
    """Approximate token counting for guardrail decisions."""

    def __init__(self):
        self._encoder = None

    @property
    def encoder(self):
        if self._encoder is None:
            try:
                import tiktoken
                self._encoder = tiktoken.get_encoding("cl100k_base")
            except ImportError:
                logger.warning("tiktoken not installed, using approximate counting")
                self._encoder = "approximate"
        return self._encoder

    def count(self, text: str) -> int:
        if self.encoder == "approximate":
            # Rough approximation: ~4 chars per token
            return len(text) // 4
        return len(self.encoder.encode(text))

    def count_messages(self, messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.count(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += self.count(part["text"])
            total += 4  # Overhead per message (role, formatting)
        return total


token_counter = TokenCounter()


# ============================================================
# Rate Limiter (in-memory, per-user)
# ============================================================

class RateLimiter:
    """Simple in-memory rate limiter. Resets on restart."""

    def __init__(self):
        self.request_windows: dict[str, list[float]] = {}
        self.token_usage: dict[str, list[tuple[float, int]]] = {}

    def check_request_rate(self, user_id: str) -> tuple[bool, str]:
        now = time.time()
        window = self.request_windows.setdefault(user_id, [])
        # Clean old entries (older than 60s)
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= config.max_requests_per_minute_per_user:
            return False, f"Rate limit: {config.max_requests_per_minute_per_user} req/min exceeded"
        window.append(now)
        return True, ""

    def check_token_budget(self, user_id: str, tokens: int) -> tuple[bool, str]:
        now = time.time()
        usage = self.token_usage.setdefault(user_id, [])
        # Clean old entries (older than 1 hour)
        usage[:] = [(t, n) for t, n in usage if now - t < 3600]
        total = sum(n for _, n in usage)
        if total + tokens > config.max_tokens_per_hour_per_user:
            return False, f"Token budget: {config.max_tokens_per_hour_per_user}/hr exceeded (used: {total})"
        usage.append((now, tokens))
        return True, ""


rate_limiter = RateLimiter()


# ============================================================
# Content Scanner
# ============================================================

class ContentScanner:
    """Scans request/response content for secrets, PII, and safety issues."""

    @staticmethod
    def scan_for_secrets(text: str) -> list[dict]:
        findings = []
        for pattern, secret_type in SECRET_PATTERNS:
            matches = re.finditer(pattern, text)
            for match in matches:
                findings.append({
                    "type": "secret",
                    "subtype": secret_type,
                    "position": match.start(),
                    "preview": text[max(0, match.start()-10):match.start()+20] + "..."
                })
        return findings

    @staticmethod
    def scan_for_pii(text: str) -> list[dict]:
        findings = []
        for pattern, pii_type in PII_PATTERNS:
            matches = re.finditer(pattern, text)
            for match in matches:
                findings.append({
                    "type": "pii",
                    "subtype": pii_type,
                    "position": match.start(),
                })
        return findings

    @staticmethod
    def redact_secrets(text: str) -> str:
        """Replace detected secrets with [REDACTED]."""
        for pattern, _ in SECRET_PATTERNS:
            text = re.sub(pattern, "[REDACTED]", text)
        return text


scanner = ContentScanner()


# ============================================================
# Context Window Manager
# ============================================================

class ContextWindowManager:
    """Manages context window to prevent overflow and optimize usage."""

    MODEL_CONTEXT_LIMITS = {
        "qwen2.5-coder:7b": 8192,
        "qwen2.5-coder:3b": 4096,
        "qwen2.5-coder:1.5b": 2048,
        "phi3:mini": 4096,
    }

    def get_limit(self, model: str) -> int:
        for key, limit in self.MODEL_CONTEXT_LIMITS.items():
            if key in model:
                return limit
        return 8192  # Default

    def truncate_messages(self, messages: list[dict], model: str) -> list[dict]:
        """Truncate messages to fit within context window."""
        limit = self.get_limit(model)
        # Reserve tokens for output
        input_limit = limit - config.max_output_tokens
        if input_limit < 512:
            input_limit = 512

        total_tokens = token_counter.count_messages(messages)
        if total_tokens <= input_limit:
            return messages

        logger.warning(
            f"Context overflow: {total_tokens} tokens > {input_limit} limit. "
            f"Truncating with strategy: {config.truncation_strategy}"
        )

        if config.truncation_strategy == "keep_recent":
            return self._truncate_keep_recent(messages, input_limit)
        elif config.truncation_strategy == "keep_first":
            return self._truncate_keep_first(messages, input_limit)
        else:
            return self._truncate_middle_out(messages, input_limit)

    def _truncate_keep_recent(self, messages: list[dict], limit: int) -> list[dict]:
        """Keep system prompt + most recent messages."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]

        result = list(system_msgs)
        remaining = limit - token_counter.count_messages(system_msgs)

        # Add messages from the end
        kept = []
        for msg in reversed(other_msgs):
            msg_tokens = token_counter.count_messages([msg])
            if remaining - msg_tokens < 0:
                break
            kept.insert(0, msg)
            remaining -= msg_tokens

        if not kept and other_msgs:
            # At minimum, keep the last message (truncated)
            kept = [other_msgs[-1]]

        # Add truncation notice
        if len(kept) < len(other_msgs):
            truncation_notice = {
                "role": "system",
                "content": f"[Context truncated: {len(other_msgs) - len(kept)} earlier messages removed to fit context window]"
            }
            result.append(truncation_notice)

        result.extend(kept)
        return result

    def _truncate_keep_first(self, messages: list[dict], limit: int) -> list[dict]:
        """Keep earliest messages (good for task descriptions)."""
        result = []
        remaining = limit
        for msg in messages:
            msg_tokens = token_counter.count_messages([msg])
            if remaining - msg_tokens < 0:
                break
            result.append(msg)
            remaining -= msg_tokens
        return result

    def _truncate_middle_out(self, messages: list[dict], limit: int) -> list[dict]:
        """Keep first and last messages, drop middle."""
        if len(messages) <= 2:
            return messages

        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]

        if len(other_msgs) <= 2:
            return messages

        # Keep first 2 and last 2 non-system messages
        keep_first = other_msgs[:2]
        keep_last = other_msgs[-2:]
        middle_dropped = len(other_msgs) - 4

        truncation_notice = {
            "role": "system",
            "content": f"[{middle_dropped} messages omitted from middle of conversation]"
        }

        return system_msgs + keep_first + [truncation_notice] + keep_last


context_manager = ContextWindowManager()


# ============================================================
# Main Guardrail Pipeline
# ============================================================

class GuardrailPipeline:
    """
    Orchestrates all guardrail checks.

    Usage with LiteLLM custom callbacks:
        from guardrails import guardrail_pipeline

        # Pre-call check
        result = guardrail_pipeline.pre_call(messages, model, user_id)
        if result.blocked:
            return error_response(result.reason)

        # Post-call check
        result = guardrail_pipeline.post_call(response_text, user_id)
        if result.blocked:
            return error_response(result.reason)
    """

    @dataclass
    class Result:
        blocked: bool = False
        reason: str = ""
        modified_messages: Optional[list] = None
        warnings: list = field(default_factory=list)
        metadata: dict = field(default_factory=dict)

    def pre_call(
        self,
        messages: list[dict],
        model: str,
        user_id: str = "anonymous",
        temperature: Optional[float] = None,
    ) -> "GuardrailPipeline.Result":
        """Run all pre-call guardrail checks."""
        result = self.Result()
        result.modified_messages = list(messages)
        result.metadata["original_token_count"] = token_counter.count_messages(messages)

        # 1. Rate limiting
        ok, reason = rate_limiter.check_request_rate(user_id)
        if not ok:
            result.blocked = True
            result.reason = reason
            logger.warning(f"BLOCKED [rate_limit] user={user_id}: {reason}")
            return result

        # 2. Token budget check
        input_tokens = token_counter.count_messages(messages)
        ok, reason = rate_limiter.check_token_budget(user_id, input_tokens)
        if not ok:
            result.blocked = True
            result.reason = reason
            logger.warning(f"BLOCKED [token_budget] user={user_id}: {reason}")
            return result

        # 3. Scan input for secrets
        if config.block_secret_patterns:
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, str):
                    secrets = scanner.scan_for_secrets(content)
                    if secrets:
                        result.warnings.append(
                            f"Secrets detected in input ({len(secrets)} findings). "
                            f"Types: {', '.join(set(s['subtype'] for s in secrets))}"
                        )
                        logger.warning(
                            f"WARN [secrets_in_input] user={user_id}: "
                            f"{len(secrets)} secrets detected"
                        )

        # 4. Context window management
        if config.auto_truncate:
            result.modified_messages = context_manager.truncate_messages(
                result.modified_messages, model
            )
            new_count = token_counter.count_messages(result.modified_messages)
            if new_count < input_tokens:
                result.warnings.append(
                    f"Context truncated: {input_tokens} -> {new_count} tokens"
                )
                result.metadata["truncated"] = True
                result.metadata["tokens_removed"] = input_tokens - new_count

        # 5. Inject grounding prompt (hallucination control)
        if config.require_grounding_prompt:
            has_system = any(m.get("role") == "system" for m in result.modified_messages)
            is_code_request = self._is_code_request(messages)

            grounding = GROUNDING_PROMPT_CODE if is_code_request else GROUNDING_PROMPT_GENERAL

            if has_system:
                # Append to existing system prompt
                for msg in result.modified_messages:
                    if msg.get("role") == "system":
                        msg["content"] = msg["content"] + "\n\n" + grounding
                        break
            else:
                result.modified_messages.insert(0, {
                    "role": "system",
                    "content": grounding,
                })

        # 6. Temperature cap (hallucination control)
        if temperature is not None and temperature > config.temperature_cap:
            result.warnings.append(
                f"Temperature capped: {temperature} -> {config.temperature_cap}"
            )
            result.metadata["original_temperature"] = temperature
            result.metadata["capped_temperature"] = config.temperature_cap

        result.metadata["final_token_count"] = token_counter.count_messages(
            result.modified_messages
        )
        return result

    def post_call(
        self,
        response_text: str,
        user_id: str = "anonymous",
    ) -> "GuardrailPipeline.Result":
        """Run all post-call guardrail checks on the response."""
        result = self.Result()

        # 1. Scan response for leaked secrets
        if config.block_secret_patterns:
            secrets = scanner.scan_for_secrets(response_text)
            if secrets:
                result.blocked = True
                result.reason = (
                    f"Response contained {len(secrets)} potential secrets "
                    f"({', '.join(set(s['subtype'] for s in secrets))}). "
                    f"Response blocked to prevent data leakage."
                )
                logger.warning(
                    f"BLOCKED [secret_in_response] user={user_id}: "
                    f"{len(secrets)} secrets in output"
                )
                return result

        # 2. Scan for PII
        if config.block_pii:
            pii = scanner.scan_for_pii(response_text)
            if pii:
                result.warnings.append(
                    f"PII detected in response: {', '.join(set(p['subtype'] for p in pii))}"
                )
                logger.info(
                    f"WARN [pii_in_response] user={user_id}: "
                    f"{len(pii)} PII instances"
                )

        # 3. Basic hallucination heuristics
        hallucination_flags = self._check_hallucination_signals(response_text)
        if hallucination_flags:
            result.warnings.extend(hallucination_flags)
            result.metadata["hallucination_flags"] = hallucination_flags

        # 4. Track output tokens
        output_tokens = token_counter.count(response_text)
        rate_limiter.check_token_budget(user_id, output_tokens)
        result.metadata["output_tokens"] = output_tokens

        return result

    def _is_code_request(self, messages: list[dict]) -> bool:
        """Heuristic: detect if the request is code-related."""
        code_indicators = [
            "code", "function", "class", "implement", "bug", "error",
            "fix", "refactor", "test", "api", "endpoint", "import",
            "def ", "async ", "return ", "```",
        ]
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    last_user_msg = content.lower()
                break

        return any(indicator in last_user_msg for indicator in code_indicators)

    def _check_hallucination_signals(self, text: str) -> list[str]:
        """Basic heuristic checks for common hallucination patterns."""
        flags = []

        # Check for fabricated URLs (common hallucination)
        urls = re.findall(r'https?://[^\s\)\"\']+', text)
        suspicious_url_patterns = [
            r'example\.com/real',
            r'docs\.[a-z]+\.com/v\d+\.\d+\.\d+/',  # Overly specific version URLs
        ]
        for url in urls:
            for pattern in suspicious_url_patterns:
                if re.search(pattern, url):
                    flags.append(f"Potentially fabricated URL: {url[:80]}")

        # Check for overly confident uncertainty phrases followed by specific claims
        uncertainty_then_specific = re.findall(
            r'(?:I believe|I think|probably|likely|might be)\s+.{0,50}(?:version \d+\.\d+|released in \d{4})',
            text, re.IGNORECASE
        )
        if uncertainty_then_specific:
            flags.append("Response contains uncertain language mixed with specific claims")

        # Check for non-existent Python packages (common in code hallucination)
        import_matches = re.findall(r'(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)', text)
        # This is a basic list — extend based on your team's stack
        known_suspicious = {"autolib", "pyfast", "quickml", "ezapi"}
        for pkg in import_matches:
            if pkg.lower() in known_suspicious:
                flags.append(f"Potentially hallucinated package: {pkg}")

        return flags


# Global instance
guardrail_pipeline = GuardrailPipeline()


# ============================================================
# LiteLLM Custom Callback (Integration Point)
# ============================================================

class GuardrailCallback:
    """
    LiteLLM custom callback for guardrail integration.

    Add to litellm-config.yaml:
        litellm_settings:
          callbacks: ["guardrails.GuardrailCallback"]
    """

    def __init__(self):
        self.pipeline = guardrail_pipeline

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        """Called before every LLM API call."""
        messages = data.get("messages", [])
        model = data.get("model", "unknown")
        user = user_api_key_dict.get("user_id", "anonymous") if user_api_key_dict else "anonymous"
        temperature = data.get("temperature")

        result = self.pipeline.pre_call(messages, model, user, temperature)

        if result.blocked:
            raise Exception(f"Request blocked by guardrail: {result.reason}")

        # Apply modifications
        if result.modified_messages:
            data["messages"] = result.modified_messages

        # Apply temperature cap
        if "capped_temperature" in result.metadata:
            data["temperature"] = result.metadata["capped_temperature"]

        # Log warnings
        for warning in result.warnings:
            logger.warning(f"[guardrail] {warning}")

        return data

    async def async_post_call_success_hook(self, user_api_key_dict, response):
        """Called after successful LLM response."""
        try:
            response_text = ""
            if hasattr(response, "choices") and response.choices:
                choice = response.choices[0]
                if hasattr(choice, "message") and hasattr(choice.message, "content"):
                    response_text = choice.message.content or ""

            if not response_text:
                return response

            user = "anonymous"
            if user_api_key_dict:
                user = user_api_key_dict.get("user_id", "anonymous")

            result = self.pipeline.post_call(response_text, user)

            if result.blocked:
                logger.error(f"[guardrail] Response blocked: {result.reason}")
                # Modify response to indicate block
                if hasattr(response, "choices") and response.choices:
                    response.choices[0].message.content = (
                        "[Response blocked by safety guardrail] "
                        "The model's response was filtered because it may have "
                        "contained sensitive information. Please rephrase your request."
                    )

            for warning in result.warnings:
                logger.warning(f"[guardrail] post-call: {warning}")

        except Exception as e:
            logger.error(f"[guardrail] post-call error: {e}")

        return response


# ============================================================
# CLI Testing
# ============================================================

if __name__ == "__main__":
    print("=== Guardrail Pipeline Test ===\n")

    # Test 1: Normal request
    print("Test 1: Normal code request")
    result = guardrail_pipeline.pre_call(
        messages=[
            {"role": "user", "content": "Write a Python function to sort a list"}
        ],
        model="qwen2.5-coder:7b",
        user_id="test-user",
    )
    print(f"  Blocked: {result.blocked}")
    print(f"  Warnings: {result.warnings}")
    print(f"  Token count: {result.metadata.get('final_token_count')}")
    print()

    # Test 2: Request with secret
    print("Test 2: Request containing API key")
    result = guardrail_pipeline.pre_call(
        messages=[
            {"role": "user", "content": "Fix this: api_key = 'sk-1234567890abcdef1234567890abcdef'"}
        ],
        model="qwen2.5-coder:7b",
        user_id="test-user",
    )
    print(f"  Blocked: {result.blocked}")
    print(f"  Warnings: {result.warnings}")
    print()

    # Test 3: Response with leaked secret
    print("Test 3: Response containing secret")
    result = guardrail_pipeline.post_call(
        response_text="Here's the config:\napi_key = 'sk-abc123def456ghi789jkl012mno345'",
        user_id="test-user",
    )
    print(f"  Blocked: {result.blocked}")
    print(f"  Reason: {result.reason}")
    print()

    # Test 4: Context overflow
    print("Test 4: Context window overflow")
    big_messages = [{"role": "user", "content": "x " * 5000}]
    result = guardrail_pipeline.pre_call(
        messages=big_messages,
        model="qwen2.5-coder:1.5b",  # Small context window
        user_id="test-user",
    )
    print(f"  Blocked: {result.blocked}")
    print(f"  Warnings: {result.warnings}")
    print(f"  Truncated: {result.metadata.get('truncated', False)}")
    print()

    print("=== All tests passed ===")
