from pathlib import Path

import nox

THIS_DIR = Path(__file__).parent
TESTS_DIR = THIS_DIR / "tests"
SUPPORTED_PYTHONS = ["3.7", "3.8", "3.9", "3.10"]

nox.needs_version = ">=2021.10.1"
nox.options.error_on_external_run = True


@nox.session(name="smoke-tests", python=SUPPORTED_PYTHONS)
def smoke_tests(session: nox.Session) -> None:
    session.install(".")
    session.run("diff-shades", "--version")
    session.install("black")
    tmp = Path(session.create_tmp())
    target = str(tmp / "fake-devnull")
    cache = str(tmp / "cache")
    failing = str(TESTS_DIR / "failing.json")
    failing_two = str(TESTS_DIR / "failing-2.json")
    log = str(tmp / "log.html")
    short_file = "src/diff_shades/__init__.py"

    base = ["diff-shades", "--force-color"]
    session.run(*base, "analyze", target, "-s", "diff-shades", "-w", cache)
    session.run(*base, "analyze", target, "-s", "diff-shades", "-w", cache, "--", "-l", "100")
    session.run(*base, "analyze", target, "-s", "diff-shades", "-s", "ptr", "-w", cache)
    session.run(*base, "show", target)
    session.run(*base, "show", target, "diff-shades", "noxfile.py")
    session.run(*base, "show", target, "diff-shades", short_file, "src")
    session.run(
        *base, "--dump-html", log, "show", target, "diff-shades", short_file, "src", "-q"
    )
    session.run(*base, "compare", target, target, "--check")
    session.run(*base, "compare", failing, failing_two, "--diff")
    session.run(*base, "show-failed", target, "--check")
