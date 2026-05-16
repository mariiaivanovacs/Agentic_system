"""
test_isolation.py — unit tests for Phase 4 data isolation.

Tests cover:
  - _is_secret_key detection
  - _sanitize_snapshot (flat, nested, list, mixed)
  - WebPage/WebEntity app_id parameter presence (compile-level)
  - _build_snapshot signature (no DB required)

Run with: python test_isolation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.agents.tools import _is_secret_key, _sanitize_snapshot

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def _assert(condition: bool, label: str) -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        _failures.append(label)


# --------------------------------------------------------------------------- #
# _is_secret_key                                                                #
# --------------------------------------------------------------------------- #

print("\n── _is_secret_key ───────────────────────────────────────")
_assert(_is_secret_key("password"),         "exact: password")
_assert(_is_secret_key("PASSWORD"),         "case-insensitive: PASSWORD")
_assert(_is_secret_key("api_key"),          "exact: api_key")
_assert(_is_secret_key("apikey"),           "exact: apikey")
_assert(_is_secret_key("auth_token"),       "exact: auth_token")
_assert(_is_secret_key("secret"),           "exact: secret")
_assert(_is_secret_key("db_password"),      "substring: db_password contains 'password'")
_assert(_is_secret_key("jwt_token"),        "substring: jwt_token contains 'token'")
_assert(_is_secret_key("private_key"),      "exact: private_key")
_assert(_is_secret_key("client_secret"),    "substring: client_secret contains 'secret'")
_assert(_is_secret_key("user-password"),    "hyphen form: user-password")

_assert(not _is_secret_key("name"),        "safe: name")
_assert(not _is_secret_key("id"),          "safe: id")
_assert(not _is_secret_key("industry"),    "safe: industry")
_assert(not _is_secret_key("expertise"),   "safe: expertise")
_assert(not _is_secret_key("app_id"),      "safe: app_id")
_assert(not _is_secret_key("score"),       "safe: score")


# --------------------------------------------------------------------------- #
# _sanitize_snapshot — flat dict                                                #
# --------------------------------------------------------------------------- #

print("\n── _sanitize_snapshot (flat) ────────────────────────────")

raw_flat = {
    "id": "C-01",
    "name": "Nexus AI",
    "password": "hunter2",
    "api_key": "sk-abc123",
    "industry": "Fintech",
}
clean_flat = _sanitize_snapshot(raw_flat)

_assert("id" in clean_flat,           "keeps: id")
_assert("name" in clean_flat,         "keeps: name")
_assert("industry" in clean_flat,     "keeps: industry")
_assert("password" not in clean_flat, "strips: password")
_assert("api_key" not in clean_flat,  "strips: api_key")
_assert(len(clean_flat) == 3,         "flat dict has exactly 3 safe keys remaining")


# --------------------------------------------------------------------------- #
# _sanitize_snapshot — nested dict                                              #
# --------------------------------------------------------------------------- #

print("\n── _sanitize_snapshot (nested) ──────────────────────────")

raw_nested = {
    "company": {
        "id": "C-02",
        "db_password": "secret123",
        "name": "EtechFinance",
    },
    "auth": {
        "jwt_token": "eyJ...",
        "expires_in": 3600,
    },
    "meta": "ok",
}
clean_nested = _sanitize_snapshot(raw_nested)

_assert("company" in clean_nested,                        "keeps: top-level company key")
_assert("id" in clean_nested["company"],                  "keeps: company.id")
_assert("name" in clean_nested["company"],                "keeps: company.name")
_assert("db_password" not in clean_nested["company"],     "strips: company.db_password")
_assert("auth" in clean_nested,                           "keeps: top-level auth key (not secret itself)")
_assert("jwt_token" not in clean_nested["auth"],          "strips: auth.jwt_token")
_assert("expires_in" in clean_nested["auth"],             "keeps: auth.expires_in")
_assert(clean_nested["meta"] == "ok",                     "keeps: non-dict value unchanged")


# --------------------------------------------------------------------------- #
# _sanitize_snapshot — list of dicts                                            #
# --------------------------------------------------------------------------- #

print("\n── _sanitize_snapshot (list) ────────────────────────────")

raw_list = {
    "companies": [
        {"id": "C-01", "name": "A", "secret": "leak"},
        {"id": "C-02", "name": "B", "credential": "leak2"},
        {"id": "C-03", "name": "C", "industry": "SaaS"},
    ]
}
clean_list = _sanitize_snapshot(raw_list)

companies = clean_list["companies"]
_assert(len(companies) == 3,                            "all 3 company rows preserved")
_assert("secret" not in companies[0],                   "strips: companies[0].secret")
_assert("credential" not in companies[1],               "strips: companies[1].credential")
_assert("industry" in companies[2],                     "keeps: companies[2].industry")
_assert(all("id" in c for c in companies),              "id kept in all rows")
_assert(all("name" in c for c in companies),            "name kept in all rows")


# --------------------------------------------------------------------------- #
# _sanitize_snapshot — non-dict passthrough                                     #
# --------------------------------------------------------------------------- #

print("\n── _sanitize_snapshot (primitives) ─────────────────────")
_assert(_sanitize_snapshot("hello") == "hello",         "string passes through unchanged")
_assert(_sanitize_snapshot(42) == 42,                   "int passes through unchanged")
_assert(_sanitize_snapshot(None) is None,               "None passes through unchanged")
_assert(_sanitize_snapshot([1, 2, 3]) == [1, 2, 3],    "list of ints passes through")


# --------------------------------------------------------------------------- #
# _sanitize_snapshot — realistic full snapshot                                  #
# --------------------------------------------------------------------------- #

print("\n── _sanitize_snapshot (realistic snapshot) ──────────────")

realistic = {
    "companies": [
        {"id": "C-10", "name": "HealthCo", "industry": "Healthtech", "db_secret": "x"},
        {"id": "C-11", "name": "FintechX",  "industry": "Fintech"},
    ],
    "mentors": [
        {"id": "M-01", "name": "Alice", "expertise": ["AI"], "access_token": "tok"},
        {"id": "M-02", "name": "Bob",   "expertise": ["Finance"]},
    ],
    "_meta": {"app_id": "example.com", "scoped": True},
}
clean_realistic = _sanitize_snapshot(realistic)

_assert("db_secret"    not in clean_realistic["companies"][0], "strips: company db_secret")
_assert("industry"         in clean_realistic["companies"][0], "keeps: company industry")
_assert("access_token" not in clean_realistic["mentors"][0],   "strips: mentor access_token")
_assert("expertise"        in clean_realistic["mentors"][0],   "keeps: mentor expertise")
_assert("_meta"            in clean_realistic,                 "keeps: _meta block")
_assert("app_id"           in clean_realistic["_meta"],        "keeps: _meta.app_id")


# --------------------------------------------------------------------------- #
# Signature checks — _build_snapshot accepts app_id                            #
# --------------------------------------------------------------------------- #

print("\n── _build_snapshot signature ────────────────────────────")
import inspect
from src.agents.tools import _build_snapshot

sig = inspect.signature(_build_snapshot)
params = list(sig.parameters.keys())
_assert("industry" in params, "_build_snapshot has 'industry' parameter")
_assert("app_id" in params,   "_build_snapshot has new 'app_id' parameter")


# --------------------------------------------------------------------------- #
# web_indexer signatures: app_id present on both write helpers                 #
# --------------------------------------------------------------------------- #

print("\n── web_indexer write-helper signatures ──────────────────")
from src.indexer.web_indexer import _write_page_node, _write_entity

sig_page = inspect.signature(_write_page_node)
_assert("app_id" in sig_page.parameters, "_write_page_node has app_id parameter")

sig_entity = inspect.signature(_write_entity)
_assert("app_id" in sig_entity.parameters, "_write_entity has app_id parameter")


# --------------------------------------------------------------------------- #
# Compile checks                                                                #
# --------------------------------------------------------------------------- #

print("\n── compile checks ───────────────────────────────────────")
import py_compile

for path in [
    "src/agents/tools.py",
    "src/indexer/web_indexer.py",
    "streamlit_app.py",
    "test_isolation.py",
]:
    try:
        py_compile.compile(path, doraise=True)
        _assert(True, f"{path} compiles cleanly")
    except py_compile.PyCompileError as exc:
        _assert(False, f"{path} compile error: {exc}")


# --------------------------------------------------------------------------- #
# Summary                                                                       #
# --------------------------------------------------------------------------- #

print()
if _failures:
    print(f"FAILED — {len(_failures)} assertion(s):")
    for f in _failures:
        print(f"  • {f}")
    sys.exit(1)
else:
    print("All isolation tests passed.")
