#!/usr/bin/env python3
"""Author (exactly once) the prerequisite-gate artifacts for family

    continuous-macro-timing-growth-defensive-style-allocation-2026-09-02

Card t_20bec925. Writes, with O_EXCL:

  /results/<family>/rounds/<family>-r1/round-spec.json   (prerequisite-gate pre-registration)
  /results/<family>/rounds/<family>-r1/verdict.json      (round verdict, contract 10.7)

Order of operations (a defect in any step aborts BEFORE any write):

  1. measure the canonical raw through the checker's own `measure_raw()`;
  2. reuse the checker's own house scan (or measure it once) ;
  3. read the frozen card body from the board DB and the canonical record;
  4. extract every verbatim excerpt by explicit anchors, ASSERT the expected
     item count of every extracted list and print each item's head;
  5. assert every excerpt is a substring of its declared source;
  6. build both documents and write them O_EXCL (a second run is refused).

Read-only against the raw store and the results tree (writes only its two new
files, with O_EXCL).
"""
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
RESULTS = "/Volumes/ExpansionDrive/qlib-results"
RAW = "/Volumes/ExpansionDrive/market-data-raw"
FAMILY = "continuous-macro-timing-growth-defensive-style-allocation-2026-09-02"
ROUND = FAMILY + "-r1"
TASK = "t_20bec925"
BOARD = "quant-strategy-research"
BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards", BOARD,
                        "kanban.db")
RECORD = "/Users/hong/.hermes/wiki/quant/%s.md" % FAMILY
CHECKER = os.path.join(REPO, "runtime",
                       "continuous_macro_timing_growth_defensive_style_allocation"
                       "_prerequisite_check.py")


