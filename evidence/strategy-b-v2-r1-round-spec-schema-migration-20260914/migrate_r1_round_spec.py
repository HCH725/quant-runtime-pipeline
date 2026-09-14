#!/usr/bin/env python3
"""One-time additive schema migration of the frozen B v2 r1 round-spec (contract v1.8 / section 26.1).

Card t_67481d49 (ChatGPT GPT-5.6 Sol).  The frozen round-spec of family
`ema-crossover-walkforward-momentum-long-short-v2` round `...-r1` was instantiated from the
v1.4.0 template and therefore carries no generic `parameter_contract`; every v1.8 non-legacy
post-survivor consumer fails closed on it, which is the only blocker of the r1-u3 launch card.

The §26.1 exception authorizes exactly one write to that file: append one top-level key
`parameter_contract`, generated verbatim from the round-spec's OWN registered
`parameter_domain` / `dca_domain`.  Nothing else may change, and the write must be provable:

  * before/after sha256 of the file bytes;
  * a text-level proof: every original byte except the final "}\n" is preserved verbatim
    (the write is a splice, never a re-serialisation) and the written text re-parses to the
    intended document;
  * a canonical-JSON proof: every pre-existing top-level/nested key and value, canonicalised,
    is identical before and after;
  * the validators at zero problems (`validate_contract`, `validate_round_spec_contract`);
  * the counts tool: zero problems and fingerprint MATCH;
  * an unchanged manifest of every sibling artifact (attempts/**, family.json).

This tool is the migration and its proof.  It never touches the u1/u2 attempts, never
publishes a run-spec and never launches anything.

The exception covers exactly ONE document: family
`ema-crossover-walkforward-momentum-long-short-v2`, round `...-r1`, at its canonical path under
the results root.  A wrong family, a wrong round or any other path is refused (rc=1) before a
single byte is written; --emit and --dry-run are held to the same scope.

usage:
  python3 migrate_r1_round_spec.py --spec <round-spec.json> --emit
  python3 migrate_r1_round_spec.py --spec <round-spec.json> --template <template.json> --dry-run
  python3 migrate_r1_round_spec.py --spec <round-spec.json> --template <template.json> \
                                   --evidence <evidence.json> [--json]
exit: 0 = ok / already_migrated, 1 = refused, 2 = usage error
"""
import argparse
import hashlib
import itertools
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "runtime"))
import parameter_contract as pc  # noqa: E402
import strategy_b_v2_counts as counts  # noqa: E402

# registered composite axes of this family -> (row column names, keys inside the declared cells)
COMPOSITE_AXES = (
    ("ema_pair", ("ema_fast", "ema_slow"), ("fast", "slow")),
    ("walk_forward", ("wf_train_days", "wf_test_days"), ("train_days", "test_days")),
)
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
CONTRACT_REF = ("v1.8 generic family parameter contract (runtime/parameter_contract.py); generated "
                "from this document's registered parameter_domain / dca_domain")
NON_PARAMS = ["symbol", "timeframe", "window_kind", "ema_pair_index", "walk_forward_index",
              "base_quote", "n_steps", "diagnostics", "metrics"]
DOCUMENT_TAIL = "}\n"

# §26.1 authorizes exactly ONE file: this family, this round, this canonical path.  Anything
# else (other family, other round, copy at another path) is out of scope and must be refused
# before any write.  Path identity is lexical: a symlink alias that resolves to the canonical
# file is another path too, so the guard never resolves symlinks.
MIGRATION_FAMILY_ID = "ema-crossover-walkforward-momentum-long-short-v2"
MIGRATION_ROUND_ID = MIGRATION_FAMILY_ID + "-r1"
MIGRATION_SPEC_PATH = ("/Volumes/ExpansionDrive/qlib-results/%s/rounds/%s/round-spec.json"
                       % (MIGRATION_FAMILY_ID, MIGRATION_ROUND_ID))


