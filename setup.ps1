<#
.SYNOPSIS
    Idempotent installer for NotebookLmTools (design.md 10.1).

.DESCRIPTION
    The sole install path -- nothing in this project is ever installed ad hoc -- and also
    the transfer mechanism to a new Windows machine. Every step is a no-op when already
    satisfied, so it is safe to re-run at any time.

        1. locate a Python >= 3.11 (validated, not assumed)
        2. create .venv if absent
        3. install pinned dependencies into it
        4. install the project and notebooklm-mcp-cli into it
        5. fetch a pinned rclone.exe into tools\ and verify its checksum
        6. configure the rclone Drive remote, with its config encrypted and the
           password stored in Windows Credential Manager
        7. log in to NotebookLM
        8. run nlmt doctor

    Steps 6 and 7 are the only interactive ones, and only on a first run or after
    credentials expire.

.PARAMETER SkipLogins
    Do everything except the two interactive logins. Useful in CI or when you only want
    to refresh the Python environment.

.PARAMETER RcloneVersion
    Which rclone to install. Defaults to the version recorded in tools\rclone.lock.json
    if present -- that is what makes the install reproducible -- otherwise "current",
    whose resolved version and hash are then written to that lock file.

.PARAMETER PythonExe
    Full path to the python.exe to build the virtual environment with. Overrides every
    other candidate. Use it when detection picks the wrong interpreter, or picks none.

.PARAMETER Force
    Rebuild the virtual environment from scratch.

.EXAMPLE
    .\setup.ps1
    First-time setup, or a re-run to repair the environment.

.EXAMPLE
    .\setup.ps1 -SkipLogins
    Refresh the Python environment without touching credentials.
