#!/usr/bin/env python3
"""File-only frozen-survivor index (Contract v1.8.0, sections 27.2 / 27.7).

Contract v1.5.0 starts the post-survivor lifecycle: every survivor frozen into a
`rounds/<round_id>/survivor-bundle.json` keeps accumulating unseen forward evidence and is
ranked on a rebuildable Top-10 leaderboard.  This script is the first half of that layer and
the only producer of

    <results-root>/_survivors/survivor-index.json

It is a host-side, pure-stdlib, deterministic READER of the frozen survivor bundles: it scans
`<results-root>/*/rounds/*/survivor-bundle.json`, re-checks each bundle's own published
identity against the contract 10.8 recipe, pins the round's research data cutoff to the
checksum the bundle itself recorded for `round-spec.json`, and describes every survivor with a
deterministic `survivor_id`.

The index is a DERIVED, rebuildable artifact - never a source of truth and never a gate.  The
source of truth stays the frozen survivor bundle; the index, the leaderboard and the forward
evidence are file artifacts only (no Registry service, no daemon, no queue: contract 1.2).

Fail-closed (never guessed, never repaired):
  * a bundle without `bundle_identity_sha256`, or whose published identity is not the recipe
    applied to itself (missing checksum / invalid bundle),
  * a bundle whose `family_id` / `round_id` disagree with the directory it sits in, whose
    `kanban_task_id` is missing on either side or disagrees with `family.json`, whose
    `source_artifacts` map is empty or malformed, or whose `round-spec.json` no longer hashes to
    the value the bundle recorded (source inconsistency),
  * a survivor record whose param cell is not exactly the registered strategy + DCA axes
    (per the round-spec's parameter_contract or the legacy A v2 bridge),
  * two entries claiming the same `survivor_id` (duplicate).

Nothing here ranks, selects, promotes or rejects a survivor, and nothing here ever writes into
a round or attempt directory: the only place this layer may write is `<results-root>/_survivors/**`
(contract 27.1).  `--out` is checked against that boundary before the first write, and the reserved
root itself must not be a symlink and must resolve to the literal `<results-root>/_survivors`
(v1.5.2), so neither a `--out`, a symlink, nor a `..` segment - and not a re-pointed `_survivors`
root either - can land on a frozen bundle/verdict/result.

usage:
  python3 runtime/survivor_index.py [--results-root <dir>] [--out <path>] [--check] [--json]
exit: 0 = ok (written / check clean), 1 = refused or mismatch, 2 = usage error
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

SCHEMA_VERSION = 1
KIND = "survivor_index"
CONTRACT_VERSION = "v1.8.0"
CONTRACT_SECTION = "27.2"
DEFAULT_RESULTS_ROOT = "/Volumes/ExpansionDrive/qlib-results"

SURVIVORS_DIRNAME = "_survivors"
INDEX_NAME = "survivor-index.json"
FORWARD_DIRNAME = "forward"
BUNDLE_NAME = "survivor-bundle.json"
BUNDLE_KIND = "frozen_survivor_bundle"

# Legacy A v2 hardcoded param axes (backward-compatible default when no parameter_contract
# is present in the round-spec).  The generic contract module is the authoritative source
# for new families.
STRATEGY_PARAM_KEYS = ("window", "discount")
DCA_PARAM_KEYS = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
ROBUSTNESS_GRIDS = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")

# The index is a derived artifact: the generation timestamp is not part of its measured
# content, so `--check` excludes it from the comparison (same idea as contract 10.8).
GENERATED_KEY = "generated_at_utc"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from survivor_bundle import identity as bundle_identity  # noqa: E402
import parameter_contract as pc  # noqa: E402


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical(obj):
    """Contract 10.8 canonicalization, reused verbatim: sorted keys, no insignificant space."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def forward_dir(results_root):
    return os.path.join(results_root, SURVIVORS_DIRNAME, FORWARD_DIRNAME)


def forward_path(results_root, survivor_id):
    return os.path.join(forward_dir(results_root), "%s.jsonl" % survivor_id)


