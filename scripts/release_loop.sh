#!/usr/bin/env bash
#
# scripts/release_loop.sh
#
# Deterministic release-closure entrypoint for Portfolio Guru.
#
# Why this exists: product fixes get committed locally, then the deploy/restart
# is remembered and run by hand as a separate second step. This wraps the
# repeatable closure steps behind ONE command so the AI keeps doing diagnosis +
# code + commit, and the *closure* (tests -> reconcile -> push -> CI deploy +
# restart -> dogfood) becomes deterministic and gated.
#
# This is a thin orchestrator. It does NOT reimplement deploy/restart logic.
# It calls the existing deterministic pieces:
#   - offline gate    -> scripts/preflight.sh + scripts/telegram_qa_offline.sh
#   - deploy+restart   -> a push to main triggers .github/workflows/deploy-mac.yml
#                         which runs scripts/deploy_mac.sh on the Mac Mini
#                         (git pull main, pip install, py_compile, launchctl restart)
#   - dogfood checkpoint -> scripts/dogfood_smoke.sh
#
# Run this on the DEV machine (laptop), not the Mac Mini deploy checkout. The
# Mac Mini checkout must stay clean and on main; ship refuses on main anyway.
#
# Modes:
#   --mode prepare   Safe, non-live. Reports release readiness and whether ship
#                    is READY or BLOCKED (and why). NEVER pushes/deploys/restarts.
#   --mode ship      Gated, conservative closure. Refuses without explicit
#                    approval and a clean, fast-forwardable tree. When approved,
#                    pushes main (so CI deploys + restarts), then collects
#                    deploy/restart proof and the dogfood checkpoint where
#                    available. It prints FINAL_RELEASE_STATE=live only when
#                    proof was actually collected; otherwise proof-pending.
#
# Surfaces:
#   --surface telegram   (only surface wired in this slice)
#
# Approval for ship (either is accepted; checked BEFORE any live action):
#   RELEASE_APPROVED=telegram-YYYYMMDD   (must match --surface and today, UTC)
#   --approved                           (explicit per-invocation opt-in flag)
#
# Exit codes:
#   0  success (ship done) / prepare says READY
#   1  prepare says BLOCKED, or an offline gate failed
#   2  ship refused: approval missing/stale
#   3  ship refused: tree/branch/reconcile gate failed
#   64 usage error
#
# Safety: never logs credentials/tokens. prepare is always side-effect free.
# Never submits Kaizen forms (draft-only product); this script does not touch
# Kaizen at all.

set -euo pipefail

# --- presentation helpers (no secrets ever printed) --------------------------

banner() { printf '\n=== %s ===\n' "$*"; }
step()   { printf '\n--- %s\n' "$*"; }
info()   { printf '    %s\n' "$*"; }
warn()   { printf '  ! %s\n' "$*"; }
err()    { printf '  ERROR: %s\n' "$*" >&2; }

final_state() {
  local state="$1"
  local gate="$2"
  local proof="$3"
  banner "FINAL RELEASE STATE"
  info "FINAL_RELEASE_STATE=$state"
  info "FINAL_RELEASE_GATE=$gate"
  info "FINAL_RELEASE_PROOF=$proof"
}

usage() {
  cat <<'EOF'
Portfolio Guru — deterministic release closure.

Usage:
  scripts/release_loop.sh --surface telegram --mode prepare
  scripts/release_loop.sh --surface telegram --mode ship [--approved]
  scripts/release_loop.sh --help

Options:
  --surface <name>   Release surface. Only "telegram" is wired today.
  --mode <mode>      "prepare" (safe readiness report) or "ship" (gated closure).
  --approved         Explicit per-invocation approval for ship.
  --no-dogfood       ship: skip the interactive dogfood checkpoint (forces
                     FINAL_RELEASE_STATE=proof-pending).
  -h, --help         Show this help.

Approval for ship (one of, checked before any live action):
  RELEASE_APPROVED=telegram-YYYYMMDD   (today, UTC, surface-scoped)
  --approved                           (explicit opt-in flag)

prepare never pushes, deploys, or restarts. ship pushes main only after every
gate passes; deploy + restart are then performed by CI (deploy_mac.sh on the
Mac Mini), not by this script. ship ends with one final state: live,
release-ready, proof-pending, or blocked.
EOF
}

