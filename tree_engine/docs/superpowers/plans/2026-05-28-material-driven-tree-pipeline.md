# Material-Driven T.R.E.E. Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the full T.R.E.E. loop from uploaded materials alone: PaddleOCR extracts files, Archivist structures them, Examiner generates exams from structured source material, Student answers, Examiner audits, and Writer produces knowledge files.

**Architecture:** `source_materials/` is the only discovery and exam-generation source. There is no external question-bank directory. Chapters map to source collections, and each in-progress chapter carries a `source_collection` field in `pipeline-state.json`.

**Tech Stack:** Python 3.12, Typer CLI, OpenAI-compatible chat API, PaddleOCR remote API, Markdown source files, Pydantic state models, pytest + ruff.

---

## Implemented Direction

```text
materials or user-selected files
  ↓ tree-run ingest --input <file-or-dir> --collection <name>
PaddleOCR
  ↓
Archivist
  ↓
source_materials/<collection>/*.md
  ↓ tree-run run
Examiner scans source_materials and creates exams
  ↓
Student answers from prior outputs + current draft only
  ↓
Examiner audits
  ↓
Writer writes drafts
  ↓
PASS moves draft to outputs/<chapter>/
```

## Current Required Code Surfaces

- `tree/io/source_ops.py`: source collection listing and Markdown loading.
- `tree/ingest.py`: integrated PaddleOCR -> Archivist -> Markdown ingestion.
- `tree/agents/prompts.py`: built-in prompts for examiner, student, writer, archivist.
- `tree/agents/examiner.py`: source-material prompt payloads for chapter discovery and exam generation.
- `tree/engine.py`: source-material chapter discovery and Step 1 source injection.
- `tree/state/models.py`: `source_collection` tracking.
- `tree/cli.py`: `tree-run ingest --collection`.
- Historical regression coverage was removed from the repository.

## Verification Commands

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check tree ingest rag
.venv/bin/python -m compileall -q tree ingest rag
.venv/bin/tree-run ingest --help
.venv/bin/tree-run status
```
