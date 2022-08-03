# =============================
# > Command implementations
# ============================

import atexit
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
    from typing import Final, Literal
else:
    from typing_extensions import Final, Literal

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
from diff_shades.results import (
    Analysis,
    ProjectResults,
    diff_two_results,
    filter_results,
    make_analysis_summary,
    make_comparison_summary,
    make_project_details_table,
    save_analysis,
)
from diff_shades.utils import DSError, color_diff, make_rich_progress

console: Final = rich.get_console()
normalize_input: Final = lambda ctx, param, v: v.casefold() if v is not None else None
READABLE_FILE: Final = click.Path(
    resolve_path=True, exists=True, dir_okay=False, path_type=Path
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
    """Returns `use` (after making sure it exists) falling back to a
    TemporaryDirectory if it's None.
    """
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
    """Assert `args` are valid and warn on questionable arguments."""
    if "--fast" in args or "--safe" in args:
        console.log("[warning]--fast/--safe is ignored, Black is always ran in safe mode.")
    if set(args).intersection({"--force-exclude", "--exclude", "--include", "-e", "-i"}):
        console.log("[warning]File discovery options only play nice with one project!")
    try:
        run_cmd([sys.executable, "-m", "black", "-", *args], input="daylily")
    except subprocess.CalledProcessError as e:
        console.print(f"[error]Invalid black arguments: {' '.join(args)}\n")
        console.print(e.stdout.strip(), style="italic")
        sys.exit(1)


def entrypoint() -> None:
    try:
        main()
    except DSError as err:
        console.print(err)
        sys.exit(2)


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
    if os.getenv("GITHUB_ACTIONS") == "true":
        # Makes it easier to debug failures on CI.
        show_locals = True
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
    theme = Theme(
        {"error": "bold red", "warning": "bold yellow", "info": "bold", **RESULT_COLORS}
    )
    rich.reconfigure(
        log_path=False, record=dump_html, color_system=color_mode, theme=theme, width=width
    )
    if clear_cache:
        shutil.rmtree(diff_shades.results.CACHE_DIR)
    diff_shades.results.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if dump_html:
        atexit.register(console.save_html, path=str(dump_html))


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
    "-r", "--repeat-projects-from",
    type=READABLE_FILE,
    help="Use the same projects (and commits!) used during another analysis."
)
@click.option(
    "-S/-P", "--force-stable-style/--force-preview-style", "force_style",
    default=None,
    callback=lambda ctx, p, v: {False: "preview", True: "stable", None: None}[v],
    help="Forcefully use the stable or preview style for all projects."
)
@click.option(
    "-v", "--verbose",
    count=True,
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
    force_style: Optional[Literal["stable", "preview"]],
    verbose: int,
) -> None:
    """Run Black against 'millions' of LOC and save the results."""

    try:
        import black
    except ImportError as err:
        raise DSError(f"Couldn't import black: {err}", tip="This command requires Black.")

    if GIT_BIN is None:
        raise DSError(
            "Couldn't find a Git executable.", tip="This command requires git sadly enough."
        )

    if results_path.exists() and results_path.is_file():
        console.log(f"[warning]Overwriting {results_path} as it already exists!")
    elif results_path.exists() and results_path.is_dir():
        raise DSError(f"{results_path} is a pre-existing directory.")

    if force_style:
        try:
            black.FileMode(preview=True)
        except TypeError:
            console.log(
                "[warning]Installed black doesn't support --preview, ignoring style flag."
            )
            force_style = None

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
            prepared = setup_projects(
                projects, work_dir, force_style, black_args, progress, task1, verbose > 0
            )

        with make_rich_progress() as progress:
            task2 = progress.add_task("[bold magenta]Running black")
            results = analyze_projects(prepared, work_dir, progress, task2, verbose > 1)

        metadata = {
            "black-version": black.__version__,
            "black-extra-args": black_args,
            "forced-style": force_style,
            "created-at": datetime.now(timezone.utc).isoformat(),
            "data-format": 1.3,
        }
        analysis = Analysis(
            projects={p.name: p for p, _, _ in prepared}, results=results, metadata=metadata
        )

    save_analysis(results_path, analysis)
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
            raise DSError(f"'{file_key}' couldn't be found under {project_key}.")

        if field_key:
            if not hasattr(result, field_key):
                raise DSError(
                    f"{file_key} has no '{field_key}' field.",
                    tip="FYI the file's status is {result.type}",
                )

            value = escape(getattr(result, field_key))
            end = "" if field_key in ("src", "dst") else "\n"
            console.print(value, end=end, highlight=False, soft_wrap=True)

        elif result.type == "nothing-changed":
            console.print("[bold][nothing-changed]Nothing-changed.")
        elif result.type == "failed":
            console.print(f"[error]{escape(result.error)}")
            console.print(f"╰─> {escape(result.message)}", highlight=False)
            if result.log is not None:
                console.line()
                console.print(escape(result.log), highlight=False)
        elif result.type == "reformatted":
            diff = result.diff(file_key)
            console.print(color_diff(diff), highlight=False)

    elif project_key and not file_key:
        # TODO: implement a list view
        # TODO: implement a diff + failures view
        raise DSError("show-ing a project is not implemented, sorry!")

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
@click.option("--diff", "format", flag_value="diff", help="Show a diff of the differences.")
@click.option("--list", "format", flag_value="list", help="List the differing files.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress log messages.")
def compare(
    analysis_path1: Path,
    analysis_path2: Path,
    check: bool,
    project_key: Optional[str],
    format: Literal[None, "diff", "list"],
    quiet: bool,
) -> None:
    """Compare two analyses for differences in the results."""

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
    panel = make_comparison_summary([(p1, p2) for _, p1, p2 in shared_projects])
    console.print(panel)
    console.line()
    if all(proj1 == proj2 for _, proj1, proj2 in shared_projects):
        console.print("[bold][nothing-changed]Nothing-changed.")
        sys.exit(0)

    if format == "diff":
        for project, proj_results, proj_results2 in shared_projects:
            if compare_project_pair(project, proj_results, proj_results2):
                console.line()
    elif format == "list":
        console.print("[error]--list is not implemented yet")
        sys.exit(1)
    else:
        console.print("[bold][reformatted]Differences found.")

    sys.exit(1 if check else 0)


