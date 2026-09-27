# Standard installation profiles

These are **candidates pending clean-environment GPU acceptance**. Successful
resolution or CPU imports are not proof of inference compatibility.

ComfyUI revision: `79be670e2d9be63e238785af307369d2b9039ed1`.
Target: Linux x86_64, glibc >= 2.28, Python 3.12, CUDA 12.8 PyTorch wheels.
The requirements lock includes exact versions and distribution SHA-256 hashes.
It was generated with uv 0.11.19 from the upstream `requirements.txt` plus:

```text
torch==2.8.0
torchvision==0.23.0
torchaudio==2.8.0
transformers<5
```

Maintainer regeneration (explicit dependency resolution, never at user install):

```bash
uv pip compile candidate.in --python-version 3.12 \
  --python-platform x86_64-unknown-linux-gnu --torch-backend cu128 \
  --generate-hashes --emit-index-url --no-header \
  --output-file comfy-cu128-py312.txt
```

`candidate.in` includes the pinned upstream requirements and the constraints above.
Users install with `uv pip sync --require-hashes --torch-backend cu128`.
The backend option is required because the lock's torch local versions come from
the official PyTorch CUDA index, not ordinary PyPI.

IndexTTS uses the committed upstream `uv.lock` at
`ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c`; its hash is checked before
`uv sync --frozen --no-dev`. The standard profile selects Python 3.11;
upstream excludes Python 3.12. Both environments are separate from Worker core.

Model weights are not part of dependency installation. Source-build toolchains,
system packages, drivers and build-isolation dependencies are not a complete
machine image lock; the installation manifest records that boundary.