def load_checker():
    spec = importlib.util.spec_from_file_location("cmt_checker", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_text(t):
    return "sha256:" + hashlib.sha256(t.encode("utf-8")).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def card_body():
    con = sqlite3.connect("file:%s?mode=ro" % BOARD_DB, uri=True)
    try:
        row = con.execute("select body from tasks where id=?", (TASK,)).fetchone()
    finally:
        con.close()
    return row[0]


def between(text, start, end, occurrence=1):
    """Slice from `start` (INCLUDED) to the `occurrence`-th `end` after it."""
    i = -1
    for _ in range(occurrence):
        i = text.find(start, i + 1)
        if i < 0:
            raise SystemExit("ANCHOR MISS (start): %r" % start[:60])
    j = text.find(end, i + len(start))
    if j < 0:
        raise SystemExit("ANCHOR MISS (end): %r" % end[:60])
    return text[i:j]


def numbered_items(block):
    out = []
    for line in block.splitlines():
        s = line.strip()
        i = 0
        while i < len(s) and s[i].isdigit():
            i += 1
        if i and s[i:i + 2] == ". " and s[i + 2:i + 4] == "**":
            out.append(s)
    return out


def bullets(block, drop_label=True):
    """Bullet items of a block, dropping the leading label line if it is one."""
    out = [line for line in block.splitlines() if line.startswith("- ")]
    if drop_label and out:
        out = out[1:]
    return out


def main():
    ck = load_checker()
    raw = ck.measure_raw(RAW)
    host_path = os.path.join(REPO, ".kanban-scratch/cmt_parts/host_scan.json")
    if os.path.isfile(host_path):
        host = ck._load_json(host_path)
    else:
        host = ck.measure_host_stores()
    body = card_body()
    record = open(RECORD, encoding="utf-8").read()
    contract = open(os.path.join(REPO, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md"),
                    encoding="utf-8").read()
    sys.path.insert(0, os.path.join(REPO, "runtime"))
    import production_handoff
    footer = production_handoff.LIFECYCLE_FOOTER

    fam_path = os.path.join(RESULTS, FAMILY, "family.json")
    fam = ck._load_json(fam_path)

    # ---------------------------------------------------------------- excerpts
    V = {}
    src = {}

    def add(key, value, source):
        V[key] = value
        src[key] = source

    add("card_source_of_truth_verbatim",
        between(body, "SOURCE OF TRUTH", "\nSCIENTIFIC HYPOTHESIS"), "card")
    add("card_family_id_verbatim",
        between(body, "- family_id 固定：", "\n- provenance（"), "card")
    add("card_provenance_verbatim",
        between(body, "- provenance（已 review 的 canonical 來源）：",
                "\n- 本機 canonical raw"), "card")
    add("card_raw_verbatim",
        between(body, "- 本機 canonical raw（read-only）：", "\n- prerequisite 條款"), "card")
    add("card_prerequisite_clause_verbatim",
        between(body, "- prerequisite 條款：", "\n\nSCIENTIFIC HYPOTHESIS"), "card")
    add("card_hypothesis_heading_verbatim",
        between(body, "SCIENTIFIC HYPOTHESIS / PRE-REGISTRATION", "\n- record title"), "card")
    add("card_record_title_verbatim",
        between(body, "- record title：", "\n- economic mechanism"), "card")
    add("card_mechanism_excerpt_verbatim",
        between(body, "- economic mechanism（canonical record 原文節錄）：",
                "\n- signal 語意與參數"), "card")
    add("card_signal_excerpt_verbatim",
        between(body, "- signal 語意與參數（canonical record 原文節錄；",
                "\n- 核心 hypothesis 以本節錄"), "card")
    add("card_registration_rule_verbatim",
        between(body, "- 核心 hypothesis 以本節錄為唯一註冊版本；", "\n- 交易成本"), "card")
    add("card_transaction_cost_verbatim",
        between(body, "- 交易成本：", "\n\nELIGIBLE UNIVERSE"), "card")
    add("card_universe_heading_verbatim",
        between(body, "ELIGIBLE UNIVERSE（不得沿用前一張卡的縮減結果",
                "\n- record required data"), "card")
    add("card_required_data_excerpt_verbatim",
        between(body, "- record required data（canonical 原文節錄）：",
                "\n- crypto portability"), "card")
    add("card_portability_excerpt_verbatim",
        between(body, "- crypto portability（canonical 原文節錄）：",
                "\n\n_（節錄；完整內容見 canonical record）_"), "card")
    add("card_registration_note_verbatim",
        between(body, "- 本卡註冊：record 自身提供 Crypto portability 段落 →",
                "\n\nDATA WINDOW"), "card")
    add("card_window_verbatim",
        between(body, "DATA WINDOW / SPLIT（首跑前寫死", "\n\nDCA PARAMETER DOMAIN"), "card")
    add("card_dca_verbatim",
        between(body, "DCA PARAMETER DOMAIN（§7.2 第 4 項", "\n\nCOHORT SURVIVOR SEMANTICS"),
        "card")
    add("card_cohort_semantics_verbatim",
        between(body, "COHORT SURVIVOR SEMANTICS（§7.3；", "\n\nROBUSTNESS 與 FALSIFICATION"),
        "card")
    add("card_robustness_verbatim",
        between(body, "ROBUSTNESS 與 FALSIFICATION（首跑前寫死", "\n\nFAILURE TAXONOMY"), "card")
    add("card_falsification_excerpt_verbatim",
        between(body, "- record falsification plan（canonical 原文節錄；不降低門檻、不刪項）：",
                "\n\n_（節錄；完整內容見 canonical record）_"), "card")
    add("card_limitations_excerpt_verbatim",
        between(body, "- record limitations／caveats（canonical 原文節錄）：",
                "\n- 標準 stress grids"), "card")
    add("card_failure_taxonomy_verbatim",
        between(body, "FAILURE TAXONOMY — 科學失敗與基建／operator 失敗必須分開（§13）",
                "\n\nSURVIVOR BUNDLE"), "card")
    add("card_survivor_bundle_verbatim",
        between(body, "SURVIVOR BUNDLE 與 POST-SURVIVOR EVIDENCE PRESERVATION（§10.8/§28）",
                "\n\nIMPLEMENTATION RULES"), "card")
    add("card_implementation_rules_verbatim",
        between(body, "IMPLEMENTATION RULES", "\n\nGIT / OPS"), "card")
    add("card_git_ops_verbatim",
        between(body, "GIT / OPS", "\n\n\n---\nLIFECYCLE FOOTER"), "card")
    footer_start = body.find("---\nLIFECYCLE FOOTER（system-owned；")
    if footer_start < 0:
        raise SystemExit("ANCHOR MISS: lifecycle footer")
    add("card_lifecycle_footer_verbatim", body[footer_start:], "card")

    add("record_title_verbatim",
        between(record, 'title: "Continuous Timing Signals', "\ncreated:"), "record")
    add("record_frontmatter_caveat_verbatim",
        between(record, "intake_decision: PASS-WITH-CAVEAT", "\nreviewed_commit"), "record")
    add("record_provenance_verbatim",
        between(record, "## Provenance", "\n## Economic mechanism"), "record")
    add("record_mechanism_verbatim",
        between(record, "## Economic mechanism", "\n## Signal"), "record")
    add("record_signal_verbatim",
        between(record, "## Signal", "\n## Required data"), "record")
    add("record_required_data_verbatim",
        between(record, "## Required data", "\n## Execution assumptions"), "record")
    add("record_execution_assumptions_verbatim",
        between(record, "## Execution assumptions", "\n## Evidence"), "record")
    add("record_evidence_verbatim",
        between(record, "## Evidence", "\n## Falsification plan"), "record")
    add("record_falsification_verbatim",
        between(record, "## Falsification plan", "\n## Crypto portability"), "record")
    add("record_portability_verbatim",
        between(record, "## Crypto portability", "\n## Limitations"), "record")
    add("record_limitations_verbatim",
        between(record, "## Limitations", "\n## Implementation status"), "record")
    add("record_implementation_status_verbatim",
        between(record, "## Implementation status", "\n## Adoption boundary"), "record")
    add("record_adoption_boundary_verbatim",
        between(record, "## Adoption boundary", "\n## Related Wiki records"), "record")
    add("record_sources_verbatim", between(record, "## Sources", "\n"), "record")

    add("contract_clause_verbatim",
        between(contract, "### 6.4 終結語意：done != PASS", "\n- `[C]` 禁止用 `done` 掩蓋失敗"),
        "contract")
    add("contract_prerequisite_missing_alignment_verbatim",
        between(contract, "- `[C]`（**operator correction，2026-09-18",
                "\n\n## 7. 卡間狀態機"), "contract")
    add("contract_verdict_semantics_verbatim",
        between(contract, "- `[C]` `TECHNICAL_INCOMPLETE` 必須附：", "\n- `[C]` 禁止用 `done` 掩蓋失敗"),
        "contract")
    add("contract_failure_layer_rule_verbatim",
        between(contract, "- `[C]` 判定層級的判準：",
                "\n- `[C]` **`operator_stopped` 的 archive hygiene"),
        "contract")
    add("contract_fullbacktest_definition_verbatim",
        between(contract, "- `[C]` **全量回測（v1.3.0）**：", "\n- `[C]` **DCA provenance classification"),
        "contract")
    add("contract_dca_provenance_verbatim",
        between(contract, "- `[C]` **DCA provenance classification（v1.3.1）**：",
                "\n- `[C]` **per-fill 成本會計"), "contract")
    add("contract_cohort_survivor_semantics_verbatim",
        between(contract, "- `[C]` **cohort SURVIVOR 最低要求（全部成立）**：",
                "\n- `[C]` cohort 淘汰原因必須逐項記錄"), "contract")
    add("contract_failure_taxonomy_verbatim",
        between(contract, "## 13. Failure taxonomy", "\n- `[C]` 判定層級的判準："), "contract")
    add("contract_verdict_json_schema_verbatim",
        between(contract, "### 10.7 `verdict.json`（round 判定，immutable）", "\n\n### 10.8"),
        "contract")
    add("contract_handoff_append_rules_verbatim",
        between(contract, "### 14.4 Automatic handoff trigger（v1.2.0）",
                "\n- `[C]` **v1.3.0 candidate card requirements**："), "contract")
    add("lifecycle_footer_verbatim", footer, "footer")

    # ---- assertions on every extracted list (count + head printed)
    card_fals = numbered_items(V["card_falsification_excerpt_verbatim"])
    rec_fals = numbered_items(V["record_falsification_verbatim"])
    assert len(card_fals) == 3, ("card falsification item count", len(card_fals))
    assert len(rec_fals) == 4, ("record falsification item count", len(rec_fals))
    assert "Subperiod Breakdown" in rec_fals[3], rec_fals[3][:80]
    assert "Cost Stress Boundary Test" in card_fals[1], card_fals[1][:80]
    card_req = bullets(V["card_required_data_excerpt_verbatim"])
    assert len(card_req) == 5, ("card required-data bullet count", len(card_req))
    card_port = bullets(V["card_portability_excerpt_verbatim"])
    assert len(card_port) == 4, ("card portability bullet count", len(card_port))
    rec_req = bullets(V["record_required_data_verbatim"], drop_label=False)
    assert len(rec_req) == 5, ("record required-data bullet count", len(rec_req))
    dca_items = bullets(V["card_dca_verbatim"], drop_label=False)
    assert len(dca_items) == 7, ("card DCA bullet count", len(dca_items))
    card_sig_sections = [l for l in V["card_signal_excerpt_verbatim"].splitlines()
                         if l.startswith("### ")]
    rec_sig_sections = [l for l in V["record_signal_verbatim"].splitlines()
                        if l.startswith("### ")]
    assert len(card_sig_sections) == 4, ("card signal section count", len(card_sig_sections))
    assert len(rec_sig_sections) == 8, ("record signal section count", len(rec_sig_sections))
    surv = [l for l in V["card_cohort_semantics_verbatim"].splitlines()
            if l.startswith("  ") and len(l) > 3 and l.strip()[1:2] == ")"]
    assert len(surv) == 5, ("survivor requirement count", len(surv))
    print("card falsification items (%d):" % len(card_fals))
    for it in card_fals:
        print("   ", it[:72])
    print("record falsification items (%d):" % len(rec_fals))
    for it in rec_fals:
        print("   ", it[:72])
    print("card required-data (%d):" % len(card_req), [x[:40] for x in card_req])
    print("card portability (%d):" % len(card_port), [x[:40] for x in card_port])
    print("record required-data (%d):" % len(rec_req), [x[:40] for x in rec_req])
    print("card signal sections (%d):" % len(card_sig_sections), card_sig_sections)
    print("record signal sections (%d):" % len(rec_sig_sections))
    print("survivor requirements (%d):" % len(surv), [x.strip()[:28] for x in surv])
    print("verbatim leaves: %d" % len(V))

    # ---- substring assertion against the DECLARED source, before any write
    sources = {"card": body, "record": record, "contract": contract, "footer": footer}
    for key, value in V.items():
        s = sources[src[key]]
        if value not in s:
            raise SystemExit("EXCERPT NOT IN SOURCE: %s (%s)" % (key, src[key]))
    print("all %d excerpts are substrings of their declared source" % len(V))

    inst = raw["instrument_surface"]
    ks = raw["kline_surface"]
    fund = raw["funding_surface"]
    win = raw["kline_1d_windows"]
    inst_doc = ck._load_json(os.path.join(RAW, "binance/usdm/instruments/usdm-perp-instruments.json"))
    local_instruments = {e["fields"]["raw_symbol"]: {
        "taker_fee": float(e["fields"]["taker_fee"]),
        "maker_fee": float(e["fields"]["maker_fee"]),
        "price_increment": float(e["fields"]["price_increment"])}
        for e in inst_doc["instruments"]}
    groups = ck.DECISIVE_GROUPS
    decisive_vocab = ["ABSENT", "ABSENT_AS_REGISTERED", "NOT_CONSTRUCTIBLE",
                      "BLOCKED_BY_ABSENCE", "NOT_EXECUTED_BLOCKED"]

    def probe_zero(group):
        return raw["decisive_group_unexplained"][group] == 0

    def probe_declared(group):
        return raw["decisive_group_declared_hits"][group]

    # matrix: one row per registered requirement / per registered signal object
    M = []

    def m(item, status, basis):
        M.append({"item": item, "status": status, "basis": basis})

    R = "record §Required data / §Signal / §Falsification plan / §Crypto portability"
    for venue in ("NYSE", "NASDAQ", "CBOE"):
        m("venue: US equity and index markets (%s)" % venue, "ABSENT",
          "measured against the canonical raw and the host scan (one venue, BINANCE "
          "USD-M perpetuals; no US-equity market anywhere) | %s" % R)
    m("instrument: growth/technology ETFs QQQ, XLK, VGT, SPYG, VUG (US equities)",
      "ABSENT", "zero instrument and zero payload hits | %s" % R)
    m("instrument: defensive income/value ETFs SCHD, VYM, VTV, FDVV, COWZ (US equities)",
      "ABSENT", "zero instrument and zero payload hits | %s" % R)
    m("instrument: benchmark SPY", "ABSENT", "zero hits | %s" % R)
    m("series: CBOE VIX index - the volatility-stress input vh_t = z(VIXPercentile_756)",
      "ABSENT", "zero hits for vix / volatility index | %s" % R)
    m("series: Deribit Bitcoin Implied Volatility Index (DVOL) - the record's OWN crypto "
      "replacement for VIX", "ABSENT",
      "the DECISIVE row: zero hits for dvol / deribit / implied vol / iv surface, and no "
      "options chain exists that could produce one | %s" % R)
    m("series: 10-year US Treasury yield (TNX) - the rate-relief input r_t",
      "ABSENT", "zero hits for tnx / treasury / 10-year yield (the crypto funding-rate "
      "replacement is registered separately) | %s" % R)
    m("series: Moody's Seasoned Baa corporate bond yield (FRED: BAA10Y) - the optional "
      "credit overlay", "ABSENT", "zero hits for baa / moody / credit spread | %s" % R)
    m("field: adjusted ETF closing prices and daily returns of the registered instruments",
      "ABSENT_AS_REGISTERED", "the store's price fields are perpetual k-line OHLCV strings "
      "for four crypto contracts, not adjusted ETF closes | %s" % R)
    m("timeframe: daily close-to-close bars for the registered instruments",
      "ABSENT_AS_REGISTERED", "measured 1d k-lines exist for the four perpetuals only | %s" % R)
    m("portability basket: growth basket tokens SOL, AVAX, NEAR, SUI",
      "ABSENT_AS_REGISTERED", "one of the four tokens (SOLUSDT) is present in the canonical "
      "raw; AVAX appears only as one column of a non-canonical derived daily panel "
      "(used=false, disclosed in other_local_stores); NEAR / SUI have zero hits | %s" % R)
    m("portability basket: defensive basket Bitcoin + USD stablecoins as tradable instruments",
      "NOT_CONSTRUCTIBLE", "BTCUSDT is present but no stablecoin instrument exists "
      "(measured instrument ids: %s) | %s" % (", ".join(inst["instrument_ids"]), R))
    m("signal input: vh_t = z(VIXPercentile_756) - high-VIX percentile",
      "NOT_CONSTRUCTIBLE", "needs a volatility index; none exists in the raw or on the host | %s" % R)
    m("signal input: vr_t = -z(dVIX_21) - VIX relief", "NOT_CONSTRUCTIBLE",
      "needs the same series as vh_t | %s" % R)
    for comp in ("HighVIX_t = softplus(vh_t)", "VIXRelief_t = softplus(vr_t)",
                 "LowVIX_t = softplus(-vh_t)"):
        m("smooth component: %s" % comp, "NOT_CONSTRUCTIBLE",
          "the softplus argument is a VIX-family input | %s" % R)
    for term in ("i1_t = r_t * vh_t", "i2_t = HighVIX_t * VIXRelief_t",
                 "i3_t = GrowthExt_t * LowVIX_t",
                 "i4_t = GrowthExt_t * LowVIX_t * RateQuiet_t"):
        m("interaction term: %s" % term, "NOT_CONSTRUCTIBLE",
          "three of the four registered interaction terms carry a VIX-family factor | %s" % R)
    m("score: StressScore_t = 0.5 z(i1) + 0.5 z(i2)", "NOT_CONSTRUCTIBLE",
      "both of its terms are unbuildable | %s" % R)
    m("score: CrowdedScore_t = 0.5 z(i3) + 0.5 z(i4)", "NOT_CONSTRUCTIBLE",
      "LowVIX_t is unbuildable | %s" % R)
    m("score: RawScore_t and its expanding Z-score", "BLOCKED_BY_ABSENCE",
      "the composite cannot be formed without the interaction terms | %s" % R)
    m("execution: point-in-time score formation at day t close, next-bar execution",
      "NOT_EXECUTED_BLOCKED", "no score exists to execute | %s" % R)
    m("falsification 1: Stationary Random Placebo Test on TNX / VIX / SPYDrawdown",
      "NOT_EXECUTED_BLOCKED", "the test perturbs the actual series; VIX is absent | %s" % R)
    m("falsification 2: Cost Stress Boundary Test (10 -> 25 / 40 bps)",
      "NOT_EXECUTED_BLOCKED", "no backtest exists to stress | %s" % R)
    m("falsification 3: Parameter Perturbation Grid (alpha, lambda_s, tau_w, eta)",
      "NOT_EXECUTED_BLOCKED", "no backtest exists to perturb | %s" % R)
    m("falsification 4: Subperiod Breakdown (2022 rate-hike regime, max DD < -25%)",
      "NOT_EXECUTED_BLOCKED", "restored from the canonical record; blocked by the same "
      "absence | %s" % R)
    m("extension: incremental Baa credit overlay (lambda_credit, lambda_r x cs)",
      "NOT_EXECUTED_BLOCKED", "the optional overlay needs the credit series | %s" % R)
    # non-decisive rows (explicitly NOT in the decisive map)
    m("portability proxy: annualized crypto perpetual funding rate (TNX replacement)",
      "PRESENT_VIA_PORTABILITY_PROXY",
      "binance/usdm/funding carries %s observations over four contracts"
      % sum(v["observations"] for v in fund.values()))
    m("portability proxy: BTC drawdown from all-time highs (SPY-drawdown replacement)",
      "PRESENT_VIA_PORTABILITY_PROXY", "BTCUSDT 1d k-lines: %d bars %s -> %s"
      % (win["BTCUSDT"]["bars"], win["BTCUSDT"]["first_bar_open_date"],
         win["BTCUSDT"]["last_bar_open_date"]))
    m("local universe: four USD-M perpetuals on the registered daily grid",
      "PRESENT", "%s; %d 1d bars each" % (", ".join(inst["instrument_ids"]),
                                          win["BTCUSDT"]["bars"]))
    decisive_map = {i["item"]: i["status"] for i in M if i["status"] in decisive_vocab}
    status_counts = {}
    for i in M:
        status_counts[i["status"]] = status_counts.get(i["status"], 0) + 1
    print("matrix rows: %d (decisive %d)" % (len(M), len(decisive_map)))
    print("status counts:", json.dumps(status_counts, ensure_ascii=False))

    measured_available = {
        "venue": raw["config"].get("venue"),
        "market_type": raw["config"].get("market_type"),
        "dataset_families": raw["dataset_families"],
        "instrument_ids": inst["instrument_ids"],
        "kline_intervals": ks["intervals"],
        "kline_files_probed": raw["kline_files_probed"],
        "finest_resolved_interval": ks["finest_resolved_interval"],
        "kline_1d_bars": {s: v["bars"] for s, v in win.items()},
        "funding_observations": {s: v["observations"] for s, v in fund.items()},
        "decisive_group_unexplained": raw["decisive_group_unexplained"],
        "decisive_probe_hits_unexplained_total": raw["decisive_probe_hits_unexplained_total"],
        "present_token_positive_control": raw["present_token_positive_control"],
    }

    measured_absence = [
        "no implied-volatility series anywhere in the raw tree: %d unexplained hits for "
        "vix / dvol / implied vol / volatility index / deribit / iv surface over %d probe "
        "tokens x %d payload files (%d declared occurrences in this group)"
        % (probe_zero("volatility_index_series"), raw["token_count"],
           raw["payload_files_probed"], probe_declared("volatility_index_series")),
        "no options-market-data surface anywhere in the raw tree: %d unexplained hits for "
        "option / strike / expiry / open interest / greeks / put-call; the %d declared "
        "occurrences are audited single-line prose (see declared_allowances)"
        % (probe_zero("options_market_data"), probe_declared("options_market_data")),
        "no US-equity venue anywhere: %d unexplained hits for nyse / nasdaq / cboe / "
        "us equity" % probe_zero("us_equity_venue"),
        "no record instrument or macro series anywhere: %d unexplained hits for "
        "qqq / xlk / vgt / spyg / vug / schd / vym / vtv / fdvv / cowz / spy / tnx / "
        "treasury / 10-year yield / baa / moody / credit spread"
        % (probe_zero("record_instrument_set") + probe_zero("record_macro_series")),
        "no portability alternate or basket token anywhere: %d unexplained hits for "
        "aave / lending rate / lending apy / supply apy / avax / near protocol / sui / "
        "stablecoin / usdc" % (probe_zero("portability_alternate_provider")
                               + probe_zero("portability_growth_basket_tokens")
                               + probe_zero("portability_defensive_store")),
        "every raw row shape measured: %d k-line files share ONE 7-key row shape (%s); "
        "funding rows carry 9 keys; the instrument definitions carry %d fields with NO "
        "option / implied-volatility / maturity field"
        % (raw["kline_files_probed"], list(raw["kline_row_key_sets"])[0],
           inst["field_count"]),
        "the four local instruments are all CryptoPerpetual (is_inverse=false, "
        "USDT-settled): no maturity, no strike, no implied volatility, no second venue",
        "one venue and three dataset families only: %s" % raw["dataset_families"],
        "finest resolved interval = %s (%ds): the store carries bars, not options or "
        "index series" % (ks["finest_resolved_interval"], ks["finest_step_seconds"]),
        "the store's own schema documents its coverage limit: %r"
        % ck.COVERAGE_LIMIT_SENTENCE,
        "host scan: %d files over %d declared roots; %d series-shaped decisive-token hits "
        "and %d prose hits, every one classified by a declared rule (%s); %d measured "
        "substitutes enumerated with used=false. The two positive disclosures are measured "
        "and dispositive: (a) the ONLY options/implied-volatility material on this host is a "
        "single-instant Deribit BTC snapshot (1,012 instruments, 1,012 book summaries with "
        "mark_iv, 13 expiries, 94 strikes, instant 2026-08-28T12:19:42Z) - no history and "
        "not inside the canonical raw, so it cannot express a 756-day percentile or a "
        "21-day change; (b) a 12-symbol DERIVED daily crypto panel exists (phase7, includes "
        "AVAXUSDT) but carries no implied-volatility series and is not the registered price "
        "series"
        % (host["files_scanned"], sum(1 for r in host["roots"] if r.get("exists")),
           len(host["series_hits"]), len(host["prose_hits"]),
           json.dumps(host["series_hit_classes"], ensure_ascii=False),
           len(host["measured_substitutes"])),
    ]

    now = "2026-09-18T07:35:00Z"
    spec = {
        "schema_version": 1,
        "document_kind": ("prerequisite-gate pre-registration (contract 6.4 / 10.7 terminal "
                          "basis; full-backtest NOT launched)"),
        "family_id": FAMILY,
        "family_title": ("Continuous Timing Signals for Growth-Defensive Style Allocation: "
                         "Macro Conditioning, Risk Matching, and Walk-Forward Evidence"),
        "round_id": ROUND,
        "kanban_task_id": TASK,
        "kanban_board": BOARD,
        "created_at_utc": now,
        "authored_by": "Hermes default (card %s, contract 14.4 production candidate)" % TASK,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md",
        "contract_sha256": sha256_file(os.path.join(
            REPO, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")),
        "contract_version": "v1.9.0 (2026-09-15)",
        "contract_version_source": ("contract header (版本：v1.9.0) + the sections quoted "
                                    "verbatim below"),
        "supersedes_round": None,
        "provenance": {
            "family_json": fam_path,
            "family_json_sha256": sha256_file(fam_path),
            "semantic_fingerprint": fam["semantic_fingerprint"],
            "canonical_record": RECORD,
            "canonical_record_sha256": sha256_file(RECORD),
            "record_status": ("research-only / not-implemented / not-approved / "
                              "approval_scope research-only"),
            "intake_decision": "PASS-WITH-CAVEAT",
            "intake_caveat": ("Accepted at Research Intake as source-faithful, research-only "
                              "knowledge. Source-reported results have not been "
                              "independently reproduced in this workflow; any "
                              "research-proposed operationalization, thresholds, execution "
                              "assumptions, or portability claims remain unvalidated and "
                              "must not be treated as source-reported or adopted."),
            "primary_source": ("Zheli Xiong, 'Continuous Timing Signals for Growth-Defensive "
                               "Style Allocation: Factor Attribution, Risk Matching, "
                               "Out-of-Sample Evidence, and a Bond/Credit Incremental "
                               "Extension', arXiv:2605.20636v2 [q-fin.PM], May 29, 2026. "
                               "DOI: 10.48550/arXiv.2605.20636."),
            "card_body_sha256": sha256_text(body),
            "card_body_source": BOARD_DB,
            "lifecycle_rule": ("system-owned lifecycle footer (contract 14.4): the eligible "
                               "universe is the canonical local raw's complete available set "
                               "on which the core signal can legitimately be computed; a "
                               "prerequisite-missing terminal is reserved for a core-signal "
                               "data type/field that exists nowhere locally"),
        },
        "hypothesis": {
            "record_title": ("Continuous Timing Signals for Growth-Defensive Style "
                             "Allocation: Macro Conditioning, Risk Matching, and "
                             "Walk-Forward Evidence"),
            "card_hypothesis_heading_verbatim": V["card_hypothesis_heading_verbatim"],
            "card_record_title_verbatim": V["card_record_title_verbatim"],
            "card_mechanism_excerpt_verbatim": V["card_mechanism_excerpt_verbatim"],
            "record_mechanism_verbatim": V["record_mechanism_verbatim"],
            "record_research_interpretation_verbatim": between(
                record, "### Research interpretation", "\n## Signal"),
            "card_registration_rule_verbatim": V["card_registration_rule_verbatim"],
            "card_transaction_cost_verbatim": V["card_transaction_cost_verbatim"],
            "hypothesis_status": ("registered verbatim; NOT computed - the registered signal "
                                  "cannot be formed from any locally available data family"),
        },
        "signal_semantics": {
            "card_signal_excerpt_verbatim": V["card_signal_excerpt_verbatim"],
            "record_signal_verbatim": V["record_signal_verbatim"],
            "record_portability_verbatim": V["record_portability_verbatim"],
            "card_portability_excerpt_verbatim": V["card_portability_excerpt_verbatim"],
            "registered_inputs": [
                {"input": "vh_t", "formula": "z(VIXPercentile_756_t)",
                 "local_analogue": "none - the record's own crypto mapping prescribes "
                                   "Deribit DVOL for this slot",
                 "status": "NOT_CONSTRUCTIBLE"},
                {"input": "vr_t", "formula": "-z(dVIX_21_t)",
                 "local_analogue": "none - same series as vh_t", "status": "NOT_CONSTRUCTIBLE"},
                {"input": "r_t", "formula": "-z(dTNX_21_t)",
                 "local_analogue": "annualized crypto perpetual funding rate (prescribed by "
                                   "the record's portability paragraph)",
                 "status": "PRESENT_VIA_PORTABILITY_PROXY"},
                {"input": "d_t", "formula": "-z(SPYDrawdown_t)",
                 "local_analogue": "BTC drawdown from all-time highs (prescribed)",
                 "status": "PRESENT_VIA_PORTABILITY_PROXY"},
                {"input": "g126_t", "formula": "z(GDTrailing126_t)",
                 "local_analogue": "requires the registered growth and defensive baskets; "
                                   "3 of the 4 named growth tokens and every stablecoin "
                                   "instrument are absent",
                 "status": "ABSENT_AS_REGISTERED"},
            ],
            "decisive_inputs_unavailable": ["vh_t", "vr_t"],
            "score_terms_blocked": ["HighVIX_t", "VIXRelief_t", "LowVIX_t",
                                    "i1_t", "i2_t", "i3_t", "i4_t",
                                    "StressScore_t", "CrowdedScore_t", "RawScore_t"],
            "direction_disclosure": ("the record's allocation is a long-only weight pair "
                                     "(w_G + w_D = 1, both >= 0) with no leverage, margin or "
                                     "shorting; no direction leg is added here"),
            "new_legs_added": False,
            "signal_status": ("not_computable: 2 of the 5 registered direction-normalized "
                              "inputs and 3 of the 4 registered interaction terms need an "
                              "implied-volatility series that exists nowhere in the "
                              "canonical raw or on the host"),
        },
        "registered_requirement": {
            "record_required_data_verbatim": V["record_required_data_verbatim"],
            "card_required_data_excerpt_verbatim": V["card_required_data_excerpt_verbatim"],
            "record_portability_verbatim": V["record_portability_verbatim"],
            "card_portability_excerpt_verbatim": V["card_portability_excerpt_verbatim"],
            "card_registration_note_verbatim": V["card_registration_note_verbatim"],
            "card_prerequisite_clause_verbatim": V["card_prerequisite_clause_verbatim"],
            "card_raw_verbatim": V["card_raw_verbatim"],
            "portability_class": "adapted / unproven (the record's own words)",
            "registration_note": ("the card registers the record's required data and the "
                                  "record's own crypto portability paragraph as the "
                                  "canonicalized adaptation; no suitability screening was "
                                  "added after intake"),
        },
        "universe_registration": {
            "rule": ("the record's required data/instruments are registered whole; when a "
                     "core-signal data family is absent for every possible local universe "
                     "the round terminalises as TECHNICAL_INCOMPLETE and the universe is "
                     "NOT shrunk"),
            "declared_verdict_alternatives": ["TECHNICAL_INCOMPLETE", "DEFERRED"],
            "registered_instruments": [
                "growth ETFs QQQ, XLK, VGT, SPYG, VUG (US equities)",
                "defensive ETFs SCHD, VYM, VTV, FDVV, COWZ (US equities)",
                "benchmark and conditioning assets: SPY, CBOE VIX index, 10-year US "
                "Treasury yield (TNX), Moody's Baa yield (FRED: BAA10Y)",
                "crypto portability mapping: growth basket SOL, AVAX, NEAR, SUI; "
                "defensive basket BTC + USD stablecoins; funding rate for TNX; Deribit "
                "DVOL for VIX; BTC drawdown for SPY drawdown",
            ],
            "registered_timeframes": ["daily close-to-close bars (the record's only timeframe)"],
            "registered_data_needed": card_req + card_port,
            "local_instruments_present": inst["instrument_ids"],
            "local_market_type": raw["config"].get("market_type"),
            "local_dataset_families": raw["dataset_families"],
            "local_finest_resolved_interval": ks["finest_resolved_interval"],
            "required_data_available_local": False,
            "universe_shrunk_to_local_list": False,
            "shrinking_disclosure": ("the four local perpetuals and their seven intervals were "
                                     "NOT registered as a substitute universe: the "
                                     "registered score needs a volatility-index series, which "
                                     "is not a function of the traded universe, so a local "
                                     "re-run would replace the estimand (a 3-input variant of "
                                     "the signal) rather than approximate it"),
            "required_data_matrix": M,
            "required_data_matrix_status_counts": dict(sorted(status_counts.items())),
            "decisive_matrix_item_count": len(decisive_map),
            "decisive_status_vocabulary": decisive_vocab,
            "decisive_status_map": dict(sorted(decisive_map.items())),
        },
        "data": {
            "record_sample_window": "2017-06-28 -> 2026-05-15 (the record's empirical window)",
            "record_sample_window_source": "record §Provenance (Research Setting)",
            "available_kline_window": {s: {"first": v["first_bar_open_utc"],
                                           "last": v["last_bar_open_utc"]}
                                       for s, v in win.items()},
            "available_funding_window": {s: {"first": v["first_utc"], "last": v["last_utc"]}
                                         for s, v in fund.items()},
            "overlap_note": ("the windows overlap but the window is NOT the decisive "
                             "absence: the decisive absence is a missing data FAMILY "
                             "(implied volatility / US-equity prices), which no window "
                             "change can supply"),
            "split_registration": ("in-sample 2022-01-01 -> 2025-09-30 / OOS 2025-10-01 -> "
                                   "2026-09-11 (the canonical local split) - registered for "
                                   "completeness only; no cell was computed"),
            "bar_labelling": {
                "raw_field": "open_time_ms",
                "status": "MEASURED at pre-registration on the canonical raw store",
                "consequence": ("every 1d grid is exactly contiguous over its window "
                                "(off_grid_steps=0, duplicate_opens=0)"),
            },
        },
        "dca_domain": {
            "configs_per_cohort_per_grid": 48,
            "axes": {"spacing_pct": [0.01, 0.02, 0.03, 0.04],
                     "size_multiplier": [1.0, 1.1],
                     "breakeven_tp_pct": [0.01, 0.02, 0.03],
                     "invalidation_pct": [0.05, 0.10]},
            "axes_status": {k: "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                            for k in ("spacing_pct", "size_multiplier",
                                      "breakeven_tp_pct", "invalidation_pct")},
            "search_axes_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
            "base_quote": 1000.0,
            "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
            "provenance_class": {
                "searched_axes": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
                "base_quote": "PROJECT_PRE_REGISTERED_CONSTANT",
                "note": ("the searched axes and the project constant are never described as "
                         "user-fixed invariants; the classification matches the card's"),
            },
            "card_dca_verbatim": V["card_dca_verbatim"],
            "dca_status": ("registered_not_executed: the whole 48-cell product stays "
                           "pre-registered and zero cells were computed (no attempt was "
                           "launched)"),
        },
        "user_fixed_invariants": {
            "status": "INHERITED from the A/B rail registration, verbatim; nothing executed",
            "items": dca_items,
            "card_dca_verbatim": V["card_dca_verbatim"],
        },
        "selector_and_disposition": {
            "disposition_unit": "cohort (instrument x timeframe)",
            "selector": "cohort-selector-v1",
            "disposition": "cohort-disposition-v1",
            "registered_slots": 0,
            "cohorts_realized": 0,
            "survivors": [],
            "card_cohort_semantics_verbatim": V["card_cohort_semantics_verbatim"],
            "band_mapping_version": "v1.4.0 (0 survivors -> REJECT; >=1 -> PASS; "
                                    "coverage/technical incomplete -> TECHNICAL_INCOMPLETE)",
            "non_gating_diagnostics": ("none: nothing was computed, so no cross-cohort "
                                       "diagnostic exists"),
        },
        "robustness_plan": {
            "registered_phase_grids": ["historical", "oos", "full", "fee_2x", "funding_2x",
                                       "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                                       "no_funding_full", "cost_attrition_40bps"],
            "cells_registered_per_grid": 0,
            "cells_registered_total": 0,
            "cells_computed": 0,
            "card_robustness_verbatim": V["card_robustness_verbatim"],
            "no_result_based_universe_shrinking": True,
        },
        "falsification": {
            "record_falsification_verbatim": V["record_falsification_verbatim"],
            "card_falsification_excerpt_verbatim": V["card_falsification_excerpt_verbatim"],
            "card_vs_record_disclosure": ("the frozen card body quotes items 1-3 of the "
                                          "record's falsification plan and truncates with "
                                          "_（節錄；完整內容見 canonical record）_; the "
                                          "record also carries item 4 (Subperiod Breakdown). "
                                          "The round registers the record's FULL battery; "
                                          "the missing item is restored from the canonical "
                                          "record and the difference is disclosed here "
                                          "instead of rewriting the frozen card bytes"),
            "card_excerpt_item_count": len(card_fals),
            "item_count": len(rec_fals),
            "restored_items": [{"item_index": 4, "source": "canonical_record",
                                "item": rec_fals[3],
                                "why": "the card's excerpt is truncated relative to the "
                                       "canonical record"}],
            "no_threshold_lowering": True,
            "no_item_removal": True,
            "falsification_status": "not_executed",
            "blocked_by": ["falsification 1 requires the actual TNX / VIX / SPYDrawdown "
                           "series; VIX is absent",
                           "falsification 2 and 3 both require a computed backtest",
                           "falsification 4 requires the 2022 subperiod of the registered "
                           "G/D baskets, which do not exist locally"],
        },
        "costs": {
            "record_cost_schedule_note": ("the record assumes flat 10 bps per two-way trade "
                                          "volume, stress-tested at 20 bps"),
            "cost_inputs_available_local": ("canonical instrument metadata exists for the "
                                            "four perpetuals (taker 5 bps / maker 2 bps, "
                                            "one tick increments measured)"),
            "cost_inputs_missing_local": ("none of the cost inputs can be applied: no "
                                          "registered instrument, no computed cell"),
            "local_cost_constants": {s: {"taker_fee": v["taker_fee"],
                                         "maker_fee": v["maker_fee"],
                                         "price_increment": v["price_increment"]}
                                     for s, v in local_instruments.items()},
            "note": ("cost constants are recorded for completeness; nothing was charged "
                     "because nothing traded"),
        },
        "strategy_domain": ("not_registered: the registered score needs an implied-volatility "
                            "series (the record's VIX slot, whose crypto replacement is "
                            "Deribit DVOL) and the two registered baskets; neither exists "
                            "for any local universe, so no strategy case can be admitted"),
        "strategy_domain_status": "not_registered",
        "expected": {
            "expected_status_reason": ("the card's §7.2 completion conditions cannot be met "
                                       "because the signal itself is not computable locally"),
            "cohorts": 0, "strategy_cases": 0, "dca_configs": 0, "phase_grids": 10,
            "expected_case_evaluations": 0,
            "grid_coverage_rule": ("G1 would require the measured cell set of every "
                                   "registered phase grid to equal cohorts x cases x 48; "
                                   "with no legal cohort the product is empty"),
        },
        "expected_status": "not_registered",
        "prerequisite_gate": {
            "verdict": "TECHNICAL_INCOMPLETE",
            "failure_layer": "card-local",
            "failure_class_used": "data_window_invalid",
            "failure_class_note": ("contract 13 has no dedicated prerequisite-missing class; "
                                   "`data_window_invalid` is the closest registered "
                                   "card-local class and its definition explicitly covers "
                                   "'instrument 缺失'. The decisive absence is a missing "
                                   "core-signal DATA FAMILY (implied volatility), not the "
                                   "window - disclosed rather than hidden"),
            "last_run_id": None,
            "attempts_launched": 0,
            "declared_verdict_alternatives": ["TECHNICAL_INCOMPLETE", "DEFERRED"],
            "measured_absence": measured_absence,
            "measured_available": measured_available,
            "decisive_absences": ["%s -> %s" % (k, v) for k, v in sorted(decisive_map.items())],
            "decisive_matrix_item_count": len(decisive_map),
            "decisive_status_map": dict(sorted(decisive_map.items())),
            "universe_shrunk": False,
            "substitute_market_used": False,
            "next_round_options": [
                "acquire an implied-volatility series (Deribit DVOL history, or an options "
                "chain capable of producing one) and register it as its own data card; the "
                "family then becomes fully evaluable on the local universe",
                "or record a NEW round whose registered signal replaces the VIX family with "
                "a locally available volatility measure - that is a semantic change "
                "(contract 8), never a retry of this round",
                "neither option is taken in this card: no data is fabricated, no threshold "
                "is lowered and no proxy market is substituted",
            ],
        },
        "other_local_stores": {
            "scan_roots": host["roots"],
            "files_scanned": host["files_scanned"],
            "series_hits_classified": host["series_hit_classes"],
            "note": host["note"],
            "measured_substitutes": host["measured_substitutes"],
            "prose_hits_note": ("every prose/source hit is classified; the only host "
                                "artifacts that even name the required data are source-cache "
                                "page fetches (Deribit / CBOE documentation), a competency "
                                "harness surface and source pages, none of which carries a "
                                "daily series"),
            "non_canonical_option_material": dict(
                ck.measure_option_snapshot(),
                instant_utc="2026-08-28T12:19:42Z",
                used=False,
                why_not_used=("a SINGLE-INSTANT Deribit BTC option snapshot (1,012 "
                              "instruments with strike/expiry, 1,012 book summaries with "
                              "mark_iv): it carries no history at all, it is not inside the "
                              "canonical raw (the card admits /data/raw read-only), and the "
                              "registered score needs a daily implied-volatility series "
                              "whose 756-day percentile and 21-day change are defined from "
                              "2017/2022 onward - one instant cannot express any of it")),
            "non_canonical_derived_panels": dict(
                ck.measure_derived_panels(),
                used=False,
                why_not_used=("a 12-symbol derived daily crypto close/quote-volume panel "
                              "(phase7; it does include AVAXUSDT, one of the portability "
                              "paragraph's named growth tokens): derived rather than "
                              "canonical, carries no implied-volatility series, and the "
                              "registered price series are US ETFs, not this panel")),
        },
        "launch": {
            "launched": False, "attempts": 0, "run_specs": 0, "terminal_sentinels": 0,
            "run_spec": None, "terminal_sentinel": None, "attempt_dir": None,
            "reason": ("prerequisite absent and measured before any computation: a launch "
                       "would require substituting an approximate dataset or rewriting the "
                       "registered signal, both forbidden by the card"),
        },
        "non_goals": [
            "no second backtester, no Manager/Service/Factory/Registry/Orchestrator/daemon",
            "no new audit profile or auditor child card",
            "no universe shrinking to manufacture executability",
            "no proxy market, no approximate dataset, no rewritten hypothesis",
            "no Qlib launch, no attempt directory, no sentinel",
            "no performance claim of any kind",
        ],
        "coverage": {
            "phase_grids": ["historical", "oos", "full", "fee_2x", "funding_2x",
                            "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                            "no_funding_full", "cost_attrition_40bps"],
            "cells_registered_per_grid": 0,
            "cells_registered_total": 0,
            "cells_computed": 0,
            "gate": ("G1 is vacuous here: with no legal cohort the registered product is "
                     "empty, and an empty product is not a coverage result"),
        },
        "card_family_id_verbatim": V["card_family_id_verbatim"],
        "card_source_of_truth_verbatim": V["card_source_of_truth_verbatim"],
        "card_provenance_verbatim": V["card_provenance_verbatim"],
        "card_raw_verbatim": V["card_raw_verbatim"],
        "card_universe_heading_verbatim": V["card_universe_heading_verbatim"],
        "card_window_verbatim": V["card_window_verbatim"],
        "card_registration_note_verbatim": V["card_registration_note_verbatim"],
        "card_failure_taxonomy_verbatim": V["card_failure_taxonomy_verbatim"],
        "card_survivor_bundle_verbatim": V["card_survivor_bundle_verbatim"],
        "card_implementation_rules_verbatim": V["card_implementation_rules_verbatim"],
        "card_git_ops_verbatim": V["card_git_ops_verbatim"],
        "card_limitations_excerpt_verbatim": V["card_limitations_excerpt_verbatim"],
        "record_title_verbatim": V["record_title_verbatim"],
        "record_frontmatter_caveat_verbatim": V["record_frontmatter_caveat_verbatim"],
        "record_provenance_verbatim": V["record_provenance_verbatim"],
        "record_execution_assumptions_verbatim": V["record_execution_assumptions_verbatim"],
        "record_evidence_verbatim": V["record_evidence_verbatim"],
        "record_limitations_verbatim": V["record_limitations_verbatim"],
        "record_implementation_status_verbatim": V["record_implementation_status_verbatim"],
        "record_adoption_boundary_verbatim": V["record_adoption_boundary_verbatim"],
        "record_sources_verbatim": V["record_sources_verbatim"],
        "contract_clause_verbatim": V["contract_clause_verbatim"],
        "contract_prerequisite_missing_alignment_verbatim":
            V["contract_prerequisite_missing_alignment_verbatim"],
        "contract_verdict_semantics_verbatim": V["contract_verdict_semantics_verbatim"],
        "contract_failure_layer_rule_verbatim": V["contract_failure_layer_rule_verbatim"],
        "contract_fullbacktest_definition_verbatim":
            V["contract_fullbacktest_definition_verbatim"],
        "contract_dca_provenance_verbatim": V["contract_dca_provenance_verbatim"],
        "contract_cohort_survivor_semantics_verbatim":
            V["contract_cohort_survivor_semantics_verbatim"],
        "contract_failure_taxonomy_verbatim": V["contract_failure_taxonomy_verbatim"],
        "contract_verdict_json_schema_verbatim": V["contract_verdict_json_schema_verbatim"],
        "contract_handoff_append_rules_verbatim": V["contract_handoff_append_rules_verbatim"],
        "lifecycle_footer_verbatim": V["lifecycle_footer_verbatim"],
        "excerpt_source_map": {},
    }

    # add the two leaves that were extracted onto the `record` source slice
    spec["hypothesis"]["record_research_interpretation_verbatim"] = between(
        record, "### Research interpretation", "\n## Signal")
    src["hypothesis.record_research_interpretation_verbatim"] = "record"

    # every *_verbatim leaf (in document order) -> declared source + digest
    spec["excerpt_source_map"] = {}
    for key, value in ck._walk_verbatim(spec):
        source = src.get(key) or src.get(key.split(".")[-1])
        if source is None:
            raise SystemExit("LEAF WITHOUT DECLARED SOURCE: %s" % key)
        spec["excerpt_source_map"][key] = {"source": source, "chars": len(value),
                                           "sha256": sha256_text(value)}
    print("excerpt_source_map entries: %d" % len(spec["excerpt_source_map"]))

    out_dir = os.path.join(RESULTS, FAMILY, "rounds", ROUND)
    os.makedirs(out_dir, exist_ok=True)
    spec_path = os.path.join(out_dir, "round-spec.json")
    with open(spec_path, "x", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1, ensure_ascii=False)
    print("wrote %s (%s)" % (spec_path, sha256_file(spec_path)))

    # ------------------------------------------------------------- verdict
    verdict = {
        "schema_version": 1,
        "family_id": FAMILY,
        "round_id": ROUND,
        "run_id": None,
        "kanban_task_id": TASK,
        "kanban_board": BOARD,
        "verdict": "TECHNICAL_INCOMPLETE",
        "performance_claimable": False,
        "missing_conditions": [
            "record data: CBOE VIX index - absent from the canonical raw and from every "
            "measured host store (the volatility-stress input vh_t / vr_t)",
            "record data: Deribit Bitcoin Implied Volatility Index (DVOL) - the record's OWN "
            "crypto replacement for VIX - absent; no options chain exists from which one "
            "could be produced",
            "record data: US-listed ETF prices (QQQ, XLK, VGT, SPYG, VUG, SCHD, VYM, VTV, "
            "FDVV, COWZ) and SPY on NYSE / NASDAQ / CBOE - absent",
            "record data: 10-year US Treasury yield (TNX) and Moody's Baa yield "
            "(FRED: BAA10Y) - absent (the annualized perpetual funding-rate replacement for "
            "TNX is present, the credit series has no replacement)",
            "record data: any options market surface (strike / expiry / implied volatility / "
            "open interest) - absent",
            "record signal: vh_t = z(VIXPercentile_756) - not constructible",
            "record signal: vr_t = -z(dVIX_21) - not constructible",
            "record signal: softplus components HighVIX / VIXRelief / LowVIX - not "
            "constructible",
            "record signal: interaction terms i1 / i2 / i3 / i4 - not constructible",
            "record signal: StressScore / CrowdedScore / RawScore and its expanding Z-score - "
            "blocked by the same absence",
            "record baskets: growth basket SOL, AVAX, NEAR, SUI (1 of 4 tokens present) and "
            "defensive basket BTC + USD stablecoins (no stablecoin instrument) - "
            "not constructible as registered",
            "record falsification 1: Stationary Random Placebo Test on the actual TNX / VIX / "
            "SPYDrawdown series - not executable",
            "record falsification 2: Cost Stress Boundary Test (10 -> 25 / 40 bps) - "
            "not executable without a computed backtest",
            "record falsification 3: Parameter Perturbation Grid (alpha, lambda_s, tau_w, "
            "eta) - not executable without a computed backtest",
            "record falsification 4: Subperiod Breakdown (2022 rate-hike regime, max DD "
            "shallower than -25%) - restored from the canonical record, blocked by the same "
            "absence",
            "section 7.2 conditions not met: no eligible universe coverage, no strategy "
            "domain, no DCA cell, no historical/OOS/robustness evaluation",
        ],
        "yield": {
            "rounds_used": 1,
            "max_rounds": 3,
            "no_progress_rounds": 1,
            "progress_evidence": [],
            "yield_decision": "STOP_TECHNICAL_INCOMPLETE",
            "note": ("§15.3: this round computed nothing and eliminated nothing, so it counts "
                     "as no progress (conservative reading; the terminal is technical, not "
                     "scientific)"),
        },
        "failure": {
            "layer": "card-local",
            "class": "data_window_invalid",
            "detail": ("the registered signal is a continuous macro-timing score over a "
                       "growth-versus-defensive style spread. Two of its five "
                       "direction-normalized inputs (vh_t = z(VIXPercentile_756), "
                       "vr_t = -z(dVIX_21)) and three of its four interaction terms require "
                       "an implied-volatility series; the record's own crypto portability "
                       "paragraph prescribes Deribit DVOL for exactly that slot. The "
                       "canonical raw is one venue's USD-M perpetual store (BINANCE, four "
                       "fixed contracts, OHLCV k-lines at 5m/15m/30m/1h/4h/1d/1w + funding + "
                       "instrument metadata): there is no implied-volatility series, no "
                       "options chain, no volatility index, no US-equity venue, no ETF "
                       "panel and no Treasury/credit series, and the four local instruments "
                       "are perpetuals with no option, maturity or index field. Because the "
                       "missing series is an external index rather than a property of a "
                       "traded universe, no legitimate local universe can compute the "
                       "registered score"),
            "failure_class_note": ("contract 13 has no dedicated prerequisite-missing class; "
                                   "`data_window_invalid` is the closest registered "
                                   "card-local class and its definition explicitly covers "
                                   "'instrument 缺失'. The decisive absence is a missing "
                                   "core-signal data family, not the window - disclosed "
                                   "rather than hidden"),
            "last_run_id": None,
            "terminal_evidence": {
                "round_spec": "rounds/%s/round-spec.json" % ROUND,
                "verdict": "rounds/%s/verdict.json" % ROUND,
                "checker": ("runtime/continuous_macro_timing_growth_defensive_style_"
                            "allocation_prerequisite_check.py"),
                "repo_evidence": ("evidence/continuous-macro-timing-growth-defensive-style-"
                                  "allocation-2026-09-02-prerequisite-gate-20260918.json"),
            },
            "incomplete_reason": ("prerequisite absent and measured: zero attempts were "
                                  "launched, so no backtest output exists and none will be "
                                  "fabricated"),
            "definitive_absences": measured_absence + [
                "decisive requirement rows with a non-positive status: %d of %d registered "
                "matrix rows (%s)"
                % (len(decisive_map), len(M), json.dumps(status_counts, ensure_ascii=False)),
                "the decisive row is the implied-volatility series: the record's own crypto "
                "mapping names Deribit DVOL, and the measured raw contains no Deribit, no "
                "DVOL, no options surface and no volatility index at all",
            ],
        },
        "attempts": {"launched": 0, "run_specs": 0, "terminal_sentinels": 0,
                     "run_spec": None, "terminal_sentinel": None, "attempt_dir": None},
        "evidence_run_ids": [],
        "prerequisite": {
            "required_data_available": False,
            "decisive_matrix_item_count": len(decisive_map),
            "decisive_status_map": dict(sorted(decisive_map.items())),
            "measured_available": measured_available,
            "measured_absence": measured_absence,
            "universe_shrunk": False,
            "substitute_market_used": False,
        },
        "cohorts": {"registered_slots": 0, "realized": 0},
        "survivors": [],
        "survivor_bundle": None,
        "coverage": {"cells_registered_per_grid": 0, "cells_registered_total": 0,
                     "cells_computed": 0, "phase_grids": spec["coverage"]["phase_grids"]},
        "decided_at_utc": now,
        "decided_by": "Hermes default (Xiaoqian), card %s" % TASK,
    }
    vpath = os.path.join(out_dir, "verdict.json")
    with open(vpath, "x", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=1, ensure_ascii=False)
    print("wrote %s (%s)" % (vpath, sha256_file(vpath)))


if __name__ == "__main__":
    main()
