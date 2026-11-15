param([switch]$InspectOnly)
$ErrorActionPreference = 'Stop'
$studioRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$studioManifestPath = Join-Path $studioRoot 'studio-release-manifest.json'
$studioManifest = Get-Content -LiteralPath $studioManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($studioManifest.status -ne 'REVIEWED_LOCAL_STUDIO') { throw 'Studio not qualified.' }
$studioRequired = @('mvc_runtime.py','launch_site.py','app.py','ui_backend.py','install_studio_shortcuts.ps1')
foreach ($studioName in $studioRequired) {
    if ($null -eq $studioManifest.files_sha256.PSObject.Properties[$studioName]) { throw "Essential file missing: $studioName" }
}
$studioSourceRoot = Join-Path $studioRoot 'qcm_studio'
$studioExpectedSources = @($studioManifest.files_sha256.PSObject.Properties.Name | Where-Object { $_.StartsWith('qcm_studio/') } | Sort-Object)
$studioActualSources = @(Get-ChildItem -LiteralPath $studioSourceRoot -Recurse -File | ForEach-Object { $_.FullName.Substring($studioRoot.Length + 1).Replace('\','/') } | Sort-Object)
if ($studioExpectedSources.Count -eq 0 -or ($null -ne (Compare-Object $studioExpectedSources $studioActualSources))) { throw 'MVC source inventory differs from release.' }
foreach ($studioEntry in $studioManifest.files_sha256.PSObject.Properties) {
    if (($studioEntry.Value -isnot [string]) -or ($studioEntry.Value -notmatch '^[a-f0-9]{64}$')) { throw 'Invalid SHA256.' }
    $studioFile = [IO.Path]::GetFullPath((Join-Path $studioRoot $studioEntry.Name))
    if (-not $studioFile.StartsWith($studioRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'File outside studio.' }
    if ((Get-FileHash -LiteralPath $studioFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $studioEntry.Value) { throw "File changed: $($studioEntry.Name)" }
}
$studioPython = (Get-Command python -CommandType Application | Select-Object -First 1).Source
$studioPythonw = Join-Path (Split-Path -Parent $studioPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $studioPythonw -PathType Leaf)) { throw 'Pythonw absent.' }
$studioDesktop = [Environment]::GetFolderPath('Desktop')
$studioShell = New-Object -ComObject WScript.Shell
$studioLinks = @(@{Name='MultiversIA - Game Editor.lnk';Script='launch_site.py';Description='Game Editor local : QCM configurable, carte, suivi et scores.'},@{Name='MultiversIA - Atelier V0.2.lnk';Script='app.py';Description='Atelier local de fonctions et modules : C#, maths, Python et Rust.'})
$studioReadback = @()
foreach ($studioItem in $studioLinks) {
    $studioPath = Join-Path $studioDesktop $studioItem.Name
    $studioArguments = '"' + (Join-Path $studioRoot $studioItem.Script) + '"'
    $studioLink = $studioShell.CreateShortcut($studioPath)
    if ((Test-Path -LiteralPath $studioPath) -and (($studioLink.TargetPath -ne $studioPythonw) -or ($studioLink.Arguments -ne $studioArguments))) { throw 'Another shortcut occupies the name; no replacement.' }
    if (-not $InspectOnly) {
        $studioLink.TargetPath=$studioPythonw
        $studioLink.Arguments=$studioArguments
        $studioLink.WorkingDirectory=$studioRoot
        $studioLink.Description=$studioItem.Description
        $studioLink.IconLocation=$studioPythonw+',0'
        $studioLink.Save()
    }
    $studioActual=$studioShell.CreateShortcut($studioPath)
    if (-not $InspectOnly -and ((-not (Test-Path -LiteralPath $studioPath)) -or $studioActual.TargetPath -ne $studioPythonw -or $studioActual.Arguments -ne $studioArguments -or $studioActual.WorkingDirectory -ne $studioRoot)) { throw 'Shortcut readback differs.' }
    $studioReadback += @{path=$studioPath;target=$studioActual.TargetPath;arguments=$studioActual.Arguments;exists=[bool](Test-Path -LiteralPath $studioPath)}
}
$studioReadback | ConvertTo-Json