# --- argument parsing --------------------------------------------------------

SURFACE="telegram"
MODE=""
APPROVED_FLAG=0
NO_DOGFOOD=0
WATCH_DEPLOY="${RELEASE_LOOP_WATCH_DEPLOY:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --surface)   SURFACE="${2:-}"; shift 2 ;;
    --surface=*) SURFACE="${1#*=}"; shift ;;
    --mode)      MODE="${2:-}"; shift 2 ;;
    --mode=*)    MODE="${1#*=}"; shift ;;
    --approved)  APPROVED_FLAG=1; shift ;;
    --no-dogfood) NO_DOGFOOD=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           err "Unknown argument: $1"; echo; usage; exit 64 ;;
  esac
done

if [[ -z "$MODE" ]]; then
  err "Missing --mode (prepare|ship)."
  echo; usage; exit 64
fi
if [[ "$MODE" != "prepare" && "$MODE" != "ship" ]]; then
  err "Invalid --mode: $MODE (expected prepare|ship)."
  exit 64
fi
if [[ "$SURFACE" != "telegram" ]]; then
  err "Unsupported --surface: $SURFACE. Only 'telegram' is wired in this slice."
  exit 64
fi

# --- repo context ------------------------------------------------------------

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  err "Not inside a git repository."
  exit 64
fi
cd "$ROOT"

branch="$(git branch --show-current)"

# --- git readiness primitives ------------------------------------------------

fetch_main() {
  # Best-effort; never fatal so prepare can still report offline state.
  git fetch --quiet origin main 2>/dev/null || git fetch --quiet origin 2>/dev/null || true
}

tracked_tree_is_clean() {
  [[ -z "$(git status --porcelain --untracked-files=no)" ]]
}

origin_main_ancestor() {
  # True when origin/main is an ancestor of HEAD => a clean fast-forward push.
  git merge-base --is-ancestor origin/main HEAD 2>/dev/null
}

# Echoes "<behind> <ahead>" relative to origin/main, or "? ?" if unknown.
ahead_behind() {
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    git rev-list --left-right --count origin/main...HEAD
  else
    echo "? ?"
  fi
}

print_git_state() {
  step "Git state"
  info "Repo:   $ROOT"
  info "Branch: ${branch:-<detached>}"
  info "Commit: $(git rev-parse --short HEAD)"
  if tracked_tree_is_clean; then
    info "Tracked tree: clean"
  else
    warn "Tracked tree: UNCOMMITTED changes present"
    git status --short --untracked-files=no | sed 's/^/      /'
  fi
  local untracked
  untracked="$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')"
  info "Untracked files: $untracked (not shipped; review before committing)"
  fetch_main
  read -r behind ahead < <(ahead_behind)
  info "vs origin/main: ahead=$ahead behind=$behind"
}

# Runs a sub-step command, capturing its exit code without aborting the script.
run_check() {
  local label="$1"; shift
  step "$label"
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    info "PASS: $label"
    return 0
  fi
  warn "FAIL (exit $rc): $label"
  return 1
}

# --- prepare -----------------------------------------------------------------

