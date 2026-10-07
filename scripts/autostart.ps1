# Reelmilly を、Windowsへのログオン時に自動で起動するかどうかを切り替える。
# スタートアップフォルダにショートカットを作る/消すだけ(管理者権限は不要)。
#
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Enable
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Disable
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1          (現在の状態を表示)
param(
    [switch]$Enable,
    [switch]$Disable
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "start-reelmilly.bat"
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "Reelmilly (auto start).lnk"

if ($Enable) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcutPath)
    $link.TargetPath = $launcher
    $link.Arguments = "/startup"          # 最小化で起動し、ブラウザは開かない
    $link.WorkingDirectory = $projectRoot
    $link.Description = "Reelmilly (Web UI + worker) auto start"
    $link.WindowStyle = 7                 # 最小化
    $link.Save()
    Write-Output "Enabled: $shortcutPath"
}
elseif ($Disable) {
    if (Test-Path $shortcutPath) {
        Remove-Item $shortcutPath -Force
        Write-Output "Disabled: removed $shortcutPath"
    } else {
        Write-Output "Already disabled."
    }
}
else {
    if (Test-Path $shortcutPath) { Write-Output "Auto start: ON  ($shortcutPath)" }
    else { Write-Output "Auto start: OFF" }
}
