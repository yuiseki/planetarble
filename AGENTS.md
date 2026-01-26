# Agent Guidelines

Before performing any work, always read everything under the `.kiro` directory.

- Task 1 (project structure and core interfaces) completed on 2025-09-23.
- Commands `planetarble acquire`, `planetarble process`, `planetarble tile`, and `planetarble package` are long-running; request a human to execute them instead of running directly, even for small regions. If a run finishes in a few minutes, treat it as a signal that the implementation might be wrong and ask a human to verify.
- A conda environment named `planetarble` exists at `/home/yuiseki/anaconda3/envs/planetarble`. Prefer running via `/home/yuiseki/anaconda3/condabin/conda run -n planetarble ...` for tests and CLI checks.
- Test entry point: `pytest` (see `pyproject.toml`); example: `conda run -n planetarble pytest tests/unit/...`.
- Always run relevant tests before committing changes. If tests cannot be run, state why explicitly and do not commit.
