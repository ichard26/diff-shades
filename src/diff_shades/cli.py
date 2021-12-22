# =============================
# > Command implementations
# ============================

import atexit
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator, Optional, Sequence, Set, Tuple

if sys.version_info >= (3, 8):
    from typing import Final
else:
    from typing_extensions import Final

import click
import rich
import rich.traceback
from rich.markup import escape
from rich.padding import Padding
from rich.theme import Theme

import diff_shades
import diff_shades.results
from diff_shades.analysis import (
    GIT_BIN,
    RESULT_COLORS,
    analyze_projects,
    run_cmd,
    setup_projects,
)
from diff_shades.config import PROJECTS, Project
from diff_shades.output import (
    color_diff,
    make_analysis_summary,
    make_comparison_summary,
    make_project_details_table,
    make_rich_progress,
)
from diff_shades.results import Analysis, ProjectResults, diff_two_results, filter_results

console: Final = rich.get_console()
normalize_input: Final = lambda ctx, param, v: v.casefold() if v is not None else None
READABLE_FILE: Final = click.Path(
    resolve_path=True, exists=True, dir_okay=False, readable=True, path_type=Path
)
WRITABLE_FILE: Final = click.Path(
    resolve_path=True, dir_okay=False, readable=False, writable=True, path_type=Path
)


def load_analysis(path: Path, msg: str = "analysis", quiet: bool = False) -> Analysis:
    analysis, cached = diff_shades.results.load_analysis(path)
    if not quiet:
        console.log(f"Loaded {msg}: {path}{' (cached)' if cached else ''}")

    return analysis


@contextmanager
def get_work_dir(*, use: Optional[Path] = None) -> Iterator[Path]:
    if use:
        use.mkdir(parents=True, exist_ok=True)
        yield use
    else:
        with TemporaryDirectory(prefix="diff-shades-") as wd:
            yield Path(wd)


def compare_project_pair(
    project: Project, results: ProjectResults, results2: ProjectResults
) -> bool:
    found_difference = False
    header = f"\[{project.name} - {project.url}]"
    if "github" in project.url:
        rev_link = project.url[:-4] + f"/tree/{project.commit}"
        revision = f"╰─> [link={rev_link}]revision {project.commit}[/link]"
    else:
        revision = f"╰─> revision {project.commit}"

    for file, r1 in results.items():
        r2 = results2[file]
        if r1 != r2:
            if not found_difference:
                console.print(f"[bold][reformatted]{header}[/][/]")
                console.print(f"[reformatted]{revision}")
                found_difference = True

            diff = diff_two_results(r1, r2, file=f"{project.name}:{file}", diff_failure=True)
            console.print(color_diff(diff), highlight=False)

    return found_difference


def check_black_args(args: Sequence[str]) -> None:
    if "--fast" in args or "--safe" in args:
        console.log("[warning]--fast/--safe is ignored, Black is always ran in safe mode.")
    try:
        run_cmd([sys.executable, "-m", "black", "-", *args], input="daylily")
    except subprocess.CalledProcessError as e:
        console.print(f"[error]Invalid black arguments: {' '.join(args)}\n")
        console.print(e.stdout.strip(), style="italic")
        sys.exit(1)


