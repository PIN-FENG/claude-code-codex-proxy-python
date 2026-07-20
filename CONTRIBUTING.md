# Contributing

Thank you for helping improve this project.

## Principles

- Keep the runtime understandable and local-first.
- Keep `codex_proxy.py` compatible with Python 3.11+ and, when practical, standard-library-only.
- Write user-facing documentation, messages, comments, and tests in clear English.
- Never add real credentials, tokens, account IDs, private prompts, or user-specific paths.
- Do not weaken loopback binding, local authentication, upstream endpoint validation, redirect rejection, logging protections, or error sanitization.
- Be precise about compatibility. Do not describe the proxy as a complete Anthropic API implementation.
- Preserve `LICENSE` and `NOTICE`, including the upstream copyright and MIT terms.
- When importing or adapting upstream changes, record the upstream repository and exact commit or release, update `NOTICE` when the provenance or change summary is affected, and retain any applicable license headers.

## Development setup

No third-party runtime dependency is required.

From the repository root, run:

```powershell
python -m compileall .
python .\codex_proxy.py --self-test
python -m unittest discover -s tests -v
```

All public automated tests must be offline and use fake credentials and non-sensitive fixtures.

## Pull requests

A pull request should:

1. Explain the problem and the chosen solution.
2. Include regression tests for behavior changes.
3. Update README, security, or privacy documentation when behavior changes.
4. Pass all offline checks on a supported Python version.
5. Avoid unrelated formatting or refactoring.
6. State whether it changes upstream data, credentials, identifiers, endpoints, or logging.

Live account tests must not run in public CI. If a change requires manual provider testing, describe the non-sensitive procedure and result without publishing credentials or private content.

## Reporting bugs

Include:

- Python and Windows versions.
- The proxy version or commit.
- The command used.
- Expected and actual behavior.
- A minimal reproduction with fake data.

Remove tokens, secrets, private prompts, personal paths, account IDs, and proprietary source code before posting.

For security issues, follow [`SECURITY.md`](SECURITY.md) instead of opening a detailed public issue.
