#!/usr/bin/env bash
# Deterministic release closure for Portfolio Guru.
#
# One approved ship run owns offline verification, exact-SHA reconciliation,
# GitHub Tests/deploy provenance, exact runtime identity, and risk-scaled proof.
# prepare is side-effect free. A resume run proves an already-pushed SHA and
# never pushes it again. Live Telegram/Kaizen guards remain authoritative.

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
  scripts/release_loop.sh --surface telegram --mode prepare [--risk internal|telegram|broad]
  scripts/release_loop.sh --surface telegram --mode ship --risk internal|telegram|broad [--approved]
  scripts/release_loop.sh --surface telegram --mode ship --risk internal|telegram|broad --release-sha <40hex> [--approved]

Options:
  --surface <name>     Only "telegram" is wired today.
  --mode <mode>        prepare (side-effect free) or ship (approval gated).
  --risk <class>       Required for ship: internal, telegram, or broad.
  --release-sha <sha>  Resume proof only for an already-pushed exact full SHA.
  --approved           Semantic approval for this complete ship/resume graph.
  --no-dogfood         Legacy broad-proof skip; always leaves proof pending.
  -h, --help           Show this help.

Approval for ship/resume (one of):
  RELEASE_APPROVED=telegram-YYYYMMDD
  --approved

Exit codes:
  0  ready/live
  1  blocked gate or completed CI/deploy failure
  2  approval missing/stale
  3  git/reconciliation/resume refusal
  4  retryable proof pending (missing/running/timeout/runtime/live proof)
  64 usage error
EOF
}

SURFACE="telegram"
MODE=""
RISK=""
RISK_SUPPLIED=0
APPROVED_FLAG=0
NO_DOGFOOD=0
RELEASE_SHA=""
PROOF_TIMEOUT="${RELEASE_LOOP_PROOF_TIMEOUT:-900}"
PROOF_INTERVAL="${RELEASE_LOOP_PROOF_INTERVAL:-5}"
GITHUB_REPOSITORY="${RELEASE_LOOP_GITHUB_REPOSITORY:-moeedahmed/portfolio-guru}"

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
    --approved) APPROVED_FLAG=1; shift ;;
    --no-dogfood) NO_DOGFOOD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "Unknown argument: $1"; echo; usage; exit 64 ;;
  esac
done

[[ -n "$MODE" ]] || { err "Missing --mode (prepare|ship)."; echo; usage; exit 64; }
[[ "$MODE" == prepare || "$MODE" == ship ]] || { err "Invalid --mode: $MODE (expected prepare|ship)."; exit 64; }
[[ "$SURFACE" == telegram ]] || { err "Unsupported --surface: $SURFACE. Only 'telegram' is wired in this slice."; exit 64; }
if [[ "$RISK_SUPPLIED" == 1 && "$RISK" != internal && "$RISK" != telegram && "$RISK" != broad ]]; then
  err "Invalid --risk: $RISK (expected internal|telegram|broad)."
  exit 64
fi
if [[ "$MODE" == ship && "$RISK_SUPPLIED" != 1 ]]; then
  err "--risk is required for ship; classification must be explicit."
  exit 64
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

tracked_tree_is_clean() { [[ -z "$(git status --porcelain --untracked-files=no)" ]]; }
origin_main_ancestor() { git merge-base --is-ancestor origin/main HEAD 2>/dev/null; }
ahead_behind() {
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    git rev-list --left-right --count origin/main...HEAD
  else
    echo "? ?"
  fi
}
fetch_main_prepare() { git fetch --quiet origin main 2>/dev/null || git fetch --quiet origin 2>/dev/null || true; }
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

