#!/usr/bin/env bash
# Deterministic strict-release dogfood summary policy.
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: dogfood_summary_policy.sh <strict:0|1> <record:0|1> <pass> <fail> <skip>" >&2
  exit 64
fi
STRICT="$1" RECORD="$2" PASS="$3" FAIL="$4" SKIP="$5"
for value in "$STRICT" "$RECORD" "$PASS" "$FAIL" "$SKIP"; do
  [[ "$value" =~ ^[0-9]+$ ]] || exit 64
done
if [[ "$STRICT" == 1 ]]; then
  [[ "$RECORD" == 1 && "$PASS" == 15 && "$FAIL" == 0 && "$SKIP" == 0 ]]
else
  [[ "$FAIL" == 0 ]]
fi
