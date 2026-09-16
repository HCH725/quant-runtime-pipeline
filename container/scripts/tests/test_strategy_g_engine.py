#!/usr/bin/env python3
"""Executable check for the Strategy G execution engine (volume-anchored sessions + DCA rail).

Runs inside the qlib container:
    container exec qlib-run env SG_ENGINE_PATH=/scripts/70_strategy_g_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_g_engine.py
and on the host with a numpy interpreter:
    SG_ENGINE_PATH=<repo>/container/scripts/70_strategy_g_run.py python3 <this file>

Drives the pure functions with synthetic half-hour bars whose fills, fees and funding charges
are known by hand, so a regression in the session construction (trailing slot-volume anchor,
PIT guarantee, entry/exit boundaries, conditioning), the window-bounded rail, the long/short
mirror, the per-fill fee ledger, the independent gross accumulator, the funding-exposure rule
or the cohort gate fails loudly instead of silently changing the science.  No market data, no
container state, no network: stdlib unittest + numpy only.
"""
import importlib.util
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SG_ENGINE_PATH", "/scripts/70_strategy_g_run.py")
_spec = importlib.util.spec_from_file_location("sg_engine", ENGINE)
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)

BAR_MS = 1800000                    # 30 min: exactly one slot per bar
BASE_MS = 1767225600000             # 2026-01-01T00:00:00Z (a clean UTC midnight)
RAIL = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02,
        "tp": 0.01, "invalidation": 0.05}
LEV = 10.0
P0 = 100.0
PEAK = 20                           # the synthetic activity peak slot (10:00 UTC)
MS_DAY = 86400000


def case_params(basis=1, entry=0, cond=0):
    """The registered one-hot strategy params of (basis index, entry index, conditioning)."""
    return {f: int(v) for f, v in zip(sg.CASE_FIELDS, sg.case_fields(basis, entry, cond))}


def case_tuple_of(basis=1, entry=0, cond=0):
    return sg.case_fields(basis, entry, cond)


