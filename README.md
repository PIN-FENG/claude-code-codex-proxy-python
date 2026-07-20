# Claude Code Codex Proxy — Python

> This repository provides a Windows-tested Python port for routing supported Claude Code requests to an OpenAI model available through Codex.

This project is a small local Python bridge based on [`raine/claude-code-proxy`](https://github.com/raine/claude-code-proxy). It lets Claude Code send supported Anthropic Messages API requests to an OpenAI model using either Codex OAuth or an OpenAI API key.

- **Tested on:** Windows 11
- **Runtime:** A single Python file running locally on your computer
- **Python requirement:** Python 3.11 or newer
- **Default address:** `http://127.0.0.1:18888`
- **Dependencies:** Python standard library only

> [!IMPORTANT]
> This is an unofficial community project. It is not affiliated with or endorsed by Anthropic, OpenAI, or the upstream project author. Model names, availability, provider endpoints, account access, pricing, and terms may change. Review the terms that apply to your Anthropic, Claude Code, OpenAI, ChatGPT, and Codex usage.

> [!WARNING]
> “Runs locally” does **not** mean that your prompts stay on your computer. The proxy forwards supported request content to OpenAI/ChatGPT. Read [Privacy](#privacy) and [`PRIVACY.md`](PRIVACY.md) before use.

## Contents

- [What this project does](#what-this-project-does)
- [Before you start](#before-you-start)
- [Windows 11 setup: complete beginner guide](#windows-11-setup-complete-beginner-guide)
- [How model selection works](#how-model-selection-works)
- [Configuration reference](#configuration-reference)
- [Security](#security)
- [Privacy](#privacy)
- [Compatibility and limitations](#compatibility-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Testing](#testing)
- [Attribution and license](#attribution-and-license)
- [FAQ](#faq)

## What this project does

```text
Claude Code
    |
    | Anthropic Messages-compatible request
    v
Local Python proxy at 127.0.0.1:18888
    |
    | Translated OpenAI Responses request
    v
OpenAI API or ChatGPT Codex endpoint
```

The proxy:

1. Accepts the subset of Anthropic Messages API requests used by Claude Code.
2. Converts messages, supported images, tools, tool calls, and streaming events between the two API formats.
3. Reads your existing Codex configuration and authentication.
4. Sends the translated request to one of two exact, OpenAI HTTPS endpoints allowlisted by this project.
5. Converts the streamed result back into the format expected by Claude Code.

The proxy reads Codex files but does not modify them.

## Before you start

You need all of the following:

1. **Windows 11.** That is the operating system currently tested by this project. Other systems may work, but are not yet claimed as tested.
2. **Python 3.11 or newer.** Download it from <https://www.python.org/downloads/> if necessary.
3. **Claude Code.** Official setup documentation: <https://code.claude.com/docs/en/setup>.
4. **One upstream authentication method:**
   - **Codex OAuth:** Install the [Codex CLI](https://github.com/openai/codex), which normally requires Node.js and npm, then run `codex login`.
   - **OpenAI API key:** Set `OPENAI_API_KEY`; Codex CLI and Node.js are not required for this mode.
5. **An account with access to the OpenAI model you configure.** A model name in a file does not grant account access.
6. **Two PowerShell windows.** Terminal 1 runs the proxy; Terminal 2 runs Claude Code.

## Windows 11 setup: complete beginner guide

Follow the steps in order. Commands marked **PowerShell** should be pasted into PowerShell, not Command Prompt.

### Step 1 — Check Python

Open PowerShell and run:

```powershell
python --version
```

Expected result:

```text
Python 3.11.x
```

A newer version is also acceptable. If `python` is not recognized, install Python and select **Add Python to PATH** during setup, then open a new PowerShell window.

### Step 2 — Choose and configure upstream authentication

#### Option A: Codex OAuth

Use the official Codex documentation if its installation command has changed. The npm installation method is:

```powershell
npm install -g @openai/codex
```

Verify the installation:

```powershell
codex --version
```

If PowerShell says that `npm` is not recognized, install the current Node.js LTS release and open a new terminal.

#### Option B: OpenAI API key

Set the key in Terminal 1 before starting the proxy:

```powershell
$env:OPENAI_API_KEY = "your-openai-api-key"
```

Treat this value as a secret. In API-key mode, you do not need Node.js, Codex CLI, or `codex login`. Continue with the environment-variable model configuration in Step 4.

### Step 3 — Sign in to Codex (OAuth only)

Skip this step if you selected OpenAI API-key mode.

Run:

```powershell
codex login
```

Complete the sign-in flow shown by Codex. Never paste your Codex access token into this repository, an issue, a screenshot, or a chat message.

Codex normally stores its files in your user profile:

```text
~/.codex/config.toml
~/.codex/auth.json
```

On Windows, `~` means your home directory, usually `C:\Users\YOUR-NAME`.

Verify that Codex itself works before continuing:

```powershell
codex
```

Exit Codex after the check.

### Step 4 — Configure the upstream model

For Codex OAuth, open this file in a text editor:

```text
C:\Users\YOUR-NAME\.codex\config.toml
```

Configure a model available to your account. For example:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
```

The current special Responses Lite path recognizes:

- `gpt-5.6-luna`
- `gpt-5.6-sol`
- `gpt-5.6-terra`

These model names and their availability can change. Your account must have access to the selected model.

For either authentication mode, you can select the model and reasoning effort with environment variables in Terminal 1. These variables are required in API-key mode unless another usable Codex configuration supplies a model:

```powershell
$env:CODEX_PROXY_MODEL = "gpt-5.6-sol"
$env:CODEX_PROXY_REASONING_EFFORT = "high"
```

### Step 5 — Install Claude Code

Follow Anthropic’s current official setup instructions:

<https://code.claude.com/docs/en/setup>

After installation, verify it:

```powershell
claude --version
```

This proxy changes the API endpoint used by Claude Code. It does not install or modify Claude Code itself.

### Step 6 — Download this repository

1. On GitHub, select **Code**.
2. Select **Download ZIP**.
3. Extract the ZIP file.
4. Open PowerShell in the extracted folder.

For the remaining steps, the folder containing `codex_proxy.py` is called the **proxy folder**.

### Step 7 — Generate a private local secret

The local secret prevents another local program from using your proxy without permission.

In **Terminal 1**, from the proxy folder, generate a strong random value:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the result. It will look similar to this, but must not be this exact example:

```text
EXAMPLE_ONLY_replace_with_your_random_value
```

You will use the **same exact value** in two places:

1. `CODEX_PROXY_SECRET` in Terminal 1.
2. `ANTHROPIC_AUTH_TOKEN` in your target project’s `.claude\settings.json`.

Do not commit your real value to Git.

### Step 8 — Configure your target coding project

Choose the project where you want to run Claude Code. For example:

```text
C:\Users\YOUR-NAME\Desktop\my-coding-project
```

Inside that project, create this folder and file if they do not exist:

```text
my-coding-project\
└── .claude\
    └── settings.json
```

This repository provides a template at [`examples/claude-settings.json`](examples/claude-settings.json).

#### If your project does not already have `.claude/settings.json`

Copy the example file:

```powershell
$TargetProject = "C:\path\to\your\coding-project"
New-Item -ItemType Directory -Force "$TargetProject\.claude" | Out-Null
Copy-Item ".\examples\claude-settings.json" "$TargetProject\.claude\settings.json"
```

Open the copied file and replace:

```text
REPLACE_WITH_YOUR_RANDOM_LOCAL_SECRET
```

with the random value generated in Step 7.

#### If your project already has `.claude/settings.json`

Do **not** overwrite it. Open both files and merge the entries under `env` into your existing `env` object. JSON permits only one `env` object and does not permit comments.

The result should contain these values:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:18888",
    "ANTHROPIC_AUTH_TOKEN": "REPLACE_WITH_YOUR_RANDOM_LOCAL_SECRET",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "gpt-5.6-sol",
    "ANTHROPIC_SMALL_FAST_MODEL": "gpt-5.6-luna",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1"
  }
}
```

`ANTHROPIC_MODEL` and `ANTHROPIC_SMALL_FAST_MODEL` are Claude-side names. They do **not** choose the upstream OpenAI model in this proxy. The Codex model from Step 4 is authoritative.

### Step 9 — Check and start the proxy in Terminal 1

Make sure Terminal 1 is in the proxy folder:

```powershell
cd C:\path\to\claude-code-codex-proxy-python
```

Set the same secret used in `.claude/settings.json`:

```powershell
$env:CODEX_PROXY_SECRET = "paste-your-random-secret-here"
```

Run the offline self-test:

```powershell
python .\codex_proxy.py --self-test
```

Expected output:

```text
Self-test passed
```

Check your Codex configuration and authentication without sending a network request:

```powershell
python .\codex_proxy.py --check
```

Expected final line:

```text
Configuration is usable (no network request was sent)
```

Start the proxy:

```powershell
python .\codex_proxy.py
```

Expected output includes:

```text
Codex proxy listening on http://127.0.0.1:18888
Using configured model: gpt-5.6-sol
```

Leave Terminal 1 open. Do not close it while using Claude Code.

### Step 10 — Start Claude Code in Terminal 2

Open a **second PowerShell window**. Change to your target coding project, not the proxy folder:

```powershell
cd C:\path\to\your\coding-project
claude
```

Claude Code reads `.claude/settings.json` from that project and sends supported requests to the local proxy.

Try a harmless first request such as:

```text
Explain the files in this project without changing anything.
```

After each completed request, Terminal 1 prints metadata-only token usage similar to:

```text
Codex usage: model=gpt-5.6-sol input=100 cached=0 output=50 total=150
```

### Step 11 — Stop the proxy

When finished, return to Terminal 1 and press:

```text
Ctrl+C
```

PowerShell environment variables set with `$env:...` apply only to that terminal process and its child processes. Set `CODEX_PROXY_SECRET` again when you open a new Terminal 1.

## How model selection works

The upstream model is selected in this order:

1. `CODEX_PROXY_MODEL` environment variable.
2. The model in the active Codex profile.
3. The top-level `model` value in `~/.codex/config.toml`.

The incoming model name sent by Claude Code is **not** used to choose the upstream model. This is why editing only `ANTHROPIC_MODEL` does not change the actual OpenAI model used by the proxy.

## Configuration reference

| Variable | Required | Purpose |
|---|---:|---|
| `CODEX_PROXY_SECRET` | Yes | Local shared secret required to start the proxy and authenticate every POST request. |
| `CODEX_PROXY_PORT` | No | Local port. Default: `18888`. |
| `CODEX_PROXY_MODEL` | No | Overrides the model from Codex configuration. |
| `CODEX_PROXY_REASONING_EFFORT` | No | Overrides reasoning effort: `none`, `minimal`, `low`, `medium`, `high`, or `xhigh`. |
| `CODEX_PROXY_DEBUG` | No | Set to `1` for metadata-only diagnostic logs. Prompts, responses, request bodies, and authentication tokens are excluded; token-usage counts and other non-secret metadata may appear. |
| `CODEX_HOME` | No | Overrides the default `~/.codex` directory. |
| `CODEX_PROFILE` | No | Selects a profile from Codex `config.toml`. |
| `OPENAI_API_KEY` | No | Uses the official OpenAI Responses API instead of Codex OAuth when set. Treat it as a secret. |
| `ANTHROPIC_BASE_URL` | Claude side | Must point Claude Code to `http://127.0.0.1:18888`. |
| `ANTHROPIC_AUTH_TOKEN` | Claude side | Must exactly match `CODEX_PROXY_SECRET`. |

To use a different local port, both sides must match:

```powershell
$env:CODEX_PROXY_PORT = "19000"
python .\codex_proxy.py
```

and:

```json
"ANTHROPIC_BASE_URL": "http://127.0.0.1:19000"
```

## Security

The implementation currently enforces **9 security safeguards**:

### Local and request boundary — 4

1. It binds only to IPv4 loopback: `127.0.0.1`.
2. Every POST requires a shared secret checked with constant-time comparison.
3. POST requests are limited to the supported Anthropic message endpoints.
4. Request bodies are limited to 32 MiB and must be valid JSON objects.

### Upstream credential confinement — 2

5. Credentials can be sent only to an exact hard-coded allowlist of OpenAI HTTPS endpoints allowlisted by this project.
6. HTTP redirects are rejected so bearer credentials cannot follow a redirect to another destination.

### Exposure reduction — 3

7. Upstream requests include `store: false`.
8. Default server logging is disabled; prompts, responses, request bodies, and authentication tokens are not logged.
9. Upstream and unexpected errors are sanitized before being returned to the caller.

Important limitations:

- The local connection uses HTTP, not HTTPS.
- Health-check GET requests do not require the shared secret.
- Any non-empty shared secret is accepted; generate a strong random value.
- This is a local development proxy, not a hardened public server.
- Never change the listener to `0.0.0.0`, expose it through port forwarding, or publish it to the internet.

See [`SECURITY.md`](SECURITY.md) for the complete threat model and reporting instructions.

## Privacy

Supported prompts and content are translated locally, then forwarded upstream. This can include:

- System and developer instructions.
- User prompts.
- Supported images.
- Tool names, descriptions, and JSON schemas.
- Tool calls, arguments, results, and file content returned by tools.
- ChatGPT account ID when Codex OAuth is used.
- Claude Code session ID, metadata user ID used as a prompt-cache key, and generated request IDs.
- Model output.

The proxy requests `store: false`, but that is not a guarantee of zero provider retention. OpenAI’s applicable policies and your account terms still control upstream data handling.

Read [`PRIVACY.md`](PRIVACY.md) before using this project with sensitive, confidential, personal, regulated, or proprietary data.

## Compatibility and limitations

- This is a compatibility bridge, not a complete implementation of every Anthropic API feature.
- Token counting is a local character-based approximation. Actual usage is read from the upstream completion event.
- Some unsupported content blocks are omitted or converted to explanatory text.
- Hosted web-search declarations with a different provider protocol are not fully supported.
- Invalid tool-call argument JSON may become an empty object.
- Some request options, including certain stop and token-limit semantics, may not map exactly.
- The ChatGPT Codex path and Responses Lite behavior use provider-specific endpoints or headers that may change.
- The three hard-coded GPT-5.6 Lite model names may need updates when OpenAI changes model availability.
- Do not assume that a successful startup means your account can access every configured model.

## Troubleshooting

| Problem | Likely cause | What to do |
|---|---|---|
| `python` is not recognized | Python is missing or not in PATH. | Install Python 3.11+ and open a new PowerShell window. |
| `codex` is not recognized | Codex CLI is not installed or npm’s global bin folder is not in PATH. | Install Codex CLI using the official instructions, then reopen PowerShell. |
| `Codex authentication was not found` | `codex login` has not completed for this Windows account. | Run `codex login`, then retry `--check`. |
| `Codex authentication expired` | The saved OAuth session is no longer valid. | Run `codex login` again. |
| `No model is configured` | No model exists in Codex config and no environment override is set. | Add `model = "gpt-5.6-sol"` to `~/.codex/config.toml` or set `CODEX_PROXY_MODEL`. |
| `CODEX_PROXY_SECRET is required` | Terminal 1 does not have the proxy secret. | Set `$env:CODEX_PROXY_SECRET` before starting the proxy. |
| Claude Code receives HTTP 401 | The two local secret values differ. | Make `ANTHROPIC_AUTH_TOKEN` exactly match `CODEX_PROXY_SECRET`, including capitalization. Restart Claude Code after editing settings. |
| Claude Code cannot connect | The proxy is stopped, or the ports differ. | Keep Terminal 1 open and use port `18888` in both places. |
| Port is already in use | Another process is using `18888`. | Stop the other process or set a different matching proxy/base-URL port. |
| OpenAI returns HTTP 401 | Codex/OpenAI authentication is invalid or expired. | Run `codex login` again, or check `OPENAI_API_KEY` if using API-key mode. |
| Configured model is unavailable | Your account lacks access or the model name changed. | Test the model in Codex and choose one available to your account. |
| Upstream redirect was rejected | The provider redirected an allowlisted endpoint. | Update Codex CLI and this project. Do not disable redirect protection as a workaround. |
| An old copy asks for `dotenv` | You are running an earlier version. | Download the current repository version; this release uses only the standard library. |
| An old copy starts on `18765` | You are running an earlier version. | Update the project, or ensure both sides temporarily use the same port. |

For metadata-only diagnostics:

```powershell
$env:CODEX_PROXY_DEBUG = "1"
python .\codex_proxy.py
```

Review debug output before sharing it because local model names, paths, and status metadata may still be visible. The program is designed not to log prompts, responses, or credentials.

## Testing

Run all offline checks from the repository root:

```powershell
python -m compileall .
python .\codex_proxy.py --self-test
python -m unittest discover -s tests -v
```

Public CI uses fake data and does not make live Codex/OpenAI requests.

## Attribution and license

This repository is a Python port and derivative work based on:

- [`raine/claude-code-proxy`](https://github.com/raine/claude-code-proxy) by Raine Virta

The upstream project is licensed under the MIT License, which permits modification and redistribution provided that its copyright notice and license terms are retained. The upstream notice and complete MIT terms are preserved in [`LICENSE`](LICENSE), with additional provenance and change information in [`NOTICE`](NOTICE).

This derivative work is also distributed under the MIT License. Before publishing, replace the temporary `PIN-FENG` copyright placeholder in [`LICENSE`](LICENSE) with the actual person or entity that owns the Python contributions.

## FAQ

### Does this modify my Codex credentials?

No. It reads Codex configuration and authentication files but does not modify them.

### Are my prompts completely local?

No. Translation and the HTTP listener are local, but supported request content is forwarded to OpenAI/ChatGPT for model inference.

### Do I need an Anthropic API key?

The example does not use one. Claude Code sends requests to this local bridge and authenticates to it with the local shared secret. Your use of Claude Code must still comply with Anthropic’s applicable terms.

### Do I need an OpenAI API key?

Not when using Codex OAuth created by `codex login`. If `OPENAI_API_KEY` is set, the proxy intentionally uses OpenAI API-key mode instead.

### Can I run this on macOS or Linux?

It may work because it uses the Python standard library, but this release has only been tested on Windows 11. Contributions adding verified instructions are welcome.

### Can I expose the proxy to another computer?

No. It is intentionally bound to `127.0.0.1` and is designed only for local use.

### Why does Claude Code show one model name while usage shows another?

Claude-side model aliases do not select the upstream model. Check `CODEX_PROXY_MODEL` and `~/.codex/config.toml` for the actual upstream choice.

### How do I get help?

Check the troubleshooting table first. Open an issue in this repository without including credentials, tokens, private prompts, source code you cannot share, or personal paths.
