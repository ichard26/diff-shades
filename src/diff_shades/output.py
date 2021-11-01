# ====================================
# > Messaging and reporting utilities
# =================================

import contextlib
import difflib
import os
from contextlib import redirect_stderr, redirect_stdout
from typing import Iterator

import rich
import rich.progress

console = rich.get_console()

# NOTE: These two functions were copied straight from black.output :P


def unified_diff(a: str, b: str, a_name: str, b_name: str) -> str:
    """Return a unified diff string between strings `a` and `b`."""
    a_lines = [line for line in a.splitlines(keepends=True)]
    b_lines = [line for line in b.splitlines(keepends=True)]
    diff_lines = []
    for line in difflib.unified_diff(
        a_lines, b_lines, fromfile=a_name, tofile=b_name, n=5
    ):
        # Work around https://bugs.python.org/issue2142. See also:
        # https://www.gnu.org/software/diffutils/manual/html_node/Incomplete-Lines.html
        if line[-1] == "\n":
            diff_lines.append(line)
        else:
            diff_lines.append(line + "\n")
            diff_lines.append("\\ No newline at end of file\n")
    return "".join(diff_lines)


def colour_diff(contents: str) -> str:
    """Inject ANSI colour codes to the diff."""
    lines = contents.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("+++") or line.startswith("---"):
            line = "\033[1;37m" + line + "\033[0m"  # bold white, reset
        elif line.startswith("@@"):
            line = "\033[36m" + line + "\033[0m"  # cyan, reset
        elif line.startswith("+"):
            line = "\033[32m" + line + "\033[0m"  # green, reset
        elif line.startswith("-"):
            line = "\033[31m" + line + "\033[0m"  # red, reset
        lines[i] = line
    return "\n".join(lines)


@contextlib.contextmanager
def suppress_output() -> Iterator:
    with open(os.devnull, "w", encoding="utf-8") as blackhole:
        with redirect_stdout(blackhole), redirect_stderr(blackhole):
            yield


def make_rich_progress() -> rich.progress.Progress:
    return rich.progress.Progress(
        "[progress.description]{task.description}",
        rich.progress.BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "-",
        "[progress.percentage]{task.completed}/{task.total}",
        "-",
        rich.progress.TimeElapsedColumn(),
        console=console,
    )
