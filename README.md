# SDD Orchestrator

Python-based multi-agent SDD (Specification-Driven Development) and TDD (Test-Driven Development) orchestrator control plane.

## Overview
The SDD Orchestrator manages automated SDD and TDD workflows across LLM agent providers (Antigravity, Codex, OpenCode, Claude Code) with deterministic gates, anti-tampering protection, family independence routing, and persistent SQLite state tracking.

## Installation
```bash
pip install -e .
```

## Usage
Run within any SpecKit-enabled workspace:
```bash
python -m orchestrator --help
python -m orchestrator run
python -m orchestrator verify
python -m orchestrator doctor
```
