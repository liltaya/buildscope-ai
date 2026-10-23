from buildscope import version


def test_version() -> None:
    assert version() == "0.1.0"
