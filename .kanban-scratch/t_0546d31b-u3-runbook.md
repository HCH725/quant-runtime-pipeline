# Strategy F r1-u3 — WAKE RUNBOOK (card t_0546d31b)

Durable copy of the u3 launch comment (the card comment may render truncated).
Written 2026-09-16 ~07:05Z by the launcher run (run 235).

## State at handoff
- `u3` = `stochastic-rsi-renko-2026-08-31-r1-u3`, RUNNING detached in `qlib-run`,
  exact-once launch `2026-09-16T07:00:19Z`, engine `f-v1-engine-1.0.2`
  (`sha256:1e0b679e47bfa9896f4a6cef0070bebd43420b8e32406c455d2f9f606aabfb71`),
  self-check `sha256:54523c1a863dd4ed778a0acc787376ffdc79d9205ec9fc4dbd89acf108831a3e`.
- attempt dir: `/Volumes/ExpansionDrive/qlib-results/stochastic-rsi-renko-2026-08-31/rounds/stochastic-rsi-renko-2026-08-31-r1/attempts/stochastic-rsi-renko-2026-08-31-r1-u3`
- run-spec `sha256:838121c69a1f402d4d26a957d6c572c93a267218015f1bd1a6d09f33cd19cd98`
  (round-spec reused verbatim, `sha256:7cd808b1…`, INV-4).
- repo commit `81eec64b38c75341c17d87157f1a7f0a744e53b0` on `origin/main` (read back).
- preflight P1–P10 all PASS (rc=0). ETA ≈ 07:40Z ± 10 min (measured pace of u1/u2).

## Why u3 exists (u2's fix was a NO-OP — measured)
- u2 (`1.0.1`) reached ARTIFACT_READY with full coverage but `no_entry_after_exhaustion`
  still false (452/8,640 `full` rows, min −4.985).
- **All 10 `grid_*.csv` are byte-identical between u1 and u2** (row diff 0/8,640 per grid).
  The 1.0.1 flip-path guard never fired once → that path was real but INACTIVE; the u1
  diagnosis was incomplete; u2 carries no independent measurement.
- Real root cause: `min_entry_equity` is recorded INSIDE `open_position`, AFTER the entry's
  own taker fee, while the pre-entry guard tested equity BEFORE that fee. An episode decided
  at +0.33 USDT posts −4.67. Fingerprint: the per-grid minimum is always just inside −1× the
  tranche fee quantum (5 USDT at 10,000 USDT notional × 5 bps):
  full −4.985, fee_2x −9.987 (2×), cost_attrition_40bps −39.995 (8×).
- Fix 1.0.2: `open_position` refuses the tranche when `START_EQUITY + (realized - entry_fee)
  <= 0` (spelled exactly like the recorded expression → the recorded value IS the value that
  passed the guard), counts `entry_refused_exhausted`, and the episode loop halts flat.

## RED / GREEN (both in-container, production interpreter)
- RED on the frozen 1.0.1 bytes (`sha256:312ef807…`, staged under
  `/results/_diag/f_red_101/`): new test `test_entry_the_account_cannot_fund_is_not_opened`
  → FAIL `-360.375 not greater than 0.0`; full suite 28 ran / 27 ok / 1 FAIL.
- GREEN on 1.0.2: 28/28 OK in `qlib-run`; repo suite
  `python3 -m unittest discover -s runtime/tests -t runtime/tests` → 342/342 OK.

## STEP 1 — verify the attempt before anything else
Read `<attempt>/state.json`. If `stage=ARTIFACT_READY`, check `result.json`:
- `assertions` ALL 26 true (now including `no_entry_after_exhaustion`),
- `coverage_complete == true`, `case_evaluations_total == expected == 86400` (8,640 × 10),
- `engine_version == "f-v1-engine-1.0.2"`.
Any false assertion → technical defect: diagnose, fix, `-u4` in the same round; do NOT
publish `DONE`. (Reference check script used at u2: `/tmp/f_verify_u2.py` style.)

## STEP 2 — u2→u3 bounded diff (the sharp prediction to falsify)
The fix can only change cells in which u2 had an entry whose OWN fee exhausted the book.
Per grid, the set of rows differing from u2 must be EXACTLY the u2 rows with
`min_entry_equity <= 0`:

| grid | expected differing rows (u2 defect cells) | u2 min |
|---|---|---|
| historical | 400 | −4.985 |
| oos | 91 | −4.979 |
| full | 452 | −4.985 |
| fee_2x | 1,101 | −9.987 |
| funding_2x | 396 | −4.976 |
| entry_delay_1_bar | 540 | −4.970 |
| slippage_2ticks | 419 | −4.959 |
| no_funding | 360 | −4.991 |
| no_funding_full | 403 | −4.991 |
| cost_attrition_40bps | 2,866 | −39.995 |

Every such row must now have `min_entry_equity > 0` with its `net_pnl` sign unchanged; any
changed row outside the set, or any unchanged row inside it, is drift → stop and diagnose.
`structural_counters[*].entry_refused_exhausted` (new, diagnostic) should sum to the number
of refused entries (≥ the per-grid defect-cell counts).
Reference diff script used at u2: `/tmp/f_diff_u1u2.py` (sha-compare + defect census).

## STEP 3 — terminal sentinel (host-side; the container never writes it)
```
python3 runtime/terminal_evidence.py publish \
  --attempt-dir <attempt> --status DONE \
  --family-id stochastic-rsi-renko-2026-08-31 \
  --round-id stochastic-rsi-renko-2026-08-31-r1 \
  --run-id stochastic-rsi-renko-2026-08-31-r1-u3 \
  --task-id t_0546d31b \
  --verdict-hint <CANDIDATE_PASS|CANDIDATE_REJECT> \
  --manifest result.json,artifacts/cohort_results.json,artifacts/cohort_survivors.json,artifacts/assertions.json,artifacts/dca_layer_histogram.json,artifacts/renko_signals.json,artifacts/funding_series.json,artifacts/input_manifest.json,artifacts/bins_build.json,artifacts/grid_*.csv
python3 runtime/terminal_evidence.py check --attempt-dir <attempt>   # rc=0, problems=[]
```

## STEP 4 — survivor bundle (only if cohort_survivor_count >= 1)
`python3 runtime/survivor_bundle.py --attempt-dir <attempt>` (§10.8 refuses a bundle for 0).

## STEP 5 — round verdict
Write `rounds/<round>/verdict.json` (§10.7, immutable; model on Strategy E's round verdict),
including `technical_retries` = [u1 ARTIFACT_READY + false assertion, u2 ARTIFACT_READY +
byte-identical grids (no-op fix) + false assertion, u3 authoritative], per-cohort
`cull_reasons`, the three registered family-level falsification readers, and the `sha256`
block (result.json, sentinel, round-spec, run-spec, pinned engine, self-check).

## STEP 6 / 7 — evidence + close
`evidence/strategy-f-v1-r1-run-<date>.json` + an `evidence/README.md` row; commit + push
`main`; read `origin/main` back; `kanban_complete` with the acceptance map (verdict,
coverage, survivor list, claimability, RED/GREEN, the bounded-diff result) and artifact paths.
