param([Parameter(Mandatory)][string]$AssemblyPath, [Parameter(Mandatory)][string]$RequestPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
Set-StrictMode -Version Latest
try {
    $request = [IO.File]::ReadAllText($RequestPath) | ConvertFrom-Json -Depth 30
    $bytes = [IO.File]::ReadAllBytes($AssemblyPath)
    $digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    if ($digest -cne $request.assembly_sha256) { throw 'DLL identity mismatch' }
    if ($request.mapping.Count -lt 1 -or $request.mapping.Count -gt 8 -or $request.cases.Count -gt 4096) { throw 'Request bound' }
    $assembly = [Reflection.Assembly]::Load($bytes)
    $flags = [Reflection.BindingFlags]::Static -bor [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::NonPublic -bor [Reflection.BindingFlags]::DeclaredOnly
    $resolved = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    function Runtime-Type([string]$type) { if ($type -ceq 'int') { return [int] }; if ($type -ceq 'bool') { return [bool] }; throw 'Unexpected type' }
    foreach ($entry in $request.mapping) {
        $type = $assembly.GetType([string]$entry.declaring_type,$true,$false)
        $parameterTypes = [type[]]@($entry.parameter_types | ForEach-Object { Runtime-Type $_ })
        $method = $type.GetMethod([string]$entry.name,$flags,$null,$parameterTypes,$null)
        if ($null -eq $method -or $method.ReturnType -ne (Runtime-Type $entry.return_type) -or $method.IsGenericMethod -or $method.DeclaringType -ne $type) { throw 'Reflection signature mismatch' }
        $resolved.Add([string]$entry.id,@{method=$method;entry=$entry})
    }
    $index=0
    foreach ($case in $request.cases) {
        if ($case.index -ne $index -or -not $resolved.ContainsKey([string]$case.unit_id)) { throw 'Case identity mismatch' }
        $item=$resolved[[string]$case.unit_id]
        if ($case.inputs.Count -ne $item.entry.parameter_types.Count) { throw 'Input count mismatch' }
        $arguments=[object[]]::new($case.inputs.Count)
        for ($p=0; $p -lt $arguments.Length; $p++) {
            $value=$case.inputs[$p]
            if ($item.entry.parameter_types[$p] -ceq 'bool') {
                if ($value -isnot [bool]) { throw 'Boolean input required' }; $arguments[$p]=[bool]$value
            } else {
                if (($value -isnot [int] -and $value -isnot [long]) -or $value -lt [int]::MinValue -or $value -gt [int]::MaxValue) { throw 'Int32 input required' }; $arguments[$p]=[int]$value
            }
        }
        $value=$item.method.Invoke($null,$arguments)
        if (($item.entry.return_type -ceq 'int' -and $value -isnot [int]) -or ($item.entry.return_type -ceq 'bool' -and $value -isnot [bool])) { throw 'Return type mismatch' }
        [ordered]@{index=$index;unit_id=[string]$case.unit_id;value=$value} | ConvertTo-Json -Compress
        $index++
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
