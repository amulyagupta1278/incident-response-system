# D Drive Runbook

This folder is the canonical working copy:

```powershell
D:\saarthi_models\incident-response-system
```

Run the full system with one command:

```powershell
.\run.ps1
```

or:

```bat
run.bat
```

Useful options:

```powershell
.\run.ps1 -Dev        # start Next.js in dev mode
.\run.ps1 -Rebuild    # force a production frontend rebuild
.\run.ps1 -SkipInstall
```

The script keeps Python `venv`, Next.js dependencies, and build output inside this D-drive folder. Git is preserved here, so commits and pushes should be run from this directory.