def index_path(results_root):
    return os.path.join(results_root, SURVIVORS_DIRNAME, INDEX_NAME)


def reserved_root(results_root):
    """`<results-root>/_survivors` as a literal path (its final component not resolved)."""
    return os.path.join(os.path.abspath(results_root), SURVIVORS_DIRNAME)


def write_boundary(results_root):
    """The one subtree contract 27.1 lets this layer write to: `<results-root>/_survivors`.

    The results root's own symlinks are resolved (a symlinked `/results` volume is fine), but the
    reserved component is kept literal - `reserved_root_problem()` refuses a root that is itself a
    symlink, so the boundary can never be silently re-pointed at a frozen round/attempt directory.
    """
    return os.path.join(os.path.realpath(os.path.abspath(results_root)), SURVIVORS_DIRNAME)


def reserved_root_problem(results_root):
    """None when `<results-root>/_survivors` is itself the literal reserved directory, else why not.

    v1.5.2 / audit t_346bcc04 finding F1: resolving both sides is not enough on its own.  If
    `_survivors` is ITSELF a symlink onto a frozen round/attempt directory, realpath resolves the
    boundary onto that frozen directory, so an "in-boundary" `--out` lands on a frozen verdict and
    `makedirs()`/`open(w)` create files inside the frozen round - all with rc=0.  The reserved root
    must therefore be a real directory sitting exactly one level under the resolved results root,
    and any root/ancestor escape is refused before the first write.
    """
    literal = reserved_root(results_root)
    boundary = write_boundary(results_root)
    if os.path.islink(literal):
        return ("the reserved post-survivor root %s is a symlink -> %s: contract 27.1 permits this "
                "layer to write only under the literal <results-root>/_survivors directory, and a "
                "symlinked root puts the write outside the reserved post-survivor write boundary "
                "%s (root/ancestor escape)"
                % (literal, os.path.realpath(literal), boundary))
    resolved = os.path.realpath(literal)
    if resolved != boundary:
        return ("the resolved reserved post-survivor root %s is not the literal %s child of the "
                "resolved results root: writing through it lands outside the reserved post-survivor "
                "write boundary %s (root/ancestor escape)"
                % (resolved, SURVIVORS_DIRNAME, boundary))
    return None


def outside_write_boundary(results_root, path):
    """None when `path` lands inside `<results-root>/_survivors/**`, else the refusal message.

    Contract 27.1: the post-survivor layer writes only under `_survivors/**`, never into a
    `<family_id>`, round or attempt directory.  Three things are checked before any write, in
    order: the reserved root itself must not be a symlink and must resolve to the literal
    `<results-root>/_survivors` (v1.5.2; audit finding F1 - a symlinked root re-pointed the whole
    boundary onto a frozen round directory, which the two-sided realpath check below could not
    see), then the target is realpath-resolved and must sit inside that boundary.  A symlink or a
    `..` segment can therefore not be used to reach a frozen bundle/verdict/result - which is what
    an unchecked `--out` did before v1.5.1 (audit finding F1: the index overwrote
    `rounds/<round_id>/survivor-bundle.json` and `verdict.json` with rc=0).
    """
    root_problem = reserved_root_problem(results_root)
    if root_problem:
        return root_problem
    boundary = write_boundary(results_root)
    target = os.path.realpath(os.path.abspath(path))
    if target == boundary or target.startswith(boundary + os.sep):
        return None
    return ("%s is outside the reserved post-survivor write boundary %s: contract 27.1 permits "
            "this layer to write only under _survivors/** - never into a <family_id>, round or "
            "attempt directory, and never over a frozen bundle, verdict or result"
            % (target, boundary))


