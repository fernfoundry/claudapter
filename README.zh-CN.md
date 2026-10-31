![Claudapter — 在 Claude Code 中使用 OpenAI 模型](docs/assets/claudapter.svg)

# Claudapter

在 Claude Code 里用 OpenAI 的模型。

Claudapter 在本机启动一个小服务，把 Claude 客户端的请求转换成 OpenAI Responses API 的格式，再把回复转回来。文本、流式输出和工具调用都在这一层处理，原来的全局 Claude 配置会保留。

你可以继续用熟悉的 Claude Code，同时为当前会话选择 OpenAI 模型。macOS 的 Claude Desktop 也能通过 Gateway 设置接入，目前验证过聊天，文件工具还需要继续测试。

[English](README.md) · [安装与排障](docs/local-setup.md) · [实现原理](docs/implementation.md) · [参与贡献](CONTRIBUTING.md)

<details>
<summary>展开看看请求是怎么走的</summary>

![Claude 客户端通过本地 Claudapter 连接远端 OpenAI API](docs/assets/protocol-adapter.svg)

图中的终端和适配器在本机，右侧代表远端 OpenAI API。工具仍由 Claude 客户端执行。

</details>

## 开始使用

需要 Python 3.10+、Node.js 24+ 和 OpenAI API Key。使用终端版还需要安装 Claude Code，确保终端能找到 `claude` 命令。项目本身不用额外装 Python 或 npm 包。

克隆仓库后启动：

```bash
git clone https://github.com/fernfoundry/claudapter.git
cd claudapter
./claudapter
```

如果下载的是 ZIP，进入解压后的目录运行 `python3 claudapter` 即可。

第一次会检查运行环境，询问模型、推理强度、可选代理和 API Key，生成配置并测试连接。Key 输入不会显示。**首次设置会请求真实 API，产生少量用量。** 首次验证通过后，以后启动会复用已有配置，不再重复询问或自动运行付费检查。

设置完成后，Claude Code 会在当前目录打开。平时想处理其他项目，可以这样启动：

```bash
# 先在 Claudapter 仓库记下路径，再进入自己的项目。
CLAUDAPTER_DIR="$PWD"
cd /path/to/your/project
"$CLAUDAPTER_DIR/claudapter"
```

把项目路径换成自己的目录。原来的 `claude` 命令仍使用原有设置。只想先配置、不打开客户端，可以运行 `./claudapter setup`。macOS 也可双击 `claudapter.command`；缺少 Python 或 Node 时会给出提示，不会自动安装软件。

<details>
<summary>配置文件和一条简单的验证命令</summary>

引导会生成 `config.json`。所选模型需要支持 Responses、函数调用和对应的 `reasoning.effort`，默认示例不保证所有账号都有权限。能直连时代理留空，需要时填写自己的 HTTP(S) 代理地址和端口。

已有 `config.json` 时不会再次询问这些选项，也不会覆盖它。可以手动修改后运行 `./claudapter restart`。

下面的请求不使用客户端工具，可以检查文本对话：

```bash
./claudapter -p "Reply with exactly CLAUDAPTER_OK." --tools "" --max-turns 1
```

这也会调用真实 API。这条请求只检查文本对话，文件工具需要另外测试。

</details>

## Claude Desktop

桌面端有自己的一套 Gateway 设置，需要单独配置。macOS 上的入口是 **Developer → Configure Third-Party Inference**，具体字段见[桌面配置指南](docs/local-setup.md#4-macos-桌面配置)。

这里容易填错的是 Key：桌面端应使用 `private/gateway-token.json` 里的本地 token，OpenAI API Key 由本地网关保存，用于访问 OpenAI。配置保存后，运行下面的命令启动服务并打开桌面端：

```bash
./claudapter desktop
```

也可以双击 `launch-desktop.command`。这不会注册开机自启，重启电脑后需要重新启动服务。

这是非官方的兼容接法。桌面端目前需要 Claude 格式的模型路由别名，实际请求仍转到配置中的 OpenAI 模型；客户端显示的上下文和功能可能因此不准确。建议先在临时目录里测试，再用于自己的文件。

## 目前能用到什么程度

| 功能 | 验证情况 |
|---|---|
| 文本、流式输出、函数调用与结果转换 | 有离线回归，初次配置时也通过了真实 API 检查 |
| Claude Code CLI 文本对话 | 初次配置时实测通过 |
| macOS Claude Desktop 聊天 | 初次配置时实测通过 |
| 桌面文件读写、完整 MCP 流程 | 尚未验证 |
| Windows 桌面与 `.command` 入口 | 不支持 |

后续修改主要通过离线测试检查。CI 配置为 Ubuntu/macOS、Node 24 和 Python 3.10，不运行付费 API 或桌面测试。如果你换了模型或客户端版本，仍建议自己验证一遍。

## 常用命令

在 Claudapter 仓库根目录运行：

```bash
./claudapter status        # 查看本地实例状态，不请求 OpenAI
./claudapter restart       # 修改配置或源码后重启
./claudapter stop          # 停止已确认归属的实例
./claudapter verify        # 重新做真实 API 检查，会产生用量
npm test                  # 离线测试，不需要真实 Key
```

修改模型、Key、代理、运行时或源码后，先结束正在进行的会话，再执行 `restart`。服务不会为了应用新配置而自动打断会话。`status` 只检查保存的实例是否正常，不代表新配置已经生效。

管理器帮助用 `./claudapter --help`；要看 Claude 自身的帮助，用 `./claudapter -- --help`。连接失败、端口占用和重启后残留状态的处理方法都在[排障指南](docs/local-setup.md#6-排障)里。

## 数据和限制

凭证、token、日志和运行状态放在被 Git 忽略的 `private/` 目录。目录权限是 `700`，凭证文件是 `600`，没有做加密存储。请不要上传这个目录。服务只监听本机 `127.0.0.1`，适合个人使用。

提示词和工具结果会转发给 OpenAI。工具本身仍由 Claude 客户端执行，文件访问和命令执行沿用客户端的权限机制。本地服务只负责协议转换，模型运行在 OpenAI 服务器上。

目前没有完整处理缓存、thinking、文档输入、原生服务端工具和工具搜索；工具结果中的图片会转成占位文字。并行工具调用被关闭，OpenAI 加密推理内容不会保留，所以长任务的表现可能与原生客户端不同。token 计数也是估算值，费用请以 OpenAI 账单为准。

重试、取消、并发和 SSE 处理还比较简单。这些都是后续需要补的地方。遇到问题时，欢迎带上客户端版本、模型和脱敏后的复现步骤提 Issue，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

协议转换代码改编自 [hanjun-lin/claude-gpt](https://github.com/hanjun-lin/claude-gpt)，使用 [MIT](LICENSE) 授权。来源和上游提交记录见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

参考文档：[OpenAI 工具调用](https://developers.openai.com/api/docs/guides/function-calling) · [Claude 桌面网关配置](https://code.claude.com/docs/en/llm-gateway-connect#desktop-app)。
