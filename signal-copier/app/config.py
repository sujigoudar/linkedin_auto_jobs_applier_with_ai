"""Runtime configuration.

Secrets (broker API keys, bot tokens, webhook secrets) come from environment
variables only — never from the YAML routing/account files, so those files
stay safe to commit as examples.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

ROUTING_CONFIG_PATH = Path(os.getenv("ROUTING_CONFIG_PATH", BASE_DIR / "config" / "routing.yaml"))
ACCOUNTS_CONFIG_PATH = Path(os.getenv("ACCOUNTS_CONFIG_PATH", BASE_DIR / "config" / "accounts.yaml"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "signal_copier.db"))

# Shared secret the webhook source checks against a header/query param so
# random requests on the public endpoint can't inject fake signals.
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
