; Uninstall hooks for the VoxVault installer (design, decision 10).
;
; Only outside the update mode: an update replaces the installation folder and
; must leave the prepared environment, the model and the start with Windows
; exactly as they were.
;
; Before removing anything, the running service is asked whether it is
; recording -- a recording in progress refuses the uninstall -- and where its
; data lives, which is then named at the end: the data folder is never touched.
; Afterwards the service is stopped and %USERPROFILE%\.voxvault goes, with the
; prepared environment in it. The Run value is removed by Tauri's own template.

Var VoxExe
Var VoxDados

; Position of AGULHA in TEXTO, or -1.
!macro VOX_PROCURAR TEXTO AGULHA SAIDA
  Push `${TEXTO}`
  Push `${AGULHA}`
  Call un.VoxProcurar
  Pop ${SAIDA}
!macroend

; Every register it touches comes back as it was: the caller's $R0 and $R1
; are often exactly the text it is searching in.
Function un.VoxProcurar
  Exch $R1
  Exch
  Exch $R0
  Push $R2
  Push $R3
  Push $R4
  StrLen $R3 $R1
  StrCpy $R2 0
  vox_procurar_laco:
    StrCpy $R4 $R0 $R3 $R2
    StrCmp $R4 "" vox_procurar_nada
    StrCmp $R4 $R1 vox_procurar_fim
    IntOp $R2 $R2 + 1
    Goto vox_procurar_laco
  vox_procurar_nada:
    StrCpy $R2 -1
  vox_procurar_fim:
  ; Stack: old R1, old R0, old R2, old R3, old R4; the answer is in $R2.
  Pop $R4
  Pop $R3
  Exch $R2
  Exch
  Pop $R0
  Exch
  Pop $R1
FunctionEnd

; The value of "diretorio_de_dados" in the status line, with the JSON escapes
; of the backslashes undone. Empty when it is not there.
Function un.VoxPastaDeDados
  Exch $R0
  Push $R1
  Push $R2
  Push $R3
  Push $R4
  !insertmacro VOX_PROCURAR $R0 '"diretorio_de_dados": "' $R1
  StrCmp $R1 -1 0 +3
    StrCpy $R0 ""
    Goto vox_pasta_fim
  IntOp $R1 $R1 + 23
  StrCpy $R0 $R0 "" $R1
  StrCpy $R2 ""
  StrCpy $R3 0
  vox_pasta_laco:
    StrCpy $R4 $R0 1 $R3
    StrCmp $R4 "" vox_pasta_pronto
    StrCmp $R4 '"' vox_pasta_pronto
    StrCmp $R4 "\" 0 vox_pasta_copia
      IntOp $R3 $R3 + 1
      StrCpy $R4 $R0 1 $R3
    vox_pasta_copia:
    StrCpy $R2 "$R2$R4"
    IntOp $R3 $R3 + 1
    Goto vox_pasta_laco
  vox_pasta_pronto:
  StrCpy $R0 $R2
  vox_pasta_fim:
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R1
  Exch $R0
FunctionEnd

!macro NSIS_HOOK_PREUNINSTALL
  StrCpy $VoxDados ""
  StrCpy $VoxExe "$PROFILE\.voxvault\runtime\ambiente\Scripts\voxvault.exe"
  ${If} $UpdateMode <> 1
  ${AndIf} ${FileExists} "$VoxExe"
    nsExec::ExecToStack '"$VoxExe" serve --status --json'
    Pop $0
    Pop $1
    !insertmacro VOX_PROCURAR $1 '"gravacao_ativa": true' $2
    ${If} $2 >= 0
      MessageBox MB_OK|MB_ICONSTOP "Há uma gravação em andamento no VoxVault. Encerre-a antes de desinstalar." /SD IDOK
      Abort "Desinstalação recusada: há uma gravação em andamento."
    ${EndIf}
    Push $1
    Call un.VoxPastaDeDados
    Pop $VoxDados
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    ${If} ${FileExists} "$VoxExe"
      ; A pending queue does not hold the uninstall back: it stays in the data
      ; folder and resumes if VoxVault is installed again.
      nsExec::Exec '"$VoxExe" serve --stop --force'
      Pop $0
      Sleep 1500
    ${EndIf}
    ; Never remove a data folder someone put inside the state folder.
    !insertmacro VOX_PROCURAR "$VoxDados" "$PROFILE\.voxvault" $2
    ${If} $2 <> 0
      RMDir /r /REBOOTOK "$PROFILE\.voxvault"
    ${EndIf}
    ; The address a notification's click opens, which the app registers.
    DeleteRegKey HKCU "Software\Classes\voxvault"
    ; The embedded browser's cache: nothing of the user's is in it.
    RMDir /r "$LOCALAPPDATA\${BUNDLEID}"
    RMDir /r "$INSTDIR\${MAINBINARYNAME}.exe.WebView2"
    RMDir "$INSTDIR"
    ${If} $VoxDados != ""
      MessageBox MB_OK|MB_ICONINFORMATION "O VoxVault foi removido. Suas gravações foram mantidas em $VoxDados." /SD IDOK
    ${Else}
      MessageBox MB_OK|MB_ICONINFORMATION "O VoxVault foi removido. Suas gravações foram mantidas na pasta de dados escolhida no preparo." /SD IDOK
    ${EndIf}
  ${EndIf}
!macroend
