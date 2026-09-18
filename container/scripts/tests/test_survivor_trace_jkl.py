#!/usr/bin/env python3
"""Engine-layer regression for the J/K/L-lineage inert trace hook (contract section 28.2/28.3).

Three things this file exists to prove, for every engine of the lineage:

  * the hook is INERT - the same cell with `TRACE = None` and with a live sink returns an
    exactly equal `record()` row (so the aggregate, the calculation order and the semantics
    are untouched),
  * the ledgers the hook emits are self-consistent - they reconcile back to the aggregate
    the engine returned (episode sums, the exit partition, a pure-python recomputation of
    Sharpe and max drawdown from the equity ledger), which is the check
    `runtime/survivor_evidence.py` repeats on the real frozen/replayed winner cells, and
  * the emitted vocabulary is the registered one - every `exit_reason` is one of
    TP / STOP / MARGIN_CALL / EOD_FLATTEN (contract 28.2, matching the aggregate's
    tp_hits / stop_hits / margin_calls / open_at_end) or TIME_EXIT (this lineage's registered
    target-state exit, matching the aggregate's `time_exits` column), and every fill closes a
    recorded episode.

The market is synthetic and deterministic (no Qlib, no container, no /results) and the signal
layer is stubbed, so the rail and the whole exit chain run over a fixed cell set; the engine's
own `Cohort` and `simulate` are never re-implemented.

Non-vacuity: `test_negative_control_dropped_episode_breaks_reconciliation` shows the same
reconciliation fails once a ledger row is removed, so a green run cannot be an artefact of the
check never being able to fail.

usage:  python3 container/scripts/tests/test_survivor_trace_jkl.py       (numpy interpreter;
            runs all three lineage engines)
        SJKL_ENGINE_PATH=<runner> python3 ...                            (single-engine
            override; this is how the RED control runs the file against the pre-hook bytes)
"""
import importlib.util
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
DEFAULT_ENGINES = [os.path.join(SCRIPTS, name) for name in
                   ("100_strategy_j_run.py", "110_strategy_k_run.py", "120_conformal_kelly_run.py")]
