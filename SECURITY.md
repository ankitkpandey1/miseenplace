# Security policy

## Reporting a vulnerability

Please report privately through GitHub: Security tab, "Report a vulnerability" on this repository. Do not open a public issue. Expect an acknowledgement within a week.

## Scope

In scope: leaking credentials or personal data, bypassing the consent gate (sending text that was not stored as a draft, or sending a draft twice), path traversal out of the data root, unsafe handling of chat content.

Known limit, documented in [docs/security.md](docs/security.md): the consent gate is structural. It stops free-text sends, not an agent that runs `mep send` on a draft you did not approve. Configure your agent harness to ask before `mep send`.

## Supported versions

The latest release.
