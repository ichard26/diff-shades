# TODO: assert output once diff-shades has a better std/out/err story
# TODO: test compare with analyses that don't share the same set of projects
# TODO: test the full matrix of supported data formats

import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple, Union
from unittest.mock import patch
from zipfile import ZipFile

if sys.version_info >= (3, 8):
    from typing import Final
else:
    from typing_extensions import Final

import black
import click
import click.testing
import pytest

import diff_shades.analysis
import diff_shades.cli
import diff_shades.results
from diff_shades.analysis import check_file
from diff_shades.config import Project
from diff_shades.results import (
    Analysis,
    FailedResult,
    FileResult,
    NothingChangedResult,
    ProjectResults,
    ReformattedResult,
    filter_results,
    load_analysis,
    save_analysis,
)
from diff_shades.utils import DSError

THIS_DIR: Final = Path(__file__).parent
DATA_DIR: Final = THIS_DIR / "data"
GIT_BIN: Final = shutil.which("git")
DIFF_SHADES_GIT_URL: Final = "https://github.com/ichard26/diff-shades.git"
WINDOWS: Final = sys.platform.startswith("win")
run_cmd: Final = partial(
    subprocess.run,
    check=True,
    encoding="utf8",
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
SupportedArgs = Sequence[Union[str, int, float, Path]]

assert GIT_BIN, "running tests requires a discoverable Git binary"


def read_data(name: str) -> str:
    path = Path(DATA_DIR, name)
    return path.read_text(encoding="utf-8")


class CLIResult:
    def __init__(self, result: click.testing.Result, cmd: Sequence[str]) -> None:
        self.result = result
        self.cmd = cmd
        self.return_code = result.exit_code
        self.stdout = result.stdout if result.stdout_bytes is not None else None
        self.stderr = result.stderr if result.stderr_bytes is not None else None

    def assert_return_code(self, code: int) -> None:
        if code != self.return_code:
            msg = f"expected {code} return code, instead got {self.return_code}"
            msg += f"\n command: {' '.join(self.cmd)}"
            if self.stdout:
                name = "output" if self.stderr is None else "stdout"
                msg += f"\n {name}:\n" + textwrap.indent(self.stdout, " " * 3)
            if self.stderr:
                msg += "\n stderr:\n" + textwrap.indent(self.stderr, " " * 3)
            raise RuntimeError(msg)


class CLIRunner:
    def run(self, args: SupportedArgs, **kwargs: Any) -> CLIResult:
        sargs = [str(a) for a in args]
        runner = click.testing.CliRunner(charset="utf-8", **kwargs)
        r = runner.invoke(diff_shades.cli.main, sargs, catch_exceptions=False)
        return CLIResult(r, cmd=sargs)

    def check(self, args: SupportedArgs, **kwargs: Any) -> CLIResult:
        result = self.run(args, **kwargs)
        result.assert_return_code(0)
        return result


@pytest.fixture
def runner() -> CLIRunner:
    return CLIRunner()


def get_basic_analysis() -> Tuple[Analysis, Path]:
    projects = {"test": Project("test", "https://example.com")}
    results = {"test": ProjectResults({"a.py": NothingChangedResult("content\n")})}
    metadata = {
        "black-version": "21.12b0",
        "black-extra-args": [],
        "created-at": "2022-01-03T20:40:08.703857+00:00",
        "data-format": 1.1,
    }
    analysis = Analysis(projects=projects, results=results, metadata=metadata)
    return analysis, DATA_DIR / "basic.analysis.json"


class TestAnalysis:
    def test_check_file(self) -> None:
        r = check_file(DATA_DIR / "nothing-changed.py")
        assert isinstance(r, NothingChangedResult) and r.type == "nothing-changed"

        r = check_file(DATA_DIR / "reformatted.py")
        assert isinstance(r, ReformattedResult) and r.type == "reformatted"
        assert r.src == "a = 'daylily'\n" and r.dst == 'a = "daylily"\n'

        r = check_file(DATA_DIR / "failed.py")
        assert isinstance(r, FailedResult) and r.type == "failed"
        assert r.error == "AssertionError" and r.log
        r = check_file(
            DATA_DIR / "reformatted.py", mode=black.Mode(string_normalization=False)
        )
        assert isinstance(r, NothingChangedResult) and r.type == "nothing-changed"

    def test_clone_repo(self, tmp_path: Path) -> None:
        target = Path(tmp_path, "diff-shades")
        # NOTE: I know test inter-dependance is bad but deal with it
        diff_shades.analysis.clone_repo(
            DIFF_SHADES_GIT_URL, to=target, sha="7a89fde30be692e21ffc70b0e8fbade59e322319"
        )
        sha, msg = diff_shades.analysis.get_commit(target)
        assert sha == "7a89fde30be692e21ffc70b0e8fbade59e322319"
        assert msg == "Branding: add logo ❀"

    def test_get_commit(self, tmp_path: Path) -> None:
        target = Path(tmp_path, "diff-shades")
        run_cmd([GIT_BIN, "clone", DIFF_SHADES_GIT_URL], cwd=tmp_path)
        sha, msg = diff_shades.analysis.get_commit(target)
        assert sha and msg
        run_cmd([GIT_BIN, "checkout", "7a89fde30be692e21ffc70b0e8fbade59e322319"], cwd=target)
        sha, msg = diff_shades.analysis.get_commit(target)
        assert sha == "7a89fde30be692e21ffc70b0e8fbade59e322319"
        assert msg == "Branding: add logo ❀"

    def test_get_files_and_mode(self) -> None:
        get_files_and_mode = diff_shades.analysis.get_files_and_mode

        single_proj = Project("single-file-proj", "throwaway-url")
        files, mode = get_files_and_mode(single_proj, DATA_DIR / "single-file-proj")
        expected = [DATA_DIR / "single-file-proj" / "a.py"]
        assert files == sorted(expected) and mode == black.Mode(line_length=79)

        multi_proj = Project("multi-file-proj", "throwaway-url")
        files, mode = get_files_and_mode(multi_proj, DATA_DIR / "multi-file-proj")
        expected = [
            DATA_DIR / "multi-file-proj" / "a.py",
            DATA_DIR / "multi-file-proj" / "b.py",
        ]
        assert files == sorted(expected) and mode == black.Mode(line_length=79)

        files, mode = get_files_and_mode(
            single_proj, DATA_DIR / "single-file-proj", extra_args=("-l", "100")
        )
        assert len(files) == 1 and mode == black.Mode(line_length=100)

    def test_suppress_output(self, capfd: pytest.CaptureFixture) -> None:
        with diff_shades.analysis.suppress_output():
            print("hi")
            click.echo("hi")
            black.main(["--version"], standalone_mode=False)

        captured = capfd.readouterr()
        assert not captured.out and not captured.err


class TestConfig:
    def test_project_supported_by_runtime(self) -> None:
        simple = Project("diff-shades", DIFF_SHADES_GIT_URL)
        supported = replace(simple, python_requires=">=3.6")
        unsupported = replace(simple, python_requires=">=5.0.0")
        assert simple.supported_by_runtime, "no python_requires should always be supported"
        assert supported.supported_by_runtime
        assert not unsupported.supported_by_runtime


class TestResults:
    def test_calculate_line_changes(self) -> None:
        diff = """\
        --- a/src/diff_shades/config.py
        +++ b/src/diff_shades/config.py
        @@ -44,15 +44,13 @@
                 "django",
                 "https://github.com/django/django.git",
                 custom_arguments=[
                     "--skip-string-normalization",
                     "--extend-exclude",
        -            (
        -                "/((docs|scripts)/|django/forms/models.py"
        -                "|tests/gis_tests/test_spatialrefsys.py"
        -                "|tests/test_runner_apps/tagged/tests_syntax_error.py)"
        -            ),
        +            "/((docs|scripts)/|django/forms/models.py"
        +            "|tests/gis_tests/test_spatialrefsys.py"
        +            "|tests/test_runner_apps/tagged/tests_syntax_error.py)",
                 ],
                 python_requires=">=3.8",
             ),
             Project("flake8-bugbear", "https://github.com/PyCQA/flake8-bugbear.git"),
             Project("hypothesis", "https://github.com/HypothesisWorks/hypothesis.git"),
        """
        diff = textwrap.dedent(diff)
        changes = diff_shades.results.calculate_line_changes(diff)
        assert changes == (3, 5)

    def test_diff_two_results(self) -> None:
        nothing = NothingChangedResult("a\n")
        reformatted = ReformattedResult("a\n", "A\n")
        failed = FailedResult("a\n", error="RuntimeError", message="heck no!")
        failed2 = replace(failed, message="nah, I'm out")
        diff_two_results = diff_shades.results.diff_two_results

        assert not diff_two_results(nothing, nothing, "1.py")
        assert not diff_two_results(reformatted, reformatted, "1.py")
        diff = diff_two_results(nothing, reformatted, "1.py")
        # fmt: off
        assert diff == textwrap.dedent("""\
            --- a/1.py
            +++ b/1.py
            @@ -1 +1 @@
            -a
            +A
        """)
        # fmt: on

        with pytest.raises(ValueError, match="Cannot diff failing file results"):
            diff_two_results(nothing, failed, "1.py")
        diff = diff_two_results(nothing, failed, "1.py", diff_failure=True)
        assert "-[no crash]\n+[RuntimeError: heck no!]" in diff
        diff = diff_two_results(failed, failed2, "1.py", diff_failure=True)
        assert "-[RuntimeError: heck no!]\n+[RuntimeError: nah, I'm out]" in diff

    def test_filter_results(self) -> None:
        nothing = NothingChangedResult("a")
        reformatted = ReformattedResult("b", "B")
        failed = FailedResult("c", error="RuntimeError", message="heck no!")

        results: Dict[str, FileResult]
        assert filter_results({}, "nothing-changed") == {}
        results = {"1.py": nothing, "2.py": nothing, "3.py": nothing}
        assert filter_results(results, "nothing-changed") == results
        assert filter_results(results, "failed") == {}
        results = {"1.py": nothing, "2.py": reformatted, "3.py": failed}
        assert filter_results(results, "failed") == {"3.py": failed}
        assert filter_results(results, "reformatted") == {"2.py": reformatted}
        assert filter_results(results, "nothing-changed") == {"1.py": nothing}

    def test_get_overall_result(self) -> None:
        nothing = NothingChangedResult("a")
        reformatted = ReformattedResult("b", "B")
        failed = FailedResult("c", error="RuntimeError", message="heck no!")
        get_overall_result = diff_shades.results.get_overall_result

        r = get_overall_result([nothing, nothing])
        assert r == "nothing-changed"
        r = get_overall_result([nothing, reformatted, nothing])
        assert r == "reformatted"
        r = get_overall_result([nothing, failed])
        assert r == "failed"
        r = get_overall_result([reformatted, reformatted, failed])
        assert r == "failed", "failed should win over reformatted"

        r = get_overall_result({"1.py": nothing, "2.py": nothing})
        assert r == "nothing-changed"
        r = get_overall_result({"1.py": nothing, "2.py": reformatted, "3.py": nothing})
        assert r == "reformatted"
        r = get_overall_result({"1.py": nothing, "2.py": failed})
        assert r == "failed"
        r = get_overall_result({"1.py": reformatted, "2.py": reformatted, "3.py": failed})
        assert r == "failed", "failed should win over reformatted"

    def test_load_analysis(self, tmp_path: Path) -> None:
        analysis, filepath = get_basic_analysis()
        with patch("diff_shades.results.CACHE_DIR", new=tmp_path):
            loaded_analysis, cached = load_analysis(filepath)
            assert not cached, "hmm, test interference?"
            assert analysis == loaded_analysis

            with pytest.raises(ValueError, match="unsupported analysis format"):
                analysis, _ = load_analysis(DATA_DIR / "invalid-data-format.analysis.json")

    def test_load_analysis_caching(self, tmp_path: Path) -> None:
        _, filepath = get_basic_analysis()
        with patch("diff_shades.results.CACHE_DIR", new=tmp_path):
            analysis, cached = load_analysis(filepath)
            assert not cached
            cached_analysis, cached = load_analysis(filepath)
            assert analysis == cached_analysis and cached

            shutil.rmtree(tmp_path)
            tmp_path.mkdir()
            for i in range(10):
                Path(tmp_path, f"idk-{i}").write_text("throwaway", "utf-8")
            load_analysis(filepath)
            entries = len(list(tmp_path.iterdir()))
            assert entries == 5

    def test_load_analysis_with_zip(self, tmp_path: Path) -> None:
        with patch("diff_shades.results.CACHE_DIR", new=tmp_path):
            analysis, _ = load_analysis(DATA_DIR / "diff-shades-default.analysis.json")
            analysis2, _ = load_analysis(DATA_DIR / "diff-shades-default.analysis.zip")
            assert analysis == analysis2

            with pytest.raises(DSError, match="more than one member"):
                load_analysis(DATA_DIR / "too-many-members.analysis.zip")

    def test_save_analysis_with_zip(self, tmp_path: Path) -> None:
        analysis, known_good_path = get_basic_analysis()
        save_analysis(tmp_path / "analysis.zip", analysis)
        with ZipFile(tmp_path / "analysis.zip") as zfile:
            with zfile.open("analysis.json") as f:
                assert f.read().decode("utf-8") == known_good_path.read_text("utf-8")

    def test_unified_diff(self) -> None:
        # fmt: off
        a = textwrap.dedent("""\
            aaaa
            bbbb
            cccc
        """)
        b = textwrap.dedent("""\
            aaaa
            BBBB
            cccc
        """)

        diff = diff_shades.results.unified_diff(a, a, "1.py", "2.py")
        assert not diff
        diff = diff_shades.results.unified_diff(a, b, "1.py", "2.py")
        assert diff == textwrap.dedent("""\
            --- 1.py
            +++ 2.py
            @@ -1,3 +1,3 @@
             aaaa
            -bbbb
            +BBBB
             cccc
        """)
        diff = diff_shades.results.unified_diff(a.rstrip(), b, "1.py", "2.py")
        assert diff == textwrap.dedent("""\
            --- 1.py
            +++ 2.py
            @@ -1,3 +1,3 @@
             aaaa
            -bbbb
            -cccc
            \\ No newline at end of file
            +BBBB
            +cccc
        """)
        # fmt: on


def test_run_as_package() -> None:
    run_cmd([sys.executable, "-m", "diff_shades", "--version"])


@pytest.mark.parametrize(
    "filename",
    [
        "basic.analysis.json",
        "diff-shades-default.analysis.json",
        "diff-shades-default.analysis.zip",
    ],
)
def test_show(filename: str, runner: CLIRunner) -> None:
    runner.check(["show", DATA_DIR / filename])


@pytest.mark.parametrize("file", ["myfile", "mysecondfile", "mythirdfile"])
def test_show_with_file(file: str, runner: CLIRunner) -> None:
    runner.check(["show", DATA_DIR / "failing-2.json", "daylily", file])


def test_show_with_file_result_attribute(runner: CLIRunner) -> None:
    analysis, filepath = get_basic_analysis()
    r = runner.check(["show", filepath, "test", "a.py", "src"])
    assert analysis.results["test"]["a.py"].src in r.stdout


def test_show_with_file_result_attribute_logged(runner: CLIRunner, tmp_path: Path) -> None:
    analysis, filepath = get_basic_analysis()
    log = tmp_path / "log.html"
    cmd = [sys.executable, "-m", "diff_shades"]
    cmd = [*cmd, "--dump-html", str(log)]
    cmd = [*cmd, "show", str(filepath), "test", "a.py", "src", "--quiet"]
    run_cmd(cmd)
    contents = log.read_text("utf-8")
    assert analysis.results["test"]["a.py"].src in contents


@pytest.mark.parametrize("filename", ["basic.analysis.json", "failing.json"])
def test_show_failed_with_check(filename: str, runner: CLIRunner) -> None:
    r = runner.run(["show-failed", DATA_DIR / filename, "--check"])
    r.assert_return_code(1 if "failing" in filename else 0)


def test_show_failed_specific_project(runner: CLIRunner) -> None:
    runner.check(["show-failed", DATA_DIR / "failing.json", "daylily"])


def test_show_failed_show_log(runner: CLIRunner) -> None:
    runner.check(["show-failed", DATA_DIR / "failing.json", "--show-log"])


def test_compare_without_changes_diff(runner: CLIRunner) -> None:
    cmd: SupportedArgs = [
        "compare",
        DATA_DIR / "failing.json",
        DATA_DIR / "failing.json",
        "--check",
        "--diff",
    ]
    r = runner.check(cmd)
    assert "Nothing-changed." in r.stdout


def test_compare_with_changes(runner: CLIRunner) -> None:
    r = runner.check(["compare", DATA_DIR / "failing.json", DATA_DIR / "failing-2.json"])
    assert "Differences found." in r.stdout


def test_compare_with_changes_diff(runner: CLIRunner) -> None:
    cmd: SupportedArgs = [
        "compare",
        DATA_DIR / "failing.json",
        DATA_DIR / "failing-2.json",
        "--check",
        "--diff",
    ]
    r = runner.run(cmd)
    r.assert_return_code(1)


def test_analyze_specific_project(runner: CLIRunner, tmp_path: Path) -> None:
    try:
        runner.check(["analyze", tmp_path / ".json", "-s", "diff-shades"])
    except PermissionError:
        if WINDOWS and os.getenv("CI"):
            # Windows on GHA doesn't allow .git's contents to be deleted.
            pass
        else:
            raise


def test_analyze_project_caching(runner: CLIRunner, tmp_path: Path) -> None:
    out = tmp_path / "analysis.json"
    cache = tmp_path / "projects-cache"
    runner.check(["analyze", out, "-s", "diff-shades", "-w", cache])
    with patch("diff_shades.analysis.clone_repo", lambda *args: 1 / 0):
        runner.check(["analyze", out, "-s", "diff-shades", "-w", cache])


def test_analyze_specific_project_custom_args(runner: CLIRunner, tmp_path: Path) -> None:
    try:
        runner.check(["analyze", tmp_path / ".json", "-s", "diff-shades", "--", "-S"])
    except PermissionError:
        if WINDOWS and os.getenv("CI"):
            # Windows on GHA doesn't allow .git's contents to be deleted.
            pass
        else:
            raise


def test_analyze_repeat_projects_from(runner: CLIRunner, tmp_path: Path) -> None:
    try:
        runner.check(
            [
                "analyze",
                tmp_path / ".json",
                "--repeat-projects-from",
                DATA_DIR / "diff-shades-default.analysis.json",
            ]
        )
    except PermissionError:
        if WINDOWS and os.getenv("CI"):
            # Windows on GHA doesn't allow .git's contents to be deleted.
            pass
        else:
            raise
