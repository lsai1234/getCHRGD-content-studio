"""Server-side connectivity diagnostic for 'Connection error.' from the engine.

Run on the VPS from the app directory:  .venv/bin/python deploy/diagnose.py
Prints ONLY redacted/config-shape info — never the API key itself — so the
output is safe to read in CI logs.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chrgd.config import get_settings  # noqa: E402

s = get_settings()
key = s.openai_api_key or ""
print(f"openai key set: {bool(key)}  length: {len(key)}  prefix: {key[:8]!r}")
print(f"base_url override: {s.openai_base_url or 'none'}")
print(f"chat model: {s.openai_model}   image model: {s.image_model}")

print("--- DNS ---")
try:
    infos = socket.getaddrinfo("api.openai.com", 443)
    addrs = sorted({i[4][0] for i in infos})
    print("api.openai.com ->", addrs)
except Exception as exc:  # noqa: BLE001
    print("DNS FAILED:", type(exc).__name__, exc)

import httpx  # noqa: E402

print("--- plain HTTPS (expect 401) ---")
try:
    r = httpx.get("https://api.openai.com/v1/models", timeout=15)
    print("status:", r.status_code)
except Exception as exc:  # noqa: BLE001
    print("FAILED:", type(exc).__name__, exc)

print("--- forced IPv4 (expect 401; isolates broken IPv6) ---")
try:
    transport = httpx.HTTPTransport(local_address="0.0.0.0")
    with httpx.Client(transport=transport, timeout=15) as c:
        r = c.get("https://api.openai.com/v1/models")
    print("status:", r.status_code)
except Exception as exc:  # noqa: BLE001
    print("FAILED:", type(exc).__name__, exc)

print("--- authed models list ---")
try:
    r = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    print("status:", r.status_code)
    if r.status_code == 200:
        ids = [m["id"] for m in r.json()["data"]]
        print("image models:", sorted(i for i in ids if "image" in i))
        print("gpt-4o family present:", any(i.startswith("gpt-4o") for i in ids))
    else:
        print("body:", r.text[:300])
except Exception as exc:  # noqa: BLE001
    print("FAILED:", type(exc).__name__, exc)

print("--- SDK chat call (the exact path the engine uses) ---")
try:
    from chrgd.pipeline import OpenAIChatClient

    client = OpenAIChatClient(s)
    result = client.complete(
        'Reply with exactly this JSON object: {"ok": true}', "go"
    )
    print("OK:", result.content[:80])
except Exception as exc:  # noqa: BLE001
    print("FAILED:", type(exc).__name__, str(exc)[:400])