#>
[CmdletBinding()]
param(
    [switch]$SkipLogins,
    [string]$RcloneVersion,
    [string]$PythonExe,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Windows PowerShell 5.1 -- the shell `powershell -f setup.ps1` runs -- does not enable
# TLS 1.2 by default, which makes every HTTPS download here fail with an unhelpful
# "underlying connection was closed". Harmless on PowerShell 7.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

$Root       = $PSScriptRoot
$VenvDir    = Join-Path $Root '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$ToolsDir   = Join-Path $Root 'tools'
$RcloneExe  = Join-Path $ToolsDir 'rclone.exe'
$RcloneLock = Join-Path $ToolsDir 'rclone.lock.json'
$LockFile   = Join-Path $Root 'requirements.lock'
$RemoteName = 'nlmtools'
$KeyService = 'NotebookLmTools'
$KeyAccount = 'rclone-config-password'

function Write-Step  { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "    ok: $m" -ForegroundColor Green }
function Write-Note  { param($m) Write-Host "    $m" -ForegroundColor DarkGray }
function Write-Warn2 { param($m) Write-Host "    warning: $m" -ForegroundColor Yellow }
function Fail        { param($m, $fix) Write-Host "`nFAILED: $m" -ForegroundColor Red
                       if ($fix) { Write-Host "     -> $fix" -ForegroundColor Yellow }
                       exit 1 }

# -- 1. Python ------------------------------------------------------------------------

$script:PythonProbeLog = @()

function Get-PythonCandidates {
    <#
        Look in four places, because PATH alone is not reliable: it differs between an
        elevated prompt, a fresh shell, and whatever launched this script, and on this
        class of machine it often surfaces the Microsoft Store alias instead of a real
        installation.
    #>
    $candidates = @()

    # 0. An explicit path always wins, so the operator can end any argument about this.
    if ($PythonExe) {
        $candidates += @{ Exe = $PythonExe; Args = @(); Why = '-PythonExe' }
    }

    # 1. The py launcher, which knows about every registered installation.
    foreach ($minor in 13, 12, 11) {
        $candidates += @{ Exe = 'py'; Args = @("-3.$minor"); Why = 'py launcher' }
    }

    # 2. Whatever PATH offers.
    foreach ($name in 'python', 'python3') {
        $candidates += @{ Exe = $name; Args = @(); Why = 'PATH' }
    }

    # 3. The registry, where every proper installer records itself.
    foreach ($hive in 'HKLM:\SOFTWARE\Python\PythonCore',
                      'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore',
                      'HKCU:\SOFTWARE\Python\PythonCore') {
        if (-not (Test-Path $hive)) { continue }
        foreach ($key in Get-ChildItem $hive -ErrorAction SilentlyContinue) {
            $installPath = Join-Path $key.PSPath 'InstallPath'
            if (-not (Test-Path $installPath)) { continue }
            try {
                $dir = (Get-ItemProperty -Path $installPath -ErrorAction Stop).'(default)'
                if ($dir) {
                    $exe = Join-Path $dir 'python.exe'
                    if (Test-Path $exe) {
                        $candidates += @{ Exe = $exe; Args = @(); Why = "registry $($key.PSChildName)" }
                    }
                }
            } catch { continue }
        }
    }

    # 4. The usual installation directories, in case the registry entry is missing.
    $roots = @('C:\', $env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)}) |
             Where-Object { $_ }
    foreach ($root in $roots) {
        foreach ($pattern in 'Python3*', 'Programs\Python\Python3*') {
            $globbed = Join-Path $root $pattern
            foreach ($dir in (Get-ChildItem -Path $globbed -Directory -ErrorAction SilentlyContinue)) {
                $exe = Join-Path $dir.FullName 'python.exe'
                if (Test-Path $exe) {
                    $candidates += @{ Exe = $exe; Args = @(); Why = 'well-known location' }
                }
            }
        }
    }
    return $candidates
}

function Find-Python {
    # Do not assume a location: this must work on a machine that installed Python
    # somewhere else entirely, and from any shell.
    $candidates = Get-PythonCandidates

    $found = @()
    # Windows PowerShell 5.1 mangles native arguments that contain quotes or spaces, so
    # `python -c "<code>"` arrives as a syntax error rather than as code. Writing the
    # probe to a file sidesteps native argument quoting entirely, and behaves the same
    # under 5.1 and 7.
    $probeScript = Join-Path ([System.IO.Path]::GetTempPath()) "nlmt-python-probe-$PID.py"
    @(
        'import sys'
        'print(sys.version_info[0])'
        'print(sys.version_info[1])'
        'print(sys.executable)'
    ) | Set-Content -Path $probeScript -Encoding ascii

    try {

    $seen = @{}
    foreach ($candidate in $candidates) {
        $label = (@($candidate.Exe) + @($candidate.Args)) -join ' '
        if ($seen.ContainsKey($label)) { continue }
        $seen[$label] = $true

        if (-not (Test-Path $candidate.Exe)) {
            $command = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
            if (-not $command) {
                $script:PythonProbeLog += "  $label  ($($candidate.Why)): not found"
                continue
            }
        }
        try {
            $probe = @($candidate.Args) + @($probeScript)
            $output = & $candidate.Exe @probe 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $output -or $output.Count -lt 3) {
                $script:PythonProbeLog += "  $label  ($($candidate.Why)): did not run (exit $LASTEXITCODE)"
                continue
            }
            $version = [version]"$($output[0]).$($output[1])"
            $output = @($output[2])  # the interpreter path, for the checks below
            if ($version -lt [version]'3.11') {
                $script:PythonProbeLog += "  $label  ($($candidate.Why)): version $version, too old"
                continue
            }
            $script:PythonProbeLog += "  $label  ($($candidate.Why)): Python $version at $($output[0])"
            $found += [pscustomobject]@{
                Exe = $candidate.Exe; Args = $candidate.Args
                Version = $version;   Path = $output[0]
                # The Microsoft Store build installs under WindowsApps behind an
                # execution alias. It works, but it redirects file writes and has bitten
                # enough tooling that a real installation is preferred when one exists.
                IsStore = $output[0] -like '*\WindowsApps\*'
            }
        } catch {
            $script:PythonProbeLog += "  $label  ($($candidate.Why)): $($_.Exception.Message)"
            continue
        }
    }
    if (-not $found) { return $null }
    $real = @($found | Where-Object { -not $_.IsStore })
    if ($real.Count -gt 0) { return $real[0] }
    return @($found)[0]

    } finally {
        Remove-Item $probeScript -Force -ErrorAction SilentlyContinue
    }
}

Write-Step 'Locating a Python 3.11 or newer'
$python = Find-Python
if (-not $python) {
    # Never claim Python is absent without showing what was actually tried: on a machine
    # that plainly has Python, that message is worse than useless.
    Write-Host ''
    Write-Host '    Everything that was probed:' -ForegroundColor DarkGray
    if ($script:PythonProbeLog) {
        $script:PythonProbeLog | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray }
    } else {
        Write-Host '  (nothing -- no candidate was even reachable)' -ForegroundColor DarkGray
    }
    Fail 'no usable Python 3.11+ found (see the probe list above)' `
         'if Python is installed, pass its path: .\setup.ps1 -PythonExe "C:\Python313\python.exe"'
}
Write-Ok "Python $($python.Version) at $($python.Path)"
if ($python.IsStore) {
    Write-Warn2 'this is the Microsoft Store build of Python; it redirects file writes'
    Write-Note  'if anything behaves oddly, install Python from python.org and re-run with -Force'
}

# -- 2. Virtual environment -----------------------------------------------------------

Write-Step 'Preparing the project virtual environment'
if ($Force -and (Test-Path $VenvDir)) {
    Write-Note 'removing the existing .venv (-Force)'
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvPython)) {
    $venvArgs = @($python.Args) + @('-m', 'venv', $VenvDir)
    & $python.Exe @venvArgs
    if ($LASTEXITCODE -ne 0) { Fail 'could not create the virtual environment' 'check disk space and permissions on this folder' }
    Write-Ok "created $VenvDir"
} else {
    Write-Ok 'virtual environment already present'
}

# The tools get their own environment so they never depend on what is installed
# system-wide, and so a different machine reproduces the same versions.
& $VenvPython -m pip install --quiet --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Fail 'could not upgrade pip inside the virtual environment' 'check your network connection or proxy settings' }

# -- 3/4. Dependencies ----------------------------------------------------------------

Write-Step 'Installing dependencies'
if (Test-Path $LockFile) {
    Write-Note "installing pinned versions from $(Split-Path $LockFile -Leaf)"
    & $VenvPython -m pip install --quiet -r $LockFile
    if ($LASTEXITCODE -ne 0) { Fail 'pinned dependency install failed' "delete $LockFile and re-run to resolve fresh versions" }
} else {
    Write-Note 'no lock file yet: resolving current versions, then writing one'
}

& $VenvPython -m pip install --quiet -e "$Root[dev]"
if ($LASTEXITCODE -ne 0) { Fail 'could not install the project' 'check the pip output above' }

if (-not (Test-Path $LockFile)) {
    # Pin whatever was just resolved, so this machine and the next agree.
    & $VenvPython -m pip freeze --exclude-editable | Set-Content -Path $LockFile -Encoding utf8
    Write-Ok "wrote $(Split-Path $LockFile -Leaf) -- commit it so other machines match"
}
Write-Ok 'python environment ready'

$nlmExe = Join-Path $VenvDir 'Scripts\nlm.exe'
if (-not (Test-Path $nlmExe)) {
    Write-Warn2 'the nlm CLI was not found in the venv; notebooklm-mcp-cli may expose a different entry point'
    Write-Note  'record what it actually installs in NOTES.md (design.md 12, item 6)'
}

# -- 5. rclone ------------------------------------------------------------------------

Write-Step 'Installing rclone'
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

if (-not $RcloneVersion) {
    if (Test-Path $RcloneLock) {
        $RcloneVersion = (Get-Content $RcloneLock -Raw | ConvertFrom-Json).version
        Write-Note "pinned to rclone $RcloneVersion by $(Split-Path $RcloneLock -Leaf)"
    } else {
        $RcloneVersion = 'current'
    }
}

function Install-Rclone {
    param([string]$Version)

    # Resolve "current" to a concrete version first. The current/ directory publishes no
    # SHA256SUMS, so downloading from it means installing an unverified binary; the
    # versioned directory does publish one. Resolving up front means even a first install
    # is checksum-verified, and the resolved version is what gets pinned.
    if ($Version -eq 'current') {
        try {
            $resolved = (Invoke-WebRequest -Uri 'https://downloads.rclone.org/version.txt' -UseBasicParsing).Content.Trim()
            if ($resolved -match '(v[\d.]+)') {
                $Version = $matches[1]
                Write-Note "resolved current -> rclone $Version"
            }
        } catch {
            Write-Warn2 'could not resolve the current rclone version; falling back to the unversioned download'
        }
    }

    $slug    = if ($Version -eq 'current') { 'rclone-current-windows-amd64.zip' }
               else { "rclone-$Version-windows-amd64.zip" }
    $baseUrl = if ($Version -eq 'current') { 'https://downloads.rclone.org' }
               else { "https://downloads.rclone.org/$Version" }
    $zipUrl  = "$baseUrl/$slug"
    $sumsUrl = "$baseUrl/SHA256SUMS"

    $temp = Join-Path ([System.IO.Path]::GetTempPath()) "rclone-$([guid]::NewGuid().ToString('n'))"
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $zipPath = Join-Path $temp $slug
        Write-Note "downloading $zipUrl"
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing

        # Verify against the checksum file rclone publishes next to the archive.
        $expected = $null
        try {
            $sums = (Invoke-WebRequest -Uri $sumsUrl -UseBasicParsing).Content
            # Windows PowerShell 5.1 hands back a byte array for a content type it does
            # not consider text; PowerShell 7 hands back a string. Splitting a byte array
            # into lines matches nothing and fails silently, which is how an unverified
            # binary would sail through.
            if ($sums -is [byte[]]) { $sums = [System.Text.Encoding]::UTF8.GetString($sums) }
            foreach ($line in $sums -split "`n") {
                if ($line -match '^\s*([0-9a-fA-F]{64})\s+\*?(\S+)\s*$' -and $matches[2] -eq $slug) {
                    $expected = $matches[1].ToLower()
                }
            }
        } catch {
            Write-Warn2 "could not fetch $sumsUrl"
        }
        if (-not $expected) {
            Write-Warn2 'no published checksum available for this download'
            Write-Note  'the binary is unverified; record this in NOTES.md if it persists'
        }

        $actual = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLower()
        if ($expected -and $actual -ne $expected) {
            Fail "rclone download failed checksum verification (expected $expected, got $actual)" `
                 'delete tools\rclone.lock.json and re-run; if it recurs, download rclone by hand from rclone.org'
        }
        if ($expected) { Write-Ok 'checksum verified against the published SHA256SUMS' }

        Expand-Archive -Path $zipPath -DestinationPath $temp -Force
        $found = Get-ChildItem -Path $temp -Filter 'rclone.exe' -Recurse | Select-Object -First 1
        if (-not $found) { Fail 'rclone.exe was not present in the downloaded archive' 'try again, or install rclone manually into tools\' }
        Copy-Item $found.FullName $RcloneExe -Force

        $resolved = (& $RcloneExe version) | Select-Object -First 1
        if ($resolved -match 'v([\d.]+)') { $resolvedVersion = "v$($matches[1])" } else { $resolvedVersion = $Version }

        @{
            version  = $resolvedVersion
            archive  = $slug
            sha256   = $actual
            pinned   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        } | ConvertTo-Json | Set-Content -Path $RcloneLock -Encoding utf8

        Write-Ok "installed rclone $resolvedVersion (pinned in $(Split-Path $RcloneLock -Leaf))"
    } finally {
        Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
    }
}

if (Test-Path $RcloneExe) {
    $installed = (& $RcloneExe version) | Select-Object -First 1
    Write-Ok "already present: $installed"
} else {
    Install-Rclone -Version $RcloneVersion
}

# -- 6. Drive credentials -------------------------------------------------------------

function Invoke-VenvPython {
    <#
        Run a short Python script in the project venv.

        Always via a temp file, never `python -c`: Windows PowerShell 5.1 mangles native
        arguments containing quotes or spaces, so an inline script arrives as a syntax
        error. This is the same failure that broke Python detection, so there is exactly
        one way to call Python from this script and it is this function.
    #>
    param(
        [string[]]$Lines,
        [string]$StdIn,
        [switch]$IgnoreFailure
    )
    $path = Join-Path ([System.IO.Path]::GetTempPath()) "nlmt-$([guid]::NewGuid().ToString('n')).py"
    try {
        $Lines | Set-Content -Path $path -Encoding utf8
        if ($PSBoundParameters.ContainsKey('StdIn')) {
            $output = $StdIn | & $VenvPython $path 2>&1
        } else {
            $output = & $VenvPython $path 2>&1
        }
        if ($LASTEXITCODE -ne 0 -and -not $IgnoreFailure) {
            throw "python failed (exit $LASTEXITCODE): $(($output | Out-String).Trim())"
        }
        return ($output | Out-String)
    } finally {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
    }
}

function Get-StoredPassword {
    try {
        $result = Invoke-VenvPython -Lines @(
            'import keyring'
            "value = keyring.get_password('$KeyService', '$KeyAccount')"
            'print(value or "")'
        )
    } catch {
        return ''
    }
    return $result.Trim()
}

function Set-StoredPassword {
    param([string]$Value)
    # The password goes in on stdin, never as a command-line argument: arguments are
    # visible to other processes and land in shell history.
    try {
        Invoke-VenvPython -StdIn $Value -Lines @(
            'import sys, keyring'
            'value = sys.stdin.readline().rstrip("\r\n")'
            "keyring.set_password('$KeyService', '$KeyAccount', value)"
        ) | Out-Null
    } catch {
        Fail "could not store the password in Windows Credential Manager: $_" `
             'check that the keyring package installed correctly, then re-run'
    }
}

