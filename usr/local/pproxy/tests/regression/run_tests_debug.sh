#!/bin/bash
# run_tests_debug.sh — runs regression tests TOTAL_RUNS times from clean state,
# records per-run results, then analyzes for flakiness and improvement areas.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

STATUS_FILE="/var/local/pproxy/status.ini"
SHADOW_DB="/var/local/pproxy/shadow.db"
TOTAL_RUNS=10
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="$SCRIPT_DIR/debug_results_$TIMESTAMP"
SUMMARY_FILE="$RESULTS_DIR/SUMMARY.txt"

mkdir -p "$RESULTS_DIR"
source "$SCRIPT_DIR/regenv/bin/activate"

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SUMMARY_FILE"
}

get_claimed() {
    grep "^claimed" "$STATUS_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' '
}

wait_for_unclaim() {
    local timeout=120 elapsed=0
    while true; do
        [ "$(get_claimed)" = "0" ] && return 0
        if [ $elapsed -ge $timeout ]; then
            log "  TIMEOUT: still claimed after ${timeout}s"
            return 1
        fi
        sleep 5; elapsed=$((elapsed + 5))
        log "  waiting for unclaim... (${elapsed}s)"
    done
}

clean_state() {
    local run_num=$1 run_dir="$RESULTS_DIR/run_${run_num}"
    log "--- Preparing clean state for run $run_num ---"

    # Restart services — resets the 'exposed' global in the wepn-api Flask process
    wepn-run 1 1
    sleep 20   # 20s: enough for pproxy to re-establish MQTT before test_claim runs

    # Unclaim if needed
    if [ "$(get_claimed)" = "1" ]; then
        log "  Device is claimed — running unclaim"
        pytest wepn-regression.py -vv -k 'test_login or test_unclaim' \
            > "$run_dir/preclaim.log" 2>&1
        wepn-run 1 1
        sleep 20
        wait_for_unclaim || return 1
    fi

    # Clear leftover shadow.db rows from any previously interrupted run
    python3 - <<PYEOF 2>&1 | tee -a "$SUMMARY_FILE"
import sqlite3, sys
try:
    conn = sqlite3.connect("$SHADOW_DB")
    n = conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    if n:
        conn.execute("DELETE FROM servers")
        conn.execute("DELETE FROM periodic")
        conn.commit()
        print(f"  Cleared {n} leftover shadow.db entries")
    conn.close()
except Exception as e:
    print(f"  DB clear skipped: {e}", file=sys.stderr)
PYEOF

    log "  Clean state ready"
    return 0
}

