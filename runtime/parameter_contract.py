#!/usr/bin/env python3
"""Generic Strategy Family Parameter Contract (Contract v1.8.0, section 29).

Single source of truth: the frozen round-spec's ``parameter_contract`` copy.
Runtime is generic: validate / normalize / match / render from that copy.
Only pre-schema Strategy A v2 uses the in-code LEGACY_A_CONTRACT bridge.
New families without a schema fail closed.

Pure stdlib, no IO side effects beyond reading JSON files passed in.
"""
import hashlib
import json
import math

PARAMETER_CONTRACT_VERSION = 1
LEGACY_A_FAMILY_ID = "close-vs-sma-mean-reversion-long-flat-v2"

LEGACY_A_CONTRACT = {
    "parameter_contract_version": 1,
    "family_id": LEGACY_A_FAMILY_ID,
    "contract_ref": "legacy-a-v2 pre-schema bridge (code constant, not round-spec)",
    "research_axes_ordered": [
        {"name": "window", "kind": "atomic", "members": ["window"],
         "registered_values": [20, 50, 100, 200], "row_fields": ["window"]},
        {"name": "discount", "kind": "atomic", "members": ["discount"],
         "registered_values": [0.01, 0.02, 0.03], "row_fields": ["discount"]},
        {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
         "registered_values": [0.01, 0.02, 0.03, 0.04], "row_fields": ["spacing_pct"]},
        {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
         "registered_values": [1.0, 1.1], "row_fields": ["size_multiplier"]},
        {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
         "registered_values": [0.01, 0.02, 0.03], "row_fields": ["breakeven_tp_pct"]},
        {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
         "registered_values": [0.05, 0.10], "row_fields": ["invalidation_pct"]},
    ],
    "row_fields": ["window", "discount", "spacing_pct", "size_multiplier",
                   "breakeven_tp_pct", "invalidation_pct"],
    "composite_map": {},
    "strategy_param_fields": ["window", "discount"],
    "dca_param_fields": ["spacing_pct", "size_multiplier", "breakeven_tp_pct",
                         "invalidation_pct"],
    "canonical_recipe": {"sort_keys": True, "separators": (",", ":"),
                         "ensure_ascii": False,
                         "numeric_rule": "JSON number finite, bool excluded"},
    "row_match_recipe": {"keys": ["symbol", "timeframe", "window", "discount",
                                  "spacing_pct", "size_multiplier",
                                  "breakeven_tp_pct", "invalidation_pct"],
                         "equality": "exact, numeric == float compare, rest bytewise"},
    "non_params": ["symbol", "timeframe", "ema_pair_index", "walk_forward_index",
                   "n_steps", "indices", "diagnostics", "metrics"],
    "domain_cardinality": {"strategy": 12, "dca": 48, "per_cohort": 576},
}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _str_list(value):
    """True when *value* is a list whose entries are all strings."""
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _same_names(left, right):
    """Order-insensitive equality of two field-name lists.

    None when either side is not a plain string list.  R2 remediation (v1.8 re-audit residual):
    a malformed mixed-type list must become a validation problem, never a TypeError out of
    sorted() - callers turn None into a problem instead of comparing.
    """
    if not (_str_list(left) and _str_list(right)):
        return None
    return sorted(left) == sorted(right)


def _axis_by_name(contract):
    out = {}
    for ax in contract.get("research_axes_ordered", []):
        if isinstance(ax, dict) and isinstance(ax.get("name"), str):
            out[ax["name"]] = ax
    return out


