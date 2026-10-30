# 部署：裸机 AstrBot + 容器 NapCat

AstrBot v4.29.3 和本仓库插件在宿主机的 Python 3.12 虚拟环境运行，由 `duckbot-astrbot.service` 管理。NapCat 继续使用现有容器、QQ 登录态和 `duckbot-qq` 网络。核心更新不重建、不升级、不重启 NapCat。

## 首次安装

需要 Linux、systemd、Git、Docker Compose、Python venv，以及部署用户可用的 sudo。以下命令以部署用户运行，不要用 root 克隆仓库。

```bash
# 管理员安装官方 uv 到标准系统路径；先下载并检查安装脚本。
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
less /tmp/uv-install.sh
sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh /tmp/uv-install.sh
uv --version
sudo apt-get install ffmpeg fonts-wqy-microhei # 语音转换与中文图片字体
git clone https://github.com/laipuran/upabot.git
cd upabot
cp .env.example .env # 仅全新安装；已有 .env 不要覆盖
# 编辑 .env，设置后端地址等；模型密钥在原 WebUI 中配置。
bash scripts/install-host.sh
```

安装脚本使用 `uv sync --locked --no-dev --group runtime --python 3.12`，uv 必要时下载 Python。运行依赖包含现有 Edge TTS 提供商所需的 `edge-tts`，宿主机还需 FFmpeg 和中文字体。无法直连 PyPI/GitHub 时，在安装命令的环境中配置可用的 `HTTPS_PROXY`，不要提交代理凭据。安装失败会中止，不会自动更新锁文件。树莓派首次冷启动可能超过一分钟，健康检查最多等待 180 次。

插件以符号链接放到 `astrbot/data/plugins/`，目前部署 Music Converter、Device Commands、Taboo、Electricity、Voice Tool；QQ Interaction Memory 保持原先的待部署/显式启用状态，不会因为这次换部署方式自动开放互动行为。遇到同名非空目录时安装脚本拒绝覆盖，需人工核对。不要在 WebUI 更新或卸载这些本地插件。

如果是新装 NapCat：按 `.env.example` 配置账号与管理面板，创建 `duckbot-qq` 外部网络，再使用 `docker-compose.napcat.yml` 显式拉取和启动。已有 NapCat 不执行这一步。

## OneBot 连接与网络

查看真实 Docker 网桥网关：

```bash
docker network inspect duckbot-qq --format '{{json .IPAM.Config}}'
```

例如当前服务器为 `172.19.0.1`。AstrBot WebUI 的原 OneBot v11 实例保持原 ID、6199 端口和 token，监听网桥地址 `172.19.0.1`；NapCat 当前账号的原 WebSocket 客户端改成 `ws://172.19.0.1:6199/ws`。保留 `array` 消息格式、匹配的 token、关闭自身消息上报。只修改已有连接，不能新建重复连接。不要把 NapCat 容器内的 `127.0.0.1` 当作宿主机。

在 NapCat WebUI 保存网络配置可热更新连接，无需重建容器或重新登录 QQ。仅改仓库模板 `napcat/config/onebot11.json` 不会改变当前账号的生效配置。若 Docker 网络日后重建、网关变化，需要同步更新两端地址；不要在仍使用时删除该网络。

裸机 WebUI 的监听地址由 `astrbot/data/cmd_config.json` 的 dashboard 配置决定，不再由 Compose 的 `ASTRBOT_WEBUI_BIND` 控制。推荐绑定 `127.0.0.1:6185`，使用 SSH 隧道：

```bash
ssh -N -L 6185:127.0.0.1:6185 -L 6099:127.0.0.1:6099 puranlai@192.168.31.119
```

不要无鉴权公开面板或 OneBot。设备、电费后端若在同一宿主机，`.env` 中分别使用 `http://127.0.0.1:5000/DeviceStatus`、`http://127.0.0.1:5051`。systemd 从 `.env` 注入变量，不要在其中使用 shell 命令或变量展开。

## 从旧 AstrBot 容器一次性迁移

1. 确认本地代码已提交推送，服务器工作区干净，以 `git pull --ff-only` 拉取最新版。记录原容器镜像 ID、挂载、重启策略，以及 NapCat 当前连接配置。不要操作 NapCat 生命周期。
2. 先安装 uv 并同步锁定运行依赖，准备好裸机运行环境，减少停机时间。
3. 仅停止旧核心：`docker stop duckbot-astrbot`。禁用其自动重启：`docker update --restart=no duckbot-astrbot`。停止后备份 `.env`、完整 `astrbot/data/`、`napcat/config/`，备份存入权限为 700 的时间戳目录；SQLite 必须在停止核心后备份。
4. 旧镜像可能生成 root 所有的文件，将 `astrbot/data` 的所有权交给部署用户；不要更改 NapCat 登录态或其他服务数据。
5. 保留原 `astrbot/data`，把 dashboard host 改为回环地址、OneBot host 改为真实网桥网关。保持平台实例 ID、模型、人格、token、数据库和插件配置不变。修改 `.env` 中原 `host.docker.internal` 后端地址为宿主机地址。
6. 运行 `bash scripts/install-host.sh`。旧只读插件挂载留下的空目录会替换为符号链接；非空目录不自动覆盖。保留 `plugin_data/astrbot_plugin_duckbot`，Taboo 继续读其中的旧库。
7. 在 NapCat 面板热更新原客户端 URL，确认裸机进程稳定、WebUI 正常、插件加载、OneBot 重连成功。测试不会自动发 QQ 消息或调用模型，实际业务回复需人工验收。
8. 成功后可删除已停止的旧容器：`docker rm duckbot-astrbot`，然后仅对原先记录的、已无容器使用的核心镜像执行 `docker image rm <原镜像ID>`。不执行全局 `docker system prune`、卷清理或 NapCat/OpenWrt 镜像清理。

核心离线期间不保证消息补发；订阅、会话和模型配置复用原数据，但进程内最近聊天缓存会清空。旧 NoneBot `data/` 不导入也不删除。

## 日常更新

```bash
# 开发机：先提交、推送 main，再部署
git push origin main
bash scripts/deploy.sh

# 服务器查看状态、日志
sudo systemctl status duckbot-astrbot
sudo journalctl -u duckbot-astrbot -f
```

远程目标由 `PI_USER`、`PI_HOST`、`PI_DIR` 覆盖，默认 `puranlai@192.168.31.119:/home/puranlai/Code/upabot`。脚本只接受干净的 main 分支，只 fast-forward 拉取，不自动推送、不覆盖凭据、不修改 NapCat。若旧核心容器还在运行则拒绝启动第二套核心。

只改插件代码或 `.env` 时，可以直接 `sudo systemctl restart duckbot-astrbot`；停止用 `sudo systemctl stop duckbot-astrbot`。更新依赖时修改 `pyproject.toml`、刷新并提交 `uv.lock`。不再通过拉取 AstrBot Docker 镜像升级。

## 回退

部署失败时不要启动双核心。先停裸机服务，再恢复停机备份中的配置、原 `.env` 和 NapCat 连接地址。仍保留旧容器时可恢复它的重启策略并启动；已清理时使用 `docker-compose.legacy.yml`（显式拉取原镜像后）重建旧核心。插件符号链接指向宿主机绝对路径，容器回退前需要还原旧插件空挂载点/目录，避免悬空链接。不得同时运行裸机服务与旧核心容器，也不要删除数据来解决启动错误。

NapCat 的显式维护仍仅使用 `docker compose -f docker-compose.napcat.yml ...`，与核心部署分开。
