#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "用法: $0（远端 fast-forward 拉取 main，以 uv.lock 部署裸机 AstrBot）" >&2
    exit 2
fi
PI_USER="${PI_USER:-puranlai}"
PI_HOST="${PI_HOST:-192.168.31.119}"
PI_DIR="${PI_DIR:-/home/puranlai/Code/upabot}"
printf -v REMOTE_DIR '%q' "$PI_DIR"

# Push explicitly before deployment; never publish uncommitted/local work implicitly.
ssh "${PI_USER}@${PI_HOST}" "bash -s -- $REMOTE_DIR" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$1"
if [[ -n "$(git status --porcelain)" ]]; then
    echo "远端工作区不干净，拒绝覆盖。请先检查 git status。" >&2
    exit 1
fi
[[ "$(git branch --show-current)" == main ]] || { echo "远端必须在 main 分支" >&2; exit 1; }
git fetch origin main
git merge --ff-only origin/main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || { echo "远端 main 含未推送提交，拒绝部署。" >&2; exit 1; }
# Reuse the repository's optional download proxy for uv, not for the running bot.
PROXY="$(git config --get http.proxy || true)"
if [[ -n "$PROXY" ]]; then export HTTPS_PROXY="$PROXY"; fi
bash scripts/install-host.sh
REMOTE_SCRIPT
