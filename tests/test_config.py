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
            self.assertEqual(config.fingerprint(), load_config(config_path).fingerprint())

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
