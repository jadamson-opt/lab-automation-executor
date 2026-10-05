#!/usr/bin/env bash
# Checks that a dropped result does not hang the run.
#
# Makes the incubator drop the result of every step (the work is done, the
# report never arrives), starts a run, and expects it to reach "completed"
# with every step completed. Puts the incubator back to normal afterwards.
set -uo pipefail

EXEC=${EXEC:-http://localhost:5001}
TIMEOUT=${TIMEOUT:-30}
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/.."

restore() {
  echo
  echo "putting incubator-1 back to normal..."
  INC_DROP_PCT=0 docker compose up -d incubator-1 >/dev/null 2>&1
  # A hung run leaves its device claim in the executor's memory.
  echo "restarting executor to clear any claim held by a hung run..."
  docker compose restart executor >/dev/null 2>&1
  until curl -sf "$EXEC/health" >/dev/null; do sleep 1; done
  sleep 4
}
trap restore EXIT

echo "making incubator-1 drop every result..."
INC_DROP_PCT=100 docker compose up -d incubator-1 >/dev/null 2>&1
sleep 5

incubator() { curl -sf "$EXEC/drivers" | jq -r ".[] | select(.device_id==\"incubator-1\") | .$1"; }

drop_pct=$(incubator drop_result_pct)
if [ "$drop_pct" != "100" ]; then
  echo "  FAIL  could not switch dropping on (incubator-1 drop_result_pct=$drop_pct)"
  exit 1
fi

failing=$(curl -sf "$EXEC/drivers" | jq -r '[.[] | select((.fail_pct // 0) > 0) | "\(.device_id)=\(.fail_pct)%"] | join(", ")')
if [ -n "$failing" ]; then
  echo "  FAIL  failures are also on ($failing). A dropped failure cannot be told"
  echo "        from a dropped success. Set LH_/INC_/PR_FAIL_PCT to 0."
  exit 1
fi

dropped_before=$(incubator dropped)

run_id=$(curl -sf -X POST "$EXEC/runs" -H 'Content-Type: application/json' -d '{}' | jq -r '.id // empty')
if [ -z "$run_id" ]; then
  echo "  FAIL  could not create a run -- is the executor up?"
  exit 1
fi
echo "run: $run_id"

curl -sf -X POST "$EXEC/runs/$run_id/start" >/dev/null

start=$(date +%s)
status=""
while [ $(( $(date +%s) - start )) -lt "$TIMEOUT" ]; do
  status=$(curl -sf "$EXEC/runs/$run_id" | jq -r '.run.status')
  case "$status" in completed|failed|aborted) break ;; esac
  sleep 1
done
elapsed=$(( $(date +%s) - start ))
dropped=$(( $(incubator dropped) - dropped_before ))
not_completed=$(curl -sf "$EXEC/runs/$run_id" | jq -r '[.steps[] | select(.status != "completed")] | length')

echo
rc=1
if [ "$status" = completed ] && [ "$not_completed" = 0 ] && [ "$dropped" -gt 0 ]; then
  echo "  PASS  the run completed after ${elapsed}s despite $dropped dropped result(s)"
  rc=0
elif [ "$status" = running ] || [ -z "$status" ]; then
  echo "  FAIL  the run is still '${status:-unknown}' after ${elapsed}s -- a dropped result hung it"
elif [ "$dropped" -le 0 ]; then
  echo "  FAIL  no results were dropped, so this run proves nothing"
else
  echo "  FAIL  the run ended as '$status' with $not_completed step(s) not completed, expected all 'completed'"
fi

echo
curl -sf "$EXEC/runs/$run_id" | jq -r '.steps[] | "  \(.name): \(.status)\(if .error then "  (\(.error))" else "" end)"'
exit $rc
