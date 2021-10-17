# ================================
# > Formatting results collection
# =============================

import dataclasses
import multiprocessing
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from unittest.mock import patch

if sys.version_info >= (3, 8):
    from typing import Final
else:
    from typing_extensions import Final

if TYPE_CHECKING:
    import black

import rich
import rich.progress

from diff_shades.config import Project
from diff_shades.output import suppress_output

GIT_BIN: Final = shutil.which("git")
FILE_RESULTS_COLOURS: Final = {
    "reformatted": "cyan",
    "nothing-changed": "magenta",
    "failed": "red"
}
run_cmd: Final = partial(
    subprocess.run,
    check=True,
    encoding="utf8",
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT
)
console: Final = rich.get_console()


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
) -> List[Tuple[Project, Path]]:
    ready = []
    for proj in projects:
        progress.update(task, current=proj.name)
        target = Path(workdir, proj.name)
        can_reuse = False
        if target.exists():
            if proj.commit is None:
                can_reuse = True
            else:
                sha, _ = get_commit(target)
                can_reuse = (proj.commit == sha)

        if can_reuse:
            progress.console.log(f"[bold]Using pre-existing clone of {proj.name} \[{proj.url}]")
        else:
            clone_repo(proj.url, to=target, sha=proj.commit)
            progress.console.log(f"[bold]Cloned {proj.name} \[{proj.url}]")

        commit_sha, commit_msg = get_commit(target)
        if verbose:
            progress.console.log(f"[dim]  commit -> {commit_msg}", highlight=False)
            progress.console.log(f"[dim]  commit -> {commit_sha}")
        proj = replace(proj, commit=commit_sha)
        ready.append((proj, target))
        progress.advance(task)

    return ready


# HACK: I know this is hacky but the benefit is I don't need to copy and
# paste a bunch of black's argument parsing, file discovery, and
# configuration code. I also get to keep the pretty output since I can
# directly invoke black.format_file_contents :D


def get_project_files_and_mode(
    project: Project, path: Path
) -> Tuple[List[Path], "black.FileMode"]:
    import black

    files: List[Path] = []
    mode = None

    def shim(sources: List[Path], *args: Any, **kwargs: Any) -> None:
        nonlocal files, mode
        files.extend(sources)
        mode = kwargs["mode"]

    with suppress_output(), patch("black.reformat_many", new=shim):
        black.main([str(path), *project.custom_arguments, "--check"], standalone_mode=False)

    assert files and isinstance(mode, black.FileMode)
    return sorted(p for p in files if p.suffix in (".py", ".pyi")), mode


# TODO: make this a little more reasonable + helpful
FileResults = Dict[str, str]  # AKA JSON


def check_file(path: Path, *, mode: Optional["black.FileMode"] = None) -> FileResults:
    import black

    # TODO: record log files if available
    # TODO: allow more control w/ black.Mode so we could use diff-shades to compare
    # for example, no-ESP vs ESP.

    mode = mode or black.FileMode()
    if path.suffix == ".pyi":
        mode = replace(mode, is_pyi=True)

    src = path.read_text("utf8")
    try:
        dst = black.format_file_contents(src, fast=False, mode=mode)
    except black.NothingChanged:
        return {"type": "nothing-changed", "src": src}

    except Exception as err:
        return {"type": "failed", "src": src, "error": err.__class__.__name__, "message": str(err)}

    return {"type": "reformatted", "src": src, "dst": dst}


def check_file_shim(
    # Unfortunately there's nothing like imap + starmap in multiprocessing.
    arguments: Tuple[Path, Path, "black.FileMode"]
) -> Tuple[str, FileResults]:
    file, project_path, mode = arguments
    result = check_file(file, mode=mode)
    normalized_path = file.relative_to(project_path).as_posix()
    return (normalized_path, result)


def analyze_projects(
    projects: List[Tuple[Project, Path]],
    progress: rich.progress.Progress,
    task: rich.progress.TaskID,
    verbose: bool,
) -> Dict[str, Any]:
    files_and_modes = [get_project_files_and_mode(proj, path) for proj, path in projects]
    file_count = sum(len(files) for files, _ in files_and_modes)
    progress.update(task, total=file_count)

    def check_project_files(
        files: List[Path], project_path: Path, *, mode: "black.FileMode",
    ) -> Dict[str, FileResults]:
        outputs = []
        data_packets = [(file_path, project_path, mode) for file_path in files]
        for data in pool.imap(check_file_shim, data_packets):
            filepath, result = data
            result_colour = FILE_RESULTS_COLOURS[result["type"]]
            if verbose:
                console.log(f"  {filepath}: [{result_colour}]{result['type']}[/]")
            outputs.append(data)
            progress.advance(task)
            progress.advance(project_task)
        return {path: data for (path, data) in outputs}

    with multiprocessing.Pool() as pool:
        results: Dict[str, Any] = {}
        for (project, path), (files, mode) in zip(projects, files_and_modes):
            project_task = progress.add_task(f"[bold] on {project.name}", total=len(files))
            if verbose:
                console.log(f"[bold]Checking {project.name}[/] ({len(files)} files) ...")
            t0 = time.perf_counter()
            results[project.name] = {}
            results[project.name]["metadata"] = dataclasses.asdict(project)
            results[project.name]["files"] = check_project_files(files, path, mode=mode)
            elapsed = time.perf_counter() - t0
            console.log(f"[bold]{project.name} finished[/] (took {elapsed:.3f} seconds)")
            progress.remove_task(project_task)

    return results
