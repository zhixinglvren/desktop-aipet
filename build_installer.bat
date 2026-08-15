@echo off
setlocal EnableDelayedExpansion
pushd "%~dp0"

set PY=D:\Software\Python3\python.exe
if not exist "%PY%" set PY=python

REM Project root = script directory. Used to build ABSOLUTE --add-data paths.
REM This is required because we set --specpath dist: with --specpath, PyInstaller
REM resolves relative --add-data sources against the spec directory, so relative
REM paths like "version.txt;." would wrongly resolve to dist\version.txt and fail.
REM Absolute paths keep resolution correct while the .spec still lands in dist\.
set ROOT=%~dp0

REM Clean stale work dirs so only the final output remains in dist.
if exist build (
  rmdir /s /q build
)
if exist dist (
  rmdir /s /q dist
)

REM Generate the application icon (robot theme) if it is not already present.
REM app.ico is a build artifact and is ignored by .gitignore.
if not exist "%ROOT%app.ico" (
  echo Generating application icon...
  "%PY%" "%ROOT%tools\generate_app_icon.py"
  if errorlevel 1 (
    echo [error] Failed to generate app.ico
    exit /b 1
  )
)

REM Step 1: freeze the application into dist\aipet\aipet.exe.
REM --specpath dist + --distpath dist => aipet.spec is written under dist\ (build
REM artifact), NOT in the source tree.
echo Building application executable...
REM 1.0.2 起引入 MCP 通知体系：需收集 mcp / uvicorn / starlette / pydantic / sse_starlette / anyio。
REM notify 包（含 mcp_server）由 aipet.py 静态/惰性导入，PyInstaller 会自动收集；
REM 若运行环境缺失 mcp，aipet.py 会降级（仅关闭通知服务），不影响其余功能。
"%PY%" -m PyInstaller --noconsole --name aipet --icon "%ROOT%app.ico" --specpath dist --distpath dist --hidden-import pystray --hidden-import PIL --hidden-import httpx --hidden-import mcp --hidden-import uvicorn --hidden-import starlette --hidden-import pydantic --hidden-import sse_starlette --hidden-import anyio --collect-all pystray --collect-all PIL --collect-all httpx --collect-all mcp --collect-all uvicorn --collect-all starlette --collect-all pydantic --collect-all sse_starlette --collect-all anyio --add-data "%ROOT%version.txt;." --add-data "%ROOT%config.json;." --add-data "%ROOT%app.ico;." --add-data "%ROOT%pets;pets" --add-data "%ROOT%LICENSE.rtf;." aipet.py
if errorlevel 1 (
  echo [error] Failed to build aipet.exe
  exit /b 1
)

REM Step 1b: freeze the graphical uninstaller as a SINGLE FILE (--onefile) and
REM ship it INSIDE the app folder (dist\aipet\uninstaller.exe) so that installing
REM the app also deploys the uninstaller. It reads the install dir from the
REM registry (REG_APP\InstallDir) written at install time.
echo Building uninstaller...
"%PY%" -m PyInstaller --noconsole --onefile --name uninstaller --icon "%ROOT%app.ico" --specpath dist --distpath dist --add-data "%ROOT%app.ico;." uninstaller.py
if errorlevel 1 (
  echo [error] Failed to build uninstaller.exe
  exit /b 1
)
move /Y "dist\uninstaller.exe" "dist\aipet\uninstaller.exe" >nul
if not exist "dist\aipet\uninstaller.exe" (
  echo [error] uninstaller.exe was not moved into dist\aipet\
  exit /b 1
)

REM Step 2: build the self-contained installer as a SINGLE FILE (--onefile).
REM The app (dist\aipet) and license are embedded; at runtime the installer
REM self-extracts to a temp dir (_MEIxxxx) and copies the embedded app out.
REM The result is one clean desktop-aipet-setup.exe with no _internal folder.
REM desktop-aipet-setup.spec also lands under dist\.
echo Building installer (single-file)...
"%PY%" -m PyInstaller --noconsole --onefile --name desktop-aipet-setup --icon "%ROOT%app.ico" --specpath dist --distpath dist --add-data "%ROOT%dist\aipet;app" --add-data "%ROOT%version.txt;." --add-data "%ROOT%config.json;." --add-data "%ROOT%LICENSE.rtf;." --add-data "%ROOT%app.png;." installer.py
if errorlevel 1 (
  echo [error] Failed to build installer
  exit /b 1
)

REM Verify the single-file installer was produced (no _internal folder in this mode).
if not exist "dist\desktop-aipet-setup.exe" (
  echo [error] desktop-aipet-setup.exe was not produced.
  echo          PyInstaller probably did not finish.
  exit /b 1
)

REM Remove only the intermediate build dir; the .spec files now live in dist\
REM alongside the build output, so they no longer pollute the source tree.
if exist build rmdir /s /q build

echo [done] installer created:
echo    dist\desktop-aipet-setup.exe  (single-file, self-extracting)
echo    build artifacts (including *.spec) are kept under dist\, not in the source tree
echo [note] Do NOT run the exe from a temp folder; use the path above.
