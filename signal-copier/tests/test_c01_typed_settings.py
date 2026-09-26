"""C01: app/config.py reads its values through a pydantic-settings
BaseSettings model. STANDBY_MODE and FORCE_SECURE_COOKIES used to parse
booleans with `.strip().lower() in ("1", "true", "yes")` -- any
unrecognized string (a typo, "enabled", a stray space-only value) silently
became False. For STANDBY_MODE that's the unsafe direction: a malformed
value defaults to "not in standby," i.e. live trading authority. A
malformed boolean must now fail loud at import time instead.
"""
import subprocess
import sys

_IMPORT_CONFIG = "import app.config"


def _run_with_env(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_CONFIG],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_malformed_standby_mode_fails_loud_at_import():
    result = _run_with_env({"STANDBY_MODE": "enabled"})
    assert result.returncode != 0
    assert "STANDBY_MODE" in result.stderr
    assert "ValidationError" in result.stderr or "validation error" in result.stderr


def test_malformed_force_secure_cookies_fails_loud_at_import():
    result = _run_with_env({"FORCE_SECURE_COOKIES": "yup"})
    assert result.returncode != 0
    assert "FORCE_SECURE_COOKIES" in result.stderr


def test_recognized_boolean_spellings_still_work():
    import os

    for value, expected in [
        ("true", "True"),
        ("1", "True"),
        ("yes", "True"),
        ("false", "False"),
        ("0", "False"),
        ("no", "False"),
    ]:
        result = subprocess.run(
            [sys.executable, "-c", "import app.config as c; print(c.STANDBY_MODE)"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "STANDBY_MODE": value},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


def test_unset_standby_mode_defaults_false():
    import os

    env = {k: v for k, v in os.environ.items() if k != "STANDBY_MODE"}
    result = subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(c.STANDBY_MODE)"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_comma_separated_list_fields_still_parse():
    import os

    result = subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(c.TWILIO_ALLOWED_FROM_NUMBERS)"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "TWILIO_ALLOWED_FROM_NUMBERS": "+15551234567, +15557654321"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['+15551234567', '+15557654321']"
