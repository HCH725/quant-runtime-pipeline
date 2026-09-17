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
| Homepage app (v2.4.0 build) | `~/workspace/quant-homepage-app` | the viewer itself (`pnpm build` once, `pnpm start` to run) |
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

1. **Quant Health** - the one overall status (`ok` / `attention` / `unknown`), the board read-back,
   the container state and the payload age.
2. **Current Research** - the newest family / round / attempt / stage / progress / cohort and when
   the attempt last wrote something. Research progress only: **not live PnL**.
3. **Leaderboard** - the Top 5 frozen survivors, verbatim from `_survivors/leaderboard.json`
   (rows link to the raw payload).
4. **Research Funnel** - reviewed → ingested Wiki records and registered → backtested families.
5. **Runtime & Agent** - qlib container, results volume, Kanban board counts, quant cron jobs.

Unknown values render as `unavailable` / blank, never as `0`.

## Boundaries (do not "fix" these)

- Localhost only: `dashboard_serve.py` hard-codes 127.0.0.1 and serves one allowlisted file; it never
  exposes the results root, `kanban.db` or the repo.
- No control plane: no card, link or widget in this config starts, stops, retries, unblocks or
  backtests anything; the payload is written by the monitoring job, never by the viewer.
- No second computation: every number comes from `candidate_snapshot.py`'s existing helpers, so the
  dashboard and the Discord line are the same snapshot.
- No Docker, no new service manager: two foreground-able processes bound to loopback.