OVERRIDE = os.environ.get("SJKL_ENGINE_PATH")
ENGINES = [OVERRIDE] if OVERRIDE else DEFAULT_ENGINES
# `record()` emits every aggregate rounded to 6 decimals, so the ledger sums are compared at
# the rounding quantum of the aggregate itself - exactly the rule `runtime/survivor_evidence.py`
# applies to the real frozen/replayed cells (TOL = 1e-6, relative to max(1, |aggregate|)).
TOL = 1e-6
MS_PER_DAY = 86400000
BASE_MS = 1767225600000          # 2026-01-01T00:00:00Z
EXIT_VOCABULARY = ("TP", "STOP", "MARGIN_CALL", "EOD_FLATTEN", "TIME_EXIT")
FILL_VOCABULARY = ("ENTRY", "DCA_ADD", "EXIT", "FLATTEN")
RAILS = (
    ("tp_tight", {"base_quote": 1000.0, "spacing_d0": 0.01, "tp": 0.001,
                  "invalidation": 0.5, "size_multiplier": 1.0}),
    ("stop_tight", {"base_quote": 1000.0, "spacing_d0": 0.01, "tp": 0.5,
                    "invalidation": 0.001, "size_multiplier": 1.0}),
    ("wide", {"base_quote": 1000.0, "spacing_d0": 0.01, "tp": 0.5,
              "invalidation": 0.9, "size_multiplier": 1.0}),
)
DCA = {"base_quote": 1000.0, "spacing_pct": 0.01, "size_multiplier": 1.0,
       "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
STRESSES = (("plain", {}, 1), ("fee_2x", {"fee_mult": 2.0}, 1),
            ("funding_2x", {"funding_mult": 2.0}, 1),
            ("entry_delay_1_bar", {"entry_delay_1_bar": True}, 1),
            ("slippage_2ticks", {}, 2), ("no_funding", {"no_funding": True}, 1))


def load_engine(path):
    spec = importlib.util.spec_from_file_location(
        "jkl_engine_trace_test_%s" % os.path.basename(path).replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook_supported(engine):
    """True when the loaded engine carries the contract 28.2 sink (never raises)."""
    return hasattr(engine, "TRACE") and callable(getattr(engine, "_trace", None))


def synth_market(n=90):
    """A deterministic planted path: rise, fall, fall, recover, mild rise."""
    close = np.empty(n, dtype=np.float64)
    close[0:6] = 100.0
    close[6:13] = np.linspace(100.0, 102.0, 7)
    close[13:32] = np.linspace(102.0, 99.0, 19)
    close[32:38] = np.linspace(99.0, 96.0, 6)
    close[38:56] = np.linspace(96.0, 99.5, 18)
    close[56:] = np.linspace(99.5, 101.0, n - 56)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return open_, close * 1.004, close * 0.996, close


class FakeCohort(object):
    """The subset of `Cohort` that `simulate()` / `_metrics()` / `record()` read."""

    def __init__(self, n=90):
        self.symbol = "SYNTHUSDT"
        self.timeframe = "1d"
        self.qlib_freq = "day"
        self.open_time_ms = BASE_MS + np.arange(n, dtype=np.int64) * MS_PER_DAY
        self.open, self.high, self.low, self.close = synth_market(n)
        self.n = n
        self.bar_ms = MS_PER_DAY
        self.price_increment = 0.1
        self.taker_fee = 0.0005
        self.leverage = 10.0
        self.margin_maint = 0.005


def synth_funding(cohort, every=5, rate=0.0005):
    """Settlements on the registered 08:00 UTC grid, mapped to their containing bars."""
    times = np.array([int(cohort.open_time_ms[i]) + 8 * 3600 * 1000
                      for i in range(2, cohort.n - 1, every)], dtype=np.int64)
    rates = np.full(len(times), rate, dtype=np.float64)
    containing = np.searchsorted(cohort.open_time_ms, times, side="left") - 1
    closed = np.searchsorted(cohort.open_time_ms, times, side="right") - 1
    return {"obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64)}


EMPTY_FUNDING = {"obs_times": np.array([], dtype=np.int64),
                 "obs_rates": np.array([], dtype=np.float64),
                 "settle_bar": np.array([], dtype=np.int64),
                 "settle_bar_closed": np.array([], dtype=np.int64)}


class StubLayer(object):
    """A planted signal layer: the engine's `signals_for` seam, stubbed.

    Entry events at bars 5 / 31 / 55; the target state flips to flat at bars 26 / 46 and
    stays long from bar 56 to the slice end, so the cell set exercises TP / STOP, the
    registered target-state exit and the slice-end flatten on one deterministic market.
    """

    def __init__(self, n=90, events=(), states=None, case=None):
        self.pos = np.zeros(n, dtype=np.int64)
        self.events = np.zeros(n, dtype=np.int64)
        self.yhat = np.zeros(n, dtype=np.float64)
        for bar, sign in events:
            self.events[bar] = sign
        for lo, hi, sign in (states or ()):
            self.pos[lo:hi] = sign
        self.case = case
        self.case_label = "stub"
        self.diag = {}


class Collector(object):
    """The same sink shape the replay driver hands to the engine."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.fills = []
        self.episodes = []
        self.equity = None

    def __call__(self, event, fields):
        if event == "fill":
            self.fills.append(dict(fields))
        elif event == "episode":
            self.episodes.append(dict(fields))
        elif event == "equity_marks":
            self.equity = dict(fields)


class Harness(object):
    """One loaded engine + one synthetic cohort + the stubbed signal seam."""

    def __init__(self, engine_path):
        self.path = engine_path
        self.engine = load_engine(engine_path)
        self.cohort = FakeCohort()
        self.funding = synth_funding(self.cohort)
        self.case = self.engine.CASE_ORDER[0]
        self.p = {f: v for f, v in zip(self.engine.CASE_FIELDS, self.case)}
        self._layer = self.default_layer()
        self._original = getattr(self.engine, "signals_for", None)
        self.engine.signals_for = lambda cohort, case: self._layer

    def default_layer(self):
        n = self.cohort.n
        return StubLayer(n, events=((5, 1), (31, 1), (55, 1)),
                         states=((6, 26, 1), (32, 46, 1), (56, n, 1)), case=self.case)

    def set_layer(self, layer):
        self._layer = layer

    def restore(self):
        if self._original is not None:
            self.engine.signals_for = self._original

    def run(self, rail, stress, slip, window=None, series=None, trace=None, kind="unit"):
        """One `simulate` call with the requested sink state; always returns the record row."""
        window = window or (0, self.cohort.n)
        series = series if series is not None else self.funding
        self.engine.TRACE = trace
        try:
            metrics = self.engine.simulate(self.cohort, self.p, rail, window, stress, slip,
                                           kind, series)
        finally:
            self.engine.TRACE = None
        return metrics

    def cell(self, rail, stress, slip):
        """(plain_row, traced_row, collector) for one cell of the fixed cell set."""
        plain_metrics = self.run(rail, stress, slip, trace=None)
        collector = Collector()
        traced_metrics = self.run(rail, stress, slip, trace=collector)
        plain_row = self.engine.record(self.cohort, self.p, DCA, "unit", plain_metrics)
        traced_row = self.engine.record(self.cohort, self.p, DCA, "unit", traced_metrics)
        return plain_row, traced_row, collector


_HARNESS = {}


def harness(engine_path):
    if engine_path not in _HARNESS:
        _HARNESS[engine_path] = Harness(engine_path)
    return _HARNESS[engine_path]


def all_cells(h):
    for _name, rail in RAILS:
        for _sname, stress, slip in STRESSES:
            yield rail, stress, slip


class TestTraceHook(unittest.TestCase):
    def test_engine_exposes_the_inert_trace_hook(self):
        for path in ENGINES:
            engine = harness(path).engine
            self.assertTrue(hook_supported(engine),
                            "%s exposes no inert trace hook (contract 28.2): no module-level "
                            "TRACE sink / _trace() emitter" % path)
            self.assertIsNone(engine.TRACE, "TRACE must default to None on the production path")

    def test_trace_off_on_aggregate_is_identical(self):
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            for rail, stress, slip in all_cells(h):
                plain, traced, collector = h.cell(rail, stress, slip)
                self.assertEqual(plain, traced,
                                 "trace off/on aggregate differs for %s (rail %s)"
                                 % (path, rail))
                self.assertGreater(traced["episodes"], 0,
                                   "cell produced no episode: %s" % path)
                self.assertGreater(len(collector.fills), 0)

    def test_trace_none_emits_nothing(self):
        for path in ENGINES:
            h = harness(path)
            collector = Collector()
            h.run(RAILS[2][1], {}, 1, trace=None)
            self.assertEqual(collector.fills, [])
            self.assertEqual(collector.episodes, [])
            self.assertIsNone(collector.equity)

    def test_episode_ledger_reconciles_to_aggregate(self):
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            for rail, stress, slip in all_cells(h):
                _plain, row, collector = h.cell(rail, stress, slip)
                self.assertEqual(len(collector.episodes), row["episodes"],
                                 "episode ledger count != aggregate for %s" % path)
                for field, key in (("gross_pnl", "gross_pnl"), ("fees", "fees"),
                                   ("funding", "funding"), ("net_pnl", "net_pnl")):
                    total = sum(e[field] for e in collector.episodes)
                    self.assertAlmostEqual(total, row[key],
                                           delta=TOL * max(1.0, abs(row[key])),
                                           msg="sum(episode %s) != aggregate for %s"
                                               % (field, path))

    def test_episode_partition_matches_aggregate(self):
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            for rail, stress, slip in all_cells(h):
                _plain, row, collector = h.cell(rail, stress, slip)
                for reason, key in (("TP", "tp_hits"), ("STOP", "stop_hits"),
                                    ("MARGIN_CALL", "margin_calls"),
                                    ("EOD_FLATTEN", "open_at_end"), ("TIME_EXIT", "time_exits")):
                    self.assertEqual(
                        sum(1 for e in collector.episodes if e["exit_reason"] == reason),
                        row[key], "episode partition %s != aggregate %s for %s"
                                  % (reason, key, path))
                for episode in collector.episodes:
                    self.assertIn(episode["exit_reason"], EXIT_VOCABULARY)
                    self.assertAlmostEqual(
                        episode["gross_pnl"] - episode["fees"] - episode["funding"],
                        episode["net_pnl"], delta=TOL)
                    self.assertGreaterEqual(episode["layers_used"], 1)
                    self.assertEqual(episode["holding_bars"],
                                     episode["exit_bar_index"] - episode["entry_bar_index"])

    def test_fill_ledger_closes_every_episode(self):
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            for rail, stress, slip in all_cells(h):
                _plain, _row, collector = h.cell(rail, stress, slip)
                closed = {e["episode"] for e in collector.episodes}
                self.assertEqual(sorted({f["episode"] for f in collector.fills}), sorted(closed))
                firsts = {}
                for fill in collector.fills:
                    self.assertIn(fill["event_type"], FILL_VOCABULARY)
                    self.assertIsNotNone(fill["open_time_ms"])
                    firsts.setdefault(fill["episode"], fill["event_type"])
                self.assertTrue(all(kind == "ENTRY" for kind in firsts.values()),
                                "every episode must open with its ENTRY fill")

    def test_equity_ledger_recomputes_sharpe_and_drawdown(self):
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            for rail, stress, slip in all_cells(h):
                _plain, row, collector = h.cell(rail, stress, slip)
                equity = collector.equity
                self.assertIsNotNone(equity, "no equity marks recorded for %s" % path)
                series = np.array(equity["equity"], dtype=np.float64)
                self.assertEqual(len(series), row["days"])
                self.assertEqual(len(equity["in_window"]), row["days"])
                self.assertEqual(len(equity["day_start_ms"]), row["days"])
                rets = np.diff(series) / series[:-1]
                sd = float(np.std(rets, ddof=1))
                sharpe = float(np.mean(rets) / sd * np.sqrt(365.0)) if sd > 0 else 0.0
                peak = np.maximum.accumulate(series)
                dd = series - peak
                self.assertAlmostEqual(round(sharpe, 6), row["sharpe"], places=6)
                self.assertAlmostEqual(round(float(dd.min()), 6), row["max_dd_usdt"], places=6)

    def test_exit_reason_and_fill_vocabulary_is_covered(self):
        """The fixed cell set must really exercise the emit points (no vacuous green)."""
        reasons, events = set(), set()
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            for rail, stress, slip in all_cells(h):
                _plain, _row, collector = h.cell(rail, stress, slip)
                reasons.update(e["exit_reason"] for e in collector.episodes)
                events.update(f["event_type"] for f in collector.fills)
        self.assertTrue(reasons <= set(EXIT_VOCABULARY),
                        "unregistered exit reason(s) emitted: %r" % (reasons - set(EXIT_VOCABULARY)))
        self.assertTrue({"TP", "STOP"} <= reasons, "TP and STOP must both be exercised: %r" % reasons)
        self.assertTrue(reasons & {"TIME_EXIT", "EOD_FLATTEN"},
                        "the target-state exit / slice-end flatten must be exercised: %r" % reasons)
        self.assertTrue({"ENTRY", "DCA_ADD"} <= events,
                        "ENTRY and DCA_ADD fills must both be exercised: %r" % events)

    def test_equity_marks_are_emitted_without_episodes(self):
        """The no-episode early return still carries the dated equity ledger."""
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            h.set_layer(StubLayer(h.cohort.n, events=(), states=(), case=h.case))
            try:
                plain, traced, collector = h.cell(RAILS[2][1], {}, 1)
            finally:
                h.set_layer(h.default_layer())
            self.assertEqual(plain["episodes"], 0)
            self.assertEqual(collector.episodes, [])
            self.assertIsNotNone(collector.equity, "no equity marks on the no-episode path")
            self.assertEqual(len(collector.equity["equity"]), traced["days"])
            self.assertTrue(all(v == 30000.0 for v in collector.equity["equity"]))

    def test_negative_control_dropped_episode_breaks_reconciliation(self):
        for path in ENGINES:
            h = harness(path)
            self.assertTrue(hook_supported(h.engine), "%s exposes no inert trace hook" % path)
            _plain, row, collector = h.cell(RAILS[0][1], {}, 1)
            self.assertGreater(len(collector.episodes), 1)
            trimmed = collector.episodes[:-1]
            total = sum(e["net_pnl"] for e in trimmed)
            self.assertGreater(abs(total - row["net_pnl"]), TOL,
                               "dropping an episode must break the reconciliation (the check "
                               "must not be vacuous)")
            self.assertNotEqual(len(trimmed), row["episodes"])


if __name__ == "__main__":
    print("engines under test:")
    for path in ENGINES:
        print("  - %s" % path)
    unittest.main(verbosity=2)
