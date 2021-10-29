# diff-shades

The Black shade analyser and comparison tool.

AKA Richard's personal take at a better black-primer (by stealing ideas from
mypy-primer) :p

Basically runs Black over millions of lines of code from various open source
projects. Why? So any changes to Black can be gauged on their relative impact.

Features include:

- Simple but readable diffing capabilities
- Repeatable analyses via --repeat-projects-from
- Structured JSON output
- per-project python_requires support
- Oh and of course, pretty output!

Potential tasks / additionals:

- jupyter notebook support
- custom per-analysis formatting configuration
- even more helpful output
- better UX (particularly when things go wrong)
- code cleanup as my code is messy as usual :p

**Notice: this is a in-progress rewrite of the original
[diff-shades][original].** I'm rewriting it as the code was quite
unmaintainable as I built it wayy too fast. This time I'm focusing on building
a good project structure, prettifying the output, and improving functionality.
The thing is that this is a WIP so the claimed features above aren't accurate
right now.

## Installation

1. Clone the repository

1. Do a local pip install in the project root (eg. `python -m pip install .`)

## Usage

*todo: finish the readme once done with the rewrite*

### Running an analysis

**Pre-requisite**: a pre-installed version of Black you want to analyze

Run the `analyze` command with an filepath to save the results to.

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades analyze data.json
[18:34:44] Cloned blackbench - https://github.com/ichard26/blackbench.git
[18:34:45] Cloned diff-shades - https://github.com/ichard26/diff-shades.git
           Cloned ptr - https://github.com/facebookincubator/ptr.git
[18:34:50] Cloned virtualenv - https://github.com/pypa/virtualenv.git
Setting up projects ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% - 4/4 - 0:00:06
[18:34:51] blackbench finished as nothing-changed
[18:34:52] diff-shades finished as reformatted
[18:34:53] ptr finished as nothing-changed
[18:35:02] virtualenv finished as reformatted
Running black ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% - 173/173 - 0:00:12

╭───────────────────────────── Summary ──────────────────────────────╮
│         File breakdown                   Project breakdown         │
│  Result            # of files     Result            # of projects  │
│ ──────────────────────────────   ───────────────────────────────── │
│  nothing-changed   169            nothing-changed   2              │
│  reformatted       4              reformatted       2              │
│  failed            0              failed            0              │
│                                                                    │
│ # of files: 173                                                    │
│ # of projects: 4                                                   │
╰────────────────────────────────────────────────────────────────────╯
```

To select which projects to run Black over you can use `-s` / `--select` and
`-e` / `--exclude`. As like with Black exclusions are calculated first with
inclusions second.

If you'd like to rerun an analysis with the same project setup there's the
`--repeat-projects-from` option available. It will check if any pre-existing
clones match the recorded commit, re-cloning if not. This is very useful to
compare formatting differences between two versions of Black as this will
ensure the same code was tested over.

Setting a cache directory for clones with `-w` / `--work-dir` is highly
recommended cloning can be rather slow. Don't worry about the cache going
stale, diff-shades will check that the clone is suitable before using it and
will replace it if necessary.

### Comparing two analyses

Run the `compare` command passing both analyses as arguments.

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades compare same.json same.json
[18:43:00] Loaded first analysis: /home/ichard26/programming/tools/diff-shades/same.json
[18:43:03] Loaded second analysis: /home/ichard26/programming/tools/diff-shades/same.json

Nothing-changed.

ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades compare analysis.json analysis-2.json
[18:45:08] Loaded first analysis: /home/ichard26/programming/tools/diff-shades/analysis.json
           Loaded second analysis: /home/ichard26/programming/tools/diff-shades/analysis-2.json

Differences found.
```

If you'd like a non-zero exit code if a difference was found please pass
`--check`.

<!-- footer stuff -->

[original]: https://github.com/ichard26/black-mypyc-wheels/blob/main/diff_shades.py