def scope_problems(spec_path, spec):
    """§26.1 scope guard: family, round and the sole canonical target path, all fail-closed.

    Returns the reasons the document is NOT the one authorized round-spec.  A non-empty list
    means: refuse (rc=1) and write nothing at all.

    Path identity is lexical (auditor finding F1): the argument must BE the canonical path,
    spelled the same way.  os.path.abspath only normalizes the spelling -- it never resolves
    symlinks -- so an alias that merely points at the canonical file is out of scope.
    """
    problems = []
    if os.path.abspath(spec_path) != MIGRATION_SPEC_PATH:
        problems.append("out of scope (contract 26.1): %s is not the one authorized round-spec "
                        "%s (path identity is lexical, never a symlink resolution)"
                        % (spec_path, MIGRATION_SPEC_PATH))
    if spec.get("family_id") != MIGRATION_FAMILY_ID:
        problems.append("out of scope (contract 26.1): family_id %r is not %r"
                        % (spec.get("family_id"), MIGRATION_FAMILY_ID))
    if spec.get("round_id") != MIGRATION_ROUND_ID:
        problems.append("out of scope (contract 26.1): round_id %r is not %r"
                        % (spec.get("round_id"), MIGRATION_ROUND_ID))
    return problems


def sha256_bytes(blob):
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def sha256_file(path):
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def generate_contract(spec):
    """Build the parameter_contract verbatim from the document's own registered domains.

    Raises ValueError when the declared domains are not the registered shapes, so a contract
    can never be generated from a domain the family did not register.
    """
    params = spec["parameter_domain"]
    dca = spec["dca_domain"]
    axes = []
    for name, row_fields, cell_keys in COMPOSITE_AXES:
        entries = params.get(name)
        if not isinstance(entries, list) or not entries:
            raise ValueError("parameter_domain.%s is not a non-empty list" % name)
        for entry in entries:
            if sorted(entry) != sorted(cell_keys):
                raise ValueError("parameter_domain.%s cell keys %r are not %r"
                                 % (name, sorted(entry), sorted(cell_keys)))
        axes.append({"name": name, "kind": "composite", "members": list(row_fields),
                     "registered_values": [[e[k] for k in cell_keys] for e in entries],
                     "row_fields": list(row_fields)})
    legal_pairs = sorted(tuple(p) for p in itertools.product(params["ema_fast"], params["ema_slow"])
                         if p[0] < p[1])
    declared_pairs = sorted((e["fast"], e["slow"]) for e in params["ema_pair"])
    if legal_pairs != declared_pairs:
        raise ValueError("parameter_domain.ema_pair is not the legal product of ema_fast x "
                         "ema_slow (declared %d, legal %d)"
                         % (len(declared_pairs), len(legal_pairs)))
    if any(int(c["train_days"]) != int(c["test_days"]) for c in params["walk_forward"]):
        raise ValueError("parameter_domain.walk_forward carries a non-diagonal cell")
    for name in DCA_AXES:
        values = dca.get(name)
        if not isinstance(values, list) or not values:
            raise ValueError("dca_domain.%s is not a non-empty list" % name)
        axes.append({"name": name, "kind": "atomic", "members": [name],
                     "registered_values": list(values), "row_fields": [name]})
    row_fields = [field for axis in axes for field in axis["row_fields"]]
    composite_map = {axis["name"]: list(axis["members"]) for axis in axes
                     if axis["kind"] == "composite"}
    strategy_fields = [field for axis in axes if axis["kind"] == "composite"
                       for field in axis["row_fields"]]
    dca_fields = [field for axis in axes if axis["kind"] == "atomic"
                  for field in axis["row_fields"]]
    strategy_card = 1
    for axis in axes:
        if axis["kind"] == "composite":
            strategy_card *= len(axis["registered_values"])
    dca_card = 1
    for axis in axes:
        if axis["kind"] == "atomic":
            dca_card *= len(axis["registered_values"])
    return {
        "parameter_contract_version": pc.PARAMETER_CONTRACT_VERSION,
        "family_id": spec["family_id"],
        "contract_ref": CONTRACT_REF,
        "research_axes_ordered": axes,
        "row_fields": row_fields,
        "composite_map": composite_map,
        "strategy_param_fields": strategy_fields,
        "dca_param_fields": dca_fields,
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"], "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + row_fields,
                             "equality": "exact, numeric == float compare, rest bytewise"},
        "non_params": list(NON_PARAMS),
        "domain_cardinality": {"strategy": strategy_card, "dca": dca_card,
                               "per_cohort": strategy_card * dca_card},
    }


