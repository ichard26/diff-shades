# =============================
# > Command implementations
# ============================

import contextlib
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ContextManager, Iterator, List, Optional, TypeVar

import click
import rich
import rich.traceback
from rich.panel import Panel
from rich.table import Table

import diff_shades
from diff_shades.analysis import (
    FILE_RESULT_COLORS,
    GIT_BIN,
    AnalysisData,
    analyze_projects,
    filter_results,
    setup_projects,
)
from diff_shades.config import PROJECTS
from diff_shades.output import make_rich_progress

console = rich.get_console()

T = TypeVar("T")


@contextlib.contextmanager
def nullcontext(enter_result: T) -> Iterator[T]:
    # contextlib.nullcontext was only added in 3.7+
    yield enter_result


@click.group()
@click.option("--force-color", is_flag=True, help="Force colored output.")
@click.version_option(version=diff_shades.__version__, prog_name="diff-shades")
def main(force_color: bool) -> None:
    """
    The Black shade analyser and comparison tool.

    AKA Richard's personal take at a better black-primer (by stealing
    ideas from mypy-primer) :p

    Basically runs Black over millions of lines of code from various
    open source projects. Why? So any changes to Black can be gauged
    on their relative impact.

    \b
    Features include:
     - Simple but readable diffing capabilities
     - Repeatable analyses via --repeat-projects-from
     - Per-project python_requires support
     - Structured JSON output
     - Oh and of course, very pretty output!

    \b
    Potential tasks / additionals:
     - jupyter notebook support
     - even more helpful output
     - stronger diffing abilities
     - better UX (particularly when things go wrong)
     - so much code cleanup - like a lot :p
    """
    rich.traceback.install(suppress=[click], show_locals=True)
    rich.reconfigure(log_path=False)
    if force_color:
        rich.reconfigure(log_path=False, color_system="truecolor")


