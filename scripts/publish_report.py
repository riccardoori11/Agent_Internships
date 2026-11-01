#!/usr/bin/env python3
"""Persist only the generated report/status from a hosted Actions checkout.

Called after the collector even when collection failed: its stale banner must
survive the ephemeral runner. A normal Git push rejects concurrent branch edits;
this script never force-pushes or modifies repository permission rules.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

FILES = ("internship.md", "data/last-run.json")


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)


def publish(branch: str, directory: Path) -> bool:
    git("check-ref-format", "refs/heads/" + branch, cwd=directory)
    if any(not (directory / name).is_file() for name in FILES):
        raise RuntimeError("The report or run status is missing; nothing will be published")
    git("add", "--", *FILES, cwd=directory)
    diff = git("diff", "--cached", "--quiet", "--", *FILES, cwd=directory, check=False)
    if diff.returncode == 0:
        print("Report and run status are unchanged; no commit needed")
        return False
    if diff.returncode != 1:
        raise RuntimeError(diff.stderr or "Cannot inspect generated-file changes")
    git("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "--only", "-m", "Update internship report", "--", *FILES, cwd=directory)
    git("push", "origin", "HEAD:refs/heads/" + branch, cwd=directory)
    print("Saved internship.md and data/last-run.json to " + branch)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", required=True, help="Repository default branch")
    options = parser.parse_args()
    try:
        publish(options.branch, Path.cwd())
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            # Retain permission/rules/rejection explanations, redacting remote
            # URLs because Git can include credentials in those diagnostics.
            detail = re.sub(r"https?://\S+", "[remote URL]", exc.stderr or "").strip()
            print(f"Git exited {exc.returncode}: {detail}", file=sys.stderr)
        print(f"Report publication failed ({type(exc).__name__}). Check repository write permissions, branch rules, and concurrent updates. The Actions artifact retains the generated output.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
