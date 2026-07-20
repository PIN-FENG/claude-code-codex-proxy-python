# Privacy

## Summary

The proxy process and protocol translation run locally, but model inference does not. Supported content is forwarded to an OpenAI API or ChatGPT Codex endpoint.

Do not interpret “local proxy” as “prompts remain on this computer.”

## Data that may be forwarded

Depending on the Claude Code request, the proxy may forward:

- System and developer instructions.
- User prompts and conversation history.
- Supported images, including embedded base64 data.
- Tool names, descriptions, and JSON schemas.
- Tool calls and tool arguments.
- Tool results, including file contents or command output returned to the model.
- Error text returned by local tools.
- The configured model and reasoning effort.
- A ChatGPT account ID when Codex OAuth is used.
- Claude Code’s session ID.
- A metadata user ID used as an upstream prompt-cache key.
- A newly generated client request ID.
- The model’s generated response.

The exact content depends on Claude Code, enabled tools, the prompt, and the files or commands the user allows Claude Code to access.

## Credentials

The proxy reads Codex authentication from `~/.codex/auth.json`, or uses `OPENAI_API_KEY` if that environment variable is set. It sends the required bearer credential only to an exact hard-coded allowlist of OpenAI HTTPS endpoints and rejects redirects.

The local `CODEX_PROXY_SECRET`/`ANTHROPIC_AUTH_TOKEN` is used only to authenticate Claude Code to the local proxy. Do not publish or reuse it.

## Logging

The proxy intentionally disables ordinary HTTP request logging and does not log prompts, responses, request bodies, or authentication tokens. It prints token-usage counts after completed requests.

Optional debug mode is designed to log metadata only. That metadata may still include model names, local paths, endpoint names, status codes, and error types. Inspect logs before sharing them.

## Storage and provider policies

Translated requests include:

```json
"store": false
```

This expresses a storage preference to the upstream service. It is not a contractual promise or technical proof of zero retention. Provider safety systems, legal obligations, account settings, service terms, and privacy policies may still apply.

Review the current policies and terms for the services and accounts you use. This project does not control upstream processing.

## Sensitive data

Before using this project with confidential, personal, regulated, proprietary, or client data:

1. Confirm that you are authorized to send that data to the selected upstream provider.
2. Review the provider’s current terms, privacy policy, retention controls, and organizational settings.
3. Review which Claude Code tools are enabled and what files they can access.
4. Remove unnecessary secrets and personal information from prompts and tool results.
5. Do not use this project if your policy requires all inference to remain offline.

## No telemetry from this repository

The Python proxy does not contain a separate analytics or telemetry service maintained by this repository. Network requests required for model inference still go to the configured OpenAI/ChatGPT endpoint, and Claude Code may have its own behavior governed by its configuration and terms.
