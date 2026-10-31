# 本地安装、CLI、桌面设置与排障

这是实验性的非官方兼容网关，仅用于可信本机。它不能替代 Claude 客户端的权限机制，也不保证完整兼容桌面文件工具或 MCP 工作流。

## 推荐入口

在仓库目录运行 `./claudapter`，第一次会引导填写配置和 Key，再验证 API 并启动 Claude Code。只配置用 `./claudapter setup`，桌面入口是 `./claudapter desktop`；桌面 Gateway 字段仍要按第 4 节单独填写。

引导需要交互终端，不从管道读入 Key。已有配置与凭证会复用，失败或格式错误的文件不会自动覆盖。首次 API 验证失败时会保留 `private/setup-pending.json`，下次重试验证通过后才继续启动客户端。Python 3.10+、Node 24+ 和客户端需预先安装，入口不会自动安装软件。

旧的 `python3 manage.py ...` 命令仍可用。下面保留手动设置的详细步骤，适合想逐项检查配置的用户。

## 1. 准备运行环境

需要 Python **3.10+** 和 Node.js **24+**。管理脚本只接受 Node 24 或更新版本；本项目使用 Node 内置 `fetch` 和环境代理支持，无需安装第三方 Python/npm 依赖。

```bash
python3 --version
node --version
```

CLI 用法还需要安装 Claude Code，确保 `command -v claude` 能找到命令。桌面入口面向 macOS，需要安装提供第三方 Gateway 配置的 Claude Desktop 版本。客户端菜单、功能权限和模型校验规则可能随版本变化。

在下载或克隆的仓库根目录执行：

```bash
cp config.example.json config.json
```

编辑 `config.json`：

- **检查 `model`**：模板中的 `gpt-6.1-sol` 是可修改的示例，不是账号权限承诺。若无权限或需要其他模型，改成实际支持 Responses API、函数调用和 `reasoning.effort` 的模型 ID。
- `reasoning`：使用该模型接受的 effort 值；不是所有模型都支持 `medium` 或 reasoning 参数。
- `https_proxy`：可直连时保持空字符串；需要代理时填写你自己的有效代理 URL，不要套用他人的本机端口。
- `port`：默认 `18789`，应为空闲的本机端口。修改后也须更新桌面保存的 URL。

不要直接运行 `src/server.js` 来绕过配置流程：管理器负责凭证、私密文件权限、进程身份和生命周期。

## 2. 保存 Key 与验证

准备自己有权限使用的 OpenAI API Key，然后运行：

```bash
python3 manage.py setup
# macOS 也可双击 setup.command
```

终端隐藏输入，Key 保存到 `private/credentials.json`。首次设置先查询模型列表，再测试函数调用、工具结果回传与流式文本；已有 Key 时会复用本地凭证。**这些步骤访问真实 API 并产生实际用量**，不能视为免费离线检测。模型出现在列表中也不证明支持全部必需能力。

如需更换 Key，先结束网关会话，保留所需的安全备份，再仅删除自己的 `private/credentials.json`，重新执行 setup。若当前实例配置已变化，按管理器提示显式 `restart`，然后执行 `verify`。不要把 Key 放进命令行参数、仓库、截图或支持工单。

首次使用也可以先运行不需要 Key 的离线测试：

```bash
npm test
```

此命令需要 Node 24+、Python 3.10+，不运行 `npm install`，不访问真实 API。CI 使用 Ubuntu/macOS、Node 24、Python 3.10 执行同一命令。

## 3. CLI 启动

在网关仓库根目录记下路径，再进入你要操作的项目：

```bash
export GATEWAY_DIR="$PWD"
cd /path/to/your/project
python3 "$GATEWAY_DIR/manage.py" launch
```

请把 `/path/to/your/project` 换成自己的项目目录。macOS 也可从目标工作目录执行 `"$GATEWAY_DIR/launch.command"`；Finder 双击时应额外确认客户端选择的工作目录。

入口会启动或复用已验证的网关，通过独立 `--settings` 与 `--model` 配置当前 CLI 进程，不覆盖 `~/.claude/settings.json`。后续参数可传给 Claude，例如：

```bash
python3 "$GATEWAY_DIR/manage.py" launch -- --help
```

客户端中的模型名、上下文估算和功能开关不等同于上游模型能力；可用模型和 reasoning 以你的实际 API 权限为准。直接执行原来的 `claude` 命令仍使用原有全局配置。

## 4. macOS 桌面配置

桌面端不继承 CLI 的配置。在支持该功能的版本中，可尝试以下路径；若菜单不存在，请检查客户端自身的功能可用性，不要修改应用内部文件来强行启用：

1. 打开 Claude Desktop，在 Help → Troubleshooting 中启用 Developer Mode（如有），按提示重启。
2. 打开 Developer → Configure Third-Party Inference。
3. Connection 选择 **Gateway**，凭证选择 **Static API key**。
4. Gateway base URL 填 `http://127.0.0.1:18789`，不加 `/v1`；若改了端口须同步修改。
5. Gateway API key 填 `private/gateway-token.json` 中的 **本地 token**，不是 OpenAI Key。
6. Gateway auth scheme 选择 **bearer**。
7. 关闭 Model discovery：该实现没有模型发现 endpoint。
8. 显式添加模型。可尝试路由别名 `claude-sonnet-4-5`，显示名用 `OpenAI via local gateway`。别名只是兼容尝试，不保证所有客户端版本接受。
9. Apply Changes → Save & Restart。
10. 在仓库根目录运行 `./launch-desktop.command`，先启动网关再打开应用。

实际默认模型由网关 `config.json` 决定，Claude 路由名不表示实际使用 Claude 模型。桌面官方网关功能面向 Claude；此转接不是官方 OpenAI 插件。桌面可能按别名推导上下文或工具能力，不能保证这些元数据与上游一致。

