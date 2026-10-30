# 代码与协议转换详解

## 1. 配置管理和凭证边界

`manage.py setup` 用 getpass 从终端读取 Key，不回显，不放入命令行参数。Key 写入 `private/credentials.json`，文件权限 600，私密目录权限 700。

`start()` 读取配置、Key，启动 Node 子进程。OpenAI Key 只通过子进程环境交给网关。健康检查通过后，PID、URL 和本地 token 保存到 `private/state.json`。

两种密钥不能混填：

| 密钥 | 持有者 | 发送目标 |
|---|---|---|
| OpenAI API Key | 管理脚本与 Node 网关 | api.openai.com |
| 本地 gateway token | Claude CLI / Desktop | 127.0.0.1:18789 |

本地 token 保存在 `private/gateway-token.json`，重启复用。网关验证 Bearer 或 x-api-key，不把这个 token 当 OpenAI Key。CLI 子进程会移除继承的 OPENAI_API_KEY。

固定端口和 token 是桌面可用的前提：CLI 可以每次生成独立配置，但桌面保存的地址和凭证不会自动跟着随机端口变化。

## 2. 本机 HTTP 路由

- GET /health：返回健康状态和配置模型，不需要 token，不调用上游。
- POST /v1/messages：鉴权后转成官方 Responses 请求。
- POST /v1/messages/count_tokens：鉴权后在本地估算 token。

没有 GET /v1/models，因此桌面关闭模型自动发现，显式填写模型列表。

## 3. 请求字段映射

| Anthropic Messages | OpenAI Responses |
|---|---|
| 顶层 system 文本 | instructions |
| 用户文本 | input 中 message / input_text |
| assistant 文本 | message / output_text |
| 用户图片 | input_image，保留 URL 或 data URL |
| tools[].input_schema | 函数工具 parameters |
| assistant tool_use | function_call，保留调用 ID |
| 用户 tool_result | function_call_output，对应 call_id |
| tool_choice: any | required |
| tool_choice: tool | 指定名字的 function |
| max_tokens | max_output_tokens，最低 16 |

例如 Claude 发出的工具调用：

```json
{"type":"tool_use","id":"call_123","name":"Read","input":{"file_path":"check.txt"}}
```

转换为：

```json
{"type":"function_call","call_id":"call_123","name":"Read","arguments":"{\"file_path\":\"check.txt\"}"}
```

工具实际由 Claude 客户端执行。客户端传回 tool_result，网关再构造对应的 function_call_output。调用 ID 的关联是多轮工具循环成立的关键；网关自身不执行模型生成的命令。

工具 schema 设置 strict: false，并移除 $schema 和 Anthropic cache_control。这是最小兼容策略，不是完整 Anthropic 特性支持。

## 4. 输出与流式事件

上游固定为 `https://api.openai.com/v1/responses`，使用 API Key。没有 ChatGPT/Codex 订阅登录接口。

请求使用 stream: true、store: false、parallel_tool_calls: false。非流式客户端也读取上游流，由 Collector 累积成普通 JSON。

| OpenAI 事件 | Anthropic 一侧的处理 |
|---|---|
| response.output_text.delta | 文本块 delta |
| response.output_item.added 中 function_call | 打开 tool_use 块，记录 item ID / call ID |
| response.function_call_arguments.delta | 工具参数 JSON 片段 |
| response.completed | 更新 usage 并完成 |
| response.incomplete | 标记 max_tokens |
| response.failed / error | 报错 |

AnthropicStreamWriter 产生 message_start、content_block_start/delta/stop、message_delta 和 message_stop。流已开始后发生错误时，发送 error 事件，而不是把错误伪装成正常回答。上游缺少完成事件就断开时抛错。

## 5. 修复：分段文字校验误判

旧验证脚本直接在原始 SSE 中寻找 STREAM_OK。模型合法返回两个 delta：STREAM 和 _OK 时，中间夹着事件和 JSON 元数据，原始字符串里没有连续的 STREAM_OK。

修正后逐条解析 data JSON，拼接 delta.text，再检查内容、error 和 message_stop。

`tests/test_stream_validation.py` 提取真正的 smoke 函数并模拟分段：旧条件稳定报“流式输出校验失败”，新条件通过。

## 6. 修复：桌面模型探测 400

Claude Desktop 检测模型时发送 max_tokens: 1。直接映射给 OpenAI 后，返回 max_output_tokens 必须至少为 16 的错误。

构造 payload 时改为：

```javascript
payload.max_output_tokens = Math.max(16, body.max_tokens);
```

离线测试覆盖正常的 2048 token 请求和桌面探测的 1 token 请求。修改源码后要停止并重启网关，当前没有热重载。

## 7. 桌面路由别名

本次表单拒绝 gpt-6.1-sol，也拒绝未知的 claude-gateway-gpt-6.1-sol。可以保存的 Model ID 是 claude-sonnet-4-5，显示名称明确设成 GPT-6.1 Sol · OpenAI API。

网关把非 GPT 模型 ID 映射到 config.json 的默认模型，因此它只是客户端兼容别名，程序不会连接 Anthropic。gpt-* 请求则使用指定的 OpenAI 模型。实现没有完整 allowlist，仅适合可信本机使用。

桌面可能从 Claude 路由名推导上下文上限、输出上限和功能；这些不保证符合真实 GPT 模型。不能把路由可用解读为官方支持 GPT 或元数据完全准确。

## 8. 测试覆盖含义

npm test 用 mock fetch，验证官方 endpoint、schema、调用 ID、工具结果、最小输出长度、流中断和分段校验，不访问网络。

manage.py verify 使用已保存的真实 Key，验证函数调用、工具结果回传与流式文字，产生实际 API 用量。它不能代替桌面 UI、文件权限或全部 MCP 工具兼容测试。