def splice_append(document_text, key, value):
    """Append one top-level key to a pretty-printed JSON document without re-serialising it.

    Returns (new_text, proof).  The proof states, in bytes: which part of the original text was
    preserved verbatim, what was inserted, and that the result still parses to the intended
    document.  Raises ValueError when the document is not in the expected closed form.
    """
    if not document_text.endswith(DOCUMENT_TAIL):
        raise ValueError("document does not end with the expected %r" % DOCUMENT_TAIL)
    head = document_text[:-len(DOCUMENT_TAIL)]           # everything before the final brace
    if not head.endswith("\n"):
        raise ValueError("document closing brace is not on its own line")
    block = json.dumps(value, indent=2, ensure_ascii=False)
    indented = block.replace("\n", "\n  ")               # depth 2, like every other top-level key
    inserted = ",\n  %s: %s\n" % (json.dumps(key), indented)
    new_text = head + inserted + DOCUMENT_TAIL
    return new_text, {
        "preserved_original_bytes": len(head),
        "original_bytes": len(document_text),
        "inserted_bytes": len(inserted),
        "inserted_region_is_a_single_top_level_key": True,
        "inserted_region_starts_with_the_json_comma_separator": inserted.startswith(","),
        "original_prefix_preserved": new_text.startswith(head),
        "original_tail_preserved": new_text.endswith(DOCUMENT_TAIL),
        "original_bytes_rewritten": 0,
    }


def tree_manifest(root, exclude=()):
    """{relpath: sha256} for every regular file under *root* except *exclude* (relpaths)."""
    out = {}
    for base, _dirs, names in os.walk(root):
        for name in sorted(names):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            if rel in exclude:
                continue
            out[rel] = sha256_file(path)
    return out


