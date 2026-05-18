# Uninstall Aethera

## CLI (recommended)

```bash
# Full uninstall (removes everything)
aethera uninstall

# Keep trading history
aethera uninstall --keep-data

# Skip confirmation prompt
aethera uninstall --yes
```

This removes: CLI symlink, virtual environment, .env (API keys).  
With `--keep-data`: backs up `data/` to `~/aethera-data-backup/`.

---

## Manual Uninstall

### Linux / macOS

```bash
rm -rf ~/aethera ~/.local/bin/aethera
rm -rf ~/coin-screener           # if installed here
rm -rf /opt/aethera              # if VPS install
```

### Windows

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\aethera"
# Then remove from PATH manually via System Settings
```
