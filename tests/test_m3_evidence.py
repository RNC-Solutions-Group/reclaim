"""M3 evidence extraction tests using synthetic documents only."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from .helpers import write_bytes

from reclaim.cli import main as cli_main
from reclaim.evidence.contracts import (
    STATUS_CRASHED,
    STATUS_EMPTY,
    STATUS_ENCRYPTED,
    STATUS_HASH_UNAVAILABLE,
    STATUS_INPUT_TOO_LARGE,
    STATUS_MALFORMED,
    STATUS_METADATA_ONLY,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNSUPPORTED,
    ExtractionLimits,
)
from reclaim.evidence.registry import (
    EvidenceSettings,
    extractor_for_suffix,
    registry_dict,
    supported_suffixes,
)
from reclaim.evidence.extractors import ExtractionFailure, extract_path
from reclaim.evidence.service import EvidenceService
from reclaim.gui.services import ProjectSession


def _text_pdf(path: Path, text: str = "Hello PDF evidence") -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(data))


class M3EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        source = self.base / "source"
        source.mkdir()
        (source / "hello.txt").write_text(
            "Hello evidence world. Synthetic text for extraction.\n",
            encoding="utf-8",
        )
        (source / "empty.txt").write_bytes(b"")
        (source / "page.html").write_text(
            "<html><head><title>Demo Title</title>"
            "<meta name='author' content='Synthetic Author'></head>"
            "<body><h1>Heading One</h1><p>Hello HTML.</p>"
            "<script>alert('never executed')</script></body></html>",
            encoding="utf-8",
        )
        (source / "sample.rtf").write_text(
            r"{\rtf1\ansi{\info{\title RTF Title}}{\b Hello} RTF text.}",
            encoding="ascii",
        )
        (source / "duplicate-a.txt").write_text("same duplicate", encoding="utf-8")
        (source / "duplicate-b.txt").write_text("same duplicate", encoding="utf-8")
        (source / "unsupported.bin").write_bytes(b"unsupported")
        (source / "malformed.pdf").write_bytes(b"%PDF-1.4 not actually a pdf")
        _text_pdf(source / "text.pdf")
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Title": "Metadata Only PDF", "/Author": "Synthetic"})
        writer.write(str(source / "blank.pdf"))
        encrypted = PdfWriter()
        encrypted.add_blank_page(width=612, height=792)
        encrypted.encrypt("secret")
        encrypted.write(str(source / "encrypted.pdf"))

        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation

        doc = Document()
        doc.core_properties.title = "DOCX Title"
        doc.core_properties.author = "Synthetic Author"
        doc.add_heading("DOCX Heading", level=1)
        doc.add_paragraph("DOCX body text.")
        doc.save(source / "sample.docx")
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = "PPTX Slide Title"
        prs.save(source / "sample.pptx")
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Data"
        worksheet["A1"] = "Name"
        worksheet["B1"] = "Value"
        worksheet["A2"] = "Alpha"
        worksheet["B2"] = 42
        workbook.properties.title = "XLSX Title"
        workbook.properties.creator = "Synthetic Author"
        workbook.save(source / "sample.xlsx")

        destination = self.base / "destination"
        destination.mkdir()
        self.session = ProjectSession.create(
            project_dir=self.base / "project",
            project_name="Evidence Synthetic",
            sources=[("Source A", source)],
            destination=destination,
        )
        self.session.scan()
        self.session.hash()
        self.service = EvidenceService(
            self.session.store,
            self.session.config,
            EvidenceSettings(),
        )
        self.config_path = self.session.config.config_path
        self.source = source

    def tearDown(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_registry_declares_supported_formats(self) -> None:
        specs = registry_dict()
        self.assertGreaterEqual(len(specs), 8)
        self.assertTrue(all(spec["version"] for spec in specs))
        self.assertIn(".pdf", supported_suffixes())
        self.assertIn(".docx", supported_suffixes())
        self.assertIn(".rtf", supported_suffixes())
        self.assertEqual(extractor_for_suffix(".pdf").extractor_id, "pdf-pypdf")

    def test_full_extraction_outcomes_and_provenance(self) -> None:
        before = {
            path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source.rglob("*")
            if path.is_file()
        }
        result = self.service.run(self.session.run_id)
        after = {
            path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(result.status, "completed_with_failures")
        self.assertEqual(result.counts[STATUS_SUCCESS], 9)
        self.assertEqual(result.counts[STATUS_METADATA_ONLY], 1)
        self.assertEqual(result.counts[STATUS_ENCRYPTED], 1)
        self.assertEqual(result.counts[STATUS_MALFORMED], 1)
        self.assertEqual(result.counts[STATUS_UNSUPPORTED], 1)
        self.assertEqual(result.counts[STATUS_EMPTY], 1)
        rows = {Path(row["source_rel_path"]).name: row for row in self.service.rows()}
        self.assertEqual(rows["page.html"]["payload"]["title"]["value"], "Demo Title")
        self.assertEqual(rows["page.html"]["payload"]["title"]["source"], "document_property")
        self.assertEqual(rows["text.pdf"]["payload"]["page_count"], 1)
        self.assertGreater(rows["text.pdf"]["payload"]["text_chars"], 0)
        self.assertEqual(rows["sample.docx"]["payload"]["headings"][0]["value"], "DOCX Heading")
        self.assertEqual(rows["sample.pptx"]["payload"]["slide_count"], 1)
        self.assertEqual(rows["sample.xlsx"]["payload"]["sheet_count"], 1)
        self.assertEqual(rows["blank.pdf"]["status"], STATUS_METADATA_ONLY)
        self.assertFalse(rows["blank.pdf"]["payload"]["has_text"])
        self.assertEqual(rows["encrypted.pdf"]["status"], STATUS_ENCRYPTED)
        self.assertEqual(rows["malformed.pdf"]["status"], STATUS_MALFORMED)
        self.assertEqual(rows["unsupported.bin"]["status"], STATUS_UNSUPPORTED)
        self.assertEqual(rows["empty.txt"]["status"], STATUS_EMPTY)
        self.assertNotIn("alert", rows["page.html"]["payload"]["text_sample"])

    def test_schema_and_archive_security_limits(self) -> None:
        tables = {
            row["name"]
            for row in self.session.store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue(
            {"evidence_records", "evidence_occurrences", "evidence_runs"} <= tables
        )
        source = self.source / "sample.docx"
        copied = self.base / "archive-bomb.docx"
        shutil.copyfile(source, copied)
        with zipfile.ZipFile(copied, "a") as archive:
            for index in range(10):
                archive.writestr(f"extra-{index}.xml", "<root />")
        expected = hashlib.sha256(copied.read_bytes()).hexdigest()
        with self.assertRaises(ExtractionFailure) as context:
            extract_path(
                copied,
                expected,
                EvidenceSettings(limits=ExtractionLimits(max_archive_entries=2)),
            )
        self.assertEqual(context.exception.status, "resource_limit")

        dtd = self.base / "xml-entity.docx"
        shutil.copyfile(source, dtd)
        with zipfile.ZipFile(dtd, "a") as archive:
            archive.writestr(
                "evil.xml",
                "<!DOCTYPE x [<!ENTITY y 'expanded'>]><x>&y;</x>",
            )
        with self.assertRaises(ExtractionFailure) as context:
            extract_path(
                dtd,
                hashlib.sha256(dtd.read_bytes()).hexdigest(),
                EvidenceSettings(),
            )
        self.assertEqual(context.exception.status, "malformed")

    def test_cache_reuse_and_config_invalidation(self) -> None:
        first = self.service.run(self.session.run_id)
        second = self.service.run(self.session.run_id)
        self.assertEqual(first.cache_hits, 0)
        self.assertGreater(second.cache_hits, 0)
        self.assertEqual(second.worker_calls, 0)

        changed = EvidenceService(
            self.session.store,
            self.session.config,
            EvidenceSettings(limits=ExtractionLimits(max_text_chars=1000)),
        )
        third = changed.run(self.session.run_id)
        self.assertGreater(third.worker_calls, 0)
        self.assertNotEqual(
            self.service.settings.config_fingerprint(),
            changed.settings.config_fingerprint(),
        )

    def test_duplicate_content_is_extracted_once_and_linked_to_both_occurrences(self) -> None:
        result = self.service.run(self.session.run_id)
        self.assertEqual(result.counts[STATUS_SUCCESS], 9)
        rows = [
            row
            for row in self.service.rows()
            if Path(row["source_rel_path"]).name.startswith("duplicate-")
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["evidence_id"], rows[1]["evidence_id"])
        self.assertEqual(result.worker_calls, 12)

    def test_timeout_and_input_limit_are_explicit(self) -> None:
        timeout_service = EvidenceService(
            self.session.store,
            self.session.config,
            EvidenceSettings(limits=ExtractionLimits(timeout_seconds=0.001)),
        )
        timeout_result = timeout_service.run(self.session.run_id)
        self.assertEqual(timeout_result.status, "completed_with_failures")
        self.assertIn(STATUS_TIMEOUT, timeout_result.counts)

        small_service = EvidenceService(
            self.session.store,
            self.session.config,
            EvidenceSettings(limits=ExtractionLimits(max_input_bytes=10)),
        )
        small_result = small_service.run(self.session.run_id)
        self.assertIn(STATUS_INPUT_TOO_LARGE, small_result.counts)

    def test_worker_crash_is_reported_without_aborting_run(self) -> None:
        with mock.patch.object(
            self.service,
            "_run_worker",
            return_value={
                "ok": False,
                "status": STATUS_CRASHED,
                "error_code": "WORKER_CRASHED",
                "error_message": "synthetic worker crash",
            },
        ):
            result = self.service.run(self.session.run_id, retry_failures=True)
        self.assertEqual(result.status, "completed_with_failures")
        self.assertIn(STATUS_CRASHED, result.counts)
        self.assertEqual(self.service.stats().failures, result.counts[STATUS_CRASHED])

    def test_scanned_pdf_is_not_reported_as_full_text(self) -> None:
        self.service.run(self.session.run_id)
        row = next(
            row for row in self.service.rows() if row["source_rel_path"].endswith("blank.pdf")
        )
        self.assertEqual(row["status"], STATUS_METADATA_ONLY)
        self.assertFalse(row["payload"]["has_text"])
        self.assertEqual(row["payload"]["text_chars"], 0)

    def test_source_hash_change_is_explicit(self) -> None:
        target = self.source / "hello.txt"
        target.write_text("changed after inventory", encoding="utf-8")
        result = self.service.run(self.session.run_id)
        self.assertIn("source_changed", result.counts)

    def test_evidence_cli_stats_and_export(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = cli_main(["evidence", "--config", str(self.config_path)])
        self.assertEqual(rc, 7)
        self.assertIn("completed_with_failures", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(
                cli_main(["evidence-stats", "--config", str(self.config_path)]),
                0,
            )
        self.assertIn("outcomes", output.getvalue())
        out = self.base / "evidence-export"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(
                cli_main(
                    [
                        "evidence-export",
                        "--config",
                        str(self.config_path),
                        "--out",
                        str(out),
                    ]
                ),
                0,
            )
        self.assertTrue((out / "evidence.jsonl").exists())
        self.assertTrue((out / "evidence.csv").exists())
        self.assertIn("evidence CSV", output.getvalue())


class EvidenceContractTests(unittest.TestCase):
    def test_evidence_record_round_trip_and_bounds(self) -> None:
        from reclaim.evidence.contracts import EvidenceRecord, observation

        record = EvidenceRecord(
            content_sha256="a" * 64,
            extractor_id="x",
            extractor_version="1",
            config_fingerprint="f",
            status=STATUS_SUCCESS,
            source_format=".txt",
            title=observation("title", "T", "document_property"),
            text_chars=100,
            text_sample="x" * 5000,
            keywords=["a"] * 50,
            warnings=["w"] * 50,
        )
        payload = record.as_dict()
        restored = EvidenceRecord.from_dict(payload)
        self.assertEqual(restored.title.value, "T")
        self.assertEqual(len(restored.text_sample), 4000)
        self.assertEqual(len(restored.keywords), 30)
        self.assertEqual(len(restored.warnings), 30)

    def test_hash_unavailable_status_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            (source / "x.txt").write_text("x", encoding="utf-8")
            dest = base / "dest"
            dest.mkdir()
            session = ProjectSession.create(
                project_dir=base / "project",
                project_name="No Hash",
                sources=[("Source", source)],
                destination=dest,
            )
            try:
                session.scan()
                service = EvidenceService(session.store, session.config, EvidenceSettings())
                result = service.run(session.run_id)
                self.assertEqual(result.counts[STATUS_HASH_UNAVAILABLE], 1)
            finally:
                session.close()
