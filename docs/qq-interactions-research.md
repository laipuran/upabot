# QQ 收到贴表情 → LLM：插件核查

核查日期：2026-10-30。仅查公开 GitHub API、仓库源码及作者 README；未安装、未实机测试、未连接远程机器人。“源码已核查”不代表运行已验证。

## 结论

**本次未找到可明确推荐为“收到 QQ 表情回应后，把回应及原消息送入聊天 LLM”的现成插件。** 已找到接收通知的插件，但用途分别是跟贴、日志/消息推送、菜单命令；不能把“使用 LLM 选择要发送的表情”误当作“LLM 感知收到的回应”。这不是对全部 GitHub 插件的不存在证明。

## 已找到且核查源码

| 插件 | 收到回应后的实际路径 | 群聊 / 私聊及判断 |
| --- | --- | --- |
| [Zhalslar/astrbot_plugin_emoji_like](https://github.com/Zhalslar/astrbot_plugin_emoji_like/blob/main/main.py) | `_follow_reaction_notice`、`on_group_emoji_reaction` 确实读取 `group_msg_emoji_like`、`likes`、`is_add`，然后调用 `set_msg_emoji_like` 跟贴，并可能 `stop_event()`；没有把收到的回应送入 LLM。其他路径的情感分析针对普通/引用消息，用来选发送表情。 | 群回应有接收代码；私聊回应未证实。**不是纯发送插件，但仍不满足 LLM 感知需求。** |
| [Dracowyn/astrbot_plugin_napcat_tools](https://github.com/Dracowyn/astrbot_plugin_napcat_tools/blob/master/tools.py) | `react_emoji` 是 LLM 调用的发送工具，调用 `set_msg_emoji_like`；不是入站通知处理器。 | 发送实现不强制群聊，但不能由此推断私聊接收支持。**不满足。** |
| [singularity2000/astrbot_plugin_emoji_sticker](https://github.com/singularity2000/astrbot_plugin_emoji_sticker/blob/main/main.py) | `on_notice` 识别 `group_msg_emoji_like`（也尝试 `notify/emoji_like`），取首个 `likes` 表情，查询成员、群和原消息，记录日志并 `context.send_message` 推送。此处理路径没有 LLM 调用或上下文注入；`judge_emotion` 属于发送选表情路径。 | **入站源为群聊**，处理依赖 `group_id`、群资料接口；[README](https://github.com/singularity2000/astrbot_plugin_emoji_sticker/blob/main/README.md) 的“私聊”指推送目的地，不是私聊回应接收。可作为群回应监控候选，**不可作为开箱即用 LLM 感知插件推荐**。 |
| [wjn1121/astrbot_plugin_face_echo](https://github.com/wjn1121/astrbot_plugin_face_echo/blob/main/main.py) | 解析该通知，再对同一消息贴相同表情；源码明确说明不产生聊天回复，未接入 LLM。 | 群聊专用；**仅附和，不满足。** |
| [pppopipupu/astrbot_plugin_emoji_reaction_like](https://github.com/pppopipupu/astrbot_plugin_emoji_reaction_like/blob/rollback_backup/main.py) | 有 `on_llm_request`，但注入的是普通消息 ID/文本列表；`on_llm_response` 解析输出标记后发送回应。没有解析入站 `group_msg_emoji_like`。 | 存在群/私聊发送范围配置；不能当作群/私聊入站回应感知。**不满足。** |

另发现 [DearCrazyLeaf/astrbot_plugin_react_menu](https://github.com/DearCrazyLeaf/astrbot_plugin_react_menu/blob/main/README.md)：作者声明群内点回应触发菜单命令，使用的名称为 `group_msg_reaction`。本次仅核查 README，未验证它与指定 NapCat 通知的兼容性；也没有证据表明它把任意消息上的回应交给 LLM。

## 协议与 AstrBot 边界

- NapCat 的[官方事件源码](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/event/notice/OB11MsgEmojiLikeEvent.ts)定义的是 **群通知** `OB11GroupMsgEmojiLikeEvent`：`notice_type='group_msg_emoji_like'`，含 `message_id`、`likes=[{emoji_id,count}]`、`is_add`。源码注释还指出，操作别人消息时 `user_id` 可能为空，不能保证总能知道是谁贴的。这里未找到私聊对应事件的证据。
- AstrBot 的[OneBot 适配器源码](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py)会将 notice 保存在 `raw_message`，但普通通知的 `message_str` 和消息链为空，`message_id` 另生成 UUID。要关联原消息必须读 **`raw_message['message_id']`**；收到通知不等于自动生成 LLM 可理解的文本。
- 作为对照，[pokepro 的事件模型](https://github.com/Zhalslar/astrbot_plugin_pokepro/blob/main/core/model.py)仅接收 `notify/poke`；[响应处理](https://github.com/Zhalslar/astrbot_plugin_pokepro/blob/main/core/on_poke.py)的 `respond_llm` 确实 `yield event.request_llm(...)`。因此它是“收到戳一戳 → LLM”的实现参考，**不是贴表情感知插件**。模型允许无 `group_id` 的私聊戳，但未实机验证。

## 建议与检索限制

若必须“收到回应 → LLM”，目前建议先不安装上述发送/跟贴插件来替代需求。最接近的群入站素材是 `emoji_sticker` 的监控处理，但仍需补充显式 LLM 请求或下次请求的上下文注入，并处理原消息归属、未知操作者、撤销、去重及自触发循环；私聊必须另行确认协议支持，不能沿用群通知推断。

检索使用 GitHub 仓库搜索：`astrbot emoji`、`astrbot reaction`、`astrbot 贴表情`、`astrbot 表情 回应` 等，并直接阅读候选文件；网页精确词搜索未带来已核实的额外 LLM 感知插件。GitHub [代码搜索 API](https://api.github.com/search/code?q=group_msg_emoji_like+astrbot)匿名请求返回 401，故本次不是全站代码穷举。引用为核查时默认分支文件，后续上游变更可能改变结论。
