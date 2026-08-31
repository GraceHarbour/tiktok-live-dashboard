#!/usr/bin/env bash
set -u
reader_dir=/home/graceharbourmedia/creator-reader
lock_file=$reader_dir/data/maintenance-rate.lock
if [ "${1:-}" != "--locked" ]; then
  exec flock -n -o "$lock_file" "$0" --locked
fi
while ! (python3 "$reader_dir/capture_maintenance_rate_firefox.py" && python3 "$reader_dir/import_maintenance_rate_firefox.py") >>"$reader_dir/data/maintenance-rate.log" 2>&1; do
  date -u '+%FT%TZ maintenance refresh failed; retrying in 5 minutes' >>"$reader_dir/data/maintenance-rate.log"
  sleep 300
done
date -u '+%FT%TZ' >"$reader_dir/data/maintenance-rate-success.txt"
