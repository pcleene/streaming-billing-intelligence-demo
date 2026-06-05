"""Kafka / change-stream consumers for the Python service.

The producer side lives in `app.streaming` (MSK producer + topic admin).
This package houses the consuming half: workers that pull events off
the topic and write them through the typed repositories. PR-3 adds
`transaction_consumer` — the V3 ingress that calls
`transaction_repo.insert_extref(...)` per event.
"""
