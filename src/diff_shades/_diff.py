import difflib
from typing import Iterator, List, Union

from pygments.lexers import get_lexer_by_name
from rich.console import Console, ConsoleOptions, RenderResult
from rich.syntax import Syntax
from rich.text import Text


class Diff:
    """Constructs a Diff object to render diff-highlighted code."""

    def __init__(
        self,
        lhs: str,
        rhs: str,
        theme: str = "dark",
    ) -> None:
        self.lhs = lhs
        self.rhs = rhs
        self.theme = theme

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

    def raw_unified_diff(self) -> Iterator[str]:
        differ = difflib.Differ()
        lines_lhs = self.lhs.splitlines()
        lines_rhs = self.rhs.splitlines()
        return differ.compare(lines_lhs, lines_rhs)

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

    def build_unified_diff(self) -> RenderResult:
        diff = self.raw_unified_diff()
        prev_marker = ""
        output_lines: List[Text] = []

        for line in diff:
            if line.startswith("+ "):
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
        yield from self.build_unified_diff()
