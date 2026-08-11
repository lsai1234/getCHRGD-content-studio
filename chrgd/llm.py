"""One OpenAI client per credential, shared by every call in the process.

Six places in the engine built their own `OpenAI(...)` — the chat client, the
concept judge, the claims/likeness judge, the scroll test, the trend scout,
today's pick — and the image path built a fresh one *per generated image*.
Each of those carries its own httpx connection pool, so every call paid a DNS
lookup, a TCP handshake and a TLS handshake before it could send a byte, and
threw the warm connection away afterwards.

That is a fixed tax of roughly 100-300ms on a fast connection and considerably
more on a slow or distant one, on every single call — and the engine makes a
lot of small calls (a gate is four or five). Reusing one client per
(key, base URL) means the second call onward rides a connection that is
already open.

The SDK's client is thread-safe and its pool is sized for concurrency
(1000 connections, 100 kept alive), which is what makes the parallel render
and the parallel gates safe to point at a shared client.
"""

from __future__ import annotations

import threading

#: Cache key → client. Small and process-lifetime: there is one credential in
#: normal use, two if an image key is set separately.
_CLIENTS: dict[tuple, object] = {}
_LOCK = threading.Lock()


def openai_client(api_key: str, base_url: str, *, timeout: float | None = None):
    """The shared `OpenAI` client for this credential, creating it on first use.

    Raises ImportError if the SDK isn't installed — callers wrap that in their
    own error type, exactly as they did when they constructed the client
    themselves.
    """
    from openai import OpenAI

    key = (api_key, base_url, timeout)
    client = _CLIENTS.get(key)
    if client is None:
        with _LOCK:
            client = _CLIENTS.get(key)
            if client is None:
                kwargs = {"api_key": api_key, "base_url": base_url}
                if timeout:
                    kwargs["timeout"] = timeout
                client = OpenAI(**kwargs)
                _CLIENTS[key] = client
    return client


def text_client(settings):
    """The shared client for text calls (chat + responses), with the text timeout."""
    return openai_client(
        settings.openai_api_key,
        settings.get_openai_base_url(),
        timeout=settings.openai_timeout or None,
    )


def image_client(settings):
    """The shared client for image generation, with the (longer) image timeout.

    Separate from `text_client` because the timeouts differ — an image is
    minutes of server-side work where a JSON completion is seconds — and
    because the image key can be a different credential.
    """
    return openai_client(
        settings.get_image_key(),
        settings.get_openai_base_url(),
        timeout=settings.image_timeout or None,
    )


def reset_clients() -> None:
    """Drop the cached clients (settings changed / test teardown)."""
    with _LOCK:
        _CLIENTS.clear()
