import shutil
import subprocess
import sys
import textwrap
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Dict

if sys.version_info >= (3, 8):
    from typing import Final
else:
    from typing_extensions import Final

import black
import click
import pytest

import diff_shades.analysis
import diff_shades.results
from diff_shades.analysis import check_file
from diff_shades.config import Project
from diff_shades.results import (
    FailedResult,
    FileResult,
    NothingChangedResult,
    ReformattedResult,
    filter_results,
)

THIS_DIR: Final = Path(__file__).parent
DATA_DIR: Final = THIS_DIR / "data"
GIT_BIN: Final = shutil.which("git")
DIFF_SHADES_GIT_URL: Final = "https://github.com/ichard26/diff-shades.git"
run_cmd: Final = partial(
    subprocess.run,
    check=True,
    encoding="utf8",
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)

assert GIT_BIN, "running tests requires a discoverable Git binary"


def read_data(name: str) -> str:
    path = Path(DATA_DIR, name)
    return path.read_text(encoding="utf-8")


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
        diff_shades.analysis.clone_repo(DIFF_SHADES_GIT_URL, to=target)
        # NOTE: I know test inter-dependance is bad but deal with it
        sha, msg = diff_shades.analysis.get_commit(target)
        assert sha and msg
        shutil.rmtree(target)
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
            \ No newline at end of file
            +BBBB
            +cccc
        """)
        # fmt: on