mode_prepare() {
  RISK="${RISK:-telegram}"
  banner "PREPARE — release readiness (safe, non-live)"
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
  fetch_main_prepare

  local reasons=() ahead
  run_check "Offline preflight" bash "$ROOT/scripts/preflight.sh" || reasons+=("offline preflight failed")
  run_check "Telegram offline QA" bash "$ROOT/scripts/telegram_qa_offline.sh" || reasons+=("Telegram offline QA failed")
  [[ "$branch" != main && -n "$branch" ]] || reasons+=("not on a feature branch")
  tracked_tree_is_clean || reasons+=("uncommitted tracked changes")
  read -r _ ahead < <(ahead_behind)
  if [[ "$ahead" == "?" ]]; then reasons+=("origin/main unknown")
  elif ! origin_main_ancestor; then reasons+=("branch not fast-forwardable onto origin/main")
  elif [[ "$ahead" == 0 ]]; then reasons+=("nothing ahead of origin/main")
  fi

  banner "READINESS"
  if [[ ${#reasons[@]} == 0 ]]; then
    info "READY — ship is unblocked."
    info "Next: scripts/release_loop.sh --surface $SURFACE --mode ship --risk $RISK --approved"
    return 0
  fi
  warn "BLOCKED — resolve before shipping:"
  local reason; for reason in "${reasons[@]}"; do printf '      - %s\n' "$reason"; done
  return 1
}

require_approval() {
  local expected
  expected="${SURFACE}-$(date -u +%Y%m%d)"
  if [[ "$APPROVED_FLAG" == 1 ]]; then info "Approval: --approved covers this ship graph."; return 0; fi
  if [[ "${RELEASE_APPROVED:-}" == "$expected" ]]; then info "Approval: RELEASE_APPROVED matches $expected."; return 0; fi
  err "SHIP refused — explicit approval required."
  [[ -z "${RELEASE_APPROVED:-}" ]] || err "RELEASE_APPROVED is stale or wrong surface (expected $expected)."
  final_state "release-ready" "provide current approval and rerun the same command" "no mutating action taken"
  exit 2
}

PUSHED_SHA=""

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
# approved SHA. Nothing is trusted that was not verified after the fact.
ship_reconcile_and_push() {
  step "Reconcile $branch -> main and push exact SHA"
  local candidate_sha
  candidate_sha="$(git rev-parse HEAD)"
  [[ "$candidate_sha" =~ ^[0-9a-fA-F]{40}$ ]] || { err "Cannot capture a full release SHA."; return 1; }
  fetch_main_required || return 1
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
  fetch_main_required || return 1
  local head remote
  head="$(git rev-parse HEAD)"
  remote="$(git rev-parse origin/main)"
  if [[ "$head" != "$RELEASE_SHA" || "$remote" != "$RELEASE_SHA" ]]; then
    err "Resume requires HEAD == origin/main == --release-sha; got HEAD=$head origin/main=$remote."
    return 1
  fi
  PUSHED_SHA="$RELEASE_SHA"
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

prove_risk_journey() {
  step "Risk-based live proof ($RISK)"
  case "$RISK" in
    internal) info "PASS: internal risk needs no manual journey."; return 0 ;;
    telegram)
      [[ "${TELEGRAM_LIVE_APPROVED:-}" == portfolio-guru-live-qa-approved ]] || { warn "Telegram proof pending: exact TELEGRAM_LIVE_APPROVED guard absent."; return 4; }
      [[ -n "${TELETHON_SESSION:-}" && -n "${TELEGRAM_API_ID:-${TELETHON_API_ID:-}}" && -n "${TELEGRAM_API_HASH:-${TELETHON_API_HASH:-}}" ]] || { warn "Telegram proof pending: credentials incomplete."; return 4; }
      RUN_LIVE_TELEGRAM=1 REQUIRE_TELEGRAM_LIVE=1 bash "$ROOT/scripts/telegram_bot_qa.sh" --focused-release || return 1
      ;;
    broad)
      [[ "$NO_DOGFOOD" != 1 ]] || { warn "Broad proof skipped; proof remains pending."; return 4; }
      [[ -t 0 && -t 1 ]] || { warn "Broad risk requires the interactive strict checklist; no TTY available."; return 4; }
      bash "$ROOT/scripts/dogfood_smoke.sh" --strict-release || return 1
      ;;
  esac
}

resume_command() {
  printf 'scripts/release_loop.sh --surface %s --mode ship --risk %s --release-sha %s --approved' "$SURFACE" "$RISK" "$PUSHED_SHA"
}

mode_ship() {
  banner "SHIP — gated release closure ($SURFACE, risk=$RISK)"
  require_approval
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
    run_check "Offline preflight" bash "$ROOT/scripts/preflight.sh" || { final_state blocked "fix offline preflight" "no push"; exit 1; }
    run_check "Telegram offline QA" bash "$ROOT/scripts/telegram_qa_offline.sh" || { final_state blocked "fix Telegram offline QA" "no push"; exit 1; }
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
  if [[ $risk_rc == 1 ]]; then final_state blocked "fix failed $RISK proof" "pushed_sha=$PUSHED_SHA tests=1 deploy=1 runtime=1"; exit 1; fi
  if [[ $risk_rc != 0 ]]; then
    banner "SHIP proof pending"; info "RESUME_COMMAND=$(resume_command)"
    final_state proof-pending "run RESUME_COMMAND with protected $RISK proof available" "pushed_sha=$PUSHED_SHA tests=1 deploy=1 runtime=1 risk=pending"
    exit 4
  fi
  banner "SHIP complete"
  final_state live none "pushed_sha=$PUSHED_SHA tests=1 deploy=1 runtime=1 risk=$RISK:1"
}

case "$MODE" in
  prepare) mode_prepare ;;
  ship) mode_ship ;;
esac
