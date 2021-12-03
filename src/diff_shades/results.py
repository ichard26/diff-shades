# ============================================
# > Analysis data representation & processing
# ==========================================

import hashlib
import json
import pickle
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Sequence, Tuple, Union, overload
from zipfile import ZipFile

if sys.version_info >= (3, 8):
    from typing import Final, Literal
else:
    from typing_extensions import Final, Literal

import platformdirs

import diff_shades
from diff_shades.config import Project

CACHE_DIR: Final = Path(platformdirs.user_cache_dir("diff-shades"))
CACHE_MAX_ENTRIES: Final = 3
CACHE_LAST_ACCESS_CUTOFF: Final = 60 * 60 * 24 * 5
JSON = Any
ResultTypes = Literal["nothing-changed", "reformatted", "failed"]


class _FileResultBase:
    pass


@dataclass(frozen=True, eq=True)
class NothingChangedResult(_FileResultBase):
    type: Literal["nothing-changed"] = field(default="nothing-changed", init=False)
    src: str


@dataclass(frozen=True)
class ReformattedResult(_FileResultBase):
    type: Literal["reformatted"] = field(default="reformatted", init=False)
    src: str
    dst: str


@dataclass(frozen=True)
class FailedResult(_FileResultBase):
    type: Literal["failed"] = field(default="failed", init=False)
    src: str
    error: str
    message: str


FileResult = Union[FailedResult, ReformattedResult, NothingChangedResult]
ProjectName = str
ProjectResults = Dict[str, FileResult]


@dataclass
class Analysis:
    projects: Dict[ProjectName, Project]
    results: Dict[ProjectName, ProjectResults]
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _parse_file_result(r: JSON) -> FileResult:
        if r["type"] == "reformatted":
            return ReformattedResult(r["src"], dst=r["dst"])
        if r["type"] == "nothing-changed":
            return NothingChangedResult(r["src"])
        if r["type"] == "failed":
            return FailedResult(r["src"], error=r["error"], message=r["message"])

        raise ValueError(f"unsupported file result type: {r['type']}")

    @classmethod
    def load(cls, data: JSON) -> "Analysis":
        projects = {name: Project(**config) for name, config in data["projects"].items()}
        results = {}
        for project_name, project_results in data["results"].items():
            for filepath, result in project_results.items():
                project_results[filepath] = cls._parse_file_result(result)
            results[project_name] = project_results

        metadata = {k.replace("_", "-"): v for k, v in data["metadata"].items()}
        return cls(projects=projects, results=results, metadata=metadata)

    def __iter__(self) -> Iterator[ProjectResults]:
        return iter(self.results.values())

    @property
    def files(self) -> Dict[str, FileResult]:
        files: Dict[str, FileResult] = {}
        for proj, proj_results in self.results.items():
            for file, file_result in proj_results.items():
                files[f"{proj}:{file}"] = file_result
        return files


@overload
def filter_results(
    file_results: Mapping[str, FileResult], type: Literal["reformatted"]
) -> Mapping[str, ReformattedResult]:
    ...


@overload
def filter_results(
    file_results: Mapping[str, FileResult], type: Literal["failed"]
) -> Mapping[str, FailedResult]:
    ...


@overload
def filter_results(
    file_results: Mapping[str, FileResult], type: Literal["nothing-changed"]
) -> Mapping[str, NothingChangedResult]:
    ...


@overload
def filter_results(
    file_results: Mapping[str, FileResult], type: str
) -> Mapping[str, FileResult]:
    ...


def filter_results(
    file_results: Mapping[str, FileResult], type: str
) -> Mapping[str, FileResult]:
    return {file: result for file, result in file_results.items() if result.type == type}


def get_overall_result(
    results: Union[ProjectResults, Sequence[FileResult]]
) -> ResultTypes:
    results = list(results.values()) if isinstance(results, dict) else results
    results_by_type = [r.type for r in results]
    if "failed" in results_by_type:
        return "failed"

    if "reformatted" in results_by_type:
        return "reformatted"

    assert [r.type == "nothing-changed" for r in results]
    return "nothing-changed"


def _clear_cache(*, ensure_room: bool = False) -> None:
    """
    Clears out old and unused analysis caches.

    By default all cached analyses may be evicted. To also make sure there's a spot
    available for one new entry please set :only_ensure_room: to True.
    """
    entries = [(entry, entry.stat().st_atime) for entry in CACHE_DIR.iterdir()]
    by_oldest = sorted(entries, key=lambda x: x[1])
    while len(by_oldest) > CACHE_MAX_ENTRIES - int(ensure_room):
        by_oldest[0][0].unlink()
        by_oldest.pop(0)
    for entry, atime in by_oldest:
        if time.time() - atime > CACHE_LAST_ACCESS_CUTOFF:
            print(f"too old: {entry}")
            entry.unlink()


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
    analysis = Analysis.load(json.loads(blob))
    _clear_cache(ensure_room=True)
    cache_path.write_bytes(pickle.dumps(analysis, protocol=4))
    return analysis, False
