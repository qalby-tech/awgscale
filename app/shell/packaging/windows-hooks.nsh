; The installer's own steps (Tauri's NSIS hooks): the VPN service lives in the
; install directory under Program Files, where only administrators may write.
; Before files are replaced the old service is taken away (it would hold them);
; after they are in place it is registered and started; the uninstaller takes it away.

!macro NSIS_HOOK_PREINSTALL
  IfFileExists "$INSTDIR\qalby.exe" 0 +2
    nsExec::Exec '"$INSTDIR\qalby.exe" service remove'
!macroend

!macro NSIS_HOOK_POSTINSTALL
  nsExec::ExecToLog '"$INSTDIR\qalby.exe" service install'
  Pop $0
  StrCmp $0 "0" +2
    MessageBox MB_ICONEXCLAMATION|MB_OK "Служба VPN awgscale не запустилась (код $0). Приложение установлено, но включить VPN не получится." /SD IDOK
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec '"$INSTDIR\qalby.exe" service remove'
!macroend
