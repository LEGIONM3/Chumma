# Project Guidelines & Rules

## Git Workflow Rules

### Rule 1: Always work on branch before commit
- **NEVER** commit directly to `main` or `master`.
- Always create or switch to a dedicated feature or task branch (e.g., `git checkout -b feature/<name>` or `git checkout -b dev`) before making any commits.
- Always check the current branch using `git branch --show-current` or `git status` before executing `git commit`.
- Submit changes to `main` via pull requests or branch merges.

## Python Script & Process Execution Rules (Resource Governance)

### Rule 2: Strict Resource Governance for Python Scripts & Processes
- **Zero Zombie Processes**: Never leave long-running, orphaned, or unmanaged `python.exe` / `pytest.exe` processes running in the background. Verify and clean up lingering processes before and after task executions.
- **Plugin Autoload Suppression**: Pytest runs must enforce `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` or pass `-p no:hypothesis -p no:xdist -p no:langsmith -p no:cov -p no:schemathesis` to prevent unbounded memory bloat.
- **Synchronous & Unbuffered Execution**: Execute Python test and verification commands synchronously with adequate wait timeouts; always use `python -u` for scripts to prevent stream buffer deadlocks.
- **No Interactive Pagers**: Commands (such as `git`) must always be invoked with `--no-pager` to prevent interactive terminal locks on Windows.
- **Explicit Resource Cleanup**: All scripts and database connections (SQLAlchemy engine pools, HTTP client sessions, file handles) must be explicitly closed and torn down. Temporary scratch scripts must be removed immediately after verification.
