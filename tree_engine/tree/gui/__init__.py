"""Local browser GUI for TREE (FastAPI + htmx + server-rendered HTML).

Requires the ``[gui]`` extra. The server binds loopback only and is launched
via ``tre gui``; it reuses the same engine/progress/planner functions the CLI
already exposes — it is a presentation layer, not new pipeline logic.
"""
