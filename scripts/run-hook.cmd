: << 'END_OF_CMD_PART'
@echo off
rem run-hook.cmd <script.py> [args...]: starts ../scripts/<script.py> with Python 3.
rem One file for two readers. cmd.exe (Windows, also when Git Bash runs a .cmd) reads
rem the first line as a label and runs this part; sh (macOS, Linux) treats this part
rem as a here-document fed to ':' and runs the part after END_OF_CMD_PART.
rem Windows: a python.exe outside WindowsApps first, then the py launcher, then any
rem python3.exe or python.exe left (the Store alias may be the real Store Python).
rem Arguments must be plain words: cmd.exe /c drops the outer quotes when a quoted
rem argument follows a quoted launcher path, which breaks a path with a space.
setlocal
set "PY="
for %%I in (python.exe) do if not "%%~$PATH:I"=="" set "PY=python"
for %%I in (python3.exe) do if not "%%~$PATH:I"=="" set "PY=python3"
for %%I in (py.exe) do if not "%%~$PATH:I"=="" set "PY=py -3"
for %%I in (python.exe) do if not "%%~$PATH:I"=="" if /i not "%%~dp$PATH:I"=="%LOCALAPPDATA%\Microsoft\WindowsApps\" set "PY=python"
if not defined PY echo run-hook: no Python 3 found on PATH, tried python, py -3 and python3 1>&2
if not defined PY exit /b 1
%PY% -B "%~dp0..\scripts\%~1" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%
END_OF_CMD_PART
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
script="$dir/../scripts/$1"
shift
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" -B "$script" "$@"
  fi
done
echo "run-hook: no Python 3 found on PATH, tried python3 and python" >&2
exit 1
