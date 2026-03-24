# Socket 字幕流式转发协议规范

## 1. 目标

本规范用于定义 `socket_client` 与 Socket 服务器之间的字幕流式转发方式。

目标如下：

- 普通文本消息继续按原方式发送和显示
- 字幕消息使用特定结构，服务端识别后显示到字幕区域
- 服务端能够实时显示“识别中”的字幕，而不是只能等完整句子
- 协议尽量简单，方便另一端 AI 或开发者快速实现


## 2. 设计原则

本协议采用以下原则：

- 不引入复杂二进制协议
- 保持与现有普通文本消息兼容
- 字幕更新采用“整句覆盖”而不是“增量追加字符”
- 服务器只要识别到特定前缀，就按字幕协议处理
- 没有特定前缀的消息，一律当作普通聊天消息处理

说明：

- STT 的 partial 结果会反复修改前面的词，所以字幕更新不能用“append”，必须用“replace”
- 客户端每次发送的是“当前整句草稿”
- 服务端收到后，用该内容覆盖当前正在显示的 live 字幕


## 3. 传输方式

### 3.1 连接方式

- 使用现有 TCP Socket 连接
- 字符编码统一为 `UTF-8`

### 3.2 消息边界

每一条业务消息以换行符 `\n` 结束。

即：

- 普通文本消息：`普通文本内容\n`
- 字幕结构消息：`[[SC_CAPTION_V1]] {json}\n`

服务端必须做按行缓冲解析，不能假设一次 `recv()` 就一定收到一整条消息。

服务端处理建议：

1. 维护一个接收缓冲区字符串
2. 每次 `recv()` 后追加到缓冲区
3. 只要缓冲区中存在 `\n`，就切出一整行处理
4. 剩余半包继续留在缓冲区等待下一次接收


## 4. 消息分类规则

### 4.1 普通消息

不以保留前缀开头的消息，全部视为普通文本消息。

示例：

```text
你好，这是普通消息
```

服务端处理方式：

- 显示到普通消息区域
- 不参与字幕状态处理

### 4.2 字幕结构消息

以如下前缀开头的消息，视为字幕协议消息：

```text
[[SC_CAPTION_V1]]
```

完整格式为：

```text
[[SC_CAPTION_V1]] {"type":"caption_partial", ...}
```

说明：

- 前缀与 JSON 之间保留一个空格
- JSON 使用单行，不允许跨多行


## 5. 字幕消息 JSON 结构

### 5.1 通用字段

所有字幕消息都使用 JSON 对象，字段如下：

| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 是 | 消息类型 |
| `stream_id` | string | 是 | 一次字幕会话的唯一 ID |
| `segment_id` | string | 否 | 当前句子的唯一 ID |
| `seq` | int | 否 | 当前句子的更新序号，递增 |
| `text` | string | 否 | 当前整句文本 |
| `lang` | string | 否 | 语言，例如 `zh-CN` |
| `timestamp_ms` | int | 否 | 客户端发送时间戳，毫秒 |

### 5.2 字段语义

- `stream_id`
  - 表示一次连续字幕会话
  - 每次客户端启动实时字幕时生成一个新的 `stream_id`
  - 同一次实时识别过程中的所有字幕消息都使用同一个 `stream_id`

- `segment_id`
  - 表示一句话或一个分段
  - 同一句话的 partial 和 final 必须使用同一个 `segment_id`
  - 当一句 final 完成后，下一句要换新的 `segment_id`

- `seq`
  - 表示该句字幕的第几次更新
  - 从 `1` 开始递增
  - 服务端只接受比当前更大的 `seq`
  - 用于丢弃乱序包或重复包

- `text`
  - 始终表示“当前整句内容”
  - 不是字符差量
  - partial 和 final 都传完整文本


## 6. 消息类型定义

本规范定义以下几种类型。

### 6.1 `caption_partial`

表示当前句子的实时草稿。

必填字段：

- `type`
- `stream_id`
- `segment_id`
- `seq`
- `text`

语义：

- 用于实时更新字幕区的 live 行
- 服务端收到后应覆盖当前 live 行显示
- 不写入最终历史区

示例：

```text
[[SC_CAPTION_V1]] {"type":"caption_partial","stream_id":"stt-20260324-001","segment_id":"seg-001","seq":3,"text":"今天的天气","lang":"zh-CN","timestamp_ms":1774321000123}
```

### 6.2 `caption_final`

表示当前句子已经结束，为最终结果。

必填字段：

- `type`
- `stream_id`
- `segment_id`
- `seq`
- `text`

语义：

