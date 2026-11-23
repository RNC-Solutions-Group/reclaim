"""Reproducible M3 synthetic evidence benchmark.

Run from the repository root with the evidence extra installed:

    $env:PYTHONPATH = "src"
    python benchmarks/m3_evidence_benchmark.py --out benchmark-output/m3.json

The corpus is generated in a temporary directory; no private data or repository
artifacts are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter
from pptx import Presentation

from reclaim.evidence.contracts import ExtractionLimits
from reclaim.evidence.registry import EvidenceSettings
from reclaim.evidence.service import EvidenceService
from reclaim.gui.services import ProjectSession


def _pdf_text(path: Path, text: str) -> None:
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


def _make_corpus(source: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for index in range(24):
        name = f"text-{index:02d}.txt"
        (source / name).write_text(
            f"Synthetic evidence document {index}. " + "word " * (index + 3),
            encoding="utf-8",
        )
        expected[name] = ""
    for index in range(8):
        name = f"page-{index:02d}.html"
        title = f"HTML Title {index}"
        (source / name).write_text(
            f"<html><head><title>{title}</title></head>"
            f"<body><h1>HTML Heading {index}</h1><p>Body {index}</p></body></html>",
            encoding="utf-8",
        )
        expected[name] = title
    for index in range(4):
        name = f"doc-{index}.docx"
        title = f"DOCX Title {index}"
        doc = Document()
        doc.core_properties.title = title
        doc.core_properties.author = "Synthetic Author"
        doc.add_heading(f"DOCX Heading {index}", level=1)
        doc.add_paragraph("Synthetic DOCX body.")
        doc.save(source / name)
        expected[name] = title
    for index in range(4):
        name = f"slides-{index}.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = f"Slide {index}"
        prs.save(source / name)
    for index in range(4):
        name = f"sheet-{index}.xlsx"
        wb = Workbook()
        wb.active["A1"] = f"Sheet data {index}"
        wb.save(source / name)
        expected[name] = ""
    for index in range(4):
        name = f"pdf-{index}.pdf"
        title = f"PDF Title {index}"
        _pdf_text(source / name, f"PDF text {index}")
        # PDF text title is metadata-free in this generator; the expected title
    # check intentionally only covers metadata-bearing formats.

    for index in range(2):
        name = f"blank-{index}.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Title": f"Blank Title {index}"})
        writer.write(str(source / name))
        expected[name] = f"Blank Title {index}"
    encrypted = PdfWriter()
    encrypted.add_blank_page(width=612, height=792)
    encrypted.encrypt("secret")
    encrypted.write(str(source / "encrypted.pdf"))
    (source / "malformed.pdf").write_bytes(b"%PDF-1.4 malformed")
    (source / "unsupported.bin").write_bytes(b"unsupported")
    for index in range(8):
        (source / f"duplicate-{index}.txt").write_text("same duplicate", encoding="utf-8")
    return expected


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="reclaim-m3-benchmark-"))
    try:
        source = base / "source"
        source.mkdir()
        expected = _make_corpus(source)
        destination = base / "destination"
        destination.mkdir()
        session = ProjectSession.create(
            project_dir=base / "project",
            project_name="M3 Synthetic Benchmark",
            sources=[("Benchmark", source)],
            destination=destination,
        )
        session.scan()
        session.hash()
        before = {path.relative_to(source).as_posix(): _sha(path) for path in source.rglob("*") if path.is_file()}
        service = EvidenceService(
            session.store,
            session.config,
            EvidenceSettings(limits=ExtractionLimits(timeout_seconds=20)),
        )
        started = time.perf_counter()
        first = service.run(session.run_id)
        elapsed_first = time.perf_counter() - started
        started = time.perf_counter()
        second = service.run(session.run_id)
        elapsed_second = time.perf_counter() - started
        after = {path.relative_to(source).as_posix(): _sha(path) for path in source.rglob("*") if path.is_file()}
        rows = service.rows()
        by_format: dict[str, list[int]] = {}
        statuses: dict[str, int] = {}
        correct = 0
        checked = 0
        for row in rows:
            payload = row["payload"]
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
            by_format.setdefault(row["source_format"], []).append(int(row["duration_ms"] or 0))
            expected_title = expected.get(Path(row["source_rel_path"]).name, "")
            if expected_title:
                checked += 1
                actual = (payload.get("title") or {}).get("value", "")
                if actual == expected_title:
                    correct += 1

        def percentile(values: list[int], fraction: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            return float(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))])

        format_stats = {
            fmt: {
                "count": len(durations),
                "p50_ms": round(statistics.median(durations), 1),
                "p95_ms": round(percentile(durations, 0.95), 1),
            }
            for fmt, durations in sorted(by_format.items())
        }
        result = {
            "corpus_files": len(rows),
            "expected_metadata_title_checks": checked,
            "expected_metadata_title_correct": correct,
            "field_correctness": round(correct / checked, 3) if checked else None,
            "statuses": statuses,
            "first_run_seconds": round(elapsed_first, 2),
            "second_run_seconds": round(elapsed_second, 2),
            "first_cache_hits": first.cache_hits,
            "second_cache_hits": second.cache_hits,
            "second_worker_calls": second.worker_calls,
            "cache_hit_rate_second": round(second.cache_hits / max(1, second.total_entries), 3),
            "per_format": format_stats,
            "source_modified_count": sum(1 for key in before if before[key] != after.get(key)),
            "worker_isolation": "subprocess",
            "timeout_seconds": 20,
            "environment": "Windows 11 / Python 3.13.5",
            "peak_parent_memory_note": "not measured; worker memory is process-isolated and OS limits are POSIX-only",
        }
        text = json.dumps(result, indent=2, sort_keys=True)
        print(text)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        session.close()
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
