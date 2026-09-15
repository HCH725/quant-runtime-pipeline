#!/usr/bin/env python3
"""One-off authoring tool: emit the frozen Strategy E v1 round-spec template.

The emitted JSON is the frozen pre-registration artifact (runtime/templates/
strategy_e_v1_round_spec.template.json); this script only exists so the 48-entry DCA grid and
the 20-entry strategy case domain are generated from the declared axes instead of being typed
by hand.  Run once; the template is never regenerated afterwards (INV-4).
"""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "templates/strategy_e_v1_round_spec.template.json")

FAMILY = "copula-cmi-pairs-relative-value-perp-v1"
FINGERPRINT_INPUT = ("copula-cmi-pairs-relative-value-perp-v1|copulas=gaussian,t,clayton,gumbel,"
                     "frank;formation=2160h;t_o=0.20,0.25,0.30,0.35,0.40;trading=336h|"
                     "breakeven_tp_pct=0.01,0.02,0.03;invalidation_pct=0.05,0.10;"
                     "size_multiplier=1.0,1.1;spacing_pct=0.01,0.02,0.03,0.04|"
                     "2022-01-01..2026-09-11|1h|market-neutral|"
                     "symbols=BNBUSDT,BTCUSDT,ETHUSDT,SOLUSDT|"
                     "selector=cohort-selector-v1;disposition=cohort-disposition-v1")
T_O = [0.20, 0.25, 0.30, 0.35, 0.40]
T_C = [0.05, 0.10, 0.15]
SUB_REF = [0.30, 0.10]
COPULA_LABELS = ["aic_selected", "gaussian", "student_t", "clayton", "gumbel", "frank"]
PAIRS = [["BTCUSDT", "ETHUSDT"], ["BTCUSDT", "BNBUSDT"], ["BTCUSDT", "SOLUSDT"],
         ["ETHUSDT", "BNBUSDT"], ["ETHUSDT", "SOLUSDT"], ["BNBUSDT", "SOLUSDT"]]
SPACING = [0.01, 0.02, 0.03, 0.04]
MULT = [1.0, 1.1]
TP = [0.01, 0.02, 0.03]
INV = [0.05, 0.10]
GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps",
         "source_cost_0p12pct"]

CASES = [[o, c, 0] for o in T_O for c in T_C] + [[SUB_REF[0], SUB_REF[1], f] for f in range(1, 6)]
CASE_NAMES = []
for o, c, f in CASES:
    label = "aic" if f == 0 else COPULA_LABELS[f]
    CASE_NAMES.append("%s_to%02d_tc%02d" % (label, round(o * 100), round(c * 100)))
DCA_GRID = [{"spacing_pct": s, "size_multiplier": m, "breakeven_tp_pct": t,
             "invalidation_pct": i, "base_quote": 1000}
            for s in SPACING for m in MULT for t in TP for i in INV]
SEARCH = ("PROJECT_PRE_REGISTERED_SEARCH_DOMAIN - this axis IS searched over the registered "
          "values. The axis is a pre-registered search DOMAIN of this round, not an immutable "
          "user-fixed invariant (contract 7.2 v1.3.1).")
CONSTANT = ("PROJECT_PRE_REGISTERED_CONSTANT - held constant across the whole domain (it is NOT "
            "a search axis) and NOT a user-fixed invariant: no operator evidence fixes it at "
            "1000. It is a project pre-registration choice, frozen here before any computation "
            "(contract 7.2 v1.3.1).")

