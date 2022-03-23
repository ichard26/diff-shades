# ============================================
# > Analysis data representation & processing
# ==========================================

import hashlib
import json
import pickle
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
    overload,
)
from zipfile import ZIP_DEFLATED, ZipFile

if sys.version_info >= (3, 8):
    from typing import Final, Literal
else:
    from typing_extensions import Final, Literal

import platformdirs
import rich
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import diff_shades
from diff_shades.config import Project
from diff_shades.utils import DSError, calculate_line_changes, readable_int, unified_diff

CACHE_DIR: Final = Path(platformdirs.user_cache_dir("diff-shades"))
CACHE_MAX_ENTRIES: Final = 5
CACHE_LAST_ACCESS_CUTOFF: Final = 60 * 60 * 24 * 5
JSON = Any
ResultTypes = Literal["nothing-changed", "reformatted", "failed"]


def _convert_line_count(instance: "FileResult") -> None:
    if instance.line_count == -1:
        lines = max(1, instance.src.count("\n"))
        object.__setattr__(instance, "line_count", lines)


@dataclass(frozen=True)
class NothingChangedResult:
    type: Literal["nothing-changed"] = field(default="nothing-changed", init=False)
    src: str
    line_count: int = -1

    @property
    def line_changes(self) -> Tuple[int, int]:
        return (0, 0)

    __post_init__ = _convert_line_count


@dataclass(frozen=True)
class ReformattedResult:
    type: Literal["reformatted"] = field(default="reformatted", init=False)
    src: str
    dst: str
    line_count: int = -1
    line_changes: Tuple[int, int] = (-1, -1)

    def __post_init__(self) -> None:
        _convert_line_count(self)
        if self.line_changes == (-1, -1):
            changes = calculate_line_changes(self.diff("throw-away-name"))
            object.__setattr__(self, "line_changes", changes)

    def diff(self, filepath: str) -> str:
        return unified_diff(self.src, self.dst, f"a/{filepath}", f"b/{filepath}")


@dataclass(frozen=True)
class FailedResult:
    type: Literal["failed"] = field(default="failed", init=False)
    src: str
    error: str
    message: str
    log: Optional[str] = None
    line_count: int = -1

    @property
    def line_changes(self) -> Tuple[int, int]:
        return (0, 0)

    __post_init__ = _convert_line_count


FileResult = Union[FailedResult, ReformattedResult, NothingChangedResult]
NamedResults = Mapping[str, FileResult]
ProjectName = str


class ProjectResults(Dict[str, FileResult]):
    @property
    def line_count(self) -> int:
        return sum(r.line_count for r in self.values())

    @property
    def line_changes(self) -> Tuple[int, int]:
        additions = sum(p.line_changes[0] for p in self.values())
        deletions = sum(p.line_changes[1] for p in self.values())
        return (additions, deletions)

    @property
    def overall_result(self) -> ResultTypes:
        return get_overall_result(self)


@dataclass
class Analysis:
    projects: Dict[ProjectName, Project]
    results: Dict[ProjectName, ProjectResults]
    metadata: Dict[str, JSON] = field(default_factory=dict)

    def __iter__(self) -> Iterator[ProjectResults]:
        return iter(self.results.values())

    def files(self) -> Dict[str, FileResult]:
        files: Dict[str, FileResult] = {}
        for proj, proj_results in self.results.items():
            for file, file_result in proj_results.items():
                files[f"{proj}:{file}"] = file_result

        return files

    @property
    def line_count(self) -> int:
        return sum(p.line_count for p in self)

    @property
    def line_changes(self) -> Tuple[int, int]:
        additions = sum(p.line_changes[0] for p in self)
        deletions = sum(p.line_changes[1] for p in self)
        return (additions, deletions)


