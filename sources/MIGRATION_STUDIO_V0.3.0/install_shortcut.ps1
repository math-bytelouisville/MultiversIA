param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$manifestFile = Join-Path $projectRoot 'release-manifest.json'
$manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
if ($manifest.status -ne 'REVIEWED_SCORING_BRIDGE') { throw 'La livraison doit avoir ses revues avant installation.' }
$requiredFiles = @('launch_migration.py', 'migration_pipeline.py', 'rust_runtime.py', 'scoring_model.py', 'scoring_rust.py', 'rust_support.rs', 'profile_import.py', 'migration_report.py', 'csharp_oracle.py', 'GUIDE.txt', 'install_shortcut.ps1')
foreach ($name in $requiredFiles) {
    $expected = $manifest.files_sha256.PSObject.Properties[$name]
    if ($null -eq $expected -or $expected.Value -notmatch '^[0-9a-f]{64}$') { throw "Empreinte absente : $name" }
    $targetFile = Join-Path $projectRoot $name
    if ((Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected.Value) { throw "Fichier modifié : $name" }
}
$pythonCommand = Get-Command python -CommandType Application | Select-Object -First 1
$pythonPath = [string]$pythonCommand.Source
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Python est absent.' }
$pythonWindowed = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowed -PathType Leaf)) { throw 'Python avec interface graphique est absent.' }
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MultiversIA - Migration C# vers Rust.lnk'
$arguments = '"' + (Join-Path $projectRoot 'launch_migration.py') + '"'
if ($VerifyOnly) {
    [pscustomobject]@{status='PREFLIGHT_PASS'; shortcut=$shortcutPath; target=$pythonWindowed; arguments=$arguments} | ConvertTo-Json
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
    $shortcut.Description = 'Carte des preuves et comparaison du calcul QCM : C# vers maths vers Rust.'
    $shortcut.Save()
}
$readback = $shell.CreateShortcut($shortcutPath)
if (-not (Test-Path -LiteralPath $shortcutPath) -or $readback.TargetPath -ne $pythonWindowed -or $readback.Arguments -ne $arguments -or $readback.WorkingDirectory -ne $projectRoot) { throw 'Le raccourci ne correspond pas à la livraison.' }
[pscustomobject]@{status='INSTALLED_AND_READ_BACK'; shortcut=$shortcutPath; target=$readback.TargetPath; arguments=$readback.Arguments; working_directory=$readback.WorkingDirectory} | ConvertTo-Json
