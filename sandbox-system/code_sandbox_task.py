"""
Code sandbox task — Cloud Run entry point for code-modification simulations.

Mirrors sandbox_task.py for the flow simulation path, but handles actual
file patches applied to an isolated copy of the source codebase.

Environment variables:
  SOURCE_PATH   Path to the codebase to copy and patch (must be accessible)
  PATCH_DATA    JSON list of patch dicts: [{file_path, old_code, new_code, description}, ...]
  TEST_CMD      Shell command to run validation (optional; auto-detected if absent)

Output protocol (same as sandbox_task.py):
  DATA_STREAM_START
  <JSON list of trace dicts>
  DATA_STREAM_END
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _detect_test_cmd(root: Path) -> str | None:
    if (root / "package.json").exists():
        try:
            pkg = json.loads((root / "package.json").read_text())
            if pkg.get("scripts", {}).get("test"):
                return "npm test"
        except Exception:
            pass
    if (root / "Cargo.toml").exists():
        return "cargo test"
    py_files = list(root.rglob("*.py"))
    if py_files:
        test_files = [f for f in py_files if f.name.startswith("test_") or "tests" in str(f)]
        if test_files:
            return f"{sys.executable} -m pytest --tb=short -q"
        return f"{sys.executable} -m py_compile " + " ".join(str(f) for f in py_files[:20])
    return None


def _parse_pytest_output(output: str) -> tuple[int, int]:
    passed = failed = 0
    for line in output.splitlines():
        m = re.search(r"(\d+) passed", line)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", line)
        if m:
            failed = int(m.group(1))
    return passed, failed


def run_code_sandbox() -> None:
    print("--- CODE SANDBOX: INITIALIZING ---")

    source_path = os.getenv("SOURCE_PATH", "")
    patch_data_str = os.getenv("PATCH_DATA", "[]")
    test_cmd_override = os.getenv("TEST_CMD", "")

    if not source_path:
        print("ERROR: SOURCE_PATH environment variable is not set.")
        print("DATA_STREAM_START")
        print(json.dumps([{"status": "ERROR", "error": "SOURCE_PATH not set"}]))
        print("DATA_STREAM_END")
        return

    try:
        patches = json.loads(patch_data_str)
    except json.JSONDecodeError as exc:
        print(f"ERROR: PATCH_DATA is not valid JSON: {exc}")
        print("DATA_STREAM_START")
        print(json.dumps([{"status": "ERROR", "error": f"Invalid PATCH_DATA: {exc}"}]))
        print("DATA_STREAM_END")
        return

    src_root = Path(source_path).resolve()
    if not src_root.exists():
        print(f"ERROR: SOURCE_PATH does not exist: {src_root}")
        print("DATA_STREAM_START")
        print(json.dumps([{"status": "ERROR", "error": f"SOURCE_PATH not found: {src_root}"}]))
        print("DATA_STREAM_END")
        return

    # Create isolated sandbox copy in /tmp
    sandbox_root = Path(tempfile.mkdtemp(prefix="code_sandbox_")) / "src"
    print(f"STATUS: Copying {src_root} → {sandbox_root}")
    shutil.copytree(
        str(src_root),
        str(sandbox_root),
        ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".venv", "venv", "*.pyc"),
    )

    traces = []
    patch_count = 0

    # Apply patches
    for patch in patches:
        rel_path = patch.get("file_path", "")
        old_code = patch.get("old_code", "")
        new_code = patch.get("new_code", "")
        description = patch.get("description", "")

        if not rel_path:
            continue

        target = sandbox_root / rel_path
        applied = False
        error_msg = None

        if old_code == "" and new_code:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_code, encoding="utf-8")
            applied = True
        elif target.exists():
            original = target.read_text(encoding="utf-8")
            if old_code in original:
                target.write_text(original.replace(old_code, new_code, 1), encoding="utf-8")
                applied = True
            else:
                error_msg = f"old_code string not found in {rel_path}"
        else:
            error_msg = f"Target file not found: {rel_path}"

        print(f"PATCH {'OK' if applied else 'FAIL'}: {rel_path} — {description}")
        if error_msg:
            print(f"  ERROR: {error_msg}")

        traces.append({
            "file": rel_path,
            "patch_applied": applied,
            "description": description,
            "error": error_msg,
            "status": "PATCH_OK" if applied else "PATCH_FAIL",
        })
        if applied:
            patch_count += 1

    print(f"STATUS: Applied {patch_count}/{len(patches)} patches.")

    # Run test command
    cmd = test_cmd_override or _detect_test_cmd(sandbox_root)
    if not cmd:
        print("STATUS: No test command detected — reporting patch-only results.")
        success_rate = patch_count / max(len(patches), 1)
        for t in traces:
            t["simulated_outcome_score"] = round(success_rate * 10, 2)
        print("DATA_STREAM_START")
        print(json.dumps(traces))
        print("DATA_STREAM_END")
        shutil.rmtree(str(sandbox_root.parent), ignore_errors=True)
        return

    print(f"STATUS: Running test command: {cmd}")
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(sandbox_root),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(sandbox_root)},
        )
    except subprocess.TimeoutExpired:
        print("ERROR: Test command timed out after 120 seconds.")
        for t in traces:
            t["simulated_outcome_score"] = 0.0
            t["test_status"] = "TIMEOUT"
        print("DATA_STREAM_START")
        print(json.dumps(traces))
        print("DATA_STREAM_END")
        shutil.rmtree(str(sandbox_root.parent), ignore_errors=True)
        return

    test_output = proc.stdout + proc.stderr
    print(f"TEST OUTPUT:\n{test_output[:1000]}")

    passed, failed = _parse_pytest_output(test_output)
    total = passed + failed
    if total == 0:
        success = proc.returncode == 0
        score = 10.0 if success else 0.0
        test_status = "TEST_PASS" if success else "TEST_FAIL"
    else:
        score = round((passed / total) * 10, 2)
        test_status = "TEST_PASS" if proc.returncode == 0 else "TEST_FAIL"

    for t in traces:
        t["simulated_outcome_score"] = score
        t["test_status"] = test_status
        t["tests_passed"] = passed
        t["tests_failed"] = failed

    print(f"STATUS: Tests {test_status} — score={score:.2f}, passed={passed}, failed={failed}")

    print("DATA_STREAM_START")
    print(json.dumps(traces))
    print("DATA_STREAM_END")

    shutil.rmtree(str(sandbox_root.parent), ignore_errors=True)


if __name__ == "__main__":
    run_code_sandbox()