def bundle_paths(results_root):
    """Every frozen survivor bundle under the root, in deterministic order.

    `_`-prefixed entries are reserved namespaces (`_survivors`, `_incidents`, `_handoff`, ...),
    never family ids, so they are skipped exactly like contract 12.6 requires of `_incidents`.
    """
    found = []
    if not os.path.isdir(results_root):
        return found
    for family in sorted(os.listdir(results_root)):
        if family.startswith("_"):
            continue
        rounds_dir = os.path.join(results_root, family, "rounds")
        if not os.path.isdir(rounds_dir):
            continue
        for rnd in sorted(os.listdir(rounds_dir)):
            path = os.path.join(rounds_dir, rnd, BUNDLE_NAME)
            if os.path.isfile(path):
                found.append(path)
    return found


def survivor_id(payload):
    """Deterministic survivor identity (contract 27.2).

    The digest covers exactly the fields the identity must pin - family_id, round_id, run_id,
    the source bundle's frozen identity, the cohort and the frozen strategy + DCA params - so
    the same frozen survivor always gets the same id on any host and any rebuild, while a
    retuned cell (see the challenger rule, contract 27.4) can never collide with its incumbent.
    Truncated to 16 hex chars: collisions are still impossible to miss, because a repeated id
    is refused instead of merged.
    """
    return "sv-" + digest(payload).split(":", 1)[1][:16]


def identity_payload(family_id, round_id, run_id, bundle_id, cohort, strategy, dca):
    return {"family_id": family_id, "round_id": round_id, "run_id": run_id,
            "bundle_identity_sha256": bundle_id, "cohort": cohort,
            "strategy_params": strategy, "dca_params": dca}


