# ====================================
# > Messaging and reporting utilities
# =================================

import difflib
from collections import Counter
from datetime import datetime
from typing import cast

import rich
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TimeElapsedColumn
from rich.table import Table

from diff_shades.results import Analysis, ResultTypes, filter_results, get_overall_result

console = rich.get_console()


def unified_diff(a: str, b: str, a_name: str, b_name: str) -> str:
    """Return a unified diff string between strings `a` and `b`."""
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
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


def color_diff(contents: str) -> str:
    """Inject rich markup into the diff."""
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


def make_analysis_summary(analysis: Analysis) -> Panel:
    main_table = Table.grid()
    stats_table = Table.grid()
    file_table = Table(title="File breakdown", show_edge=False, box=rich.box.SIMPLE)
    file_table.add_column("Result")
    file_table.add_column("# of files")
    project_table = Table(title="Project breakdown", show_edge=False, box=rich.box.SIMPLE)
    project_table.add_column("Result")
    project_table.add_column("# of projects")
    for type in ("nothing-changed", "reformatted", "failed"):
        count = len(filter_results(analysis.files, type))
        file_table.add_row(type, str(count), style=type)
    type_counts = Counter(get_overall_result(proj) for proj in analysis)
    for type in ("nothing-changed", "reformatted", "failed"):
        count = type_counts.get(cast(ResultTypes, type), 0)
        project_table.add_row(type, str(count), style=type)
    stats_table.add_row(file_table, "   ", project_table)
    main_table.add_row(stats_table)
    main_table.add_row(
        f"\n# of files: {len(analysis.files)}\n"
        f"# of projects: {len(analysis.projects)}",
        style="bold",
    )
    created_at = datetime.fromisoformat(analysis.metadata["created-at"])
    subtitle = (
        f"[dim]black {analysis.metadata['black-version']} -"
        f" {created_at.strftime('%b %d %Y %X')} UTC"
    )

    return Panel(main_table, title="[bold]Summary", subtitle=subtitle, expand=False)
