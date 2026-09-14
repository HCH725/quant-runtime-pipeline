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

    def build_round_copy(self, root, round_id):
        """A migratable copy of the live round-spec, at a temporary path, labelled *round_id*."""
        doc = json.loads(Path(CANONICAL).read_text())
        doc.pop("parameter_contract", None)
        doc["round_id"] = round_id
        family_dir = root / MIG.MIGRATION_FAMILY_ID
        round_dir = family_dir / "rounds" / round_id
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
            spec = self.build_round_copy(root, MIG.MIGRATION_FAMILY_ID + "-r99")
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
            spec = self.build_round_copy(root, MIG.MIGRATION_FAMILY_ID + "-r99")
            rc, record = self.run_migrator(spec, extra=("--emit",))
            self.assertEqual(rc, 1, record)
            self.assertIn("out of scope", " ".join(record.get("problems") or []))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # --- F1 regression: an alias that RESOLVES to the canonical spec is still out of scope ---
    @unittest.skipUnless(os.path.exists(CANONICAL), "canonical r1 round-spec not mounted")
    def test_cli_refuses_a_symlink_alias_to_the_canonical_spec(self):
        """Path identity is lexical, never realpath (auditor finding F1).

        The bypass: a symlink at another path pointing at the canonical round-spec.  Resolving
        symlinks made the guard accept it and os.replace() then wrote the alias, so the fixture
        here must be refused (rc=1, no action) with zero writes -- bytes AND metadata -- to
        either file.  In this process only, MIGRATION_SPEC_PATH is pointed at the temp canonical
        copy so the alias really does resolve to "the" canonical document; the live tree is
        never touched.
        """
        root = Path(tempfile.mkdtemp(prefix="qrp-migration-symlink-"))
        try:
            canonical = self.build_round_copy(root / "canonical", MIG.MIGRATION_ROUND_ID)
            alias_root = root / "alias"
            alias = (alias_root / MIG.MIGRATION_FAMILY_ID / "rounds" / MIG.MIGRATION_ROUND_ID
                     / "round-spec.json")
            alias.parent.mkdir(parents=True)
            (alias.parent / "attempts").mkdir()
            shutil.copy2(Path(CANONICAL).parent.parent.parent / "family.json",
                         alias_root / MIG.MIGRATION_FAMILY_ID / "family.json")
            alias.symlink_to(canonical)

            canonical_bytes, alias_bytes = canonical.read_bytes(), alias.read_bytes()
            canonical_meta, alias_meta = os.stat(canonical), os.lstat(alias)

            had_constant = hasattr(MIG, "MIGRATION_SPEC_PATH")
            saved_constant = getattr(MIG, "MIGRATION_SPEC_PATH", None)
            setattr(MIG, "MIGRATION_SPEC_PATH", str(canonical))
            try:
                rc, record = self.run_migrator(alias)
            finally:
                if had_constant:
                    setattr(MIG, "MIGRATION_SPEC_PATH", saved_constant)
                else:
                    delattr(MIG, "MIGRATION_SPEC_PATH")

            self.assertEqual(rc, 1, record)
            self.assertIsNone(record.get("action"), record)
            problems = " ".join(record.get("problems") or [])
            self.assertIn("out of scope", problems)
            self.assertIn("26.1", problems)

            # zero writes: the alias is still the very same symlink, byte- and inode-identical
            self.assertTrue(os.path.islink(alias), "the alias must still be a symlink")
            self.assertEqual(os.readlink(alias), str(canonical))
            alias_after = os.lstat(alias)
            self.assertEqual((alias_after.st_ino, alias_after.st_mtime_ns),
                             (alias_meta.st_ino, alias_meta.st_mtime_ns))
            self.assertEqual(alias.read_bytes(), alias_bytes)
            self.assertNotIn("parameter_contract", json.loads(alias.read_text()),
                             "wrote_contract must be False: the alias carries no contract")
            # ... and so is the canonical target it points at
            canonical_after = os.stat(canonical)
            self.assertEqual((canonical_after.st_ino, canonical_after.st_mtime_ns),
                             (canonical_meta.st_ino, canonical_meta.st_mtime_ns))
            self.assertEqual(canonical.read_bytes(), canonical_bytes)
            self.assertNotIn("parameter_contract", json.loads(canonical.read_text()))
            for round_dir in (canonical.parent, alias.parent):
                self.assertEqual(sorted(p.name for p in round_dir.iterdir()),
                                 ["attempts", "round-spec.json"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
