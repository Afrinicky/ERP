# Packaging

Nickland ERP is shipped as a Windows installer, `NicklandERP-Setup-<version>.exe`, built on
GitHub Actions the same way SECH_LIMS is.

## Building an installer

| How | Result |
| --- | --- |
| **Actions → Build Windows Installer → Run workflow** | The installer, as the `Nickland_ERP_Windows_Installer` artifact (kept 14 days) |
| A push to `main` | The same |
| A tag `v<version>` (for example `git tag v0.2.0 && git push origin v0.2.0`) | The same, and a GitHub release with the installer attached |

The version is `project.version` in `pyproject.toml`; raise it before tagging.

## What the workflow does

`.github/workflows/build-windows-installer.yml`, on `windows-latest`:

1. Builds the interface (`ui/`, Node 22): type check, then `vite build`.
2. Installs Pango and its GTK libraries from MSYS2, which WeasyPrint draws PDF documents
   with.
3. Installs the application with its desktop and PostgreSQL extras, and PyInstaller.
4. **Self-tests from source**: `python -m nickland.desktop --self-test` must find the
   interface and the migrations and draw a PDF.
5. Freezes the application with `packaging/nickland.spec` into `dist/NicklandERP/`.
6. **Self-tests the frozen build** the same way, and runs `nickland-host.exe --help`.
7. Builds the installer with Inno Setup (`packaging/installer.iss`) into `release/`.
8. Uploads it; on a tag, publishes the release.

A build that fails either self-test is not packaged.

## What is installed

| Item | Where |
| --- | --- |
| `NicklandERP.exe` | The application. Standalone by default; a LAN workstation when configured as one |
| `nickland-host.exe` | The LAN host's command: certificates, database roles, upgrade, transfer, serve, backup, drill |
| The interface, the migrations, the PDF libraries | Beside them, in `_internal` |
| Shortcuts | Start menu (the application and the host tools), optionally the desktop |

Program files go to `Program Files\Nickland ERP`. The business's data (database,
attachments, backups, logs, certificates) lives in the user's local application data
folder, `%LOCALAPPDATA%\Nickland\Nickland ERP`, and is never touched by uninstalling or
upgrading.

The window is drawn by Microsoft Edge WebView2, part of Windows 10 and 11. The installer
says so if a computer lacks it.

## First start and upgrades

A new installation has no database. On first start the application creates it by applying
every migration, then shows the setup wizard. An upgraded installation applies only the
migrations it lacks, and copies the database to `backups\before-upgrade-<revision>-<time>.db`
before changing it.

The LAN host is upgraded deliberately instead, by its schema owner with
`nickland-host upgrade`; it refuses to serve a schema that is behind its code.

## Building locally

```bash
pip install -e '.[desktop,postgres,installer]'
(cd ui && npm ci && npm run build)
pyinstaller packaging/nickland.spec --noconfirm
dist/NicklandERP/NicklandERP --self-test
```

On Windows, set `NICKLAND_GTK_DIR` to an MSYS2 `mingw64` folder holding Pango before
running PyInstaller, so the PDF libraries are bundled; then
`iscc /DAppVersion=0.1.0 packaging\installer.iss`.

## Not yet done

The installer is not code-signed, so Windows SmartScreen warns on first run. Signing needs
a certificate, added to the workflow as a secret when one is obtained.
