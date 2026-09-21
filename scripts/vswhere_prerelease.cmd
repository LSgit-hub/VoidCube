@echo off
rem vswhere wrapper that forces the prerelease channel to be included.
rem
rem Nuitka's bundled SCons invokes vswhere as:
rem     vswhere.exe -all -products * -format json -utf8
rem without -prerelease, so Insider/Preview Visual Studio installations
rem (e.g. Visual Studio 2026 Insiders) are invisible and Nuitka falls back
rem to the zig C backend, which fails to link the bundled executable.
rem
rem Pointing the VSWHERE environment variable at this wrapper makes SCons
rem "see" the prerelease installation while keeping the real query intact.
setlocal
set "VSWHERE_EXE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE_EXE%" set "VSWHERE_EXE=%ProgramFiles%\Microsoft Visual Studio\Installer\vswhere.exe"
"%VSWHERE_EXE%" -prerelease %*
endlocal