run_once() {
    local run_num=$1
    local run_dir="$RESULTS_DIR/run_${run_num}"
    mkdir -p "$run_dir"

    log "=========================================="
    log "RUN $run_num / $TOTAL_RUNS  ($(date '+%Y-%m-%d %H:%M:%S'))"
    log "=========================================="

    clean_state "$run_num" || {
        log "ERROR: could not establish clean state — skipping run $run_num"
        echo "status=SKIPPED
passed=0
failed=0
retried=0
duration=0
failed_tests=
retried_tests=" > "$run_dir/result.ini"
        return
    }

    local start_ts end_ts duration
    start_ts=$(date +%s)

    pytest wepn-regression.py -vvv -r wfE \
        --retries 2 --retry-delay 30 \
        --html="$run_dir/report.html" \
        --self-contained-html \
        2>&1 | tee "$run_dir/output.log"
    local pytest_exit=${PIPESTATUS[0]}
    # 60s cooldown — prevents OAuth rate limiting across back-to-back runs
    sleep 60

    end_ts=$(date +%s)
    duration=$((end_ts - start_ts))

    # Parse counts from the final summary line
    local summary_line
    summary_line=$(grep -E '[0-9]+ (passed|failed)' "$run_dir/output.log" | tail -1)
    local passed failed retried warnings
    passed=$(echo "$summary_line" | grep -oP '\d+(?= passed)' || true)
    failed=$(echo "$summary_line"  | grep -oP '\d+(?= failed)'  || true)
    retried=$(grep -oP '\d+(?= retried)' "$run_dir/output.log" | tail -1 || true)
    warnings=$(echo "$summary_line" | grep -oP '\d+(?= warning)' || true)

    # Names of failed and retried tests
    local failed_tests retried_tests
    failed_tests=$(grep "^FAILED " "$run_dir/output.log" \
        | sed 's/FAILED wepn-regression\.py:://' \
        | tr '\n' '|' | sed 's/|$//')
    retried_tests=$(grep " RETRY " "$run_dir/output.log" \
        | grep -oP 'wepn-regression\.py::\K\S+' \
        | sort -u | tr '\n' '|' | sed 's/|$//')

    local run_status
    [ "$pytest_exit" -eq 0 ] && run_status="PASS" || run_status="FAIL"

    {
        echo "status=$run_status"
        echo "passed=${passed:-0}"
        echo "failed=${failed:-0}"
        echo "retried=${retried:-0}"
        echo "warnings=${warnings:-0}"
        echo "duration=$duration"
        echo "failed_tests=${failed_tests:-}"
        echo "retried_tests=${retried_tests:-}"
        echo "exit_code=$pytest_exit"
    } > "$run_dir/result.ini"

    log "Run $run_num: $run_status — passed=${passed:-0} failed=${failed:-0} retried=${retried:-0} ${duration}s"
    [ -n "$failed_tests" ]  && log "  Failed:  $failed_tests"
    [ -n "$retried_tests" ] && log "  Retried: $retried_tests"
}

