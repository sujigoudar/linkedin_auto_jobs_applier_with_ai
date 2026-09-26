"""C06 (bounded): HTTP abuse controls for this app's two ingress routes
that accept unauthenticated-until-checked traffic (`POST
/webhook/{source_name}`, `POST /sms/twilio`) -- each request still costs
real work (JSON/form parsing, an auth compare, a store lookup for
idempotency) before it can be rejected as invalid/unauthorized, so an
unrestrained flood can burn CPU and DB connections even when every
single request is ultimately refused.

Uses slowapi (a thin FastAPI wrapper over the `limits` library) with its
default in-memory fixed-window store, keyed by client IP
(`get_remote_address`). That's the right scope for how this app actually
runs: one process, one writer (see README's single-active-writer
precedent) -- a deployment with multiple processes behind a shared load
balancer would need a shared backend (slowapi's `storage_uri`, e.g.
Redis), not implemented here since nothing in this project runs that
way.

The limit (30/minute per IP, on each route independently) is a defensive
ceiling against abuse/flooding, not a constraint on legitimate traffic: a
real alerting platform firing several signals across different symbols
within the same second stays far under it. It exists to stop a flood
(malicious or a misbehaving/looping sender), not to throttle normal use.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

INGRESS_RATE_LIMIT = "30/minute"