doc = {
    "schema_version": 1,
    "document_kind": "round-spec template (Strategy E v1; instantiate to "
                     "/results/<family_id>/rounds/<round_id>/round-spec.json BEFORE launch)",
    "template_instantiation": {
        "placeholders": ["{{round_id}}", "{{kanban_task_id}}", "{{created_at_utc}}"],
        "rule": "Every scientific field in this template is FROZEN before the first run. "
                "Instantiation may only substitute the three placeholders, the "
                "TO_BE_RECOMPUTED fingerprint and the template-era implementation-status prose; "
                "it must not touch the domains, the split, the cycle plan, the selector/"
                "disposition versions, the gates or the falsification list (contract INV-4).",
        "instantiated_to": "/results/<family_id>/rounds/<round_id>/round-spec.json",
        "validated_by": "python3 runtime/strategy_e_v1_counts.py --spec "
                        "runtime/templates/strategy_e_v1_round_spec.template.json",
        "status": "PREREGISTRATION ONLY / NOT LAUNCHED"
    },
    "family_id": FAMILY,
    "family_title": "Production Strategy E - Copula-CMI pairs relative value (market neutral), "
                    "BTC/ETH/BNB/SOL USD-M perp, six pairs at 1h",
    "round_id": "{{round_id}}",
    "kanban_task_id": "{{kanban_task_id}}",
    "kanban_board": "quant-strategy-research",
    "created_at_utc": "{{created_at_utc}}",
    "authored_by": "Hermes default (Xiaoqian) - Contract v1.7.0 production card "
                   "{{kanban_task_id}}",
    "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.7.0 (cohort survivor "
                "semantics of sections 7.2 / 7.3; the v1.8.0 launch gate additionally requires "
                "this document's generic parameter_contract, which is generated below from the "
                "domains registered here)",
    "implementation_status": {
        "engine": "TO_BE_SET_AT_INSTANTIATION (/scripts/50_strategy_e_run.py sha256)",
        "engine_selfcheck": "TO_BE_SET_AT_INSTANTIATION (/scripts/tests/test_strategy_e_engine.py "
                            "sha256; run in qlib-run before launch)",
        "no_launch_in_this_card": "The preregistration template was authored without launching "
                                  "anything; the launch is performed by card "
                                  "{{kanban_task_id}} and lands in this instantiated copy."
    },
    "semantic_fingerprint": {
        "fingerprint_input": FINGERPRINT_INPUT,
        "normalisation": "The frozen handoff value (contract 14.2 step 7 / 14.4): family_id | "
                         "signal family and its source-specified mechanics | DCA domain (axes "
                         "alphabetically) | data window | timeframe | direction | symbols | "
                         "selector;disposition version. It was written into the immutable "
                         "/results family.json by the automatic production handoff and is copied "
                         "here VERBATIM; this template never rewrites it.",
        "semantic_fingerprint": "TO_BE_RECOMPUTED",
        "registered_axis_vs_family_fingerprint": "Disclosure (no silent widening): the frozen "
            "fingerprint_input records the copula family set, the 2160h formation / 336h trading "
            "cycle, the t_o grid, the market-neutral direction, the four DCA axes, the split and "
            "the selector/disposition versions. This round-spec registers the COMPLETE bounded "
            "strategy case domain those fields define: every (t_o, t_c) cell with the "
            "AIC-selected copula plus the five forced-family substitution cases at the "
            "registered reference cell. The exit-threshold grid t_c and the substitution "
            "reference are registered HERE, before the first computation, because the card "
            "requires a complete strategy parameter domain rather than a single-point trial. "
            "The family record is immutable (INV-4); the delta is disclosed here instead of "
            "being hidden."
    },
    "parent_family": None,
    "lineage_note": "Independent family (signal family = relative-value statistical arbitrage: "
                    "cointegration screen + copula conditional-CDF deviation on a beta-hedged "
                    "pair spread). The Kanban parent edge is scheduling/order only; scientific "
                    "lineage is null: this family does not refine, inherit parameters from, or "
                    "shrink the universe of Strategy A (close-vs-SMA mean reversion, REJECT), "
                    "Strategy B (EMA-crossover momentum) or Strategy C (funding-decile "
                    "contrarian, REJECT) or Strategy D (UTC clock-hour seasonality, REJECT). It "
                    "trades a two-leg market-neutral spread rather than a single-asset "
                    "direction. Source: the reviewed wiki record "
                    "quant/copula-cmi-crypto-perpetual-pairs-trading-market-overlay-2026-09-07.md "
                    "(status research-only, implementation_status not-implemented, adoption "
                    "not-approved at handoff time).",
    "hypothesis": {
        "statement": "Two cointegrated perpetuals share a long-run equilibrium; deviations of "
                     "their joint distribution from the fitted copula are transient and revert. "
                     "The Copula Mispricing Index CMI_t = h_{1|2}(U1_t | U2_t) - 0.5 measures the "
                     "current mispricing; a beta-hedged long-spread entry at an extreme CMI is "
                     "expected to earn the reversion after fees, funding and the registered "
                     "adverse slippage.",
        "mechanism": "Relative-value liquidity provision between two legs of the same market. "
                     "The signal is cross-sectional (the pair's joint distribution), not the "
                     "price level of a single asset, so it is orthogonal in information set to "
                     "Strategy A (single-asset mean reversion), Strategy B (single-asset "
                     "momentum) and Strategy D (clock-time seasonality).",
        "direction": "BOTH directions are registered and evaluated separately: long A1 / short "
                     "A2 on CMI < -t_o, short A1 / long A2 on CMI > +t_o. Every cohort is judged "
                     "independently (contract 7.3); the two directions are never merged into one "
                     "number by the gate.",
        "test_statistic": "Every one of the 6 pair cohorts (BTC-ETH, BTC-BNB, BTC-SOL, ETH-BNB, "
                          "ETH-SOL, BNB-SOL at 1h): the deterministic historical-only winner "
                          "cell is carried unchanged into OOS, the full window, the eleven "
                          "registered phase grids and its historical parameter neighbourhood. "
                          "The family statistic is the COUNT of cohort survivors (0..6), never a "
                          "cross-cohort median and never the best cohort.",
        "source": "Reviewed wiki record quant/copula-cmi-crypto-perpetual-pairs-trading-market-"
                  "overlay-2026-09-07.md (Pindza & Mba 2026, QFE 10(2) 378-404; a 10-symbol "
                  "Binance USD-M perp study, treated as research-only).",
        "declared_negative_evidence": "The source's primary finding is NEGATIVE: market-neutral "
            "copula pairs trading earned -14% to -16% total (-5.0% to -5.8% annualized, Sharpe "
            "-2.64 to -3.67) after realistic costs and funding, with funding alone consuming "
            "0.18% per trade against a 0.15% gross edge. This round therefore re-measures a "
            "falsifiable negative-result hypothesis under this machine's canonical cost and "
            "funding accounting; it is NOT a replication of a known-profitable strategy, and a "
            "negative outcome is a legitimate, pre-declared result.",
        "pair_test": "Each pair is evaluated with its own formation fit (hedge ratio, "
                     "cointegration, half-life, copula family, CMI) and its own winner cell; the "
                     "cross-pair distribution of outcomes is reported as a descriptive "
                     "(non_gating) diagnostic."
    },
    "eligible_universe": {
        "symbols": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "pairs": PAIRS,
        "pair_order_rule": "A1 = the first-named symbol of the registered pair (the leg whose "
                           "conditional CDF the CMI is built on), A2 = the second. The order is "
                           "registered here and never re-picked after results.",
        "symbol_source": "BINANCE USD-M perpetual, canonical raw store (/data/raw, read-only)",
        "timeframes": [{"raw_interval": "1h", "qlib_freq": "60min"}],
        "cohort_count": 6,
        "cohort_definition": "one cohort = one pair at 1h (the leg-aware mapping of the v1.3.0 "
                             "(symbol, timeframe) disposition unit); all six cohorts are always "
                             "reported separately and none may be dropped",
        "universe_shrinkage_disclosure": "The source studies 10 perpetuals (BTC/ETH/BNB/ADA/SOL/"
            "XRP/DOT/LTC/LINK/DOGE). The canonical raw store of this machine holds only 4 of "
            "them, so the pair universe is the complete set of 6 pairs those 4 symbols form. "
            "The shrinkage is fixed BEFORE any computation and must not be narrowed or widened "
            "afterward.",
        "instruments_metadata_measured_at_pre_registration": {
            "BTCUSDT": {"price_increment": 0.1, "taker_fee": 0.0005, "maker_fee": 0.0002,
                        "margin_init": 0.1},
            "ETHUSDT": {"price_increment": 0.01, "taker_fee": 0.0005, "maker_fee": 0.0002,
                        "margin_init": 0.1},
            "BNBUSDT": {"price_increment": 0.01, "taker_fee": 0.0005, "maker_fee": 0.0002,
                        "margin_init": 0.1},
            "SOLUSDT": {"price_increment": 0.01, "taker_fee": 0.0005, "maker_fee": 0.0002,
                        "margin_init": 0.1}
        },
        "excluded": ["every other symbol and every other timeframe present in raw (the 1h bar "
                     "grid is the source-specified price reference of this family)"],
        "universe_immutability": "Registered here, before computation. Must not be narrowed or "
                                 "widened after seeing results (contract 7.2)."
    },
    "parameter_domain": {
        "kind": "strategy parameter domain (the registered signal case set): the complete "
                "t_o x t_c grid with the AIC-selected copula plus the five forced-family "
                "substitution cases at the registered reference cell",
        "case_definition": "a strategy case is (t_o, t_c, copula_family) where copula_family is "
                           "the registered numeric index 0 = AIC-selected, 1..5 = gaussian, "
                           "student_t, clayton, gumbel, frank (forced)",
        "grid_cases": [{"t_o": o, "t_c": c, "copula_family": f} for o, c, f in CASES],
        "grid_case_names": CASE_NAMES,
        "legal_cases_per_cohort": 20,
        "t_o_grid": T_O,
        "t_c_grid": T_C,
        "substitution_reference": SUB_REF,
        "copula_labels": COPULA_LABELS,
        "copula_selection_status": "RESEARCH_PROPOSED (registered before the first run) - the "
            "source specifies AIC selection over the five families and a Rosenblatt-transform "
            "GOF (KS and CvM, bootstrap p-value >= 5%); this round additionally registers the "
            "five forced-family substitution cells at the reference cell so the family "
            "substitution the source lists as a falsification item is measured, not assumed. "
            "The substitution never replaces the primary AIC selection: it is a separate "
            "registered case and is reported on its own.",
        "t_o_status": "SOURCE_NORMALIZED - the t_o grid {0.20, 0.25, 0.30, 0.35, 0.40} is the "
                      "source's own entry-threshold grid, registered verbatim.",
        "t_c_status": "RESEARCH_PROPOSED - the source names the CMI-reversion exit but gives no "
                      "threshold values; this round registers the t_c grid {0.05, 0.10, 0.15}, "
                      "frozen before the first run and never re-picked after results.",
        "entry_timing_status": "RESEARCH_PROPOSED - the source assumes same-bar execution; this "
            "round registers NEXT-BAR-OPEN execution (a bar-close signal fills at the following "
            "bar's open), which is the contract's no-look-ahead rule and is disclosed as a "
            "deliberate deviation from the source's assumption.",
        "exit_rule_status": "RESEARCH_PROPOSED - the source states exit on CMI reversion, a "
            "profit target, a stop-loss or a maximum holding period but fixes none of the "
            "numbers; this round registers spread PT = 2%, spread SL = 4% and max holding = 336 "
            "bars, plus the DCA rail's own per-leg take profit and resting invalidation.",
        "substitution_rule": "REGISTERED BEFORE THE FIRST RUN (no post-hoc combination): the "
            "cohort winner is elected ONLY by the deterministic HISTORICAL-only selector of "
            "contract 7.3 over this registered domain; the OOS window selects nothing and is "
            "never used to decide inclusion. A forced-family case can be a winner like any "
            "other case, and it then carries the same five survivor requirements.",
        "domain_immutability": "The domain is complete and bounded; no post-hoc parameter "
                               "additions, no selection on OOS (contract 7.3)."
    },
    "cycle_domain": {
        "kind": "registered cycle plan (fixed; NOT searched)",
        "formation_bars": 2160,
        "trading_bars": 336,
        "anchor": "2022-01-01",
        "cycle_anchoring": "Cycle boundaries are anchored on the registered data start "
            "2022-01-01T00:00Z, so every slice (historical / oos / full / every phase grid) "
            "evaluates a SUBSET of the same cycles and the historical/OOS split never moves a "
            "cycle boundary. A slice evaluates exactly the cycles whose whole 336-bar trading "
            "window lies inside it; the formation window of an OOS cycle may reach back into the "
            "historical period (walk-forward: it uses only information available at its own "
            "start, and no future bar enters any fit).",
        "cointegration_rule": "Engle-Granger (ADF on the formation residual, constant, 24 "
            "registered lags) AND Kapetanios-Shin-Snell (nonlinear unit root on the demeaned "
            "residual) must BOTH reject the unit root at the registered 5% critical values, and "
            "the residual's mean-reversion half-life must be below 30 days.",
        "adf_lags": 24,
        "adf_cv_5pct": -3.34,
        "kss_cv_5pct": -2.93,
        "half_life_max_days": 30.0,
        "hedge_ratio_rule": "OLS slope of log P1 on log P2 over the formation window "
            "(source-specified). Leg 2's notional is |beta| x leg 1's notional at every fill, so "
            "the pair's beta-hedged net notional is zero at entry by construction (asserted in "
            "artifacts/assertions.json).",
        "hedge_beta_min": 0.2,
        "hedge_beta_max": 5.0,
        "copula_families": ["gaussian", "student_t", "clayton", "gumbel", "frank"],
        "copula_fit_rule": "MLE per family on the formation pseudo-observations; AIC = 2k - 2ll "
            "selects the family. Student-t is fitted with a profiled two-stage procedure (nu on "
            "the registered grid {3,4,5,6,7.5,9,12,16,25,50}, rho by bounded MLE for each "
            "candidate), used identically for the observed fit and for every bootstrap refit; "
            "the other four families are fitted by one-parameter bounded MLE.",
        "gof_bootstrap": 100,
        "gof_min_p": 0.05,
        "gof_rule": "Rosenblatt-transform GOF of the SELECTED family: the Rosenblatt coordinate "
            "w = h_{1|2}(u1|u2) must be U(0,1); KS and CvM statistics are compared against their "
            "own parametric-bootstrap distribution (B = 100 refits of the same family on "
            "simulated samples). Both p-values must be >= 5% or the cycle is not tradable. The "
            "second Rosenblatt coordinate is u2 itself, uniform by construction of the frozen "
            "empirical marginals, so it carries no test information and is not re-tested.",
        "marginal_rule": "The marginal transform is the FROZEN empirical CDF of the formation "
            "window: u = #{formation values <= x} / n. A trading price outside the formation "
            "range saturates at 0 or 1; the saturation counts are reported per cycle in "
            "artifacts/cycle_diagnostics.json. No trading-window information enters the "
            "marginal, the fit or the family selection.",
        "cmi_rule": "CMI_t = h_{1|2}(U1_t | U2_t) - 0.5 in [-0.5, 0.5] on the trading window's "
            "bars, using the cycle's frozen fit. CMI < -t_o -> long A1 / short A2; CMI > +t_o -> "
            "short A1 / long A2; the entry fills at the NEXT bar's open.",
        "spread_pt": 0.02,
        "spread_sl": 0.04,
        "max_holding_bars": 336,
        "max_holding_note": "The registered holding bound is an OUTER bound and is disclosed as "
            "such: because the trading window is itself 336 bars and an entry always fills at "
            "the bar AFTER its signal bar, the cycle-end flatten binds at or before the "
            "max-holding bar. The engine still evaluates the holding condition on every "
            "in-market bar and reports max_holding_exits separately (0 by construction in this "
            "family), so the registered bound is visible in the artifacts.",
        "reentry_rule": "At most ONE episode per cycle (one entry and one exit): after the "
            "position is flat the cycle records no further entry. This is a registered choice "
            "frozen before the first run; between cycles the book is FLAT.",
        "criteria_status": "RESEARCH_PROPOSED - the source specifies the tests and screens but "
            "not the critical values, the lag length, the hedge-ratio band or the bootstrap "
            "size; every number used by this round is registered above and is applied "
            "identically to all six cohorts.",
        "cycle_immutability": "Fixed before the first run; never re-tuned after results."
    },
    "dca_domain": {
        "kind": "DCA parameter domain (the execution rail), four axes - contract 7.2 item 4",
        "base_quote": 1000,
        "base_quote_status": CONSTANT,
        "spacing_pct": SPACING,
        "spacing_pct_status": SEARCH,
        "size_multiplier": MULT,
        "size_multiplier_status": SEARCH,
        "breakeven_tp_pct": TP,
        "breakeven_tp_pct_status": SEARCH,
        "invalidation_pct": INV,
        "invalidation_pct_status": SEARCH,
        "config_count": 48,
        "grid": DCA_GRID
    },
    "dca_execution_semantics": {
        "authorization": "Inherited verbatim from the Strategy A/B/C/D engine family (per-fill "
            "fee accounting, breakeven-anchored take profit, resting invalidation, reduce-only "
            "exits, capital-exhaustion backstop). This round applies the rail LEG-AWARE (see "
            "leg_aware_rule) and does not re-interpret the single-asset semantics.",
        "leg_aware_rule": "The 12-tranche rail is applied at the PAIR level: level 0 is the "
            "simultaneous entry of both legs; levels 1..10 are simultaneous adverse-price "
            "scale-ins whose spread offsets are k x spacing_pct measured on the beta-hedged log "
            "spread S_t = log P1_t - beta x log P2_t (so the two legs' level alignment is "
            "anchored on the SPREAD, never on one leg's price); tranche #12 (index 11) is the "
            "reserve and is never routinely deployed; each LEG therefore carries at most 11 "
            "active levels. Each leg's own running average cost anchors its breakeven take "
            "profit and its resting invalidation, and every fill of every level is slipped "
            "adversely on that leg's own price increment.",
        "sizing": "level k: leg 1 notional = base_quote x size_multiplier^k x leverage; leg 2 "
                  "notional = |beta| x leg 1 notional (the registered hedge-ratio rule). Margins "
                  "sum over the two legs at the pair level.",
        "take_profit": "two registered layers: (a) the PAIR spread profit target at +2% of the "
            "beta-hedged spread; (b) each leg's own breakeven-anchored take profit at "
            "running_average_cost x (1 +/- breakeven_tp_pct). Either closes BOTH legs "
            "reduce-only.",
        "invalidation": "two registered layers: (a) the PAIR spread stop at -4% of the "
            "beta-hedged spread; (b) each leg's RESTING invalidation at running_average_cost x "
            "(1 -/+ invalidation_pct). Either closes BOTH legs reduce-only.",
        "exit_order": "Registered order inside one bar: (1) the adverse ladder and the resting "
            "stops, (2) the spread profit target and the leg take profits, (3) the CMI "
            "reversion at the bar close, (4) the max holding bound and the cycle end.",
        "gap_rules": "An adverse gap through a stop is never flattered: it fills at the bar's "
            "open (the first price really available). A favourable gap is never credited: a "
            "take profit fills at its own level. Pair-level exits fill at the adverse/favourable "
            "sample prices of the bar (the extreme samples), which is conservative with respect "
            "to the registered level.",
        "intrabar_ordering": "Deterministic and conservative; the same-bar multi-level crossing "
            "order is the registered ladder-walk order, and the two legs of one level always "
            "fill together in the same bar.",
        "funding": "charged per leg on its own notional at every settlement inside the CLOSED "
            "holding interval [entry - 1s, exit + 1s], never assuming a fixed 8h grid.",
        "capital_exhaustion": "backstop only: forced full flatten at the bar close if "
            "unrealised equity <= margin_maint x total notional; no new episode is opened once "
            "the realised equity is gone.",
        "concurrency": "one pair episode at a time; the two legs are always opened and closed "
            "together; no add after a FLAT/kill; the book is FLAT between cycles.",
        "market_neutrality": "leg 2 notional = |beta| x leg 1 notional at entry and at every "
            "scale-in, so the pair's beta-hedged net notional is zero by construction; the "
            "engine asserts this per entry."
    },
    "selector_and_disposition": {
        "selector_version": "cohort-selector-v1",
        "disposition_version": "cohort-disposition-v1",
        "selection_protocol": "Deterministic, HISTORICAL window ONLY; OOS must never be used to "
                              "select parameters (contract 7.3).",
        "selection_steps": [
            "1. Sufficiency: if the cohort's best historical case has fewer than "
            "min_episodes_is (60) pair-cycles, the cohort is culled with reason "
            "insufficient_trades.",
            "2. Eligibility: a case is a candidate iff historical net_pnl > 0 AND historical "
            "sharpe > 0 AND historical pair-cycles >= min_episodes_is.",
            "3. If no case is a candidate, the cohort is culled with reason "
            "no_qualifying_candidate.",
            "4. Ranking: Sharpe descending, then net_pnl descending, then a fixed lexical "
            "tie-break over the REGISTERED INDEX of each axis in the fixed order t_o, t_c, "
            "copula_family, spacing_pct, size_multiplier, breakeven_tp_pct, invalidation_pct. "
            "The first case is the cohort winner.",
            "5. The winner's strategy params + DCA params are carried as ONE frozen bundle into "
            "OOS / full / robustness / neighbourhood; the bundle is never re-chosen after OOS "
            "is seen."
        ],
        "tie_break_axis_order": ["t_o", "t_c", "copula_family", "spacing_pct", "size_multiplier",
                                 "breakeven_tp_pct", "invalidation_pct"],
        "neighbourhood_axes": ["t_o", "t_c", "spacing_pct", "size_multiplier",
                               "breakeven_tp_pct", "invalidation_pct"],
        "cohort_survivor_requirements": [
            "a) a historical winner exists (steps 1-4)",
            "b) OOS: net_pnl > 0 AND sharpe > 0 AND annualized Sharpe >= 0.40 AND annualized "
            "return >= 0% for the winner's cell",
            "c) full window: net_pnl > 0 for the winner's cell",
            "d) robustness: the SAME winner cell has net_pnl > 0 in each of fee_2x, "
            "funding_2x, entry_delay_1_bar, slippage_2ticks and in the cost_attrition_40bps grid",
            "e) parameter-neighbourhood stability: at least 60% of the winner cell's legal "
            "face-adjacent neighbours (+-1 registered step on exactly ONE axis, still inside the "
            "registered joint grid) agree with the winner's HISTORICAL net-PnL sign; historical "
            "rows only"
        ],
        "cohort_outcomes": ["SURVIVOR", "CULLED"],
        "cull_reasons": [
            "insufficient_trades", "no_qualifying_candidate", "oos_economic", "full_economic",
            "robustness_economic:<grids>", "parameter_neighbourhood"
        ],
        "family_disposition": {
            "0 cohort survivors": "REJECT / NO_SURVIVOR (verdict REJECT, performance_claimable "
                                  "false)",
            "1 cohort survivor": "SURVIVOR_FOUND (verdict PASS; performance_claimable still "
                                 "requires the full contract 9.6 condition set and no "
                                 "family-level falsification hit)",
            ">=1 cohort survivors": "PASS (band MULTIPLE_SURVIVORS above one); every survivor is "
                                    "frozen by contract 10.8 and advances, and the survivor "
                                    "count is never itself a reason for "
                                    "performance_claimable=false",
            "coverage or technical incompleteness": "TECHNICAL_INCOMPLETE (no cohort is judged "
                                                    "at all)"
        },
        "cross_cohort_medians": "never a gate (contract 7.2): any cross-cohort, cross-pair or "
                                "all-case median reported in the artifacts is descriptive and "
                                "marked non_gating",
        "no_signal_landing": "A cycle that fails the cointegration screen, the hedge-ratio band "
            "or the copula GOF gate holds no position (counted per cycle). A cohort whose "
            "historical best case has fewer than 60 pair-cycles is culled with "
            "insufficient_trades. If EVERY cohort produces zero tradable cycles the round "
            "records a technical failure of the copula layer rather than a scientific REJECT "
            "(the engine marks the run TECHNICAL_INCOMPLETE via its assertions)."
    },
    "registered_family_level_falsification": {
        "note": "Four FAMILY-level readers, registered before the first run. They never cull a "
                "cohort by themselves; a hit must NEVER be recorded as PASS (the runner "
                "recommends REJECT or DEFERRED per the evidence, mirroring the D-family "
                "landing).",
        "source-consistency": {
            "definition": "the elected winners' full-window net PnL sum is <= 0, i.e. this "
                          "round reproduces the source's negative net-return conclusion",
            "landing": "family-level: a hit must NEVER be recorded as PASS (it is recorded as "
                       "REJECT); reproducing the paper's negative finding is not a PASS"
        },
        "cost-boundary": {
            "definition": "for any elected winner, the full-window net-PnL sign under the "
                          "canonical cost track differs from the sign under the registered "
                          "source-reported cost track",
            "landing": "family-level falsification: the evidence decides REJECT vs DEFERRED; "
                       "never PASS"
        },
        "pair-concentration": {
            "definition": "among the survivors, the largest single pair's share of the total "
                          "positive OOS net PnL exceeds 70%",
            "landing": "family-level robustness disclosure: a hit must NEVER be recorded as "
                       "PASS (the evidence decides REJECT vs DEFERRED)"
        },
        "oos-grid-instability": {
            "definition": "an elected winner's t_o/t_c neighbourhood (the AIC cells) disagrees "
                          "in OOS net-PnL sign on 40% or more of its registered neighbours",
            "landing": "family-level disclosure of the registered grid-instability item (the "
                       "historical arm is the G7 gate); a hit must NEVER be recorded as PASS"
        }
    },
    "data": {
        "source": "/data/raw (host /Volumes/ExpansionDrive/market-data-raw), read-only",
        "venue": "BINANCE USD-M perpetual",
        "data_start": "2022-01-01",
        "data_end": "2026-09-11",
        "historical_start": "2022-01-01",
        "historical_end": "2025-09-30",
        "oos_start": "2025-10-01",
        "oos_end": "2026-09-11",
        "split_immutability": "The split is fixed here, before any computation, and must not be "
                              "moved afterwards.",
        "derived_store": "Built per attempt from raw into the rebuildable /qlib/work area via "
                         "upstream qlib dump_bin.py; read back exclusively through the qlib data "
                         "layer. Never written to /results. Bars are never resampled or "
                         "gap-filled: a missing bar stays missing, and a non-contiguous 1h grid "
                         "fails the run closed.",
        "bar_labelling": {
            "status": "VERIFIED",
            "verified_before_computation": True,
            "raw_field": "open_time_ms",
            "finding": "open_time_ms is the bar START in UTC: close_time_ms = open_time_ms + "
                       "3,599,999 ms on a contiguous 3,600,000 ms grid, and the first bar of "
                       "every registered symbol opens exactly at 2022-01-01T00:00:00Z",
            "pairs_verified_before_computation": "all six pairs' legs were measured to share the "
                                                 "same contiguous 1h grid over the registered "
                                                 "window (the engine fails closed on any "
                                                 "misalignment)",
            "consequence": "the entry signal of a cycle is computed at a bar CLOSE and fills at "
                           "the NEXT bar's open; no boundary is inferred from a label"
        },
        "funding_series_contract": {
            "registered_window": "2022-01-01T00:00:00Z .. 2026-09-11T23:59:59Z",
            "pair_alignment_rule": "Each leg carries its own funding series and its own "
                "settlement instants (the raw 2022 modeled segment and SOLUSDT's early-2022 "
                "2h/4h-era schedule included); the engine never assumes a fixed 8h grid and "
                "charges every settlement on its own instant against the position notional of "
                "the leg it belongs to.",
            "engine_rule": "the position is exposed to every settlement whose raw instant lies "
                "in the CLOSED interval [entry instant - 1s, exit instant + 1s]; a settlement "
                "inside the hold is charged at the close mark of the bar containing it, and a "
                "settlement at the exit instant is charged on the exit notional. The sign "
                "follows the leg: a positive rate is a cost to a long leg and a credit to a "
                "short leg.",
            "no_fabrication": "the artifacts must disclose the modelled/official split; no "
                              "modelled row may be relabelled official"
        }
    },
    "costs": {
        "source": "canonical instrument metadata (raw binance/usdm/instruments/"
                  "usdm-perp-instruments.json), read at run time, never hard-coded",
        "maker_fee": 0.0002,
        "taker_fee": 0.0005,
        "margin_init": 0.1,
        "margin_maint": 0.1,
        "apply_rule": "every fill is a market order: taker fee on the full traded notional of "
                      "that leg, plus adverse slippage of baseline_slippage_ticks instrument "
                      "ticks of that leg. No maker fills are assumed anywhere.",
        "baseline_slippage_ticks": 1,
        "baseline_slippage_provenance": "research assumption of this round (NOT user-specified); "
                                        "robustness re-measures it at 2 ticks",
        "funding": "charged per settlement inside each leg's own closed exposure interval "
                   "(rate x that leg's position notional); funding_2x doubles it and the "
                   "no_funding grids zero it (descriptive)",
        "funding_exposure_rule": "The position is exposed to every funding settlement whose raw "
            "instant lies in the CLOSED interval [entry instant - 1s, exit instant + 1s]. The "
            "measured raw jitter of the settlement timestamps is 0..28 ms LATE and never early, "
            "so a settlement sitting on a bar boundary is charged to the episode that holds the "
            "position at that instant, and every settlement is charged on its own leg's series "
            "and instant (never a fixed 8h assumption).",
        "cost_attrition_grid": "cost_attrition_40bps applies 8x the registered taker fee = "
                               "40 bps per fill, the registered attrition stress of the original "
                               "falsification battery (G8)",
        "source_reported_cost_track": {
            "grid": "source_cost_0p12pct",
            "per_fill_rate": 0.0003,
            "disclosure": "DELIBERATELY CHEAPER than the canonical 5 bps taker fee: the source "
                          "declares 0.04% taker per side plus 0.02% spread per leg = a 0.12% "
                          "two-leg round trip, which is 12 bps over four fills = 3 bps per "
                          "fill per leg. The track exists to re-measure the same cells under "
                          "the source's own cost assumption (the fairest comparison to the "
                          "source's negative result) and is reported as its own grid, never "
                          "substituted for the canonical track.",
            "funding": "actual funding is still charged (the source's per-trade funding impact "
                       "was a first-order cost in its own decomposition)"
        },
        "funding_immutability": "the funding baseline must not be swapped for a modelled series "
                                "to improve the outcome, nor vice versa"
    },
    "authorization_invariants": {
        "provenance_class": "USER_FIXED - only items with explicit operator evidence. A searched "
                            "axis, a project constant, or a signal-mechanics field must NOT be "
                            "listed here (contract 7.2 v1.3.1).",
        "starting_equity_usdt": 30000,
        "numeraire": "USDT (sole)",
        "venue": "BINANCE USD-M linear perpetual",
        "leverage": "10x (1 / margin_init)",
        "tranches": 12,
        "tranche_12": "reserve/buffer, never routinely deployed",
        "routine_active_levels": 11,
        "side": "long AND short: both legs of the pair are always traded, one long and one short, "
                "in the direction the registered CMI sign chooses",
        "market_neutrality": "beta-hedged: leg 2 notional = |beta| x leg 1 notional at entry and "
                             "at every scale-in, so the pair's net notional is neutral by "
                             "construction and no naked directional exposure is carried",
        "exit": "reduce-only",
        "no_add_after_flat_or_kill": True,
        "same_bar_multi_level_ordering": "deterministic and conservative - see "
                                         "dca_execution_semantics.exit_order and gap_rules"
    },
    "metrics_definitions": {
        "equity": "30000 + cumulative realised net PnL (USDT); unrealised marked at bar closes",
        "daily_series": "end-of-UTC-day equity marks of the EVALUATED slice, forward-filled "
                        "inside the slice only; the series is never padded with pre-window "
                        "history",
        "sharpe": "mean/std (ddof=1) of the slice's daily returns x sqrt(365); risk-free 0. The "
                  "slice scoping is registered explicitly because a padded series would dilute "
                  "the mean and standard deviation unevenly and silently shrink Sharpe - a "
                  "measurement defect, not a convention. OOS Sharpe is a gate (G4).",
        "max_dd": "peak-to-trough of the daily equity series, reported in % and USDT",
        "cagr": "(ending_equity / 30000) ^ (1/years) - 1, years from the slice's own day count",
        "max_effective_leverage": "max of (total pair notional / unrealised equity), sampled "
                                  "every in-market bar",
        "capital_utilization": "mean of (deployed margin / unrealised equity) over in-market bars",
        "turnover": "sum of |fill notional| over every entry / scale-in / exit fill of the cell",
        "dca_layer_histogram": "count of fills per ladder level, aggregated over all "
                               "full-window-registered base cases; direct evidence of how much "
                               "of the 12-tranche rail is reachable",
        "pair_cycles": "the episode count of a cell (one episode at most per cycle) - the "
                       "registered sufficiency unit of this family",
        "cohort_winner": "the historical-only selector output for that pair cohort",
        "pair_distribution": "descriptive (non_gating) cross-pair table of outcomes and medians"
    },
    "robustness_plan": {
        "declared_before_first_run": True,
        "must_not_narrow_universe_or_lower_gates": True,
        "items": [
            {"id": "R1", "name": "parameter-neighbourhood robustness",
             "method": "for each cohort winner, compare the winner's historical net-PnL sign "
                       "against its legal face-adjacent neighbours (+-1 registered step on "
                       "exactly one axis of t_o, t_c and the four DCA axes, still inside the "
                       "registered grid); require >= 60% sign agreement; historical rows only"},
            {"id": "R2", "name": "execution stress reruns",
             "method": "full-window evaluation of all registered cells under fee_2x, funding_2x, "
                       "entry_delay_1_bar, slippage_2ticks; each winner cell must stay net_pnl "
                       "> 0 in all four"},
            {"id": "R3", "name": "copula-family substitution",
             "method": "the five forced-family cases at the registered reference cell are part "
                       "of every phase grid, so the AIC-selected family's result is compared "
                       "against each of the five families one by one, per cohort; reported as a "
                       "non-gating diagnostic that never replaces the AIC selection"},
            {"id": "R4", "name": "t_o / t_c grid completeness",
             "method": "the full 5 x 3 (AIC) grid is evaluated in every phase grid; the whole "
                       "grid is reported, never only the best cell"},
            {"id": "R5", "name": "funding-baseline diagnostics",
             "method": "no_funding (historical window) and no_funding_full (full window) grids "
                       "for all cells; descriptive only, never a gate"},
            {"id": "R6", "name": "cost attrition (G8)",
             "method": "cost_attrition_40bps grid for all cells; the winner's cell must stay "
                       "net_pnl > 0, otherwise the cohort is culled with reason "
                       "robustness_economic:cost_attrition_40bps"},
            {"id": "R7", "name": "source-cost track",
             "method": "the registered source_cost_0p12pct grid re-measures every cell under the "
                       "source's own 0.12% two-leg round-trip cost level; the sign agreement "
                       "with the canonical track is a registered family-level reader"},
            {"id": "R8", "name": "cross-pair consistency",
             "method": "the six pairs' outcomes, medians and winner cells are reported side by "
                       "side (descriptive); pair concentration is a registered family-level "
                       "reader"},
            {"id": "R9", "name": "slippage stress",
             "method": "slippage_2ticks re-runs the full product at two adverse ticks per fill "
                       "per leg"},
            {"id": "R10", "name": "sub-period stability",
             "method": "per-calendar-year net PnL of each winner cell on the full window, "
                       "reported as a descriptive diagnostic"}
        ]
    },
    "gates": {
        "G1_coverage": "every one of the 11 registered phase grids must contain exactly 5760 "
                       "cases (6 pair cohorts x 20 strategy cases x 48 DCA configs), the measured "
                       "strategy/DCA cell sets must equal the registered products, and the case "
                       "evaluation total must equal 63,360; otherwise TECHNICAL_INCOMPLETE and no "
                       "cohort is judged",
        "G2_insufficient_trades": "per cohort: fewer than min_episodes_is (60) historical "
                                  "pair-cycles in the cohort's best case, or fewer than "
                                  "min_episodes_oos (20) pair-cycles in the elected winner's OOS "
                                  "slice, culls the cohort (insufficient_trades)",
        "G3_cohort_selector": "historical net_pnl > 0 AND sharpe > 0 AND pair-cycles >= "
                              "min_episodes_is, ranked by Sharpe desc, net_pnl desc, "
                              "registered-index lexical tie-break",
        "G4_cohort_oos": "OOS net_pnl > 0 AND sharpe > 0 AND annualized Sharpe >= "
                         "min_oos_sharpe (0.40) AND annualized return >= "
                         "min_oos_annualized_return (0.0)",
        "G5_cohort_full": "full-window net_pnl > 0 for the winner's cell",
        "G6_cohort_robustness": "the winner's cell has net_pnl > 0 in all four execution stress "
                                "reruns",
        "G7_cohort_neighbourhood": "at least 60% of legal face-adjacent neighbours agree in "
                                   "historical net-PnL sign",
        "G8_cohort_cost_attrition": "the winner's cell has net_pnl > 0 in the "
                                    "cost_attrition_40bps grid",
        "verdict_rule": "TECHNICAL_INCOMPLETE if G1 fails; else each cohort passing G3-G8 is a "
                        "SURVIVOR: 0 survivors -> REJECT, >=1 survivors -> PASS (band "
                        "SURVIVOR_FOUND / MULTIPLE_SURVIVORS describes the count only). The "
                        "four registered family-level readers then forbid PASS. No post-hoc "
                        "threshold edits, no tuning to pass.",
        "min_episodes_is": 60,
        "min_episodes_is_provenance": "the card registers a no-signal floor of 60 pair-cycles "
                                      "for the historical selection window and 20 for the OOS "
                                      "slice; both are adopted verbatim, not lowered, and the "
                                      "OOS arm is evaluated after selection",
        "min_episodes_oos": 20,
        "neighborhood_min_same_sign_fraction": 0.6,
        "min_oos_sharpe": 0.4,
        "min_oos_annualized_return": 0.0,
        "known_gate_set_limitation": "The registered gate set contains no benchmark-relative "
            "test and no buy-and-hold comparison; a pair spread can show positive PnL through "
            "the sample's funding regime or through the pair's common trend. This limitation is "
            "declared BEFORE the run and is NOT patched afterwards; the cross-pair distribution "
            "and the no_funding grids remain non-gating diagnostics."
    },
    "falsification": [
        "no signal / insufficient trades (G2: fewer than 60 historical pair-cycles in the "
        "cohort's best case, or fewer than 20 in the winner's OOS slice; culls the cohort)",
        "no qualifying historical candidate (G3)",
        "OOS economic rejection of the winner cell (G4: annualized Sharpe < 0.40 or annualized "
        "return < 0)",
        "full-window economic rejection of the winner cell (G5)",
        "robustness economic failure of the winner cell (G6: fee_2x, funding_2x, "
        "entry_delay_1_bar, slippage_2ticks)",
        "parameter-neighbourhood fragility of the winner cell (G7: < 60% historical sign "
        "agreement)",
        "cost attrition: the winner cell turns negative under 40 bps per fill (G8)",
        "source-consistency: if this round reproduces the source's negative net-return "
        "conclusion the family is recorded REJECT - reproducing a negative paper result is "
        "never a PASS (family-level, never PASS)",
        "cost-boundary: the canonical and source-reported cost tracks disagree in sign on an "
        "elected winner (family-level, never PASS)",
        "pair-concentration: the OOS positive contribution concentrates in a single pair above "
        "70% (family-level disclosure, never PASS)",
        "grid-instability: an elected winner's t_o/t_c neighbourhood disagrees in OOS sign on "
        "40% or more of its registered neighbours (family-level disclosure, never PASS)",
        "coverage incomplete in any registered phase grid (G1)",
        "DCA execution incomplete or not true order-fill accounting (see assertions.json; any "
        "false assertion = TECHNICAL_INCOMPLETE)",
        "the source-cost track is a no-op (if it produced identical measurements to the "
        "canonical track, the registered cost-boundary reader would be vacuous -> "
        "TECHNICAL_INCOMPLETE)"
    ],
    "performance_claimable_rule": "true only when the family verdict == PASS (>=1 cohort "
        "survivor) and contract 9.6 holds (canonical raw, no look-ahead, explicit OOS, declared "
        "costs, full section 7.2 coverage including the DCA parameter domain) and none of the "
        "four registered family-level falsification readers has fired. The number of survivors "
        "is never itself a reason to claim false.",
    "survivor_bundle": {
        "artifact": "rounds/<round_id>/survivor-bundle.json (round level, immutable, contract "
                    "10.8)",
        "produced_by": "runtime/survivor_bundle.py (host-side, pure stdlib, deterministic; run "
                       "once the attempt is terminally DONE)",
        "rule": "the bundle freezes EVERY survivor of the round, in the order the run recorded "
                "them (that order is not a ranking). It is only produced from a "
                "coverage_complete=true attempt whose assertions are all true, and it is never "
                "rewritten once written."
    },
    "expected": {
        "cohorts": 6,
        "strategy_cases_per_cohort": 20,
        "dca_configs_per_cohort": 48,
        "base_combinations_per_cohort": 960,
        "cohort_grid_kinds": GRIDS,
        "phase_grid_count": 11,
        "case_evaluations_per_cohort_per_grid": 960,
        "case_evaluations_per_cohort_all_grids": 10560,
        "case_evaluations_per_grid": 5760,
        "expected_case_evaluations": 63360,
        "arithmetic": "6 pair cohorts (the six pairs of BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT at 1h) x "
                      "20 registered strategy cases x 48 DCA configs = 960 base combinations per "
                      "phase grid; x 11 phase grids = 63,360 full case evaluations. A case "
                      "evaluation = one engine simulation of one joint parameter cell of one "
                      "pair cohort on one phase grid.",
        "runtime_expectation": {
            "basis": "Strategy A v2 measured 103,680 case evaluations in 1,302 s in qlib-run; "
                     "this family evaluates 63,360 cells whose episodes hold only inside the "
                     "registered 336-bar trading windows, plus a per-cycle copula fit with a "
                     "B=100 parametric-bootstrap GOF per qualifying cycle (6 cohorts x up to 116 "
                     "cycles).",
            "estimate": "a detached run of roughly one hour (bins rebuild + cycle precompute + "
                        "63,360 cell simulations) - an estimate from the synthetic benchmark in "
                        "this card, not a guarantee; progress is read from "
                        "artifacts/progress.json, the per-pair log lines and state.json.",
            "status": "estimate only; the run is launched detached inside the container and "
                      "monitored from the attempt state/log, never by holding an agent turn open"
        }
    },
    "non_goals": [
        "no parameter search beyond the registered strategy case set and the registered DCA "
        "domain",
        "no OOS-based selection of any parameter",
        "no re-fit of the copula inside a trading window (roll only at cycle boundaries)",
        "no signal-level stop-loss / take profit beyond the registered exit family",
        "no intra-cycle re-entry after a flat (one episode per cycle, registered)",
        "no Paper or Live trading",
        "no second backtester or engine",
        "no new Manager/Service/Factory/Registry/Orchestrator/daemon/queue",
        "no legacy Nautilus runtime, gate or performance truth",
        "no narrowing or widening of the registered pair universe after seeing results",
        "no rewriting of Strategy A/B/C/D artifacts (immutable)",
        "no withdrawal of the disclosed negative evidence of the source"
    ],
    "parameter_contract": {
        "parameter_contract_version": 1,
        "family_id": FAMILY,
        "contract_ref": "v1.8 generic family parameter contract (runtime/parameter_contract.py); "
                        "generated from this document's registered parameter_domain / dca_domain",
        "research_axes_ordered": [
            {"name": "strategy_case", "kind": "composite",
             "members": ["t_o", "t_c", "copula_family"],
             "registered_values": CASES,
             "row_fields": ["t_o", "t_c", "copula_family"]},
            {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
             "registered_values": SPACING, "row_fields": ["spacing_pct"]},
            {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
             "registered_values": MULT, "row_fields": ["size_multiplier"]},
            {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
             "registered_values": TP, "row_fields": ["breakeven_tp_pct"]},
            {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
             "registered_values": INV, "row_fields": ["invalidation_pct"]}
        ],
        "row_fields": ["t_o", "t_c", "copula_family", "spacing_pct", "size_multiplier",
                       "breakeven_tp_pct", "invalidation_pct"],
        "composite_map": {"strategy_case": ["t_o", "t_c", "copula_family"]},
        "strategy_param_fields": ["t_o", "t_c", "copula_family"],
        "dca_param_fields": ["spacing_pct", "size_multiplier", "breakeven_tp_pct",
                             "invalidation_pct"],
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"], "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {
            "keys": ["symbol", "timeframe", "t_o", "t_c", "copula_family", "spacing_pct",
                     "size_multiplier", "breakeven_tp_pct", "invalidation_pct"],
            "equality": "exact, numeric == float compare, rest bytewise"
        },
        "non_params": ["symbol", "timeframe", "leg1_symbol", "leg2_symbol", "window_kind",
                       "strategy_case", "case_name", "base_quote", "cycles_seen",
                       "cycles_tradable", "cycles_entered", "diagnostics", "metrics"],
        "domain_cardinality": {"strategy": 20, "dca": 48, "per_cohort": 960}
    }
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as fh:
    fh.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
print("wrote", OUT, os.path.getsize(OUT), "bytes")