def validate_contract(contract):
    """Structural validation of one parameter_contract dict. Returns [problems]."""
    problems = []
    if not isinstance(contract, dict):
        return ["parameter_contract is not an object"]
    if contract.get("parameter_contract_version") != PARAMETER_CONTRACT_VERSION:
        problems.append("parameter_contract_version must be %r, got %r"
                        % (PARAMETER_CONTRACT_VERSION, contract.get("parameter_contract_version")))
    fam = contract.get("family_id")
    if not (isinstance(fam, str) and fam.strip()):
        problems.append("family_id must be a non-empty string")
    axes = contract.get("research_axes_ordered")
    if not (isinstance(axes, list) and axes):
        problems.append("research_axes_ordered must be a non-empty list")
        return problems
    seen_row_fields = []
    for ax in axes:
        if not isinstance(ax, dict):
            problems.append("research axis entry is not an object: %r" % (ax,))
            continue
        name = ax.get("name")
        kind = ax.get("kind")
        members = ax.get("members")
        reg = ax.get("registered_values")
        rfields = ax.get("row_fields")
        if not (isinstance(name, str) and name.strip()):
            problems.append("axis name must be a non-empty string: %r" % (ax,))
        if kind not in ("atomic", "composite"):
            problems.append("axis %r kind must be atomic|composite, got %r" % (name, kind))
        if not (isinstance(members, list) and members
                and all(isinstance(m, str) and m.strip() for m in members)):
            problems.append("axis %r members must be a non-empty string list" % (name,))
        if not (isinstance(reg, list) and reg):
            problems.append("axis %r registered_values must be a non-empty list" % (name,))
        if not (isinstance(rfields, list) and rfields
                and all(isinstance(f, str) and f.strip() for f in rfields)):
            problems.append("axis %r row_fields must be a non-empty string list" % (name,))
            continue
        if isinstance(members, list) and isinstance(rfields, list):
            try:
                if sorted(members) != sorted(rfields):
                    # members must equal row_fields as sets (composite members are row fields)
                    problems.append("axis %r members %r != row_fields %r (must match as sets)"
                                    % (name, members, rfields))
            except TypeError:
                problems.append("axis %r members or row_fields contains non-sortable values"
                                % (name,))
        if kind == "composite" and isinstance(members, list) and isinstance(reg, list):
            for v in reg:
                if not (isinstance(v, (list, tuple)) and len(v) == len(members)):
                    problems.append("axis %r composite arity mismatch: value %r length != members %r"
                                    % (name, v, members))
                    break
        seen_row_fields.extend(rfields if isinstance(rfields, list) else [])
    if len(seen_row_fields) != len(set(seen_row_fields)):
        dupes = sorted({f for f in seen_row_fields if seen_row_fields.count(f) > 1})
        problems.append("duplicate row fields across axes: %r" % (dupes,))
    row_fields = contract.get("row_fields")
    if not (isinstance(row_fields, list) and row_fields):
        problems.append("row_fields must be a non-empty list")
    else:
        same = _same_names(row_fields, seen_row_fields)
        if same is None:
            # R2 remediation: a mixed-type row_fields list must not reach sorted().
            problems.append("row_fields must be a string list, got %r" % (row_fields,))
        elif not same:
            problems.append("row_fields %r != flattened axis row_fields %r"
                            % (row_fields, seen_row_fields))
    cmap = contract.get("composite_map")
    if not isinstance(cmap, dict):
        problems.append("composite_map must be an object")
    else:
        by_name = _axis_by_name(contract)
        for k, v in cmap.items():
            ax = by_name.get(k)
            if ax is None or ax.get("kind") != "composite":
                problems.append("composite_map key %r is not a composite axis" % (k,))
            elif not isinstance(v, list):
                # F3 remediation: malformed composite_map values (non-list) accumulate
                # validation problems instead of raising TypeError from sorted().
                problems.append("composite_map[%r] value is not a list: %r" % (k, v))
            else:
                same = _same_names(v, ax.get("members"))
                if same is None:
                    # R2 remediation (v1.8 re-audit residual): a mixed-type value list, or a
                    # referenced axis whose members are mixed-type, must accumulate a problem
                    # instead of raising TypeError out of sorted().
                    problems.append("composite_map[%r] value %r and axis members %r must both be "
                                    "string lists" % (k, v, ax.get("members")))
                elif not same:
                    problems.append("composite_map[%r] %r != axis members %r"
                                    % (k, v, ax.get("members")))
    strat = contract.get("strategy_param_fields")
    dca = contract.get("dca_param_fields")
    if not (isinstance(strat, list) and strat and all(isinstance(s, str) for s in strat)):
        problems.append("strategy_param_fields must be a non-empty string list")
    if not (isinstance(dca, list) and dca and all(isinstance(s, str) for s in dca)):
        problems.append("dca_param_fields must be a non-empty string list")
    if isinstance(strat, list) and isinstance(dca, list) and isinstance(row_fields, list):
        # R2 remediation: set()/sorted() over malformed field lists must not raise.  A non-string
        # list is already reported by the non-empty-string-list checks above, so the cross-checks
        # simply do not run on it (still fail closed via those problems).
        if _str_list(strat) and _str_list(dca):
            overlap = sorted(set(strat) & set(dca))
            if overlap:
                problems.append("strategy_param_fields and dca_param_fields overlap: %r"
                                % (overlap,))
            if _str_list(row_fields) and sorted(list(strat) + list(dca)) != sorted(row_fields):
                problems.append("strategy+dca fields must partition row_fields exactly")
    card = contract.get("domain_cardinality")
    if not isinstance(card, dict):
        problems.append("domain_cardinality must be an object")
    else:
        for k in ("strategy", "dca", "per_cohort"):
            v = card.get(k)
            if not (isinstance(v, int) and not isinstance(v, bool) and v > 0):
                problems.append("domain_cardinality.%s must be a positive int, got %r" % (k, v))
        if all(isinstance(card.get(k), int) for k in ("strategy", "dca", "per_cohort")):
            if card["per_cohort"] != card["strategy"] * card["dca"]:
                problems.append("domain_cardinality per_cohort %r != strategy %r x dca %r"
                                % (card["per_cohort"], card["strategy"], card["dca"]))
            # product check: strategy axes product must equal strategy count
            try:
                strat_set = set(strat) if isinstance(strat, list) else set()
                prod_s, prod_d = 1, 1
                for ax in axes:
                    members = ax.get("members", [])
                    reg = ax.get("registered_values", [])
                    if not members or not reg:
                        continue
                    if set(members) <= strat_set:
                        prod_s *= len(reg)
                    else:
                        prod_d *= len(reg)
                if prod_s != card["strategy"]:
                    problems.append("strategy cardinality %r != product of strategy axes %r"
                                    % (card["strategy"], prod_s))
                if prod_d != card["dca"]:
                    problems.append("dca cardinality %r != product of dca axes %r"
                                    % (card["dca"], prod_d))
            except (TypeError, AttributeError):
                pass
    return problems