- 服务端收到后将该句写入最终字幕历史区
- 同时清除对应的 live 行

示例：

```text
[[SC_CAPTION_V1]] {"type":"caption_final","stream_id":"stt-20260324-001","segment_id":"seg-001","seq":4,"text":"今天的天气不错。","lang":"zh-CN","timestamp_ms":1774321000456}
```

### 6.3 `caption_clear`

表示清除当前 live 字幕。

适用场景：

- 识别停止
- 会话取消
- 一句 partial 已经没意义，需要主动清掉

必填字段：

- `type`
- `stream_id`

可选字段：

- `segment_id`
- `seq`

语义：

- 服务端收到后清空当前 live 行
- 不写入最终字幕历史区

示例：

```text
[[SC_CAPTION_V1]] {"type":"caption_clear","stream_id":"stt-20260324-001","segment_id":"seg-001","seq":5,"timestamp_ms":1774321000600}
```


## 7. 客户端发送规则

### 7.1 普通文本发送

用户主动输入并发送的普通文本，按原方式发送：

```text
普通文本内容\n
```

不增加前缀，不包装 JSON。

### 7.2 字幕发送总规则

客户端启用字幕转发后，应遵守以下规则：

1. 启动一次实时字幕时，生成新的 `stream_id`
2. 同一句话第一次出现 partial 时，生成新的 `segment_id`
3. 每次 partial 文本变化时，发送 `caption_partial`
4. 每条 partial 的 `seq` 递增
5. 收到 final 时，发送 `caption_final`
6. final 发出后，该 `segment_id` 生命周期结束
7. 下一句重新创建新的 `segment_id`
8. 如果停止识别时还有未 final 的 live 内容，发送一次 `caption_clear`

### 7.3 partial 发送策略

客户端不应在每个回调都无脑发送，建议做如下优化：

- 只有当文本内容发生变化时才发送
- 发送频率限制在每 `80ms` 到 `150ms` 一次
- 如果 partial 文本和上次发送完全一致，则不重复发送

说明：

- 这样可以减少网络压力
- 也可以减少服务端 UI 刷新抖动

### 7.4 final 发送策略

当 STT 产生最终识别结果时：

- 立即发送 `caption_final`
- `text` 使用最终完整文本
- `segment_id` 必须与该句最后一次 partial 相同
- `seq` 必须大于之前所有 partial 的 `seq`

### 7.5 推荐 ID 规则

推荐格式如下：

- `stream_id`: `stt-YYYYMMDD-HHMMSS-随机数`
- `segment_id`: `seg-0001`、`seg-0002`

只要全局唯一或在同一连接内唯一即可。


## 8. 服务端处理规则

### 8.1 普通消息处理

当收到的整行消息不以 `[[SC_CAPTION_V1]]` 开头时：

- 按普通文本消息处理
- 显示到聊天/消息区域
- 不影响字幕区状态

### 8.2 字幕消息解析

当收到的整行消息以 `[[SC_CAPTION_V1]]` 开头时：

1. 去掉前缀和后面的空格
2. 解析后面的 JSON
3. 根据 `type` 分发处理

如果 JSON 解析失败：

- 记录日志
- 丢弃该条消息
- 不影响普通连接状态

### 8.3 服务端字幕状态模型

服务端应按“每个连接一个 live 字幕状态”维护数据。

最小状态如下：

- `current_stream_id`
- `current_segment_id`
- `last_seq`
- `live_text`
- `final_history`

如果服务端支持多客户端列表，可按“连接 ID / 客户端 ID”分别维护。

### 8.4 `caption_partial` 处理规则

当收到 `caption_partial` 时：

1. 如果 `stream_id` 不同，可切换到新的字幕会话
2. 如果 `segment_id` 是新句子，则创建新的 live 状态
3. 如果 `seq` 小于等于当前 `last_seq`，则忽略
4. 否则更新：
   - `current_segment_id = segment_id`
   - `last_seq = seq`
   - `live_text = text`
5. 刷新字幕显示区的 live 行

关键点：

- 是“覆盖当前 live 行”
- 不是“把 text 追加到末尾”

### 8.5 `caption_final` 处理规则

当收到 `caption_final` 时：

1. 校验 `stream_id`、`segment_id`
2. 如果 `seq` 小于当前 `last_seq`，可忽略
3. 将 `text` 作为最终字幕写入历史列表
4. 清空当前 live 行
5. 当前句结束

### 8.6 `caption_clear` 处理规则

当收到 `caption_clear` 时：

- 清空当前 live 行
- 不写入最终历史


