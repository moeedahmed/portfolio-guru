import os
import shutil
import subprocess
import json

KAIZEN_USERNAME_ID = "6e14d32b-6fff-480d-87b0-b3f300ee30f6"
KAIZEN_PASSWORD_ID = "f311d41a-fa77-44f8-be42-b3f300ee3e08"

# BWS token file search paths — checked in order.
# Override with BWS_TOKEN_PATH env var for non-standard setups.
_DEFAULT_BWS_TOKEN_PATHS = [
    "~/.bws-token",
    "~/.openclaw/.bws-token",
    "~/.config/bws/token",
]


def _find_bws_token() -> str | None:
    """Locate the BWS access token from env or token files."""
    token = os.environ.get("BWS_ACCESS_TOKEN")
    if token:
        return token

    override = os.environ.get("BWS_TOKEN_PATH")
    search_paths = [override] if override else _DEFAULT_BWS_TOKEN_PATHS

    for path in search_paths:
        expanded = os.path.expanduser(path)
        if os.path.isfile(expanded):
            with open(expanded) as f:
                token = f.read().strip()
            if token:
                return token
    return None


def get_bws_secret(secret_id: str) -> str:
    """Fetch a secret from Bitwarden Secrets Manager."""
    bws_token = _find_bws_token()
    if not bws_token:
        raise ValueError("BWS_ACCESS_TOKEN not available (checked env and token file paths)")

    bws_bin = os.environ.get("BWS_BIN", shutil.which("bws") or "/usr/local/bin/bws")

    result = subprocess.run(
        [bws_bin, "secret", "get", secret_id, "--output", "json"],
        env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["value"]


def get_kaizen_credentials() -> tuple[str, str]:
    """Returns (username, password) for Kaizen."""
    username = os.environ.get("KAIZEN_USERNAME") or get_bws_secret(KAIZEN_USERNAME_ID)
    password = os.environ.get("KAIZEN_PASSWORD") or get_bws_secret(KAIZEN_PASSWORD_ID)
    return username, password
