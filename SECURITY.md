# Security policy

## Supported versions

Token Sprout is currently a pre-release project. Security fixes are applied to
the latest code on the default branch until the first stable release defines a
longer support window.

## Reporting a vulnerability

Please do not open a public issue for a suspected credential leak, proxy
bypass, unsafe file permission, or other vulnerability. Use GitHub's private
**Security → Report a vulnerability** form for this repository. Include the
affected version or commit, operating system, reproduction steps, and the
impact you observed. Do not include real API keys, OAuth tokens, prompts, or
completions in the report.

You should receive an acknowledgement within 72 hours. A fix and disclosure
timeline will be coordinated through the private report.

## Security boundary

The proxy listens only on `127.0.0.1`. It forwards authentication headers but
does not parse, log, or persist them. A malicious process running as the same
OS user is outside the threat boundary because it can already read that
user's Claude credentials. See the README's security model for the complete,
auditable list of files written to disk.
