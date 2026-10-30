# 表情编号与名称来源

`emoji_names.json` 是 [koishijs/QFace](https://github.com/koishijs/QFace) 的编号/名称事实快照，不包含图片、动画或运行代码。

- 固定提交：`c96fa14ff894147ac0442dda0fce6a4523e5fcbd`
- [原始索引](https://github.com/koishijs/QFace/blob/c96fa14ff894147ac0442dda0fce6a4523e5fcbd/public/assets/qq_emoji/_index.v2.json)
- 索引同步时间：`2026-09-28T03:43:28.805Z`；本地整理：2026-10-31。
- 从每项取 `emojiId` / `describe`；名称只去掉开头的 `/`。QQ 数字编号原样保留；Unicode 单字符项使用索引的十进制 `qcid`，不使用 QQ 的其他平台编号。
- 共 537 项，包含 372 个 QQ 编号和 165 个 Unicode 编号；运行时不联网自动更新。快照不代表后续新增表情或实际账号的回应支持范围。
- QFace 仓库代码采用 MIT，许可见 `LICENSE-QFace.txt`；其 README 声明腾讯表情资源版权属于腾讯、仅供学习交流且不得商用。本插件仅使用编号和文字名称，不分发表情素材，不对腾讯素材授予许可。

名称仅说明图案，不断言用户的情绪或意图。未知编号仍会被感知，但名称为 `null`；发送工具拒绝未知编号。不重试替代编号，也不会把普通表情发送成功等同于消息回应受支持。
