# Install the worker on the server

Use **install_background_worker.cmd** as the worker installation entry point.
Its accompanying PowerShell file is the backend, not a second installation step.
Python and `.venv` are unnecessary for an installed `Biuro.exe`.

1. Sign into Windows on the server as the account that will run the worker
   (for example `TenvirkADM`). Install Biuro if needed.
2. Open Biuro on that server. Configure the **same PostgreSQL database** and
   **same office encryption key** as on your PC. Enable the desired jobs in
   **Settings → Worker w tle**. The Windows task account needs this configuration
   in its own profile; the Biuro login is separate from the Windows account.
3. In Biuro's installation folder, open `scripts`. Right-click
   **install_background_worker.cmd → Run as administrator**. If using the
   standalone helper ZIP, extract it first and keep both scripts together.
   If the application cannot be found automatically, select the installed
   **Biuro.exe** in the file chooser.
4. Enter the task account's **Windows** credentials. Do not enter the SQL `sa`
   password or the Biuro application password in this prompt.
5. Check that **Settings → Worker w tle** displays a recent heartbeat. You can
   close the Biuro window; the worker starts with the server and runs without
   an interactive Windows login. The server must remain powered on.

For a nonstandard installation folder, use an Administrator PowerShell window
in the helper folder:

```powershell
.\install_background_worker.cmd -ExecutablePath "D:\Apps\Biuro\Biuro.exe"
```

Replace the example with the real EXE location. If your helper files are in
`C:\BiuroSync\scripts`, keep the updated CMD and PS1 there together. No manual
project-directory argument is needed. The launcher passes its own absolute
location; fallback directory detection runs inside error handling, after
PowerShell binds parameters.

## If something fails

- The window stays open. Copy the error text, not passwords or configuration
  values. An execution-policy block happens before the backend can create a
  log; the launcher retains that message without changing system policy.
- Installer log: `%LOCALAPPDATA%\Biuro\logs\worker-install.log` for the account
  running the installer.
- Worker log: `%LOCALAPPDATA%\Biuro\logs\biuro.log` for the **task account**.
  That account needs `DB_URL` and the shared `BIO_ENCRYPTION_KEY` in its environment
  or `%LOCALAPPDATA%\Biuro\.env`, and permission to read the EXE and required files.
- The script does not overwrite an existing task. Inspect/edit the existing
  task in Task Scheduler when reported; avoid creating duplicate workers.
- The standalone ENOVA daily-task installer/wrapper have been removed. If a
  legacy `Biuro-Enova-Daily` task is still registered on the server, disable it
  after confirming the unified worker heartbeat. Removing repository files
  does not delete scheduled tasks on your server.

A complete installed application is required; helpers alone are not a worker
runtime. Source installations remain supported with their real `.venv` and
installed dependencies, as described in [background-worker.md](background-worker.md).
Installing system Python by itself does not create that environment.

GitHub Windows CI invokes the installer backend in path-only mode under the
actual Windows PowerShell 5.1 executable, from a different directory and with
spaces in the installation path. This does not register tasks or execute jobs.
The release workflow also publishes a `worker-installer-windows` helper artifact.