def is_number(value):
    """True iff value is a real, finite JSON number (bools, strings and NaN are not)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def num(value):
    return value if is_number(value) else None


def param_cell(winner, label, problems, contract=None):
    """Split a frozen winner cell into its registered strategy + DCA axes (or refuse).

    When *contract* is provided (from the round-spec's parameter_contract or the legacy
    bridge), strategy_param_fields and dca_param_fields are read from it.  When None,
    the hardcoded A v2 axes are used (backward-compatible default).
    """
    if contract is not None:
        strat_keys = tuple(contract.get("strategy_param_fields", []))
        dca_keys = tuple(contract.get("dca_param_fields", []))
    else:
        strat_keys = STRATEGY_PARAM_KEYS
        dca_keys = DCA_PARAM_KEYS
    if not isinstance(winner, dict):
        problems.append("%s: winner cell is not an object" % label)
        return None, None
    keys = set(winner)
    missing = [k for k in strat_keys + dca_keys if k not in keys]
    if missing:
        problems.append("%s: winner cell is missing registered param axis/axes %r" % (label, missing))
    unknown = sorted(keys - set(strat_keys) - set(dca_keys))
    if unknown:
        problems.append("%s: winner cell carries param key(s) outside the registered axes %r"
                        % (label, unknown))
    if missing or unknown:
        return None, None
    for key in strat_keys + dca_keys:
        if not is_number(winner[key]):
            problems.append("%s: param %s is not numeric (%r)" % (label, key, winner[key]))
            return None, None
    strategy = {k: winner[k] for k in strat_keys}
    dca = {k: winner[k] for k in dca_keys}
    return strategy, dca


def metrics_evidence(rec, label, problems):
    """The frozen evidence the leaderboard reads: OOS/full, the four stress grids, neighbourhood."""
    metrics = rec.get("metrics")
    if not isinstance(metrics, dict):
        problems.append("%s: survivor record carries no metrics" % label)
        return None
    for window in ("oos", "full"):
        block = metrics.get(window)
        if not isinstance(block, dict) or not is_number(num(block.get("sharpe"))):
            problems.append("%s: metrics.%s.sharpe is missing or non-numeric" % (label, window))
    robustness = metrics.get("robustness")
    if not isinstance(robustness, dict):
        problems.append("%s: metrics.robustness is missing" % label)
        return None
    missing = [g for g in ROBUSTNESS_GRIDS
               if not isinstance(robustness.get(g), dict)
               or not is_number(num(robustness[g].get("net_pnl")))]
    if missing:
        problems.append("%s: metrics.robustness lacks a numeric net_pnl for %r"
                        % (label, missing))
    if problems:
        return None
    floors = {g: {"net_pnl": robustness[g]["net_pnl"],
                  "sharpe": num(robustness[g].get("sharpe")),
                  "max_dd_pct": num(robustness[g].get("max_dd_pct"))}
              for g in ROBUSTNESS_GRIDS}
    # The stress floor is the worst of the four reruns; the grid name is recorded so the number
    # is never read without knowing which assumption produced it.  Ties resolve lexically.
    floor_grid = min(ROBUSTNESS_GRIDS, key=lambda g: (floors[g]["net_pnl"], g))
    neighbourhood = metrics.get("neighbourhood") or rec.get("neighbourhood") or {}
    if not isinstance(neighbourhood, dict):
        neighbourhood = {}
    oos = metrics["oos"]
    full = metrics["full"]
    historical = metrics.get("historical") if isinstance(metrics.get("historical"), dict) else {}
    return {
        "historical": {k: num(historical.get(k)) for k in ("net_pnl", "sharpe", "episodes", "max_dd_pct")},
        "oos": {k: num(oos.get(k)) for k in ("net_pnl", "sharpe", "episodes", "max_dd_pct")},
        "full": {k: num(full.get(k)) for k in ("net_pnl", "sharpe", "episodes", "max_dd_pct")},
        "robustness": floors,
        "robustness_stress_floor_net_pnl": floors[floor_grid]["net_pnl"],
        "robustness_stress_floor_grid": floor_grid,
        "neighbourhood": {"same_sign_fraction": num(neighbourhood.get("same_sign_fraction")),
                          "passed": neighbourhood.get("passed"),
                          "neighbours": num(neighbourhood.get("neighbours")),
                          "agreeing": num(neighbourhood.get("agreeing"))},
    }


def checksum_problems(bundle, label, problems):
    """The bundle's own identity, and the source checksums the index pins the cutoff to."""
    declared = bundle.get("bundle_identity_sha256")
    if not declared:
        problems.append("%s: bundle publishes no bundle_identity_sha256 (missing checksum): a "
                        "survivor whose frozen identity cannot be checked is never indexed"
                        % label)
    elif bundle_identity(bundle) != declared:
        problems.append("%s: bundle is invalid - it publishes bundle_identity_sha256 %r but the "
                        "contract 10.8 recipe (canonical JSON minus generated_at_utc and "
                        "bundle_identity_sha256) recomputes %r"
                        % (label, declared, bundle_identity(bundle)))
    sources = bundle.get("source_artifacts")
    if not isinstance(sources, dict) or not sources:
        problems.append("%s: bundle carries no source_artifacts checksum map" % label)
    else:
        bad = sorted(k for k, v in sources.items()
                     if not (isinstance(v, str) and v.startswith("sha256:") and len(v) == 71))
        if bad:
            problems.append("%s: source_artifacts has malformed checksum(s) for %r" % (label, bad))
    if bundle.get("kind") != BUNDLE_KIND:
        problems.append("%s: kind %r is not %r" % (label, bundle.get("kind"), BUNDLE_KIND))


def research_cutoff(bundle, round_dir, label, problems):
    """The round's registered research data end, verified against the checksum the bundle froze.

    The cutoff decides what counts as post-freeze evidence (contract 27.3), so it may not be
    read from an unverified file: the value is only accepted when the `round-spec.json` on disk
    still hashes to the checksum the frozen bundle recorded for it.
    """
    spec_path = os.path.join(round_dir, "round-spec.json")
    declared = (bundle.get("source_artifacts") or {}).get("round-spec.json")
    if not declared:
        problems.append("%s: the bundle recorded no round-spec.json checksum, so the research "
                        "data cutoff cannot be trusted" % label)
        return None
    if not os.path.isfile(spec_path):
        problems.append("%s: round-spec.json is missing at %s" % (label, spec_path))
        return None
    actual = sha256_file(spec_path)
    if actual != declared:
        problems.append("%s: round-spec.json does not hash to the checksum the bundle recorded "
                        "(bundle %s, on disk %s): refusing to derive the research data cutoff "
                        "from an inconsistent source" % (label, declared, actual))
        return None
    data = load_json(spec_path).get("data")
    cutoff = None
    if isinstance(data, dict):
        legacy_cutoff = data.get("data_end")
        current_cutoff = data.get("end")
        if legacy_cutoff and current_cutoff and legacy_cutoff != current_cutoff:
            problems.append("%s: round-spec.json data.data_end %r disagrees with data.end %r"
                            % (label, legacy_cutoff, current_cutoff))
            return None
        cutoff = legacy_cutoff or current_cutoff
    if not isinstance(cutoff, str) or not cutoff:
        problems.append("%s: round-spec.json has no data.data_end or data.end (research data cutoff)"
                        % label)
        return None
    return cutoff


