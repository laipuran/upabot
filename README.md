# DuckBot

基于 **AstrBot + NapCat / OneBot v11** 的 QQ 机器人。业务插件直接执行，不经过 Agent：

- 群聊、私聊自动将 Apple Music 链接转换为网易云音乐链接。
- 私聊 `/upa status` 查询设备状态。
- 私聊 `/taboo`（别名 `/tb`）注册关键词，群内命中时私信发送最近聊天记录图片。

AI 聊天、模型、人格和唤醒方式由 AstrBot WebUI 管理；未配置模型时也可以使用上述功能。本阶段不额外实现音乐分享的 LLM 上下文。

三个业务分别由 **Music Converter**、**Device Commands**、**Taboo** 插件提供，可以在 AstrBot 中独立启停和重载。各插件管理自己的依赖和资源；Taboo 数据库初始化失败不会阻止另外两个插件初始化。

## 快速开始

```bash
cp .env.example .env
docker network create duckbot-qq
docker compose -f docker-compose.napcat.yml pull --policy always napcat
docker compose -f docker-compose.napcat.yml up -d napcat
docker compose pull --policy always astrbot
docker compose up -d
```

按[部署指南](docs/deploy.md)打开本机 6185 / 6099 面板，配置 AstrBot OneBot 实例和 NapCat 的 `ws://astrbot:6199/ws` 连接。管理端口默认只绑定回环地址，远程使用 SSH 隧道。

两个 Compose 项目独立运行：`docker-compose.napcat.yml` 只管理 NapCat，默认 `docker-compose.yml` 只管理 AstrBot，共用外部网络 `duckbot-qq`。网络已存在时不要重复创建。两个项目都不会在启动时自动拉取镜像。

从旧单项目部署迁移时，需要按[部署指南](docs/deploy.md)进行一次性容器重建，保留 QQ 登录态和旧数据库；不导入旧 Taboo 订阅。不要直接覆盖现有 `.env` 或 NapCat 账号配置。

## 调试只重启核心

修改本机挂载的插件代码后：

```bash
docker compose restart astrbot
docker compose logs -f astrbot
```

修改核心 Compose 或环境变量后，执行 `docker compose up -d --no-deps --force-recreate astrbot`。停止核心可用 `docker compose down`，不会停止 NapCat 或删除外部网络。核心离线期间不处理消息，也不保证补发；恢复后由 NapCat 重连，不需要因重启核心重新登录 QQ。

从合并的 `astrbot_plugin_duckbot` 升级到三个插件时，挂载发生变化，必须重建核心，不能只执行 `restart`。Taboo 继续使用原 `astrbot/data/plugin_data/astrbot_plugin_duckbot/taboo.sqlite3`，已有订阅和开关不需要迁移或重建；完整注意事项见[部署指南](docs/deploy.md#从合并插件升级)。

远程更新运行 `scripts/deploy.sh`：只同步核心源码并重建 AstrBot，不拉取镜像、不操作 NapCat。显式升级 AstrBot 使用 `scripts/deploy.sh --upgrade`。NapCat 的启动、停止和升级始终使用独立的 Compose 文件。

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
docker-compose.yml        # duckbot-core：仅 AstrBot
docker-compose.napcat.yml # duckbot-napcat：仅 QQ 接入
.env                     # 环境变量
napcat/config/           # NapCat 配置
plugins/
  ├── astrbot_plugin_music_converter/ # 链接检测、音乐转换和 HTTP 资源
  ├── astrbot_plugin_device_commands/ # /upa 命令、设备查询和 HTTP 资源
  └── astrbot_plugin_taboo/           # /tb、持久化、分群缓存和图片通知
astrbot/data/            # AstrBot 运行数据（不提交）
tests/                   # 单元测试
scripts/deploy.sh        # 只同步并重建核心，NapCat 不受影响
```

每个插件都有独立的 `main.py`、`metadata.yaml` 和 `requirements.txt`，不依赖另外两个插件的 Python 模块。
