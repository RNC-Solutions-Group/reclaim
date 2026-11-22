# M2 — Packaging and PySide6/Qt Redistribution Notes

## 1. Current packaging

- The engine is a Python package with a console entry point: `reclaim`.
- The desktop shell adds an optional `gui` extra containing PySide6.
- The desktop entry point is `reclaim-gui` (`[project.gui-scripts]`).
- The wheel does **not** bundle Qt binaries; PySide6/Qt is installed as a
  separate dependency.
- M2 is a functional alpha, not an installer or code-signed distribution.

## 2. ADR-009 constraints

ADR-009 conditionally approves PySide6/Qt with:

- dynamic linking, not static Qt linking;
- inclusion of Qt/PySide6 license notices in a future distribution;
- a documented replacement/compliance path for the LGPL libraries;
- legal review before distribution.

The source tree uses PySide6 as an optional runtime dependency only. No Qt
source or binaries are modified or vendored. The application does not impose
additional restrictions on replacing the dynamically linked Qt libraries.

## 3. Build and smoke test

From the repository root:

```powershell
python -m pip wheel . --no-deps --wheel-dir .\wheelhouse
python -m venv .\qt-smoke
.\qt-smoke\Scripts\python -m pip install PySide6
.\qt-smoke\Scripts\python -m pip install --no-deps .\wheelhouse\reclaim-*.whl
.\qt-smoke\Scripts\reclaim --version
.\qt-smoke\Scripts\reclaim-gui
```

The M2 validation record must state whether the GUI itself was launched in the
isolated environment. Current M2 smoke result: `reclaim-0.3.0` wheel built,
installed into a virtual environment with access to the host PySide6 6.9.3,
`reclaim --version` returned `0.3.0`, `reclaim-gui.exe`/`reclaim.exe` were
generated, and `MainWindow` imported/instantiated offscreen from the installed
wheel. A native Qt platform launch from the venv was not performed.

For an offline distribution, pre-stage the PySide6 wheels and Qt notices; do
not rely on a network install. Distribution must include the LGPLv3 text,
PySide6/Qt notices, and any third-party notices required by the Qt modules
actually shipped.

## 4. Platform status

- **Windows 11 / Python 3.13 / PySide6 6.9.3:** source-tree GUI and offscreen
  tests executed; screenshots generated.
- **macOS/Linux:** not executed. Qt platform packaging and filesystem semantics
  remain unverified for M2.
- **Installer/code signing/auto-update:** not implemented.

## 5. Not a distribution approval

This document records engineering constraints and smoke-test procedure. It does
not constitute legal approval for redistribution. Operator/legal review remains
required before shipping binaries that include Qt/PySide6.
