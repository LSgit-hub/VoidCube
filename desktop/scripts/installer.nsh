; VoidCube installer custom hooks.
; Registers the bundled CLI directory on the user PATH so the `voidcube`
; command is available from any terminal after installation, and cleans it
; up on uninstall.

!include "LogicLib.nsh"
!include "StrFunc.nsh"
${StrRep}

!macro customInstall
  ReadRegStr $0 HKCU "Environment" "Path"
  ${If} $0 == ""
    WriteRegExpandStr HKCU "Environment" "Path" "$INSTDIR\resources\voidcube"
  ${Else}
    WriteRegExpandStr HKCU "Environment" "Path" "$0;$INSTDIR\resources\voidcube"
  ${EndIf}
  SendMessage ${HWND_BROADCAST} ${WM_WININCHANGE} 0 "STR:Environment" /TIMEOUT=5000
!macroend

!macro customUnInstall
  ReadRegStr $0 HKCU "Environment" "Path"
  ${If} $0 != ""
    ${StrRep} $1 $0 ";$INSTDIR\resources\voidcube" ""
    ${StrRep} $2 $1 "$INSTDIR\resources\voidcube;" ""
    ${StrRep} $3 $2 "$INSTDIR\resources\voidcube" ""
    WriteRegExpandStr HKCU "Environment" "Path" "$3"
    SendMessage ${HWND_BROADCAST} ${WM_WININCHANGE} 0 "STR:Environment" /TIMEOUT=5000
  ${EndIf}
!macroend
