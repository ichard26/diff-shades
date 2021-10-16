# =============================
# > Command implementations
# ============================

import json
import sys
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional

import click
import rich
import rich.progress
import rich.traceback

import diff_shades
from diff_shades.analysis import GIT_BIN, analyze_projects, setup_projects
from diff_shades.config import PROJECTS, Project

console = rich.get_console()


@click.group()
@click.version_option(version=diff_shades.__version__, prog_name="diff-shades")
def main():
    """
    The Black shade analyser and comparsion tool.

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
     - Oh and of course, pretty output!

    \b
    Potential tasks / additionals:
     - jupyter notebook support
     - per-project python_requires support
     - even more helpful output
     - stronger diffing abilities
     - better UX (particularly when things go wrong)
     - so much code cleanup - like a lot :p
    """
    rich.traceback.install(suppress=[click])
    rich.reconfigure(log_path=False)
    console.hi = False


@main.command()
@click.argument(
    "results-filepath", metavar="results-filepath",
    type=click.Path(resolve_path=True, path_type=Path)
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
        "temporary directory is used which will be cleaned up at exit."
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
def analyze(
    results_filepath: Path,
    select: List[str],
    exclude: List[str],
    work_dir: Optional[Path],
    repeat_projects_from: Optional[Path],
    verbose: bool
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
        projects = [Project(**proj.metadata) for proj in AnalysisData(data).projects.values()]
    else:
        projects = PROJECTS

    if exclude:
        excluders = [e.casefold().strip() for e in exclude]
        projects = [p for p in projects if p.name not in excluders]
    if select:
        selectors = [project.casefold().strip() for project in select]
        projects = [p for p in projects if p.name in selectors]
    projects = [p for p in projects if p.supported_by_runtime]

    workdir_provider: ContextManager
    if work_dir:
        workdir_provider = nullcontext(work_dir)
        if not work_dir.exists():
            work_dir.mkdir()
    else:
        workdir_provider = TemporaryDirectory(prefix="diff-shades-")

    with workdir_provider as _work_dir:
        setup_progress = rich.progress.Progress(
            "[progress.description]{task.description}",
            rich.progress.BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            "•",
            "[progress.percentage]{task.completed}/{task.total}",
            "•",
            rich.progress.TimeElapsedColumn(),
            console=console,
        )
        with setup_progress as progress:
            setup_task = progress.add_task("[bold blue]Setting up projects", total=len(projects))
            prepped_projects = setup_projects(
                projects, Path(_work_dir), progress, setup_task, verbose
            )
        if not console.is_terminal:
            # Curiously this is needed when redirecting to a file so the next emitted
            # line isn't attached to the (completed) progress bar.
            console.line()

        results = {}
        analyze_progress = rich.progress.Progress(
            "[progress.description]{task.description}",
            rich.progress.BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            # "•",
            # "[progress.percentage]{task.completed}/{task.total}",
            "•",
            rich.progress.TimeElapsedColumn(),
            console=console,
        )
        with analyze_progress as progress:
            analyze_task = progress.add_task("[bold magenta]Running black")
            results["projects"] = analyze_projects(
                prepped_projects, progress, analyze_task, verbose
            )
        results["black-version"] = black.__version__

    # with open(result_filepath, "w", encoding="utf-8") as f:
    #     json.dump(results, f, separators=(",", ":"), ensure_ascii=False)
    #     f.write("\n")
