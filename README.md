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
- Per-project python_requires support
- Custom per-analysis formatting configuration
- Oh and of course, pretty output!

Potential tasks / additionals:

- jupyter notebook support
- even more helpful output
- better UX (particularly when things go wrong)
- code cleanup as my code is messy as usual :p

## Installation

**Pre-requisite**: Python 3.7 or higher

diff-shades is currently not available on any public index. This might change
later on, but for the time being you can install diff-shades via this command:

```
python -m pip install https://github.com/ichard26/diff-shades/archive/main.zip
```

## Usage

```
Usage: diff-shades [OPTIONS] COMMAND [ARGS]...

  The Black shade analyser and comparison tool.

Options:
  --no-color / --force-color  Force disable/enable colored output.
  --show-locals               Show locals for unhandled exceptions.
  --dump-html FILE            Save a HTML copy of the emitted output.
  --version                   Show the version and exit.
  --help                      Show this message and exit.

Commands:
  analyze      Run Black against 'millions' of LOC and save the results.
  compare      Compare two analyses for differences in the results.
  show         Show results or metadata from an analysis.
  show-failed  Show and check for failed files in an analysis.
```

### Running an analysis

**Pre-requisite**: a pre-installed version of Black you want to analyze

Run the `analyze` command with an filepath to save the results to.

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades analyze data.json -s attrs -s blackbench -s diff-shades -s ptr
[22:51:49] Cloned attrs - https://github.com/python-attrs/attrs.git
[22:51:50] Cloned blackbench - https://github.com/ichard26/blackbench.git
           Cloned diff-shades - https://github.com/ichard26/diff-shades.git
[22:51:51] Cloned ptr - https://github.com/facebookincubator/ptr.git
Setting up projects ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% - 4/4 - 0:00:03
[22:51:58] attrs finished as reformatted
           blackbench finished as nothing-changed
           diff-shades finished as nothing-changed
[22:51:59] ptr finished as nothing-changed
Running black ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% - 76/76 - 0:00:08

╭───────────────────────────── Summary ──────────────────────────────╮
│         File breakdown                   Project breakdown         │
│  Result            # of files     Result            # of projects  │
│ ──────────────────────────────   ───────────────────────────────── │
│  nothing-changed   49             nothing-changed   3              │
│  reformatted       27             reformatted       1              │
│  failed            0              failed            0              │
│                                                                    │
│ # of lines: 21 069                                                 │
│ # of files: 76                               1075 changes in total │
│ # of projects: 4                     267 additions - 808 deletions │
╰───────────── black 21.12b0 - Dec 12 2021 03:51:59 UTC ─────────────╯
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

You can also pass custom Black arguments that'll get joined to the hard-coded
configuration in [`diff_shades/config.py`](./src/diff_shades/config.py). Just
pass `--` and then any valid arguments and you're good! The `--` is strongly
recommended since anything that comes after will be left unprocessed and won’t
be treated as options for diff-shades itself. Any unknown options will be
rejected but unsupported options will be silently ignored... except for the
file discovery options! Those will have an impact and are highly discouraged
since they'll be applied to all selected projects.

For very detailed logs on what exactly diff-shades is doing `--verbose` has you
covered.

### Comparing analyses

Run the `compare` command passing both analyses as arguments. To compare a
specific project also pass the project's name.

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades compare same.json same.json
[18:43:00] Loaded first analysis: /home/ichard26/programming/tools/diff-shades/same.json
[18:43:03] Loaded second analysis: /home/ichard26/programming/tools/diff-shades/same.json

Nothing-changed.

ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades compare analysis.json analysis-2.json
[18:45:08] Loaded first analysis: /home/ichard26/programming/tools/diff-shades/analysis.json
           Loaded second analysis: /home/ichard26/programming/tools/diff-shades/analysis-2.json

╭────────────────────── Summary ──────────────────────╮
│ 7 projects & 25 files changed / 30 changes [+0/-30] │
╰─────────────────────────────────────────────────────╯

