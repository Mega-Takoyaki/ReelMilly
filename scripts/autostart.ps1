# Reelmilly を、Windowsへのログオン時に自動で起動するかどうかを切り替える。
# スタートアップフォルダにショートカットを作る/消すだけ(管理者権限は不要)。
#
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Enable         Web UI + ワーカーを自動起動
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Disable
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -EnableRemote   Claude Code の Remote Control(サーバーモード)も自動起動
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -DisableRemote
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1                  (現在の状態を表示)
param(
    [switch]$Enable,
    [switch]$Disable,
    [switch]$EnableRemote,
    [switch]$DisableRemote
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "start-reelmilly.bat"
$remoteLauncher = Join-Path $PSScriptRoot "run-claude-remote.bat"
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "Reelmilly (auto start).lnk"
$remoteShortcutPath = Join-Path $startupDir "Reelmilly Claude Remote (auto start).lnk"

function New-Shortcut($path, $target, $description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $target
    $link.Arguments = "/startup"          # 最小化で起動し、ブラウザは開かない
    $link.WorkingDirectory = $projectRoot
    $link.Description = $description
    $link.WindowStyle = 7                 # 最小化
    $link.Save()
}

if ($Enable) {
    New-Shortcut $shortcutPath $launcher "Reelmilly (Web UI + worker) auto start"
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
elseif ($EnableRemote) {
    New-Shortcut $remoteShortcutPath $remoteLauncher "Claude Code Remote Control (server mode) auto start"
    Write-Output "Enabled: $remoteShortcutPath"
}
elseif ($DisableRemote) {
    if (Test-Path $remoteShortcutPath) {
        Remove-Item $remoteShortcutPath -Force
        Write-Output "Disabled: removed $remoteShortcutPath"
    } else {
        Write-Output "Remote Control auto start is already disabled."
    }
}
else {
    if (Test-Path $shortcutPath) { Write-Output "Auto start: ON  ($shortcutPath)" }
    else { Write-Output "Auto start: OFF" }
    if (Test-Path $remoteShortcutPath) { Write-Output "Remote Control auto start: ON  ($remoteShortcutPath)" }
    else { Write-Output "Remote Control auto start: OFF" }
}
