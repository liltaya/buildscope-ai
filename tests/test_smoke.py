from buildscope import __version__, version


def test_version_returns_package_version() -> None:
    assert version() == __version__


def test_initial_version() -> None:
    assert __version__ == "0.1.0"