def challenger_problems(family, cutoff_spec, label, problems):
    """Contract 27.4: a challenger's new OOS window starts after its preregistration cutoff."""
    challenger_of = family.get("challenger_of")
    if not challenger_of:
        return None
    created = family.get("created_at_utc")
    oos_start = (cutoff_spec or {}).get("data", {}).get("oos_start") if isinstance(cutoff_spec, dict) else None
    if not isinstance(created, str) or not isinstance(oos_start, str):
        problems.append("%s: challenger_of is set but family.created_at_utc / round-spec "
                        "data.oos_start is missing, so the post-preregistration rule cannot be "
                        "checked" % label)
        return challenger_of
    if oos_start <= created[:10]:
        problems.append("%s: challenger OOS start %s is not after the challenger's "
                        "preregistration cutoff %s - data already seen at retune time must never "
                        "be re-labelled OOS/forward (contract 27.4)" % (label, oos_start, created[:10]))
    return challenger_of


def entries_for_bundle(bundle_path, problems):
    """Every survivor of one frozen bundle, or [] with the reasons appended."""
    label = os.path.relpath(bundle_path, os.path.dirname(bundle_path))
    round_dir = os.path.dirname(bundle_path)
    family_dir = os.path.dirname(os.path.dirname(round_dir))
    family_id = os.path.basename(family_dir)
    round_id = os.path.basename(round_dir)
    label = "%s/%s" % (family_id, round_id)

    try:
        bundle = load_json(bundle_path)
    except (OSError, ValueError) as exc:
        problems.append("%s: bundle is unreadable/unparsable (%s)" % (label, exc))
        return []
    if not isinstance(bundle, dict):
        problems.append("%s: bundle is not an object" % label)
        return []

    checksum_problems(bundle, label, problems)
    if bundle.get("family_id") != family_id:
        problems.append("%s: bundle family_id %r disagrees with its directory name %r"
                        % (label, bundle.get("family_id"), family_id))
    if bundle.get("round_id") != round_id:
        problems.append("%s: bundle round_id %r disagrees with its directory name %r"
                        % (label, bundle.get("round_id"), round_id))

    family_path = os.path.join(family_dir, "family.json")
    family = {}
    if not os.path.isfile(family_path):
        problems.append("%s: family.json is missing at %s (ownership/lineage cannot be checked)"
                        % (label, family_path))
    else:
        try:
            family = load_json(family_path)
        except (OSError, ValueError) as exc:
            problems.append("%s: family.json is unreadable/unparsable (%s)" % (label, exc))
        if isinstance(family, dict):
            if family.get("family_id") != family_id:
                problems.append("%s: family.json family_id %r disagrees with the directory name"
                                % (label, family.get("family_id")))
            # Contract 27.2 item 4 / 22 A28(3): ownership must be present, a non-empty string and
            # equal on both sides.  Comparing only when BOTH sides are truthy fails open on a
            # bundle that carries no `kanban_task_id` at all (audit finding F3), which is exactly
            # the shape an unverifiable source takes - and a non-string id (number/bool/list) that
            # happens to match on both sides is not provenance either: the pipeline's ownership id
            # is a Kanban card id, so anything but a non-empty string is source inconsistency.
            family_task = family.get("kanban_task_id")
            bundle_task = bundle.get("kanban_task_id")
            if not (isinstance(family_task, str) and family_task.strip()
                    and isinstance(bundle_task, str) and bundle_task.strip()):
                problems.append("%s: source ownership is incomplete - kanban_task_id is not a "
                                "non-empty string on both sides (family.json %r, bundle %r); a "
                                "missing, empty, numeric, boolean, list or null ownership id is "
                                "source inconsistency and is never indexed, even when both sides "
                                "carry the very same JSON value (contract 27.2 item 4 / 22 A28)"
                                % (label, family_task, bundle_task))
            elif family_task != bundle_task:
                problems.append("%s: kanban_task_id mismatch: family.json %r != bundle %r"
                                % (label, family_task, bundle_task))
        else:
            problems.append("%s: family.json is not an object" % label)

    spec_path = os.path.join(round_dir, "round-spec.json")
    spec = {}
    if os.path.isfile(spec_path):
        try:
            spec = load_json(spec_path)
        except (OSError, ValueError):
            spec = {}
    cutoff = research_cutoff(bundle, round_dir, label, problems)
    challenger_of = challenger_problems(family if isinstance(family, dict) else {}, spec, label,
                                        problems)

    # Load the parameter contract: generic families carry it in the round-spec; legacy A v2
    # uses the in-code bridge.  Unknown families without a contract fail closed.
    contract, contract_problems, _is_legacy = pc.load_contract_from_round_spec(spec)
    if contract_problems:
        for cp in contract_problems:
            problems.append("%s: %s" % (label, cp))

    survivors = bundle.get("survivors")
    if not isinstance(survivors, list):
        problems.append("%s: survivors is not a list" % label)
        return []
    if not survivors:
        if bundle.get("verdict") == "PASS":
            problems.append("%s: verdict is PASS but the bundle holds no survivor" % label)
        return []

    entries = []
    for rec in survivors:
        if not isinstance(rec, dict):
            problems.append("%s: survivor record is not an object" % label)
            continue
        cohort = rec.get("cohort")
        if not isinstance(cohort, str) or "/" not in cohort:
            problems.append("%s: survivor cohort %r is not a SYMBOL/TIMEFRAME pair"
                            % (label, cohort))
            continue
        symbol, timeframe = cohort.split("/", 1)
        if not symbol or not timeframe:
            problems.append("%s: survivor cohort %r is not a SYMBOL/TIMEFRAME pair"
                            % (label, cohort))
            continue
        cell_label = "%s %s" % (label, cohort)
        strategy, dca = param_cell(rec.get("winner"), cell_label, problems, contract=contract)
        evidence = metrics_evidence(rec, cell_label, problems)
        if strategy is None or evidence is None:
            continue
        identity = identity_payload(bundle.get("family_id"), bundle.get("round_id"),
                                   bundle.get("run_id"), bundle.get("bundle_identity_sha256"),
                                   cohort, strategy, dca)
        entries.append({
            "survivor_id": survivor_id(identity),
            "family_id": bundle.get("family_id"),
            "round_id": bundle.get("round_id"),
            "run_id": bundle.get("run_id"),
            "kanban_task_id": bundle.get("kanban_task_id"),
            "cohort": cohort,
            "symbol": symbol,
            "timeframe": timeframe,
            "challenger_of": challenger_of,
            "strategy_params": strategy,
            "dca_params": dca,
            "params_sha256": digest({"strategy_params": strategy, "dca_params": dca}),
            "research_data_cutoff": cutoff,
            "bundle_path": os.path.abspath(bundle_path),
            "bundle_sha256": sha256_file(bundle_path),
            "bundle_identity_sha256": bundle.get("bundle_identity_sha256"),
            "source_disposition_band": bundle.get("disposition_band"),
            "source_verdict": bundle.get("verdict"),
            **evidence,
        })
    return entries


