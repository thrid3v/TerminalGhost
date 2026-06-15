# scripts/powershell_hooks.ps1 — TerminalGhost shell integration for PowerShell.
#
# Dot-source this file from your $PROFILE:
#   . C:\path\to\terminalghost\scripts\powershell_hooks.ps1
#
# What this installs:
#   1. A wrapped `prompt` function — after each command it reads the last
#      Get-History entry (command text, start/end time) and the success state,
#      and sends a JSON event to the daemon over TCP loopback. Fire-and-forget
#      with a short timeout; silently a no-op when the daemon is down.
#   2. `qq` (and `??` where the parser allows it) — runs `terminalghost ask`,
#      which streams the LLM answer back to this terminal.
#
# Configuration (set before dot-sourcing):
#   $env:TG_HOST — daemon host, default 127.0.0.1
#   $env:TG_PORT — daemon port, default 48632

if (-not $env:TG_HOST) { $env:TG_HOST = '127.0.0.1' }
if (-not $env:TG_PORT) { $env:TG_PORT = '48632' }

$global:__TG_LastHistoryId = (Get-History -Count 1).Id

function global:Send-TerminalGhostEvent {
    param([string]$Cmd, [int]$ExitCode, [int]$DurationMs)
    try {
        $token = ''
        try {
            $tokenFile = Join-Path $HOME '.local\share\terminalghost\token'
            $token = (Get-Content $tokenFile -Raw -ErrorAction Stop).Trim()
        } catch { }
        $payload = (@{
            cmd      = $Cmd
            exit     = [Math]::Max(0, [Math]::Min(255, $ExitCode))
            cwd      = (Get-Location).Path
            duration = [Math]::Max(0, $DurationMs)
            ts       = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
            pid      = $PID
            shell    = 'powershell'
            token    = $token
        } | ConvertTo-Json -Compress) + "`n"

        $client = New-Object System.Net.Sockets.TcpClient
        $connect = $client.BeginConnect($env:TG_HOST, [int]$env:TG_PORT, $null, $null)
        if ($connect.AsyncWaitHandle.WaitOne(200)) {
            $client.EndConnect($connect)
            $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
            $client.GetStream().Write($bytes, 0, $bytes.Length)
        }
        $client.Close()
    } catch {
        # daemon not running — never bother the shell about it
    }
}

# Wrap the existing prompt so customized prompts keep working.
$global:__TG_OriginalPrompt = $function:prompt

function global:prompt {
    $lastSuccess = $global:?          # must be read before anything else runs
    $entry = Get-History -Count 1
    if ($entry -and $entry.Id -ne $global:__TG_LastHistoryId) {
        $global:__TG_LastHistoryId = $entry.Id
        $exitCode = 0
        if (-not $lastSuccess) {
            $exitCode = if ($global:LASTEXITCODE) { $global:LASTEXITCODE } else { 1 }
        }
        $duration = [int]($entry.EndExecutionTime - $entry.StartExecutionTime).TotalMilliseconds
        Send-TerminalGhostEvent -Cmd $entry.CommandLine -ExitCode $exitCode -DurationMs $duration
    }
    & $global:__TG_OriginalPrompt
}

function global:qq { terminalghost ask @args }

# tgr <cmd> — run a command with its output captured for the next qq/??.
# tga — run the command TerminalGhost last suggested (asks first).
function global:tgr { terminalghost exec @args }
function global:tga { terminalghost apply @args }
# tg: ask normally, but explain piped input (e.g. `make 2>&1 | tg`).
function global:tg {
  if ([Console]::IsInputRedirected) { $input | terminalghost explain } else { terminalghost ask @args }
}

# `??` works as a function name in Windows PowerShell 5.1; in PowerShell 7+
# it collides with the null-coalescing operator, so it is defined only where
# it parses (use `qq` otherwise).
if ($PSVersionTable.PSVersion.Major -lt 7) {
    Invoke-Expression 'function global:?? { terminalghost ask @args }'
}
