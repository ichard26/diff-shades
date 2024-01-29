# ================================
# > Formatting results collection
# =============================

import os
import shutil
import subprocess
import sys
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from functools import lru_cache, partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Tuple

if sys.version_info >= (3, 8):
    from typing import Final, Literal
else:
    from typing_extensions import Final, Literal

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
)

GIT_BIN: Final = shutil.which("git")
NUM_PROCESSES: Final = 2
RESULT_COLORS: Final = {"reformatted": "cyan", "nothing-changed": "magenta", "failed": "red"}
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
        run_cmd([GIT_BIN, "init"], cwd=to)
        run_cmd([GIT_BIN, "fetch", url, sha], cwd=to)
        run_cmd([GIT_BIN, "checkout", sha], cwd=to)
    else:
        run_cmd([GIT_BIN, "clone", url, "--depth", "1", str(to)])


CommitMsg = str
CommitSHA = str
PreparedProject = Tuple[Project, List[Path], "black.Mode"]


def get_commit(repo: Path) -> Tuple[CommitSHA, CommitMsg]:
    assert GIT_BIN
    proc = run_cmd([GIT_BIN, "log", "--format=%H:%s", "-n1"], cwd=repo)
    output = proc.stdout.strip()
    sha, _, msg = output.partition(":")
    return sha, msg


def setup_projects(
    projects: List[Project],
    workdir: Path,
    force_style: Optional[Literal["stable", "preview"]],
    extra_args: Sequence[str],
    progress: rich.progress.Progress,
    task: rich.progress.TaskID,
    verbose: bool,
) -> List[PreparedProject]:
    console = progress.console
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
            if verbose:
                console.log(f"{bold}Using pre-existing clone of {proj.name} - {proj.url}")
        else:
            clone_repo(proj.url, to=target, sha=proj.commit)
            console.log(f"{bold}Cloned {proj.name} - {proj.url}")

        commit_sha, commit_msg = get_commit(target)
        if verbose:
            console.log(f"[dim]  commit -> {commit_msg}", highlight=False)
            console.log(f"[dim]  commit -> {commit_sha}")
        proj = replace(proj, commit=commit_sha)
        files, mode = get_files_and_mode(proj, target, force_style, extra_args)
        ready.append((proj, files, mode))
        progress.advance(task)
        progress.refresh()

    return ready


@contextmanager
def suppress_output() -> Iterator:
    from unittest.mock import patch

    with open(os.devnull, "w", encoding="utf-8") as blackhole:
        with redirect_stdout(blackhole), redirect_stderr(blackhole):
            # It shouldn't be necessary to also patch click.echo but I've
            # received reports of the stream redirections not working :shrug:
            with patch("click.echo", new=lambda *args, **kwargs: None):
                yield


@lru_cache(maxsize=1)
def _find_black_reformat_many() -> str:
    # NOTE: this only exists because mypyc puts imports in the global module scope even if
    # the import is within a function, so after the first run of Black, `black.reformat_many`
    # will exist even if it doesn't in the source ... >.<
    import black

    if hasattr(black, "reformat_many"):
        return "black.reformat_many"
    else:
        import black.concurrency

        assert hasattr(black.concurrency, "reformat_many")
        return "black.concurrency.reformat_many"


