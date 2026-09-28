"""HTTP abuse controls for this service's own public, unauthenticated-
by-design routes -- currently just `POST /portfolios/{slug}/fit-
simulation` (PU-03's "Try our fit simulator"), the one public route that
does real off-box work (a signed call to signal-copier) per request.
Same convention as signal-copier's own app/rate_limit.py (deliberately
NOT shared code -- these are two separate processes/deployments): uses
slowapi (a thin FastAPI wrapper over the `limits` library) with its
default in-memory fixed-window store, keyed by client IP
(`get_remote_address`). A deployment running multiple processes behind a
shared load balancer would need a shared backend (slowapi's
`storage_uri`, e.g. Redis) -- not implemented here since nothing in this
project runs that way yet.

The limit is a defensive ceiling against abuse/flooding of the one route
that fans out into a cross-service call, not a constraint on legitimate
browsing -- a real prospect trying a few account sizes on one portfolio
page stays far under it.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

#: `POST /portfolios/{slug}/fit-simulation` -- lower than signal-copier's
#: own CATALOG_FIT_SIM_RATE_LIMIT (20/minute) because THIS is the layer
#: that actually sees each individual visitor's own IP (signal-copier
#: only ever sees this service's own egress IP for every visitor
#: combined) -- this is the real per-visitor bound.
PUBLIC_FIT_SIM_RATE_LIMIT = "10/minute"
