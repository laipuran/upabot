#!/usr/bin/env bash
set -euo pipefail

UPGRADE=false
if [[ $# -gt 1 ]]; then
    echo "用法: $0 [--upgrade]（仅升级 AstrBot，不操作 NapCat）" >&2
    exit 2
fi
case "${1:-}" in
    "") ;;
    --upgrade) UPGRADE=true ;;
    *)
        echo "用法: $0 [--upgrade]（仅升级 AstrBot，不操作 NapCat）" >&2
        exit 2
        ;;
esac

PI_USER="${PI_USER:-puranlai}"
PI_HOST="${PI_HOST:-192.168.31.119}"
PI_DIR="${PI_DIR:-/home/puranlai/Code/upabot}"
REMOTE="${PI_USER}@${PI_HOST}"
cd "$(dirname "$0")/.."
ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/duckbot-deploy.XXXXXX.tar.gz")"
trap 'rm -f -- "$ARCHIVE"' EXIT
printf -v REMOTE_DIR '%q' "$PI_DIR"

echo "==> 1/3 打包核心 Compose 和插件（不含 NapCat、凭据和运行数据）"
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$ARCHIVE" \
    docker-compose.yml .env.example plugins

echo "==> 2/3 同步到 ${REMOTE}:${PI_DIR}"
ssh "$REMOTE" "mkdir -p -- $REMOTE_DIR"
scp "$ARCHIVE" "${REMOTE}:${PI_DIR}/.duckbot-deploy.tar.gz"

echo "==> 3/3 更新并重建 AstrBot（不停止或更新 NapCat）"
ssh "$REMOTE" "bash -s -- $REMOTE_DIR $UPGRADE" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$1"
if [[ ! -f .env ]]; then
    echo "请先在目标目录创建 .env；脚本不会自动生成或覆盖凭据。" >&2
    exit 1
fi
# A legacy combined project must be split explicitly, not silently adopted or removed.
if [[ -f docker-compose.yml ]]; then
    services="$(docker compose -f docker-compose.yml config --services)"
    if grep -Eq '^(duckbot|napcat)$' <<< "$services"; then
        echo "检测到旧版单项目部署，请先按 docs/deploy.md 完成一次性拆分。" >&2
        exit 1
    fi
fi
if ! docker network inspect duckbot-qq >/dev/null 2>&1; then
    echo "共享网络 duckbot-qq 不可用，请先按 docs/deploy.md 准备网络。" >&2
    exit 1
fi
tar -xzf .duckbot-deploy.tar.gz
if [[ "$2" == true ]]; then
    docker compose -p duckbot-core -f docker-compose.yml pull --policy always astrbot
fi
# Recreate even for source-only updates, so the Python module is actually reloaded.
docker compose -p duckbot-core -f docker-compose.yml up -d \
    --no-deps --force-recreate --pull never astrbot
rm -f -- .duckbot-deploy.tar.gz
REMOTE_SCRIPT

echo "==> AstrBot 已重建；NapCat 未操作，连接将在下游就绪后自动重连。"