def get_files_and_mode(
    project: Project,
    path: Path,
    force_style: Optional[Literal["stable", "preview"]] = None,
    extra_args: Sequence[str] = (),
) -> Tuple[List[Path], "black.Mode"]:
    # HACK: I know this is hacky but the benefit is I don't need to copy and
    # paste a bunch of black's argument parsing, file discovery, and
    # configuration code. I also get to keep the pretty output since I can
    # directly invoke black.format_file_contents :D
    #
    # This pulls in a ton of stuff including the heavy asyncio. Let's avoid the
    # import cost unless till the last possible moment.
    from unittest.mock import patch

    import black

    files: List[Path] = []
    mode = None

    def many_shim(sources: List[Path], *args: Any, **kwargs: Any) -> None:
        nonlocal files, mode
        files.extend(sources)
        mode = kwargs["mode"]

    def single_shim(src: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal files, mode
        files = [src]
        mode = kwargs["mode"]

    many_target = _find_black_reformat_many()
    # I really should implement better context manager handling in black ...
    with suppress_output(), patch(many_target, new=many_shim), patch(
        "black.reformat_one", new=single_shim
    ):
        cmd = [
            str(path),
            *project.custom_arguments,
            *extra_args,
            "--check",
            # Override the required black version, since we need to run diff-shades
            # with any future black versions.
            "--required-version=",
        ]
        black.main(cmd, standalone_mode=False)

    assert files and isinstance(mode, black.FileMode), (files, mode)
    if force_style:
        with suppress_output():
            mode = replace(mode, preview=(force_style == "preview"))

    return sorted(p for p in files if p.suffix in (".py", ".pyi")), mode


def check_file(path: Path, *, mode: Optional["black.Mode"] = None) -> FileResult:
    """
    Format file at `path` and return the result.
    """
    import black

    mode = mode or black.FileMode()
    if path.suffix == ".pyi":
        with suppress_output():
            mode = replace(mode, is_pyi=True)

    src = path.read_text("utf8")
    try:
        with suppress_output():
            dst = black.format_file_contents(src, fast=False, mode=mode)
    except black.NothingChanged:
        return NothingChangedResult(src)

    except Exception as err:
        msg = str(err)
        tb = "".join(traceback.format_exception(None, err, err.__traceback__)).strip()
        # If this error contains a log file, let's record it!
        if "helpful: " in msg:
            _, file_path = msg.split("helpful: ")
            log_path = Path(file_path)
            if log_path.name.startswith("blk") and log_path.suffix == ".log":
                log = log_path.read_text("utf-8")
                # The log files have randomized names and we need to get rid of this so
                # identical runs record the same error messages.
                msg = msg.replace(str(log_path), "(use diff-shades show or show-failed)")
        else:
            log = None

        return FailedResult(src, err.__class__.__name__, msg, log=log, traceback=tb)

    return ReformattedResult(src, dst)


def check_file_shim(arguments: Tuple[Path, Path, "black.Mode"]) -> Tuple[str, FileResult]:
    # Unfortunately there's nothing like imap + starmap in multiprocessing.
    file, project_path, mode = arguments
    result = check_file(file, mode=mode)
    normalized_path = file.relative_to(project_path).as_posix()
    return (normalized_path, result)


def analyze_projects(
    projects: List[PreparedProject],
    work_dir: Path,
    progress: rich.progress.Progress,
    task: rich.progress.TaskID,
    verbose: bool,
) -> Dict[str, ProjectResults]:
    # Slow import, let's not pay all of the time (this makes show and friends faster).
    import multiprocessing

    # For consistency w/ Windows so things don't unintentionally work only on Linux.
    mp = multiprocessing.get_context("spawn")

    file_count = sum(len(files) for _, files, _ in projects)
    progress.update(task, total=file_count)
    bold = "[bold]" if verbose else ""

    def check_project_files(
        files: List[Path], project_path: Path, mode: "black.Mode"
    ) -> ProjectResults:
        file_results = {}
        data_packets = [(file_path, project_path, mode) for file_path in files]
        for filepath, result in pool.imap(check_file_shim, data_packets):
            if verbose:
                console.log(f"  {filepath}: [{result.type}]{result.type}")
            file_results[filepath] = result
            progress.advance(task)
            progress.advance(project_task)
        return ProjectResults(file_results)

    # Sadly the Pool context manager API doesn't play nice with pytest-cov so
    # we have to use this uglier alternative ...
    # https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html#if-you-use-multiprocessing-pool
    pool = mp.Pool(NUM_PROCESSES)
    console.log(
        f"[bold]Running analysis with {NUM_PROCESSES} processes "
        f"(os.cpu_count() = {os.cpu_count()})"
    )
    try:
        results = {}
        for project, files, mode in projects:
            project_task = progress.add_task(f"[bold]╰─> {project.name}", total=len(files))
            if verbose:
                console.log(f"[bold]Checking {project.name} ({len(files)} files)")
            results[project.name] = check_project_files(files, work_dir / project.name, mode)
            overall_result = results[project.name].overall_result
            console.log(f"{bold}{project.name} finished as [{overall_result}]{overall_result}")
            progress.remove_task(project_task)
    finally:
        pool.close()
        pool.join()

    return results
