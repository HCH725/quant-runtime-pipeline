# Strategy A — production full-backtest, run report

Card: `t_97208408` (quant-strategy-research). Family:
`close-vs-sma-mean-reversion-long-flat-v1`. Contract v1.1.1 (AUDITED PASS / FROZEN).

Verdict: **REJECT** — `performance_claimable = false`.
Authoritative record: `/Volumes/ExpansionDrive/qlib-results/close-vs-sma-mean-reversion-long-flat-v1/rounds/close-vs-sma-mean-reversion-long-flat-v1-r2/verdict.json`.

## Runs

| round / run | status | note |
|---|---|---|
| r1 / u1 | FAILED | card-local `script_bug` — numpy bool not JSON serializable |
| r2 / u1 | FAILED | bare `json.dumps` on stdout + positions reopened after equity went negative |
| r2 / u2 | DONE | all 9 phase grids; `result.json` contained bare `NaN` tokens |
| r2 / u3 | **DONE (governing)** | same numbers, strictly valid JSON |

r2 supersedes r1: the intrabar execution semantics were corrected from "every ladder level
crossed by the bar low fills" to a descending-price trigger walk in which a resting
invalidation cancels the levels behind it. No hypothesis, universe, parameter domain, split,
DCA rail parameter, gate or falsification item changed between r1 and r2.

## Headline (median over the 240 pre-registered legal cases)

| metric | historical | full window |
|---|---|---|
| net PnL (USDT) | −25,069.39 | −27,257.97 |
| fees (USDT) | 9,923.05 | 11,112.53 |
| funding (USDT) | 39.78 | 16.79 |
| ending equity (USDT) | 4,930.61 | 2,742.03 |
| Sharpe (daily, ann.) | −0.137 | −0.162 |
| Max DD (USDT / %) | −29,638.88 / −92.9% | −30,130.84 / −96.3% |
| max effective leverage | 11.88 | 13.47 |
| capital utilisation | 0.199 | 0.215 |
| episodes | 336 | 396 |

OOS (20 pre-registered representatives): median net PnL −7,003.88 USDT, median Sharpe −0.275.

## Gates

coverage 9 × 240 = 2160 cases: PASS. Sufficient trades: PASS. Official-funding stability:
PASS. **historical economic: FAIL. OOS economic: FAIL. parameter neighbourhood: FAIL
(13/20). robustness economic: FAIL (all four stress reruns negative).**

## Findings worth keeping

1. The 1.2% take profit against a 5% invalidation needs a >81% win rate to break even before
   costs; the grid does not deliver it. Fees alone consume a median ~9,900 USDT of the
   30,000 starting equity in the historical window.
2. DCA layer histogram: only levels 0–4 ever fill (`level_05`..`level_11` = 0 across all 2160
   cases). With 2% spacing and a 5% invalidation implemented as a resting stop, the 12-tranche
   / 1.1× sizing is almost entirely unexercised — the inherited rail is internally mismatched
   for a hard-stop LONG mirror.
3. All four 4h cohorts are strongly positive (best: ETHUSDT/4h +66,543 USDT, Sharpe 2.35)
   while the 5m/15m/30m/1h cohorts are deeply negative.
4. The r1 → r2 semantics correction moved the aggregate from strongly positive (partial r1
   artifacts: median full +19,548 USDT) to strongly negative (−27,258 USDT): the original rule
   assumed fills that a resting invalidation order makes impossible.

## Engineering checks

* `container/scripts/tests/test_strategy_a_engine.py` — 14/14 OK inside `qlib-run`.
* Preflight P1–P10 green for every launch: `evidence/strategy-a-preflight-*.json`.
* Launch records: `evidence/strategy-a-launch-record-20260913.json`.
* Reconcile dry-run over all four sentinels: `scanned=4 unblocked=[] incidents=0`.

## Reproduction

```
python3 runtime/preflight.py --launch --attempt-dir <host attempt dir>
container exec qlib-run /opt/venv/bin/python /scripts/20_strategy_a_run.py <container run-spec>
python3 runtime/terminal_evidence.py publish --attempt-dir <host attempt dir> --status DONE ...
```

The runner rebuilds the qlib bin store from the read-only raw store into `/qlib/work`
(79,187,392 bytes, ~40 s) and never writes `/results` or `/data/raw`.
