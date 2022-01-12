# ====================================
# > Messaging and reporting utilities
# =================================

import textwrap
from collections import Counter
from datetime import datetime
from typing import Sequence, Tuple, cast

import rich
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from diff_shades.results import (
    Analysis,
    ProjectResults,
    ResultTypes,
    calculate_line_changes,
    diff_two_results,
    filter_results,
)

console = rich.get_console()


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
        count = len(filter_results(analysis.files(), type))
        file_table.add_row(type, str(count), style=type)
    type_counts = Counter(proj.overall_result for proj in analysis)
    for type in ("nothing-changed", "reformatted", "failed"):
        count = type_counts.get(cast(ResultTypes, type), 0)
        project_table.add_row(type, str(count), style=type)
    stats_table.add_row(file_table, "   ", project_table)
    main_table.add_row(stats_table)

    additions, deletions = analysis.line_changes
    left_stats = f"""
        [bold]# of lines: {readable_int(analysis.line_count)}
        # of files: {len(analysis.files())}
        # of projects: {len(analysis.projects)}\
    """
    right_stats = (
        f"\n\n[bold]{readable_int(additions + deletions)} changes in total[/]"
        f"\n[green]{readable_int(additions)} additions[/]"
        f" - [red]{readable_int(deletions)} deletions"
    )
    stats_table_two = Table.grid(expand=True)
    stats_table_two.add_row(
        textwrap.dedent(left_stats), Text.from_markup(right_stats, justify="right")
    )
    main_table.add_row(stats_table_two)
    extra_args = analysis.metadata.get("black-extra-args")
    if extra_args:
        pretty_args = Text(" ".join(extra_args), style="itatic", justify="center")
        main_table.add_row(Panel(pretty_args, title="\[custom arguments]", border_style="dim"))
    created_at = datetime.fromisoformat(analysis.metadata["created-at"])
    subtitle = (
        f"[dim]black {analysis.metadata['black-version']} -"
        f" {created_at.strftime('%b %d %Y %X')} UTC"
    )

    return Panel(main_table, title="[bold]Summary", subtitle=subtitle, expand=False)


def make_comparison_summary(
    project_pairs: Sequence[Tuple[ProjectResults, ProjectResults]],
) -> Panel:
    # NOTE: This code assumes both project results used the same project revision.
    lines = sum(p.line_count for p, _ in project_pairs)
    files = sum(len(p) for p, _ in project_pairs)
    differing_projects = 0
    differing_files = 0
    additions = 0
    deletions = 0
    for results_one, results_two in project_pairs:
        if results_one != results_two:
            differing_projects += 1
            for file, r1 in results_one.items():
                r2 = results_two[file]
                if r1 != r2:
                    differing_files += 1
                    if "failed" not in (r1.type, r2.type):
                        diff = diff_two_results(r1, r2, "throwaway", theme="dark")
                        changes = calculate_line_changes(diff)
                        additions += changes[0]
                        deletions += changes[1]

    def fmt(number: int) -> str:
        return "[cyan]" + readable_int(number) + "[/cyan]"

    line = fmt(differing_projects) + " projects & " + fmt(differing_files) + " files changed /"
    line += f" {fmt(additions + deletions)} changes"
    line += f" [[green]+{readable_int(additions)}[/]/[red]-{readable_int(deletions)}[/]]"
    line += f"\n\n... out of {fmt(lines)} lines"
    line += f", {fmt(files)} files"
    line += f" & {fmt(len(project_pairs))} projects"
    return Panel(line, title="[bold]Summary", expand=False)


def make_project_details_table(analysis: Analysis) -> Table:
    project_table = Table(show_edge=False, box=rich.box.SIMPLE)
    project_table.add_column("Name")
    project_table.add_column("Results (n/r/f)")
    project_table.add_column("Line changes (total +/-)")
    project_table.add_column("# files")
    project_table.add_column("# lines")
    for proj, proj_results in analysis.results.items():
        results = ""
        for type in ("nothing-changed", "reformatted", "failed"):
            count = len(filter_results(proj_results, type))
            results += f"[{type}]{count}[/]/"
        results = results[:-1]

        additions, deletions = proj_results.line_changes
        if additions or deletions:
            line_changes = (
                f"{readable_int(additions + deletions)}"
                f" [[green]{readable_int(additions)}[/]"
                f"/[red]{readable_int(deletions)}[/]]"
            )
        else:
            line_changes = "n/a"
        file_count = str(len(proj_results))
        line_count = readable_int(proj_results.line_count)
        color = proj_results.overall_result
        project_table.add_row(proj, results, line_changes, file_count, line_count, style=color)

    return project_table