class FakeCohort:
    """Half-hour bars on a clean UTC grid; bar i opens at base_ms + i * 30 min."""

    def __init__(self, closes, highs=None, lows=None, opens=None, vols=None,
                 base_ms=BASE_MS, bar_ms=BAR_MS):
        n = len(closes)
        self.symbol = "SYNTH"
        self.timeframe = "30m"
        self.close = np.array(closes, dtype=np.float64)
        self.open = np.array(opens if opens is not None else closes, dtype=np.float64)
        self.high = np.array(highs if highs is not None else closes, dtype=np.float64)
        self.low = np.array(lows if lows is not None else closes, dtype=np.float64)
        self.volume = np.array(vols if vols is not None else [1.0] * n, dtype=np.float64)
        self.n = n
        self.open_time_ms = np.array([base_ms + i * bar_ms for i in range(n)], dtype=np.int64)
        self.bar_ms = bar_ms
        self.slot_of_bar = ((self.open_time_ms % MS_DAY) // sg.MS_PER_SLOT).astype(np.int64)
        self.day_of_bar = (self.open_time_ms // MS_DAY).astype(np.int64)
        self.day0 = int(self.day_of_bar[0])
        self.n_days = int(self.day_of_bar[-1]) - self.day0 + 1
        self.vol_by_day_slot = np.zeros((self.n_days, sg.SLOTS_PER_DAY), dtype=np.float64)
        np.add.at(self.vol_by_day_slot, (self.day_of_bar - self.day0, self.slot_of_bar),
                  self.volume)
        self.price_increment = 0.0
        self.taker_fee = 0.0
        self.leverage = LEV
        self.margin_maint = 0.1
        self.sessions = sg.build_sessions(self)

    def slice(self, _a, _b):
        return 0, self.n


def mk_cohort(n_days, peak=PEAK, price=P0, day_volumes=None, signal_move=0.001,
              session_move=0.0):
    """Volume concentrated in `peak` on every day; the anchor bar moves by `signal_move`
    relative to its own open (first half hour), and every later bar of the same session is
    quoted `session_move` away from the previous bar (a stepwise drift, flat per bar)."""
    n = n_days * sg.SLOTS_PER_DAY
    vols = [1.0] * n
    for d in range(n_days):
        vols[d * sg.SLOTS_PER_DAY + peak] = 1000.0
    if day_volumes:
        for (day, slot), v in day_volumes.items():
            vols[day * sg.SLOTS_PER_DAY + slot] = v
    opens = [price] * n
    closes = [price] * n
    for d in range(n_days):
        i0 = d * sg.SLOTS_PER_DAY + peak
        opens[i0] = price
        closes[i0] = price * (1.0 + signal_move)
        if session_move:
            p = closes[i0]
            for k in range(1, sg.SLOTS_PER_DAY):
                idx = i0 + k
                if idx >= n:
                    break
                p = p * (1.0 + session_move)
                opens[idx] = closes[idx] = p
    highs = [max(o, c) for o, c in zip(opens, closes)]
    lows = [min(o, c) for o, c in zip(opens, closes)]
    return FakeCohort(closes, highs, lows, opens, vols)


def mk_series(cohort, settlements=()):
    times = np.array([int(t) for t, _r in settlements], dtype=np.int64)
    rates = np.array([float(r) for _t, r in settlements], dtype=np.float64)
    containing = (np.searchsorted(cohort.open_time_ms, times, side="left") - 1
                  if len(times) else np.array([], dtype=np.int64))
    closed = (np.searchsorted(cohort.open_time_ms, times, side="right") - 1
              if len(times) else np.array([], dtype=np.int64))
    return {"label": "synthetic", "counts": {}, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64),
            "first_obs_ms": None, "last_obs_ms": None}


def run(cohort, params, rail=None, series=None, slip=0.0, stress=None, kind="full", diag=False,
        count_layers=True, window=None):
    w = window or (0, cohort.n)
    return sg.simulate(cohort, params, rail or RAIL, w, stress or {}, slip, kind,
                       series or mk_series(cohort), diag=diag, count_layers=count_layers)


def reset_counters():
    sg.COUNTERS.clear()


def spec_stub():
    """The minimum spec surface the selector/tie-break needs (registered axes + floor)."""
    return {"family_id": "synthetic", "round_id": "synthetic-r1", "run_id": "synthetic-r1-u1",
            "parameter_domain": {"grid_cases": [{f: v for f, v in zip(sg.CASE_FIELDS, case)}
                                                for case in sg.CASE_ORDER]},
            "dca_domain": {"spacing_pct": [0.01, 0.02, 0.03, 0.04], "size_multiplier": [1.0, 1.1],
                           "breakeven_tp_pct": [0.01, 0.02, 0.03], "invalidation_pct": [0.05, 0.1]},
            "gates": {"min_episodes_is": 100}}


def session_rows(cohort, basis="trail30"):
    return cohort.sessions[basis]


class TestSessionConstruction(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_anchor_is_the_trailing_peak_slot(self):
        c = mk_cohort(40)
        rows = session_rows(c)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row[1], PEAK)

    def test_sessions_start_thirty_min_before_the_registered_entry(self):
        c = mk_cohort(40)
        _day, _anchor, e_hold, e_last30, x_bar, _sign, _q, _rv, _thr, start_ms = \
            session_rows(c)[0]
        self.assertEqual(int(c.open_time_ms[e_hold]), start_ms + sg.MS_PER_SLOT)
        self.assertEqual(int(c.open_time_ms[e_last30]), start_ms + MS_DAY - sg.MS_PER_SLOT)
        self.assertEqual(int(c.open_time_ms[x_bar] + c.bar_ms), start_ms + MS_DAY)

    def test_warmup_follows_the_registered_trailing_window_and_the_tail_is_complete_only(self):
        c = mk_cohort(40)
        # days 7..38 for trail7 (32 sessions) and days 30..38 for trail30 (9 sessions): the
        # LAST day of the cohort can never open a session, because its 24 h window would end
        # past the loaded data and is dropped (counted as not-fully-covered) rather than
        # truncated.
        self.assertEqual(len(c.sessions["trail7"]), 32)
        self.assertEqual(len(c.sessions["trail30"]), 9)
        self.assertGreater(sg.COUNTERS["sessions"]["session_not_fully_covered"], 0)

    def test_the_session_day_never_enters_its_own_profile(self):
        c = mk_cohort(12, day_volumes={(9, 40): 10 ** 9})
        rows = session_rows(c, "trail7")
        self.assertEqual([r[0] for r in rows], [7, 8, 9, 10])
        by_day = {r[0]: r[1] for r in rows}
        self.assertEqual(by_day[9], PEAK)                     # own day excluded
        self.assertEqual(by_day[10], 40)                      # the NEXT day may see it

    def test_ties_resolve_to_the_smallest_slot(self):
        vols = {}
        for d in range(10):
            vols[(d, 10)] = 2000.0
            vols[(d, 30)] = 2000.0
        c = mk_cohort(10, day_volumes=vols)
        self.assertEqual(session_rows(c, "trail7")[-1][1], 10)

    def test_signal_direction_follows_the_first_half_hour(self):
        c = mk_cohort(10, signal_move=0.001)
        by_day = {r[0]: r[5] for r in session_rows(c, "trail7")}
        self.assertEqual(by_day[7], 1)
        c2 = mk_cohort(10, signal_move=-0.001)
        by_day2 = {r[0]: r[5] for r in session_rows(c2, "trail7")}
        self.assertEqual(by_day2[7], -1)
        c3 = mk_cohort(10, signal_move=0.0)
        self.assertTrue(all(r[5] == 0 for r in session_rows(c3, "trail7")))

    def test_conditioning_uses_only_prior_sessions(self):
        c = mk_cohort(80)
        rows = session_rows(c, "trail30")
        self.assertEqual(len(rows), 49)
        self.assertTrue(all(r[8] is None for r in rows[:sg.RV_MEDIAN_SESSIONS]))
        self.assertTrue(all(r[8] is not None for r in rows[sg.RV_MEDIAN_SESSIONS:]))


class TestSessionEpisodes(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_hold_to_close_entry_is_the_post_signal_bar(self):
        c = mk_cohort(40)
        eps = sg.session_episodes(c, 0, c.n, case_tuple_of(1, 0, 0), 0, "full")
        self.assertEqual(len(eps), 9)
        b, x, sign = eps[0]
        row = session_rows(c)[0]
        self.assertEqual(b, row[2])
        self.assertEqual(x, row[4])
        self.assertEqual(sign, row[5])

    def test_last_half_hour_episode_is_the_final_slot(self):
        c = mk_cohort(40)
        eps = sg.session_episodes(c, 0, c.n, case_tuple_of(1, 1, 0), 0, "full")
        b, x, _sign = eps[0]
        row = session_rows(c)[0]
        self.assertEqual(b, row[3])
        self.assertEqual(x, row[4])
        self.assertEqual(x, b)          # exactly one bar of holding on the 30m base grid

    def test_entry_delay_moves_the_entry_not_the_exit(self):
        c = mk_cohort(40)
        plain = sg.session_episodes(c, 0, c.n, case_tuple_of(1, 0, 0), 0, "full")
        delayed = sg.session_episodes(c, 0, c.n, case_tuple_of(1, 0, 0), 1, "entry_delay_1_bar")
        self.assertEqual([(b + 1, x) for b, x, _s in plain],
                         [(b, x) for b, x, _s in delayed])

    def test_slice_clipping_is_counted_not_truncated(self):
        c = mk_cohort(40)
        eps = sg.session_episodes(c, 0, 30 * sg.SLOTS_PER_DAY, case_tuple_of(1, 0, 0), 0, "oos")
        self.assertTrue(all(x < 30 * sg.SLOTS_PER_DAY for _b, x, _s in eps))
        self.assertGreater(sg.COUNTERS["oos"]["window_clipped_at_slice_edge"], 0)

    def test_zero_signal_sessions_are_skipped_and_counted(self):
        c = mk_cohort(40, signal_move=0.0)
        eps = sg.session_episodes(c, 0, c.n, case_tuple_of(1, 0, 0), 0, "full")
        self.assertEqual(eps, [])
        self.assertGreater(sg.COUNTERS["full"]["session_with_zero_signal"], 0)

    def test_unregistered_case_fails_closed(self):
        c = mk_cohort(40)
        with self.assertRaises(SystemExit):
            sg.simulate(c, {"basis_trail7": 0, "basis_trail30": 0, "basis_trail90": 0,
                            "entry_hold_to_close": 0, "entry_last_half_hour": 0,
                            "cond_high_vol": 0}, RAIL, (0, c.n), {}, 0.0, "full",
                        mk_series(c))


class TestRail(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_flat_long_pays_entry_and_exit_fees_only(self):
        c = mk_cohort(40)
        c.taker_fee = 0.0005
        m = run(c, case_params(1, 1, 0))
        qty = RAIL["base_quote"] * LEV / P0
        self.assertEqual(m["fills"], 2 * m["episodes"])
        self.assertAlmostEqual(m["fees"], 2 * m["episodes"] * qty * P0 * 0.0005, places=6)
        self.assertAlmostEqual(m["gross_pnl"], 0.0, places=6)
        self.assertAlmostEqual(m["net_pnl"], -m["fees"], places=6)
        self.assertTrue(sg.pnl_decomposition_ok(m))

    def test_long_tp_fires_on_the_high_and_keeps_the_identity(self):
        c = mk_cohort(40)
        c.taker_fee = 0.0005
        for row in session_rows(c):
            for i in range(row[2], row[4] + 1):
                c.high[i] = P0 * 1.015
        m = run(c, case_params(1, 1, 0))
        self.assertEqual(m["tp_hits"], m["episodes"])
        self.assertGreater(m["gross_pnl"], 0.0)
        self.assertTrue(sg.pnl_decomposition_ok(m))

    def test_fee_2x_moves_net_and_never_gross(self):
        c = mk_cohort(40)
        c.taker_fee = 0.0005
        base = run(c, case_params(1, 1, 0))
        stressed = run(c, case_params(1, 1, 0), stress={"fee_mult": 2.0}, kind="fee_2x")
        self.assertAlmostEqual(base["gross_pnl"], stressed["gross_pnl"], places=6)
        self.assertGreater(stressed["fees"], base["fees"])
        self.assertLess(stressed["net_pnl"], base["net_pnl"])

    def test_short_mirror_profits_when_the_session_falls(self):
        c = mk_cohort(40, signal_move=-0.001, session_move=-0.02)
        c.taker_fee = 0.0005
        self.assertTrue(all(r[5] == -1 for r in session_rows(c)))
        m = run(c, case_params(1, 0, 0))
        self.assertGreater(m["net_pnl"], 0.0)
        self.assertTrue(sg.pnl_decomposition_ok(m))

    def test_overlap_skip_is_a_live_counter(self):
        c = mk_cohort(40)
        rows = []
        for row in session_rows(c):
            row_list = list(row)
            if row[0] >= 32:                 # move the anchor TWO slots EARLIER
                row_list[1] = PEAK - 2
                row_list[2] = row[2] - 2
                row_list[3] = row[3] - 2
                row_list[9] = row[9] - 2 * sg.MS_PER_SLOT
            rows.append(tuple(row_list))
        c.sessions["trail30"] = rows
        eps = sg.session_episodes(c, 0, c.n, case_tuple_of(1, 0, 0), 0, "full")
        m = run(c, case_params(1, 0, 0))
        self.assertGreater(sg.COUNTERS["full"]["overlap_skip"], 0)
        self.assertLess(m["episodes"], len(eps))

    def test_funding_inside_the_session_is_charged_and_outside_is_not(self):
        c = mk_cohort(40)
        c.taker_fee = 0.0
        row = session_rows(c)[0]
        before = row[9] - 5 * 3600000
        inside = row[9] + 12 * 3600000
        series = mk_series(c, [(before, 0.0001), (inside, 0.0001)])
        m = run(c, case_params(1, 0, 0), series=series)
        qty = RAIL["base_quote"] * LEV / P0
        self.assertAlmostEqual(m["funding"], qty * P0 * 0.0001, places=6)
        self.assertTrue(sg.pnl_decomposition_ok(m))

    def test_high_vol_case_can_be_a_subset_of_the_unconditional_case(self):
        c = mk_cohort(45)
        rows = session_rows(c)
        for idx, row in enumerate(rows):
            row_list = list(row)
            row_list[6] = bool(idx % 2)
            rows[idx] = tuple(row_list)
        c.sessions["trail30"] = rows
        alls = run(c, case_params(1, 0, 0))
        hv = run(c, case_params(1, 0, 1))
        self.assertLess(hv["episodes"], alls["episodes"])


class TestGates(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_band_strings_match_the_bundle_writer(self):
        import importlib.util as ilu
        path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(ENGINE)),
                                             "..", "..", "runtime", "survivor_bundle.py"))
        if not os.path.exists(path):
            self.skipTest("runtime/survivor_bundle.py not present in this checkout")
        spec = ilu.spec_from_file_location("sb", path)
        sb = ilu.module_from_spec(spec)
        spec.loader.exec_module(sb)
        for count in (0, 1, 2):
            survivors = [{"cohort": "c%d" % i} for i in range(count)]
            got = sg.family_disposition(survivors, True)["disposition"]
            self.assertEqual(got, sb.band_for(count))

    def test_disposition_mapping_is_the_v140_table(self):
        self.assertEqual(sg.family_disposition([], True)["verdict_recommendation"], "REJECT")
        self.assertEqual(sg.family_disposition([{"x": 1}], True)["verdict_recommendation"], "PASS")
        self.assertEqual(sg.family_disposition([{"x": 1}, {"x": 2}], True)["disposition"],
                         "MULTIPLE_SURVIVORS")
        self.assertEqual(sg.family_disposition([{"x": 1}], False)["disposition"],
                         "TECHNICAL_INCOMPLETE")
        self.assertFalse(sg.family_disposition([], True)["performance_claimable_recommendation"])
        self.assertFalse(sg.family_disposition([{"x": 1}], False)
                         ["performance_claimable_recommendation"])

    def test_selector_is_historical_only_and_ranked_by_sharpe_first(self):
        rows = []
        for i, case in enumerate(sg.CASE_ORDER):
            rows.append({"symbol": "SYNTH", "timeframe": "30m", "window_kind": "historical",
                         "window_case": sg.CASE_NAMES[i],
                         **{f: v for f, v in zip(sg.CASE_FIELDS, case)},
                         "spacing_pct": 0.02, "size_multiplier": 1.1, "breakeven_tp_pct": 0.01,
                         "invalidation_pct": 0.05, "net_pnl": 10.0 + i, "sharpe": 1.0,
                         "episodes": 400})
        rows[5]["sharpe"] = 3.0
        winner, reason = sg.select_cohort_winner(rows, spec_stub())
        self.assertEqual(reason, "selected")
        self.assertEqual(sg.case_index(sg.case_tuple(winner)), 5)

    def test_selector_refuses_oos_rows(self):
        rows = [{"symbol": "SYNTH", "timeframe": "30m", "window_kind": "oos",
                 **{f: v for f, v in zip(sg.CASE_FIELDS, sg.CASE_ORDER[0])},
                 "spacing_pct": 0.02, "size_multiplier": 1.1, "breakeven_tp_pct": 0.01,
                 "invalidation_pct": 0.05, "net_pnl": 100.0, "sharpe": 9.0, "episodes": 400}]
        with self.assertRaises(ValueError):
            sg.select_cohort_winner(rows, spec_stub())

    def test_boundary_guards_stay_silent_on_a_clean_grid(self):
        c = mk_cohort(40)
        sg.simulate(c, case_params(1, 1, 0), RAIL, (0, c.n), {}, 0.0, "full", mk_series(c))
        self.assertEqual(sg.counters_total("case_not_registered"), 0)
        self.assertEqual(sg.counters_total("entry_bar_not_at_registered_boundary"), 0)
        self.assertEqual(sg.counters_total("exit_bar_not_at_window_end"), 0)
        self.assertEqual(sg.counters_total("entry_before_signal_complete"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
