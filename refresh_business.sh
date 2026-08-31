#!/usr/bin/env bash
set -u
reader_dir=/home/graceharbourmedia/creator-reader
lock_file=$reader_dir/data/business-essentials.lock
if [ "${1:-}" != "--locked" ]; then
  exec flock -n -o "$lock_file" "$0" --locked
fi
while ! "$reader_dir/sync_business.sh" >>"$reader_dir/data/business-essentials.log" 2>&1; do
  date -u '+%FT%TZ business refresh failed; retrying in 5 minutes' >>"$reader_dir/data/business-essentials.log"
  sleep 300
done
date -u '+%FT%TZ' >"$reader_dir/data/business-essentials-success.txt"
