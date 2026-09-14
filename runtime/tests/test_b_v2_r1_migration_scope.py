#!/usr/bin/env python3
"""§26.1 scope lock for the one-time r1 round-spec migration tool (card t_e2eca79c, finding F1).

The migration exception covers exactly ONE document: family
`ema-crossover-walkforward-momentum-long-short-v2`, round `...-r1`, at its canonical path under
the results root.  Any other family, any other round and any copy at another path must be
refused with rc=1 and zero bytes written.

Run: python3 runtime/tests/test_b_v2_r1_migration_scope.py   (stdlib unittest, no container)
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = (REPO_ROOT / "evidence" / "strategy-b-v2-r1-round-spec-schema-migration-20260914"
            / "migrate_r1_round_spec.py")
TEMPLATE = REPO_ROOT / "runtime" / "templates" / "strategy_b_v2_round_spec.template.json"


def load_migrator():
    spec = importlib.util.spec_from_file_location("migrate_r1_round_spec", MIGRATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MIG = load_migrator()
# The §26.1 document, spelled here too so this file can also run against a pre-guard migrator
# (RED proof) instead of dying at import; the module constants themselves are asserted in
# test_scope_constants_are_the_one_authorized_document.
AUTHORIZED_FAMILY = "ema-crossover-walkforward-momentum-long-short-v2"
CANONICAL = getattr(MIG, "MIGRATION_SPEC_PATH",
                    "/Volumes/ExpansionDrive/qlib-results/%s/rounds/%s-r1/round-spec.json"
                    % (AUTHORIZED_FAMILY, AUTHORIZED_FAMILY))


class MigrationScopeCase(unittest.TestCase):
    """Out-of-scope documents are refused before any write."""

    def scope(self, spec_path, spec):
        self.assertTrue(hasattr(MIG, "scope_problems"),
                        "the migrator exposes no 26.1 scope guard")
        return MIG.scope_problems(spec_path, spec)

    def run_migrator(self, spec_path, extra=()):
        argv = [str(MIGRATOR), "--spec", str(spec_path), "--template", str(TEMPLATE),
                "--json"] + list(extra)
        saved = sys.argv
        sys.argv = argv
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = MIG.main()
        finally:
            sys.argv = saved
        try:
            record = json.loads(out.getvalue())
        except ValueError:
            record = {"stdout": out.getvalue(), "stderr": err.getvalue()}
        return rc, record

    def build_r99_copy(self, root):
        """A migratable copy of the live round-spec, but round r99 at a temporary path."""
        doc = json.loads(Path(CANONICAL).read_text())
        doc.pop("parameter_contract", None)
        doc["round_id"] = MIG.MIGRATION_FAMILY_ID + "-r99"
        family_dir = root / MIG.MIGRATION_FAMILY_ID
        round_dir = family_dir / "rounds" / (MIG.MIGRATION_FAMILY_ID + "-r99")
        (round_dir / "attempts").mkdir(parents=True)
        shutil.copy2(Path(CANONICAL).parent.parent.parent / "family.json",
                     family_dir / "family.json")
        spec = round_dir / "round-spec.json"
        spec.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        return spec

    # --- unit level: the guard itself ---
    def test_scope_constants_are_the_one_authorized_document(self):
        for attr in ("MIGRATION_FAMILY_ID", "MIGRATION_ROUND_ID", "MIGRATION_SPEC_PATH"):
            self.assertTrue(hasattr(MIG, attr),
                            "the migrator exposes no %s: no 26.1 scope lock" % attr)
        self.assertEqual(MIG.MIGRATION_FAMILY_ID, AUTHORIZED_FAMILY)
        self.assertEqual(MIG.MIGRATION_ROUND_ID, AUTHORIZED_FAMILY + "-r1")
        self.assertEqual(MIG.MIGRATION_SPEC_PATH,
                         "/Volumes/ExpansionDrive/qlib-results/%s/rounds/%s/round-spec.json"
                         % (MIG.MIGRATION_FAMILY_ID, MIG.MIGRATION_ROUND_ID))

    def test_canonical_document_is_in_scope(self):
        doc = {"family_id": MIG.MIGRATION_FAMILY_ID, "round_id": MIG.MIGRATION_ROUND_ID}
        self.assertEqual(self.scope(CANONICAL, doc), [])

    def test_wrong_path_is_out_of_scope(self):
        doc = {"family_id": MIG.MIGRATION_FAMILY_ID, "round_id": MIG.MIGRATION_ROUND_ID}
        problems = self.scope("/tmp/elsewhere/round-spec.json", doc)
        self.assertTrue(any("out of scope" in p and "26.1" in p for p in problems), problems)

    def test_wrong_family_is_out_of_scope(self):
        doc = {"family_id": "some-other-family", "round_id": MIG.MIGRATION_ROUND_ID}
        problems = self.scope(CANONICAL, doc)
        self.assertTrue(any("family_id" in p for p in problems), problems)

    def test_wrong_round_is_out_of_scope(self):
        doc = {"family_id": MIG.MIGRATION_FAMILY_ID,
               "round_id": MIG.MIGRATION_FAMILY_ID + "-r99"}
        problems = self.scope(CANONICAL, doc)
        self.assertTrue(any("round_id" in p for p in problems), problems)

    # --- CLI level: the auditor's reproducer, now refused with zero writes ---
    @unittest.skipUnless(os.path.exists(CANONICAL), "canonical r1 round-spec not mounted")
    def test_cli_refuses_an_r99_copy_and_writes_no_byte(self):
        root = Path(tempfile.mkdtemp(prefix="qrp-migration-scope-"))
        try:
            spec = self.build_r99_copy(root)
            before = spec.read_bytes()
            rc, record = self.run_migrator(spec)
            self.assertEqual(rc, 1, record)
            self.assertNotEqual(record.get("action"), "migrated", record)
            problems = " ".join(record.get("problems") or [])
            self.assertIn("out of scope", problems)
            self.assertIn("26.1", problems)
            self.assertEqual(spec.read_bytes(), before)
            self.assertNotIn("parameter_contract", json.loads(spec.read_text()))
            self.assertEqual(sorted(p.name for p in spec.parent.iterdir()),
                             ["attempts", "round-spec.json"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    @unittest.skipUnless(os.path.exists(CANONICAL), "canonical r1 round-spec not mounted")
    def test_cli_emit_is_held_to_the_same_scope(self):
        root = Path(tempfile.mkdtemp(prefix="qrp-migration-scope-"))
        try:
            spec = self.build_r99_copy(root)
            rc, record = self.run_migrator(spec, extra=("--emit",))
            self.assertEqual(rc, 1, record)
            self.assertIn("out of scope", " ".join(record.get("problems") or []))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
