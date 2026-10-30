#!/usr/bin/env bash
# Run as the deployment user, not root. sudo is used only for systemd installation.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"
UV="${UV_BIN:-$(command -v uv || true)}"
[[ -x "$UV" ]] || { echo "需要先安装 uv 到标准 PATH（推荐 /usr/local/bin/uv）。" >&2; exit 1; }
[[ -f .env ]] || { echo "缺少 .env；不会自动生成或覆盖凭据。" >&2; exit 1; }
if docker inspect duckbot-astrbot --format '{{.State.Running}}' 2>/dev/null | grep -qx true; then
    echo "旧 AstrBot 容器仍在运行。请先备份数据并完成 docs/deploy.md 中的首次迁移。" >&2
    exit 1
fi
sudo -n true
"$UV" sync --locked --no-dev --group runtime --python 3.12
mkdir -p astrbot/data/plugins
touch astrbot/.astrbot
# Only the explicitly deployed plugins are linked. QQ interaction remains opt-in.
for plugin in music_converter device_commands taboo electricity voice_tool; do
    name="astrbot_plugin_${plugin}"
    target="$ROOT/astrbot/data/plugins/$name"
    if [[ -d "$target" && ! -L "$target" ]]; then
        # Container bind mounts leave empty directories behind. Never delete actual plugin files.
        rmdir "$target" || { echo "拒绝覆盖非空插件目录: $target" >&2; exit 1; }
    fi
    if [[ -e "$target" && ! -L "$target" ]]; then
        echo "拒绝覆盖插件路径: $target" >&2; exit 1
    fi
    ln -sfn "$ROOT/plugins/$name" "$target"
done
UNIT="$(mktemp)"
trap 'rm -f -- "$UNIT"' EXIT
cat > "$UNIT" <<EOF
[Unit]
Description=DuckBot AstrBot (native host runtime)
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=simple
User=$(id -un)
Group=$(id -gn)
WorkingDirectory=$ROOT/astrbot
EnvironmentFile=$ROOT/.env
Environment=TZ=Asia/Shanghai
Environment=PYTHONUNBUFFERED=1
ExecStart=$ROOT/.venv/bin/astrbot run
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
sudo -n install -m 644 "$UNIT" /etc/systemd/system/duckbot-astrbot.service
sudo -n systemctl daemon-reload
sudo -n systemctl enable duckbot-astrbot.service
sudo -n systemctl restart duckbot-astrbot.service
"$ROOT/.venv/bin/python" - "$ROOT" <<'PY'
import json
import sys
import time
import urllib.request
from pathlib import Path

config_path = Path(sys.argv[1]) / "astrbot/data/cmd_config.json"
for attempt in range(60):
    if config_path.exists():
        break
    time.sleep(1)
else:
    sys.exit("AstrBot 未生成配置；请检查 journalctl。")
config = json.loads(config_path.read_text(encoding="utf-8-sig"))
dashboard = config.get("dashboard", {})
if not dashboard.get("enable", True):
    sys.exit("Dashboard 未启用；请通过 journalctl 手动确认启动状态。")
host = dashboard.get("host", "127.0.0.1")
if host == "0.0.0.0":
    host = "127.0.0.1"
url = f"http://{host}:{dashboard.get('port', 6185)}/"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(60):
    try:
        with opener.open(url, timeout=2) as response:
            if response.status == 200:
                print("AstrBot WebUI 就绪。")
                break
    except OSError:
        pass
    time.sleep(1)
else:
    sys.exit("AstrBot WebUI 健康检查失败；查看 sudo journalctl -u duckbot-astrbot。")
PY
sudo -n systemctl is-active --quiet duckbot-astrbot.service
echo "AstrBot 已交给 systemd；仍需确认插件和 OneBot 日志，NapCat 未操作。"