@main.command("show-failed")
@click.argument("analysis-path", metavar="analysis", type=READABLE_FILE)
@click.argument("key", metavar="project", callback=normalize_input, required=False)
@click.option(
    "--show-log", is_flag=True, help="Show log files if present, otherwise tracebacks."
)
@click.option("--check", is_flag=True, help="Return 1 if there's a failure.")
@click.option(
    "--check-allow",
    multiple=True,
    callback=lambda ctx, p, v: set(v),
    help="Ignore these failures when determining return code (--check).",
)
def show_failed(
    analysis_path: Path,
    key: Optional[str],
    show_log: bool,
    check: bool,
    check_allow: Set[str],
) -> None:
    """
    Show and check for failed files in an analysis.
    """
    analysis = load_analysis(analysis_path)
    console.line()

    if key and key not in analysis.projects:
        raise DSError(f"The project '{key}' couldn't be found.")

    failed_projects = 0
    failed_files = 0
    disallowed_failures = 0
    for proj_name, proj_results in analysis.results.items():
        if key and key != proj_name:
            continue

        failed = filter_results(proj_results, "failed")
        failed_files += len(failed)
        failed_projects += int(bool(failed))
        if failed:
            console.print(f"[bold red]{proj_name}:", highlight=False)
            for number, (file, result) in enumerate(failed.items(), start=1):
                s = f"{number}. {file}: {escape(result.error)}"
                if result.message:
                    s += f" - {escape(result.message)}"
                if f"{proj_name}:{file}" in check_allow:
                    s += "[green] (allowed)[/]"
                else:
                    disallowed_failures += 1

                console.print(Padding(s, (0, 0, 0, 2), expand=False), highlight=False)
                if show_log:
                    escaped = escape(result.log or result.traceback)
                    padded = Padding(escaped, (0, 0, 0, 4), expand=False)
                    console.print(padded, highlight=False, style="dim")
            console.line()

    console.print(f"[bold]# of failed files: {failed_files}")
    console.print(f"[bold]# of failed projects: {failed_projects}")
    if check:
        sys.exit(disallowed_failures > 0)


if __name__ == "__main__":
    entrypoint()
