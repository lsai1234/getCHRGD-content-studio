"""Resilience for models that only accept the default temperature, and the
current-date anchoring that stops the scout drifting to old years."""

from __future__ import annotations

from chrgd.pipeline import _temperature_unsupported, chat_json_create

_TEMP_400 = (
    "Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' "
    "does not support 0.9 with this model. Only the default (1) value is "
    "supported.\", 'type': 'invalid_request_error', 'param': 'temperature'}}"
)


def test_detects_the_temperature_400():
    assert _temperature_unsupported(Exception(_TEMP_400))
    assert not _temperature_unsupported(Exception("rate limit exceeded"))


class _FakeCompletions:
    def __init__(self, reject_temperature):
        self.reject = reject_temperature
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject and "temperature" in kwargs:
            raise Exception(_TEMP_400)
        return {"ok": True, "temperature_sent": "temperature" in kwargs}


class _FakeClient:
    def __init__(self, reject_temperature):
        self.chat = type("C", (), {"completions": _FakeCompletions(reject_temperature)})()


def test_retries_without_temperature_when_model_rejects_it():
    client = _FakeClient(reject_temperature=True)
    resp = chat_json_create(client, model="gpt-x", system="s", user="u", temperature=0.9)
    # It fell back to a second call with no temperature and succeeded.
    assert resp["temperature_sent"] is False
    assert len(client.chat.completions.calls) == 2
    assert "temperature" not in client.chat.completions.calls[1]


def test_keeps_temperature_when_the_model_accepts_it():
    client = _FakeClient(reject_temperature=False)
    resp = chat_json_create(client, model="gpt-x", system="s", user="u", temperature=0.9)
    assert resp["temperature_sent"] is True
    assert len(client.chat.completions.calls) == 1


def test_other_errors_are_not_swallowed():
    class Boom:
        def create(self, **kwargs):
            raise Exception("insufficient_quota")

    client = type("K", (), {"chat": type("C", (), {"completions": Boom()})()})()
    try:
        chat_json_create(client, model="m", system="s", user="u", temperature=0.9)
    except Exception as exc:  # noqa: BLE001
        assert "insufficient_quota" in str(exc)
    else:
        raise AssertionError("a non-temperature error should propagate")


def test_scout_anchors_to_todays_date(monkeypatch):
    import chrgd.trends as trends
    from chrgd.config import Settings

    seen = {}

    class FakeClient:
        def search(self, system, user):
            seen["user"] = user
            return '{"moments": []}'

    trends.scout_discover(Settings(CHRGD_SECRET_KEY="x"), "moments", 3, client=FakeClient())
    assert "TODAY'S DATE IS" in seen["user"]
    assert "do NOT rely on training knowledge" in seen["user"]
