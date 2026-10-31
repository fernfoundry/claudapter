# Contributing to Claudapter / 参与贡献

The most useful contribution is a small reproducible client flow that either works or exposes a gap. General compatibility claims are hard to verify.

最有价值的贡献是可复现的客户端流程：说明什么能用、哪里失败，而不是笼统宣称兼容所有功能。

## Run the tests

Use Node.js 24+ and Python 3.10+, then run from the repository root:

```bash
npm test
```

No dependency installation or real API key is required. Tests use mocks, temporary loopback listeners, and owned temporary processes. `setup` and `verify` are different: they use the real API and incur usage.

## Good first contributions

- Reproduce one real CLI/Desktop tool flow in a disposable directory; record client and model versions.
- Add a failing protocol fixture before fixing a translation bug.
- Improve unsupported input handling rather than silently dropping important content.
- Validate startup, shutdown, and recovery across Ubuntu and macOS.
- Translate manager output and expand English troubleshooting.

Keep patches focused. Do not change stored service identities or credential formats just to rename the project. A rename of display text should not force a runtime migration.

## Report a bug

Include OS, Python/Node/Claude versions, model ID, whether a proxy is configured, exact steps, expected result, and a **redacted** error. Distinguish CLI from Desktop and chat from file/tool operations. State whether the failure comes from an offline test or a billable API check.

Never attach `private/`, real API keys, gateway tokens, complete state/settings files, or unreviewed logs. Use synthetic prompts and files when possible. Do not run `verify` or a live API test as a hidden part of offline CI.

请勿上传 `private/`、真实 Key、本地 token 或完整配置/状态文件。优先使用临时文件与无私密内容的提示词复现。