def build(results_root, bundle_list=None):
    """Rebuild the index from the bundles on disk.  Returns (index, problems)."""
    problems = []
    results_root = os.path.abspath(results_root)
    if not os.path.isdir(results_root):
        return None, ["results root does not exist: %s" % results_root]

    paths = bundle_paths(results_root) if bundle_list is None else list(bundle_list)
    by_id = {}
    skipped = []
    for path in paths:
        before = len(problems)
        entries = entries_for_bundle(path, problems)
        for entry in entries:
            previous = by_id.get(entry["survivor_id"])
            if previous is not None:
                problems.append("duplicate survivor_id %s: %s and %s claim the same frozen "
                                "survivor identity (family/round/run/cohort/params/bundle "
                                "identity); a duplicate is refused, never merged"
                                % (entry["survivor_id"], previous["bundle_path"],
                                   entry["bundle_path"]))
                continue
            by_id[entry["survivor_id"]] = entry
        if len(problems) == before and not entries:
            skipped.append({"bundle_path": os.path.abspath(path),
                            "reason": "the round produced no cohort survivor"})

    if problems:
        return None, problems

    ordered = [by_id[key] for key in sorted(by_id)]
    index = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md %s" % CONTRACT_VERSION,
        "contract_section": CONTRACT_SECTION,
        "results_root": results_root,
        "derived": True,
        "source_of_truth": ("the frozen survivor bundles under this root "
                            "(<family_id>/rounds/<round_id>/survivor-bundle.json); this index is "
                            "a derived, rebuildable artifact and never a gate"),
        "ordering": ("survivor_id ascending, deterministic across hosts and rebuilds; this is "
                     "NOT a ranking"),
        "bundle_count": len(paths),
        "survivor_count": len(ordered),
        "skipped_bundles": skipped,
        "survivors": ordered,
        GENERATED_KEY: now_utc(),
    }
    return index, []


