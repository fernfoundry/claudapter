# Troubleshooting Claudapter

[Back to README](../README.md) · [完整中文指南](local-setup.md#6-排障)

## First-run setup

Run `./claudapter` for guided configuration, or `./claudapter setup` to configure without opening a client. A terminal is required for missing configuration/credentials. Existing files are preserved. If verification fails, a `private/setup-pending.json` marker keeps the next launch from skipping the failed checks. Do not delete it just to bypass a failed API check.

## Start with the local checks

Run from the repository root:

```bash
python3 manage.py --help
python3 manage.py status
npm test
```

`status` checks stored identity, authenticated loopback health and PID. It does not call OpenAI or confirm that current configuration has been applied. No state means “not managed here,” not “the port is free.” `npm test` requires Node 24+ and Python 3.10+, uses temporary processes/listeners, and does not use real API keys.

| Symptom | Check first |
|---|---|
| Missing `config.json` or malformed JSON | Copy the example on first setup; check quotes, commas and types |
| No Node / wrong Node version | Use Node 24+; terminal and Finder can have different PATH values |
| Local 401 / `Invalid local gateway token` | Desktop must use the **local token**, not the OpenAI key |
| OpenAI 401 | Check the OpenAI API key and its project/organization |
| OpenAI 429 | Read the error code; balance, budget and rate limits differ |
| Gateway unreachable | Start Claudapter, then check the configured URL and optional network proxy |
| Model unavailable / HTTP 400 | Read client Details; check model access, Responses support and reasoning value |
| Start asks for `restart` | Configuration, credentials, source or relevant runtime/environment changed |

## Node.js and Finder

No package installation is needed for this project, but the runtimes must already exist. The manager checks PATH and common NVM locations. You can explicitly provide executable paths in a terminal:

```bash
CLAUDE_OPENAI_NODE=/absolute/path/to/node \
CLAUDE_OPENAI_CLI=/absolute/path/to/claude \
python3 manage.py launch
```

Replace both paths. Node still needs to meet the version requirement. Temporary variables in another terminal are not automatically inherited by Finder.

## Proxy or direct connection?

`https_proxy` in `config.json` is optional. Keep it empty for direct access; otherwise provide your own working `http://host:port` or `https://host:port` proxy. A random local browser page is not proof that the port is an HTTP proxy.

Local health and smoke requests must stay on loopback. The adapter clears inherited proxy variables before applying its configured network route. Changes to proxy configuration require `restart`.

A bridge is still needed on a network with direct OpenAI access: it translates protocols. The network proxy only affects connectivity.

## Stale state, occupied ports and explicit restarts

The manager deliberately does not kill processes solely because a PID or port appears in a file. `stop` and `restart` verify the saved service identity with a local token first.

After a reboot or a move from an older version, a stored instance may no longer answer. If identity cannot be verified:

1. Identify the process, its command and its originating directory. A listener/PID list is a clue, not proof of ownership.
2. Stop only an instance you have independently confirmed belongs to you, using its original controls or a system process tool.
3. Confirm that the old instance is gone and the port is available.
4. Remove **only the stale `private/state.json` from the corresponding repository**, then start again.

Do not remove the whole private directory or send signals to a PID copied from an unverified state file. Keep credentials and the persistent local token unless you intentionally want to replace them.

For a healthy instance after a configuration change, finish active sessions and run:

```bash
python3 manage.py restart
```

There is no source hot reload or automatic service startup after reboot.

## Streaming or tool failures

```bash
npm test
python3 manage.py verify
```

The second command makes **billable API requests**. Offline tests do not establish real model access; a live smoke check does not establish complete Desktop tool compatibility.

The stream checker reconstructs text deltas before looking for its expected response. An error event or missing completion is a failure. The adapter raises Desktop's one-token model probe to at least 16 output tokens, but this does not resolve every possible model constraint.

Unsupported inputs such as full document blocks, server-side tools or images inside tool results may need additional translation support. Include a small synthetic reproduction in a bug report.

## Share diagnostics safely

Local diagnostics are under `private/gateway.log` and `private/verification.json`. Review and redact any excerpt before sharing. Never upload `private/`, real API keys, gateway tokens, complete state/settings files, or unreviewed logs.

The credential files use local permissions, not Keychain or encryption. Prompts and tool results are sent to OpenAI; tool execution and file permissions remain the client's responsibility.
