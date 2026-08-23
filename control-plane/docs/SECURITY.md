# Security and public/private boundary

## Public repository may contain

- Source code.
- Architecture notes and diagrams.
- Sanitised configuration schemas.
- Example environment files with placeholder values.
- Tests, mock telemetry, and deployment instructions.
- Storage policy and operating-profile definitions.

## Private data must remain outside the public repository

- Passwords, API keys, tokens, cookies, and certificates.
- SSH keys and recovery codes.
- Real NAS, Windows, router, or cloud credentials.
- Exact private network details where they are not required by the documentation.
- Personal files, Knowledge Vault content, media, model files, logs containing sensitive data, and backups.
- Machine-specific paths or identifiers when they reveal private information.

## Rules

- Bind development services to loopback unless remote access is deliberately enabled.
- Do not put credentials in browser local storage.
- Use a private authenticated channel between the Synology deployment and the Windows agent.
- Keep the Windows agent least-privileged; use a narrowly scoped elevated helper only for approved actions.
- Treat dashboard status as telemetry, not proof of authorisation.
- Add secret scanning before the first public push.