def measured(index):
    """The index minus its generation timestamp: the content a rebuild must reproduce."""
    return {k: v for k, v in index.items() if k != GENERATED_KEY}


def write_index(index, out_path):
    text = json.dumps(index, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if os.path.exists(out_path):
        with open(out_path) as fh:
            if fh.read() == text:
                return "unchanged"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)
    return "written"


def main(argv=None):
    ap = argparse.ArgumentParser(description="file-only frozen-survivor index (contract v1.5.2)")
    ap.add_argument("--results-root", default=DEFAULT_RESULTS_ROOT,
                    help="the durable results root (default: %(default)s)")
    ap.add_argument("--out", default=None,
                    help="index path (default: <results-root>/_survivors/survivor-index.json)")
    ap.add_argument("--check", action="store_true",
                    help="rebuild and compare with the existing index; write nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.results_root)
    out_path = os.path.abspath(args.out) if args.out else index_path(root)
    escape = outside_write_boundary(root, out_path)
    if escape:
        sys.stderr.write("REFUSED: %s\n" % escape)
        return 1

    index, problems = build(root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    assert index is not None  # build() returns problems or an index, never neither

    if args.check:
        if not os.path.exists(out_path):
            sys.stderr.write("REFUSED: no index at %s\n" % out_path)
            return 1
        existing = load_json(out_path)
        if measured(existing) != measured(index):
            sys.stderr.write("REFUSED: %s is not the rebuild of the bundles on disk (it is a "
                             "derived artifact: rebuild it)\n" % out_path)
            return 1
        result = "check_clean"
    else:
        result = write_index(index, out_path)

    payload = {"ok": True, "result": result, "index_path": out_path,
               "survivor_count": index["survivor_count"], "bundle_count": index["bundle_count"],
               "survivors": [{"survivor_id": s["survivor_id"], "cohort": s["cohort"],
                              "params_sha256": s["params_sha256"],
                              "research_data_cutoff": s["research_data_cutoff"]}
                             for s in index["survivors"]],
               "index_sha256": digest(measured(index))}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("index=%s result=%s bundles=%d survivors=%d"
              % (out_path, result, index["bundle_count"], index["survivor_count"]))
        for s in index["survivors"]:
            print("  %s  %-14s cutoff=%s params=%s"
                  % (s["survivor_id"], s["cohort"], s["research_data_cutoff"],
                     s["params_sha256"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
