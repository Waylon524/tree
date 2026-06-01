"""Interactive TREE> shell.

Slash commands: /start /watch /progress /status /stop /quit /exit /help.
Force-close (Ctrl+C / EOF) runs /quit (stop engine + embedding); only /exit
keeps background services. See docs/LEGACY-DESIGN.md §8.2.

TODO (step 9): full REPL + background process management.
"""

from __future__ import annotations

from rich import print as rprint


def run_repl() -> None:
    rprint("[bold green]TREE[/bold green] interactive shell — skeleton.")
    rprint("Slash commands (/start /watch /status /quit ...) land in step 9.")
    rprint("For now use subcommands, e.g. [cyan]tre doctor[/cyan], [cyan]tre init[/cyan].")