def load_contract_from_round_spec(spec):
    """Return (contract, problems, is_legacy).

    - round-spec with a valid parameter_contract -> (contract, [], False)
    - round-spec without one and family is pre-schema A -> (LEGACY_A_CONTRACT, [], True)
    - otherwise fail closed -> (None, [reason], False)
    """
    if not isinstance(spec, dict):
        return None, ["round-spec is not an object"], False
    fam = spec.get("family_id")
    raw = spec.get("parameter_contract")
    if raw is None:
        if fam == LEGACY_A_FAMILY_ID:
            return dict(LEGACY_A_CONTRACT), [], True
        return None, ["round-spec has no parameter_contract and family %r is not pre-schema %r: fail closed (v1.8+)"
                      % (fam, LEGACY_A_FAMILY_ID)], False
    problems = validate_contract(raw)
    if problems:
        return None, ["parameter_contract invalid: " + "; ".join(problems)], False
    if raw.get("family_id") != fam:
        return None, ["parameter_contract.family_id %r != round-spec family_id %r"
                      % (raw.get("family_id"), fam)], False
    return raw, [], False


def normalize_winner(contract, winner, label, problems):
    """Split a frozen winner cell into (strategy, dca) via contract, or (None, None).

    Fail-closed on missing / unknown / non-numeric (downstream needs only these three).
    """
    strat_fields = list(contract.get("strategy_param_fields", []))
    dca_fields = list(contract.get("dca_param_fields", []))
    if not isinstance(winner, dict):
        problems.append("%s: winner cell is not an object" % label)
        return None, None
    keys = set(winner)
    want = strat_fields + dca_fields
    missing = [k for k in want if k not in keys]
    if missing:
        problems.append("%s: winner cell is missing registered param axis/axes %r" % (label, missing))
    unknown = sorted(keys - set(want))
    if unknown:
        problems.append("%s: winner cell carries param key(s) outside the registered axes %r"
                        % (label, unknown))
    if missing or unknown:
        return None, None
    for key in want:
        if not is_number(winner[key]):
            problems.append("%s: param %s is not numeric (%r)" % (label, key, winner[key]))
            return None, None
    return ({k: winner[k] for k in strat_fields}, {k: winner[k] for k in dca_fields})


def match_row(rows, symbol, timeframe, params, contract):
    """Exactly-one frozen grid row for symbol/timeframe + full row_fields, else raise."""
    row_fields = list(contract.get("row_fields", []))
    hits = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("symbol") != symbol or r.get("timeframe") != timeframe:
            continue
        ok = True
        for k in row_fields:
            rv = r.get(k)
            pv = params.get(k)
            try:
                if rv is None or pv is None or float(rv) != float(pv):
                    ok = False
                    break
            except (TypeError, ValueError):
                ok = False
                break
        if ok:
            hits.append(r)
    if len(hits) != 1:
        raise ValueError("expected exactly one frozen row for %s/%s %r, found %d"
                         % (symbol, timeframe, params, len(hits)))
    return hits[0]


