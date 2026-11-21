from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from .helpers import write_bytes

from reclaim.errors import CHANGED_DURING_READ, SIZE_MISMATCH
from reclaim.hashing import hash_file, verify_file


class HashingTests(unittest.TestCase):
    def test_known_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            payload = b"reclaim synthetic content\n"
            write_bytes(path, payload)
            stat = path.stat()
            outcome = hash_file(
                path,
                expected_size=stat.st_size,
                expected_mtime_ns=stat.st_mtime_ns,
            )
            self.assertEqual(outcome.status, "ok")
            self.assertEqual(outcome.digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(outcome.bytes_read, len(payload))
            self.assertTrue(outcome.signature_hex)

    def test_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.txt"
            write_bytes(path, b"")
            stat = path.stat()
            outcome = hash_file(
                path, expected_size=0, expected_mtime_ns=stat.st_mtime_ns
            )
            self.assertEqual(outcome.digest, hashlib.sha256(b"").hexdigest())

    def test_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.txt"
            write_bytes(path, b"1234")
            stat = path.stat()
            outcome = hash_file(
                path,
                expected_size=stat.st_size + 1,
                expected_mtime_ns=stat.st_mtime_ns,
            )
            self.assertEqual(outcome.status, "error")
            self.assertEqual(outcome.error_code, SIZE_MISMATCH)

    def test_change_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.txt"
            write_bytes(path, b"1234")
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
            outcome = hash_file(
                path,
                expected_size=stat.st_size,
                expected_mtime_ns=stat.st_mtime_ns,
            )
            self.assertEqual(outcome.status, "error")
            self.assertEqual(outcome.error_code, CHANGED_DURING_READ)

    def test_signature_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.pdf"
            write_bytes(path, b"%PDF-1.7\nsynthetic")
            stat = path.stat()
            outcome = hash_file(
                path, expected_size=stat.st_size, expected_mtime_ns=stat.st_mtime_ns
            )
            self.assertTrue(outcome.signature_hex.startswith(b"%PDF".hex()))

    def test_verify_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.txt"
            write_bytes(path, b"verify me")
            digest = hashlib.sha256(b"verify me").hexdigest()
            ok, error = verify_file(path, expected_digest=digest)
            self.assertTrue(ok)
            self.assertIsNone(error)
            ok, error = verify_file(path, expected_digest="0" * 64)
            self.assertFalse(ok)
            self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
