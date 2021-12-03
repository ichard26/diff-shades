# =============================
# > Command implementations
# ============================

import dataclasses
import json
import os
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ContextManager, Optional, Set

if sys.version_info >= (3, 8):
    from typing import Final
else:
    from typing_extensions import Final

import click
import rich
import rich.traceback
from rich.markup import escape
from rich.padding import Padding
from rich.syntax import Syntax
from rich.theme import Theme

import diff_shades
from diff_shades.analysis import GIT_BIN, RESULT_COLORS, analyze_projects, setup_projects
from diff_shades.config import PROJECTS
from diff_shades.output import (
    color_diff,
    make_analysis_summary,
    make_rich_progress,
    unified_diff,
)
from diff_shades.results import CACHE_DIR, Analysis, filter_results, load_analysis

console: Final = rich.get_console()
normalize_input: Final = (
    lambda ctx, param, val: val.casefold() if val is not None else None
)


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
    # fmt: off
    theme = Theme({
        "error": "bold red",
        "warning": "bold yellow",
        "info": "bold",
        **RESULT_COLORS
    })
    # fmt: on
    rich.reconfigure(log_path=False, color_system=color_mode_key[no_color], theme=theme)
    os.makedirs(CACHE_DIR, exist_ok=True)


# fmt: off
@main.command()
@click.argument(
    "results-path", metavar="results-filepath",
    type=click.Path(resolve_path=True, readable=False, writable=True, path_type=Path)
)
@click.option(
    "-s", "--select",
    multiple=True,
    callback=lambda ctx, param, values: {p.strip().casefold() for p in values},
    help="Select projects from the main list."
)
@click.option(
    "-e", "--exclude",
    multiple=True,
    callback=lambda ctx, param, values: {p.strip().casefold() for p in values},
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
        console.print(f"[error]Couldn't import black: {err}")
        console.print("[info]-> This command requires an installation of Black.")
        sys.exit(1)

    if GIT_BIN is None:
        console.print("[error]Couldn't find a Git executable.")
        console.print("[info]-> This command requires git sadly enough.")
        sys.exit(1)

    if results_path.exists() and results_path.is_file():
        console.log(f"[warning]Overwriting {results_path} as it already exists!")
    elif results_path.exists() and results_path.is_dir():
        console.print(f"[error]{results_path} is a pre-existing directory.")
        console.print("[info]-> Can't continue as I won't overwrite a directory.")
        sys.exit(1)

    if repeat_projects_from:
        analysis, cached = load_analysis(repeat_projects_from)
        console.log(
            f"[bold]Loaded blueprint analysis: {repeat_projects_from}"
            + (" (cached)" if cached else "")
        )
        projects = list(analysis.projects.values())
    else:
        projects = PROJECTS

    projects = [p for p in projects if p.name not in exclude]
    if select:
        projects = [p for p in projects if p.name in select]
    for proj in projects:
        if not proj.supported_by_runtime:
            projects.remove(proj)
            console.log(
                "[warning]"
                f"Skipping {proj.name} as it requires python{proj.python_requires}"
            )

    workdir_provider: ContextManager
    if work_dir:
        workdir_provider = nullcontext(work_dir)
        os.makedirs(work_dir, exist_ok=True)
    else:
        workdir_provider = TemporaryDirectory(prefix="diff-shades-")
    with workdir_provider as _work_dir:
        work_dir = Path(_work_dir)
        with make_rich_progress() as progress:
            setup_task = progress.add_task(
                "[bold blue]Setting up projects", total=len(projects)
            )
            projects = setup_projects(projects, work_dir, progress, setup_task, verbose)

        with make_rich_progress() as progress:
            analyze_task = progress.add_task("[bold magenta]Running black")
            results = analyze_projects(
                projects, work_dir, progress, analyze_task, verbose
            )
        metadata = {
            "black-version": black.__version__,
            "created-at": datetime.now(timezone.utc).isoformat(),
        }
        analysis = Analysis(
            projects={proj.name: proj for proj in projects},
            results=results,
            metadata=metadata,
        )

    with open(results_path, "w", encoding="utf-8") as f:
        raw = dataclasses.asdict(analysis)
        # Escaping non-ASCII characters in the JSON blob is very important to keep
        # memory usage and load times managable. CPython (not sure about other
        # implementations) guarantees that string index operations will be roughly
        # constant time which flies right in the face of the efficient UTF-8 format.
        # Hence why str instances transparently switch between Latin-1 and other
        # constant-size formats. In the worst case a UCS-4 is used exploding
        # memory usage (and load times as memory is not infinitely fast). I've seen
        # peaks of 1GB max RSS with 100MB analyses which is just not OK.
        # See also: https://stackoverflow.com/a/58080893
        json.dump(raw, f, separators=(",", ":"), ensure_ascii=True)
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
@click.argument(
    "project_key", metavar="project", callback=normalize_input, required=False
)
@click.argument("file_key", metavar="file", required=False)
@click.argument("field_key", metavar="field", callback=normalize_input, required=False)
def show(
    analysis_path: Path,
    project_key: Optional[str],
    file_key: Optional[str],
    field_key: Optional[str],
) -> None:
    """
    Show results or metadata from an analysis.
    """
    analysis, cached = load_analysis(analysis_path)
    console.log(f"Loaded analysis: {analysis_path}{' (cached)' if cached else ''}")
    console.line()

    if project_key and file_key:
        try:
            result = analysis.results[project_key][file_key]
        except KeyError:
            console.print(f"[error]'{file_key}' couldn't be found under {project_key}.")
            sys.exit(1)

        if field_key:
            if not hasattr(result, field_key):
                console.print(f"[error]{file_key} has no '{field_key}' field.")
                console.print(f"[bold]-> FYI the file's status is {result.type}")
                sys.exit(1)

            console.print(Syntax(getattr(result, field_key), "python"))

        elif result.type == "nothing-changed":
            console.print("[bold nothing-changed]Nothing-changed.")
        elif result.type == "failed":
            console.print(f"[error]{escape(result.error)}")
            console.print(f"[info]-> {escape(result.message)}")
        elif result.type == "reformatted":
            diff = unified_diff(result.src, result.dst, f"a/{file_key}", f"b/{file_key}")
            console.print(color_diff(diff), highlight=False)

    elif project_key and not file_key:
        # TODO: implement a list view
        # TODO: implement a diff + failures view
        console.print("[error]show-ing a project is not implemented, sorry!")
        sys.exit(26)

    else:
        panel = make_analysis_summary(analysis)
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

    first, cached = load_analysis(analysis_one)
    console.log(f"Loaded first analysis: {analysis_one}{' (cached)' if cached else ''}")
    second, cached = load_analysis(analysis_two)
    console.log(f"Loaded second analysis: {analysis_two}{' (cached)' if cached else ''}")

    # TODO: Gracefully warn but accept analyses that weren't set up the exact same way.
    if set(first.projects) ^ set(second.projects) or not all(
        first.projects[name] == second.projects[name] for name in first.projects
    ):
        console.print("[error]\nThe two analyses don't have the same set of projects.")
        console.print(
            "[italic]-> Eventually this will be just a warning, but that's a TODO"
        )
        sys.exit(1)

    console.line()
    if first.results == second.results:
        console.print("[bold][nothing-changed]Nothing-changed.")
        sys.exit(0)
    else:
        console.print("[bold][reformatted]Differences found.")
        sys.exit(1 if check else 0)


@main.command("show-failed")
@click.argument(
    "analysis-path",
    metavar="analysis",
    type=click.Path(resolve_path=True, exists=True, readable=True, path_type=Path),
)
@click.argument("key", metavar="project", callback=normalize_input, required=False)
@click.option(
    "--check", is_flag=True, help="Return a non-zero exit code if there's a failure."
)
def show_failed(analysis_path: Path, key: Optional[str], check: bool) -> None:
    """
    Show and check for failed files in an analysis.
    """
    analysis, cached = load_analysis(analysis_path)
    console.log(f"Loaded analysis: {analysis_path}{' (cached)' if cached else ''}\n")

    if key and key not in analysis.projects:
        console.print(f"[error]The project '{key}' couldn't be found.")
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
                s = f"{number}. {file}: {escape(result.error)} - {escape(result.message)}"
                console.print(Padding(s, (0, 0, 0, 2)), highlight=False)
                failed_files += 1
            failed_projects += 1
            console.line()

    console.print(f"[bold]# of failed files: {failed_files}")
    console.print(f"[bold]# of failed projects: {failed_projects}")
    if check:
        sys.exit(bool(failed_files))
