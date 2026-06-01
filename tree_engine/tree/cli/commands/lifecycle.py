"""Lifecycle commands: start / stop / quit / run / resume / continue.

Background process management for the engine (the embedding server stays global
and shared). See docs/REBUILD-DESIGN.md §8, docs/LEGACY-DESIGN.md §8.2.
TODO (step 9): move start/run/etc. out of cli/app.py into here.
"""
