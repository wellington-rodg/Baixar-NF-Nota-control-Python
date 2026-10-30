@echo off
REM Abre um Chrome normal (sem flag de automacao) com porta de depuracao 9222.
REM Perfil proprio: o login feito aqui fica salvo para as proximas vezes.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%USERPROFILE%\chrome-automacao" ^
  "https://www.notaeletronica.com.br/goiania/Login/Login_NFE.aspx"
