# 本机配置全过程、桌面设置和排障

记录日期：2026-10-30。

## 初始环境

CLI 最初检查为 2.1.314；原始 ~/.claude/settings.json 连接阿里云的 Anthropic 兼容接口，模型 qwen3.8-max。桌面安装在 /Applications/Claude.app，检查时版本 4.4212.0。用户已在领取额度的 OpenAI API 组织里创建项目 Key。

全程未覆盖原始 CLI 全局配置。新仓库不包含真实 Key、本地 token、运行 PID、日志或真实验证记录。

## 实际操作顺序

1. 下载并检查 hanjun-lin/claude-gpt，记录上游提交和 MIT 授权。
2. 发现原有 API Key 分支走 Chat Completions，提取 Responses 转换逻辑，改为官方 Responses endpoint，删除 OAuth 和 Anthropic 透传依赖。
3. 增加 Python 管理脚本，隐藏输入 Key，本地权限受限保存。
4. 增加 CLI 独立启动入口，通过 --settings / --model 覆盖当前进程，保留阿里云全局配置。
5. 网络检查发现 OpenAI 直连被重置；Clash 7897 端口返回预期的未认证 401。
6. Python 官方 HTTPS 请求走该代理；Node 使用 HTTPS_PROXY 和 NODE_USE_ENV_PROXY=1，本地回环访问直连。
7. 修复 SSE 分段文字误判，真实 API 和工具闭环通过。
8. CLI 实测返回 CLAUDE_GATEWAY_OK。
9. 为桌面固定 18789 端口和持久化的本地 token。
10. 通过开发者菜单保存 Gateway 设置，模型路由使用兼容别名，显示名称写明 GPT。
11. 修复桌面模型探测 max_tokens: 1 不满足上游最小值的问题。
12. 桌面实际对话返回 DESKTOP_GPT_OK。

## 桌面设置逐步复现

1. 打开 Claude Desktop。
2. Help → Troubleshooting → Enable Developer Mode…，确认后重启。
3. Developer → Configure Third-Party Inference…。
4. Connection 选择 Gateway 和 Static API key。
5. Gateway base URL 填 http://127.0.0.1:18789，不加 /v1。
6. Gateway API key 填 private/gateway-token.json 中的本地 token，OpenAI Key 留在网关端。
7. Gateway auth scheme 选 bearer。
8. 关闭 Model discovery；该实现没有模型发现 endpoint。
9. Add model：Model ID 为 claude-sonnet-4-5，Display name 为 GPT-6.1 Sol · OpenAI API。
10. Apply Changes → Save & Restart。
11. 界面显示 You’re using Gateway，模型选择器显示 GPT 名称。
12. 先发无工具短消息，再单独验证文件操作和工具任务。

桌面官方网关配置面向 Claude 模型。上述 ID 仅为兼容别名，实际推理由 OpenAI 完成。这不是官方 GPT 插件。

这次 Code 模式卡在 Trust workspace，重启应用恢复。仅确认桌面对话通过，未完成 Code 文件读取验证。系统若请求访问目录，应按实际需要决定授权；本项目不要求开启全盘访问。

## 当前运行目录与新仓库

| 目录 | 状态 |
|---|---|
| /Users/mac/Documents/ChatGPT/个人/claude-openai | 当前使用的网关与私密配置 |
| /Users/mac/code/python/claude-openai-gateway | 整理后的独立源码仓库 |

新仓库的 private/ 初始没有现有 Key。创建仓库不切换桌面设置，不影响正在运行的网关。现在继续用旧目录的 launch-desktop.command 即可。

## 未来切换到新仓库的方法

1. 结束正在使用网关的会话。
2. 在旧目录运行 python3 manage.py stop。
3. 检查新仓库 config.json，运行 setup.command，在本地隐藏输入 API Key。
4. 新仓库生成新的本地 token；在桌面 Gateway API key 更新 token，URL 可保持不变。
5. 保存并重启桌面，用新仓库 verify 和桌面对话确认。
6. 出错时停止新网关，恢复旧目录启动入口，并把桌面 token 改回旧值。

本次没有执行运行目录迁移。不要把两个目录同时启动在同一端口。

## 排查方法

### Gateway unreachable

确认 Clash 开启，运行 launch-desktop.command。网关没有系统开机自启，电脑重启后需重新启动。端口占用时先确认是不是旧目录网关，不要直接终止未知进程。

### 401

本地 Invalid local gateway token 是桌面 token 不匹配。OpenAI 401 是上游 Key / 组织认证问题。两种 Key 不能混填。

### 429

检查 API 组织额度、项目、模型速率限制和错误 code。额度余额与速率限制是不同问题，实际抵扣以 OpenAI 账单为准。

### Configured model not available

展开 Details 查看 HTTP 错误。这次曾是 max_output_tokens 最小值问题，不是 Key 无效。修改源码需要重启网关。

### 验证脚本说流式输出失败

检查是不是旧进程仍加载旧校验逻辑。正确做法是解析并拼接文本 delta，不能在原始 SSE 里搜索完整单词。离线测试可稳定复现旧判断并验证新判断。

### Finder 双击找不到 node / claude

Finder 的 PATH 可能与终端不同；电脑还有多个 Node 版本。管理脚本会检查可用的 Node 24+ 和 NVM 安装，必要时设置 CLAUDE_OPENAI_NODE / CLAUDE_OPENAI_CLI 的绝对路径。在终端运行离线 npm test 时先切换到 Node 24+。

### 日志在哪里

各运行目录的 private/gateway.log 和 private/verification.json。凭证和 state JSON 含密钥，不要整份截图或发送。

### 如何恢复

CLI 直接执行原来的 claude，仍使用阿里云全局配置。Desktop 在第三方推理设置中切回原提供方并重启；Gateway 模式与账号登录模式是不同路径，云端能力不能假定完全相同。