@click.group()
@click.option(
    "--no-color/--force-color", default=None, help="Force disable/enable colored output."
)
@click.option("--show-locals", is_flag=True, help="Show locals for unhandled exceptions.")
@click.option(
    "--dump-html", type=WRITABLE_FILE, help="Save a HTML copy of the emitted output."
)
@click.option("--clear-cache", is_flag=True, help="Drop all cached analyses.")
@click.version_option(version=diff_shades.__version__, prog_name="diff-shades")
def main(
    no_color: Optional[bool], show_locals: bool, dump_html: Optional[Path], clear_cache: bool
) -> None:
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
     - Per-project python_requires support
     - Custom per-analysis formatting configuration
     - Oh and of course, pretty output!

    \b
    Potential tasks / additionals:
     - jupyter notebook support
     - even more helpful output
     - better UX (particularly when things go wrong)
     - code cleanup as my code is messy as usual :p
    """
    rich.traceback.install(suppress=[click], show_locals=show_locals)
    color_mode_key = {True: None, None: "auto", False: "truecolor"}
    color_mode = color_mode_key[no_color]
    width: Optional[int] = None
    if os.getenv("GITHUB_ACTIONS") == "true":
        # Force colors when running on GitHub Actions (unless --no-color is passed).
        if no_color is not True:
            color_mode = "truecolor"
        # Annoyingly enough rich autodetects the width to be far too small on GHA.
        width = 115
    # fmt: off
    theme = Theme({
        "error": "bold red",
        "warning": "bold yellow",
        "info": "bold",
        **RESULT_COLORS
    })
    # fmt: on
    rich.reconfigure(
        log_path=False, record=dump_html, color_system=color_mode, theme=theme, width=width
    )
    if clear_cache:
        shutil.rmtree(diff_shades.results.CACHE_DIR)
    diff_shades.results.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if dump_html:
        atexit.register(console.save_html, path=dump_html)


# fmt: off
@main.command()
@click.argument("results-path", metavar="results-filepath", type=WRITABLE_FILE)
@click.argument("black-args", metavar="[-- black-args]", nargs=-1, type=click.UNPROCESSED)
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
    "-w", "--work-dir", "cli_work_dir",
    type=click.Path(dir_okay=True, file_okay=False, resolve_path=True, path_type=Path),
    help=(
        "Directory where project clones are used / stored. By default a"
        " temporary directory is used which will be cleaned up at exit."
        " Use this option to reuse or cache projects."
    )
)
@click.option(
    "--repeat-projects-from",
    type=READABLE_FILE,
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
    black_args: Tuple[str, ...],
    select: Set[str],
    exclude: Set[str],
    cli_work_dir: Optional[Path],
    repeat_projects_from: Optional[Path],
    verbose: bool,
) -> None:
    """Run Black against 'millions' of LOC and save the results."""

    try:
        import black
    except ImportError as err:
        console.print(f"[error]Couldn't import black: {err}")
        console.print("[info]╰─> This command requires an installation of Black.")
        sys.exit(1)

    if GIT_BIN is None:
        console.print("[error]Couldn't find a Git executable.")
        console.print("[info]╰─> This command requires git sadly enough.")
        sys.exit(1)

    if results_path.exists() and results_path.is_file():
        console.log(f"[warning]Overwriting {results_path} as it already exists!")
    elif results_path.exists() and results_path.is_dir():
        console.print(f"[error]{results_path} is a pre-existing directory.")
        console.print("[info]╰─> Can't continue as I won't overwrite a directory.")
        sys.exit(1)

    if black_args:
        check_black_args(black_args)

    if repeat_projects_from:
        analysis = load_analysis(repeat_projects_from, msg="blueprint analysis")
        projects = list(analysis.projects.values())
    else:
        projects = PROJECTS

    projects = [p for p in projects if p.name not in exclude]
    if select:
        projects = [p for p in projects if p.name in select]
    for proj in projects:
        if not proj.supported_by_runtime:
            projects.remove(proj)
            msg = f"[warning]Skipping {proj.name} as it requires python{proj.python_requires}"
            console.log(msg)

    with get_work_dir(use=cli_work_dir) as work_dir:
        with make_rich_progress() as progress:
            title = "[bold cyan]Setting up projects"
            task1 = progress.add_task(title, total=len(projects))
            prepared = setup_projects(projects, work_dir, black_args, progress, task1, verbose)

        with make_rich_progress() as progress:
            task2 = progress.add_task("[bold magenta]Running black")
            results = analyze_projects(prepared, work_dir, progress, task2, verbose)

        metadata = {
            "black-version": black.__version__,
            "black-extra-args": black_args,
            "created-at": datetime.now(timezone.utc).isoformat(),
            "data-format": 1,
        }
        analysis = Analysis(
            projects={p.name: p for p, _, _ in prepared}, results=results, metadata=metadata
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
        json.dump(raw, f, indent=2, ensure_ascii=True)
        f.write("\n")

    console.line()
    panel = make_analysis_summary(analysis)
    console.print(panel)


@main.command()
@click.argument("analysis-path", metavar="analysis", type=READABLE_FILE)
@click.argument("project_key", metavar="[project]", callback=normalize_input, required=False)
@click.argument("file_key", metavar="[file]", required=False)
@click.argument("field_key", metavar="[field]", callback=normalize_input, required=False)
@click.option("-q", "--quiet", is_flag=True, help="Suppress log messages.")
def show(
    analysis_path: Path,
    project_key: Optional[str],
    file_key: Optional[str],
    field_key: Optional[str],
    quiet: bool,
) -> None:
    """
    Show results or metadata from an analysis.
    """
    analysis = load_analysis(analysis_path, quiet=quiet)
    if not quiet:
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

            console.print(getattr(result, field_key), highlight=False, soft_wrap=True)

        elif result.type == "nothing-changed":
            console.print("[bold][nothing-changed]Nothing-changed.")
        elif result.type == "failed":
            console.print(f"[error]{escape(result.error)}")
            console.print(f"[info]-> {escape(result.message)}")
        elif result.type == "reformatted":
            diff = result.diff(file_key)
            console.print(color_diff(diff), highlight=False)

    elif project_key and not file_key:
        # TODO: implement a list view
        # TODO: implement a diff + failures view
        console.print("[error]show-ing a project is not implemented, sorry!")
        sys.exit(26)

    else:
        panel = make_analysis_summary(analysis)
        console.print(panel)
        console.line()
        project_table = make_project_details_table(analysis)
        console.print(project_table)


@main.command()
@click.argument("analysis-path1", metavar="analysis-one", type=READABLE_FILE)
@click.argument("analysis-path2", metavar="analysis-two", type=READABLE_FILE)
@click.argument("project_key", metavar="[project]", callback=normalize_input, required=False)
@click.option("--check", is_flag=True, help="Return 1 if differences were found.")
@click.option("--diff", "diff_mode", is_flag=True, help="Show a diff of the differences.")
@click.option("--list", "list_mode", is_flag=True, help="List the differing files.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress log messages.")
def compare(
    analysis_path1: Path,
    analysis_path2: Path,
    check: bool,
    project_key: Optional[str],
    diff_mode: bool,
    list_mode: bool,
    quiet: bool,
) -> None:
    """Compare two analyses for differences in the results."""

    if diff_mode and list_mode:
        console.print("[error]--diff and --list can't be used at the same time.")
        sys.exit(1)

    analysis_one = load_analysis(analysis_path1, msg="first analysis", quiet=quiet)
    analysis_two = load_analysis(analysis_path2, msg="second analysis", quiet=quiet)

    if project_key is None:
        names = {*analysis_one.projects, *analysis_two.projects}
    else:
        names = {project_key}
    shared_projects = []
    for n in sorted(names):
        if n not in analysis_one.projects or n not in analysis_two.projects:
            console.log(f"[warning]Skipping {n} as it's not present in both.")
        elif analysis_one.projects[n] != analysis_two.projects[n]:
            console.log(f"[warning]Skipping {n} as it was configured differently.")
        else:
            proj = analysis_one.projects[n]
            shared_projects.append((proj, analysis_one.results[n], analysis_two.results[n]))

    console.line()
    if all(proj1 == proj2 for _, proj1, proj2 in shared_projects):
        console.print("[bold][nothing-changed]Nothing-changed.")
        sys.exit(0)

    panel = make_comparison_summary((p1, p2) for _, p1, p2 in shared_projects)
    console.print(panel)
    console.line()
    if diff_mode:
        for project, proj_results, proj_results2 in shared_projects:
            if compare_project_pair(project, proj_results, proj_results2):
                console.line()
    elif list_mode:
        console.print("[error]--list is not implemented yet")
        sys.exit(1)
    else:
        console.print("[bold][reformatted]Differences found.")

    sys.exit(1 if check else 0)


@main.command("show-failed")
@click.argument("analysis-path", metavar="analysis", type=READABLE_FILE)
@click.argument("key", metavar="project", callback=normalize_input, required=False)
@click.option("--check", is_flag=True, help="Return 1 if there's a failure.")
def show_failed(analysis_path: Path, key: Optional[str], check: bool) -> None:
    """
    Show and check for failed files in an analysis.
    """
    analysis = load_analysis(analysis_path)
    console.line()

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