def diff_two_results(
    r1: FileResult, r2: FileResult, file: str, diff_failure: bool = False
) -> str:
    """Compare two results for the same file producing a diff.

    Setting `diff_failure` to True allows failing results to be compared
    in a diff-like format. The diff can be empty if there's no difference.
    """
    if "failed" in (r1.type, r2.type):
        if not diff_failure:
            raise ValueError("Cannot diff failing file results.")

        first_dst = f"[{r1.error}: {r1.message}]\n" if r1.type == "failed" else "[no crash]\n"
        second_dst = f"[{r2.error}: {r2.message}]\n" if r2.type == "failed" else "[no crash]\n"
    else:
        first_dst = r1.dst if r1.type == "reformatted" else r1.src
        second_dst = r2.dst if r2.type == "reformatted" else r2.src

    return unified_diff(first_dst, second_dst, f"a/{file}", f"b/{file}")


# fmt: off
@overload
def filter_results(
    results: NamedResults, type: Literal["reformatted"]
) -> Mapping[str, ReformattedResult]:
    ...

@overload
def filter_results(
    results: NamedResults, type: Literal["failed"]
) -> Mapping[str, FailedResult]:
    ...

@overload
def filter_results(
    results: NamedResults, type: Literal["nothing-changed"]
) -> Mapping[str, NothingChangedResult]:
    ...

@overload
def filter_results(results: NamedResults, type: str) -> NamedResults:
    ...

def filter_results(results: NamedResults, type: str) -> NamedResults:
    return {file: result for file, result in results.items() if result.type == type}
# fmt: on


def get_overall_result(results: Union[NamedResults, Sequence[FileResult]]) -> ResultTypes:
    """Summarize a group of file results as one result type.

    The group is considered X result under the following conditions:

    failed: there's at least one failing result
    reformatted: there's at least one reformatted result
    nothing-changed: ALL results are nothing-changed

    If the group meets the requirement for failed and reformatted, failed
    wins out.
    """
    results = list(results.values()) if isinstance(results, Mapping) else results
    result_types = {r.type for r in results}
    if "failed" in result_types:
        return "failed"

    if "reformatted" in result_types:
        return "reformatted"

    assert result_types == {"nothing-changed"}
    return "nothing-changed"


def clear_cache(*, ensure_room: bool = False) -> None:
    """
    Clears out old analysis caches.
    """
    entries = [(entry, entry.stat().st_atime) for entry in CACHE_DIR.iterdir()]
    by_oldest = sorted(entries, key=lambda x: x[1])
    while len(by_oldest) > CACHE_MAX_ENTRIES - int(ensure_room):
        by_oldest[0][0].unlink()
        by_oldest.pop(0)
    for entry, atime in by_oldest:
        if time.time() - atime > CACHE_LAST_ACCESS_CUTOFF:
            entry.unlink()


def calculate_cache_key(filepath: Path) -> str:
    filepath = filepath.resolve()
    stat = filepath.stat()
    cache_key = f"{filepath};{stat.st_mtime};{stat.st_size};{diff_shades.__version__}"
    hasher = hashlib.blake2b(cache_key.encode("utf-8"), digest_size=15)
    return hasher.hexdigest()


def load_analysis_contents(data: JSON) -> Analysis:
    def _parse_file_result(r: JSON) -> FileResult:
        rtype: ResultTypes = r.pop("type")
        cls: Type[FileResult]
        if rtype == "reformatted":
            cls = ReformattedResult
        elif rtype == "nothing-changed":
            cls = NothingChangedResult
        elif rtype == "failed":
            cls = FailedResult
        if "line_changes" in r:
            r["line_changes"] = tuple(r["line_changes"])

        return cls(**r)

    projects = {name: Project(**config) for name, config in data["projects"].items()}
    metadata = {k.replace("_", "-"): v for k, v in data["metadata"].items()}
    data_format = metadata.get("data-format", None)
    if not (1 <= data_format < 2):
        raise ValueError(f"unsupported analysis format: {data_format}")

    results = {}
    for project_name, project_results in data["results"].items():
        for filepath, result in project_results.items():
            project_results[filepath] = _parse_file_result(result)
        results[project_name] = ProjectResults(project_results)

    return Analysis(projects=projects, results=results, metadata=metadata)


