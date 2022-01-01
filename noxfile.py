import shutil
import sys
from pathlib import Path
from typing import Optional, Union

import nox

THIS_DIR = Path(__file__).parent
TESTS_DIR = THIS_DIR / "tests"
WINDOWS = sys.platform.startswith("win")
SUPPORTED_PYTHONS = ["3.7", "3.8", "3.9", "3.10"]


nox.needs_version = ">=2021.10.1"
nox.options.error_on_external_run = True


def wipe(session: nox.Session, path: Union[str, Path]) -> None:
    if "--install-only" in sys.argv:
        return

    if isinstance(path, str):
        path = Path.cwd() / path
    normalized = path.relative_to(Path.cwd())

    if not path.exists():
        return

    if path.is_file():
        session.log(f"Deleting '{normalized}' file.")
        path.unlink()
    elif path.is_dir():
        session.log(f"Deleting '{normalized}' directory.")
        shutil.rmtree(path)


def get_flag(session: nox.Session, flag: str) -> bool:
    if flag in session.posargs:
        index = session.posargs.index(flag)
        del session.posargs[index]
        return True

    return False


def get_option(session: nox.Session, name: str) -> Optional[str]:
    assert name.startswith("--")
    if name in session.posargs:
        index = session.posargs.index(name)
        try:
            value = session.posargs[index + 1]
        except IndexError:
            session.warn(f"[WARN] missing argument to {name}")
        else:
            del session.posargs[index : index + 1]
            assert isinstance(value, str)
            return value

    return None


@nox.session(name="lint")
def lint(session: nox.Session) -> None:
    """Run pre-commit."""
    session.install("pre-commit")
    session.run("pre-commit", "run", "--all-files", "--show-diff-on-failure")


@nox.session(name="tests", python=SUPPORTED_PYTHONS)
def tests(session: nox.Session) -> None:
    """A proper unit and functional test suite."""
    session.install("-e", ".[test]")
    session.run("diff-shades", "--version")
    black_req = get_option(session, "--black-req")
    if black_req:
        session.install(black_req)
    else:
        session.install("black")

    coverage = not get_flag(session, "--no-cov")
    cmd = ["pytest", "tests"]
    if coverage:
        wipe(session, "htmlcov")
        cmd.extend(["--cov", "--cov-context", "test"])
    session.run(*cmd, *session.posargs)
    if coverage:
        session.run("coverage", "html")


@nox.session(name="smoke-tests", python=SUPPORTED_PYTHONS)
def smoke_tests(session: nox.Session) -> None:
    """Trying to make sure diff-shades isn't fundamentally broken."""
    session.install(".")
    session.run("diff-shades", "--version")
    black_req = get_option(session, "--black-req")
    if black_req:
        session.install(black_req)
    else:
        session.install("black")

    tmp = Path(session.create_tmp())
    target = str(tmp / "fake-devnull")
    cache = str(tmp / "cache")
    failing = str(TESTS_DIR / "data" / "failing.json")
    failing_two = str(TESTS_DIR / "data" / "failing-2.json")
    log = str(tmp / "log.html")
    short_file = "src/diff_shades/__init__.py"

    base = ["diff-shades"]
    if "--force-color" in sys.argv:
        base.append("--force-color")
    session.run(*base, "analyze", target, "-s", "diff-shades", "-w", cache)
    session.run(*base, "analyze", target, "-s", "diff-shades", "-w", cache, "--", "-l", "100")
    session.run(*base, "analyze", target, "-s", "diff-shades", "-s", "ptr", "-w", cache)
    # fmt: off
    session.run(
        *base, "analyze", target, "-s", "diff-shades", "-s", "ptr",
        "-w", cache, "--show-project-revision",
    )
    # fmt: on
    session.run(*base, "show", target)
    session.run(*base, "show", target, "diff-shades", "noxfile.py")
    session.run(*base, "show", target, "diff-shades", short_file, "src")
    session.run(
        *base, "--dump-html", log, "show", target, "diff-shades", short_file, "src", "-q"
    )
    session.run(*base, "compare", target, target, "--check")
    session.run(*base, "compare", failing, failing_two, "--diff")
    session.run(*base, "show-failed", target, "--check")


@nox.session(name="setup-env", venv_backend="none")
def setup_env(session: nox.Session) -> None:
    """Setup a basic (virtual) environment for manual testing."""
    env_dir = THIS_DIR / ".venv"
    bin_dir = env_dir / ("Scripts" if WINDOWS else "bin")
    wipe(session, env_dir)
    session.run(sys.executable, "-m", "virtualenv", str(env_dir))
    session.run(bin_dir / "python", "-m", "pip", "install", "-e", ".")
    session.run(bin_dir / "python", "-m", "pip", "install", "black")
    session.log("Virtual environment at project root under '.venv' ready to go!")
