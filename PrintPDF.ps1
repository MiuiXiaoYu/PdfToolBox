<#
PrintPDF.ps1  -  batch print PDF files on Windows, no subscription needed

Engines
  default      SumatraPDF.exe, silent, one process per file (free, open source)
  -UseShell    falls back to the default PDF app's "Print" verb
               (a print dialog pops up for every file)

Examples
  .\PrintPDF.ps1
  .\PrintPDF.ps1 -Folder "D:\invoices" -Printer "HP LaserJet M404"
  .\PrintPDF.ps1 -Folder "D:\invoices" -Recurse -Copies 2
  .\PrintPDF.ps1 -Folder "D:\invoices" -Settings "1-3,5"
  .\PrintPDF.ps1 -Folder "D:\invoices" -DryRun
  .\PrintPDF.ps1 -Folder "D:\invoices" -UseShell -DelaySeconds 6

Portable SumatraPDF (not on PATH, version in the file name):
  Either pass -SumatraPath "C:\path\to\SumatraPDF.exe"
  or write "sumatra_path=C:\path\to\SumatraPDF.exe" into config.ini next to
  this script (copy config.example as config.ini first).

Notes
  * Every message in this file is ASCII on purpose: a zh-CN console runs on
    codepage 936 and would garble non-ASCII text saved as UTF-8 without BOM.
  * Do not put non-ASCII characters into this file if you edit it.
  * DryRun never talks to a printer, it only lists what would happen.
#>

[CmdletBinding()]
param(
    [string]$Folder      = "",
    [string]$Filter      = "*.pdf",
    [string]$Printer     = "",
    [string]$SumatraPath = "",
    [string]$Settings    = "",
    [int]   $Copies      = 0,
    [int]   $DelaySeconds = 2,
    [switch]$Recurse,
    [switch]$UseShell,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

# Colors only when a real console is attached. Write-Host -ForegroundColor
# emits ANSI escapes when the output is piped or redirected, which shows up as
# junk characters in log files.
$script:UseColor = -not [Console]::IsOutputRedirected

function ColorLine {
    param([string]$m, [string]$c)
    if ($script:UseColor) { Write-Host $m -ForegroundColor $c } else { Write-Host $m }
}
function Say  { param($m) ColorLine "[i] $m" 'Cyan' }
function Good { param($m) ColorLine "[+] $m" 'Green' }
function Warn { param($m) ColorLine "[!] $m" 'Yellow' }
function Bad  { param($m) ColorLine "[x] $m" 'Red' }

# Readable reason for a SumatraPDF print exit code. The granular codes are
# documented for 3.7; the 3.6.x line reports plain 1 for every failure, so 1 is
# labelled generically and any non-zero code is treated as a failure.
function Fail-Reason {
    param([int]$code)
    switch ($code) {
        1 { 'print failed (file, printer, driver or permission)' }
        2 { 'file could not be opened' }
        3 { 'document does not allow printing' }
        4 { 'printer was not found' }
        5 { 'printer driver failed' }
        6 { 'printing disabled by policy' }
        default { "exit code $code" }
    }
}

function Find-Sumatra {
    param([string]$Hint)

    if ($Hint) {
        if (Test-Path -LiteralPath $Hint) { return (Resolve-Path -LiteralPath $Hint).Path }
        Warn "SumatraPath not found: $Hint"
    }

    $cmd = Get-Command 'SumatraPDF.exe' -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @()
    if ($env:LOCALAPPDATA)  { $candidates += (Join-Path $env:LOCALAPPDATA  'SumatraPDF\SumatraPDF.exe') }
    if (${env:ProgramFiles}) { $candidates += (Join-Path ${env:ProgramFiles} 'SumatraPDF\SumatraPDF.exe') }
    if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'SumatraPDF\SumatraPDF.exe') }

    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }

    $wingetRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $wingetRoot) {
        $hit = Get-ChildItem -LiteralPath $wingetRoot -Filter 'SumatraPDF.exe' -File -Recurse -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }

    return $null
}

