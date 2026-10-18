"""Test helpers: per-scenario wrapper executables around fake_claude.py."""
import os
import shlex

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_claude.py")


def make_wrapper(dir_path, scenario, resume_scenario="ok", name="claude"):
    """Write an executable wrapper; returns (wrapper_path, call_log_path)."""
    log = os.path.join(dir_path, "calls.jsonl")
    path = os.path.join(dir_path, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nexec python3 %s --scenario %s --resume-scenario %s --log %s \"$@\"\n"
                % tuple(shlex.quote(x) for x in (FAKE, scenario, resume_scenario, log)))
    os.chmod(path, 0o755)
    return path, log
