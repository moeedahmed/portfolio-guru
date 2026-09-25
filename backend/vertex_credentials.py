"""Fetch the Vertex AI service-account credential from BWS into process
memory only — never to disk, never into the process environment.

Supersedes the earlier persistent-file contract (a validated, operator
-provisioned key at GCP_VERTEX_CREDENTIALS_PATH). BWS remains the canonical
encrypted store; this module performs exactly one lookup, by a fixed,
nonsecret secret id configured via PG_VERTEX_SA_SECRET_ID, and turns the
result directly into an in-memory google.oauth2.service_account.Credentials
object.

Contract:
  - The BWS access token is never read from this process's own environment.
    It is read, at call time, from a regular file at the absolute path in
    PG_VERTEX_BWS_TOKEN_PATH (a nonsecret config value the activation owner
    points at the existing beta token route — e.g. ~/.openclaw/.bws-token or
    ~/.hermes/.bws-token — this module does not choose or default that
    path). The file must be owned by the current user, not a symlink, not
    group/other readable, and is read with a bounded, O_NOFOLLOW open. The
    token is handed only to the `bws` child process's environment — it is
    never stored in this process's own os.environ.
  - Invokes a fixed, guarded `bws` binary path (no PATH search, no
    `shutil.which`), with shell=False, a bounded timeout, and a minimal
    child environment (BWS_ACCESS_TOKEN, HOME, and a fixed minimal PATH — no
    other inherited variables). No credential values ever appear in argv.
    stdout is parsed and discarded; stderr is never surfaced.
  - Fetches exactly one secret (never `secret list` of values) and validates
    the response id, key name, size, required service-account fields,
    "service_account" type, matching project, matching expected
    client_email (PG_VERTEX_SA_CLIENT_EMAIL, nonsecret, pins the exact
    existing identity — a matching project_id alone is not sufficient), and
    a fixed expected Google token endpoint before constructing SDK
    credentials.
  - Caches the resulting credentials object in memory for the life of the
    process, guarded by a lock so concurrent callers trigger at most one
    fetch. The cache is bound to the (secret id, project id) pair used on
    first fetch; a later call with a different pair is treated as
    configuration drift and rejected rather than silently re-fetched.
  - Every failure path raises VertexCredentialError with a fixed, generic
    message — never secret material, never raw subprocess output. The
    fetch/parse/validate/build sequence runs behind a single boundary: on
    failure, only the fixed message string crosses out of that boundary and
    a fresh VertexCredentialError is raised outside the except clause, so
    the original exception (and any locals held by its traceback frames)
    is not reachable from what callers see or log.

Residual limitation: this only keeps the credential out of the filesystem
and the environment block. It is still plaintext in this process's memory
for as long as the process runs, which is not a defence against a
sufficiently privileged host (e.g. ptrace, core dumps, swap).
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import threading

_SECRET_KEY = "GCP_VERTEX_SA_JSON"
_EXPECTED_TOKEN_URI = "https://oauth2.googleapis.com/token"
_REQUIRED_STRING_FIELDS = ("type", "project_id", "private_key", "client_email", "token_uri")
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_TOKEN_BYTES = 8 * 1024
_FETCH_TIMEOUT_SECONDS = 15
_BWS_BINARY = "/Users/moeedahmed/.cargo/bin/bws"
_CHILD_PATH = "/usr/bin:/bin"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_lock = threading.Lock()
_cache: dict | None = None


class VertexCredentialError(RuntimeError):
    """Raised on any failure to obtain a valid, in-memory Vertex credential.

    Messages are always fixed, generic strings — callers may log them
    directly without redaction.
    """


def _bws_binary() -> str:
    """Fixed, guarded binary path. No PATH search, no raw-binary fallback."""
    return _BWS_BINARY


def _validate_secret_id(raw: str) -> str:
    if not raw or not _UUID_RE.match(raw):
        raise VertexCredentialError(
            "PG_VERTEX_SA_SECRET_ID is not set or is not a valid secret id"
        )
    return raw


def _validate_expected_client_email(raw: str) -> str:
    if not raw or "@" not in raw:
        raise VertexCredentialError(
            "PG_VERTEX_SA_CLIENT_EMAIL is not set or is not a valid service-account email"
        )
    return raw


def _read_bws_token() -> str:
    """Read the BWS access token from the operator-configured file path.

    Never reads BWS_ACCESS_TOKEN from this process's own environment, and
    never writes the token back into it. Bounded, symlink-refusing, single
    regular file, owner-only permissions.
    """
    path = os.environ.get("PG_VERTEX_BWS_TOKEN_PATH", "")
    if not path or not os.path.isabs(path):
        raise VertexCredentialError(
            "PG_VERTEX_BWS_TOKEN_PATH is not set or is not an absolute path"
        )

    try:
        info = os.lstat(path)
    except OSError:
        raise VertexCredentialError("BWS token path is not accessible") from None

    if stat.S_ISLNK(info.st_mode):
        raise VertexCredentialError("BWS token path must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise VertexCredentialError("BWS token path is not a regular file")
    if info.st_uid != os.getuid():
        raise VertexCredentialError("BWS token path is not owned by the current user")
    if info.st_mode & 0o077:
        raise VertexCredentialError("BWS token path has unsafe group/other permissions")

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise VertexCredentialError("BWS token path could not be opened") from None
    try:
        raw = os.read(fd, _MAX_TOKEN_BYTES + 1)
    finally:
        os.close(fd)

    if len(raw) > _MAX_TOKEN_BYTES:
        raise VertexCredentialError("BWS token file is oversized")

    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise VertexCredentialError("BWS token file is not valid text") from None

    if not token:
        raise VertexCredentialError("BWS token file is empty")
    return token


def _fetch_secret_envelope(secret_id: str) -> dict:
    token = _read_bws_token()

    binary = _bws_binary()
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        raise VertexCredentialError("bws CLI is not available at the guarded path")

    child_env = {
        "BWS_ACCESS_TOKEN": token,
        "HOME": os.environ.get("HOME", ""),
        "PATH": _CHILD_PATH,
    }
    try:
        result = subprocess.run(
            [binary, "secret", "get", secret_id, "--output", "json"],
            shell=False,
            env=child_env,
            capture_output=True,
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise VertexCredentialError("bws secret fetch timed out") from None
    except OSError:
        raise VertexCredentialError("bws secret fetch could not be started") from None

    if result.returncode != 0:
        raise VertexCredentialError("bws secret fetch failed")

    stdout = result.stdout or b""
    if len(stdout) > _MAX_RESPONSE_BYTES:
        raise VertexCredentialError("bws secret fetch returned an oversized response")

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VertexCredentialError("bws secret fetch returned malformed output") from None

    if not isinstance(payload, dict):
        raise VertexCredentialError("bws secret fetch returned an unexpected response shape")

    return payload


def _extract_sa_json_value(payload: dict, secret_id: str) -> str:
    if payload.get("id") != secret_id:
        raise VertexCredentialError("bws secret fetch returned a mismatched secret id")
    if payload.get("key") != _SECRET_KEY:
        raise VertexCredentialError("bws secret fetch returned an unexpected secret key")

    value = payload.get("value")
    if not isinstance(value, str) or not value:
        raise VertexCredentialError("bws secret fetch returned no value")
    if len(value.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise VertexCredentialError("bws secret value is oversized")
    return value


def _parse_and_validate_sa_info(value: str, expected_project: str, expected_client_email: str) -> dict:
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        raise VertexCredentialError("service-account secret value is not valid JSON") from None

    if not isinstance(data, dict):
        raise VertexCredentialError("service-account secret value must be a JSON object")

    for field in _REQUIRED_STRING_FIELDS:
        field_value = data.get(field)
        if not isinstance(field_value, str) or not field_value:
            raise VertexCredentialError("service-account secret is missing a required field")

    if data.get("type") != "service_account":
        raise VertexCredentialError("service-account secret is not a service_account key")
    if data.get("project_id") != expected_project:
        raise VertexCredentialError("service-account secret project_id does not match GCP_PROJECT_ID")
    if data.get("client_email") != expected_client_email:
        raise VertexCredentialError(
            "service-account secret client_email does not match PG_VERTEX_SA_CLIENT_EMAIL"
        )
    if data.get("token_uri") != _EXPECTED_TOKEN_URI:
        raise VertexCredentialError("service-account secret token_uri is not the expected Google endpoint")

    return data


def _build_sdk_credentials(sa_info: dict):
    try:
        from google.oauth2 import service_account
    except ImportError:
        raise VertexCredentialError("google-auth is not available") from None

    try:
        return service_account.Credentials.from_service_account_info(sa_info, scopes=list(_SCOPES))
    except Exception:
        raise VertexCredentialError("failed to construct SDK credentials from the service-account secret") from None


def _fetch_and_build(secret_id: str, project_id: str, expected_client_email: str):
    """Secret-work boundary: fetch, parse, validate, and build credentials.

    May raise VertexCredentialError whose traceback frames hold secret
    material in locals (the raw envelope, the SA JSON value/dict). Callers
    must not let that exception object escape this module — see
    get_credentials, which catches it, keeps only the fixed message string,
    and raises a fresh exception outside the except clause.
    """
    payload = _fetch_secret_envelope(secret_id)
    sa_json = _extract_sa_json_value(payload, secret_id)
    sa_info = _parse_and_validate_sa_info(sa_json, project_id, expected_client_email)
    return _build_sdk_credentials(sa_info)


def get_credentials(project_id: str):
    """Return an in-memory, SDK-ready Vertex credential for `project_id`.

    Fetched from BWS at most once per process (subsequent calls for the same
    project/secret id return the cached object). Raises VertexCredentialError
    on any failure or on a project/secret id that doesn't match whatever this
    process already fetched. The raised exception is always fresh and
    carries no secret-bearing traceback frames or locals.
    """
    global _cache

    if not project_id:
        raise VertexCredentialError("no GCP project id supplied")

    secret_id = _validate_secret_id(os.environ.get("PG_VERTEX_SA_SECRET_ID", ""))
    expected_client_email = _validate_expected_client_email(
        os.environ.get("PG_VERTEX_SA_CLIENT_EMAIL", "")
    )

    with _lock:
        if _cache is not None:
            if _cache["secret_id"] != secret_id or _cache["project_id"] != project_id:
                raise VertexCredentialError(
                    "Vertex credential request does not match the already-cached secret/project binding"
                )
            return _cache["credentials"]

        failure_message = None
        credentials = None
        try:
            credentials = _fetch_and_build(secret_id, project_id, expected_client_email)
        except VertexCredentialError as exc:
            failure_message = str(exc)
        # `exc` (and the original traceback/locals it references) is
        # discarded by Python at the end of the except clause above; only
        # the plain message string survives past this point.

        if failure_message is not None:
            raise VertexCredentialError(failure_message)

        _cache = {"secret_id": secret_id, "project_id": project_id, "credentials": credentials}
        return credentials


def _reset_cache_for_tests() -> None:
    """Test-only hook to clear the process-wide cache between test cases."""
    global _cache
    with _lock:
        _cache = None