# fmt: off
@main.command()
@click.argument(
    "results-filepath", metavar="results-filepath",
    type=click.Path(resolve_path=True, readable=False, writable=True, path_type=Path)
)
@click.option(
    "-s", "--select",
    multiple=True,
    help="Select projects from the main list."
)
@click.option(
    "-e", "--exclude",
    multiple=True,
    help="Exclude projects from running."
)
@click.option(
    "-w", "--work-dir",
    type=click.Path(exists=False, dir_okay=True, file_okay=False, resolve_path=True, path_type=Path),
    help=(
        "Directory where project clones are used / stored. By default a"
        " temporary directory is used which will be cleaned up at exit."
        " Use this option to reuse or cache projects."
    )
)
@click.option(
    "--repeat-projects-from",
    type=click.Path(exists=True, dir_okay=False, file_okay=True, resolve_path=True, path_type=Path),
    help=(
        "Use the same projects (and commits!) used during another anaylsis."
        " This is similar to --work-dir but for when you don't have the"
        " checkouts available."
    )
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    help="Be more verbose."
)
# fmt: on
def analyze(
    results_filepath: Path,
    select: List[str],
    exclude: List[str],
    work_dir: Optional[Path],
    repeat_projects_from: Optional[Path],
    verbose: bool,
) -> None:
    """Run Black against 'millions' of LOC and save the results."""

    try:
        import black
    except ImportError as err:
        console.print(f"[red bold]Couldn't import black: {err}")
        console.print("[bold]-> This command requires an installation of Black.")
        sys.exit(1)

    if GIT_BIN is None:
        console.print("[red bold]Couldn't find a Git executable.")
        console.print("[bold]-> This command requires git sadly enough.")
        sys.exit(1)

    if repeat_projects_from:
        data = json.loads(repeat_projects_from.read_text("utf-8"))
        projects = [proj_data.project for proj_data in AnalysisData.load(data)]
    else:
        projects = PROJECTS

    if exclude:
        excluders = [e.casefold().strip() for e in exclude]
        projects = [p for p in projects if p.name not in excluders]
    if select:
        selectors = [project.casefold().strip() for project in select]
        projects = [p for p in projects if p.name in selectors]
    filtered = []
    for proj in projects:
        if proj.supported_by_runtime:
            filtered.append(proj)
        else:
            console.log(
                f"[bold yellow]Skipping {proj.name} as it requires python{proj.python_requires}"
            )

    workdir_provider: ContextManager
    if work_dir:
        workdir_provider = nullcontext(work_dir)
        if not work_dir.exists():
            work_dir.mkdir()
    else:
        workdir_provider = TemporaryDirectory(prefix="diff-shades-")

    with workdir_provider as _work_dir:
        with make_rich_progress() as progress:
            setup_task = progress.add_task(
                "[bold blue]Setting up projects", total=len(projects)
            )
            prepped_projects = setup_projects(
                filtered, Path(_work_dir), progress, setup_task, verbose
            )
        if not console.is_terminal:
            # Curiously this is needed when redirecting to a file so the next emitted
            # line isn't attached to the (completed) progress bar.
            console.line()

        with make_rich_progress() as progress:
            analyze_task = progress.add_task("[bold magenta]Running black")
            results = analyze_projects(prepped_projects, progress, analyze_task, verbose)
        metadata = {
            "black_version": black.__version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        analysis = AnalysisData(projects=results, metadata=metadata)
        if not console.is_terminal:
            # Curiously this is needed when redirecting to a file so the next emitted
            # line isn't attached to the (completed) progress bar.
            console.line()

    with open(results_filepath, "w", encoding="utf-8") as f:
        raw = dataclasses.asdict(analysis)
        json.dump(raw, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    console.line()
    main_table = Table.grid()
    file_table = Table(title="File breakdown", show_edge=False, box=rich.box.SIMPLE)
    file_table.add_column("Result")
    file_table.add_column("# of files")
    for type in ("nothing-changed", "reformatted", "failed"):
        count = len(filter_results(analysis.files, type))
        file_table.add_row(type, str(count), style=FILE_RESULT_COLORS[type])
    project_table = Table(title="Project breakdown", show_edge=False, box=rich.box.SIMPLE)
    project_table.add_column("Result")
    project_table.add_column("# of projects")
    for type in ("nothing-changed", "reformatted", "failed"):
        if type == "nothing-changed":
            count = sum(
                proj.results == filter_results(proj.results, type) for proj in analysis
            )
        else:
            count = sum(bool(filter_results(proj.results, type)) for proj in analysis)
        project_table.add_row(type, str(count), style=FILE_RESULT_COLORS[type])
    main_table.add_row(file_table, "   ", project_table)
    main_table.add_row(
        f"\n[bold]# of files: {len(analysis.files)}\n"
        f"[bold]# of projects: {len(analysis.projects)}"
    )
    panel = Panel(main_table, title="[bold]Summary", expand=False)
    console.print(panel)


@main.command()
@click.argument(
    "analysis-one",
    metavar="analysis-one",
    type=click.Path(resolve_path=True, exists=True, readable=True, path_type=Path),
)
@click.argument(
    "analysis-two",
    metavar="analysis-two",
    type=click.Path(resolve_path=True, exists=True, readable=True, path_type=Path),
)
@click.option(
    "--check", is_flag=True, help="Return a non-zero exit code if differences were found."
)
def compare(analysis_one: Path, analysis_two: Path, check: bool) -> None:
    """Compare two analyses for differences in the results."""

    # TODO: allow filtering of projects and files checked
    # TODO: more informative output (in particular on the differences)

    first = AnalysisData.load(json.loads(analysis_one.read_text("utf-8")))
    console.log(f"Loaded first analysis: {analysis_one}")
    second = AnalysisData.load(json.loads(analysis_two.read_text("utf-8")))
    console.log(f"Loaded second analysis: {analysis_two}")

    # TODO: Gracefully warn but accept analyses that weren't set up the exact same way.
    # fmt: off
    if (
        set(first.projects) ^ set(second.projects)
        or not all(x.project == y.project for x, y in zip(first, second))
    ):
    # fmt: on
        console.print("[bold red]\nThe two analyses don't have the same set of projects.")
        console.print(
            "[italic]-> Eventually this will be just a warning, but that's a TODO"
        )
        sys.exit(1)

    console.line()
    if first == second:
        console.print(f"[bold {FILE_RESULT_COLORS['nothing-changed']}]Nothing-changed.")
        sys.exit(0)
    else:
        console.print(f"[bold {FILE_RESULT_COLORS['reformatted']}]Differences found.")
        sys.exit(1 if check else 0)
