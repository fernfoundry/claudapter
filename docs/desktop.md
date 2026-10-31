# Claude Desktop with Claudapter

This macOS configuration uses Claude Desktop's third-party Gateway mode. It is experimental: the initial setup verified chat, not Desktop file tools or complete MCP workflows.

[Back to README](../README.md) · [中文设置指南](local-setup.md#4-macos-桌面配置)

## Before you start

- Install a Claude Desktop version with third-party inference configuration.
- Run `./claudapter setup` in the repository for guided configuration and API checks.
- Keep the adapter running with `python3 manage.py start`.
- Check the configured local instance with `python3 manage.py status`.

If another copy already owns the same port, do not start or kill an arbitrary instance. See [troubleshooting](troubleshooting.md).

## Configure the app

1. In **Help → Troubleshooting**, choose **Enable Developer Mode** if available. Restart as prompted.
2. Open **Developer → Configure Third-Party Inference**.
3. In Connection, select **Gateway** and **Static API key**.
4. Fill the fields below.
5. Choose **Apply Changes → Save & Restart**.

| Field | Value |
|---|---|
| Gateway base URL | `http://127.0.0.1:18789`, without `/v1`; use your configured port |
| Gateway API key | The `token` value from local `private/gateway-token.json` |
| Gateway auth scheme | `bearer` |
| Model discovery | **Off**; the adapter does not implement `GET /v1/models` |
| Model ID | Try `claude-sonnet-4-5` as a compatibility route |
| Display name | `OpenAI via Claudapter` |

Open the token file locally and copy only the token value. **Do not use your OpenAI API key in Desktop's gateway field.** Do not upload either key or the whole private directory.

### Understand the model alias

The Desktop form tested during initial setup rejected the literal OpenAI model ID. A Claude-shaped route was accepted, so the adapter maps that route to the actual OpenAI model in `config.json`.

This is a compatibility alias, not a claim that a Claude model is running. Desktop may infer the wrong context limits or capabilities from it. The official Gateway feature targets Claude models; newer app versions may reject this use. Do not bypass client or OS permission controls to make it work.

## Start and check

```bash
./claudapter desktop
```

On macOS you can also double-click `launch-desktop.command`. This starts the adapter before opening Claude, but does not configure Desktop automatically or register an auto-start service.

First send a synthetic, text-only message:

```text
Do not use tools or access files. Reply with exactly CLAUDAPTER_OK.
```

This is a live API request and incurs usage. A successful reply proves chat connectivity, not file or tool compatibility.

For Code mode, choose the project folder deliberately and review the workspace/OS permission prompts. Test in a disposable directory before using valuable files. During the initial setup, the Code workspace-trust step stalled and required an app restart; file reads and writes were not verified.

## Update or roll back

Changes to the model, key, proxy, runtime or source need an explicit `python3 manage.py restart`. Finish active sessions first. The default local URL and token persist across ordinary restarts.

If the port or token changes, update Desktop too. To return to your previous Desktop provider, switch the third-party inference configuration back and restart the app. CLI users can run their original `claude` command with the original global configuration.

Official references: [Connect Desktop to a gateway](https://code.claude.com/docs/en/llm-gateway-connect#desktop-app) · [Gateway fields](https://claude.com/docs/third-party/claude-desktop/gateway).
