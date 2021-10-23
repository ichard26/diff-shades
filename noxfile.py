from pathlib import Path

import nox

SUPPORTED_PYTHONS = ["3.6", "3.7", "3.8", "3.9", "3.10"]

nox.needs_version = ">=2021.10.1"
nox.options.error_on_external_run = True


@nox.session(python=SUPPORTED_PYTHONS)
def tests(session: nox.Session) -> None:
    session.install(".")
    session.run("diff-shades", "--version")
    session.install("black")
    target = Path(session.create_tmp(), "fake-devnull")
    cache = Path(session.create_tmp(), "cache")
    base = ["diff-shades", "analyze", str(target)]
    session.run(*base, "-s", "blackbench", "-w", str(cache))
    session.run(*base, "-s", "blackbench", "-s", "black", "-w", str(cache))
