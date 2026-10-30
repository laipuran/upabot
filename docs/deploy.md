# 部署 AstrBot + NapCat

NapCat 常驻，AstrBot 与插件独立更新。`docker-compose.napcat.yml` 只管理 QQ 接入，默认 `docker-compose.yml` 只管理核心；即使对核心执行 `down`，也不会停止 NapCat。两个项目通过外部网络 `duckbot-qq` 通信，不声明启动依赖。

核心直接运行官方 AstrBot v4.29.3 镜像，以只读卷挂载本地插件。默认只在宿主机回环地址开放管理面板，OneBot 6199 不发布到宿主机。两个项目设置 `pull_policy: never`，启动和调试不自动下载新镜像；首次安装或升级必须显式 `pull`。

## 1. 准备配置，保护原有数据

需要 Docker Compose 和一个可用的 QQ 账号。首次部署复制 `.env.example` 为 `.env`，设置 `DEVICE_STATUS_API_URL`。管理员、唤醒前缀、模型等在 AstrBot WebUI 配置，不再使用 NoneBot 的 `SUPERUSERS`、`NICKNAME`。

从旧版切换时先备份旧 Compose、`.env`、`napcat/config/`、`ntqq/` 和 `data/`。保留原来的挂载目录和 QQ 登录态，不要重复启动第二个 NapCat。若从新 worktree 部署，运行目录本身不带这些被 Git 忽略的数据，必须明确复用原服务器的挂载目录，不能把空目录误当成旧登录态。

旧版单项目中的容器不能直接交给新 Compose 项目管理。**第一次拆分需要一次 NapCat 重建**：在旧部署目录、旧 Compose 仍生效时执行 `docker compose down`（不要加 `-v`），再换入新的两个 Compose 文件，按下一节分别启动。这会有短暂离线，但不会删除上述绑定目录。已拆分的部署以后不再执行这一步，日常更新脚本也不会代你停止旧项目。不要通过 `--remove-orphans` 尝试跨项目接管容器。

AstrBot 使用新的 `astrbot/data/`，不会打开或覆盖旧 `data/db.sqlite3`。本次不导入旧 Taboo 订阅，迁移后重新注册关键词即可。原 `.env` 可以保留设备 API 配置，按 `.env.example` 补充新变量，不要用示例覆盖已有密钥。

## 2. 分别启动 QQ 接入和核心

以下命令在同一个部署目录执行，两个文件通过各自的 `name` 使用不同项目名。不要把两个文件用 `-f ... -f ...` 合并，也不要用同一个 `-p` 或 `COMPOSE_PROJECT_NAME` 覆盖两个项目。

先准备外部网络（仅创建一次；已存在则跳过）：

```bash
docker network create duckbot-qq
```

独立启动 NapCat：

```bash
docker compose -f docker-compose.napcat.yml pull --policy always napcat
docker compose -f docker-compose.napcat.yml up -d napcat
docker compose -f docker-compose.napcat.yml logs napcat
```

独立启动核心：

```bash
docker compose pull --policy always astrbot
docker compose up -d astrbot
docker compose logs astrbot
```

第一次才需要拉取镜像；日常启动只执行相应的 `up -d`。显式拉取使用 `--policy always`，覆盖文件中的 `pull_policy: never`，否则某些 Compose 版本会跳过下载。NapCat 可以先启动，核心就绪后会按反向 WS 的重连间隔连接；AstrBot 也能在 NapCat 未运行时独立启动以调试 WebUI 和插件。共享网络不随任一项目的 `down` 删除，只有确认两个项目都不用时才手动移除它。

`NAPCAT_IMAGE` 可改为已验证的版本或 digest，以固定 QQ 接入版本。示例保留 `latest`，但核心项目和日常部署不会拉取或重建它。将已经登录过的 QQ 号填入 `.env` 的 `NAPCAT_ACCOUNT`，镜像会通过 `-q` 尝试使用缓存快速登录；留空时即使保留了账号数据，也可能进入扫码流程。首次登录或缓存失效仍需手机授权。

### 打开管理面板

本机访问：

- AstrBot：`http://127.0.0.1:6185`。首次密码从启动日志获取，登录后修改。
- NapCat：`http://127.0.0.1:6099/webui/`。按当前版本的日志获取登录 token；首次登录 QQ 时扫码，已有有效登录态不需要另建实例。

远程服务器使用隧道（替换用户名和地址）：

