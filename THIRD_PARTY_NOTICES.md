# 源码来源

Node.js 协议转换代码改编自 [hanjun-lin/claude-gpt](https://github.com/hanjun-lin/claude-gpt)，MIT License。

本次检查与下载的上游提交：`801c7547a3baf2ff053d35f183f5a4bce5f514ac`。
原项目的 MIT 授权与版权信息保存在根目录 LICENSE。

本地修改：

- 提取 Messages、工具调用与 SSE 转换，删除 Codex OAuth 和 Anthropic 透传路径。
- 固定上游为 `https://api.openai.com/v1/responses`，使用用户自己的 API Key。
- 增加本地鉴权、固定端口、持久化本地访问 token。
- 增加 Python 配置管理、启动入口和验证。
- 修复分段 SSE 校验、流式错误、桌面探测最小输出 token 等兼容问题。

这是本地兼容实现，不是 Anthropic 或 OpenAI 官方客户端插件。

## 说明插图

`docs/assets/protocol-adapter.svg` 使用 [Anatomy](https://github.com/wheresryan22/anatomy) 的等距几何与 SVG 样式工具生成。工具采用 MIT License，Copyright (c) 2026 Ryan；授权文本保存在 `docs/assets/anatomy-LICENSE.txt`。插图为本项目制作，表示协议转换关系，不是实际硬件拓扑。
