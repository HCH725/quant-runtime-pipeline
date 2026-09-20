# Quant Runtime Observability / Display Plane

Production read-only observability/display for the quant research loop, built on gethomepage/homepage v2.4.0. It is a viewer, not a monitoring authority and not a control plane. If this layer is down, Qlib, reconciler, handoff, survivor lifecycle and authoritative `/results` evidence continue unchanged.

## Production topology

```text
Runtime truth/read-back
  ├─ /Volumes/ExpansionDrive/qlib-results
  ├─ quant_runtime_watchdog.json
  └─ Kanban read-back
             │
             ▼
runtime/candidate_snapshot.py --dashboard-json
             │  every 300s
             ▼
~/quant-dashboard/data/dashboard.json
             │
             ├─ runtime/dashboard_serve.py 127.0.0.1:8787
             │      └─ /dashboard.json + /detail
             └─ Homepage v2.4.0 127.0.0.1:3000
                    │ widgets poll every 60s
                    ▼
        Cloudflare Tunnel + Access
                    ▼
        https://quant.vicchong1983.trade
```

`/detail` routes to port 8787; the remaining `quant.vicchong1983.trade` paths route to Homepage on port 3000. Both services bind loopback only. Cloudflare Access is the remote authentication boundary.

## Components

| Component | Role |
|---|---|
| `homepage/{settings,services,bookmarks}.yaml`, `custom.css` | presentation/configuration |
| `runtime/candidate_snapshot.py --dashboard-json ...` | single read-only payload producer |
| `runtime/dashboard_serve.py` | loopback JSON/detail server |
| `ai.quant.dashboard-refresh` | launchd one-shot refresh, `StartInterval=300`; no Discord post |
| `ai.quant.dashboard-payload` | launchd keepalive for port 8787 |
| `ai.quant.dashboard-homepage` | launchd keepalive for Homepage on port 3000 |
| Cloudflare Tunnel + Access | authenticated delivery only; no runtime/control-plane role |

## Presentation contract

- **System Health** passes through `quant_runtime_watchdog.py`; the dashboard has no independent health algorithm.
- **Current Research** presents the current family/card/stage/progress/read-back. `unknown` or attention text can be truthful upstream runtime state rather than a display failure.
- **Research Funnel** presents reviewed → ingested strategies → registered families → completed-backtest families.
- **Leaderboard** shows Top 3 on Homepage and up to Top 10 on `/detail#leaderboard`. Sharpe, annualized return and MaxDD come from existing frozen survivor evidence/leaderboard fields. Missing historical annualized return renders as `—`; it is never guessed.
- **Freshness** keeps results readability, leaderboard evidence time and dashboard snapshot time separate.
- **Quick Access** contains Detail, Runtime GitHub and Strategy Research. CatDesk is intentionally not linked: port 3200 is an MCP/API service and its root path is not a browsable UI.

## Hard boundaries

- **No second monitor**: watchdog remains the health truth.
- **No second computation**: the display layer does not re-rank strategies or create performance truth.
- **No control path**: no display action may mutate Kanban or `/results`, or start/stop/retry a backtest.
- **No evidence writes**: `dashboard.json` lives under `~/quant-dashboard/data`, never `/results`.
- **Loopback only**: Homepage = `127.0.0.1:3000`; payload/detail = `127.0.0.1:8787`.
- **Non-blocking**: display failure is never a runtime gate.

## Refresh and deployment

`ai.quant.dashboard-refresh` runs the checked-out repo script every 300 seconds:

```sh
/opt/homebrew/bin/python3 /Users/hong/workspace/quant-runtime-pipeline/runtime/candidate_snapshot.py \
  --dashboard-json /Users/hong/quant-dashboard/data/dashboard.json
```

Homepage widgets poll every 60 seconds. The refresh job is independent of Discord notification cron retirement.

The refresh job executes the working-tree path directly. Therefore an uncommitted edit to `runtime/candidate_snapshot.py` can affect display output before it is committed. This cannot rewrite authoritative research evidence, but intentional display-producer changes should be reviewed/committed before production deployment.

Homepage `/` is SSG (`getStaticProps`). Changes to `settings.yaml`, `services.yaml` or `bookmarks.yaml` require a rebuild. After `pnpm build`, this deployment must also contain:

```text
.next/standalone/.next/static
.next/standalone/public
```

Missing either can produce a partially rendered page even when `/` returns 200. `custom.css` is dynamic; Cloudflare has a bypass rule for `quant.vicchong1983.trade/api/config/*` so config CSS/JS is not held by stale edge cache.

## Production acceptance gate

`localhost` is preflight only. A display change is complete only after all applicable checks pass:

1. focused tests/config parse and independent audit when warranted;
2. localhost API/service preflight;
3. deploy/restart existing service(s);
4. authenticated `https://quant.vicchong1983.trade` loads;
5. authenticated `/detail` loads;
6. displayed data matches authoritative source/read-back;
7. root-referenced `/_next/*` assets return 200 and widget errors are zero;
8. `/api/config/*` is not served from stale Cloudflare edge cache;
9. mobile-width visual inspection is performed on the production domain.

Do not declare completion from localhost or tests alone.

## Manual preflight

```sh
/opt/homebrew/bin/python3 runtime/candidate_snapshot.py \
  --dashboard-json ~/quant-dashboard/data/dashboard.json

curl -f http://127.0.0.1:3000/
curl -f http://127.0.0.1:8787/dashboard.json
curl -f http://127.0.0.1:8787/detail
```

The production domain is behind Cloudflare Access; an unauthenticated HTTP client is expected to receive the Access redirect rather than the dashboard body.
