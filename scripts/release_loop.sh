#!/usr/bin/env bash
# Deterministic release closure for Portfolio Guru.
#
# One prepared card and one approval of that card's exact SHA cover the whole
# unchanged release: exact-SHA push to main, CI Tests, deploy, runtime identity,
# the named proof, an unchanged proof resume, and bounded rollback to the
# known-good SHA that was verified live before main moved. Anything that drifts
# from the card — SHA, surface, risk, effect, proof mode, live target, rollback
# target — needs a new card and a new approval.
#
# prepare makes no remote, runtime, or user-facing change; it refreshes local
# refs and writes only its local approval card. resume never pushes. attest closes manual proof without
# touching anything external. rollback is the one bounded recovery the same
# approval already covers: it is operator-triggered, never silent, and it moves
# main forward to the known-good tree rather than rewriting history. Live
# Telegram/Kaizen guards stay authoritative and are never relaxed by a release
# approval.

set -euo pipefail

banner() { printf '\n=== %s ===\n' "$*"; }
step()   { printf '\n--- %s\n' "$*"; }
info()   { printf '    %s\n' "$*"; }
warn()   { printf '  ! %s\n' "$*"; }
err()    { printf '  ERROR: %s\n' "$*" >&2; }

final_state() {
  banner "FINAL RELEASE STATE"
  info "FINAL_RELEASE_STATE=$1"
  info "FINAL_RELEASE_GATE=$2"
  info "FINAL_RELEASE_PROOF=$3"
}

usage() {
  cat <<'EOF'
Portfolio Guru — deterministic release closure.

Usage:
  scripts/release_loop.sh --surface telegram --mode prepare --risk internal|telegram|broad \
      --effect "<one line about what changes for a doctor>" [--live-target <bot_username>]
  scripts/release_loop.sh --surface telegram --mode ship --risk <class> --approved <40hex>
  scripts/release_loop.sh --surface telegram --mode ship --risk <class> --approved <40hex> --release-sha <40hex>
  scripts/release_loop.sh --surface telegram --mode attest --risk <class> --approved <40hex> \
      --result pass|fail --note "<one line, no secrets>"
  scripts/release_loop.sh --surface telegram --mode rollback --risk <class> --approved <40hex>

Options:
  --surface <name>     Only "telegram" is wired today.
  --mode <mode>        prepare (local ref refresh/card only; no remote or product effect),
                       ship (approval gated), attest (manual proof closure),
                       rollback (bounded operator-triggered recovery to the card's
                       known-good SHA; never runs itself, never rewrites history).
  --risk <class>       internal, telegram, or broad. Required for every mode.
  --effect <line>      prepare only: one plain line naming what this release changes.
  --live-target <name> prepare only: exact bot username for telegram-risk live proof.
  --release-sha <sha>  Resume proof only for an already-pushed exact full SHA.
  --approved <40hex>   Approval of one prepared card, named by its exact full SHA.
  --result pass|fail   attest only: the operator's verdict on the named manual proof.
  --note <line>        attest only: one non-sensitive line recording what was observed.
  --no-dogfood         Legacy broad-proof skip; always leaves proof pending.
  -h, --help           Show this help.

Approval for ship/resume/attest/rollback (one of):
  --approved <40hex>
  RELEASE_APPROVED=<40hex>
A dated or bare approval no longer covers a release: an approval names one SHA.
Rollback reuses that same approval; the card already names the known-good SHA it
rolls back to, so this bounded recovery needs no second approval. Rerun the exact
same rollback command to resume it; --release-sha is not used by rollback.

Exit codes:
  0  ready/live/rolled-back
  1  blocked gate or completed CI/deploy failure
  2  approval missing, malformed, or not covering this card/SHA
  3  git/reconciliation/resume refusal
  4  retryable proof pending (missing/running/timeout/runtime/live proof)
  64 usage error
EOF
}

SURFACE="telegram"
MODE=""
RISK=""
RISK_SUPPLIED=0
APPROVED_VALUE=""
NO_DOGFOOD=0
RELEASE_SHA=""
EFFECT=""
LIVE_TARGET=""
RESULT=""
NOTE=""
PROOF_TIMEOUT="${RELEASE_LOOP_PROOF_TIMEOUT:-900}"
PROOF_INTERVAL="${RELEASE_LOOP_PROOF_INTERVAL:-5}"
GITHUB_REPOSITORY="${RELEASE_LOOP_GITHUB_REPOSITORY:-moeedahmed/portfolio-guru}"

# Fixed throwaway values for the offline children only, applied per command with
# `env` and never exported. Same non-secret Fernet key as the CI Tests job: it
# encrypts synthetic test data and nothing else. Deploy, runtime and live proof
# children are launched without them, so nothing downstream can inherit a fake
# credential and quietly "pass".
OFFLINE_ENV=(
  FERNET_SECRET_KEY=5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ=
  TELEGRAM_BOT_TOKEN=fake
  GOOGLE_API_KEY=fake
)

LIVE_APPROVAL_VALUE="portfolio-guru-live-qa-approved"
DEFAULT_LIVE_ALLOWLIST="portfolio_guru_bot"

# Rollback is covered by the same approval, but it only ever happens because the
# operator ran the printed command. Nothing here rolls back on its own; the
# deploy script's own health-check rollback is separate and unchanged.
ROLLBACK_MODE_VALUE="operator-triggered"

CARD_EXCLUSIONS="supervisor submission,credential or secret change,schema or data migration,\
pricing or spend change,any new recipient or public announcement,history rewrite or force push,\
any release SHA other than the one named on this card"

need_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    err "$option requires a value."
    exit 64
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --surface) need_value "$1" "${2:-}"; SURFACE="$2"; shift 2 ;;
    --surface=*) SURFACE="${1#*=}"; [[ -n "$SURFACE" ]] || { err "--surface requires a value."; exit 64; }; shift ;;
    --mode) need_value "$1" "${2:-}"; MODE="$2"; shift 2 ;;
    --mode=*) MODE="${1#*=}"; [[ -n "$MODE" ]] || { err "--mode requires a value."; exit 64; }; shift ;;
    --risk) need_value "$1" "${2:-}"; RISK="$2"; RISK_SUPPLIED=1; shift 2 ;;
    --risk=*) RISK="${1#*=}"; [[ -n "$RISK" ]] || { err "--risk requires a value."; exit 64; }; RISK_SUPPLIED=1; shift ;;
    --release-sha) need_value "$1" "${2:-}"; RELEASE_SHA="$2"; shift 2 ;;
    --release-sha=*) RELEASE_SHA="${1#*=}"; [[ -n "$RELEASE_SHA" ]] || { err "--release-sha requires a value."; exit 64; }; shift ;;
    --approved) need_value "$1" "${2:-}"; APPROVED_VALUE="$2"; shift 2 ;;
    --approved=*) APPROVED_VALUE="${1#*=}"; [[ -n "$APPROVED_VALUE" ]] || { err "--approved requires a value."; exit 64; }; shift ;;
    --effect) need_value "$1" "${2:-}"; EFFECT="$2"; shift 2 ;;
    --effect=*) EFFECT="${1#*=}"; [[ -n "$EFFECT" ]] || { err "--effect requires a value."; exit 64; }; shift ;;
    --live-target) need_value "$1" "${2:-}"; LIVE_TARGET="$2"; shift 2 ;;
    --live-target=*) LIVE_TARGET="${1#*=}"; [[ -n "$LIVE_TARGET" ]] || { err "--live-target requires a value."; exit 64; }; shift ;;
    --result) need_value "$1" "${2:-}"; RESULT="$2"; shift 2 ;;
    --result=*) RESULT="${1#*=}"; [[ -n "$RESULT" ]] || { err "--result requires a value."; exit 64; }; shift ;;
    --note) need_value "$1" "${2:-}"; NOTE="$2"; shift 2 ;;
    --note=*) NOTE="${1#*=}"; [[ -n "$NOTE" ]] || { err "--note requires a value."; exit 64; }; shift ;;
    --no-dogfood) NO_DOGFOOD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "Unknown argument: $1"; echo; usage; exit 64 ;;
  esac
