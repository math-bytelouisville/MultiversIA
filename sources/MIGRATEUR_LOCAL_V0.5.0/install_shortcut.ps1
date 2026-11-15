param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'release-manifest.json') -Raw | ConvertFrom-Json
if ($manifest.status -ne 'REVIEWED_LOCAL_MIGRATOR') { throw 'La livraison doit avoir ses revues avant installation.' }
if ($manifest.files_sha256 -isnot [pscustomobject]) { throw 'Inventaire des fichiers invalide.' }
$requiredFiles = @('app.py', 'migrator.py', 'common.py', 'emitters.py', 'csharp_import.py', 'rust_import.py', 'verifier.py', 'original_csharp.py', 'original_csharp_compile.ps1', 'original_csharp_run.ps1', 'module_model.py', 'math_core.py', 'module_backends.py', 'code_backends.py', 'legacy_bridge.py', 'module_import.ps1', 'FOUNDATION_V050.json', 'GUIDE.txt', 'install_shortcut.ps1')
foreach ($name in $requiredFiles) {
    $expected = $manifest.files_sha256.PSObject.Properties[$name]
    if ($null -eq $expected -or $expected.Value -isnot [string] -or $expected.Value -notmatch '^[0-9a-f]{64}$') { throw "Empreinte absente ou invalide : $name" }
}
$seenNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $manifest.files_sha256.PSObject.Properties) {
    $name = $entry.Name
    if ($name -notmatch '^[A-Za-z0-9_.-]+$' -or -not $seenNames.Add($name) -or $entry.Value -isnot [string] -or $entry.Value -notmatch '^[0-9a-f]{64}$') { throw "Entrée de manifeste invalide : $name" }
    $targetFile = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $name))
    if (-not $targetFile.StartsWith($projectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Chemin hors de la livraison : $name" }
    if ((Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { throw "Fichier modifié : $name" }
}
$pythonCommand = Get-Command python -CommandType Application | Select-Object -First 1
$pythonPath = [string]$pythonCommand.Source
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Python est absent.' }
$pythonWindowed = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowed -PathType Leaf)) { throw 'Python avec interface graphique est absent.' }
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MultiversIA - Migrateur local V0.5.0.lnk'
$arguments = '"' + (Join-Path $projectRoot 'app.py') + '"'
if ($VerifyOnly) {
    [pscustomobject]@{status='PREFLIGHT_PASS'; shortcut=$shortcutPath; target=$pythonWindowed; arguments=$arguments; working_directory=$projectRoot} | ConvertTo-Json
    exit 0
}
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -ne $pythonWindowed -or $existing.Arguments -ne $arguments -or $existing.WorkingDirectory -ne $projectRoot) {
        throw 'Un raccourci différent porte déjà ce nom. Il a été conservé.'
    }
} else {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonWindowed
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = 'Atelier local V0.5.0 : source Rust/C#, modèle Int32 modulo et langage cible.'
    $shortcut.Save()
}
$readback = $shell.CreateShortcut($shortcutPath)
if (-not (Test-Path -LiteralPath $shortcutPath) -or $readback.TargetPath -ne $pythonWindowed -or $readback.Arguments -ne $arguments -or $readback.WorkingDirectory -ne $projectRoot) { throw 'Le raccourci ne correspond pas à la livraison.' }
[pscustomobject]@{status='INSTALLED_AND_READ_BACK'; shortcut=$shortcutPath; target=$readback.TargetPath; arguments=$readback.Arguments; working_directory=$readback.WorkingDirectory} | ConvertTo-Json