```bash
ssh -N -L 6185:127.0.0.1:6185 -L 6099:127.0.0.1:6099 user@server
```

然后在自己电脑访问上面的本机地址。确需内网直接访问时，可在 `.env` 修改 `ASTRBOT_WEBUI_BIND`、`NAPCAT_WEBUI_BIND` 为目标内网地址，并配置防火墙；不要无鉴权公网开放。

## 3. 建立唯一的 OneBot 连接

在 AstrBot WebUI 的机器人页面创建 **OneBot v11** 实例：

- 选择固定实例 ID，例如 `duckbot_qq`；Taboo 订阅按此实例隔离，之后不要随意改 ID。
- 监听地址 `0.0.0.0`、端口 `6199`，设置一枚足够随机的反向 WebSocket token，启用实例。

在 NapCat 当前账号的网络配置中，编辑原有 WebSocket 客户端：

- URL：`ws://astrbot:6199/ws`。
- 消息格式：`array`。
- token：与 AstrBot 一致，不是 NapCat 面板登录 token。
- 关闭自身消息上报，保存并启用。
- 删除或禁用原先指向 `ws://duckbot:8080/onebot/v11/ws` 的连接，不保留两套应用处理消息。

仓库的 `napcat/config/onebot11.json` 仅为默认模板，token 留空以免提交凭据。已登录账号通常使用 `onebot11_<QQ号>.json`，修改模板不保证改变生效配置；以 WebUI 中当前账号的连接为准，并在使用前为两端配置匹配的 token。

## 4. 验证插件功能，再配置 AI

AstrBot 插件页应出现 **Music Converter**、**Device Commands**、**Taboo**、**Electricity**，日志中不应有加载失败。Compose 分别将四个本地插件目录只读挂入标准插件路径，官方镜像包含它们所用的 HTTP、SQLite 和 Pillow 依赖，不需要自己在面板重复安装。四个插件可独立启停和重载，但仍运行在同一个 AstrBot 进程内。电费后端地址、选房和报警配置见[电费插件指南](electricity.md)。

此外 Compose 挂载 **Voice Tool**，通过 `speak(text)` 工具使用当前会话的 TTS 提供商发送语音，无额外 Python 依赖。需要在 WebUI 配置 TTS 和音色，并允许模型调用该工具；详见[语音工具](voice-tool.md)。首次增加挂载需重建核心，不能只重启。

保留全局唤醒前缀 `/`，验证：

| 操作 | 预期 |
| --- | --- |
| 群聊或私聊分享 Apple Music 链接 | 直接回复网易云链接，不调用 Agent |
| 私聊 `/upa status` | 返回设备状态，不调用 Agent |
| 私聊 `/tb reg 鸭鸭`、`/tb list` | 注册和列出关键词，不调用 Agent |
| 另一人在机器人所在群中提到“鸭鸭” | 注册者收到最近最多 10 条聊天记录图片；本人提及不通知 |
| 私聊 `/tb off`、`/tb on`、`/tb unreg 鸭鸭` | 关闭、开启或删除订阅，开关和订阅在重启后保留 |
| 普通群消息 | 不因这些业务插件自动回复；AstrBot 自身是否回复取决于其唤醒与主动聊天配置 |

设备 API 默认通过 `host.docker.internal:5000/DeviceStatus` 访问宿主机。Compose 保留 `host-gateway` 映射；确认宿主机服务监听可达地址且防火墙允许 Docker 网络。错误回复不再回显内部 URL。

关键词通知使用 OneBot 主动私信，接收者是否可达受 QQ 好友关系和平台风控限制；发送失败会记录日志，不会阻止音乐转换或 AI 聊天。通知图片以 base64 发送，不依赖两个容器共享图片路径。

基础功能不需要 LLM Key。接着可在 AstrBot 配置模型提供商、模型和人格，并为当前机器人选择相应配置。推荐先采用额外 LLM 前缀，例如全局 `/` 加 LLM 前缀 `chat`，以 `/chat 问题` 聊天；@、引用、私聊唤醒也应检查，不要认为只改前缀就关闭了所有其他入口。默认先不开主动回复、代码执行和高权限 MCP，并填写实际群/用户白名单（空列表不表示拒绝所有人）。

音乐分享历史没有额外写入模型上下文，也没有禁用 AstrBot 本来支持的上下文能力。本次不为它新增历史采集或注入逻辑。

## 5. 调试和部署只操作核心

