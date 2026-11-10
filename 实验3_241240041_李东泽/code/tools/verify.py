"""Repeatable local build gate; writes actual outputs rather than claimed results."""

import ast
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tokenize


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / "验证材料"


def run(name, arguments):
    """Capture an executed command with its exit code and fail the build on errors."""
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "$ " + " ".join(arguments) + "\n" + result.stdout + result.stderr
    output += f"\nEXIT_CODE={result.returncode}\n"
    (EVIDENCE / f"{name}.txt").write_text(output, encoding="utf-8")
    print(f"{name}: exit={result.returncode}")
    if result.returncode:
        print(output)
        raise SystemExit(result.returncode)


def source_counts():
    """Count nonblank noncomment source lines, additionally excluding docstrings."""
    entries = []
    for path in sorted((ROOT / "src" / "market").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        document_lines = set()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                if node.body and isinstance(node.body[0], ast.Expr):
                    expression = node.body[0]
                    if isinstance(expression.value, ast.Constant) and isinstance(
                        expression.value.value, str
                    ):
                        document_lines.update(
                            range(expression.lineno, expression.end_lineno + 1)
                        )
        comment_lines = set()
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                if lines[token.start[0] - 1].lstrip().startswith("#"):
                    comment_lines.add(token.start[0])
        effective = sum(
            bool(line.strip()) and index not in document_lines | comment_lines
            for index, line in enumerate(lines, 1)
        )
        entries.append(
            {
                "file": str(path.relative_to(ROOT)),
                "physical": len(lines),
                "code_excluding_comments_docstrings": effective,
            }
        )
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "files": entries,
        "production_code_lines": sum(
            row["code_excluding_comments_docstrings"] for row in entries
        ),
        "physical_lines": sum(row["physical"] for row in entries),
        "excluded": "tests, tools, generated files, blank lines, comments, docstrings",
    }
    (EVIDENCE / "source-counts.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Production source lines: {report['production_code_lines']}")
    if report["production_code_lines"] <= 1500:
        raise SystemExit("Production code must exceed 1500 lines")


def main():
    """Compile, test, lint, package and record source scale."""
    EVIDENCE.mkdir(exist_ok=True)
    source_counts()
    run("compile", [sys.executable, "-m", "compileall", "-q", "src", "tests"])
    run("tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    run("pylint", [sys.executable, "-m", "pylint", "src/market"])
    run("build", [sys.executable, "-m", "build", "--no-isolation"])


if __name__ == "__main__":
    main()
