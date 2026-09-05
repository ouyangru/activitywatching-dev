# ActivityWatch deployment notes

This folder is prepared for deploying the official ActivityWatch repository:

```powershell
.\deploy-activitywatch.ps1
```

If GitHub must be reached through a local proxy, pass it only for this run:

```powershell
.\deploy-activitywatch.ps1 -Proxy http://127.0.0.1:7890
```

To build and start through the repository's own `Makefile`:

```powershell
.\deploy-activitywatch.ps1 -Run
```

Current machine state checked by Codex:

- `git` is installed.
- `python` is installed: 3.10.10.
- `node` is installed: 22.11.0.
- `npm` is installed: 10.9.0.
- `poetry` is not installed globally, so the script installs it into `.venv-tools`.
- `make` is not installed, so the script will stop before build until GNU Make is available.
- `rustc` and `cargo` are not installed. They may be needed if the current ActivityWatch branch builds the Rust server components from source.

The command environment currently has invalid proxy variables set to `http://127.0.0.1:9`. The script removes those values only inside its own PowerShell process unless you explicitly pass `-Proxy`.
