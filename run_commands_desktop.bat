@echo off
rem Batch launcher to run app/commands.py using project's venv
rem Copy this file to your Desktop or run it from anywhere — it uses an absolute project path.

set "PROJECT_DIR=C:\Users\zalupix\Documents\VibeProjecting\0xVoice2Code"

pushd "%PROJECT_DIR%" || (
  echo Failed to change directory to %PROJECT_DIR%
  pause
  exit /b 1
)

if exist "%PROJECT_DIR%\venv\Scripts\activate.bat" (
  call "%PROJECT_DIR%\venv\Scripts\activate.bat"
) else (
  echo Virtual environment not found at %PROJECT_DIR%\venv
  echo Please create a venv named "venv" in the project root, or edit this .bat to point to your venv.
  pause
)

rem Run the package so package-level entrypoint (app.__main__) executes
python -m app %*

popd

exit /b %ERRORLEVEL%
