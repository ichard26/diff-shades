# ============================================
# > Analysis data representation & processing
# ==========================================

import difflib
import hashlib
import json
import pickle
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
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
from zipfile import ZipFile

if sys.version_info >= (3, 8):
    from typing import Final, Literal
else:
    from typing_extensions import Final, Literal

import platformdirs

import diff_shades
from diff_shades.config import Project

CACHE_DIR: Final = Path(platformdirs.user_cache_dir("diff-shades"))
CACHE_MAX_ENTRIES: Final = 5
CACHE_LAST_ACCESS_CUTOFF: Final = 60 * 60 * 24 * 5
JSON = Any
ResultTypes = Literal["nothing-changed", "reformatted", "failed"]


def unified_diff(a: str, b: str, a_name: str, b_name: str) -> str:
    """Return a unified diff string between strings `a` and `b`."""
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff_lines = []
    for line in difflib.unified_diff(a_lines, b_lines, fromfile=a_name, tofile=b_name, n=5):
        # Work around https://bugs.python.org/issue2142. See also:
        # https://www.gnu.org/software/diffutils/manual/html_node/Incomplete-Lines.html
        if line[-1] == "\n":
            diff_lines.append(line)
        else:
            diff_lines.append(line + "\n")
            diff_lines.append("\\ No newline at end of file\n")
    return "".join(diff_lines)


def calculate_line_changes(diff: str) -> Tuple[int, int]:
    """Return a two-tuple (additions, deletions) of a diff."""
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if line[0] == "+" and not line.startswith("+++"):
            additions += 1
        elif line[0] == "-" and not line.startswith("---"):
            deletions += 1

    return additions, deletions


class _FileResultBase:
    def __init__(self) -> None:
        self.src: str
        self.line_count: int

    def __post_init__(self) -> None:
        if self.line_count == -1:
            lines = max(1, self.src.count("\n"))
            object.__setattr__(self, "line_count", lines)


@dataclass(frozen=True)
class NothingChangedResult(_FileResultBase):
    type: Literal["nothing-changed"] = field(default="nothing-changed", init=False)
    src: str
    line_count: int = -1

    @property
    def line_changes(self) -> Tuple[int, int]:
        return (0, 0)


@dataclass(frozen=True)
class ReformattedResult(_FileResultBase):
    type: Literal["reformatted"] = field(default="reformatted", init=False)
    src: str
    dst: str
    line_count: int = -1
    line_changes: Tuple[int, int] = (-1, -1)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.line_changes == (-1, -1):
            changes = calculate_line_changes(self.diff("throw-away-name"))
            object.__setattr__(self, "line_changes", changes)

    @lru_cache(maxsize=None)
    def diff(self, filepath: str) -> str:
        return unified_diff(self.src, self.dst, f"a/{filepath}", f"b/{filepath}")


@dataclass(frozen=True)
class FailedResult(_FileResultBase):
    type: Literal["failed"] = field(default="failed", init=False)
    src: str
    error: str
    message: str
    log: Optional[str] = None
    line_count: int = -1

    @property
    def line_changes(self) -> Tuple[int, int]:
        return (0, 0)


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
    metadata: Dict[str, Any] = field(default_factory=dict)

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

    return unified_diff(first_dst, second_dst, f"a/{file}", f"b/{file}").rstrip()


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
    result_classes: Dict[str, Type[FileResult]] = {
        "reformatted": ReformattedResult,
        "nothing-changed": NothingChangedResult,
        "failed": FailedResult,
    }

    def _parse_file_result(r: JSON) -> FileResult:
        cls = result_classes[r.pop("type")]
        if "line_changes" in r:
            r["line_changes"] = tuple(r["line_changes"])
        return cls(**r)

    projects = {name: Project(**config) for name, config in data["projects"].items()}
    metadata = {k.replace("_", "-"): v for k, v in data["metadata"].items()}
    data_format = metadata.get("data-format", None)
    if 1 > data_format > 2:
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

    if filepath.name.endswith(".zip"):
        with ZipFile(filepath) as zfile:
            entries = zfile.infolist()
            if len(entries) > 1:
                # TODO: improve the error message handling for the whole tool
                raise ValueError(
                    f"{filepath} contains more than one member."
                    " Please unzip and pass the right file manually."
                )

            with zfile.open(entries[0]) as f:
                blob = f.read().decode("utf-8")
    else:
        blob = filepath.read_text("utf-8")
    analysis = load_analysis_contents(json.loads(blob))
    clear_cache(ensure_room=True)
    cache_path.write_bytes(pickle.dumps(analysis, protocol=4))
    return analysis, False
