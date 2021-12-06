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
from typing import Any, Dict, Iterator, Mapping, Sequence, Tuple, Type, Union, overload
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
    for line in difflib.unified_diff(
        a_lines, b_lines, fromfile=a_name, tofile=b_name, n=5
    ):
        # Work around https://bugs.python.org/issue2142. See also:
        # https://www.gnu.org/software/diffutils/manual/html_node/Incomplete-Lines.html
        if line[-1] == "\n":
            diff_lines.append(line)
        else:
            diff_lines.append(line + "\n")
            diff_lines.append("\\ No newline at end of file\n")
    return "".join(diff_lines)


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
            additions = 0
            deletions = 0
            for line in self.diff(":daylily:"):
                if line[0] == "+" and not line.startswith("+++"):
                    additions += 1
                elif line[0] == "-" and not line.startswith("---"):
                    deletions += 1
            object.__setattr__(self, "line_changes", (additions, deletions))

    @lru_cache(maxsize=None)
    def diff(self, filepath: str) -> str:
        return unified_diff(self.src, self.dst, f"a/{filepath}", f"b/{filepath}")


@dataclass(frozen=True)
class FailedResult(_FileResultBase):
    type: Literal["failed"] = field(default="failed", init=False)
    src: str
    error: str
    message: str
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
    results = list(results.values()) if isinstance(results, Mapping) else results
    results_by_type = [r.type for r in results]
    if "failed" in results_by_type:
        return "failed"

    if "reformatted" in results_by_type:
        return "reformatted"

    assert [r.type == "nothing-changed" for r in results]
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


def load_analysis_contents(data: JSON) -> Analysis:
    result_classes: Dict[str, Type[FileResult]] = {
        "reformatted": ReformattedResult,
        "nothing-changed": NothingChangedResult,
        "failed": FailedResult,
    }

    def _parse_file_result(r: JSON) -> FileResult:
        cls = result_classes[r["type"]]
        del r["type"]
        return cls(**r)

    projects = {name: Project(**config) for name, config in data["projects"].items()}
    metadata = {k.replace("_", "-"): v for k, v in data["metadata"].items()}
    data_format = metadata.get("data-format", None)
    if data_format != 1:
        raise ValueError(f"unsupported analysis format: {data_format}")

    results = {}
    for project_name, project_results in data["results"].items():
        for filepath, result in project_results.items():
            project_results[filepath] = _parse_file_result(result)
        results[project_name] = ProjectResults(project_results)

    return Analysis(projects=projects, results=results, metadata=metadata)


def load_analysis(filepath: Path) -> Tuple[Analysis, bool]:
    filepath = filepath.resolve()
    stat = filepath.stat()
    cache_key = f"{filepath};{stat.st_mtime};{stat.st_size};{diff_shades.__version__}"
    hasher = hashlib.blake2b(cache_key.encode("utf-8"), digest_size=15)
    short_key = hasher.hexdigest()

    cache_path = Path(CACHE_DIR, f"{short_key}.pickle")
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
