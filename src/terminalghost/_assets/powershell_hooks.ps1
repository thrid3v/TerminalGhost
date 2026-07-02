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
#   $env:TG_HOST  — daemon host, default 127.0.0.1
#   $env:TG_PORT  — daemon port. When unset, the port the daemon recorded in
#                   its runtime file (~/.local/share/terminalghost/port) is
#                   used, so a custom or ephemeral (port = 0) config just
#                   works; else 48632.
#   $env:TG_HINTS — set to '1' for a proactive one-line hint after a failure.
#   $env:TG_CAPTURE_OUTPUT — set to '1' to transcribe the session
#       (Start-Transcript) and send each command's output with its event.
#       The daemon stores output only when capture.capture_output = true.
#       Caveat: Windows PowerShell 5.1 transcripts miss most native .exe
#       console output (PowerShell 7+ captures it) — `tgr <cmd>` remains the
#       guaranteed way to capture a native command's output.

if (-not $env:TG_HOST) { $env:TG_HOST = '127.0.0.1' }

function global:Get-TerminalGhostPort {
    # Explicit TG_PORT wins; else the daemon's runtime port file; else 48632.
    if ($env:TG_PORT) { return [int]$env:TG_PORT }
    try {
        $portFile = Join-Path $HOME '.local\share\terminalghost\port'
        return [int]((Get-Content $portFile -Raw -ErrorAction Stop).Trim())
    } catch {
        return 48632
    }
}

$global:__TG_LastHistoryId = (Get-History -Count 1).Id

# --- Optional output capture (EXPERIMENTAL, opt-in) -------------------------
# The PowerShell analog of the POSIX `script` wrapper: transcribe the session
# and tail the delta per command. Silently disabled if a transcript is
# already running (e.g. corporate logging policy).
$global:__TG_Transcript = $null
$global:__TG_TranscriptOffset = 0
if ($env:TG_CAPTURE_OUTPUT -eq '1') {
    $global:__TG_Transcript = Join-Path ([IO.Path]::GetTempPath()) "tg-capture.$PID.log"
    try {
        Start-Transcript -Path $global:__TG_Transcript -Force | Out-Null
        # Skip past the transcript header block.
        $global:__TG_TranscriptOffset = (Get-Item $global:__TG_Transcript -ErrorAction Stop).Length
    } catch {
        $global:__TG_Transcript = $null
    }
}

function global:Get-TerminalGhostOutput {
    # The transcript text appended since the last prompt (= last command's
    # output), with transcript metadata lines stripped; '' when unavailable.
    if (-not $global:__TG_Transcript) { return '' }
    try {
        $fs = [IO.File]::Open($global:__TG_Transcript, 'Open', 'Read', 'ReadWrite')
        try {
            $len = $fs.Length
            if ($len -le $global:__TG_TranscriptOffset) { return '' }
            [void]$fs.Seek($global:__TG_TranscriptOffset, 'Begin')
            $buf = New-Object byte[] ($len - $global:__TG_TranscriptOffset)
            [void]$fs.Read($buf, 0, $buf.Length)
            $global:__TG_TranscriptOffset = $len
        } finally { $fs.Close() }
        $text = [Text.Encoding]::UTF8.GetString($buf)
        $lines = ($text -split "`r?`n") | Where-Object {
            $_ -notmatch '^\*{15,}$' -and
            $_ -notmatch '^(Windows PowerShell transcript|Command start time:|Start time:|End time:|Username:|RunAs User:|Machine:|Host Application:|Process ID:|PSVersion|PSEdition|PSCompatibleVersions|BuildVersion|CLRVersion|WSManStackVersion|PSRemotingProtocolVersion|SerializationVersion|Configuration Name:)'
        }
        $joined = (($lines | Select-Object -Last 40) -join "`n").Trim()
        if ($joined.Length -gt 4000) { $joined = $joined.Substring($joined.Length - 4000) }
        return $joined
    } catch { return '' }
}

function global:Send-TerminalGhostEvent {
    param([string]$Cmd, [int]$ExitCode, [int]$DurationMs, [string]$CmdOutput = '')
    try {
        $token = ''
        try {
            $tokenFile = Join-Path $HOME '.local\share\terminalghost\token'
            $token = (Get-Content $tokenFile -Raw -ErrorAction Stop).Trim()
        } catch { }
        $data = @{
            cmd      = $Cmd
            exit     = [Math]::Max(0, [Math]::Min(255, $ExitCode))
            cwd      = (Get-Location).Path
            duration = [Math]::Max(0, $DurationMs)
            ts       = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
            pid      = $PID
            shell    = 'powershell'
            token    = $token
        }
        if ($CmdOutput) { $data.output = $CmdOutput }
        $payload = ($data | ConvertTo-Json -Compress) + "`n"

        $client = New-Object System.Net.Sockets.TcpClient
        $connect = $client.BeginConnect($env:TG_HOST, (Get-TerminalGhostPort), $null, $null)
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
        $cmdOutput = ''
        if ($global:__TG_Transcript) { $cmdOutput = Get-TerminalGhostOutput }
        Send-TerminalGhostEvent -Cmd $entry.CommandLine -ExitCode $exitCode `
            -DurationMs $duration -CmdOutput $cmdOutput
        # Opt-in proactive hint after a failure (TG_HINTS=1). Blocks briefly;
        # the hint client is silent when the daemon/model has nothing to say.
        if ($env:TG_HINTS -eq '1' -and $exitCode -ne 0) {
            try { terminalghost hint 2>$null } catch { }
        }
    }
    & $global:__TG_OriginalPrompt
}

function global:qq { terminalghost ask @args }

# tgr <cmd> — run a command with its output captured for the next qq/??.
# tga [name] — run the last suggested fix, or a saved one (asks first).
# tgs <name> — save the last suggested fix under a name.
function global:tgr { terminalghost exec @args }
function global:tga { terminalghost apply @args }
function global:tgs { terminalghost save @args }
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