analyze_results() {
    log ""
    log "=========================================="
    log "ANALYSIS"
    log "=========================================="

    python3 - "$RESULTS_DIR" "$TOTAL_RUNS" <<'PYEOF' 2>&1 | tee -a "$SUMMARY_FILE"
import sys, os, re
from collections import defaultdict

results_dir = sys.argv[1]
total_runs  = int(sys.argv[2])

def read_ini(path):
    r = {}
    with open(path) as f:
        for line in f:
            k, _, v = line.strip().partition('=')
            r[k.strip()] = v.strip()
    return r

runs = []
for i in range(1, total_runs + 1):
    p = os.path.join(results_dir, f"run_{i}", "result.ini")
    if os.path.exists(p):
        r = read_ini(p)
        r['run'] = i
        runs.append(r)

if not runs:
    print("No completed runs.")
    sys.exit(0)

n = len(runs)
passes  = sum(1 for r in runs if r.get('status') == 'PASS')
skipped = sum(1 for r in runs if r.get('status') == 'SKIPPED')
fails   = n - passes - skipped

# ── Run-by-run table ──────────────────────────────────────────────
print(f"\nOverall: {passes}/{n} runs fully passed  ({fails} failed, {skipped} skipped)\n")
print(f"  {'Run':>4}  {'Status':8}  {'Pass':>5}  {'Fail':>5}  {'Retry':>5}  {'Duration':>8}")
print(f"  {'----':>4}  {'--------':8}  {'-----':>5}  {'-----':>5}  {'-----':>5}  {'--------':>8}")
for r in runs:
    d = int(r.get('duration', 0))
    print(f"  {r['run']:>4}  {r.get('status','?'):8}  "
          f"{r.get('passed','?'):>5}  {r.get('failed','?'):>5}  "
          f"{r.get('retried','?'):>5}  {d//60:>3}m{d%60:02d}s")

# ── Failure frequency ─────────────────────────────────────────────
all_failures = defaultdict(list)
all_retries  = defaultdict(list)
for r in runs:
    for t in r.get('failed_tests', '').split('|'):
        t = t.strip().split(' ')[0]   # strip trailing status markers
        if t:
            all_failures[t].append(r['run'])
    for t in r.get('retried_tests', '').split('|'):
        t = t.strip()
        if t:
            all_retries[t].append(r['run'])

print(f"\nTest failure rates (across {n} runs):")
if all_failures:
    for test, fruns in sorted(all_failures.items(), key=lambda x: -len(x[1])):
        pct = len(fruns) / n * 100
        tag = "ALWAYS " if pct == 100 else ("FREQUENT" if pct >= 50 else "FLAKY   ")
        print(f"  [{tag}] {test}: {len(fruns)}/{n} ({pct:.0f}%)  runs={fruns}")
else:
    print("  None — all tests passed in every run.")

print(f"\nRetry rates (across {n} runs):")
if all_retries:
    for test, rruns in sorted(all_retries.items(), key=lambda x: -len(x[1])):
        pct = len(rruns) / n * 100
        print(f"  {test}: retried in {len(rruns)}/{n} ({pct:.0f}%)  runs={rruns}")
else:
    print("  None — no retries needed in any run.")

# ── Timing ────────────────────────────────────────────────────────
durations = [int(r.get('duration', 0)) for r in runs if r.get('status') != 'SKIPPED']
if durations:
    avg = sum(durations) / len(durations)
    mn, mx = min(durations), max(durations)
    print(f"\nRun duration:  avg={avg/60:.1f}m  min={mn//60}m{mn%60:02d}s  max={mx//60}m{mx%60:02d}s")

# ── Suggestions ───────────────────────────────────────────────────
print(f"\n{'='*55}")
print("AREAS FOR IMPROVEMENT")
print(f"{'='*55}")
s = []

if passes < n:
    s.append(f"Pass rate {passes}/{n} ({passes/n*100:.0f}%) — investigate root causes before reducing retries.")

for test, fruns in sorted(all_failures.items(), key=lambda x: -len(x[1])):
    pct = len(fruns) / n * 100
    if pct == 100:
        s.append(f"'{test}' failed every run — likely a real bug or environment problem; fix before shipping.")
    elif pct >= 50:
        s.append(f"'{test}' failed {pct:.0f}% of runs — high flakiness; review assertions and timing.")
    else:
        s.append(f"'{test}' is occasionally flaky ({pct:.0f}%) — consider a targeted retry or tighter preconditions.")

for test, rruns in sorted(all_retries.items(), key=lambda x: -len(x[1])):
    pct = len(rruns) / n * 100
    if pct >= 70:
        s.append(f"'{test}' needs retries {pct:.0f}% of the time — increase wait_until timeout or add a dedicated delay.")
    elif pct >= 40:
        s.append(f"'{test}' retried in {pct:.0f}% of runs — marginal timing; check what it's waiting for.")

if durations:
    avg = sum(durations) / len(durations)
    if avg > 400:
        s.append(f"Average run time is {avg/60:.1f}m — the 30s retry delays are the main contributor; profile where time is spent.")

if not s:
    s.append("All runs passed cleanly — suite looks stable.")

for line in s:
    print(f"  • {line}")
PYEOF
}

# ── Main ───────────────────────────────────────────────────────────────────────
log "WEPN Regression Debug Runner"
log "Runs: $TOTAL_RUNS | Results dir: $RESULTS_DIR"
log "Started: $(date)"
log ""

for i in $(seq 1 "$TOTAL_RUNS"); do
    run_once "$i"
done

log ""
log "=========================================="
log "RUN SUMMARY"
log "=========================================="
total_p=0 total_f=0
for i in $(seq 1 "$TOTAL_RUNS"); do
    result="$RESULTS_DIR/run_${i}/result.ini"
    [ -f "$result" ] || continue
    p=$(grep "^passed=" "$result" | cut -d= -f2)
    f=$(grep "^failed=" "$result"  | cut -d= -f2)
    total_p=$((total_p + ${p:-0}))
    total_f=$((total_f + ${f:-0}))
done
log "Cumulative individual-test counts: passed=$total_p  failed=$total_f"
log "Completed: $(date)"

analyze_results

echo ""
echo "Done. Results: $RESULTS_DIR"
echo "Summary:  $SUMMARY_FILE"
