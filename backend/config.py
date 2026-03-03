import os
import subprocess
import json

KAIZEN_USERNAME_ID = "6e14d32b-6fff-480d-87b0-b3f300ee30f6"
KAIZEN_PASSWORD_ID = "f311d41a-fa77-44f8-be42-b3f300ee3e08"


def get_bws_secret(secret_id: str) -> str:
    """Fetch a secret from Bitwarden Secrets Manager."""
    bws_token_path = os.path.expanduser("~/.openclaw/.bws-token")

    # In production (Railway), BWS_ACCESS_TOKEN is set as env var
    bws_token = os.environ.get("BWS_ACCESS_TOKEN")
    if not bws_token and os.path.exists(bws_token_path):
        with open(bws_token_path) as f:
            bws_token = f.read().strip()

    if not bws_token:
        raise ValueError("BWS_ACCESS_TOKEN not available")

    result = subprocess.run(
        ["/usr/local/bin/bws", "secret", "get", secret_id, "--output", "json"],
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
