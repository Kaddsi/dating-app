$ErrorActionPreference = 'Stop'

Write-Host 'Syncing mini app files...' -ForegroundColor Cyan
Copy-Item -Path 'd:/dating/premium-dating-app.html' -Destination 'd:/dating/webapp-deploy/index.html' -Force

$envFile = 'd:/dating/.env'
if (Test-Path $envFile) {
    $content = Get-Content -Path $envFile -Raw
    $version = Get-Date -Format 'yyyy-MM-dd-HHmmss'
    $updated = [regex]::Replace($content, '^WEB_APP_VERSION=.*$', "WEB_APP_VERSION=$version", 'Multiline')
    Set-Content -Path $envFile -Value $updated -NoNewline
    Write-Host "Updated WEB_APP_VERSION to $version" -ForegroundColor Green
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host 'Next:'
Write-Host '1) Commit and push changes to GitHub (Render auto-deploy will start)'
Write-Host '2) Restart local bot: python d:/dating/bot/main.py'
