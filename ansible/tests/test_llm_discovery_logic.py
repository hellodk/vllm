# ansible/tests/test_llm_discovery_logic.py
import re
import pytest

QUANT_REGEX = r"[-_](q[0-9]+[_k_msx0-9]*|f16|f32|f64|mxfp4|gguf)"


def extract_quant(model_name: str) -> str:
    m = re.search(QUANT_REGEX, model_name, re.IGNORECASE)
    return m.group(1).lower() if m else "unknown"


@pytest.mark.parametrize("model_name,expected", [
    ("qwen2.5-coder-3b-q4_k_m",              "q4_k_m"),
    ("llama3:8b-instruct-q4_K_M",            "q4_k_m"),
    ("gpt-oss-20B-MXFP4-MoE",               "mxfp4"),
    ("bge-m3-f16",                           "f16"),
    ("llama3.2-3b-q4_0",                     "q4_0"),
    ("mistral-7b-v0.3-q8_0",                "q8_0"),
    ("some-model-without-quant",             "unknown"),
    ("Qwen2.5-14B-Instruct-Q4_K_M.gguf",   "q4_k_m"),
])
def test_extract_quant(model_name, expected):
    assert extract_quant(model_name) == expected
