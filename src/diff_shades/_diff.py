import difflib
from typing import Iterator, List, Literal, Union

from pygments.lexers import get_lexer_by_name
from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


class Diff:
    """Constructs a Diff object to render diff-highlighted code."""

    def __init__(
        self,
        lhs: str,
        rhs: str,
        lhs_name: str,
        rhs_name: str,
        theme: Literal["dark", "light"] = "dark",
    ) -> None:
        self.lhs = lhs
        self.rhs = rhs
        self.theme = theme
        self.lhs_name = lhs_name
        self.rhs_name = rhs_name

        self.lexer = get_lexer_by_name(
            "python3",
            stripnl=False,
            ensurenl=True,
        )
        self.syntax = Syntax("", "python3", theme=self.marker_style["code"])

    def syntax_higlight(self, code: str, bg: str) -> Text:
        self.syntax.background_color = bg
        text = self.syntax.highlight(code)
        text.rstrip()
        return text

    @property
    def marker_style(self) -> dict[str, Union[dict[str, str], str]]:
        return {
            # Source: https://github.com/dandavison/delta/blob/50ece4b0cc8dab57a69511fd62013feb892c9c20/themes.gitconfig#L146
            "dark": {
                "code": "ansi_dark",
                "+": {
                    " ": "#004000",
                    "+": "#007800",
                    "^": "#007800",
                },
                "-": {
                    " ": "#400000",
                    "-": "#780000",
                    "^": "#780000",
                },
            },
            # Source: https://github.com/dandavison/delta/blob/50ece4b0cc8dab57a69511fd62013feb892c9c20/themes.gitconfig#L76
            "light": {
                "code": "ansi_light",
                "+": {
                    " ": "#d0ffd0",
                    "+": "#a0efa0",
                    "^": "#a0efa0",
                },
                "-": {
                    " ": "#ffe0e0",
                    "-": "#ffc0c0",
                    "^": "#ffc0c0",
                },
            },
        }[self.theme]

    def raw_unified_diff(self, n: int = 3) -> Iterator[str]:
        """Costum implementation of the unified diff, largely inspired from the implementation of difflib @ cpython."""
        lhs = self.lhs.splitlines()
        rhs = self.rhs.splitlines()

        started = False
        diff = difflib.Differ()
        for group in difflib.SequenceMatcher(None, lhs, rhs).get_grouped_opcodes(n):
            if not started:
                started = True
                yield "--- {}\n+++ {}".format(self.rhs_name, self.lhs_name)

            first, last = group[0], group[-1]
            file1_range = f"{first[1]},{last[2]}"
            file2_range = f"{first[3]},{last[4]}"
            yield "\n"
            yield "@@ -{} +{} @@".format(file1_range, file2_range)

            for tag, alo, ahi, blo, bhi in group:
                if tag == "replace":
                    g = diff._fancy_replace(lhs, alo, ahi, rhs, blo, bhi)
                elif tag == "delete":
                    g = diff._dump("-", lhs, alo, ahi)
                elif tag == "insert":
                    g = diff._dump("+", rhs, blo, bhi)
                elif tag == "equal":
                    g = diff._dump(" ", lhs, alo, ahi)

                yield from g

    def rewrite_line(self, line, line_to_rewrite, prev_marker):
        marker_style_map = self.marker_style.copy()
        new_line = Text("")
        current_span = []

        # Form tokens since we are syntax highlighting the code in pieces
        tokens = []
        for token_type, token in self.lexer.get_tokens(line_to_rewrite):
            style = self.syntax._theme.get_style_for_token(token_type)
            for t in token:
                tokens.append((t, style))

        # Get index of the token in the tokens list so that we can get the required style
        def get_index(token):
            return [x for x, t in enumerate(tokens) if t[0] == token][0]

        # Differ lines start with a 2 letter code, so skip past that
        prev_char = line[2]
        for idx, char in enumerate(line[2:], start=2):
            if prev_marker in ("+", "-"):
                if char != prev_char:
                    bgcolor = marker_style_map.get(prev_marker, {}).get(prev_char, None)

                    if bgcolor is not None:
                        text = Text("")
                        text.append_tokens(
                            [(x, tokens.pop(get_index(x))[1]) for x in current_span]
                        )
                        text.stylize(f"on {bgcolor}")
                        new_line.append_text(text)

                    current_span = []
                if idx - 2 < len(line_to_rewrite):
                    current_span.append(line_to_rewrite[idx - 2])
            prev_char = char

        # Lines starting with ? aren't guaranteed to be the same length as the lines before them
        #  so some characters may be left over. Add any leftover characters to the output.
        # subtract 2 for code at start
        remaining_index = idx - 2

        # remaining_index
        text = Text("")
        text.append_tokens(
            [(x, tokens.pop(get_index(x))[1]) for x in line_to_rewrite[remaining_index:]]
        )
        text.stylize(f"on {marker_style_map[prev_marker][' ']}")
        new_line.append_text(text)

        # Damn, we have completely rewritten the line, that took a lot of work 🥱
        return new_line

    def build_rich_diff(self) -> RenderResult:
        """Compare `self.lhs` and `self.rhs` to generate the delta as a rich unified diff."""
        diff = self.raw_unified_diff(n=5)
        prev_marker = ""
        output_lines: List[Text] = []

        for line in diff:
            if line.startswith("---"):
                output_lines.append(Text(line, style="bold"))
            elif line.startswith("@@"):
                output_lines.append(
                    Panel(
                        line,
                        box=box.ROUNDED,
                        style="cyan" if self.theme == "dark" else "blue",
                    )
                )
            elif line.startswith("+ "):
                output_lines.append(
                    self.syntax_higlight(line[2:], self.marker_style["+"][" "])
                )
            elif line.startswith("- "):
                output_lines.append(
                    self.syntax_higlight(line[2:], self.marker_style["-"][" "])
                )
            elif line.startswith("? "):
                line_to_rewrite = output_lines[-1].plain
                output_lines[-1] = self.rewrite_line(line, line_to_rewrite, prev_marker)
            else:
                output_lines.append(self.syntax_higlight(line[2:], ""))

            prev_marker = line[0]
        return output_lines

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield from self.build_rich_diff()