先测试不使用工具的短对话。随后仅在临时目录中、审查权限后测试单个文件或工具操作。**真实桌面文件读取、写入和完整工具流程尚未验证；对话成功不等于全部兼容。** 不需要默认开启全盘访问，不要绕过工作区信任或系统目录权限提示。

桌面入口没有注册系统开机自启；重启电脑后，只点应用图标不会自动启动网关。

## 5. 配置更新与进程管理

以下命令在网关仓库根目录运行：

```bash
python3 manage.py start
python3 manage.py restart
python3 manage.py stop
python3 manage.py verify
```

- `start`：启动或复用经认证 `/health` 验证的本目录实例。PID 和实例标识必须匹配保存的身份；配置指纹用于检测 Key、模型、reasoning、代理和相关源码变化。
- 检测到配置变化时，`start` **拒绝复用并要求运行 `restart`，不会静默重启**。先结束会话，再显式重启。
- `restart`：先安全停止已确认归属的实例，再应用当前配置启动。它不是按端口杀进程的工具。
- `stop`：只停止能验证身份的本目录实例；无法验证的旧版身份、过期状态或未知服务不会被盲目终止。
- `verify`：使用真实 Key 做 API smoke 验证，会产生用量；不能替代桌面 UI、权限和工具验证。

更新源码后没有热重载，同样需要显式重启。不要同时在相同端口启动多个仓库副本。

## 6. 排障

### 找不到 Node 或 Claude

Finder 的 PATH 可能不同于终端。管理器检查 PATH 与常见 NVM 安装；可在终端显式指定正确的可执行文件路径：

```bash
CLAUDE_OPENAI_NODE="$(command -v node)" \
CLAUDE_OPENAI_CLI="$(command -v claude)" \
python3 manage.py start
```

先确认 `command -v` 不为空、Node 是 24+。指定旧版 Node 不能绕过版本要求；从 Finder 双击也不会自动继承你刚在另一个终端设置的临时变量。CLI 启动时也须能找到 `claude`。

### Gateway unreachable 或端口占用

先运行 `python3 manage.py start`，核对桌面 URL 和配置端口。仅当你配置了网络代理时，才检查该代理是否可用；本机回环通信不应绕到外部代理。

若端口已被占用，先确认服务来源和启动目录，不要直接终止未知 PID。可以在 macOS 用 `lsof -nP -iTCP:18789 -sTCP:LISTEN` 辅助检查，但端口/PID 列表本身不是安全归属证明。

### 提示配置变化，需要 restart

这是防止旧进程继续使用旧 Key、reasoning、代理或源码的保护。先结束会话，再运行 `python3 manage.py restart`。不要反复 `start`，也不要靠删除状态文件绕过身份校验。

### stop / restart 拒绝旧版或过期身份

管理器无法通过带 token 的 `/health` 确认保存的 PID 和实例标识时会拒绝停止。旧版网关可能没有身份信息，或者状态对应的进程已退出、被其他进程复用了 PID。

先人工确认监听进程、命令行、启动目录和来源。仅在确认确为你自己的旧实例后，使用其原始控制终端或系统进程工具停止。确认该实例退出且端口释放后，才清理对应仓库的过期 `private/state.json` 并重新启动。不要按状态文件里的 PID 盲目发送信号，也不要删除整个 `private/` 来处理端口冲突。

### 401

区分错误来自哪一侧：本地 `Invalid local gateway token` 表示客户端 token 不匹配；上游 OpenAI 401 表示 API 凭证或项目认证问题。桌面必须填本地 token，OpenAI Key 只留在网关端。不要把两种凭证互换。

### 429

检查上游错误 code、项目用量/预算和模型速率限制。用量不足与限速是不同问题；以自己的 API 项目和账单为准。

### Configured model not available / 400

展开客户端 Details 并查看网关日志。确认模型真实存在且账号有权限，支持 Responses、函数调用和当前 reasoning 值；不要把所有 400 都归因于 Key。网关把输出上限映射为至少 16，但这不保证满足所有模型的其他约束。桌面路由别名也可能被客户端新版规则拒绝。

### 流式验证失败

检查上游连接、错误分类及是否运行了已更新的实例。验证器按 SSE 文本 delta 拼接结果，不是在原始 SSE 中查找完整单词；缺少正常结束事件或收到 error 会判失败。先运行离线 `npm test`，需要真实验证时再执行 `verify`。

### 日志与隐私

查看对应仓库的 `private/gateway.log` 和 `private/verification.json`。`private/credentials.json`、`private/gateway-token.json`、`private/state.json` 和 CLI 设置含凭证或认证信息；日志和验证记录也可能含模型或上游诊断。分享排障片段前手动脱敏，不要整份上传 `private/`。

私密目录权限为 `700`，凭证/状态文件为 `600`，但不是加密或 Keychain 存储。同一用户下的恶意进程仍可能访问它们。

## 7. 恢复原配置或更换仓库目录

CLI 直接运行原来的 `claude` 即可沿用原有配置。Desktop 在第三方推理设置中切回原提供方并重启；Gateway 与账号登录路径的能力不能假定一致。

需要更换运行目录时：

1. 结束网关会话，在原目录安全停止已验证的实例；旧版实例按上述身份排障流程处理。
2. 在新目录检查配置，重新安全输入 Key；不要把私密文件作为源码提交。
3. 新目录生成的本地 token 可能不同，更新桌面 token 和端口并重启桌面。
4. 如需真实 API 检查再运行 `verify`，并另行测试客户端。

不要同时启动两份占用同一端口的服务；保留原目录不代表桌面设置会自动迁移。
