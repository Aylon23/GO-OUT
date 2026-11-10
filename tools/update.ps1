# Self-update from GitHub. Called by start.bat on every start.
# Downloads the latest version of the branch if it changed, and copies it over this folder.
# Your data (data\), the Python and Node packages (.venv, node_modules) are never touched.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$repo = 'Aylon23/GO-OUT'
$branch = 'claude/optimistic-rubin-f6yohu'
$verFile = Join-Path $root '.version'
$stampFile = Join-Path $root '.version.json'   # what is installed: shown at the top of the app
$checkFile = Join-Path $root '.update-check.json'  # the last time this script looked for an update

function Save-Json($path, $obj) { $obj | ConvertTo-Json -Compress | Set-Content -Path $path -Encoding UTF8 }
function Now-Utc { (Get-Date).ToUniversalTime().ToString('o') }

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $info = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/commits/$branch" -Headers @{ 'User-Agent' = 'benchmark-updater' } -TimeoutSec 15
    $sha = $info.sha
} catch {
    Write-Host 'Could not check for updates (no internet?). Starting the version you have.'
    Save-Json $checkFile @{ at = (Now-Utc); ok = $false; error = "$($_.Exception.Message)" }
    exit 0
}

$local = ''
if (Test-Path $verFile) { $local = (Get-Content $verFile -Raw).Trim() }
$stamp = @{ sha = $sha; date = "$($info.commit.committer.date)"; message = "$($info.commit.message)" }
if ($info.commit.committer.date -is [datetime]) { $stamp.date = $info.commit.committer.date.ToUniversalTime().ToString('o') }
if ($local -eq $sha) {
    Write-Host 'Up to date.'
    Save-Json $stampFile $stamp
    Save-Json $checkFile @{ at = (Now-Utc); ok = $true; latest = $sha }
    exit 0
}

Write-Host 'A new version is available. Updating...'
$tmp = Join-Path $env:TEMP ('benchmark-update-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'update.zip'
    Invoke-WebRequest -Uri "https://github.com/$repo/archive/$sha.zip" -OutFile $zip -UseBasicParsing -TimeoutSec 180
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
    # start.bat is running right now, so it is placed next to itself and swapped in by start.bat
    Copy-Item (Join-Path $src.FullName 'start.bat') (Join-Path $root 'start.bat.new') -Force
    robocopy $src.FullName $root /E /XD data inbox .venv node_modules .git /XF start.bat .version .version.json .update-check.json /NFL /NDL /NJH /NJS /NP | Out-Null
    Set-Content -Path $verFile -Value $sha
    Save-Json $stampFile $stamp
    Save-Json $checkFile @{ at = (Now-Utc); ok = $true; latest = $sha }
    Write-Host 'Updated.'
} catch {
    Write-Host "Update failed, starting the version you have. ($($_.Exception.Message))"
    Save-Json $checkFile @{ at = (Now-Utc); ok = $false; latest = $sha; error = "The download failed: $($_.Exception.Message)" }
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
