from __future__ import annotations

import configparser
from pathlib import Path

import pytest

_PYTEST_INI = Path(__file__).resolve().parents[1] / "pytest.ini"


def _load_pytest_section() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(_PYTEST_INI, encoding="utf-8")
    assert parser.has_section("pytest"), f"missing [pytest] in {_PYTEST_INI}"
    return parser["pytest"]


def test_pytest_ini_declares_timeout() -> None:
    section = _load_pytest_section()
    assert "timeout" in section, "pytest.ini must set a default `timeout`"
    value = section["timeout"].strip()
    assert value.isdigit() and int(value) > 0, (
        f"`timeout` must be a positive integer, got {value!r}"
    )


def test_pytest_ini_uses_thread_method() -> None:
    section = _load_pytest_section()
    assert section.get("timeout_method", "").strip() == "thread", (
        "`timeout_method = thread` is required for Windows agents without SIGALRM"
    )


@pytest.mark.timeout(5)
def test_plugin_is_active() -> None:
    assert True
