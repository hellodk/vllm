#!/usr/bin/env python3
"""
Hydra AirLLM Server
Minimal OpenAI-compatible HTTP wrapper around AirLLM for layer-by-layer inference.
Loaded model path and cache dir come from environment variables set by LaunchDaemon.

Endpoints:
  GET  /health
  GET  /v1/models
  POST /v1/chat/completions
  POST /v1/completions
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

MODEL_PATH   = os.environ.get("AIRLLM_MODEL_PATH", "")
CACHE_DIR    = os.environ.get("AIRLLM_CACHE_DIR", "/tmp/airllm_cache")
COMPRESSION  = float(os.environ.get("AIRLLM_COMPRESSION", "0.16"))

if not MODEL_PATH:
    raise RuntimeError("AIRLLM_MODEL_PATH environment variable is not set")

app = FastAPI(title="Hydra AirLLM", version="1.0")

_model_lock = threading.Lock()
_model = None


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from airllm import AutoModel
            _model = AutoModel.from_pretrained(
                MODEL_PATH,
                compression=COMPRESSION,
                cache_dir=CACHE_DIR,
            )
    return _model


# ── Streaming helper ──────────────────────────────────────────────────────────

def _stream_response(text: str, model_name: str):
    """Yield SSE chunks simulating token streaming.

    AirLLM generates layer-by-layer rather than token-by-token, so true
    incremental streaming is not available without patching the library.
    Instead the full response is generated first, then chunked into ~3-word
    pieces and emitted as OpenAI-format SSE events.  This unblocks clients
    (e.g. LiteLLM) that send stream=True and would otherwise wait for the full
    blocking response before reading a single byte.
    """
    words = text.split(" ")
    chunk_size = 3  # ~3 words per chunk (~4 tokens on average)
    for i in range(0, len(words), chunk_size):
        chunk_text = " ".join(words[i:i + chunk_size])
        if i + chunk_size < len(words):
            chunk_text += " "
        chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    # final chunk signals end-of-stream
    final = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "airllm-server", "model": MODEL_PATH}


# ── Models ────────────────────────────────────────────────────────────────────

@app.get("/v1/models")
def list_models():
    model_name = os.path.basename(MODEL_PATH) or "airllm-model"
    return {
        "object": "list",
        "data": [{
            "id": model_name,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "hydra",
        }],
    }


# ── Request schemas ───────────────────────────────────────────────────────────

class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = ""
    messages: List[Message]
    max_tokens: int = 512
    temperature: float = 0.7
    stream: bool = False


class CompletionRequest(BaseModel):
    model: str = ""
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.7
    stream: bool = False


# ── Chat completions ──────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    model = _get_model()
    model_name = os.path.basename(MODEL_PATH) or "airllm-model"
    prompt = "\n".join(f"{m.role}: {m.content}" for m in req.messages) + "\nassistant:"
    try:
        tokens = model.tokenizer(prompt, return_tensors="pt")
        output = model.generate(
            tokens["input_ids"],
            max_new_tokens=req.max_tokens,
            temperature=req.temperature,
        )
        text = model.tokenizer.decode(output[0], skip_special_tokens=True)
        reply = text[len(prompt):].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # accurate token count via tokenizer instead of word-splitting
    completion_ids = model.tokenizer(reply, return_tensors="pt")["input_ids"]
    completion_token_count = len(completion_ids[0])
    prompt_token_count = len(tokens["input_ids"][0])

    if req.stream:
        return StreamingResponse(
            _stream_response(reply, model_name),
            media_type="text/event-stream",
        )

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": reply},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_token_count,
            "completion_tokens": completion_token_count,
            "total_tokens": prompt_token_count + completion_token_count,
        },
    }


# ── Text completions ──────────────────────────────────────────────────────────

@app.post("/v1/completions")
def completions(req: CompletionRequest):
    model = _get_model()
    model_name = os.path.basename(MODEL_PATH) or "airllm-model"
    try:
        tokens = model.tokenizer(req.prompt, return_tensors="pt")
        output = model.generate(
            tokens["input_ids"],
            max_new_tokens=req.max_tokens,
            temperature=req.temperature,
        )
        text = model.tokenizer.decode(output[0], skip_special_tokens=True)
        reply = text[len(req.prompt):].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # accurate token count via tokenizer instead of word-splitting
    completion_ids = model.tokenizer(reply, return_tensors="pt")["input_ids"]
    completion_token_count = len(completion_ids[0])
    prompt_token_count = len(tokens["input_ids"][0])

    if req.stream:
        return StreamingResponse(
            _stream_response(reply, model_name),
            media_type="text/event-stream",
        )

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:8]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"text": reply, "index": 0, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_token_count,
            "completion_tokens": completion_token_count,
            "total_tokens": prompt_token_count + completion_token_count,
        },
    }