def load_analysis(filepath: Path) -> Tuple[Analysis, bool]:
    """Load an analysis from `filepath` potentially using a cached copy.

    Upon loading an analysis a cached copy will be written to disk for
    future use. If a cached analysis fails to load it'll be deleted and
    the original will loaded instead.

    If the filepath ends with the .zip extension, it'll be auto-extracted
    with the contained analysis cached (erroring out if there's more than
    one member).
    """
    cache_key = calculate_cache_key(filepath)
    cache_path = Path(CACHE_DIR, f"{cache_key}.pickle")
    if cache_path.exists():
        try:
            analysis = pickle.loads(cache_path.read_bytes())
        except Exception:
            cache_path.unlink()
        else:
            return analysis, True

    if filepath.suffix == ".zip":
        with ZipFile(filepath) as zfile:
            entries = zfile.infolist()
            if len(entries) > 1:
                raise DSError(
                    f"'{filepath}' contains more than one member.",
                    tip="Please unzip and pass the right file manually.",
                )

            with zfile.open(entries[0]) as f:
                blob = f.read().decode("utf-8")
    else:
        blob = filepath.read_text("utf-8")
    analysis = load_analysis_contents(json.loads(blob))
    clear_cache(ensure_room=True)
    cache_path.write_bytes(pickle.dumps(analysis, protocol=4))
    return analysis, False


def save_analysis(filepath: Path, analysis: Analysis) -> None:
    raw = asdict(analysis)
    # Escaping non-ASCII characters in the JSON blob is very important to keep
    # memory usage and load times managable. CPython (not sure about other
    # implementations) guarantees that string index operations will be roughly
    # constant time which flies right in the face of the efficient UTF-8 format.
    # Hence why str instances transparently switch between Latin-1 and other
    # constant-size formats. In the worst case UCS-4 is used exploding
    # memory usage (and load times as memory is not infinitely fast). I've seen
    # peaks of 1GB max RSS with 100MB analyses which is just not OK.
    # See also: https://stackoverflow.com/a/58080893
    blob = json.dumps(raw, indent=2, ensure_ascii=True) + "\n"

    if filepath.suffix == ".zip":
        with ZipFile(filepath, mode="w", compression=ZIP_DEFLATED) as zfile:
            zfile.writestr("analysis.json", blob)
    else:
        filepath.write_text(blob, encoding="utf-8")


# ========================= #
# Result summarization & stats
# ======================= #


def make_analysis_summary(analysis: Analysis) -> Panel:
    main_table = Table.grid()
    stats_table = Table.grid()
    stats_table_two = Table.grid(expand=True)

    file_table = Table(title="File breakdown", show_edge=False, box=rich.box.SIMPLE)
    file_table.add_column("Result")
    file_table.add_column("# of files")
    for rtype in ("nothing-changed", "reformatted", "failed"):
        count = len(filter_results(analysis.files(), rtype))
        file_table.add_row(rtype, str(count), style=rtype)

    project_table = Table(title="Project breakdown", show_edge=False, box=rich.box.SIMPLE)
    project_table.add_column("Result")
    project_table.add_column("# of projects")
    for rtype in ("nothing-changed", "reformatted", "failed"):
        count = sum(proj.overall_result == rtype for proj in analysis)
        project_table.add_row(rtype, str(count), style=rtype)
    stats_table.add_row(file_table, "   ", project_table)

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
    stats_table_two.add_row(
        textwrap.dedent(left_stats), Text.from_markup(right_stats, justify="right")
    )

    main_table.add_row(stats_table)

    forced_style = analysis.metadata.get("forced-style")
    if forced_style:
        main_table.add_row(f"\n[bold]Forced code style:[/] {forced_style}")

    main_table.add_row(stats_table_two)
    extra_args = analysis.metadata.get("black-extra-args")
    if extra_args:
        pretty_args = Text(" ".join(extra_args), style="itatic", justify="center")
        main_table.add_row(Panel(pretty_args, title="\[custom arguments]", border_style="dim"))

    black_version = analysis.metadata["black-version"]
    created_at = datetime.fromisoformat(analysis.metadata["created-at"])
    created_at_string = created_at.strftime("%b %d %Y %X") + " UTC"
    subtitle = f"[dim]black {black_version} - {created_at_string}"
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
                        diff = diff_two_results(r1, r2, "throwaway")
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
