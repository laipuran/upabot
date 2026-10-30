# Voice Tool

`astrbot_plugin_voice_tool` 注册 LLM 工具 `speak(text: string)`，说明为“把一段话合成语音发给用户，想发声时用”。是否调用由模型决定，不新增聊天命令，不强制把所有回复转为语音。

## 配置与部署

Compose 已挂载插件。更新后需要重建核心（仅重启不会增加新挂载）：

```bash
docker compose up -d --no-deps --force-recreate astrbot
```

也可以按现有流程运行 `scripts/deploy.sh`，不影响 NapCat。

在 AstrBot WebUI 中为当前会话配置 TTS 提供商，并确认所用模型支持且允许调用 `speak`。音色沿用提供商配置的晓伊；要更换音色，在 WebUI 修改，插件不传音色参数。

| 配置项 | 默认值 | 行为 |
| --- | --- | --- |
| `enabled` | `true` | 关闭后不调用 TTS，向模型返回文字降级说明 |
| `max_chars` | `120` | 按字符数截断，超长时先发文字提醒，再发语音；最小为 1 |
| `cleanup` | `true` | 消息发送处理结束后删除 provider 返回的本地临时音频文件 |

插件使用 `event.unified_msg_origin` 选择当前会话的 TTS 提供商，等待提供商查询和合成最多 30 秒。无提供商、超时、空路径、文件不存在或合成异常，均向模型返回字符串，要求改用文字回复；不会返回空结果。实际文字降级仍由模型完成。

音频必须是 AstrBot 容器内可读的本地文件。`cleanup=true` 适用于返回独立临时文件的 provider；如果 provider 使用需要保留的共享缓存文件，应关闭清理。清理失败只记日志，不影响已发送语音。平台发送失败由 AstrBot 工具执行器记录，不自动重试。

## 验证

- 请求模型“用语音说你好”，确认调用 `speak`、收到语音。
- 输入超过限制的内容，确认收到截断提醒，且仅合成前 `max_chars` 个字符。
- 取消当前会话的 TTS 配置，确认工具返回降级说明，模型继续文字回复。
- 本地单元测试：`uv run python -m unittest discover -s tests -p test_voice_tool.py -v`。
