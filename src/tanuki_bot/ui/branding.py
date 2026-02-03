from __future__ import annotations

from rich.console import Console
from rich.text import Text


# Big pixel wordmark
TANUKI_WORDMARK = r"""
████████╗ █████╗ ███╗   ██╗██╗   ██╗██╗  ██╗██╗
╚══██╔══╝██╔══██╗████╗  ██║██║   ██║██║ ██╔╝██║
   ██║   ███████║██╔██╗ ██║██║   ██║█████╔╝ ██║
   ██║   ██╔══██║██║╚██╗██║██║   ██║██╔═██╗ ██║
   ██║   ██║  ██║██║ ╚████║╚██████╔╝██║  ██╗██║
   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚═╝  ╚═╝╚═╝
""".strip("\n")


def show_banner(console: Console) -> None:
    console.print()
    console.print(Text(TANUKI_WORDMARK, style="bold dark_orange"))
    console.print()