插件代码在 Git 中维护并只读挂载，修改后可重载对应插件或重启 `astrbot`；不要在 WebUI 对这些本地插件执行更新、卸载或写入操作。更换服务器时同时同步 `plugins/`，不能只复制 Compose。其他插件仍安装到正常的可写插件目录。

### 从合并插件升级

同步新的核心 Compose 和三个插件源码，然后执行下一节的强制重建命令，或运行 `scripts/deploy.sh`。新的 Compose 不再挂载 `astrbot_plugin_duckbot`，而是分别挂载 `astrbot_plugin_music_converter`、`astrbot_plugin_device_commands`、`astrbot_plugin_taboo`。只执行 `restart` 不会替换挂载。

Taboo 会继续读取 `astrbot/data/plugin_data/astrbot_plugin_duckbot/taboo.sqlite3`，因此从合并 AstrBot 插件升级不会清空订阅或开关，也不需要搬动数据库。不要把此数据目录误当成废弃代码删除；这与第 1 节“不导入 NoneBot 旧数据库”是两件不同的事。

若曾手动安装过旧合并插件，应在启用三个新插件前先停用旧插件，不要通过卸载来清理其数据。正常的旧版只读挂载部署只需替换挂载；残留的空插件目录不会提供功能。如果配置曾按名称选择启用插件，需要将旧名称替换为三个新名称，确认当前机器人配置均已启用它们，避免旧插件和新插件同时回复或新插件被过滤。更新只操作核心，不重启 NapCat。

### 重建核心

本机插件源码已挂载，调试时只重启核心：

```bash
docker compose restart astrbot
docker compose logs -f astrbot
```

`restart` 不应用 Compose 或环境变量变化；这类变化使用：

```bash
docker compose up -d --no-deps --force-recreate astrbot
```

停止核心使用 `docker compose down`，NapCat 仍然保持登录。核心离线期间命令和通知暂停，不能假定 NapCat 会缓存或重放这些消息；核心恢复后连接自动重建。Taboo 订阅保留，但进程内最近聊天缓存会清空。

远程部署使用 `scripts/deploy.sh`（原 `build.sh`），通过 `PI_USER`、`PI_HOST`、`PI_DIR` 指定目标。它只上传核心 Compose、插件与环境示例，不上传 NapCat Compose、实际 `.env`、QQ 配置、登录态或数据库。目标必须已经按前文完成项目拆分和共享网络准备。

- `scripts/deploy.sh`：不拉取镜像，使用目标机器已有的 AstrBot 镜像，强制重建核心以加载新代码和环境变量。
- `scripts/deploy.sh --upgrade`：先仅拉取 AstrBot 镜像，再重建核心。更换版本时应先在目标 `.env` 修改 `ASTRBOT_IMAGE`。

脚本不执行全项目 `down`、`pull` 或 `--remove-orphans`，也不调用 NapCat 项目的任何生命周期命令。检测到旧单项目配置时会拒绝执行，要求先完成一次性拆分。新机器没有本地核心镜像时，先显式拉取或使用 `--upgrade`。

## 6. 单独维护 NapCat，以及回退

NapCat 的维护必须显式指定自己的 Compose 文件：

```bash
# 主动重启 QQ 接入（普通核心调试不要执行）
docker compose -f docker-compose.napcat.yml restart napcat

# 明确升级 QQ 接入：先在 .env 选择 NAPCAT_IMAGE，再执行
docker compose -f docker-compose.napcat.yml pull --policy always napcat
docker compose -f docker-compose.napcat.yml up -d --no-deps --force-recreate napcat
```

升级 NapCat 会造成 QQ 短暂离线，保留数据也不能保证永远不要求重新登录。只有这类主动维护或宿主机/容器自身故障才涉及 QQ 接入重启，不是更新核心代码的必需步骤。

只回退核心代码或 AstrBot 镜像时，恢复对应版本并重建核心，不动 NapCat。若回到旧单项目 NoneBot 部署，先分别停止这两个新项目，恢复备份的旧 Compose 和 NapCat 连接，再启动旧部署，避免同名容器冲突；旧 `data/` 和 `ntqq/` 不应被删除。新 Taboo 订阅不会自动回写旧数据库。

## 官方接口依据

- [AstrBot Docker 部署](https://docs.astrbot.app/deploy/astrbot/docker.html)
- [AstrBot OneBot v11 接入](https://docs.astrbot.app/platform/aiocqhttp.html)
- [AstrBot 插件与事件传播](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)
- [NapCat 网络配置](https://napneko.github.io/config/basic)
