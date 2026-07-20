# Security Policy

## Intended use and threat model

This project is a local development bridge for one user on one computer. It is intentionally bound to IPv4 loopback (`127.0.0.1`) and is not designed to be a public, shared, remote, or production API service.

Never:

- Change the listener to `0.0.0.0` or a LAN/public address.
- Expose the port through a router, tunnel, reverse proxy, container publishing rule, or cloud service.
- Commit Codex authentication files, API keys, bearer tokens, account IDs, or a personalized Claude settings file.
- Reuse the placeholder secret from the example configuration.

## Enforced safeguards

The current implementation has **9 distinct enforced safeguards**.

### Local and request boundary — 4

1. **Loopback-only listener:** the server binds to `127.0.0.1`.
2. **Authenticated POST requests:** every POST requires `CODEX_PROXY_SECRET`, and comparison uses `hmac.compare_digest`.
3. **Restricted POST routes:** the proxy accepts only `/v1/messages` and `/v1/messages/count_tokens` for POST requests.
4. **Body validation:** request bodies are limited to 32 MiB and must decode to a JSON object.

### Upstream credential confinement — 2

5. **Exact HTTPS endpoint allowlist:** credentials can be sent only to the exact OpenAI endpoints hard-coded in `ALLOWED_UPSTREAM_ENDPOINTS`.
6. **Redirect rejection:** upstream HTTP redirects are rejected instead of following credentials to a new location.

### Exposure reduction — 3

7. **Storage opt-out request:** translated upstream requests set `store` to `false`.
8. **Sensitive logging disabled:** normal HTTP access logging is disabled, and debug diagnostics are designed to exclude prompts, responses, request bodies, and authentication tokens.
9. **Sanitized errors:** upstream and unexpected failures return controlled messages rather than raw provider bodies, credentials, or exception details.

## Known limitations

- Local traffic is HTTP rather than HTTPS. Loopback binding is part of the security boundary.
- Health-check GET requests are unauthenticated.
- The program requires a non-empty secret but does not enforce a minimum length. Generate a random secret with:

  ```powershell
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- The standard-library threaded HTTP server does not enforce a concurrency or rate limit. A leaked local secret could allow another local process to consume memory, threads, upstream usage, or account quota.
- The maximum request size is 32 MiB and the upstream timeout is 600 seconds.
- Debug logs may expose non-secret metadata such as local paths, model names, endpoint names, request status, and error types. Review logs before sharing them.
- `store: false` is an upstream request setting, not a guarantee of zero provider retention.

## Reporting a vulnerability

Do not disclose an unpatched vulnerability in a public issue.

Use GitHub’s **Report a vulnerability** feature under this repository’s **Security** tab if private vulnerability reporting is enabled. If it is not enabled, open a minimal issue asking the maintainer to provide a private reporting channel. Do not include exploit details, credentials, private prompts, or tokens in that public issue.

A useful private report includes:

- The affected version or commit.
- Windows/Python versions.
- A clear reproduction using fake credentials and non-sensitive data.
- The expected and actual security behavior.
- A suggested mitigation, if available.

## Supported versions

Only the latest published release receives security fixes. Users should reproduce an issue on the latest release before reporting it when it is safe to do so.
