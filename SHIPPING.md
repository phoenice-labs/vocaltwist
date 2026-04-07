# VocalTwist — Shipping & Compiled Distribution Guide

> This guide explains how to build, distribute, and integrate the **compiled/minified**
> version of VocalTwist.  For source-code integration instructions see
> [INTEGRATION.md](INTEGRATION.md).

---

## Why compile?

| Concern | Solution |
|---------|----------|
| Protect proprietary modifications to Python source | `compileall` produces `.pyc` bytecode; `.py` files are excluded from `dist/` |
| Reduce frontend JS payload size | `esbuild` minifies JS ~60–70%; removes comments and whitespace |
| Simplify deployment | `dist/` is a self-contained directory; no build step needed by the receiver |
| Consistent builds | `build.ps1` is reproducible and version-pinned |

> **Note:** Python `.pyc` bytecode is not encryption.  It can be decompiled with
> tools like `uncompyle6`.  If you need strong IP protection, consider a commercial
> obfuscator (Cython, PyArmor) as a separate step.  For most open-source or
> internal deployments, bytecode is sufficient to avoid casual browsing of source.

---

## Building

### Prerequisites

- Python 3.10+ virtual environment with dependencies installed
- Node.js 18+ (only needed for JS minification)

### Run the build script

```powershell
# Full build (frontend minification + backend bytecode)
.\build.ps1

# Skip frontend (no Node.js required)
.\build.ps1 -SkipFrontend

# Skip backend compilation (JS only)
.\build.ps1 -SkipBackend

# Keep existing dist/ (incremental)
.\build.ps1 -Clean:$false
```

The script produces:

```
dist/
├── backend/                  # Python .pyc files (no .py source)
│   ├── __init__.pyc
│   ├── config.pyc
│   ├── middleware.pyc
│   ├── models.pyc
│   ├── security.pyc
│   └── providers/
│       ├── base.pyc
│       ├── edge_tts_provider.pyc
│       └── whisper_provider.pyc
│
├── frontend/                 # Minified JS and CSS
│   ├── vocal-twist.min.js    # ~10KB (from ~32KB source)
│   ├── ambient-vad.min.js    # ~4KB  (from ~12KB source)
│   ├── vocal-twist.min.css   # ~3KB  (from ~8KB source)
│   └── sample-usage.html     # Updated to reference .min files
│
├── VocalTwistTest/           # Compiled demo app
│   ├── app.pyc
│   ├── static/               # Chat UI HTML/JS + minified VT assets
│   └── tests/                # (bytecode only — for CI use)
│
├── .env.example
├── requirements.txt
├── openapi.yaml
├── README.md
├── INTEGRATION.md
├── SHIPPING.md
├── LICENSE
└── start.ps1                 # Self-bootstrapping launcher
```

---

## Running the compiled build

```powershell
cd dist
.\start.ps1
```

The launcher:
1. Creates a `.venv` if one doesn't exist
2. Installs dependencies from `requirements.txt`
3. Copies `.env.example` → `.env` if `.env` doesn't exist
4. Starts the server on port 8000

Or run manually:
```powershell
cd dist
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
copy .env.example .env    # then edit as needed
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\uvicorn.exe VocalTwistTest.app:app --port 8000
```

---

## Integrating the compiled frontend into an existing app

### Differences from INTEGRATION.md

| INTEGRATION.md (source) | SHIPPING.md (compiled) |
|------------------------|------------------------|
| `<script src=".../vocal-twist.js">` | `<script src=".../vocal-twist.min.js">` |
| `<script src=".../ambient-vad.js">` | `<script src=".../ambient-vad.min.js">` |
| `<link href=".../vocal-twist.css">` | `<link href=".../vocal-twist.min.css">` |
| Source maps available | No source maps (minified) |
| Readable for debugging | Use browser DevTools "pretty print" |

All JavaScript **class names, method names, and callback signatures are identical**
between source and compiled versions.  Only whitespace and comments are removed;
no symbols are renamed (non-mangled build).

### Serving minified assets from your own server

Copy the three files from `dist/frontend/` to your web server's static directory:

```
your-app/
└── static/
    ├── vocal-twist.min.js
    ├── ambient-vad.min.js
    └── vocal-twist.min.css
```

Reference them in your HTML:

```html
<!-- Load onnxruntime-web + vad-web from CDN (only needed for ambient mode) -->
<script src="https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/ort.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@ricky0123/vad-web/dist/bundle.min.js"></script>

<!-- Load minified VocalTwist assets -->
<script src="/static/ambient-vad.min.js"></script>
<link  rel="stylesheet" href="/static/vocal-twist.min.css" />
<script src="/static/vocal-twist.min.js"></script>
```

Everything else in INTEGRATION.md (JavaScript API, REST calls, language selection,
agentic patterns) is identical.

### Serving from the VocalTwist backend (compiled)

If you are running the compiled `dist/` build as your server, the backend
automatically serves the minified assets at:

```
http://localhost:8000/vocal-twist.min.js
http://localhost:8000/ambient-vad.min.js
http://localhost:8000/vocal-twist.min.css
```

Update your `<script src>` paths accordingly.

---

## Integrating the compiled backend into an existing FastAPI app

The compiled `.pyc` files are importable exactly like `.py` files — Python's
import system resolves them transparently:

```python
# app.py — your existing application
from fastapi import FastAPI

# ── VocalTwist (compiled) ──────────────────────────────────────────────────────
import sys
sys.path.insert(0, "/path/to/dist")   # dist/ must be on sys.path

from backend.middleware import router as vt_router  # imports .pyc automatically

app = FastAPI()
app.include_router(vt_router)   # adds /api/transcribe, /api/speak, etc.
```

Or set `PYTHONPATH` in your environment before launching:

```bash
PYTHONPATH=/path/to/dist uvicorn myapp:app --port 8000
```

> The `.env` file must be present in the working directory (or pass settings
> as environment variables).  See [INTEGRATION.md § Environment Variables](INTEGRATION.md#10-environment-variables-reference).

---

## Updating after source changes

After modifying any `.py` or `.js` file in the source tree:

```powershell
.\build.ps1        # rebuilds dist/ completely
```

Distribute the new `dist/` to consumers.  The build is idempotent —
running it multiple times produces the same output.

---

## Security notes on distribution

- **Never include `.env`** in `dist/` — it contains secrets.  Ship only `.env.example`.
- The compiled `dist/` still requires `requirements.txt` to be installed by the
  receiver.  Dependencies are not bundled.
- `requirements.txt` pins all versions exactly — review it for CVEs before shipping:
  ```bash
  pip-audit -r requirements.txt
  ```
- Sign the `dist/` archive before sending to third parties:
  ```powershell
  Get-FileHash dist\*.whl -Algorithm SHA256   # or use cosign / sigstore
  ```
