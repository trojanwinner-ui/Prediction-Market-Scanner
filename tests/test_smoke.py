"""Phase 0 smoke test: the package installs and imports."""

import pm_scanner


def test_package_imports() -> None:
    assert pm_scanner.__version__ == "0.1.0"
