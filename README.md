# diff-shades

The Black shade analyser and comparsion tool.

AKA Richard's personal take at a better black-primer (by stealing
ideas from mypy-primer) :p

Basically runs Black over millions of lines of code from various
open source projects. Why? So any changes to Black can be gauged
on their relative impact.

Features include:
 - Simple but readable diffing capabilities
 - Repeatable analyses via --repeat-projects-from
 - Structured JSON output
 - Oh and of course, pretty output!

Potential tasks / additionals:
 - jupyter notebook support
 - per-project python_requires support
 - even more helpful output
 - stronger diffing abilities
 - better UX (particularly when things go wrong)
 - so much code cleanup - like a lot :p

**Notice: this is a in-progress rewrite of the original
[diff-shades][original].** I'm rewriting it as the code was quite
unmaintainable as I built it wayy too fast. This time I'm focusing
on building a good project structure, prettifying the output, and
improving functionality. The thing is that this is a WIP so the
claimed features above aren't accurate right now.

## Installation

1. Clone the repository

1. Do a local pip install in the project root (eg. `python -m pip install .`)

## Usage

*todo: finish the readme once done with the rewrite*



[original]: https://github.com/ichard26/black-mypyc-wheels/blob/main/diff_shades.py
