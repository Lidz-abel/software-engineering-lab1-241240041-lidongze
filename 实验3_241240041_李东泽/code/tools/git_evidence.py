"""Capture real repository state without inventing retrospective history."""

from pathlib import Path
import subprocess


def main():
    """Record repository commands before the final artifact commit."""
    root = Path(__file__).resolve().parents[3]
    commands = [
        ["git", "remote", "-v"],
        ["git", "log", "--graph", "--oneline", "--decorate", "-8"],
        ["git", "show", "--stat", "--oneline", "42a2758"],
        ["git", "show", "--stat", "--oneline", "79153f4"],
        [
            "git",
            "ls-remote",
            "origin",
            "refs/heads/main",
            "refs/tags/lab3-implementation",
        ],
    ]
    output = ["Snapshot before final report/evidence commit.\n"]
    for arguments in commands:
        result = subprocess.run(
            arguments,
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        output.extend(["$ " + " ".join(arguments), result.stdout, result.stderr])
    destination = root / "实验3_241240041_李东泽" / "验证材料" / "git-history.txt"
    destination.write_text("\n".join(output), encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