def main():
    ap = argparse.ArgumentParser(description="one-time additive schema migration (contract 26.1)")
    ap.add_argument("--spec", required=True, help="round-spec to migrate")
    ap.add_argument("--template", default=None, help="round-spec template the block must equal")
    ap.add_argument("--emit", action="store_true", help="print the generated contract and exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--evidence", default=None, help="where to write the evidence JSON")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    spec_path = os.path.abspath(args.spec)
    record = {"schema_version": 1, "kind": "b_v2_r1_round_spec_schema_migration",
              "contract_section": "26.1", "card": "t_67481d49",
              "ran_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "spec": spec_path, "dry_run": args.dry_run, "problems": []}
    try:
        with open(spec_path) as fh:
            original_text = fh.read()
        spec = json.loads(original_text)
    except (OSError, ValueError) as exc:
        sys.stderr.write("usage error: cannot read %s: %s\n" % (spec_path, exc))
        return 2

    scope = scope_problems(spec_path, spec)
    if scope:
        record["family_id"] = spec.get("family_id")
        record["round_id"] = spec.get("round_id")
        record["problems"] += scope
        return finish(record, args, 1)

    try:
        contract = generate_contract(spec)
    except (KeyError, ValueError, TypeError) as exc:
        record["problems"].append("cannot generate the contract from the registered domains: %s"
                                  % exc)
        return finish(record, args, 1)
    if args.emit:
        print(json.dumps(contract, indent=2, ensure_ascii=False))
        return 0

    record["family_id"] = spec.get("family_id")
    record["round_id"] = spec.get("round_id")
    record["generated_contract"] = contract

    problems = pc.validate_contract(contract)
    record["validators"] = {"validate_contract": problems}
    if problems:
        record["problems"].append("generated contract is not valid: %s" % problems)
        return finish(record, args, 1)

    existing = spec.get("parameter_contract")
    if existing is not None:
        if existing == contract:
            record["action"] = "already_migrated"
            record["after_sha256"] = sha256_file(spec_path)
            return finish(record, args, 0)
        record["problems"].append("target already carries a DIFFERENT parameter_contract: a "
                                  "second migration is never applied (contract 26.1)")
        return finish(record, args, 1)

    migrated = dict(spec)
    migrated["parameter_contract"] = contract
    round_problems = pc.validate_round_spec_contract(migrated)
    record["validators"]["validate_round_spec_contract"] = round_problems
    if round_problems:
        record["problems"].append("migrated round-spec does not validate: %s" % round_problems)
        return finish(record, args, 1)

    computed, count_problems, extra = counts.check(migrated, None)
    record["counts"] = {
        "ok": not count_problems, "problems": count_problems,
        "computed": {k: computed[k] for k in ("cohorts", "strategy_cases", "dca_configs",
                                              "base_combinations_per_cohort",
                                              "case_evaluations_per_grid",
                                              "expected_case_evaluations")},
        "fingerprint_declared": extra["fingerprint_declared"],
        "fingerprint_computed": extra["fingerprint_computed"],
        "fingerprint_match": extra["fingerprint_declared"] == extra["fingerprint_computed"]}
    if count_problems:
        record["problems"].append("counts check of the migrated document failed: %s"
                                  % count_problems)
    if not record["counts"]["fingerprint_match"]:
        record["problems"].append("semantic fingerprint does not match after migration")

    try:
        new_text, splice_proof = splice_append(original_text, "parameter_contract", contract)
    except ValueError as exc:
        record["problems"].append("cannot splice the key into the frozen file: %s" % exc)
        return finish(record, args, 1)
    try:
        reparsed = json.loads(new_text)
    except ValueError as exc:
        record["problems"].append("written text does not parse: %s" % exc)
        return finish(record, args, 1)

    before_sha = sha256_bytes(original_text.encode())
    after_sha = sha256_bytes(new_text.encode())
    record["before_sha256"] = before_sha
    record["after_sha256"] = after_sha
    record["preservation"] = {
        "splice": splice_proof,
        "added_top_level_keys": sorted(set(migrated) - set(spec)),
        "removed_top_level_keys": sorted(set(spec) - set(migrated)),
        "changed_existing_keys": sorted(k for k in spec if k in migrated and migrated[k] != spec[k]),
        "canonical_json_of_remaining_keys_equal": pc.canonical(
            {k: v for k, v in migrated.items() if k != "parameter_contract"}) == pc.canonical(spec),
        "written_text_parses_to_the_intended_document": reparsed == migrated,
        "bytes_before": len(original_text), "bytes_after": len(new_text),
    }
    p = record["preservation"]
    if p["added_top_level_keys"] != ["parameter_contract"] or p["removed_top_level_keys"] \
            or p["changed_existing_keys"] or not p["canonical_json_of_remaining_keys_equal"] \
            or not p["written_text_parses_to_the_intended_document"]:
        record["problems"].append("field-preservation proof failed: %r" % p)

    # the block must be exactly the generation of the same registered domains used by the
    # forward-authored template, and the template block must equal it
    if args.template:
        template_path = os.path.abspath(args.template)
        template = None
        template_generated = None
        try:
            with open(template_path) as fh:
                template = json.load(fh)
            template_generated = generate_contract(template)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record["problems"].append("cannot generate from the template domains: %s" % exc)
        if template is not None and template_generated is not None:
            record["template_cross_check"] = {
                "template": template_path,
                "template_contract_equals_generated": template.get("parameter_contract")
                == template_generated,
                "generated_equals_migration_contract": template_generated == contract,
                "template_contract_sha256": sha256_bytes(
                    pc.canonical(template.get("parameter_contract")).encode()),
                "migration_contract_sha256": sha256_bytes(pc.canonical(contract).encode()),
            }
            if not all(record["template_cross_check"][k] for k in
                       ("template_contract_equals_generated",
                        "generated_equals_migration_contract")):
                record["problems"].append("template cross-check failed: %r"
                                          % record["template_cross_check"])

    # every sibling artifact must be untouched by the write (attempts/**, family.json, ...)
    round_dir = os.path.dirname(spec_path)
    family_dir = os.path.dirname(os.path.dirname(round_dir))
    attempts_dir = os.path.join(round_dir, "attempts")
    manifest_before = tree_manifest(round_dir, exclude=(os.path.basename(spec_path),))
    family_before = sha256_file(os.path.join(family_dir, "family.json"))
    attempt_pins_before = {}
    for name in sorted(os.listdir(attempts_dir)):
        for fname in ("run-spec.json", "FAILED", "state.json"):
            path = os.path.join(attempts_dir, name, fname)
            if os.path.isfile(path):
                attempt_pins_before["%s/%s" % (name, fname)] = sha256_file(path)
    record["sibling_immutability"] = {
        "round_dir": round_dir, "attempts": sorted(os.listdir(attempts_dir)),
        "files": len(manifest_before),
        "attempt_pins_before": attempt_pins_before,
        "family_json_sha256_before": family_before,
    }

    if record["problems"]:
        return finish(record, args, 1)

    if not args.dry_run:
        tmp = spec_path + ".migrate-%d.tmp" % os.getpid()
        with open(tmp, "w") as fh:
            fh.write(new_text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, spec_path)
        on_disk = sha256_file(spec_path)
        with open(spec_path) as fh:
            readback_text = fh.read()
        readback = json.loads(readback_text)
        manifest_after = tree_manifest(round_dir, exclude=(os.path.basename(spec_path),))
        attempt_pins_after = {}
        for name in sorted(os.listdir(attempts_dir)):
            for fname in ("run-spec.json", "FAILED", "state.json"):
                path = os.path.join(attempts_dir, name, fname)
                if os.path.isfile(path):
                    attempt_pins_after["%s/%s" % (name, fname)] = sha256_file(path)
        record["readback"] = {
            "sha256": on_disk, "sha256_matches": on_disk == after_sha,
            "bytes_identical_to_planned_text": readback_text == new_text,
            "validate_contract": pc.validate_contract(readback.get("parameter_contract")),
            "validate_round_spec_contract": pc.validate_round_spec_contract(readback),
        }
        s = record["sibling_immutability"]
        s["files"] = len(manifest_after)
        s["attempt_pins_after"] = attempt_pins_after
        s["attempt_pins_equal"] = attempt_pins_before == attempt_pins_after
        s["sibling_manifests_equal"] = manifest_before == manifest_after
        s["family_json_sha256_after"] = sha256_file(os.path.join(family_dir, "family.json"))
        s["family_json_equal"] = s["family_json_sha256_before"] == s["family_json_sha256_after"]
        if not record["readback"]["sha256_matches"] \
                or not record["readback"]["bytes_identical_to_planned_text"] \
                or record["readback"]["validate_contract"] \
                or record["readback"]["validate_round_spec_contract"] \
                or not s["attempt_pins_equal"] or not s["sibling_manifests_equal"] \
                or not s["family_json_equal"]:
            record["problems"].append("post-write read-back / sibling immutability failed")

    # any earlier evidence record that quoted the pre-migration sha stays a historical snapshot
    needle = before_sha.replace("sha256:", "").encode()
    superseded = []
    for base, _dirs, names in os.walk(os.path.join(REPO_ROOT, "evidence")):
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(base, name)
            try:
                with open(path, "rb") as fh:
                    if needle in fh.read():
                        superseded.append(os.path.relpath(path, REPO_ROOT))
            except OSError:
                continue
    record["superseded_sha_records"] = sorted(superseded)
    record["action"] = "dry_run_verified" if args.dry_run else "migrated"
    return finish(record, args, 0)


def finish(record, args, rc):
    if args.evidence:
        with open(args.evidence, "w") as fh:
            fh.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        print("migration: %s%s" % (record.get("action", "refused"),
                                   "" if not record["problems"] else
                                   " (%d problem(s))" % len(record["problems"])))
        for p in record["problems"]:
            print("PROBLEM: %s" % p)
        print("before=%s after=%s" % (record.get("before_sha256"), record.get("after_sha256")))
    return 0 if not record["problems"] and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
