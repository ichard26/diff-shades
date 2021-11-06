from pathlib import Path

import nox

SUPPORTED_PYTHONS = ["3.6", "3.7", "3.8", "3.9", "3.10"]

nox.needs_version = ">=2021.10.1"
nox.options.error_on_external_run = True


@nox.session(name="smoke-tests", python=SUPPORTED_PYTHONS)
def smoke_tests(session: nox.Session) -> None:
    session.install(".")
    session.run("diff-shades", "--version")
    session.install("black")
    target = Path(session.create_tmp(), "fake-devnull")
    cache = Path(session.create_tmp(), "cache")
    base = ["diff-shades", "--force-color"]
    session.run(*base, "analyze", str(target), "-s", "blackbench", "-w", str(cache))
    session.run(
        *base, "analyze", str(target), "-s", "blackbench", "-s", "black", "-w", str(cache)
    )
    session.run(*base, "show", str(target))
    session.run(*base, "show", str(target), "blackbench:docs/conf.py")
    session.run(*base, "inspect", str(target), "src", "blackbench:docs/conf.py")
    session.run(*base, "compare", str(target), str(target), "--check")
    session.run(*base, "show-failed", str(target), "--check")
