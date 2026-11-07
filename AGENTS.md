# Agent Guidelines

- If `AGENTS_MEMO.md` exists at the repository root, read it first to restore project memory: it is an untracked, host-specific operator memo recording the current status, known issues, and disk/storage constraints (e.g. on z-t it documents the Sentinel-2 work history and the `/data` relocation).
- Task 1 (project structure and core interfaces) completed on 2025-09-23.
- Commands `planetarble acquire`, `planetarble process`, `planetarble tile`, and `planetarble package` are long-running; request a human to execute them instead of running directly, even for small regions. If a run finishes in a few minutes, treat it as a signal that the implementation might be wrong and ask a human to verify.
- The environment is managed with uv. Create the venv with `uv venv --python /usr/bin/python3 --system-site-packages && uv sync` so the system GDAL bindings and numpy are visible (see "Local setup (uv)" in README.md for why they are not in `uv.lock`). Do not add `numpy` or `gdal` to `pyproject.toml`.
- Test entry point: `uv run pytest` (pytest is in the `dev` dependency group); example: `uv run pytest tests/unit/...`. If conda's `GDAL_DATA` / `GDAL_DRIVER_PATH` / `PROJ_DATA` are set, unset them first (`env -u GDAL_DATA -u GDAL_DRIVER_PATH -u PROJ_DATA uv run pytest`), otherwise GDAL-backed tests fail.
- Always run relevant tests before committing changes. If tests cannot be run, state why explicitly and do not commit.
- If tests pass, commit without waiting for additional confirmation.
