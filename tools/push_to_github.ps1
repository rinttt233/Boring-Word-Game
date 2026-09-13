# push_to_github.ps1 -- push this repository (source + version tags) to GitHub.
#
# ASCII-only on purpose: Windows PowerShell 5.1 mis-reads UTF-8 script files.
#
# ---------------------------------------------------------------------------
# If github.com is blocked by a hosts-file entry (this machine had
# "127.0.0.1 github.com #S302"), DNS is redirected to localhost and git/gh
# cannot connect. Workaround WITHOUT touching the system hosts file:
#   1) start the local CONNECT proxy (maps github.com to reachable IPs):
#        python -X utf8 tools\gh_proxy.py --port 8443
#   2) point git/gh at it for the commands:
#        $env:HTTPS_PROXY = "http://127.0.0.1:8443"
#        $env:HTTP_PROXY  = "http://127.0.0.1:8443"
#   3) run this script (TLS stays end-to-end; github.com's cert is still verified)
# ---------------------------------------------------------------------------
#
# Usage (run in YOUR OWN terminal, not inside a sandboxed agent shell):
#   powershell -ExecutionPolicy Bypass -File tools\push_to_github.ps1 -Create
#   powershell -ExecutionPolicy Bypass -File tools\push_to_github.ps1
#   powershell -ExecutionPolicy Bypass -File tools\push_to_github.ps1 -Force
#
# Auth options (pick one):
#   1) gh auth login            (recommended; then just run this script)
#   2) $env:GITHUB_TOKEN = 'ghp_...'   (classic PAT with 'repo' scope)
#   3) git credential manager already has a GitHub login for https://github.com
#
param(
    [string]$Repo = "rinttt233/Boring-Word-Game",
    [string]$Branch = "main",
    [switch]$Create,   # create the remote repo via gh if it does not exist
    [switch]$Force     # allow non-fast-forward (--force-with-lease)
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$url = "https://github.com/$Repo.git"

Write-Output "[push] repo   : $Repo"
Write-Output "[push] branch : $Branch"
Write-Output ("[push] tags   : " + ((git tag -l) -join ", "))

# --- auth check ------------------------------------------------------------
$token = $env:GITHUB_TOKEN
if ($token) {
    Write-Output "[push] auth   : GITHUB_TOKEN (one-shot URL, not stored in .git/config)"
} else {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        gh auth status 2>&1 | Out-String | Write-Output
    } else {
        Write-Output "[push] gh CLI not found; will rely on git credential manager."
    }
}

# --- remote ----------------------------------------------------------------
$existing = (git remote 2>$null)
if ($existing -contains "origin") {
    Write-Output "[push] remote origin already configured, keeping it."
} else {
    git remote add origin $url
    Write-Output "[push] remote origin added: $url"
}

if ($Create) {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) { throw "gh CLI is required for -Create" }
    Write-Output "[push] gh repo create (skip if it already exists)"
    gh repo create $Repo --public --source=. --remote=origin 2>&1 | Write-Output
}

# --- push (with retry: the proxied path can hit transient TLS resets) --------
$pushTarget = if ($token) { "https://x-access-token:$token@github.com/$Repo.git" } else { "origin" }
$forceArg = @()
if ($Force) { $forceArg = @("--force-with-lease") }

function Invoke-Push([string[]]$args2) {
    for ($i = 1; $i -le 4; $i++) {
        Write-Output ("[push] attempt " + $i + "/4: git push " + ($args2 -join " "))
        git push $pushTarget @args2
        if ($LASTEXITCODE -eq 0) { return $true }
        Write-Output "[push] failed (exit $LASTEXITCODE); retrying in 2s ..."
        Start-Sleep -Seconds 2
    }
    return $false
}

Write-Output "[push] pushing branch $Branch ..."
$ok1 = Invoke-Push (@("${Branch}:${Branch}") + $forceArg)
Write-Output "[push] pushing tags ..."
$ok2 = Invoke-Push (@("--tags") + $forceArg)
if (-not ($ok1 -and $ok2)) { throw "git push failed after retries" }

Write-Output "[push] done. Now open: https://github.com/$Repo/releases"
Write-Output "[push] tip: attach releases/v<version>/ zips as GitHub Release assets."
