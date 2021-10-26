# ============================
# > Project definition & setup
# ============================

import dataclasses
import platform
import sys
from typing import List, Optional

if sys.version_info >= (3, 8):
    from typing import Final
else:
    from typing_extensions import Final

from packaging.specifiers import SpecifierSet


@dataclasses.dataclass(eq=True)
class Project:
    name: str
    url: str
    custom_arguments: List[str] = dataclasses.field(default_factory=list)
    python_requires: Optional[str] = None
    commit: Optional[str] = None

    @property
    def supported_by_runtime(self) -> bool:
        if self.python_requires is None:
            return True

        return SpecifierSet(self.python_requires).contains(platform.python_version())


PROJECTS: Final = [
    Project("aioexabgp", "https://github.com/cooperlees/aioexabgp.git"),
    Project("attrs", "https://github.com/python-attrs/attrs.git"),
    Project("bandersnatch", "https://github.com/pypa/bandersnatch.git"),
    Project("black", "https://github.com/psf/black.git"),
    Project("blackbench", "https://github.com/ichard26/blackbench.git"),
    Project("channel", "https://github.com/django/channels.git"),
    Project(
        "django",
        "https://github.com/django/django.git",
        custom_arguments=[
            "--skip-string-normalization",
            "--extend-exclude",
            (
                "/((docs|scripts)/|django/forms/models.py"
                "|tests/gis_tests/test_spatialrefsys.py"
                "|tests/test_runner_apps/tagged/tests_syntax_error.py)"
            ),
        ],
        python_requires=">=3.8",
    ),
    Project("diff-shades", "https://github.com/ichard26/diff-shades.git"),
    Project("flake8-bugbear", "https://github.com/PyCQA/flake8-bugbear.git"),
    Project("hypothesis", "https://github.com/HypothesisWorks/hypothesis.git"),
    Project("pandas", "https://github.com/pandas-dev/pandas.git"),
    Project("pillow", "https://github.com/python-pillow/Pillow.git"),
    Project("poetry", "https://github.com/python-poetry/poetry.git"),
    Project("pyanalyze", "https://github.com/quora/pyanalyze.git"),
    Project("pyramid", "https://github.com/Pylons/pyramid.git"),
    Project("ptr", "https://github.com/facebookincubator/ptr.git"),
    Project("pytest", "https://github.com/pytest-dev/pytest.git"),
    Project("scikit-lego", "https://github.com/koaning/scikit-lego"),
    Project("sqlalchemy", "https://github.com/sqlalchemy/sqlalchemy.git"),
    Project("tox", "https://github.com/tox-dev/tox.git"),
    Project("typeshed", "https://github.com/python/typeshed.git"),
    Project("virtualenv", "https://github.com/pypa/virtualenv.git"),
    Project("warehouse", "https://github.com/pypa/warehouse.git"),
]
for p in PROJECTS:
    if p.custom_arguments is None:
        p.custom_arguments = ["--experimental-string-processing"]
    else:
        p.custom_arguments.append("--experimental-string-processing")
