from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .helpers import write_config

from reclaim.config import load_config
from reclaim.errors import ConfigError, SafetyError, SourceUnavailableError


class ConfigTests(unittest.TestCase):
    def test_load_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source-a"
            source.mkdir()
            config_path = write_config(base, {"source-a": source})
            config = load_config(config_path)
            self.assertEqual(config.project_name, "synthetic-cp1")
            self.assertEqual(config.sources[0].path, source.resolve())
            self.assertTrue(config.workspace.name == "workspace")
            self.assertIsNone(config.default_run_id)
            self.assertEqual(config.fingerprint(), load_config(config_path).fingerprint())

    def test_default_run_id_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source-a"
            source.mkdir()
            config_path = write_config(base, {"source-a": source})
            text = config_path.read_text(encoding="utf-8")
            text = text.replace(
                'reports_dir = "reports"',
                'reports_dir = "reports"\nrun = "20260101T000000Z-00000000"',
            )
            config_path.write_text(text, encoding="utf-8")
            config = load_config(config_path)
            self.assertEqual(config.default_run_id, "20260101T000000Z-00000000")

    def test_workspace_inside_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source-a"
            (source / "workspace").mkdir(parents=True)
            config_path = write_config(
                base, {"source-a": source}, workspace=source / "workspace"
            )
            with self.assertRaises(SafetyError):
                load_config(config_path)

    def test_nested_sources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outer = base / "outer"
            inner = outer / "inner"
            inner.mkdir(parents=True)
            config_path = write_config(base, {"outer": outer, "inner": inner})
            with self.assertRaises(SafetyError):
                load_config(config_path)

    def test_missing_source_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_path = write_config(base, {"source-a": base / "does-not-exist"})
            with self.assertRaises(SourceUnavailableError):
                load_config(config_path)

    def test_invalid_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source-a"
            source.mkdir()
            config_path = write_config(base, {"Bad ID": source})
            with self.assertRaises(ConfigError):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()