# ---------------------------------------------------------------- target folder
if ([string]::IsNullOrWhiteSpace($Folder)) {
    Write-Host ""
    ColorLine "Batch print PDFs" 'White'
    Write-Host "Press Enter to use this script's own folder."
    $Folder = Read-Host "Folder path"
    if ([string]::IsNullOrWhiteSpace($Folder)) { $Folder = $PSScriptRoot }
}

if (-not (Test-Path -LiteralPath $Folder)) {
    Bad "Folder not found: $Folder"
    exit 1
}
$Folder = (Resolve-Path -LiteralPath $Folder).Path
Say "Folder : $Folder"

# ---------------------------------------------------------------- collect files
if ($Recurse) {
    $files = @(Get-ChildItem -LiteralPath $Folder -Filter $Filter -File -Recurse -ErrorAction SilentlyContinue)
} else {
    $files = @(Get-ChildItem -LiteralPath $Folder -Filter $Filter -File -ErrorAction SilentlyContinue)
}

$files = @($files | Sort-Object FullName)

if ($files.Count -eq 0) {
    Bad "No '$Filter' found in this folder."
    exit 2
}

$empty = @($files | Where-Object { $_.Length -eq 0 })
if ($empty.Count -gt 0) {
    Warn ("Skipping " + $empty.Count + " empty file(s).")
    $files = @($files | Where-Object { $_.Length -gt 0 })
}
if ($files.Count -eq 0) {
    Bad "Nothing left to print."
    exit 2
}
Say "Found  : $($files.Count) file(s)"

# ---------------------------------------------------------------- target printer
$printers = @(Get-CimInstance Win32_Printer -ErrorAction SilentlyContinue)
if ([string]::IsNullOrWhiteSpace($Printer)) {
    $def = $printers | Where-Object { $_.Default } | Select-Object -First 1
    if ($def) {
        $Printer = $def.Name
    } else {
        Bad "No default printer configured. Use -Printer ""exact name""."
        exit 3
    }
}
if ($printers.Count -gt 0 -and -not ($printers | Where-Object { $_.Name -eq $Printer })) {
    Warn "Printer '$Printer' is not in the system printer list, continuing anyway."
}
Say "Printer: $Printer"

if ($Printer -match 'PDF|OneNote|XPS|Fax') {
    Warn "This looks like a virtual printer: every file will ask for a save path."
}

# ---------------------------------------------------------------- engine
# A portable SumatraPDF build is not on PATH and its file name carries the
# version, so allow a one-time path to be stored in SumatraPath.txt next to
# this script. One line, no quotes needed.
if ([string]::IsNullOrWhiteSpace($SumatraPath)) {
    # 1) unified config file config.ini  (sumatra_path=...)
    $cfgFile = Join-Path $PSScriptRoot 'config.ini'
    if (Test-Path -LiteralPath $cfgFile) {
        $hit = Get-Content -LiteralPath $cfgFile -Encoding UTF8 -ErrorAction SilentlyContinue |
               Where-Object { $_.Trim() -notmatch '^[#;]' -and $_.IndexOf('=') -gt 0 } |
               ForEach-Object {
                   $i = $_.IndexOf('=')
                   $k = $_.Substring(0, $i).Trim()
                   $v = $_.Substring($i + 1).Trim().Trim('"').Trim("'")
                   if ($k -ieq 'sumatra_path' -and $v) { $v }
               } | Select-Object -First 1
        if ($hit) {
            $SumatraPath = $hit
            Say "Config : config.ini -> $SumatraPath"
        }
    }
    # 2) legacy single-line config SumatraPath.txt (backward compatible)
    if ([string]::IsNullOrWhiteSpace($SumatraPath)) {
        $cfg = Join-Path $PSScriptRoot 'SumatraPath.txt'
        if (Test-Path -LiteralPath $cfg) {
            $line = Get-Content -LiteralPath $cfg -ErrorAction SilentlyContinue |
                    Where-Object { $_.Trim() -ne '' } | Select-Object -First 1
            if ($line) {
                $SumatraPath = $line.Trim().Trim('"')
                Say "Config : SumatraPath.txt -> $SumatraPath"
            }
        }
    }
}

