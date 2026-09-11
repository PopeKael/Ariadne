# Discovery + Signal local workflow

This repository now has one Compose model with explicit overlays:

| File | Purpose | Ports | Storage |
| --- | --- | --- | --- |
| `compose.yaml` | shared service/build/network contract | internal `8788`/`8789` | supplied by overlay |
| `compose.local.yaml` | Docker Desktop DEV | Signal `18788`, Discovery `18789` | `runtime/discovery-signal-dev/signal` and `runtime/discovery-signal-dev/discovery` |
| `compose.hera.yaml` | reviewed Hera production cutover | Signal `8788`, Discovery `8789` | existing `/volume1/docker/.../data` paths |

Ariadne Home remains at [http://localhost:8765/](http://localhost:8765/); this
workflow does not claim or change that port.

## Everyday local development

From the repository root:

```powershell
.\scripts\discovery-signal.ps1 up
.\scripts\discovery-signal.ps1 status
.\scripts\discovery-signal.ps1 down
```

`down` intentionally does not use `-v`, so DEV SQLite state survives a
restart. DEV Signal has built-in feeds disabled and DEV Discovery sends only
to the Compose peer `http://signal:8788`. It cannot use Hera's `8788` because
the target is private to the local Compose network.

The status command prints `state`, `environment`, `instance`, and `build_sha`
for both services. The same fields are available at:

```text
http://localhost:18788/v1/health
http://localhost:18789/v1/health
```

For a clean configuration review without starting containers:

```powershell
.\scripts\discovery-signal.ps1 config
```

## Hera cutover

No Hera deployment is performed by this change. When a reviewed commit has
passed local acceptance, run the following on Hera from that exact clean
checkout (or through the operator's existing remote Docker/SSH workflow):

```powershell
$sha = (git rev-parse HEAD).Trim()
.\scripts\discovery-signal.ps1 hera-preflight -BuildSha $sha
.\scripts\discovery-signal.ps1 hera-cutover -BuildSha $sha -ConfirmCutover
```

The script requires the full current `HEAD` to equal `-BuildSha`, refuses a
dirty checkout, validates the existing Signal and Discovery data directories
and SQLite files, and checks that the two existing production containers are
present. It then stops only those exact containers (never their bind-mounted
data), starts the reviewed build as `ariadne-*-service-prod`, and verifies
both health responses report `environment=prod`, the Hera instance, and the
requested SHA.

The default data paths are the current production locations:

```text
/volume1/docker/ariadne-signal-service/data
/volume1/docker/ariadne-discovery-service/data
```

Set `HERA_SIGNAL_DATA_PATH` or `HERA_DISCOVERY_DATA_PATH` only when the
confirmed existing path differs. The overlay uses the internal `signal:8788`
route, so production Discovery also cannot accidentally push to a host or
DEV Signal instance.

If post-cutover verification fails, the old containers remain available for a
deliberate rollback:

```powershell
.\scripts\discovery-signal.ps1 hera-rollback -BuildSha $sha -ConfirmCutover
```

Rollback stops/removes only the new Compose containers and starts the retained
old containers. Neither path runs `docker compose down -v`.
