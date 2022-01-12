# Contributing

**Requirements:**

- [Nox]

  All development commands are managed by Nox which provides automated
  environment provisioning. For us, it’s basically a task runner. I strongly
  recommend using pipx.

- [pre-commit] \[optional\]

  pre-commit runs Git commit hooks that assert a baseline level of quality.
  Once again, pipx is encouraged. It's optional; and you can always use the
  `lint` Nox session instead.

To run the smoke tests (proper tests TBD) simply run `nox -s smoke-tests`. If
you need to run the "test suite" with a specific version of Black you can use
the `--black-req` option. For example,
`nox -s smoke-tests-3.8 -- --black-req "black==21.5b2"`. Note that the `--` is
important since the option was implemented at the session level and is 100%
custom.

You might find it helpful to have a virtual environment to manually test out
your local copy of diff-shades which is why there's a `setup-env` session too.
You can run it with `nox -s setup-env` and it should create a well prepared
environment under `.venv` in the project root. Activating it depends on your
shell and OS but usually it's `source .venv/bin/activate` on Linux / MacOS and
`.venv\Scripts\activate` on Windows.

Maintainers, the main things you need to know are the following:

- psf/black pulls in whatever is on the `stable` branch so make sure to update
  this branch regularly once you're sure diff-shades is stable :wink: (note
  that the pre-commit and Ubuntu CI jobs must pass before updating the branch).

- Please please *please* bump the version in `src/diff_shades/__init__.py`
  *every time* you make a modification to the PROJECTS list so the caching the
  psf/black integration implements won't cause issues.[^1]

Finally, regardless if you are a contributor or a co-maintainer, thank you for
your help! If you get stuck, don’t hesitate to ask for help on the relevant
issue or PR. Alternatively, we can talk in the #black-formatter[^2] text
channel on Python Discord. Here’s an
[invite link](https://discord.gg/RtVdv86PrH)!

[^1]: I'll fix this with a hash based solution eventually, but for now let's be
careful.

[^2]: I know it’s specifically for Black, but diff-shades is a development tool for
Black so I consider it acceptable - although I never asked … but then again, I
am a maintainer of Black so yeah :p

[nox]: https://nox.thea.codes/en/stable/
[pre-commit]: https://pre-commit.com/
