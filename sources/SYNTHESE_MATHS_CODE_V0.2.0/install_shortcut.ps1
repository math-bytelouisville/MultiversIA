param([switch]$InspectOnly)
$ErrorActionPreference = 'Stop'
$atelierRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$atelierApp = Join-Path $atelierRoot 'app.py'
$atelierPythonCommand = Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
[string]$atelierPythonConsole = $atelierPythonCommand.Source
if (-not (Test-Path -LiteralPath $atelierPythonConsole -PathType Leaf)) { throw 'Python console absent.' }
$atelierPython = Join-Path (Split-Path -Parent $atelierPythonConsole) 'pythonw.exe'
$atelierManifest = Join-Path $atelierRoot 'release-manifest.json'
if (-not (Test-Path -LiteralPath $atelierApp -PathType Leaf)) { throw 'Application absente.' }
if (-not (Test-Path -LiteralPath $atelierPython -PathType Leaf)) { throw 'Python graphique absent.' }
if (-not (Test-Path -LiteralPath $atelierManifest -PathType Leaf)) { throw 'Manifeste de livraison absent.' }
$atelierRelease = Get-Content -LiteralPath $atelierManifest -Raw -Encoding UTF8 | ConvertFrom-Json
if ($atelierRelease.status -ne 'REVIEWED_BOUNDED_MILESTONE') { throw 'Jalon non qualifie.' }
if (($null -eq $atelierRelease.files_sha256) -or
    ($atelierRelease.files_sha256 -isnot [System.Management.Automation.PSCustomObject]) -or
    (@($atelierRelease.files_sha256.PSObject.Properties).Count -eq 0)) { throw 'Inventaire des empreintes absent ou invalide.' }
$atelierRequired = @('app.py', 'ui_backend.py', 'pipeline.py', 'migration.py', 'legacy_bridge.py', 'math_core.py', 'code_backends.py', 'install_shortcut.ps1')
foreach ($atelierName in $atelierRequired) {
    if ($null -eq $atelierRelease.files_sha256.PSObject.Properties[$atelierName]) { throw "Fichier essentiel non couvert : $atelierName" }
}
foreach ($atelierEntry in $atelierRelease.files_sha256.PSObject.Properties) {
    if (($atelierEntry.Value -isnot [string]) -or ($atelierEntry.Value -notmatch '^[a-f0-9]{64}$')) { throw 'Empreinte SHA-256 invalide.' }
    $atelierFile = [System.IO.Path]::GetFullPath((Join-Path $atelierRoot $atelierEntry.Name))
    if (-not $atelierFile.StartsWith($atelierRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Chemin hors atelier.' }
    if (-not (Test-Path -LiteralPath $atelierFile -PathType Leaf)) { throw "Fichier absent : $($atelierEntry.Name)" }
    if ((Get-FileHash -LiteralPath $atelierFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $atelierEntry.Value) { throw "Empreinte differente : $($atelierEntry.Name)" }
}
$atelierDesktop = [Environment]::GetFolderPath('Desktop')
$atelierLink = Join-Path $atelierDesktop 'MultiversIA - Maths vers code.lnk'
$atelierShell = New-Object -ComObject WScript.Shell
$atelierShortcut = $atelierShell.CreateShortcut($atelierLink)
$atelierArgs = '"' + $atelierApp + '"'
if ((Test-Path -LiteralPath $atelierLink) -and
    (($atelierShortcut.TargetPath -ne $atelierPython) -or ($atelierShortcut.Arguments -ne $atelierArgs))) {
    throw 'Un autre raccourci utilise ce nom ; aucun remplacement.'
}
if (-not $InspectOnly) {
    $atelierShortcut.TargetPath = $atelierPython
    $atelierShortcut.Arguments = $atelierArgs
    $atelierShortcut.WorkingDirectory = $atelierRoot
    $atelierShortcut.Description = 'Atelier local : maths vers Python, C# et Rust. Fonctions pures seulement.'
    $atelierShortcut.IconLocation = $atelierPython + ',0'
    $atelierShortcut.Save()
}
$atelierReadback = $atelierShell.CreateShortcut($atelierLink)
if (-not $InspectOnly) {
    if ((-not (Test-Path -LiteralPath $atelierLink -PathType Leaf)) -or
        ($atelierReadback.TargetPath -ne $atelierPython) -or
        ($atelierReadback.Arguments -ne $atelierArgs) -or
        ($atelierReadback.WorkingDirectory -ne $atelierRoot)) { throw 'Raccourci non conforme apres sauvegarde.' }
}
[ordered]@{
    inspect_only = [bool]$InspectOnly
    path = $atelierLink
    exists = [bool](Test-Path -LiteralPath $atelierLink -PathType Leaf)
    target = $atelierReadback.TargetPath
    arguments = $atelierReadback.Arguments
    working_directory = $atelierReadback.WorkingDirectory
    manifest_checked = $true
} | ConvertTo-Json
