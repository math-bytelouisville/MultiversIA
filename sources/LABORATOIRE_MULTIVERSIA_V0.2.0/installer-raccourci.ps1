$ErrorActionPreference = 'Stop'
$labFolder = $PSScriptRoot
$pythonGui = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
$pythonConsole = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$entryPoint = Join-Path $labFolder 'app.py'
if (-not (Test-Path -LiteralPath $pythonGui) -or -not (Test-Path -LiteralPath $entryPoint)) {
    throw 'Python ou application introuvable.'
}
& $pythonConsole (Join-Path $labFolder 'check_release.py') | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Livraison non qualifiée. Voir evidence/release-check.json.' }
$desktopFolder = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopFolder 'MultiversIA - Laboratoire.lnk'
$targetArguments = '"' + $entryPoint + '"'
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and ($shortcut.TargetPath -ne $pythonGui -or $shortcut.Arguments -ne $targetArguments)) {
    throw 'Un autre raccourci porte déjà ce nom : il est conservé.'
}
$shortcut.TargetPath = $pythonGui
$shortcut.Arguments = $targetArguments
$shortcut.WorkingDirectory = $labFolder
$shortcut.Description = 'Carte libre du laboratoire MultiversIA : missions, preuves et C# vers maths'
$shortcut.IconLocation = $pythonGui + ',0'
$shortcut.Save()
$savedShortcut = $shellObject.CreateShortcut($shortcutPath)
if ($savedShortcut.TargetPath -ne $pythonGui -or $savedShortcut.Arguments -ne $targetArguments -or $savedShortcut.WorkingDirectory -ne $labFolder) {
    throw 'Vérification du raccourci échouée.'
}
[ordered]@{shortcut=$shortcutPath;target=$savedShortcut.TargetPath;arguments=$savedShortcut.Arguments;working_directory=$savedShortcut.WorkingDirectory;verified=$true;at=[DateTime]::UtcNow.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $labFolder 'evidence\desktop-shortcut.json') -Encoding utf8
Write-Output $shortcutPath