## 9. 服务端 UI 显示规范

建议服务端 UI 分成两个区域：

### 9.1 普通消息区

显示：

- 普通文本消息
- 系统日志

不显示：

- `caption_partial`
- `caption_final`
- `caption_clear`

### 9.2 字幕区

建议拆成两部分：

- `live 字幕行`
  - 只显示当前正在识别的一句
  - 每次 partial 到来时覆盖
- `final 字幕历史`
  - 保存最近若干条最终字幕
  - 每次 final 到来时追加

推荐视觉效果：

- live 行颜色稍浅或带“识别中”标识
- final 行颜色正常


## 10. 客户端与服务端交互示例

### 10.1 示例一：普通消息

客户端发送：

```text
你好，服务器
```

服务端处理：

- 当普通消息显示
- 不更新字幕区

### 10.2 示例二：一段完整的流式字幕

客户端依次发送：

```text
[[SC_CAPTION_V1]] {"type":"caption_partial","stream_id":"stt-1","segment_id":"seg-1","seq":1,"text":"今天"}
[[SC_CAPTION_V1]] {"type":"caption_partial","stream_id":"stt-1","segment_id":"seg-1","seq":2,"text":"今天的天气"}
[[SC_CAPTION_V1]] {"type":"caption_partial","stream_id":"stt-1","segment_id":"seg-1","seq":3,"text":"今天的天气不错"}
[[SC_CAPTION_V1]] {"type":"caption_final","stream_id":"stt-1","segment_id":"seg-1","seq":4,"text":"今天的天气不错。"}
```

服务端字幕区变化：

1. live 显示：`今天`
2. live 替换为：`今天的天气`
3. live 替换为：`今天的天气不错`
4. final 历史追加：`今天的天气不错。`
5. live 清空

### 10.3 示例三：识别被中断

客户端发送：

```text
[[SC_CAPTION_V1]] {"type":"caption_partial","stream_id":"stt-1","segment_id":"seg-2","seq":1,"text":"我们现在"}
[[SC_CAPTION_V1]] {"type":"caption_clear","stream_id":"stt-1","segment_id":"seg-2","seq":2}
```

服务端处理：

1. live 显示：`我们现在`
2. live 清空
3. 不写入 final 历史


## 11. 异常与容错规则

### 11.1 非法 JSON

如果字幕结构消息中的 JSON 不合法：

- 记录日志
- 丢弃消息
- 不关闭连接

### 11.2 缺少关键字段

如果缺少 `type` 或 `stream_id`：

- 丢弃该消息
- 记录日志

如果 `caption_partial` 或 `caption_final` 缺少 `segment_id`、`seq` 或 `text`：

- 丢弃该消息
- 记录日志

### 11.3 重复消息

如果同一 `stream_id + segment_id` 的 `seq` 小于等于当前已处理序号：

- 视为旧消息
- 直接忽略

### 11.4 未知类型

如果 `type` 不在本规范中定义：

- 忽略该消息
- 记录日志


## 12. 最小实现要求

另一端实现时，最低必须支持以下能力：

### 客户端最低要求

- 能发送普通文本消息
- 能发送 `caption_partial`
- 能发送 `caption_final`
- partial 使用同一 `segment_id`
- final 能结束该句

### 服务端最低要求

- 能区分普通消息与字幕结构消息
- 能解析 `caption_partial`
- 能解析 `caption_final`
- partial 覆盖 live 行
- final 进入历史并清空 live

`caption_clear` 可以作为第二优先级实现，但建议支持。


## 13. 推荐实现结论

本项目建议采用以下最终方案：

- 普通消息：保持原样发送
- 字幕消息：使用保留前缀 `[[SC_CAPTION_V1]]`
- 协议体：单行 JSON
- 字幕更新语义：`partial = 覆盖 live`，`final = 写入历史并清空 live`
- 分包处理：按换行符做缓冲解析

这个方案的优点：

- 简单
- 与现有消息兼容
- 易于调试
- 方便让另一段 AI 直接实现


## 14. 给实现方的简明指令

如果要把本规范交给另一段 AI，可直接附上以下要求：

1. 保持普通文本消息兼容，不要改现有聊天逻辑
2. 新增对 `[[SC_CAPTION_V1]] {json}` 单行消息的识别
3. 字幕消息支持 `caption_partial`、`caption_final`、`caption_clear`
4. `caption_partial` 必须覆盖当前 live 字幕，不能追加
5. `caption_final` 必须写入历史并清空 live
6. Socket 接收必须做换行缓冲，不能假设一次 `recv()` 就是一整条消息

