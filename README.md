# DuckBot

基于 **AstrBot + NapCat / OneBot v11** 的 QQ 机器人。业务插件直接执行，不经过 Agent：

- 群聊、私聊自动将 Apple Music 链接转换为网易云音乐链接。
- 私聊 `/upa status` 查询设备状态。
- 私聊 `/taboo`（别名 `/tb`）注册关键词，群内命中时私信发送最近聊天记录图片。
- `/电费`（别名 `/elec`）交互选房，按私聊/群聊绑定房间，查询电费并配置低余额报警。详见[电费插件](docs/electricity.md)。

AI 聊天、模型、人格和唤醒方式由 AstrBot WebUI 管理；未配置模型时也可以使用上述功能。本阶段不额外实现音乐分享的 LLM 上下文。

可选语音由 **Voice Tool** 插件注册 `speak(text)` 工具，使用当前会话的 TTS 提供商，音色仍在 WebUI 配置。配置、降级行为与验证方式见[语音工具](docs/voice-tool.md)。

戳一戳和消息贴表情由本地待部署的 **QQ Interaction Memory** 提供：第一部分自动附加互动上下文、被戳当场触发文字回复；第二部分提供独立开关的戳人和贴表情聊天工具。表情表只在本地查找，不整表注入上下文。总开关默认关闭，未加入部署挂载，详见[QQ 互动指南](docs/qq-interactions.md)。

四个业务分别由 **Music Converter**、**Device Commands**、**Taboo**、**Electricity** 插件提供，可以在 AstrBot 中独立启停和重载，各自管理依赖和资源。电费插件仅保存对话绑定、报警设置和去重状态，余额数据直接查询后端，不另建缓存。

## 快速开始

```bash
cp .env.example .env # 仅首次安装，不覆盖已有密钥
docker network create duckbot-qq
docker compose -f docker-compose.napcat.yml pull --policy always napcat
docker compose -f docker-compose.napcat.yml up -d napcat
bash scripts/install-host.sh
```

先安装 uv 并准备 `.env`，完整步骤见[部署指南](docs/deploy.md)。AstrBot 及插件在宿主机运行，由 `duckbot-astrbot.service` 管理；NapCat 保留在原容器。NapCat 连接宿主机 Docker 网桥上的 OneBot 服务，例如 `ws://172.19.0.1:6199/ws`（以实际网络网关为准）。

`docker-compose.napcat.yml` 只管理 NapCat，`duckbot-qq` 网络需保留。旧核心配置改名为 `docker-compose.legacy.yml`，仅供回退，不要与裸机服务同时启动。

从旧核心容器迁移时，按[部署指南](docs/deploy.md)先备份、停止旧核心，再复用 `astrbot/data`；不重建 NapCat，不覆盖账号配置。

## 调试只重启核心

修改插件代码或环境变量后：

```bash
sudo systemctl restart duckbot-astrbot
sudo journalctl -u duckbot-astrbot -f
```

停止核心使用 `sudo systemctl stop duckbot-astrbot`，不会停止 NapCat。核心离线期间不处理消息，也不保证补发；恢复后由 NapCat 重连，不需要重新登录 QQ。

Taboo 继续使用原 `astrbot/data/plugin_data/astrbot_plugin_duckbot/taboo.sqlite3`，已有订阅和开关不需要迁移。不要删除这个兼容数据目录。

最新代码先提交并推送，再运行 `bash scripts/deploy.sh`：远端以 fast-forward 拉取 `origin/main`，按 `uv.lock` 安装依赖并重启裸机核心；工作区有改动则拒绝覆盖。不会操作 NapCat。AstrBot 升级需显式修改依赖版本与锁文件，不再使用 `--upgrade` 或镜像标签。

## 本地单元测试

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。开发依赖包含固定版本的真实 AstrBot SDK，不用假 SDK 替代插件接口。

```bash
uv sync --locked --python 3.12
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```

测试替换外部 HTTP 和 QQ 发送，不启动机器人、不调用模型、不连接真实 QQ。测试的 AstrBot 运行文件放在 `/tmp/opencode`（存在时）或系统临时目录，不使用实际部署数据。

## 项目结构

```
docker-compose.legacy.yml # 旧核心容器配置，仅供回退
docker-compose.napcat.yml # duckbot-napcat：仅 QQ 接入
.env                     # 环境变量
napcat/config/           # NapCat 配置
plugins/
  ├── astrbot_plugin_music_converter/ # 链接检测、音乐转换和 HTTP 资源
  ├── astrbot_plugin_device_commands/ # /upa 命令、设备查询和 HTTP 资源
  ├── astrbot_plugin_taboo/           # /tb、持久化、分群缓存和图片通知
  ├── astrbot_plugin_electricity/     # /elec、交互选房、查询和低余额报警
  ├── astrbot_plugin_voice_tool/      # speak 工具、会话 TTS、语音发送和临时文件清理
  └── astrbot_plugin_qq_reaction_listener/ # 互动上下文、被戳回复、戳/贴工具（待部署）
astrbot/data/            # AstrBot 运行数据（不提交）
tests/                   # 单元测试
scripts/deploy.sh        # 远端拉取 main 并部署裸机核心
scripts/install-host.sh  # uv 锁定依赖、链接插件、安装 systemd 服务
```

每个插件都有独立的 `main.py`、`metadata.yaml` 和 `requirements.txt`，不依赖其他业务插件的 Python 模块。