Differences found.
```

For extra details there's the mutually exclusive `--diff` and `--list` flags
(although `--list` is not implemented yet :p). They work great especially as CI
reports (perhaps w/ `--dump-html` too).

If you'd like a non-zero exit code if a difference was found please pass
`--check`.

### Showing an analysis

The `show` command is diff-shades's all purpose analysis viewer. It operates at
the analysis, project, file, or even file attribute level.

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades show data.json
[22:53:43] Loaded analysis: /home/ichard26/programming/tools/diff-shades/data.json

╭───────────────────────────── Summary ──────────────────────────────╮
│         File breakdown                   Project breakdown         │
│  Result            # of files     Result            # of projects  │
│ ──────────────────────────────   ───────────────────────────────── │
│  nothing-changed   49             nothing-changed   3              │
│  reformatted       27             reformatted       1              │
│  failed            0              failed            0              │
│                                                                    │
│ # of lines: 21 069                                                 │
│ # of files: 76                               1075 changes in total │
│ # of projects: 4                     267 additions - 808 deletions │
╰───────────── black 21.12b0 - Dec 12 2021 03:51:59 UTC ─────────────╯

 Name          Results (n/r/f)   Line changes (total +/-)   # files   # lines
──────────────────────────────────────────────────────────────────────────────
 attrs         23/27/0           1075 [267/808]             50        16 068
 blackbench    13/0/0            n/a                        13        1627
 diff-shades   8/0/0             n/a                        8         1176
 ptr           5/0/0             n/a                        5         2198
```

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades show data.json diff-shades src/diff_shades/config.py
[22:59:42] Loaded analysis: /home/ichard26/programming/tools/diff-shades/data.json

Nothing-changed.
```

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades show data.json diff-shades src/diff_shades/__init__.py src
[23:02:16] Loaded analysis: /home/ichard26/programming/tools/diff-shades/data.json

"""
The Black shade analyser and comparison tool.
"""

__author__ = "Richard Si, et al."
__license__ = "MIT"
__version__ = "21.12a2"
```

If you're using `show` as part of a larger script, then `-q` / `--quiet` may be
useful by suppressing non-essential output.

**Note**: show-ing a project is currently not implemented and so are `--diff` /
`--list` output modes.

### Showing an analysis (failures only!)

The `show-failed` command lists all failures (optionally narrowable to a single
project) in a compact format.

```console
ichard26@acer-ubuntu:~/programming/tools/diff-shades$ diff-shades show-failed failing.json
[22:46:04] Loaded analysis: /home/ichard26/programming/tools/diff-shades/failing.json

daylily:
  1. myfile: AssertionError - i feel like crashing today

# of failed files: 1
# of failed projects: 1
```

Asserting that the analysis is failure-free is incredibly simple with
`--check`.

### Appendix: tips!

diff-shades supports reading analyses stored as ZIP files as uncompressed
analysis files frequently hit the 100MB+ milestone. No special handing is
required, just pass a file with a `.zip` extension and diff-shades will
auto-extract it!

diff-shades also caches analysis file reads (saving the loaded objects as
pickles) to further improve responsiveness and overall performance. At most
five analyses will be cached at once. Cache entries which haven't been used in
the last 5 days will be dropped. If you're experiencing weird issues you can
clear the cache with the `--clear-cache` flag (before the command!).

If you're using diff-shades in CI, `--dump-html` might come in handy saving a
copy of all emitted output. Unfortunately it behaves poorly with progress bars
so don't use this w/ `analyze`. Additionally, `--no-color` and `--force-color`
exist to override rich's color support detection (say for GHA where colors are
supported but rich doesn't know that).

## Contributing

to be written ...

## License

diff-shades: MIT.

Analyses generated by diff-shades may contain code not licensed under MIT.
Please check the list of analyzed projects for their licensing details.

## Acknowledgements

Maintainers:

- Richard S. ([@ichard26](https://github.com/ichard26))

diff-shades also sees outside contributions whose contributors I greatly
appreciate. A list of all contributors can be found on the
[repo's insights page](https://github.com/ichard26/diff-shades/graphs/contributors).

Finally, this project wouldn't have existed if it wasn't for [black-primer]
which the legendary Cooper Lees (co-maintainer of psf/black) originally wrote.
Black-primer eventually spawned the creation of [mypy-primer], a black-primer
equivalent for mypy. Many features present in diff-shades come from
black-primer and mypy-primer.

## Changelog

### 21.12a6

- Don't forcefully set `--force-colors` if `--no-colors` was passed on GHA.
- Add `--quiet` to the compare command to suppress the unnecessary log
  messages.

### 21.12a5

- When running on GitHub Actions, `--force-colors` and the width will be
  forcefully set for you.

### 21.12a4

First public release, enjoy the alpha quality software :)

[black-primer]: https://github.com/psf/black/tree/main/src/black_primer
[mypy-primer]: https://github.com/hauntsaninja/mypy_primer/
