# DuckBot 当前架构

项目已从 NoneBot2 迁到 AstrBot，QQ 接入继续使用原有 NapCat。迁移后只有一套应用框架，不运行双框架消息分流。

```text
QQ ↔ NapCat ↔ OneBot v11 反向 WebSocket ↔ AstrBot
                                         ├─ DuckBot 本地插件
                                         └─ 内置 LLM / Agent（按需配置）
```

DuckBot 插件提供音乐转换、设备状态、Taboo 命令和关键词通知。确定性功能直接执行代码；监控先于命令和音乐转换运行，避免业务处理终止事件后漏掉群消息。业务命令和音乐转换会禁止本次默认模型调用，普通消息不会被插件统一终止。

本阶段不增加工具/MCP、音乐分享上下文注入、群摘要或历史数据库导入。旧 NoneBot 数据保留在原目录，不覆盖或自动迁移；Taboo 新订阅存入 `astrbot/data/plugin_data/astrbot_plugin_duckbot/taboo.sqlite3`，按平台实例隔离。群缓存只在内存中保存，每群最多 50 条、通知使用最近 10 条。

部署直接使用固定版本 `soulter/astrbot:v4.29.3`，官方镜像支持 Linux amd64 和 arm64。插件源码以只读卷挂载到 AstrBot 插件目录，数据单独持久化；不用维护自制镜像或 QEMU 构建链。部署与首次配置见 [docs/deploy.md](docs/deploy.md)。

QQ 接入和核心代码采用独立生命周期：`docker-compose.napcat.yml` 的项目名为 `duckbot-napcat`，`docker-compose.yml` 的项目名为 `duckbot-core`。两个项目仅共享外部网络 `duckbot-qq`，不声明跨项目启动依赖，也不会在 `down` 时删除共享网络。NapCat 通过 `ws://astrbot:6199/ws` 连接核心，核心重建后自动重连。日常部署只重建核心，不拉取镜像、不停止 NapCat；升级必须显式执行。拆分旧项目时需要一次性重建容器以更换 Compose 项目标记，不代表每次部署都要重建 NapCat。

新增功能应优先复用 AstrBot 的配置、消息和模型接口。只有明确需要自然语言调用时，才额外注册 Agent 工具；普通命令不应为了执行功能先调用模型。插件依赖同时记录在 `pyproject.toml` 和插件的 `requirements.txt`，更新后刷新 `uv.lock` 并运行单元测试。
