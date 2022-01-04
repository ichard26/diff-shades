import rich
from diff_shades.cli import main
from diff_shades._diff import Diff

# main()

lhs = """\
def hello():
    print("Hello, world!")

hello()
"""
rhs = """\
def hello(name):
    print(f"Hello, {name}!")

hello("World")
"""

diff = Diff(lhs, rhs, "dark")
rich.print(diff)