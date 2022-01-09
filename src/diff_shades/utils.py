import difflib
from dataclasses import dataclass
from typing import Optional, Tuple

import rich
from rich.markup import escape
from rich.progress import BarColumn, Progress, TimeElapsedColumn

console = rich.get_console()


@dataclass
class DSError(Exception):
    error: str
    tip: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.error} -> {self.tip}"

    def __rich__(self) -> str:
        if self.tip:
            return f"[error]{escape(self.error)}[/]\n[info]╰─> {escape(self.tip)}[/]"
        else:
            return f"[error]{escape(self.error)}"


def unified_diff(a: str, b: str, a_name: str, b_name: str) -> str:
    """Return a unified diff string between strings `a` and `b`."""
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff_lines = []
    for line in difflib.unified_diff(a_lines, b_lines, fromfile=a_name, tofile=b_name, n=5):
        # Work around https://bugs.python.org/issue2142. See also:
        # https://www.gnu.org/software/diffutils/manual/html_node/Incomplete-Lines.html
        if line[-1] == "\n":
            diff_lines.append(line)
        else:
            diff_lines.append(line + "\n")
            diff_lines.append("\\ No newline at end of file\n")
    return "".join(diff_lines)


def calculate_line_changes(diff: str) -> Tuple[int, int]:
    """Return a two-tuple (additions, deletions) of a diff."""
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if line[0] == "+" and not line.startswith("+++"):
            additions += 1
        elif line[0] == "-" and not line.startswith("---"):
            deletions += 1

    return additions, deletions


def color_diff(contents: str) -> str:
    """Inject rich markup into a diff."""
    lines = escape(contents).split("\n")
    for i, line in enumerate(lines):
        if line.startswith("+++") or line.startswith("---"):
            line = "[bold]" + line + "[/]"
        elif line.startswith("@@"):
            line = "[cyan]" + line + "[/]"
        elif line.startswith("+"):
            line = "[green]" + line + "[/]"
        elif line.startswith("-"):
            line = "[red]" + line + "[/]"
        lines[i] = line
    return "\n".join(lines)


def make_rich_progress() -> Progress:
    return Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "-",
        "[progress.percentage]{task.completed}/{task.total}",
        "-",
        TimeElapsedColumn(),
        console=console,
    )


def readable_int(number: int) -> str:
    if number < 10000:
        return str(number)

    return f"{number:,}".replace(",", " ")
