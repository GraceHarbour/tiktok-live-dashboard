#!/usr/bin/env bash
set -euo pipefail
cd /home/graceharbourmedia/creator-reader
exec ./.venv/bin/python backstage_session.py --source goals --goal-view creators --keep-session --headless --no-prompt --publish-url https://dashboard.graceharbourmedia.com/internal/backstage/snapshot --sync-secret-file sync.secret --iap-audience /projects/821521586230/global/backendServices/1872090425312325376