def validate_round_spec_contract(spec):
    """Launch-time (preflight/instantiate) fail-closed checks 1-6. Returns [problems]."""
    contract, problems, _ = load_contract_from_round_spec(spec)
    if problems:
        return problems
    out = []
    # 5. schema hash / family consistency is already checked in load; check domain counts
    # against the round-spec's own declared domains when present (A: parameter_domain,
    # B: parameter_domain + dca_domain).
    card = contract.get("domain_cardinality", {})
    dom = spec.get("parameter_domain", {}) if isinstance(spec.get("parameter_domain"), dict) else {}
    dca_dom = spec.get("dca_domain", {}) if isinstance(spec.get("dca_domain"), dict) else {}
    # B-style: legal_cases_per_cohort should equal strategy cardinality
    if "legal_cases_per_cohort" in dom:
        try:
            if int(dom["legal_cases_per_cohort"]) != int(card.get("strategy", -1)):
                out.append("schema domain mismatch: parameter_domain.legal_cases_per_cohort %r != contract strategy %r"
                           % (dom["legal_cases_per_cohort"], card.get("strategy")))
        except (TypeError, ValueError):
            out.append("parameter_domain.legal_cases_per_cohort is not an int")
    # A-style: same key
    # DCA: config_count / grid_size should equal dca cardinality
    for key in ("config_count", "grid_size"):
        if key in dca_dom:
            try:
                if int(dca_dom[key]) != int(card.get("dca", -1)):
                    out.append("schema domain mismatch: dca_domain.%s %r != contract dca %r"
                               % (key, dca_dom[key], card.get("dca")))
            except (TypeError, ValueError):
                out.append("dca_domain.%s is not an int" % key)
    # expected block cross-check when present
    exp = spec.get("expected", {}) if isinstance(spec.get("expected"), dict) else {}
    if isinstance(exp, dict):
        if "strategy_cases_per_cohort" in exp:
            try:
                if int(exp["strategy_cases_per_cohort"]) != int(card.get("strategy", -1)):
                    out.append("schema domain mismatch: expected.strategy_cases_per_cohort %r != contract strategy %r"
                               % (exp["strategy_cases_per_cohort"], card.get("strategy")))
            except (TypeError, ValueError):
                pass
        if "dca_configs_per_cohort" in exp:
            try:
                if int(exp["dca_configs_per_cohort"]) != int(card.get("dca", -1)):
                    out.append("schema domain mismatch: expected.dca_configs_per_cohort %r != contract dca %r"
                               % (exp["dca_configs_per_cohort"], card.get("dca")))
            except (TypeError, ValueError):
                pass
        if "base_combinations_per_cohort" in exp:
            try:
                if int(exp["base_combinations_per_cohort"]) != int(card.get("per_cohort", -1)):
                    out.append("schema domain mismatch: expected.base_combinations_per_cohort %r != contract per_cohort %r"
                               % (exp["base_combinations_per_cohort"], card.get("per_cohort")))
            except (TypeError, ValueError):
                pass
    return out


def render_parameter_contract(contract):
    """Pure-stdlib human-readable PARAMETER CONTRACT block (six required lines).

    Single source: the round-spec parameter_contract dict. No second table.
    """
    axes = contract.get("research_axes_ordered", [])
    parts = []
    for ax in axes:
        name = ax.get("name")
        kind = ax.get("kind")
        reg = ax.get("registered_values", [])
        if kind == "composite":
            members = ", ".join(ax.get("members", []))
            vals = ",".join("(%s)" % ",".join(str(x) for x in (v if isinstance(v, (list, tuple)) else [v]))
                            for v in reg[:6])
            more = "" if len(reg) <= 6 else ",...(+%d)" % (len(reg) - 6,)
            parts.append("%s{(composite %s)%s%s}" % (name, members, vals, more))
        else:
            vals = ",".join(str(v) for v in reg[:8])
            more = "" if len(reg) <= 8 else ",...(+%d)" % (len(reg) - 8,)
            parts.append("%s{%s%s}" % (name, vals, more))
    cmap = contract.get("composite_map", {}) or {}
    cmap_s = ", ".join("%s->[%s]" % (k, ",".join(v)) for k, v in sorted(cmap.items())) or "none"
    card = contract.get("domain_cardinality", {})
    lines = [
        "PARAMETER CONTRACT [%s, %s]" % (contract.get("family_id"),
                                        "v1, composite" if cmap
                                        else "legacy-a-v2, no composite"
                                        if contract.get("family_id") == LEGACY_A_FAMILY_ID
                                        else "v1, atomic"),
        "research: " + " x ".join(parts),
        "composite: " + cmap_s,
        "DCA/execution: " + ",".join(contract.get("dca_param_fields", []))
        + "=%s" % (card.get("dca"),),
        "cardinality: strategy=%s dca=%s per-cohort=%s" % (card.get("strategy"), card.get("dca"),
                                                          card.get("per_cohort")),
        "row: [%s]" % (",".join(contract.get("row_fields", [])),),
        "canonical: strategy=[%s] dca=[%s]; match=symbol+timeframe+full-row exactly-one"
        % (",".join(contract.get("strategy_param_fields", [])),
           ",".join(contract.get("dca_param_fields", []))),
    ]
    return "\n".join(lines)
