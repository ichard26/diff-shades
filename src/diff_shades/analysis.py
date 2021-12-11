# ================================
# > Formatting results collection
# =============================

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple

if sys.version_info >= (3, 8):
    from typing import Final
else:
    from typing_extensions import Final

if TYPE_CHECKING:
    import black

import rich
import rich.progress

from diff_shades.config import Project
from diff_shades.results import (
    FailedResult,
    FileResult,
    NothingChangedResult,
    ProjectResults,
    ReformattedResult,
    get_overall_result,
)

GIT_BIN: Final = shutil.which("git")
RESULT_COLORS: Final = {
    "reformatted": "cyan",
    "nothing-changed": "magenta",
    "failed": "red",
}
run_cmd: Final = partial(
    subprocess.run,
    check=True,
    encoding="utf8",
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
console: Final = rich.get_console()

# =====================
# > Setup and analysis
# ==================


def clone_repo(url: str, *, to: Path, sha: Optional[str] = None) -> None:
    assert GIT_BIN
    if sha:
        if not to.exists():
            to.mkdir()
        run_cmd(["git", "init"], cwd=to)
        run_cmd(["git", "fetch", url, sha], cwd=to)
        run_cmd(["git", "checkout", sha], cwd=to)
    else:
        run_cmd([GIT_BIN, "clone", url, "--depth", "1", str(to)])


CommitMsg = str
CommitSHA = str


def get_commit(repo: Path) -> Tuple[CommitSHA, CommitMsg]:
    assert GIT_BIN
    proc = run_cmd([GIT_BIN, "log", "--format=%H:%s", "-n1"], cwd=repo)
    output = proc.stdout.strip()
    sha, _, msg = output.partition(":")
    return sha, msg


def setup_projects(
    projects: List[Project],
    workdir: Path,
    progress: rich.progress.Progress,
    task: rich.progress.TaskID,
    verbose: bool,
) -> List[Project]:
    bold = "[bold]" if verbose else ""
    ready = []
    for proj in projects:
        target = Path(workdir, proj.name)
        can_reuse = False
        if target.exists():
            if proj.commit is None:
                can_reuse = True
            else:
                sha, _ = get_commit(target)
                can_reuse = proj.commit == sha

        if can_reuse:
            progress.console.log(f"{bold}Using pre-existing clone of {proj.name} - {proj.url}")
        else:
            clone_repo(proj.url, to=target, sha=proj.commit)
            progress.console.log(f"{bold}Cloned {proj.name} - {proj.url}")

        commit_sha, commit_msg = get_commit(target)
        if verbose:
            progress.console.log(f"[dim]  commit -> {commit_msg}", highlight=False)
            progress.console.log(f"[dim]  commit -> {commit_sha}")
        proj = replace(proj, commit=commit_sha)
        ready.append(proj)
        progress.advance(task)

    return ready


@contextmanager
def suppress_output() -> Iterator:
    from unittest.mock import patch

    with open(os.devnull, "w", encoding="utf-8") as blackhole:
        with redirect_stdout(blackhole), redirect_stderr(blackhole):
            with patch("click.echo", new=lambda *args, **kwargs: None):
                yield


# HACK: I know this is hacky but the benefit is I don't need to copy and
# paste a bunch of black's argument parsing, file discovery, and
# configuration code. I also get to keep the pretty output since I can
# directly invoke black.format_file_contents :D


def get_files_and_mode(project: Project, path: Path) -> Tuple[List[Path], "black.Mode"]:
    # This pulls in a ton of stuff including the heavy asyncio. Let's avoid the import cost
    # unless till the last possible moment.
    from unittest.mock import patch

    import black

    files: List[Path] = []
    mode = None

    def shim(sources: List[Path], *args: Any, **kwargs: Any) -> None:
        nonlocal files, mode
        files.extend(sources)
        mode = kwargs["mode"]

    with suppress_output(), patch("black.reformat_many", new=shim):
        cmd = [str(path), *project.custom_arguments, "--check"]
        black.main(cmd, standalone_mode=False)

    assert files and isinstance(mode, black.FileMode)
    return sorted(p for p in files if p.suffix in (".py", ".pyi")), mode


def check_file(path: Path, *, mode: Optional["black.FileMode"] = None) -> FileResult:
    import black

    # TODO: record log files if available
    # TODO: allow more control w/ black.Mode so we could use diff-shades to compare
    # for example, no-ESP vs ESP.

    mode = mode or black.FileMode()
    if path.suffix == ".pyi":
        mode = replace(mode, is_pyi=True)

    src = path.read_text("utf8")
    try:
        with suppress_output():
            dst = black.format_file_contents(src, fast=False, mode=mode)
    except black.NothingChanged:
        return NothingChangedResult(src=src)

    except Exception as err:
        return FailedResult(src=src, error=err.__class__.__name__, message=str(err))

    return ReformattedResult(src=src, dst=dst)


def check_file_shim(
    # Unfortunately there's nothing like imap + starmap in multiprocessing.
    arguments: Tuple[Path, Path, "black.FileMode"]
) -> Tuple[str, FileResult]:
    file, project_path, mode = arguments
    result = check_file(file, mode=mode)
    normalized_path = file.relative_to(project_path).as_posix()
    return (normalized_path, result)


def analyze_projects(
    projects: List[Project],
    work_dir: Path,
    progress: rich.progress.Progress,
    task: rich.progress.TaskID,
    verbose: bool,
) -> Dict[str, ProjectResults]:
    # Slow import, let's not pay all of the time (this makes show and friends faster).
    import multiprocessing

    # For consistency w/ Windows so things don't unintentionally work only on Linux.
    multiprocessing.set_start_method("spawn")

    # TODO: refactor this and related functions cuz it's a bit of a mess :)
    files_and_modes = [get_files_and_mode(proj, work_dir / proj.name) for proj in projects]
    file_count = sum(len(files) for files, _ in files_and_modes)
    progress.update(task, total=file_count)
    bold = "[bold]" if verbose else ""

    def check_project_files(
        files: List[Path], project_path: Path, *, mode: "black.FileMode"
    ) -> ProjectResults:
        file_results = ProjectResults()
        data_packets = [(file_path, project_path, mode) for file_path in files]
        for (filepath, result) in pool.imap(check_file_shim, data_packets):
            if verbose:
                console.log(f"  {filepath}: [{result.type}]{result.type}")
            file_results[filepath] = result
            progress.advance(task)
            progress.advance(project_task)
        return file_results

    with multiprocessing.Pool() as pool:
        results = {}
        for project, (files, mode) in zip(projects, files_and_modes):
            project_task = progress.add_task(f"[bold]-> {project.name}", total=len(files))
            if verbose:
                console.log(f"[bold]Checking {project.name} ({len(files)} files)")
            file_results = check_project_files(files, work_dir / project.name, mode=mode)
            results[project.name] = file_results
            overall_result = get_overall_result(file_results)
            coloring = RESULT_COLORS[overall_result]
            console.log(f"{bold}{project.name} finished as [{coloring}]{overall_result}")
            progress.remove_task(project_task)

    return results