mode_prepare() {
  banner "PREPARE — release readiness (safe, non-live)"
  info "Surface: $SURFACE. This run never pushes, deploys, or restarts."
  print_git_state

  local reasons=()

  # Offline gates (reused, not reimplemented).
  run_check "Offline preflight (scripts/preflight.sh)" \
    bash "$ROOT/scripts/preflight.sh" \
    || reasons+=("offline preflight failed")

  run_check "Telegram offline QA (scripts/telegram_qa_offline.sh)" \
    bash "$ROOT/scripts/telegram_qa_offline.sh" \
    || reasons+=("telegram offline QA failed")

  # Static readiness gates (no code run).
  step "Ship preconditions"
  if [[ "$branch" == "main" || -z "$branch" ]]; then
    warn "On main / detached HEAD — ship runs from a feature branch."
    reasons+=("not on a feature branch")
  else
    info "On feature branch: $branch"
  fi

  if tracked_tree_is_clean; then
    info "Working tree: clean (commit already present)"
  else
    warn "Uncommitted tracked changes — commit before shipping."
    reasons+=("uncommitted tracked changes")
  fi

  read -r behind ahead < <(ahead_behind)
  if [[ "$ahead" == "?" ]]; then
    warn "origin/main not found locally — cannot confirm reconcile."
    reasons+=("origin/main unknown")
  elif ! origin_main_ancestor; then
    warn "origin/main is not an ancestor of HEAD — rebase onto main first."
    reasons+=("branch not fast-forwardable onto origin/main")
  elif [[ "$ahead" == "0" ]]; then
    warn "No commits ahead of origin/main — nothing to ship."
    reasons+=("nothing ahead of origin/main")
  else
    info "Fast-forwardable onto origin/main (ahead=$ahead, behind=$behind)"
  fi

  banner "READINESS"
  if [[ ${#reasons[@]} -eq 0 ]]; then
    info "READY — ship is unblocked."
    info "Next: scripts/release_loop.sh --surface $SURFACE --mode ship --approved"
    info "  (or set RELEASE_APPROVED=$SURFACE-$(date -u +%Y%m%d))"
    return 0
  fi
  warn "BLOCKED — resolve before shipping:"
  local r
  for r in "${reasons[@]}"; do
    printf '      - %s\n' "$r"
  done
  return 1
}

# --- ship --------------------------------------------------------------------

require_approval() {
  local today expected
  today="$(date -u +%Y%m%d)"
  expected="${SURFACE}-${today}"
  if [[ "$APPROVED_FLAG" == "1" ]]; then
    info "Approval: --approved flag present."
    return 0
  fi
  if [[ "${RELEASE_APPROVED:-}" == "$expected" ]]; then
    info "Approval: RELEASE_APPROVED matches $expected."
    return 0
  fi
  err "SHIP refused — explicit approval required."
  echo "  Provide one of:" >&2
  echo "    RELEASE_APPROVED=$expected   (today, surface-scoped)" >&2
  echo "    --approved                   (explicit per-invocation opt-in)" >&2
  if [[ -n "${RELEASE_APPROVED:-}" ]]; then
    echo "  Note: RELEASE_APPROVED is set but does not match $expected (stale or wrong surface)." >&2
  fi
  final_state "release-ready" "provide RELEASE_APPROVED=$expected or --approved, then rerun ship" "no live action taken"
  exit 2
}

ship_reconcile_and_push() {
  step "Reconcile $branch -> main and push (LIVE — triggers CI deploy)"
  local start_branch="$branch"
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
  git merge --ff-only "$start_branch"
  git push origin main
  git checkout "$start_branch"
  info "Pushed origin/main -> $(git rev-parse --short origin/main)"
}

ship_deploy_restart_proof() {
  step "Deploy + restart proof (delegated to CI — not reimplemented here)"
  if [[ "$WATCH_DEPLOY" == "1" ]] && command -v gh >/dev/null 2>&1; then
    info "Watching latest Deploy Mac Mini run (best-effort)…"
    local run_id
    run_id="$(gh run list --workflow "Deploy Mac Mini" --limit 1 \
      --json databaseId -q '.[0].databaseId' 2>/dev/null || true)"
    if [[ -n "$run_id" ]]; then
      if gh run watch "$run_id" 2>/dev/null; then
        local conclusion
        conclusion="$(gh run view "$run_id" --json conclusion -q '.conclusion' 2>/dev/null || true)"
        if [[ "$conclusion" == "success" ]]; then
          info "PASS: Deploy Mac Mini workflow completed successfully."
          return 0
        fi
        warn "Deploy Mac Mini workflow conclusion was '${conclusion:-unknown}'."
      else
        warn "Could not watch CI run to completion."
      fi
    else
      warn "No Deploy Mac Mini run found yet."
    fi
  fi
  warn "Deploy/restart proof not auto-collected. Next gate:"
  warn "  gh run list --workflow \"Deploy Mac Mini\" --limit 1"
  warn "  launchctl print gui/\$(id -u)/com.portfolioguru.bot | sed -n '1,25p'"
  warn "  tail -n 30 /tmp/portfolio-guru-bot.log   # bot logs commit+branch on boot"
  return 1
}

ship_dogfood_checkpoint() {
  step "Dogfood checkpoint"
  if [[ "$NO_DOGFOOD" == "1" ]]; then
    warn "Skipped by --no-dogfood. Run 'bash scripts/dogfood_smoke.sh' before"
    warn "trusting this release — this step is required, not optional."
    return 1
  fi
  if [[ -t 0 && -t 1 ]]; then
    info "Launching interactive dogfood smoke (scripts/dogfood_smoke.sh)…"
    if bash "$ROOT/scripts/dogfood_smoke.sh"; then
      info "PASS: dogfood checkpoint completed."
      return 0
    fi
    warn "Dogfood checkpoint failed."
    return 1
  else
    warn "Non-interactive shell — dogfood smoke needs a TTY. Run it by hand:"
    warn "  bash scripts/dogfood_smoke.sh"
    return 1
  fi
}

mode_ship() {
  banner "SHIP — gated release closure ($SURFACE)"

  # Gate 1: approval, BEFORE any live or mutating action.
  require_approval

  # Gate 2: must be on a feature branch (protects the Mac Mini main checkout).
  if [[ "$branch" == "main" || -z "$branch" ]]; then
    err "SHIP refused — on main / detached HEAD. Ship from a feature branch."
    final_state "blocked" "checkout a feature branch with the release commit, then rerun ship" "no live action taken"
    exit 3
  fi

  fetch_main

  # Gate 3: working tree must be committed. ship does NOT create commits.
  if ! tracked_tree_is_clean; then
    err "SHIP refused — uncommitted tracked changes present. Commit first."
    git status --short --untracked-files=no | sed 's/^/      /' >&2
    final_state "blocked" "commit or revert tracked changes, then rerun ship" "no live action taken"
    exit 3
  fi

  # Gate 4: branch must fast-forward onto origin/main and carry new commits.
  read -r behind ahead < <(ahead_behind)
  if [[ "$ahead" == "?" ]]; then
    err "SHIP refused — origin/main not found; cannot reconcile safely."
    final_state "blocked" "fetch origin/main or repair the remote, then rerun ship" "no live action taken"
    exit 3
  fi
  if ! origin_main_ancestor; then
    err "SHIP refused — origin/main is not an ancestor of HEAD (behind=$behind)."
    err "Rebase onto main before shipping."
    final_state "blocked" "rebase onto origin/main, then rerun ship" "no live action taken"
    exit 3
  fi
  if [[ "$ahead" == "0" ]]; then
    err "SHIP refused — no commits ahead of origin/main. Nothing to ship."
    final_state "blocked" "create a release commit or use prepare for readiness only" "no live action taken"
    exit 3
  fi

  # Gate 5: offline gates must pass before anything goes out.
  run_check "Offline preflight (scripts/preflight.sh)" \
    bash "$ROOT/scripts/preflight.sh" \
    || { err "SHIP refused — offline preflight failed."; final_state "blocked" "fix scripts/preflight.sh failures, then rerun ship" "no live action taken"; exit 1; }
  run_check "Telegram offline QA (scripts/telegram_qa_offline.sh)" \
    bash "$ROOT/scripts/telegram_qa_offline.sh" \
    || { err "SHIP refused — telegram offline QA failed."; final_state "blocked" "fix scripts/telegram_qa_offline.sh failures, then rerun ship" "no live action taken"; exit 1; }

  # Live closure — only reached after every gate passes.
  ship_reconcile_and_push
  local deploy_proved=0
  local dogfood_proved=0
  ship_deploy_restart_proof && deploy_proved=1
  ship_dogfood_checkpoint && dogfood_proved=1

  if [[ "$deploy_proved" == "1" && "$dogfood_proved" == "1" ]]; then
    banner "SHIP complete"
    info "main is pushed; deploy/restart and dogfood proof were collected."
    final_state "live" "none" "deploy/restart workflow succeeded; dogfood checkpoint passed"
    return 0
  fi

  local next_gate="collect deploy/restart proof, then run bash scripts/dogfood_smoke.sh"
  if [[ "$deploy_proved" == "1" ]]; then
    next_gate="run bash scripts/dogfood_smoke.sh"
  elif [[ "$dogfood_proved" == "1" ]]; then
    next_gate="collect deploy/restart proof from GitHub Actions, launchctl, and bot logs"
  fi
  banner "SHIP proof pending"
  info "main is pushed; do not call this release live until the next gate passes."
  final_state "proof-pending" "$next_gate" "deploy_proved=$deploy_proved dogfood_proved=$dogfood_proved"
}

# --- dispatch ----------------------------------------------------------------

case "$MODE" in
  prepare) mode_prepare ;;
  ship)    mode_ship ;;
esac
