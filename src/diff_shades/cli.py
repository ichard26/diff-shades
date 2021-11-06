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
from typing import ContextManager, Iterator, Optional, Set, TypeVar

import click
import rich
import rich.traceback
from rich.markup import escape
from rich.padding import Padding
from rich.syntax import Syntax

import diff_shades
from diff_shades.analysis import (
    GIT_BIN,
    RESULT_COLORS,
    Analysis,
    FailedResult,
    NothingChangedResult,
    ReformattedResult,
    analyze_projects,
    filter_results,
    setup_projects,
)
from diff_shades.config import PROJECTS
from diff_shades.output import (
    color_diff,
    make_analysis_summary,
    make_rich_progress,
    unified_diff,
)

console = rich.get_console()

NOTHING_CHANGED_COLOR = RESULT_COLORS["nothing-changed"]
T = TypeVar("T")


@contextlib.contextmanager
def nullcontext(enter_result: T) -> Iterator[T]:
    # contextlib.nullcontext was only added in 3.7+
    yield enter_result


@click.group()
@click.option(
    "--no-color/--force-color", default=None, help="Force disable/enable colored output."
)
@click.version_option(version=diff_shades.__version__, prog_name="diff-shades")
def main(no_color: Optional[bool]) -> None:
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
     - Structured JSON output
     - per-project python_requires support
     - Oh and of course, pretty output!

    \b
    Potential tasks / additionals:
     - jupyter notebook support
     - custom per-analysis formatting configuration
     - even more helpful output
     - better UX (particularly when things go wrong)
     - code cleanup as my code is messy as usual :p
    """
    rich.traceback.install(suppress=[click], show_locals=True)
    color_mode_key = {True: None, None: "auto", False: "truecolor"}
    rich.reconfigure(log_path=False, color_system=color_mode_key[no_color])


# fmt: off
@main.command()
@click.argument(
    "results-path", metavar="results-filepath",
    type=click.Path(resolve_path=True, readable=False, writable=True, path_type=Path)
)
@click.option(
    "-s", "--select",
    multiple=True,
    callback=lambda ctx, param, values: set(p.strip().casefold() for p in values),
    help="Select projects from the main list."
)
@click.option(
    "-e", "--exclude",
    multiple=True,
    callback=lambda ctx, param, values: set(p.strip().casefold() for p in values),
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
        "Use the same projects (and commits!) used during another analysis."
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
    results_path: Path,
    select: Set[str],
    exclude: Set[str],
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

    if results_path.exists() and results_path.is_file():
        console.log(f"[yellow bold]Overwriting {results_path} as it already exists!")
    elif results_path.exists() and results_path.is_dir():
        console.print(f"[red bold]{results_path} is a pre-existing directory.")
        console.print("[bold]-> Can't continue as I won't overwrite a directory.")
        sys.exit(1)

    if repeat_projects_from:
        data = json.loads(repeat_projects_from.read_text("utf-8"))
        console.log(f"[bold]Loaded blueprint analysis: {repeat_projects_from}")
        projects = list(Analysis.load(data).projects.values())
    else:
        projects = PROJECTS

    projects = [p for p in projects if p.name not in exclude]
    if select:
        projects = [p for p in projects if p.name in select]
    for proj in projects:
        if not proj.supported_by_runtime:
            projects.remove(proj)
            console.log(
                "[bold yellow]"
                f"Skipping {proj.name} as it requires python{proj.python_requires}"
            )

    workdir_provider: ContextManager
    if work_dir:
        workdir_provider = nullcontext(work_dir)
        if not work_dir.exists():
            work_dir.mkdir()
    else:
        workdir_provider = TemporaryDirectory(prefix="diff-shades-")

    with workdir_provider as _work_dir:
        work_dir = Path(_work_dir)
        with make_rich_progress() as progress:
            setup_task = progress.add_task(
                "[bold blue]Setting up projects", total=len(projects)
            )
            projects = setup_projects(projects, work_dir, progress, setup_task, verbose)
        if not console.is_terminal:
            # Curiously this is needed when redirecting to a file so the next emitted
            # line isn't attached to the (completed) progress bar.
            console.line()

        with make_rich_progress() as progress:
            analyze_task = progress.add_task("[bold magenta]Running black")
            results = analyze_projects(
                projects, work_dir, progress, analyze_task, verbose
            )
        metadata = {
            "black_version": black.__version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        analysis = Analysis(
            projects={proj.name: proj for proj in projects},
            results=results,
            metadata=metadata,
        )
        if not console.is_terminal:
            # Curiously this is needed when redirecting to a file so the next emitted
            # line isn't attached to the (completed) progress bar.
            console.line()

    with open(results_path, "w", encoding="utf-8") as f:
        raw = dataclasses.asdict(analysis)
        json.dump(raw, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    console.line()
    panel = make_analysis_summary(analysis)
    console.print(panel)


@main.command()
@click.argument(
    "analysis-path",
    metavar="analysis",
    type=click.Path(resolve_path=True, exists=True, readable=True, path_type=Path),
)
@click.argument("key", metavar="project[:file]", default="", required=False)
def show(analysis_path: Path, key: str) -> None:
    """
    Show results or metadata from an analysis.
    """
    analysis = Analysis.load(json.loads(analysis_path.read_text("utf-8")))
    console.log(f"Loaded analysis: {analysis_path}\n")

    project_key, _, file_key = key.partition(":")
    project_key = project_key.casefold()
    if project_key and file_key:
        try:
            result = analysis.results[project_key][file_key]
        except KeyError:
            console.print(f"[bold red]{project_key}:{file_key} couldn't be found.")
            sys.exit(1)

        if isinstance(result, NothingChangedResult):
            console.print(f"[bold {NOTHING_CHANGED_COLOR}]Nothing-changed.")
        elif isinstance(result, FailedResult):
            console.print(f"[bold red]{escape(result.error)}")
            console.print(f"[red]-> {escape(result.message)}")
        elif isinstance(result, ReformattedResult):
            diff = unified_diff(result.src, result.dst, f"a/{file_key}", f"b/{file_key}")
            console.print(color_diff(diff), highlight=False)

    elif project_key and not file_key:
        # TODO: implement a list view
        # TODO: implement a diff + failures view
        console.print("[bold red]show-ing a project is not implemented, sorry!")
        sys.exit(26)

    else:
        panel = make_analysis_summary(analysis)
        console.print(panel)


@main.command()
@click.argument(
    "analysis-path",
    metavar="analysis",
    type=click.Path(resolve_path=True, exists=True, readable=True, path_type=Path),
)
@click.argument("field", type=click.Choice(["src", "dst"], case_sensitive=False))
@click.argument("key", metavar="project:file")
@click.option("-q", "--quiet", is_flag=True, help="Suppress log output.")
def inspect(analysis_path: Path, field: str, key: str, quiet: bool) -> None:
    """
    Query file result fields in the raw analysis data.
    """
    analysis = Analysis.load(json.loads(analysis_path.read_text("utf-8")))
    if not quiet:
        console.log(f"Loaded analysis: {analysis_path}\n")

    project_key, _, file_key = key.partition(":")
    try:
        result = analysis.results[project_key][file_key]
    except KeyError:
        console.print(f"[bold red]{project_key}:{file_key} couldn't be found.")
        sys.exit(1)

    if not hasattr(result, field):
        console.print(f"[bold red]{key} doesn't contain the '{field}' field.")
        console.print(f"[bold]-> FYI the file's status is {result.type}")
        sys.exit(1)

    console.print(Syntax(getattr(result, field), "python"))


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

    first = Analysis.load(json.loads(analysis_one.read_text("utf-8")))
    console.log(f"Loaded first analysis: {analysis_one}")
    second = Analysis.load(json.loads(analysis_two.read_text("utf-8")))
    console.log(f"Loaded second analysis: {analysis_two}")

    # TODO: Gracefully warn but accept analyses that weren't set up the exact same way.
    if set(first.projects) ^ set(second.projects) or not all(
        first.projects[name] == second.projects[name] for name in first.projects
    ):
        console.print("[bold red]\nThe two analyses don't have the same set of projects.")
        console.print(
            "[italic]-> Eventually this will be just a warning, but that's a TODO"
        )
        sys.exit(1)

    console.line()
    if first.results == second.results:
        console.print(f"[bold {RESULT_COLORS['nothing-changed']}]Nothing-changed.")
        sys.exit(0)
    else:
        console.print(f"[bold {RESULT_COLORS['reformatted']}]Differences found.")
        sys.exit(1 if check else 0)


@main.command("show-failed")
@click.argument(
    "analysis-path",
    metavar="analysis",
    type=click.Path(resolve_path=True, exists=True, readable=True, path_type=Path),
)
@click.argument(
    "key",
    metavar="project",
    default="",
    callback=lambda c, p, s: s.casefold(),
    required=False,
)
@click.option(
    "--check", is_flag=True, help="Return a non-zero exit code if there's a failure."
)
def show_failed(analysis_path: Path, key: str, check: bool) -> None:
    """
    Show and check for failed files in an analysis.
    """
    analysis = Analysis.load(json.loads(analysis_path.read_text("utf-8")))
    console.log(f"Loaded analysis: {analysis_path}\n")

    if key and key not in analysis.projects:
        console.print(f"[bold red]The project '{key}' couldn't be found.")
        sys.exit(1)

    failed_projects = 0
    failed_files = 0
    for proj_name, proj_results in analysis.results.items():
        if key and key != proj_name:
            continue

        failed = filter_results(proj_results, "failed")
        if failed:
            console.print(f"[bold red]{proj_name}:", highlight=False)
            for number, (file, result) in enumerate(failed.items(), start=1):
                # I could just write even more overloads for filter_results so this isn't
                # necesssary but that seems overkill for the time being.
                assert isinstance(result, FailedResult)
                s = f"{number}. {file}: {escape(result.error)} - {escape(result.message)}"
                console.print(Padding(s, (0, 0, 0, 2)), highlight=False)
                failed_files += 1
            failed_projects += 1
            console.line()

    console.print(f"[bold]# of failed files: {failed_files}")
    console.print(f"[bold]# of failed projects: {failed_projects}")
    if check:
        sys.exit(bool(failed_files))