done

[[ -n "$MODE" ]] || { err "Missing --mode (prepare|ship|attest|rollback)."; echo; usage; exit 64; }
case "$MODE" in
  prepare|ship|attest|rollback) ;;
  *) err "Invalid --mode: $MODE (expected prepare|ship|attest|rollback)."; exit 64 ;;
esac
[[ "$SURFACE" == telegram ]] || { err "Unsupported --surface: $SURFACE. Only 'telegram' is wired in this slice."; exit 64; }
if [[ "$RISK_SUPPLIED" == 1 && "$RISK" != internal && "$RISK" != telegram && "$RISK" != broad ]]; then
  err "Invalid --risk: $RISK (expected internal|telegram|broad)."
  exit 64
fi
if [[ "$RISK_SUPPLIED" != 1 ]]; then
  err "--risk is required for $MODE; classification must be explicit."
  exit 64
fi
if [[ "$MODE" == prepare ]]; then
  [[ -n "$EFFECT" ]] || { err "--effect is required for prepare: one plain line naming what this release changes."; exit 64; }
  [[ "$EFFECT" != *$'\n'* ]] || { err "--effect must be a single line."; exit 64; }
  if [[ "$RISK" == telegram && -z "$LIVE_TARGET" ]]; then
    err "--live-target is required for telegram risk: name the exact bot the live proof will touch."
    exit 64
  fi
fi
if [[ "$MODE" == rollback && -n "$RELEASE_SHA" ]]; then
  err "--release-sha is not used by rollback; rerun the exact same rollback command to resume it."
  exit 64
fi
if [[ "$MODE" == attest ]]; then
  [[ "$RESULT" == pass || "$RESULT" == fail ]] || { err "--result must be exactly pass or fail."; exit 64; }
  [[ -n "$NOTE" ]] || { err "--note is required for attest: one non-sensitive line recording what was observed."; exit 64; }
  [[ "$NOTE" != *$'\n'* ]] || { err "--note must be a single line."; exit 64; }
fi
[[ -z "$RELEASE_SHA" || "$RELEASE_SHA" =~ ^[0-9a-fA-F]{40}$ ]] || { err "--release-sha must be a full 40-character hexadecimal SHA."; exit 64; }
[[ "$PROOF_TIMEOUT" =~ ^[0-9]+$ && "$PROOF_INTERVAL" =~ ^[0-9]+$ ]] || { err "Proof timeout/interval must be base-10 whole seconds."; exit 64; }
if (( 10#$PROOF_TIMEOUT == 0 || 10#$PROOF_INTERVAL == 0 )); then
  [[ "${RELEASE_LOOP_TEST_MODE:-0}" == 1 ]] || { err "Proof timeout and interval must be positive outside explicit test mode."; exit 64; }
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || { err "Not inside a git repository."; exit 64; }
cd "$ROOT"
branch="$(git branch --show-current)"

RELEASE_DIR="$ROOT/.release"
card_path()        { printf '%s/%s.card.json' "$RELEASE_DIR" "$1"; }
attestation_path() { printf '%s/%s.attestation.json' "$RELEASE_DIR" "$1"; }
rollback_path()    { printf '%s/%s.rollback.json' "$RELEASE_DIR" "$1"; }
card_tool()        { python3 "$ROOT/scripts/release_card.py" "$@"; }
utc_now()          { date -u +%Y-%m-%dT%H:%M:%SZ; }

tracked_tree_is_clean() { [[ -z "$(git status --porcelain --untracked-files=no)" ]]; }
origin_main_ancestor() { git merge-base --is-ancestor origin/main HEAD 2>/dev/null; }
ahead_behind() {
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    git rev-list --left-right --count origin/main...HEAD
  else
    echo "? ?"
  fi
}
fetch_main_required() {
  git fetch origin main || { err "Required fetch of origin/main failed."; return 1; }
}

run_check() {
  local label="$1"; shift
  step "$label"
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then info "PASS: $label"; return 0; fi
  warn "FAIL (exit $rc): $label"
  return 1
}

# Offline gates run with fixed throwaway credentials scoped to that one child.
run_offline_check() {
  local label="$1"; shift
  run_check "$label" env "${OFFLINE_ENV[@]}" "$@"
}

# Automated readiness is a semantic question, not a presence check: the session,
# API id/hash, the configured bot username and the allowlist must all agree on
# the one target the card names. Anything less is manual proof.
telegram_live_ready() {
  local target="$1" username allowlist entry
  [[ -n "$target" ]] || return 1
  [[ -n "${TELETHON_SESSION:-}" ]] || return 1
  [[ -n "${TELEGRAM_API_ID:-${TELETHON_API_ID:-}}" ]] || return 1
  [[ -n "${TELEGRAM_API_HASH:-${TELETHON_API_HASH:-}}" ]] || return 1
  username="${TELEGRAM_BOT_USERNAME:-}"
  username="${username#@}"
  [[ -n "$username" && "$username" == "$target" ]] || return 1
  allowlist="${TELEGRAM_LIVE_ALLOWED_BOTS:-$DEFAULT_LIVE_ALLOWLIST}"
  local saved_ifs="$IFS"
  IFS=','
  for entry in $allowlist; do
    entry="${entry//[[:space:]]/}"
    entry="${entry#@}"
    if [[ -n "$entry" && "$entry" == "$target" ]]; then IFS="$saved_ifs"; return 0; fi
  done
  IFS="$saved_ifs"
  return 1
}

resolved_proof_mode() {
  case "$RISK" in
    internal) printf 'automated\n' ;;
    telegram) if telegram_live_ready "$LIVE_TARGET"; then printf 'automated\n'; else printf 'manual\n'; fi ;;
    broad)    printf 'manual\n' ;;
  esac
}

