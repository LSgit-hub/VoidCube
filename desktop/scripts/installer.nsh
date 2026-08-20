; VoidCube installer custom hooks.
; Registers the bundled CLI directory on the user PATH so the `voidcube`
; command is available from any terminal after installation, and cleans it
; up on uninstall.

!include "LogicLib.nsh"
!include "StrFunc.nsh"
!include "WinMessages.nsh"

; The uninstaller StrRep function generates `Function un.StrRep`. Declare it
; ONLY when building the uninstaller (electron-builder's BUILD_UNINSTALLER
; pass); otherwise the main-installer pass would carry uninstaller code
; without a WriteUninstaller call and makensis would fail with warning 6020.
!ifdef BUILD_UNINSTALLER
  ${UnStrRep}
!endif

!macro customInstall
  ReadRegStr $0 HKCU "Environment" "Path"
  ${If} $0 == ""
    WriteRegExpandStr HKCU "Environment" "Path" "$INSTDIR\resources\voidcube"
  ${Else}
    WriteRegExpandStr HKCU "Environment" "Path" "$0;$INSTDIR\resources\voidcube"
  ${EndIf}
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
!macroend

!macro customUnInstall
  ReadRegStr $0 HKCU "Environment" "Path"
  ${If} $0 != ""
    ${UnStrRep} $1 $0 ";$INSTDIR\resources\voidcube" ""
    ${UnStrRep} $2 $1 "$INSTDIR\resources\voidcube;" ""
    ${UnStrRep} $3 $2 "$INSTDIR\resources\voidcube" ""
    WriteRegExpandStr HKCU "Environment" "Path" "$3"
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
  ${EndIf}
!macroend