$sumatra = $null
if (-not $UseShell) {
    $sumatra = Find-Sumatra -Hint $SumatraPath
    if (-not $sumatra -and -not $DryRun) {
        Bad "SumatraPDF not found."
        ColorLine "    Install it (free): https://www.sumatrapdfreader.org/download-free-pdf-viewer" 'Gray'
        ColorLine "    Or run again with -UseShell to use the default PDF app instead." 'Gray'
        exit 4
    }
    if ($sumatra) { Say "Engine : SumatraPDF  $sumatra" }
} else {
    Say "Engine : shell Print verb (a dialog per file)"
}

# copies / page range -> Sumatra print-settings
# Items are comma separated: page ranges, then advanced options. "3x" on its
# own means 3 copies, so copies go in as a separate item: "1-3,5,2x".
$settingsStr = $Settings
if ($Copies -gt 0) {
    if ([string]::IsNullOrWhiteSpace($settingsStr)) {
        $settingsStr = "$Copies" + 'x'
    } else {
        $settingsStr = $settingsStr + ',' + $Copies + 'x'
    }
}
if ($settingsStr -and -not $UseShell) { Say "Settings: $settingsStr" }

# ---------------------------------------------------------------- confirm
if ($files.Count -gt 20 -and -not $Yes -and -not $DryRun) {
    $ans = Read-Host ("About to print " + $files.Count + " files, continue? [y/N]")
    if ($ans -notmatch '^(y|Y)') { Write-Host "Cancelled."; exit 0 }
}

# ---------------------------------------------------------------- run
$ok = 0
$fail = 0
$failed = @()
$index = 0

foreach ($f in $files) {
    $index++
    $tag = "[$index/$($files.Count)]"
    Write-Host ("$tag " + $f.Name)

    if ($DryRun) { continue }

    try {
        if ($UseShell) {
            Start-Process -FilePath $f.FullName -Verb Print | Out-Null
            if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }
            $ok++
        } else {
            $argString = '-print-to "' + $Printer + '" -silent'
            if ($settingsStr) { $argString += ' -print-settings "' + $settingsStr + '"' }
            $argString += ' "' + $f.FullName + '"'

            $proc = Start-Process -FilePath $sumatra -ArgumentList $argString -Wait -PassThru -WindowStyle Hidden
            if ($proc.ExitCode -eq 0) {
                $ok++
            } else {
                $fail++
                $failed += $f.FullName
                Warn ($f.Name + " -> " + (Fail-Reason $proc.ExitCode))
            }
            if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }
        }
    } catch {
        $fail++
        $failed += $f.FullName
        Bad ($f.Name + " -> " + $_.Exception.Message)
    }
}

# ---------------------------------------------------------------- summary + log
Write-Host ""
if ($DryRun) {
    Good ("DryRun: " + $files.Count + " file(s) would be printed to '" + $Printer + "'.")
} else {
    Good ("Done. ok=" + $ok + " failed=" + $fail)
    if ($failed.Count -gt 0) {
        Warn "Failed files:"
        $failed | ForEach-Object { ColorLine "    $_" 'Gray' }
    }
}

try {
    $logPath = Join-Path $Folder ("PrintLog_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".txt")
    $lines = @()
    $lines += "time    : " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    $lines += "folder  : " + $Folder
    $lines += "printer : " + $Printer
    $lines += "engine  : " + $(if ($UseShell) { 'shell-print' } else { 'sumatra' })
    $lines += "total   : " + $files.Count
    $lines += "ok      : " + $ok
    $lines += "failed  : " + $fail
    $lines += ""
    foreach ($f in $files) { $lines += $f.FullName }
    if ($failed.Count -gt 0) {
        $lines += ""
        $lines += "FAILED:"
        foreach ($f in $failed) { $lines += $f }
    }
    Set-Content -LiteralPath $logPath -Value $lines -Encoding UTF8
    Say "Log    : $logPath"
} catch {
    Warn "Could not write the log file: $($_.Exception.Message)"
}

if ($fail -gt 0) { exit 5 }
exit 0
