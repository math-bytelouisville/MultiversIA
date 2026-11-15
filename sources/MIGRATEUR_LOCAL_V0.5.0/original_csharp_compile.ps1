param([Parameter(Mandatory)][string]$SourcePath, [Parameter(Mandatory)][string]$ExpectedPath,
      [Parameter(Mandatory)][string]$AssemblyPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
Set-StrictMode -Version Latest
$answer = [ordered]@{status='INCONCLUSIVE';reason='UNFINISHED';source_sha256=$null;mapping=@();diagnostics=@()}
try {
    $bytes = [IO.File]::ReadAllBytes($SourcePath)
    if ($bytes.Length -gt 65536) { throw 'Source exceeds 64 KiB' }
    $text = [Text.UTF8Encoding]::new($false,$true).GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    $answer.source_sha256 = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    $expected = [IO.File]::ReadAllText($ExpectedPath) | ConvertFrom-Json -Depth 100
    if ($expected.source_sha256 -cne $answer.source_sha256) { throw 'Source identity mismatch' }
    Add-Type -Path (Join-Path $PSHOME 'Microsoft.CodeAnalysis.dll')
    Add-Type -Path (Join-Path $PSHOME 'Microsoft.CodeAnalysis.CSharp.dll')
    $ct = [Threading.CancellationToken]::None
    $parse = [Microsoft.CodeAnalysis.CSharp.CSharpParseOptions]::Default.WithLanguageVersion([Microsoft.CodeAnalysis.CSharp.LanguageVersion]::CSharp12)
    $tree = [Microsoft.CodeAnalysis.CSharp.CSharpSyntaxTree]::ParseText($text,$parse,'',[Text.Encoding]::UTF8,$ct)
    $reference = [Microsoft.CodeAnalysis.MetadataReference]::CreateFromFile([object].Assembly.Location,[Microsoft.CodeAnalysis.MetadataReferenceProperties]::Assembly,$null)
    $options = [Microsoft.CodeAnalysis.CSharp.CSharpCompilationOptions]::new([Microsoft.CodeAnalysis.OutputKind]::DynamicallyLinkedLibrary).WithOverflowChecks($false).WithDeterministic($true)
    $compilation = [Microsoft.CodeAnalysis.CSharp.CSharpCompilation]::Create('MiaOriginal',[Microsoft.CodeAnalysis.SyntaxTree[]]@($tree),[Microsoft.CodeAnalysis.MetadataReference[]]@($reference),$options)
    $errors = @($compilation.GetDiagnostics($ct) | Where-Object { [string]$_.Severity -eq 'Error' })
    if ($errors.Count -gt 0) { throw 'Source compilation diagnostics' }
    $root = [Microsoft.CodeAnalysis.SyntaxTree].GetMethod('GetRoot',[type[]]@([Threading.CancellationToken])).Invoke($tree,[object[]]@($ct))
    $methods = @($root.DescendantNodes() | Where-Object { $_ -is [Microsoft.CodeAnalysis.CSharp.Syntax.MethodDeclarationSyntax] })
    if ($methods.Count -ne $expected.units.Count -or $methods.Count -lt 1 -or $methods.Count -gt 8) { throw 'Method count mismatch' }
    $model = [Microsoft.CodeAnalysis.Compilation].GetMethod('GetSemanticModel',[type[]]@([Microsoft.CodeAnalysis.SyntaxTree],[bool])).Invoke($compilation,[object[]]@($tree,$false))
    $display = [Microsoft.CodeAnalysis.ISymbol].GetMethod('ToDisplayString',[type[]]@([Microsoft.CodeAnalysis.SymbolDisplayFormat]))
    function Type-Name($symbol) {
        switch ([string]$symbol.SpecialType) { 'System_Int32' { return 'int' }; 'System_Boolean' { return 'bool' }; default { throw 'Unexpected signature type' } }
    }
    for ($i=0; $i -lt $methods.Count; $i++) {
        $symbol = [Microsoft.CodeAnalysis.CSharp.CSharpExtensions]::GetDeclaredSymbol($model,[Microsoft.CodeAnalysis.CSharp.Syntax.BaseMethodDeclarationSyntax]$methods[$i],$ct)
        $unit = $expected.units[$i]
        $typeDisplay = [string]$display.Invoke($symbol.ContainingType,[object[]]@([Microsoft.CodeAnalysis.SymbolDisplayFormat]::CSharpErrorMessageFormat))
        if (-not $symbol.IsStatic -or $symbol.IsGenericMethod -or $symbol.Parameters.Length -ne $unit.parameters.Count -or
            $unit.id -cne ('u'+$i) -or $unit.name -cne [string]$symbol.Name -or
            $unit.qualified_name -cne ($typeDisplay+'.'+[string]$symbol.Name) -or $unit.return_type -cne (Type-Name $symbol.ReturnType)) { throw 'Method signature mismatch' }
        $parameterTypes = @()
        for ($p=0; $p -lt $symbol.Parameters.Length; $p++) {
            $parameter = $symbol.Parameters[$p]
            $t = Type-Name $parameter.Type
            if ($unit.parameters[$p].name -cne [string]$parameter.Name -or $unit.parameters[$p].type -cne $t -or [string]$parameter.RefKind -ne 'None') { throw 'Parameter signature mismatch' }
            $parameterTypes += $t
        }
        # MetadataName removes source escapes correctly, including namespace components.
        $parts = @([string]$symbol.ContainingType.MetadataName)
        $ns = $symbol.ContainingNamespace
        while (-not $ns.IsGlobalNamespace) { $parts = @([string]$ns.MetadataName) + $parts; $ns = $ns.ContainingNamespace }
        $answer.mapping += [ordered]@{id=[string]$unit.id;name=[string]$symbol.MetadataName;qualified_name=[string]$unit.qualified_name;declaring_type=($parts -join '.');parameter_types=@($parameterTypes);return_type=[string]$unit.return_type}
    }
    # Do not load or execute the generated assembly in this compilation process.
    $stream = [IO.File]::Open($AssemblyPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $emitted = $compilation.Emit($stream) } finally { $stream.Dispose() }
    if (-not $emitted.Success) { throw 'Roslyn DLL emission failed' }
    $answer.status='COMPILED'; $answer.reason='Exact source emitted as standalone DLL'
    $answer.assembly_sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([IO.File]::ReadAllBytes($AssemblyPath))).ToLowerInvariant()
    $answer.language_version='CSharp12'
    $answer.check_overflow=[bool]$options.CheckOverflow
} catch {
    $answer.status='INCONCLUSIVE'; $answer.reason=$_.Exception.Message
}
$answer | ConvertTo-Json -Depth 30 -Compress
