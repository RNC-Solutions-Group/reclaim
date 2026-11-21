from __future__ import annotations

import unittest

from .helpers import SRC  # noqa: F401

from reclaim.classify import (
    Override,
    load_policy,
    load_type_profile,
    signature_family,
)
from reclaim.scanner import EntryRecord


def record(
    rel_path: str,
    *,
    size: int = 10,
    is_media: bool = False,
    is_cloud: bool = False,
) -> EntryRecord:
    name = rel_path.rsplit("/", 1)[-1]
    parent = "." if "/" not in rel_path else rel_path.rsplit("/", 1)[0]
    return EntryRecord(
        source_id="source-a",
        rel_path=rel_path,
        parent_rel_path=parent,
        name=name,
        depth=rel_path.count("/") + 1,
        entry_type="file",
        size_bytes=size,
        created_ns=None,
        modified_ns=None,
        accessed_ns=None,
        mode=None,
        attributes=0,
        is_hidden=False,
        is_system=False,
        is_reparse=False,
        is_cloud=is_cloud,
        is_media_library=is_media,
    )


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy, cls.profile = load_policy()

    def test_default_retains_ordinary_document(self) -> None:
        decision = self.policy.evaluate(record("Documents/letter.txt"), "document")
        self.assertEqual(decision.disposition, "retain")
        self.assertEqual(decision.rule_id, "default-retain")

    def test_shortcut_needs_review(self) -> None:
        decision = self.policy.evaluate(record("Documents/Server.lnk"), "shortcut")
        self.assertEqual(decision.disposition, "needs_review")
        self.assertEqual(decision.rule_id, "shortcut-or-link")

    def test_downloads_installer_needs_review(self) -> None:
        decision = self.policy.evaluate(record("Downloads/setup.exe"), "executable")
        self.assertEqual(decision.disposition, "needs_review")
        self.assertEqual(decision.rule_id, "download-installer")

    def test_downloads_archive_needs_review(self) -> None:
        decision = self.policy.evaluate(record("Downloads/archive.zip"), "archive")
        self.assertEqual(decision.disposition, "needs_review")
        self.assertEqual(decision.rule_id, "download-archive")

    def test_temp_extension_is_potential_junk(self) -> None:
        decision = self.policy.evaluate(record("Downloads/file.partial"), "temp")
        self.assertEqual(decision.disposition, "potential_junk")

    def test_empty_file_needs_review(self) -> None:
        decision = self.policy.evaluate(record("Documents/empty.txt", size=0), "document")
        self.assertEqual(decision.disposition, "needs_review")
        self.assertEqual(decision.rule_id, "empty-file")

    def test_media_library_deferred(self) -> None:
        decision = self.policy.evaluate(
            record("Photos/iPhoto Library/Originals/a.jpg", is_media=True), "image"
        )
        self.assertEqual(decision.disposition, "defer_media")
        self.assertEqual(decision.rule_id, "media-library-deferred")

    def test_os_metadata_is_junk_candidate(self) -> None:
        decision = self.policy.evaluate(record(".DS_Store"), "unknown")
        self.assertEqual(decision.disposition, "potential_junk")
        self.assertEqual(decision.rule_id, "os-metadata")

    def test_override_precedence(self) -> None:
        recommendation = self.policy.evaluate(record("Downloads/setup.exe"), "executable")
        directory = Override("retain", "operator wants installers")
        file_override = Override("retain", "this installer is needed", "override-1")
        effective = self.policy.resolve(
            recommendation,
            directory_override=directory,
            file_override=file_override,
        )
        self.assertEqual(effective.disposition, "retain")
        self.assertEqual(effective.source, "file_override")
        effective = self.policy.resolve(recommendation, directory_override=directory)
        self.assertEqual(effective.source, "directory_override")

    def test_signature_mismatch(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 8
        self.assertEqual(signature_family(png), "image")
        self.assertTrue(
            self.profile.signature_mismatch_for_kind("pdf", png.hex())
        )
        zip_head = b"PK\x03\x04" + b"0" * 8
        self.assertFalse(
            self.profile.signature_mismatch_for_kind("document", zip_head.hex())
        )

    def test_signature_brands_do_not_produce_false_mismatches(self) -> None:
        m4a = b"\x00\x00\x00\x20ftypM4A " + b"0" * 8
        self.assertEqual(signature_family(m4a), "audio")
        self.assertFalse(self.profile.signature_mismatch_for_kind("audio", m4a.hex()))
        svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">'
        self.assertEqual(signature_family(svg), "image")
        self.assertFalse(self.profile.signature_mismatch_for_kind("image", svg.hex()))

    def test_type_profile(self) -> None:
        self.assertEqual(self.profile.kind_for(record("a.DOCX")), "document")
        self.assertEqual(self.profile.kind_for(record("a.unknownext")), "unknown")
        appdata = self.profile.kind_for(
            record("AppData/Local/SomeApp/config.json")
        )
        self.assertEqual(appdata, "appdata")

    def test_policy_overlay_appends_project_rule(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.toml"
            overlay.write_text(
                """
[[rules]]
id = "project-media-deferred"
priority = 85
kind_any = ["image"]
disposition = "defer_media"
rationale = "project phase boundary"
confidence = 0.9
""",
                encoding="utf-8",
            )
            engine, _ = load_policy(overlay_path=overlay)
            decision = engine.evaluate(record("Pictures/holiday.jpg"), "image")
            self.assertEqual(decision.disposition, "defer_media")
            self.assertEqual(decision.rule_id, "project-media-deferred")

    def test_duplicate_rule_id_in_overlay_is_rejected(self) -> None:
        import tempfile
        from pathlib import Path

        from reclaim.errors import ConfigError

        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.toml"
            overlay.write_text(
                """
[[rules]]
id = "os-metadata"
priority = 1
disposition = "retain"
rationale = "duplicate"
confidence = 1.0
""",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_policy(overlay_path=overlay)


if __name__ == "__main__":
    unittest.main()
