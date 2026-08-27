<#
.SYNOPSIS
  Send a Telegram message from the VPS with zero Python dependencies.

.DESCRIPTION
  Every daemon in this folder reports through here, so notifications survive a
  broken venv, a dead opencode, or a half-migrated repo. Token and chat id are
  read from (in order): explicit parameters, process environment, the milo
  state .env, then milo-bot\.env.

  Long bodies are chunked to Telegram's 4096-character limit. Delivery failures
  are logged, never thrown: a failed notification must not fail the pipeline
  that was trying to report success.

.EXAMPLE
  .\Send-Telegram.ps1 -Text "shorts sweep ok"
  Get-Content big.log -Tail 50 | .\Send-Telegram.ps1
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromPipeline = $true, Position = 0)][string[]]$Text,
    [string]$Token = '',
    [string]$ChatId = '',
    [switch]$Silent
)

begin { $buffer = New-Object System.Collections.Generic.List[string] }
process { if ($Text) { foreach ($line in $Text) { $buffer.Add([string]$line) } } }

end {
    $ErrorActionPreference = 'Continue'

    function Get-EnvFileValue([string]$Path, [string]$Key) {
        if (-not (Test-Path $Path)) { return '' }
        $line = Select-String -Path $Path -Pattern "^\s*$Key\s*=" -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if (-not $line) { return '' }
        return ($line.Line -replace "^\s*$Key\s*=", '').Trim().Trim('"').Trim("'")
    }

    $repo  = if ($env:MILO_REPO) { $env:MILO_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
    $state = if ($env:MILO_HOME) { $env:MILO_HOME } else { Join-Path $env:LOCALAPPDATA 'milo' }
    $envFiles = @((Join-Path $state '.env'), (Join-Path $repo 'milo-bot\.env'), (Join-Path $repo '.env'))

    if (-not $Token)  { $Token  = $env:TELEGRAM_BOT_TOKEN }
    if (-not $ChatId) { $ChatId = $env:TELEGRAM_CHAT_ID }
    foreach ($f in $envFiles) {
        if (-not $Token)  { $Token  = Get-EnvFileValue $f 'TELEGRAM_BOT_TOKEN' }
        if (-not $ChatId) { $ChatId = Get-EnvFileValue $f 'TELEGRAM_CHAT_ID' }
    }

    $body = ($buffer -join "`n").Trim()
    if (-not $body) { return }

    if (-not $Token -or -not $ChatId) {
        Write-Warning 'Send-Telegram: no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID found; falling back to log only'
        $fallback = Join-Path $state 'logs\channels.log'
        New-Item -ItemType Directory -Force -Path (Split-Path $fallback) | Out-Null
        Add-Content -Path $fallback -Value "$(Get-Date -Format s) [undelivered] $body"
        return
    }

    # Telegram hard-caps a message at 4096 characters. Split on line boundaries
    # so log tails stay readable instead of being cut mid-line.
    $chunks = New-Object System.Collections.Generic.List[string]
    $current = ''
    foreach ($line in ($body -split "`r?`n")) {
        if (($current.Length + $line.Length + 1) -gt 3800) {
            if ($current) { $chunks.Add($current) }
            $current = $line
        } else {
            $current = if ($current) { "$current`n$line" } else { $line }
        }
    }
    if ($current) { $chunks.Add($current) }

    $uri = "https://api.telegram.org/bot$Token/sendMessage"
    foreach ($chunk in $chunks) {
        $payload = @{
            chat_id                  = $ChatId
            text                     = $chunk
            disable_web_page_preview = $true
            disable_notification     = [bool]$Silent
        } | ConvertTo-Json -Compress
        try {
            Invoke-RestMethod -Uri $uri -Method Post -ContentType 'application/json; charset=utf-8' `
                -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 25 | Out-Null
        } catch {
            Write-Warning "Send-Telegram: delivery failed - $($_.Exception.Message)"
            $fallback = Join-Path $state 'logs\channels.log'
            New-Item -ItemType Directory -Force -Path (Split-Path $fallback) | Out-Null
            Add-Content -Path $fallback -Value "$(Get-Date -Format s) [failed] $chunk"
        }
        Start-Sleep -Milliseconds 350   # stay under Telegram's per-chat rate limit
    }
}
