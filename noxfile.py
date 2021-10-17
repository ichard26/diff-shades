import os

import nox

SUPPORTED_PYTHONS = ["3.6", "3.7", "3.8", "3.9", "3.10"]

nox.options.error_on_external_run = True


@nox.session(python=SUPPORTED_PYTHONS)
def tests(session: nox.Session) -> None:
    session.install(".")
    session.run("diff-shades", "--version")
    session.run("diff-shades", "analyze", os.devnull, "-s", "blackbench", "-w", "cache")
    session.run("diff-shades", "analyze", os.devnull, "-s", "blackbench", "-w", "cache")
