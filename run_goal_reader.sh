#!/usr/bin/env bash
set -euo pipefail
reader_dir="/home/graceharbourmedia/creator-reader"
profile_dir="$reader_dir/data/backstage-login-direct-profile"
status_file="$reader_dir/data/goal-reader-status.json"
log_file="$reader_dir/data/goal-reader-last.log"
lock_file="$reader_dir/data/goal-reader.lock"
mkdir -p "$reader_dir/data" "$profile_dir"
exec 9>"$lock_file"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
goal_run_started_et="$(TZ=America/New_York date +%Y-%m-%dT%H:%M:%S%:z)"
if ! flock -n 9; then
  printf '{"state":"busy","started_at":"%s"}\n' "$started_at" > "$status_file"
  exit 0
fi
state="failed"
: > "$log_file"
for attempt in 1 2 3; do
  printf "%s Goal refresh attempt %s of 3\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$attempt" >> "$log_file"
  if BACKSTAGE_BROWSER_PROFILE="$profile_dir" timeout 600 "$reader_dir/sync.sh" >> "$log_file" 2>&1; then
    state="success"
    break
  fi
  if [ "$attempt" -lt 3 ]; then
    printf "%s Goal refresh failed; retrying in 60 seconds\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$log_file"
    sleep 60
  fi
done
if [ "$state" = "success" ]; then
  timeout 180 "$reader_dir/.venv/bin/python" "$reader_dir/hourly_goal_metrics.py" >> "$log_file" 2>&1 || echo "Hourly Goal metrics refresh failed; retained last verified values." >> "$log_file"
  GOAL_RUN_STARTED_ET="$goal_run_started_et" timeout 180 "$reader_dir/.venv/bin/python" "$reader_dir/firefox-source/monthly_goal_rollover.py" >> "$log_file" 2>&1 || \
    echo "Month-end goal rollover failed; retained prior verified goals." >> "$log_file"
fi
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"state":"%s","started_at":"%s","finished_at":"%s"}\n' "$state" "$started_at" "$finished_at" > "$status_file"
[ "$state" = "success" ]
