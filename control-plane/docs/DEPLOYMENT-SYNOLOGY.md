# Synology deployment concept

The Synology deployment is the always-on reference instance of Ariadne's browser interface.

## Development path

1. Build and test the interface locally on Windows.
2. Keep the host agent local and loopback-bound during early testing.
3. Commit sanitised source and documentation to a public GitHub repository.
4. Pull the repository on the Synology.
5. Build or serve the static dashboard there.
6. Add authenticated Windows-agent communication only after the read-only model is trusted.

## Important boundary

The Synology page may display the Windows machine, but it must not receive unrestricted shell access. Remote actions should be named API operations with validation, authorisation, timeouts, and audit records.

## Deployment questions to verify later

- Synology CPU architecture and supported runtime.
- Whether the preferred host is Web Station, Container Manager, or a lightweight native service.
- HTTPS and local-network access policy.
- Authentication method for the private dashboard.
- Backup and rollback method for the deployed release.