Write-Step 'Configuring Google Drive access'
if ($SkipLogins) {
    Write-Note 'skipped (-SkipLogins)'
} else {
    $password = Get-StoredPassword
    if (-not $password) {
        # A random password the operator never has to know or type: it lives in Windows
        # Credential Manager (DPAPI, bound to this account) and reaches rclone only
        # through an environment variable on the child process.
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $password = [Convert]::ToBase64String($bytes)
        Set-StoredPassword -Value $password
        Write-Ok 'generated an rclone config password and stored it in Windows Credential Manager'
    } else {
        Write-Ok 'rclone config password found in Windows Credential Manager'
    }

    $env:RCLONE_CONFIG_PASS = $password
    try {
        $remotes = & $RcloneExe listremotes 2>$null
        if ($LASTEXITCODE -ne 0) { $remotes = @() }

        if ($remotes -contains "${RemoteName}:") {
            Write-Ok "rclone remote '$RemoteName' already configured"
        } else {
            Write-Host ''
            Write-Host '    A browser window will open so you can authorize rclone against your' -ForegroundColor Yellow
            Write-Host '    normal Google account. Nothing is created in Google Cloud, and the' -ForegroundColor Yellow
            Write-Host '    drive.file scope means rclone can only see files it creates itself.' -ForegroundColor Yellow
            Write-Host ''
            & $RcloneExe config create $RemoteName drive scope drive.file
            if ($LASTEXITCODE -ne 0) {
                Fail 'rclone Drive authorization did not complete' `
                     'run: tools\rclone.exe config   and create a "drive" remote named nlmtools with scope drive.file'
            }
            Write-Ok "authorized Drive remote '$RemoteName'"
        }

        # Encrypt the config at rest. The command moved between rclone versions, so probe
        # rather than assume; an unencrypted config is a finding, not a silent pass.
        $encryptionHelp = & $RcloneExe config encryption --help 2>&1
        if ($LASTEXITCODE -eq 0) {
            $check = & $RcloneExe config encryption check 2>&1
            if ($LASTEXITCODE -ne 0) {
                & $RcloneExe config encryption set 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { Write-Ok 'rclone config encrypted at rest' }
                else { Write-Warn2 'could not encrypt rclone.conf automatically -- see NOTES.md' }
            } else {
                Write-Ok 'rclone config already encrypted'
            }
        } else {
            Write-Warn2 "this rclone build has no 'config encryption' command"
            Write-Note  'encrypt by hand: tools\rclone.exe config  ->  s) Set configuration password'
            Write-Note  'then record the version and the working command in NOTES.md'
        }
    } finally {
        Remove-Item Env:\RCLONE_CONFIG_PASS -ErrorAction SilentlyContinue
    }
}

# -- 7. NotebookLM credentials --------------------------------------------------------

Write-Step 'Configuring NotebookLM access'
if ($SkipLogins) {
    Write-Note 'skipped (-SkipLogins)'
} elseif (-not (Test-Path $nlmExe)) {
    Write-Warn2 'nlm.exe not found in the venv; skipping the NotebookLM login'
} else {
    & $nlmExe login --check 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'NotebookLM session is valid'
    } else {
        Write-Host ''
        Write-Host '    A browser will open for the NotebookLM login. Use the SAME Google' -ForegroundColor Yellow
        Write-Host '    account you just authorized for Drive -- a mismatch causes permission' -ForegroundColor Yellow
        Write-Host '    errors that only surface much later and make no sense.' -ForegroundColor Yellow
        Write-Host ''
        & $nlmExe login
        if ($LASTEXITCODE -ne 0) { Fail 'nlm login did not complete' 'run: .venv\Scripts\nlm.exe login' }
        Write-Ok 'logged in to NotebookLM'
    }
}

# -- 8. Verify ------------------------------------------------------------------------

Write-Step 'Verifying the installation'
$nlmt = Join-Path $VenvDir 'Scripts\nlmt.exe'
if (Test-Path $nlmt) {
    & $nlmt --version
    Write-Ok 'nlmt is installed'
} else {
    Fail 'nlmt was not installed into the virtual environment' 'check the pip output above'
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host ''
Write-Host '  Activate:  .\.venv\Scripts\Activate.ps1'
Write-Host '  Learn:     nlmt --ai            (complete reference, written for an agent)'
Write-Host '             nlmt help workflow   (what these tools do and why)'
Write-Host '  Check:     nlmt doctor          (confirms both logins are the same account)'
Write-Host ''
