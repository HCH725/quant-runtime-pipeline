# Quant runtime dashboard (Homepage, localhost stage)

Read-only observability for the quant research loop, built on
[gethomepage/homepage](https://github.com/gethomepage/homepage). It is a **viewer**: it renders one
JSON payload and can start, stop, retry, unblock or backtest nothing. The quant runtime does not
depend on it - if both processes are down, every scheduled job keeps running unchanged.

## Pieces

| Piece | Where | Role |
|---|---|---|
| `homepage/{settings,services,widgets}.yaml`, `homepage/custom.css` | this repo | the dashboard config (this directory *is* `HOMEPAGE_CONFIG_DIR`) |
| `runtime/candidate_snapshot.py --dashboard-json <path>` | this repo | the single producer of the payload (same helpers as the Discord `#candidate` line) |
| `runtime/dashboard_serve.py` | this repo | stdlib server, 127.0.0.1 only, allowlist = `dashboard.json` (nothing else is reachable) |
| Homepage app (v2.4.0 build) | `~/workspace/quant-homepage-app` | the viewer itself (`pnpm install && pnpm build` once; started as `.next/standalone/server.js`, see below) |
| payload + logs + pids | `~/quant-dashboard/` | `data/dashboard.json`, `run/*.log`, `run/*.pid` |

## Run

```sh
sh homepage/run_local.sh          # starts the JSON server (:8787) and Homepage (:3000)
sh homepage/run_local.sh stop     # stops both
```

Then open http://localhost:3000/ . To refresh the payload by hand (the hourly snapshot job does it
automatically once the reviewed `candidate_snapshot.py` is deployed to the main tree):

```sh
/opt/homebrew/bin/python3 runtime/candidate_snapshot.py \
    --dashboard-json ~/quant-dashboard/data/dashboard.json
```

Reinstalling the viewer (only needed on a Homepage version bump, no Docker involved):

```sh
git clone --depth 1 --branch v2.4.0 https://github.com/gethomepage/homepage.git ~/workspace/quant-homepage-app
cd ~/workspace/quant-homepage-app && pnpm install && pnpm build
```

## Page layout

1. **Quant Health** - the one overall status (`ok` / `attention` / `unknown`), which *is*
   `quant_runtime_watchdog.py`'s own state passed through: `attention` exactly when the watchdog
   holds an active signature, `unknown` when its state file cannot be read. Plus the payload age.
2. **Current Research** - the newest family / round / attempt / stage / progress / cohort and when
   the attempt last wrote something. Research progress only: **not live PnL**.
3. **Leaderboard** - the Top 5 frozen survivors, verbatim from `_survivors/leaderboard.json`
   (rows link to the raw payload).
4. **Research Funnel** - reviewed → ingested Wiki records and registered → backtested families.
5. **Runtime & Agent** - the watchdog's own W1-W4 state (status, active findings, last check, last
   healthy) and the Kanban board read-back.

Unknown values render as `unavailable` / blank, never as `0`.

## Boundaries (do not "fix" these)

- Localhost only: `dashboard_serve.py` hard-codes 127.0.0.1 and serves one allowlisted file; it never
  exposes the results root, `kanban.db` or the repo.
- No control plane: no card, link or widget in this config starts, stops, retries, unblocks or
  backtests anything; the payload is written by the monitoring job, never by the viewer.
- No second computation: every number comes from `candidate_snapshot.py`'s existing helpers, so the
  dashboard and the Discord line are the same snapshot.
- No second monitor: runtime health is the watchdog's own verdict passed through, never a health
  algorithm of this dashboard's own - container / results-volume / cron re-checks are deliberately
  absent, and the watchdog keeps owning W1-W4.
- Loopback proven, not assumed: `run_local.sh` starts Homepage as the standalone server with
  `HOSTNAME=127.0.0.1`, and every port is then judged by its real listening socket (`lsof`) - both
  when the process was already running and when this script just started it - requiring each socket
  to be exactly `127.0.0.1:<port>`. A wildcard (`*:<port>`) or IPv6 (`[::1]:<port>`) listener is a
  refusal, not a warning, because both also answer on 127.0.0.1. `next start` would default to
  `0.0.0.0`, and `HOMEPAGE_ALLOWED_HOSTS` is only a Host-header guard, not a bind.
- The payload writer refuses any `--dashboard-json` target inside the results root, so "never under
  /results" is enforced by the script rather than by caller discipline.
- No Docker, no new service manager: two foreground-able processes bound to loopback.
