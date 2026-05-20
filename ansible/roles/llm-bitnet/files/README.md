# llm-bitnet/files — Offline Prep

## Option A — Pre-built binary (preferred)

Place the ARM64 Darwin binary here:
```
ansible/roles/llm-bitnet/files/bitnet-server-darwin-arm64
```

Build on a Mac with internet:
```bash
git clone https://github.com/microsoft/BitNet.git
cd BitNet
cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_METAL=ON
cmake --build build --config Release -j$(sysctl -n hw.logicalcpu)
cp build/bin/bitnet-server ansible/roles/llm-bitnet/files/bitnet-server-darwin-arm64
```

## Option B — Python wheels (fallback)

```bash
pip download \
  "bitnet>=0.0.1" "torch>=2.3.0" \
  "transformers>=4.41.0" "huggingface-hub>=0.23.0" \
  "fastapi==0.115.0" "uvicorn[standard]==0.32.0" \
  "numpy>=1.26.0" \
  --platform macosx_14_0_arm64 \
  --python-version 311 \
  --only-binary=:all: \
  -d ansible/roles/llm-bitnet/files/wheels/
```

The role auto-detects which option is available; binary takes precedence.
