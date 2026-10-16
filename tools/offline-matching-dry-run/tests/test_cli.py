"""Real local CLI tests: safe errors, no overwrites, isolated imports and completion."""

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from helpers import CPF_ALFA, CPF_BETA, api, roster_data

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "run_dry_run.py"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="matching-contribution-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pages = self.root / "pages.json"
        self.roster = self.root / "roster.json"
        self.output = self.root / "review"
        self.pages.write_text(
            json.dumps(
                {"schema_version": 1, "pages": [CPF_ALFA, CPF_ALFA, CPF_BETA, "Sem identidade"]}
            ),
            encoding="utf-8",
        )
        data = roster_data()
        data["observed_at"] = datetime.now(UTC).isoformat()
        self.roster.write_text(json.dumps(data), encoding="utf-8")
        self.arguments = [
            "--pages",
            str(self.pages),
            "--roster",
            str(self.roster),
            "--account",
            "synthetic",
            "--company",
            "1",
            "--unit",
            "2",
            "--service",
            "3",
            "--document-type",
            "100",
            "--competence",
            "2026-09",
            "--output",
            str(self.output),
        ]

    def run_cli(self, args=None, env=None):
        self.assertTrue(ENTRY.is_file(), "Standalone local CLI entry is missing")
        return subprocess.run(
            [sys.executable, "-I", "-B", str(ENTRY), *(self.arguments if args is None else args)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def assert_safe_error(self, result, code):
        self.assertEqual(result.returncode, 2)
        data = json.loads(result.stdout)
        self.assertEqual(data["error"], code)
        self.assertIs(data["upload_enabled"], False)
        self.assertIs(data["network_requested"], False)
        self.assertEqual(result.stderr, "")
        for forbidden in (str(self.root), CPF_ALFA, "Traceback"):
            self.assertNotIn(forbidden, result.stdout)

    def test_real_cli_produces_hashed_json_csv_and_complete_without_modifying_inputs(self):
        original = self.pages.read_bytes(), self.roster.read_bytes()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["counts"]["candidate"], 3)
        self.assertEqual(summary["counts"]["unresolved"], 1)
        self.assertIs(summary["upload_enabled"], False)
        self.assertEqual(result.stderr, "")
        report = json.loads((self.output / "report.json").read_bytes())
        self.assertEqual(report["sources"]["pages_sha256"], hashlib.sha256(original[0]).hexdigest())
        self.assertEqual(
            report["sources"]["roster_sha256"], hashlib.sha256(original[1]).hexdigest()
        )
        complete = json.loads((self.output / "COMPLETE.json").read_bytes())
        for name, field in (("report.json", "report_sha256"), ("report.csv", "csv_sha256")):
            self.assertEqual(
                complete[field], hashlib.sha256((self.output / name).read_bytes()).hexdigest()
            )
        self.assertFalse((self.output / "INCOMPLETE").exists())
        rows = list(
            csv.DictReader(
                (self.output / "report.csv").read_text(encoding="utf-8-sig").splitlines(),
                delimiter=";",
            )
        )
        self.assertEqual(len(rows), 4)
        self.assertTrue(
            all(r["upload_enabled"] == "false" and r["network_requested"] == "false" for r in rows)
        )
        encoded = (
            (self.output / "report.json").read_text()
            + (self.output / "report.csv").read_text(encoding="utf-8-sig")
            + result.stdout
        )
        self.assertNotIn(CPF_ALFA, encoded)
        self.assertNotIn("Pessoa Sintetica Alfa", encoded)
        self.assertEqual(original, (self.pages.read_bytes(), self.roster.read_bytes()))

    def test_existing_output_is_preserved_and_not_reused(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("original")
        self.assert_safe_error(self.run_cli(), "OUTPUT_EXISTS")
        self.assertEqual(marker.read_text(), "original")
        self.assertFalse((self.output / "report.json").exists())

    def test_scope_failure_creates_no_output(self):
        self.arguments[self.arguments.index("--unit") + 1] = "999"
        self.assert_safe_error(self.run_cli(), "SCOPE_MISMATCH")
        self.assertFalse(self.output.exists())

    def test_malformed_input_error_does_not_echo_input(self):
        self.pages.write_text("SYNTHETIC_PRIVATE_TEXT")
        self.assert_safe_error(self.run_cli(), "PAGE_SCHEMA_INVALID")
        self.assertFalse(self.output.exists())

    def test_upload_flag_is_not_accepted(self):
        self.assert_safe_error(self.run_cli([*self.arguments, "--upload"]), "ARGUMENTS_INVALID")
        self.assertFalse(self.output.exists())

    def test_hardlinked_input_is_rejected(self):
        link = self.root / "alias.json"
        os.link(self.pages, link)
        self.assert_safe_error(self.run_cli(), "INPUT_UNAVAILABLE")
        self.assertFalse(self.output.exists())

    def test_input_output_alias_is_rejected_without_changes(self):
        original = self.pages.read_bytes()
        self.arguments[-1] = str(self.pages)
        self.assert_safe_error(self.run_cli(), "OUTPUT_EXISTS")
        self.assertEqual(original, self.pages.read_bytes())

    def test_missing_parent_does_not_create_directories(self):
        self.arguments[-1] = str(self.root / "missing" / "review")
        self.assert_safe_error(self.run_cli(), "OUTPUT_UNAVAILABLE")
        self.assertFalse((self.root / "missing").exists())

    def test_module_imports_are_isolated_from_cwd_and_pythonpath(self):
        poison = self.root / "document_matching"
        poison.mkdir()
        (poison / "__init__.py").write_text("raise RuntimeError('UNTRUSTED_IMPORT')")
        result = self.run_cli(["--help"], {**os.environ, "PYTHONPATH": str(self.root)})
        self.assertEqual(result.returncode, 0)
        self.assertIn("uploads disabled", result.stdout.lower())
        self.assertNotIn("UNTRUSTED_IMPORT", result.stdout + result.stderr)

    def test_network_path_is_rejected_without_network_access(self):
        self.arguments[1] = "//unreachable.invalid/share/pages.json"
        self.assert_safe_error(self.run_cli(), "INPUT_UNAVAILABLE")
        self.assertFalse(self.output.exists())

    def test_oversized_input_is_rejected_before_parsing(self):
        self.pages.write_bytes(b" " * (10 * 1024 * 1024 + 1))
        self.assert_safe_error(self.run_cli(), "INPUT_LIMIT_EXCEEDED")
        self.assertFalse(self.output.exists())

    def test_changed_input_prevents_bundle_completion(self):
        cli = api(self, "__main__")
        snapshot = cli._read_input(self.pages)
        changed = b'{"schema_version":1,"pages":["synthetic change"]}'
        self.pages.write_bytes(changed)
        with self.assertRaises(cli.PreparationError) as caught:
            cli._write_bundle(
                self.output, {"pages": [], "review_status": "review_required"}, (snapshot,)
            )
        self.assertEqual(caught.exception.code, "INPUT_CHANGED")
        self.assertTrue((self.output / "INCOMPLETE").is_file())
        self.assertFalse((self.output / "COMPLETE.json").exists())
        self.assertEqual(self.pages.read_bytes(), changed)

    def test_report_size_failure_is_sanitized_and_retains_incomplete_marker(self):
        cli = api(self, "__main__")
        stdout = io.StringIO()
        # The marker fits, but the real report does not. No large allocation is needed.
        with patch.object(cli, "REPORT_MAX_BYTES", 64), redirect_stdout(stdout):
            result = cli.main(self.arguments)
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stdout.getvalue())["error"], "REPORT_LIMIT_EXCEEDED")
        self.assertNotIn(str(self.root), stdout.getvalue())
        self.assertTrue((self.output / "INCOMPLETE").is_file())
        self.assertFalse((self.output / "COMPLETE.json").exists())
        self.assertFalse((self.output / "report.json").exists())

    def test_nonmonthly_cli_does_not_invent_a_competence(self):
        index = self.arguments.index("--competence")
        self.arguments[index : index + 2] = ["--nonmonthly"]
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.output / "report.json").read_bytes())
        self.assertIs(report["document"]["monthly"], False)
        self.assertIsNone(report["document"]["competence"])
        self.assertIs(report["upload_enabled"], False)