mode_prepare() {
  banner "PREPARE — one release card (safe, non-live)"
  info "Surface: $SURFACE. Risk: $RISK. This run never pushes, deploys, or restarts."
  step "Git state"
  info "Repo: $ROOT"
  info "Branch: ${branch:-<detached>}"
  info "Commit: $(git rev-parse --short HEAD)"
  if tracked_tree_is_clean; then
    info "Tracked tree: clean"
  else
    warn "Tracked tree: UNCOMMITTED changes present"
  fi
  info "Untracked files: $(git ls-files --others --exclude-standard | wc -l | tr -d ' ') (not shipped)"
  local reasons=() ahead
  fetch_main_required || reasons+=("current origin/main could not be fetched")
  run_offline_check "Offline preflight" bash "$ROOT/scripts/preflight.sh" || reasons+=("offline preflight failed")
  run_offline_check "Telegram offline QA" bash "$ROOT/scripts/telegram_qa_offline.sh" || reasons+=("Telegram offline QA failed")
  [[ "$branch" != main && -n "$branch" ]] || reasons+=("not on a feature branch")
  tracked_tree_is_clean || reasons+=("uncommitted tracked changes")
  read -r _ ahead < <(ahead_behind)
  if [[ "$ahead" == "?" ]]; then reasons+=("origin/main unknown")
  elif ! origin_main_ancestor; then reasons+=("branch not fast-forwardable onto origin/main")
  elif [[ "$ahead" == 0 ]]; then reasons+=("nothing ahead of origin/main")
  fi

  if [[ ${#reasons[@]} == 0 ]]; then
    verify_known_good || reasons+=("live runtime did not verify against current origin/main")
  fi

  banner "READINESS"
  if [[ ${#reasons[@]} != 0 ]]; then
    warn "BLOCKED — resolve before preparing a card:"
    local reason; for reason in "${reasons[@]}"; do printf '      - %s\n' "$reason"; done
    info "No card was written; nothing to approve yet."
    return 1
  fi

  local sha proof_mode card
  sha="$(git rev-parse HEAD)"
  proof_mode="$(resolved_proof_mode)"
  card="$(card_path "$sha")"
  if ! card_tool write \
        --path "$card" \
        --sha "$sha" \
        --surface "$SURFACE" \
        --risk "$RISK" \
        --effect "$EFFECT" \
        --proof-mode "$proof_mode" \
        --live-target "$LIVE_TARGET" \
        --known-good-sha "$KNOWN_GOOD_SHA" \
        --rollback-mode "$ROLLBACK_MODE_VALUE" \
        --exclusions "$CARD_EXCLUSIONS" \
        --created-at "$(utc_now)"; then
    err "Refused to write a release card."
    return 1
  fi

  banner "RELEASE CARD"
  card_tool render --path "$card" \
    --ship-command "scripts/release_loop.sh --surface $SURFACE --mode ship --risk $RISK --approved $sha" \
    --rollback-command "$(rollback_command "$sha")"
  if [[ "$RISK" == telegram && "$proof_mode" == manual ]]; then
    info ""
    info "Telegram live proof is manual on this card: Telethon session/API id/hash,"
    info "TELEGRAM_BOT_USERNAME and the live allowlist do not all name @$LIVE_TARGET."
    info "After shipping, close it with --mode attest. This card can never send automatically."
  fi
  info ""
  info "Approving the SHA above covers that whole release once. Nothing else."
  return 0
}

APPROVAL_SHA=""
require_approval() {
  local supplied="${APPROVED_VALUE:-${RELEASE_APPROVED:-}}"
  if [[ -z "$supplied" ]]; then
    err "$MODE refused — explicit approval required: --approved <40-hex release SHA>."
    final_state "release-ready" "prepare a card, then approve its exact SHA" "no mutating action taken"
    exit 2
  fi
  supplied="$(printf '%s' "$supplied" | tr 'A-F' 'a-f')"
  if [[ ! "$supplied" =~ ^[0-9a-f]{40}$ ]]; then
    err "Approval must be the exact full 40-character SHA printed on the prepared card."
    err "Dated or bare approvals are stale under the one-card standard; each approval names one SHA."
    final_state "release-ready" "approve the exact SHA printed by --mode prepare" "no mutating action taken"
    exit 2
  fi
  APPROVAL_SHA="$supplied"
  info "Approval: covers the card for exact SHA $APPROVAL_SHA."
}

refuse_approval_scope() {
  err "$1"
  err "A changed SHA, surface, risk, effect, proof mode, live target or rollback target needs a new card and a new approval."
  final_state blocked "run --mode prepare and approve the printed SHA" "no mutation"
  exit 2
}

CARD_FILE=""
# head_check is "require-head" everywhere except rollback, whose whole job is to
# move HEAD off the released SHA; rollback checks HEAD itself against both the
# released SHA and its own recorded rollback commit.
load_and_verify_card() {
  local sha="$1" head_check="${2:-require-head}" head card_env
  CARD_FILE="$(card_path "$sha")"
  [[ -f "$CARD_FILE" ]] || refuse_approval_scope "No release card for $sha; this approval names a card that was never prepared."
  card_env="$(card_tool export --path "$CARD_FILE")" || refuse_approval_scope "Release card for $sha did not validate."
  eval "$card_env"
  [[ "${CARD_SHA:-}" == "$sha" ]] || refuse_approval_scope "Card records SHA ${CARD_SHA:-<none>} but the approval names $sha."
  [[ "${CARD_SURFACE:-}" == "$SURFACE" ]] || refuse_approval_scope "Card surface ${CARD_SURFACE:-<none>} does not equal --surface $SURFACE."
  [[ "${CARD_RISK:-}" == "$RISK" ]] || refuse_approval_scope "Card risk ${CARD_RISK:-<none>} does not equal --risk $RISK."
  if [[ "$head_check" == require-head ]]; then
    head="$(git rev-parse HEAD)"
    [[ "$head" == "$sha" ]] || refuse_approval_scope "Approval names $sha but HEAD is $head."
  fi
  info "Card: $CARD_FILE"
  info "Card effect: $CARD_EFFECT"
  info "Card proof mode: $CARD_PROOF_MODE${CARD_LIVE_TARGET:+ (live target @$CARD_LIVE_TARGET)}"
}

PUSHED_SHA=""
KNOWN_GOOD_SHA=""

# The rollback target is part of the approved card. Prepare proves and freezes
# it before asking for approval; ship proves the same SHA again immediately
# before main moves. The card is never amended after approval.
verify_known_good() {
  local expected="${1:-}" base output
  step "Known-good runtime verification (before approval/main move)"
  base="$(git rev-parse origin/main)"
  [[ "$base" =~ ^[0-9a-f]{40}$ ]] || { err "Cannot read a full origin/main SHA to roll back to."; return 1; }
  if [[ -n "$expected" && "$base" != "$expected" ]]; then
    err "Current origin/main $base no longer equals the approved rollback target $expected."
    return 1
  fi
  [[ -x "$ROOT/scripts/verify_live_runtime.py" ]] || { err "Runtime verifier unavailable; no rollback target can be established."; return 1; }
  if ! output="$("$ROOT/scripts/verify_live_runtime.py" --expected-sha "$base" 2>&1)"; then
    err "Live runtime does not verify against current origin/main $base: $output"
    return 1
  fi
  if [[ "$output" != *"expected_sha=$base"* || "$output" != *"checkout_sha=$base"* || "$output" != *"runtime_sha=$base"* ]]; then
    err "Runtime verifier omitted exact stable SHA fields for the known-good check."
    return 1
  fi
  KNOWN_GOOD_SHA="$base"
  info "KNOWN_GOOD_SHA=$KNOWN_GOOD_SHA (verified live; card remains immutable)"
}

# Reconcile without checking out main.
#
# This used to `git checkout main`, fast-forward it twice, push, then switch
# back. That fails outright once main is held by the live deployment worktree,
# and in a shared checkout it was itself a hazard: switching branches mid-release
# yanks the tree out from under anything else working there.
#
# Pushing HEAD straight at main keeps every guarantee. The ancestry check
# refuses anything that is not a fast-forward, a non-force push makes the server
# refuse it too, and origin/main is re-read afterwards and compared to the exact
# approved SHA. Nothing is trusted that was not verified after the fact. The push
# of the exact SHA is also what preserves the commit remotely; no second backup
# push is taken.
ship_reconcile_and_push() {
  step "Reconcile $branch -> main and push exact SHA"
  local candidate_sha
  candidate_sha="$(git rev-parse HEAD)"
  [[ "$candidate_sha" == "$APPROVAL_SHA" ]] || { err "HEAD moved away from the approved SHA; refusing to push."; return 1; }
  if ! git merge-base --is-ancestor origin/main HEAD; then
    err "Release branch is not a fast-forward of origin/main; rebase before shipping."
    return 1
  fi
  if ! git push origin "HEAD:refs/heads/main"; then
    err "Push of exact release SHA failed."
    return 1
  fi
  fetch_main_required || return 1
  local local_sha remote_sha
  local_sha="$(git rev-parse HEAD)"
  remote_sha="$(git rev-parse origin/main)"
  [[ "$local_sha" == "$candidate_sha" && "$remote_sha" == "$candidate_sha" ]] || { err "Post-push HEAD/origin/main exact-SHA proof failed."; return 1; }
  PUSHED_SHA="$candidate_sha"
  info "PUSHED_SHA=$PUSHED_SHA"
}

prepare_resume() {
  step "Validate exact-SHA proof resume"
  [[ "$RELEASE_SHA" == "$APPROVAL_SHA" ]] || { err "Resume SHA $RELEASE_SHA is not the approved SHA $APPROVAL_SHA."; return 1; }
  fetch_main_required || return 1
  local head remote
  head="$(git rev-parse HEAD)"
  remote="$(git rev-parse origin/main)"
  if [[ "$head" != "$RELEASE_SHA" || "$remote" != "$RELEASE_SHA" ]]; then
    err "Resume requires HEAD == origin/main == --release-sha; got HEAD=$head origin/main=$remote."
    return 1
  fi
  PUSHED_SHA="$RELEASE_SHA"
  KNOWN_GOOD_SHA="${CARD_KNOWN_GOOD_SHA:-}"
  info "RESUMING_RELEASE_SHA=$PUSHED_SHA"
  info "Proof-only resume: no checkout, merge, or push will run."
}

workflow_runs_json() {
  local workflow_name="$1" workflow_file="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh run list --workflow "$workflow_name" --branch main --limit 100 \
      --json databaseId,headSha,status,conclusion,event,createdAt,startedAt,updatedAt
    return
  fi
  command -v curl >/dev/null 2>&1 || return 1
  curl --fail --silent --show-error --max-time 20 \
    "https://api.github.com/repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/runs?branch=main&head_sha=$PUSHED_SHA&per_page=100"
}

select_provenance_run() {
  local sha="$1" expected_event="$2" not_before="${3:-}"
  python3 -c '
import datetime as dt, json, sys
sha, expected_event, not_before = sys.argv[1:4]
def value(run, camel, snake): return run.get(camel) or run.get(snake) or ""
def instant(raw):
    if not isinstance(raw, str) or not raw: return None
    try: parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError): return None
    return parsed if parsed.tzinfo is not None else None
def run_id(run):
    raw = value(run, "databaseId", "id")
    if isinstance(raw, bool): return None
    if isinstance(raw, int): return raw if raw > 0 else None
    if isinstance(raw, str) and raw.isdigit() and int(raw) > 0: return int(raw)
    return None
try: payload = json.load(sys.stdin)
except Exception: raise SystemExit(2)
if isinstance(payload, list): runs = payload
elif isinstance(payload, dict): runs = payload.get("workflow_runs", [])
else: runs = []
if not isinstance(runs, list): raise SystemExit(2)
boundary = instant(not_before) if not_before else None
if not_before and boundary is None: raise SystemExit(2)
candidates = []
for run in runs:
    if not isinstance(run, dict): continue
    if value(run, "headSha", "head_sha") != sha or run.get("event") != expected_event: continue
    identifier = run_id(run)
    created = value(run, "createdAt", "created_at")
    started = value(run, "startedAt", "run_started_at")
    updated = value(run, "updatedAt", "updated_at")
    created_at, started_at, updated_at = instant(created), instant(started), instant(updated)
    if identifier is None or None in (created_at, started_at, updated_at): continue
    if boundary is not None and (created_at < boundary or started_at < boundary): continue
    candidates.append((started_at, run, identifier))
if candidates:
    _, run, identifier = max(candidates, key=lambda item: item[0])
    fields = [str(identifier), run.get("status") or "unknown",
              run.get("conclusion") or "", value(run, "updatedAt", "updated_at"),
              value(run, "createdAt", "created_at"), value(run, "startedAt", "run_started_at"), run.get("event") or ""]
    print("|".join(fields))
' "$sha" "$expected_event" "$not_before"
}

WORKFLOW_UPDATED_AT=""
wait_for_exact_workflow() {
  local workflow_name="$1" workflow_file="$2" sha="$3" expected_event="$4" not_before="${5:-}"
  local deadline json match run_id status conclusion updated _created _started event now
  deadline=$(( $(date +%s) + 10#$PROOF_TIMEOUT ))
  step "$workflow_name provenance proof for exact SHA"
  while true; do
    if json="$(workflow_runs_json "$workflow_name" "$workflow_file" 2>/dev/null)"; then
      match="$(printf '%s' "$json" | select_provenance_run "$sha" "$expected_event" "$not_before" 2>/dev/null || true)"
      if [[ -n "$match" ]]; then
        IFS='|' read -r run_id status conclusion updated _created _started event <<< "$match"
        info "MATCH: workflow=$workflow_name run_id=$run_id head_sha=$sha event=$event status=$status conclusion=${conclusion:-pending}"
        if [[ "$status" == completed ]]; then
          if [[ "$conclusion" == success ]]; then
            WORKFLOW_UPDATED_AT="$updated"
            info "PASS: $workflow_name succeeded with required provenance."
            return 0
          fi
          warn "$workflow_name completed for exact SHA with conclusion=${conclusion:-unknown}."
          return 1
        fi
      fi
    fi
    now="$(date +%s)"
    if (( now >= deadline )); then
      warn "No $workflow_name run for exact SHA $sha with event=$expected_event completed successfully within ${PROOF_TIMEOUT}s."
      return 4
    fi
    sleep "$PROOF_INTERVAL"
  done
}

prove_exact_live_runtime() {
  local sha="$1" output
  step "Mac Mini runtime proof for exact SHA"
  [[ "$(git rev-parse HEAD)" == "$sha" && "$(git rev-parse origin/main)" == "$sha" ]] || { warn "Local HEAD/origin/main no longer equal release SHA."; return 4; }
  command -v launchctl >/dev/null 2>&1 || { warn "Local launchd access unavailable."; return 4; }
  launchctl print "gui/$(id -u)/com.portfolioguru.bot" >/dev/null 2>&1 || { warn "Portfolio Guru service is not locally accessible."; return 4; }
  [[ -x "$ROOT/scripts/verify_live_runtime.py" ]] || { warn "Runtime verifier unavailable."; return 4; }
  if ! output="$("$ROOT/scripts/verify_live_runtime.py" --expected-sha "$sha" 2>&1)"; then warn "Runtime verification failed: $output"; return 4; fi
  [[ "$output" == *"expected_sha=$sha"* && "$output" == *"checkout_sha=$sha"* && "$output" == *"runtime_sha=$sha"* ]] || { warn "Runtime verifier omitted exact stable SHA fields."; return 4; }
  info "$output"
}

# Quiet form of the runtime identity check, for questions rather than gates:
# "is the Mac Mini actually running this SHA right now?"
RUNTIME_REPORT=""
runtime_matches() {
  local sha="$1" output
  RUNTIME_REPORT=""
  [[ -x "$ROOT/scripts/verify_live_runtime.py" ]] || return 1
  output="$("$ROOT/scripts/verify_live_runtime.py" --expected-sha "$sha" 2>&1)" || return 1
  [[ "$output" == *"expected_sha=$sha"* && "$output" == *"checkout_sha=$sha"* && "$output" == *"runtime_sha=$sha"* ]] || return 1
  RUNTIME_REPORT="$output"
}

attest_command() {
  printf "scripts/release_loop.sh --surface %s --mode attest --risk %s --approved %s --result pass|fail --note '<one line>'" \
    "$SURFACE" "$RISK" "$PUSHED_SHA"
}

prove_risk_journey() {
  step "Risk-based live proof ($RISK)"
  case "$RISK" in
    internal) info "PASS: internal risk needs no manual journey."; return 0 ;;
    telegram)
      if [[ "${CARD_PROOF_MODE:-}" != automated ]]; then
        warn "Manual proof required: this card was prepared as manual, so the loop will not send live messages."
        warn "Close it with: $(attest_command)"
        return 4
      fi
      if ! telegram_live_ready "${CARD_LIVE_TARGET:-}"; then
        warn "Automated readiness for @${CARD_LIVE_TARGET:-<none>} is no longer complete (session/API id/hash, TELEGRAM_BOT_USERNAME, allowlist)."
        warn "Refusing to run the live child. Restore readiness and resume, or prepare a new manual card."
        return 4
      fi
      env TELEGRAM_LIVE_APPROVED="$LIVE_APPROVAL_VALUE" \
          TELEGRAM_BOT_USERNAME="$CARD_LIVE_TARGET" \
          RUN_LIVE_TELEGRAM=1 \
          REQUIRE_TELEGRAM_LIVE=1 \
          bash "$ROOT/scripts/telegram_bot_qa.sh" --focused-release || return 1
      ;;
    broad)
      [[ "$NO_DOGFOOD" != 1 ]] || { warn "Broad proof skipped; proof remains pending."; return 4; }
      if [[ ! -t 0 || ! -t 1 ]]; then
        warn "Broad risk requires the interactive strict checklist; no TTY available."
        warn "Run it at a terminal, or record the operator verdict with: $(attest_command)"
        return 4
      fi
      bash "$ROOT/scripts/dogfood_smoke.sh" --strict-release || return 1
      ;;
  esac
}

rollback_command() {
  printf 'scripts/release_loop.sh --surface %s --mode rollback --risk %s --approved %s' \
    "$SURFACE" "$RISK" "$1"
}

rollback_notice() {
  if [[ -z "$KNOWN_GOOD_SHA" ]]; then
    warn "No verified known-good SHA was recorded; do not guess a rollback target."
    return 0
  fi
  warn "$PUSHED_SHA stays live until a targeted rollback is actually run. Nothing has been reverted."
  warn "Bounded rollback covered by this card: redeploy the verified known-good SHA $KNOWN_GOOD_SHA."
  warn "Rollback is operator-triggered, never silent. It runs only when you run this exact command:"
  info "ROLLBACK_COMMAND=$(rollback_command "$PUSHED_SHA")"
  warn "That makes one normal forward commit onto the known-good tree. This loop will"
  warn "never rewrite history to undo a release."
}

resume_command() {
  printf 'scripts/release_loop.sh --surface %s --mode ship --risk %s --release-sha %s --approved %s' \
    "$SURFACE" "$RISK" "$PUSHED_SHA" "$APPROVAL_SHA"
}

mode_ship() {
  banner "SHIP — gated release closure ($SURFACE, risk=$RISK)"
  require_approval
  load_and_verify_card "$APPROVAL_SHA"
  [[ "$branch" != main && -n "$branch" ]] || { err "SHIP refused — use the preserved feature branch."; final_state blocked "checkout feature branch" "no mutation"; exit 3; }
  tracked_tree_is_clean || { err "SHIP refused — uncommitted tracked changes present."; final_state blocked "commit or revert changes" "no mutation"; exit 3; }

  if [[ -n "$RELEASE_SHA" ]]; then
    prepare_resume || { final_state blocked "restore exact resume SHA state" "no push attempted"; exit 3; }
  else
    fetch_main_required || { final_state blocked "repair origin fetch" "no push attempted"; exit 3; }
    local ahead
    read -r _ ahead < <(ahead_behind)
    [[ "$ahead" != "?" && "$ahead" != 0 ]] || { err "SHIP refused — no release commit ahead of origin/main."; final_state blocked "create release commit" "no mutation"; exit 3; }
    origin_main_ancestor || { err "SHIP refused — origin/main is not an ancestor of HEAD."; final_state blocked "rebase onto origin/main" "no mutation"; exit 3; }
    run_offline_check "Offline preflight" bash "$ROOT/scripts/preflight.sh" || { final_state blocked "fix offline preflight" "no push"; exit 1; }
    run_offline_check "Telegram offline QA" bash "$ROOT/scripts/telegram_qa_offline.sh" || { final_state blocked "fix Telegram offline QA" "no push"; exit 1; }
    verify_known_good "$CARD_KNOWN_GOOD_SHA" || { final_state blocked "prepare a new card from the current verified runtime" "no mutation"; exit 1; }
    ship_reconcile_and_push || { final_state blocked "repair reconcile/push and confirm original branch" "push stage failed closed"; exit 1; }
  fi

  local tests_rc deploy_rc runtime_rc risk_rc
  set +e
  wait_for_exact_workflow Tests test.yml "$PUSHED_SHA" push; tests_rc=$?
  set -e
  if [[ $tests_rc == 1 ]]; then final_state blocked "fix failed Tests run" "pushed_sha=$PUSHED_SHA"; exit 1; fi
  if [[ $tests_rc == 4 ]]; then
    banner "SHIP proof pending"; info "RESUME_COMMAND=$(resume_command)"
    final_state proof-pending "run RESUME_COMMAND after Tests progresses" "pushed_sha=$PUSHED_SHA tests=pending"
    exit 4
  fi
  local tests_completed="$WORKFLOW_UPDATED_AT"
  if [[ -z "$tests_completed" ]]; then
    banner "SHIP proof pending"; info "RESUME_COMMAND=$(resume_command)"
    final_state proof-pending "run RESUME_COMMAND after Tests provides a valid completion boundary" "pushed_sha=$PUSHED_SHA tests=metadata-pending"
    exit 4
  fi

  set +e
  wait_for_exact_workflow "Deploy Mac Mini" deploy-mac.yml "$PUSHED_SHA" workflow_run "$tests_completed"; deploy_rc=$?
  set -e
  if [[ $deploy_rc == 1 ]]; then final_state blocked "fix failed deploy run" "pushed_sha=$PUSHED_SHA tests=1 deploy=failed"; exit 1; fi
  if [[ $deploy_rc == 4 ]]; then
    banner "SHIP proof pending"; info "RESUME_COMMAND=$(resume_command)"
    final_state proof-pending "run RESUME_COMMAND after deploy progresses" "pushed_sha=$PUSHED_SHA tests=1 deploy=pending"
    exit 4
  fi

  set +e; prove_exact_live_runtime "$PUSHED_SHA"; runtime_rc=$?; set -e
  if [[ $runtime_rc != 0 ]]; then
    banner "SHIP proof pending"; info "RESUME_COMMAND=$(resume_command)"
    final_state proof-pending "run RESUME_COMMAND where runtime is accessible" "pushed_sha=$PUSHED_SHA tests=1 deploy=1 runtime=pending"
    exit 4
  fi
  set +e; prove_risk_journey; risk_rc=$?; set -e
  if [[ $risk_rc == 1 ]]; then
    banner "SHIP blocked on live proof"
    rollback_notice
    final_state blocked "fix failed $RISK proof" "pushed_sha=$PUSHED_SHA tests=1 deploy=1 runtime=1"
    exit 1
  fi
  if [[ $risk_rc != 0 ]]; then
    banner "SHIP proof pending"; info "RESUME_COMMAND=$(resume_command)"
    final_state proof-pending "run RESUME_COMMAND with protected $RISK proof available" "pushed_sha=$PUSHED_SHA tests=1 deploy=1 runtime=1 risk=pending"
    exit 4
  fi
  banner "SHIP complete"
  final_state live none "pushed_sha=$PUSHED_SHA tests=1 deploy=1 runtime=1 risk=$RISK:1"
}

# Manual proof closure. Records what the operator observed; never claims the
# automated journey ran, and never touches anything outside .release/.
mode_attest() {
  banner "ATTEST — manual proof closure ($SURFACE, risk=$RISK)"
  require_approval
  load_and_verify_card "$APPROVAL_SHA"

  if [[ "$RISK" == internal ]]; then
    err "Internal risk has no manual journey to attest; its proof is CI, deploy and runtime identity."
    final_state blocked "close internal risk with ship/resume" "no mutation"
    exit 3
  fi
  if [[ "${CARD_PROOF_MODE:-}" != manual ]]; then
    err "This card was prepared for automated proof. Its proof mode cannot be changed after approval."
    err "Restore automated readiness and run ship/resume, or prepare and approve a new manual card."
    final_state blocked "run ship/resume or approve a newly prepared manual card" "no mutation"
    exit 3
  fi

  fetch_main_required || { final_state blocked "repair origin fetch" "no mutation"; exit 3; }
  local head remote
  head="$(git rev-parse HEAD)"
  remote="$(git rev-parse origin/main)"
  if [[ "$head" != "$APPROVAL_SHA" || "$remote" != "$APPROVAL_SHA" ]]; then
    err "Attest requires HEAD == origin/main == approved SHA; got HEAD=$head origin/main=$remote."
    final_state blocked "restore exact release SHA state" "no mutation"
    exit 3
  fi
  PUSHED_SHA="$APPROVAL_SHA"
  KNOWN_GOOD_SHA="${CARD_KNOWN_GOOD_SHA:-}"

  local runtime_rc
  set +e; prove_exact_live_runtime "$APPROVAL_SHA"; runtime_rc=$?; set -e
  if [[ $runtime_rc != 0 ]]; then
    final_state proof-pending "attest where the Mac Mini runtime is accessible" "release_sha=$APPROVAL_SHA runtime=pending"
    exit 4
  fi

  step "Record operator attestation"
  if ! card_tool attest \
        --card "$CARD_FILE" \
        --path "$(attestation_path "$APPROVAL_SHA")" \
        --sha "$APPROVAL_SHA" \
        --result "$RESULT" \
        --note "$NOTE" \
        --attested-at "$(utc_now)"; then
    err "Attestation refused; nothing was recorded."
    final_state blocked "supply a single-line, non-sensitive note" "no mutation"
    exit 3
  fi
  info "Recorded: $(attestation_path "$APPROVAL_SHA")"

  if [[ "$RESULT" == pass ]]; then
    banner "ATTEST complete"
    info "manual proof attested by operator"
    final_state live none "release_sha=$APPROVAL_SHA proof=manual-operator-attestation result=pass"
    return 0
  fi
  banner "ATTEST recorded a failure"
  info "manual proof attested by operator"
  rollback_notice
  final_state blocked "roll back to the known-good SHA or ship a fix" "release_sha=$APPROVAL_SHA proof=manual-operator-attestation result=fail"
  exit 1
}

# --- bounded, operator-triggered rollback ------------------------------------
#
# The card that authorised the release already names both ends of this: the
# released SHA R it approved, and the known-good SHA K it verified live before
# main moved. Rolling R back to K is inside that approval envelope rather than a
# new decision, so it needs no second approval — and it is never automatic
# either. It happens only because the operator ran the exact printed command.
# (The deploy script's own post-deploy health rollback is a separate mechanism
# and is not touched by any of this.)
#
# The recovery is one normal forward commit B: parent exactly R, tree exactly
# K's. Nothing is reset, force-pushed or rewritten, the released history stays
# intact, and the deploy pipeline treats B as an ordinary release.

resolve_ref() {
  local out
  out="$(git rev-parse "$1" 2>/dev/null || true)"
  [[ "$out" =~ ^[0-9a-f]{40}$ ]] || return 1
  printf '%s\n' "$out"
}

verify_rollback_shape() {
  local rollback="$1" released="$2" known_good="$3"
  local parent tree good_tree
  parent="$(resolve_ref "$rollback^")" || { err "Rollback commit $rollback has no readable parent."; return 1; }
  tree="$(resolve_ref "$rollback^{tree}")" || { err "Rollback commit $rollback has no readable tree."; return 1; }
  good_tree="$(resolve_ref "$known_good^{tree}")" || { err "Known-good commit $known_good has no readable tree."; return 1; }
  [[ "$parent" == "$released" ]] || { err "Rollback commit parent is $parent, not the released SHA $released."; return 1; }
  [[ "$tree" == "$good_tree" ]] || { err "Rollback commit tree is $tree, not the known-good tree $good_tree."; return 1; }
}

rollback_commit_message() {
  printf 'revert(release): roll %s back to known-good tree %s\n\n' "${1:0:12}" "${2:0:12}"
  printf 'Operator-triggered bounded rollback, covered by the approval of release card %s.\n' "$1"
  printf 'Forward commit only: parent %s, tree exactly that of %s. No history was rewritten.\n' "$1" "$2"
}

ROLLBACK_STATE_FILE=""
ROLLBACK_COMMIT_SHA=""
ROLLBACK_STATE_STATUS=""
ROLLBACK_STATE_CREATED=""

# Load whatever a previous, interrupted run of this exact rollback recorded. A
# state that does not validate, or that names a different release or target, is
# refused rather than guessed at: reconciling towards an unapproved SHA is the
# one thing a resumable rollback must never do.
load_rollback_state() {
  local released="$1" known_good="$2" state_env
  ROLLBACK_STATE_FILE="$(rollback_path "$released")"
  ROLLBACK_COMMIT_SHA=""
  ROLLBACK_STATE_STATUS=""
  ROLLBACK_STATE_CREATED=""
  [[ -f "$ROLLBACK_STATE_FILE" ]] || { info "No prior rollback state; this is the first execution."; return 0; }
  state_env="$(card_tool rollback-export --path "$ROLLBACK_STATE_FILE")" || {
    err "Rollback state for $released did not validate; refusing to guess what was already done."
    return 1
  }
  eval "$state_env"
  [[ "${ROLLBACK_RELEASED_SHA:-}" == "$released" ]] || { err "Rollback state names released SHA ${ROLLBACK_RELEASED_SHA:-<none>}, not $released."; return 1; }
  [[ "${ROLLBACK_KNOWN_GOOD_SHA:-}" == "$known_good" ]] || { err "Rollback state targets ${ROLLBACK_KNOWN_GOOD_SHA:-<none>}, but the approved card names $known_good."; return 1; }
  [[ "${ROLLBACK_SURFACE:-}" == "$SURFACE" ]] || { err "Rollback state surface ${ROLLBACK_SURFACE:-<none>} does not equal --surface $SURFACE."; return 1; }
  [[ "${ROLLBACK_RISK:-}" == "$RISK" ]] || { err "Rollback state risk ${ROLLBACK_RISK:-<none>} does not equal --risk $RISK."; return 1; }
  ROLLBACK_COMMIT_SHA="${ROLLBACK_ROLLBACK_SHA:-}"
  ROLLBACK_STATE_STATUS="${ROLLBACK_STATUS:-}"
  ROLLBACK_STATE_CREATED="${ROLLBACK_CREATED_AT:-}"
  info "Prior rollback state: status=$ROLLBACK_STATE_STATUS rollback_sha=$ROLLBACK_COMMIT_SHA"
}

write_rollback_state() {
  local released="$1" known_good="$2" rollback="$3" status="$4" now
  now="$(utc_now)"
  [[ -n "$ROLLBACK_STATE_CREATED" ]] || ROLLBACK_STATE_CREATED="$now"
  if ! card_tool rollback-write \
        --path "$ROLLBACK_STATE_FILE" \
        --released-sha "$released" \
        --known-good-sha "$known_good" \
        --rollback-sha "$rollback" \
        --surface "$SURFACE" \
        --risk "$RISK" \
        --status "$status" \
        --created-at "$ROLLBACK_STATE_CREATED" \
        --updated-at "$now"; then
    err "Could not record rollback state; refusing to continue without a resumable record."
    return 1
  fi
  ROLLBACK_STATE_STATUS="$status"
  info "ROLLBACK_STATE=$status rollback_sha=$rollback"
}

mode_rollback() {
  banner "ROLLBACK — bounded operator-triggered recovery ($SURFACE, risk=$RISK)"
  require_approval
  load_and_verify_card "$APPROVAL_SHA" defer-head

  local released="$APPROVAL_SHA" known_good="${CARD_KNOWN_GOOD_SHA:-}"
  if [[ "${CARD_ROLLBACK_MODE:-}" != "$ROLLBACK_MODE_VALUE" ]]; then
    err "Card rollback mode is ${CARD_ROLLBACK_MODE:-<none>}, not $ROLLBACK_MODE_VALUE; it authorises no rollback."
    final_state blocked "prepare and approve a card that authorises rollback" "no mutation"
    exit 2
  fi
  if [[ ! "$known_good" =~ ^[0-9a-f]{40}$ ]]; then
    err "Card carries no full known-good SHA to roll back to."
    final_state blocked "prepare a new card from a verified live runtime" "no mutation"
    exit 2
  fi
  if [[ "$known_good" == "$released" ]]; then
    err "Card known-good SHA equals the released SHA; there is nothing to roll back to."
    final_state blocked "prepare a card whose known-good SHA precedes the release" "no mutation"
    exit 2
  fi
  info "ROLLBACK_FROM=$released"
  info "ROLLBACK_TO=$known_good"
  info "Rollback is $ROLLBACK_MODE_VALUE: it runs because you ran this command, never on its own."

  [[ "$branch" != main && -n "$branch" ]] || { err "ROLLBACK refused — run it from the release feature branch, never from main."; final_state blocked "checkout the release feature branch" "no mutation"; exit 3; }
  tracked_tree_is_clean || { err "ROLLBACK refused — uncommitted tracked changes present."; final_state blocked "commit or revert changes" "no mutation"; exit 3; }
  load_rollback_state "$released" "$known_good" || { final_state blocked "repair or remove the invalid rollback state" "no mutation"; exit 3; }
  fetch_main_required || { final_state blocked "repair origin fetch" "no mutation"; exit 3; }

  local head origin_main
  head="$(git rev-parse HEAD)"
  origin_main="$(git rev-parse origin/main)"

  # main may only be the released SHA (nothing pushed yet) or the exact rollback
  # commit already recorded here. Anything else is a release this card never
  # named, and rolling one of those back would land somewhere nobody approved.
  if [[ "$origin_main" != "$released" ]] && [[ -z "$ROLLBACK_COMMIT_SHA" || "$origin_main" != "$ROLLBACK_COMMIT_SHA" ]]; then
    err "origin/main is $origin_main: neither the released SHA $released nor a recorded rollback commit."
    final_state blocked "reconcile main before rolling anything back" "no mutation"
    exit 3
  fi
  if [[ "$head" != "$released" ]] && [[ -z "$ROLLBACK_COMMIT_SHA" || "$head" != "$ROLLBACK_COMMIT_SHA" ]]; then
    err "HEAD is $head; rollback expects the released SHA $released or its recorded rollback commit."
    final_state blocked "check out the approved release SHA" "no mutation"
    exit 3
  fi

  step "Rollback target ancestry"
  resolve_ref "$known_good^{tree}" >/dev/null || {
    err "Known-good $known_good is not readable in this checkout; fetch it before rolling back."
    final_state blocked "fetch the known-good commit" "no mutation"
    exit 3
  }
  git merge-base --is-ancestor "$known_good" "$released" 2>/dev/null || {
    err "Known-good $known_good is not an ancestor of released $released; that is not a bounded rollback."
    final_state blocked "prepare a new card naming a real ancestor" "no mutation"
    exit 3
  }
  info "PASS: $known_good is an ancestor of $released."

  # Reconcile and report what is actually live, before anything mutates. Until
  # the rollback commit is pushed, the released SHA is what should be running;
  # if it is not, this is not the situation the card described.
  if [[ "$origin_main" == "$released" ]]; then
    step "Live runtime reconciliation (before any mutation)"
    if runtime_matches "$released"; then
      info "RUNTIME_NOW=$released"
      info "$RUNTIME_REPORT"
    elif runtime_matches "$known_good"; then
      err "Live runtime already reports the known-good SHA $known_good while origin/main is $released."
      err "That is a deployment inconsistency, not the state this card's rollback covers."
      final_state blocked "reconcile the deployment before rolling back" "no mutation"
      exit 3
    else
      err "Could not confirm that the released SHA $released is what is live right now."
      err "Refusing to roll back a release that cannot be shown to be running."
      final_state proof-pending "rerun rollback where the Mac Mini runtime is readable" "no mutation"
      exit 4
    fi
  fi

  local rollback_sha="$ROLLBACK_COMMIT_SHA" tree
  if [[ -z "$rollback_sha" ]]; then
    step "Create the forward rollback commit"
    tree="$(resolve_ref "$known_good^{tree}")" || {
      err "Could not read the known-good tree of $known_good."
      final_state blocked "fetch the known-good commit" "no mutation"
      exit 3
    }
    rollback_sha="$(git commit-tree "$tree" -p "$released" -m "$(rollback_commit_message "$released" "$known_good")" 2>/dev/null || true)"
    if [[ ! "$rollback_sha" =~ ^[0-9a-f]{40}$ ]]; then
      err "Could not create the rollback commit object; nothing was changed."
      final_state blocked "retry rollback in a healthy checkout" "no mutation"
      exit 3
    fi
    verify_rollback_shape "$rollback_sha" "$released" "$known_good" || {
      err "Refusing the rollback commit: it is not exactly parent=$released with the tree of $known_good."
      final_state blocked "retry rollback in a healthy checkout" "no mutation"
      exit 3
    }
    # Recorded before the branch moves and long before the push, so an
    # interruption anywhere after this reconciles onto this one commit instead
    # of making a second one.
    write_rollback_state "$released" "$known_good" "$rollback_sha" committed || {
      final_state blocked "make .release writable and rerun the same rollback command" "no mutation"
      exit 3
    }
  else
    verify_rollback_shape "$rollback_sha" "$released" "$known_good" || {
      err "The recorded rollback commit no longer has the approved parent/tree shape."
      final_state blocked "repair or remove the invalid rollback state" "no mutation"
      exit 3
    }
    info "Reusing recorded rollback commit $rollback_sha; a second rollback commit is never created."
  fi

  if [[ "$head" != "$rollback_sha" ]]; then
    step "Move $branch onto the rollback commit"
    if ! git read-tree -u -m "$released" "$rollback_sha"; then
      err "Could not write the known-good tree into the working tree; restoring the tracked preimage."
      git read-tree -u --reset HEAD || err "Preimage restore failed; do not continue by hand until git status is clean."
      final_state blocked "restore a clean checkout and rerun the same rollback command" "no push"
      exit 3
    fi
    if ! git update-ref -m "rollback $released to known-good $known_good" "refs/heads/$branch" "$rollback_sha" "$released"; then
      err "Could not move $branch from $released to $rollback_sha; restoring the tracked preimage."
      git read-tree -u --reset HEAD || err "Preimage restore failed; do not continue by hand until git status is clean."
      final_state blocked "resolve the branch conflict and rerun the same rollback command" "no push"
      exit 3
    fi
  fi

  step "Rollback commit invariants"
  head="$(git rev-parse HEAD)"
  [[ "$head" == "$rollback_sha" ]] || { err "HEAD is $head, not the rollback commit $rollback_sha."; final_state blocked "restore the branch and rerun the same rollback command" "no push"; exit 3; }
  verify_rollback_shape "$rollback_sha" "$released" "$known_good" || { final_state blocked "restore the branch and rerun the same rollback command" "no push"; exit 3; }
  tracked_tree_is_clean || { err "Working tree does not exactly match the known-good tree after the rollback commit."; final_state blocked "restore a clean checkout and rerun the same rollback command" "no push"; exit 3; }
  info "ROLLBACK_COMMIT=$rollback_sha (parent $released, tree of $known_good)"

  if [[ "$origin_main" == "$rollback_sha" ]]; then
    info "origin/main is already $rollback_sha; skipping a duplicate push."
  else
    step "Push the rollback commit to main"
    if ! git push origin "$rollback_sha:refs/heads/main"; then
      err "Push of the rollback commit failed; main is unchanged at $released."
      info "ROLLBACK_RESUME_COMMAND=$(rollback_command "$released")"
      final_state blocked "repair the push and rerun the same rollback command" "rollback commit exists locally, main unchanged"
      exit 1
    fi
    fetch_main_required || { final_state blocked "repair origin fetch and rerun the same rollback command" "push attempted"; exit 1; }
    origin_main="$(git rev-parse origin/main)"
    if [[ "$origin_main" != "$rollback_sha" ]]; then
      err "Post-push origin/main is $origin_main, not the rollback commit $rollback_sha."
      final_state blocked "reconcile main and rerun the same rollback command" "post-push exact-SHA proof failed"
      exit 1
    fi
  fi
  write_rollback_state "$released" "$known_good" "$rollback_sha" pushed || { final_state blocked "make .release writable and rerun the same rollback command" "rollback commit is on main"; exit 3; }
  info "ROLLBACK_PUSHED_SHA=$rollback_sha"

  # Exactly the release proof gates, keyed to the rollback commit. No live
  # journey runs here: a rollback restores a tree that already passed its own
  # proof, and it must never be the reason a message reaches a real doctor.
  local tests_rc deploy_rc runtime_rc tests_completed
  set +e; wait_for_exact_workflow Tests test.yml "$rollback_sha" push; tests_rc=$?; set -e
  if [[ $tests_rc == 1 ]]; then
    err "main is $rollback_sha, but its Tests run failed. The rollback is not live."
    info "ROLLBACK_RESUME_COMMAND=$(rollback_command "$released")"
    final_state blocked "fix the failed Tests run for the rollback commit" "rollback_sha=$rollback_sha tests=failed"
    exit 1
  fi
  tests_completed="$WORKFLOW_UPDATED_AT"
  if [[ $tests_rc == 4 || -z "$tests_completed" ]]; then
    warn "main is $rollback_sha; Tests has not proven it yet, and nothing else has changed."
    info "ROLLBACK_RESUME_COMMAND=$(rollback_command "$released")"
    final_state proof-pending "rerun the same rollback command after Tests progresses" "rollback_sha=$rollback_sha tests=pending"
    exit 4
  fi

  set +e; wait_for_exact_workflow "Deploy Mac Mini" deploy-mac.yml "$rollback_sha" workflow_run "$tests_completed"; deploy_rc=$?; set -e
  if [[ $deploy_rc == 1 ]]; then
    err "main is $rollback_sha, but its deploy failed. The rollback is not live."
    info "ROLLBACK_RESUME_COMMAND=$(rollback_command "$released")"
    final_state blocked "fix the failed deploy run for the rollback commit" "rollback_sha=$rollback_sha tests=1 deploy=failed"
    exit 1
  fi
  if [[ $deploy_rc == 4 ]]; then
    warn "main is $rollback_sha; its deploy has not completed successfully yet."
    info "ROLLBACK_RESUME_COMMAND=$(rollback_command "$released")"
    final_state proof-pending "rerun the same rollback command after deploy progresses" "rollback_sha=$rollback_sha tests=1 deploy=pending"
    exit 4
  fi

  set +e; prove_exact_live_runtime "$rollback_sha"; runtime_rc=$?; set -e
  if [[ $runtime_rc != 0 ]]; then
    if runtime_matches "$released"; then
      err "Rollback blocked: main is $rollback_sha, but the live runtime is still the released SHA $released."
      err "The deploy left the released code running. Nothing on the Mac Mini has been reverted."
      info "ROLLBACK_RESUME_COMMAND=$(rollback_command "$released")"
      final_state blocked "make the deploy of $rollback_sha actually land, then rerun the same rollback command" "rollback_sha=$rollback_sha runtime=$released"
      exit 1
    fi
    warn "main is $rollback_sha; its runtime identity is not proven yet."
    info "ROLLBACK_RESUME_COMMAND=$(rollback_command "$released")"
    final_state proof-pending "rerun the same rollback command where the runtime is readable" "rollback_sha=$rollback_sha tests=1 deploy=1 runtime=pending"
    exit 4
  fi

  write_rollback_state "$released" "$known_good" "$rollback_sha" proved || { final_state blocked "make .release writable and rerun the same rollback command" "runtime proved"; exit 3; }
  banner "ROLLBACK complete"
  info "RELEASED_SHA=$released"
  info "ROLLBACK_COMMIT_SHA=$rollback_sha"
  info "KNOWN_GOOD_TREE_SHA=$known_good"
  info "main and the live runtime are $rollback_sha, whose tree is exactly that of $known_good."
  final_state rolled-back none "released_sha=$released rollback_sha=$rollback_sha known_good_sha=$known_good tests=1 deploy=1 runtime=1"
}

case "$MODE" in
  prepare) mode_prepare ;;
  ship) mode_ship ;;
  attest) mode_attest ;;
  rollback) mode_rollback ;;
esac
