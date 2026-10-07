# KStore maintenance - 2026-10-07

Warren authorized obsolete container/image cleanup, DSM project alignment and
updates to existing official images.

## Confirmed causes

Article preparation deployment renamed stopped containers and copied their old
Compose labels to replacements, retaining rollback containers indefinitely.
Discovery ran under discovery-service while DSM registered
ariadne-discovery-service. Cache/news were unmanaged.
Two RUNNING n8n containers used the SAME /volume1/docker/n8n/data database.

## Access

Warren installed scripts/enable_nas_docker_access.sh through password-only SSH.
A fresh Codex key-based session independently verified passwordless Docker sudo.
See hera-ssh-access.md: use the normal sandbox context for its owned key.
Future Docker maintenance does not need another password from Warren.

## Completed

- Audit found 20 containers. Removed 9 verified stopped/never-started obsolete
  containers by exact inspected IDs. No volumes or bind data deleted.
- All four Ariadne backends now use real Compose-managed containers with original
  immutable images, ports, environment, commands and data mounts. Their original Docker
  networks are preserved, including Signal's proxy-dependent network.
- Discovery project name matches DSM.
- Cache registered in DSM as ariadne-article-cache.
- All four backend endpoints responded after reconciliation. Signal briefly
  exceeded its startup verification window; internal and published endpoints
  were subsequently independently verified.
- Future deploy_article_preparation.py uses authoritative Compose definitions,
  with project-managed update/rollback and database/definition archives.
  No indefinitely retained renamed containers. Three focused tests pass, including preservation of the original network.
- Official httpd, n8n and SearXNG replacement images pulled successfully.

## Official updates and cleanup

- Archived previous official images in the private updates folder (1.73 GB tar).
- All three websites run Apache 2.4.69; all respond HTTP 200.
- n8n updated from 2.39.6 to 2.42.3. Full offline backup includes its node-owned
  encryption config. Verified six workflows before and after. Removed the
  duplicate process; both 5678 and 32770 publish the ONE managed process.
- SearXNG updated to 2026.10.4-d48c4b555, retaining the exact existing configuration/cache volumes via
  explicit external references. Enabled JSON alongside HTML because the existing
  Discovery integration requests JSON and the old configuration returned 403.
  The representative Thailand news JSON search returns ten results. Three
  upstream engines report existing CAPTCHA/access/rate restrictions; DuckDuckGo
  provides usable results. No CAPTCHA bypass or security weakening performed.
- Archived and removed the unmounted temporary ttyd web shell.
- Updated the deployment helper BOTH in repository source and on the NAS staging source.
- All ten HTTP checks passed, including both n8n ports and all Ariadne backends.
  Signal semantic health is healthy: 11 stored interest embeddings, 11,618
  stored signal embeddings and 72 matches.
- Unused image pruning completed: 34 images reduced to 7, reclaiming 3.62 GB.
  No volumes were pruned. The seven remaining images serve the nine containers.

## Private NAS records

/volume1/docker/ariadne-maintenance-20261007/

Full inspect/config records stay in owner-only private-before, private-reconcile
and private-updates folders. Environment values must never enter Git/chat.
inventory.json is sanitized. SQLite backups use the backup API after the owner
stops. Persistent volumes are never globally pruned.

## Verification

Three Compose deployment/network regression tests and four existing slider
regressions pass. Source syntax checks and git diff whitespace checks pass.
Ariadne Home displayed Discover cards, recent chats, weather and markets during
maintenance. The temporary semantic-provider failure caused by network placement
was identified by a private endpoint 403 and fixed by restoring its original
network; no allowlist was broadened.

Final daemon/Compose/DSM parity (all nine DSM project indicators green): nine projects, nine running containers, zero
unmanaged or stopped containers, and seven current images. Eleven obsolete
containers removed in total. DSM Build adopted the already-running cache/news
projects so their status/control records match the live containers.

Rendered Home's Signal and semantic badges are healthy; 104 Discover cards,
weather and five market cards are populated. Temporary maintenance processes
have finished. Private rollback archives remain intentionally documented.

Warren accepted the live cleanup and requested a full GitHub checkpoint on
7 October 2026. This checkpoint includes the maintenance tooling, deployment
regressions, access runbook, bug log and Interest priority Save controls.
Private NAS configurations, databases and rollback archives remain on the NAS.
The earlier Interest ranking checkpoint is already in repository history; this
maintenance preserved the deployed backend versions.
