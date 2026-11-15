param([string]$SourcePath = '', [string]$SourceText = '')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Pure Roslyn analysis only. Never emits or executes the supplied C#.
# Module adapter: one class, 1-8 static methods, resolved direct internal calls, int/bool and pure conditions. No execution of supplied source.
$answer = [ordered]@{schema='mia-csharp-module-ir-1';status='INCONCLUSIF';reason='INTERNAL_ERROR';source_sha256=$null;roslyn_version=$null;analysis_metadata=$null;units=@();inventory=@();class_name=$null;diagnostics=@()}
function Refuse([string]$message) { throw [System.NotSupportedException]::new($message) }
function Symbol-Key($symbol) {
    $display = [Microsoft.CodeAnalysis.ISymbol].GetMethod('ToDisplayString',[type[]]@([Microsoft.CodeAnalysis.SymbolDisplayFormat]))
    return [string]$display.Invoke($symbol,[object[]]@([Microsoft.CodeAnalysis.SymbolDisplayFormat]::CSharpErrorMessageFormat))
}
function Type-Name($symbol) {
    if ($null -eq $symbol) { Refuse 'Missing expression type' }
    switch ([string]$symbol.SpecialType) {
        'System_Int32' { return 'int' }
        'System_Boolean' { return 'bool' }
        default { Refuse ('Unsupported type: ' + [string]$symbol.SpecialType) }
    }
}
function Read-Operation($operation, [int]$depth = 0) {
    if ($null -eq $operation -or $depth -gt 40) { Refuse 'Missing or deeply nested operation' }
    $type = Type-Name $operation.Type
    $next = $depth + 1
    switch ([string]$operation.Kind) {
        'Literal' {
            if (-not $operation.ConstantValue.HasValue) { Refuse 'Nonconstant literal' }
            $value = $operation.ConstantValue.Value
            if (($type -eq 'int' -and $value -isnot [int]) -or ($type -eq 'bool' -and $value -isnot [bool])) { Refuse 'Literal type mismatch' }
            return @{kind='constant';type=$type;value=$value}
        }
        'ParameterReference' {
            $name = [string]$operation.Parameter.Name
            if (-not $script:parameterTypes.ContainsKey($name) -or $script:parameterTypes[$name] -ne $type) { Refuse 'Unknown parameter reference' }
            return @{kind='param';type=$type;name=$name}
        }
        'Invocation' {
            $target = $operation.TargetMethod
            if ($null -eq $target -or -not $target.IsStatic -or $target.IsGenericMethod -or $null -ne $target.ReducedFrom -or $null -ne $operation.Instance -or $operation.IsVirtual) { Refuse 'Only direct static internal calls are supported' }
            $key = (Symbol-Key $target)
            if (-not $script:methodIds.ContainsKey($key)) { Refuse 'External or unresolved call' }
            if ($operation.Arguments.Length -ne $target.Parameters.Length) { Refuse 'Call arity mismatch' }
            $args = @()
            foreach ($argument in $operation.Arguments) {
                if ([string]$argument.ArgumentKind -ne 'Explicit' -or $argument.Parameter.Ordinal -ne $args.Count) { Refuse 'Only positional explicit arguments are supported' }
                $value = Read-Operation $argument.Value $next
                if ($value.type -ne (Type-Name $argument.Parameter.Type)) { Refuse 'Call argument type mismatch' }
                $args += $value
            }
            return @{kind='call';type=$type;target=$script:methodIds[$key];arguments=@($args)}
        }
        'Conversion' {
            if (-not $operation.Conversion.IsIdentity -or $null -ne $operation.OperatorMethod -or $operation.IsChecked) { Refuse 'Only identity conversions are supported' }
            $child = Read-Operation $operation.Operand $next
            if ($child.type -ne $type) { Refuse 'Conversion type mismatch' }
            return $child
        }
        'Unary' {
            if ($null -ne $operation.OperatorMethod -or $operation.IsLifted) { Refuse 'Lifted or overloaded unary operation' }
            $operator = [string]$operation.OperatorKind
            if ($operator -eq 'Minus' -and $type -eq 'int' -and $operation.ConstantValue.HasValue -and
                $operation.Syntax -is [Microsoft.CodeAnalysis.CSharp.Syntax.PrefixUnaryExpressionSyntax] -and
                $operation.Syntax.Operand -is [Microsoft.CodeAnalysis.CSharp.Syntax.LiteralExpressionSyntax] -and
                $operation.ConstantValue.Value -is [int]) {
                return @{kind='constant';type='int';value=$operation.ConstantValue.Value}
            }
            if ($operator -ne 'Not' -or $type -ne 'bool' -or $operation.IsChecked) { Refuse 'Only Boolean not and negative int literals are supported' }
            $operand = Read-Operation $operation.Operand $next
            if ($operand.type -ne 'bool') { Refuse 'Boolean not operand mismatch' }
            return @{kind='not';type='bool';operand=$operand}
        }
        'Binary' {
            if ($null -ne $operation.OperatorMethod -or $operation.IsLifted -or $operation.IsChecked) { Refuse 'Lifted, checked or overloaded binary operation' }
            $operators = @{Equals='eq';NotEquals='ne';LessThan='lt';LessThanOrEqual='le';GreaterThan='gt';GreaterThanOrEqual='ge';ConditionalAnd='and';ConditionalOr='or'}
            $operator = [string]$operation.OperatorKind
            if (-not $operators.ContainsKey($operator) -or $type -ne 'bool') { Refuse ('Unsupported binary operation: ' + $operator) }
            $left = Read-Operation $operation.LeftOperand $next
            $right = Read-Operation $operation.RightOperand $next
            if ($left.type -ne $right.type) { Refuse 'Binary operands differ in type' }
            $op = $operators[$operator]
            if ($op -in @('lt','le','gt','ge') -and $left.type -ne 'int') { Refuse 'Ordering requires int operands' }
            if ($op -in @('and','or') -and $left.type -ne 'bool') { Refuse 'Logical operation requires bool operands' }
            return @{kind='binary';type='bool';op=$op;left=$left;right=$right}
        }
        'Conditional' {
            if ($operation.IsRef) { Refuse 'Ref conditional is unsupported' }
            $condition = Read-Operation $operation.Condition $next
            $whenTrue = Read-Operation $operation.WhenTrue $next
            $whenFalse = Read-Operation $operation.WhenFalse $next
            if ($condition.type -ne 'bool' -or $whenTrue.type -ne $type -or $whenFalse.type -ne $type) { Refuse 'Conditional types differ' }
            return @{kind='ite';type=$type;condition=$condition;when_true=$whenTrue;when_false=$whenFalse}
        }
        default { Refuse ('Unsupported operation: ' + [string]$operation.Kind) }
    }
}
function Assert-ExpressionSyntax($expression) {
    if ($null -eq $expression) { Refuse 'Missing pure expression' }
    $nodes = @($expression.DescendantNodesAndSelf())
    if ($nodes.Count -gt 1000) { Refuse 'Expression exceeds 1000 syntax nodes' }
    foreach ($node in $nodes) {
        if ($node -is [Microsoft.CodeAnalysis.CSharp.Syntax.ArgumentSyntax] -and ($null -ne $node.NameColon -or $node.RefKindKeyword.RawKind -ne 0)) { Refuse 'Named/ref arguments are unsupported' }
        if ($node -is [Microsoft.CodeAnalysis.CSharp.Syntax.InvocationExpressionSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.ArgumentListSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.ArgumentSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.MemberAccessExpressionSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.IdentifierNameSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.LiteralExpressionSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.ParenthesizedExpressionSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.BinaryExpressionSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.PrefixUnaryExpressionSyntax] -or
            $node -is [Microsoft.CodeAnalysis.CSharp.Syntax.ConditionalExpressionSyntax]) { continue }
        Refuse ('Unsupported expression syntax: ' + $node.GetType().Name)
    }
}
function Assert-Statement($statement, [int]$depth = 0) {
    if ($null -eq $statement -or $depth -gt 40) { Refuse 'Missing or deeply nested statement' }
    # Validate every statement, including unreachable statements. Nothing is
    # silently discarded until its syntax and pure expression semantics pass.
    switch ([string]$statement.Kind) {
        'Block' {
            if ($statement.Locals.Length -ne 0) { Refuse 'Local declarations are unsupported, including unreachable ones' }
            foreach ($child in $statement.Operations) { Assert-Statement $child ($depth + 1) }
        }
        'Return' {
            if ($statement.Syntax -isnot [Microsoft.CodeAnalysis.CSharp.Syntax.ReturnStatementSyntax] -or $null -eq $statement.ReturnedValue) { Refuse 'Only value-return statements are supported' }
            Assert-ExpressionSyntax $statement.Syntax.Expression
            $value = Read-Operation $statement.ReturnedValue
            if ($value.type -ne $script:expectedReturnType) { Refuse 'Return value type differs from method type' }
        }
        'Conditional' {
            if ($statement.Syntax -isnot [Microsoft.CodeAnalysis.CSharp.Syntax.IfStatementSyntax] -or $null -ne $statement.Type -or $statement.IsRef) { Refuse 'Only ordinary if statements are supported' }
            Assert-ExpressionSyntax $statement.Syntax.Condition
            $condition = Read-Operation $statement.Condition
            if ($condition.type -ne 'bool') { Refuse 'If condition must be bool' }
            Assert-Statement $statement.WhenTrue ($depth + 1)
            if ($null -ne $statement.WhenFalse) { Assert-Statement $statement.WhenFalse ($depth + 1) }
        }
        default { Refuse ('Unsupported statement operation: ' + [string]$statement.Kind) }
    }
}
function Read-Statement($statement, $continuation, [int]$depth = 0) {
    if ($depth -gt 40) { Refuse 'Control nesting exceeds 40' }
    switch ([string]$statement.Kind) {
        'Block' {
            $result = $continuation
            for ($index = $statement.Operations.Length - 1; $index -ge 0; $index--) {
                $result = Read-Statement $statement.Operations[$index] $result ($depth + 1)
            }
            return $result
        }
        'Return' { return (Read-Operation $statement.ReturnedValue) }
        'Conditional' {
            $condition = Read-Operation $statement.Condition
            $whenTrue = Read-Statement $statement.WhenTrue $continuation ($depth + 1)
            $whenFalse = $continuation
            if ($null -ne $statement.WhenFalse) { $whenFalse = Read-Statement $statement.WhenFalse $continuation ($depth + 1) }
            # Roslyn-certified constant bool conditions can discard a pure,
            # already-validated unreachable branch without inventing a return.
            if ($statement.Condition.ConstantValue.HasValue -and $statement.Condition.ConstantValue.Value -is [bool]) {
                if ($statement.Condition.ConstantValue.Value) { return $whenTrue }
                return $whenFalse
            }
            return @{kind='ite';type=$script:expectedReturnType;condition=$condition;when_true=$whenTrue;when_false=$whenFalse}
        }
        default { Refuse 'Statement was not validated before lowering' }
    }
}
function Assert-IrBudget($node, [int]$depth = 0) {
    if ($null -eq $node) { Refuse 'A control-flow path has no return; no fallback value is invented' }
    $script:expandedIrNodes++
    if ($depth -gt 40 -or $script:expandedIrNodes -gt 1000) { Refuse 'Expanded IR exceeds depth or size budget' }
    switch ($node.kind) {
        'call' { foreach ($argument in $node.arguments) { Assert-IrBudget $argument ($depth + 1) }; return }
        'constant' { return }
        'param' { return }
        'not' { Assert-IrBudget $node.operand ($depth + 1); return }
        'binary' { Assert-IrBudget $node.left ($depth + 1); Assert-IrBudget $node.right ($depth + 1); return }
        'ite' { Assert-IrBudget $node.condition ($depth + 1); Assert-IrBudget $node.when_true ($depth + 1); Assert-IrBudget $node.when_false ($depth + 1); return }
        default { Refuse 'Unexpected IR node after lowering' }
    }
}
try {
    if ([bool]$SourcePath -eq [bool]$SourceText) { throw [System.ArgumentException]::new('Supply exactly one of SourcePath or nonempty SourceText') }
    if ($SourcePath) {
        $bytes = [System.IO.File]::ReadAllBytes([System.IO.Path]::GetFullPath($SourcePath))
        $utf8 = [System.Text.UTF8Encoding]::new($false,$true)
        $text = $utf8.GetString($bytes)
        if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    } else {
        $text = $SourceText
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    }
    if ($bytes.Length -gt 65536) { Refuse 'Source exceeds 64 KiB limit' }
    $answer.source_sha256 = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    Add-Type -Path (Join-Path $PSHOME 'Microsoft.CodeAnalysis.dll')
    Add-Type -Path (Join-Path $PSHOME 'Microsoft.CodeAnalysis.CSharp.dll')
    $answer.roslyn_version = [Microsoft.CodeAnalysis.CSharp.CSharpCompilation].Assembly.GetName().Version.ToString()
    $ct = [System.Threading.CancellationToken]::None
    # Pin the supported language version rather than inheriting Roslyn's default.
    $parseOptions = [Microsoft.CodeAnalysis.CSharp.CSharpParseOptions]::Default.WithLanguageVersion([Microsoft.CodeAnalysis.CSharp.LanguageVersion]::CSharp12)
    $tree = [Microsoft.CodeAnalysis.CSharp.CSharpSyntaxTree]::ParseText($text,$parseOptions,'',[System.Text.Encoding]::UTF8,$ct)
    $reference = [Microsoft.CodeAnalysis.MetadataReference]::CreateFromFile([object].Assembly.Location,[Microsoft.CodeAnalysis.MetadataReferenceProperties]::Assembly,$null)
    $options = [Microsoft.CodeAnalysis.CSharp.CSharpCompilationOptions]::new([Microsoft.CodeAnalysis.OutputKind]::DynamicallyLinkedLibrary)
    # Record effective options and the exact runtime/compiler dependencies.
    # No project settings are imported: these are standalone analysis settings.
    $answer.analysis_metadata = [ordered]@{
        extraction_version = 'module-0.2.0'
        supported_syntax = @('literal','parameter','comparison','boolean','conditional-expression','pure-block','if-return','single-namespace-wrapper','resolved-static-internal-call')
        max_syntax_nodes = 1000
        max_control_depth = 40
        max_expanded_ir_nodes = 1000
        language_version = [string]$parseOptions.LanguageVersion
        source_kind = [string]$parseOptions.Kind
        documentation_mode = [string]$parseOptions.DocumentationMode
        check_overflow = [bool]$options.CheckOverflow
        nullable_context = [string]$options.NullableContextOptions
        optimization_level = [string]$options.OptimizationLevel
        allow_unsafe = [bool]$options.AllowUnsafe
        concurrent_build = [bool]$options.ConcurrentBuild
        deterministic = [bool]$options.Deterministic
        corelib_sha256 = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.IO.File]::ReadAllBytes([object].Assembly.Location))).ToLowerInvariant()
        codeanalysis_sha256 = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.IO.File]::ReadAllBytes([Microsoft.CodeAnalysis.Compilation].Assembly.Location))).ToLowerInvariant()
        csharp_sha256 = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.IO.File]::ReadAllBytes([Microsoft.CodeAnalysis.CSharp.CSharpCompilation].Assembly.Location))).ToLowerInvariant()
    }
    $compilation = [Microsoft.CodeAnalysis.CSharp.CSharpCompilation]::Create('MiaReadOnlyAnalysis',[Microsoft.CodeAnalysis.SyntaxTree[]]@($tree),[Microsoft.CodeAnalysis.MetadataReference[]]@($reference),$options)
    $rootMethod = [Microsoft.CodeAnalysis.SyntaxTree].GetMethod('GetRoot',[type[]]@([System.Threading.CancellationToken]))
    $root = $rootMethod.Invoke($tree,[object[]]@($ct))
    $answer.inventory = @($root.DescendantNodes() | Where-Object { $_ -is [Microsoft.CodeAnalysis.CSharp.Syntax.MemberDeclarationSyntax] } | ForEach-Object {
        @{kind=$_.GetType().Name;start=$_.Span.Start;length=$_.Span.Length;text=$_.ToString();status='INVENTORIED'}
    })
    $diagnostics = @($compilation.GetDiagnostics($ct))
    $errors = @($diagnostics | Where-Object { [string]$_.Severity -eq 'Error' })
    $answer.diagnostics = @($diagnostics | Select-Object -First 20 | ForEach-Object { @{id=$_.Id;severity=[string]$_.Severity;message=$_.GetMessage()} })
    if ($errors.Count -gt 0) {
        $answer.reason = 'COMPILATION_ERROR'
    } else {
        # Base-class reflection avoids PowerShell ambiguity on covariant overrides.
        $rootMethod = [Microsoft.CodeAnalysis.SyntaxTree].GetMethod('GetRoot',[type[]]@([System.Threading.CancellationToken]))
        $root = $rootMethod.Invoke($tree,[object[]]@($ct))
        if ($root.ContainsDirectives -or $root.AttributeLists.Count -ne 0 -or $root.Externs.Count -ne 0 -or $root.Usings.Count -ne 0 -or $root.Members.Count -ne 1) { Refuse 'Require one class without directives, using, extern or assembly attributes' }
        $class = $root.Members[0]
        if ($class -is [Microsoft.CodeAnalysis.CSharp.Syntax.NamespaceDeclarationSyntax] -or
            $class -is [Microsoft.CodeAnalysis.CSharp.Syntax.FileScopedNamespaceDeclarationSyntax]) {
            if ($class.Usings.Count -ne 0 -or $class.Externs.Count -ne 0 -or $class.Members.Count -ne 1) {
                Refuse 'Namespace wrapper must contain exactly one class and no using or extern directives'
            }
            $class = $class.Members[0]
        }
        if ($class -isnot [Microsoft.CodeAnalysis.CSharp.Syntax.ClassDeclarationSyntax]) { Refuse 'Root member must be a class' }
        if ($class.AttributeLists.Count -ne 0 -or $null -ne $class.TypeParameterList -or $null -ne $class.BaseList -or $class.ConstraintClauses.Count -ne 0 -or $class.Members.Count -lt 1 -or $class.Members.Count -gt 8 -or $null -ne $class.ParameterList) { Refuse 'Class attributes, generic/base/primary-constructor types or more than eight members are unsupported' }
        foreach ($modifier in $class.Modifiers) { if ($modifier.Text -notin @('public','internal','static')) { Refuse 'Unsupported class modifier' } }
        $modelMethod = [Microsoft.CodeAnalysis.Compilation].GetMethod('GetSemanticModel',[type[]]@([Microsoft.CodeAnalysis.SyntaxTree],[bool]))
        $model = $modelMethod.Invoke($compilation,[object[]]@($tree,$false))
        $script:methodIds = [System.Collections.Generic.Dictionary[string,string]]::new([System.StringComparer]::Ordinal)
        $methodNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        foreach ($member in $class.Members) {
            if ($member -isnot [Microsoft.CodeAnalysis.CSharp.Syntax.MethodDeclarationSyntax]) { Refuse 'All class members must be methods; none silently dropped' }
            $symbol = [Microsoft.CodeAnalysis.CSharp.CSharpExtensions]::GetDeclaredSymbol($model,[Microsoft.CodeAnalysis.CSharp.Syntax.BaseMethodDeclarationSyntax]$member,$ct)
            if (-not $methodNames.Add([string]$symbol.Name)) { Refuse 'Overloaded or duplicate method names are unsupported' }
            $script:methodIds[(Symbol-Key $symbol)] = 'u' + $script:methodIds.Count
            $answer.class_name = (Symbol-Key $symbol.ContainingType)
        }
        foreach ($method in $class.Members) {
        if ($method -isnot [Microsoft.CodeAnalysis.CSharp.Syntax.MethodDeclarationSyntax]) { Refuse 'Class member must be a method' }
        if ($method.AttributeLists.Count -ne 0 -or $null -ne $method.TypeParameterList -or $method.ConstraintClauses.Count -ne 0 -or $null -ne $method.ExplicitInterfaceSpecifier) { Refuse 'Method attributes, generics and explicit interfaces are unsupported' }
        foreach ($modifier in $method.Modifiers) { if ($modifier.Text -notin @('public','private','internal','static')) { Refuse 'Unsupported method modifier' } }
        if ($method.Identifier.ValueText -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { Refuse 'Method name must be ASCII identifier' }
        $modelMethod = [Microsoft.CodeAnalysis.Compilation].GetMethod('GetSemanticModel',[type[]]@([Microsoft.CodeAnalysis.SyntaxTree],[bool]))
        $model = $modelMethod.Invoke($compilation,[object[]]@($tree,$false))
        $symbol = [Microsoft.CodeAnalysis.CSharp.CSharpExtensions]::GetDeclaredSymbol($model,[Microsoft.CodeAnalysis.CSharp.Syntax.BaseMethodDeclarationSyntax]$method,$ct)
        if (-not $symbol.IsStatic -or $symbol.IsGenericMethod -or $symbol.ReturnsByRef -or $symbol.ReturnsByRefReadonly -or $symbol.IsAsync -or $symbol.IsExtern -or $symbol.Parameters.Length -lt 1 -or $symbol.Parameters.Length -gt 3) { Refuse 'Require static non-generic value-returning method with one to three parameters' }
        $returnType = Type-Name $symbol.ReturnType
        $script:expectedReturnType = $returnType
        $script:parameterTypes = [System.Collections.Generic.Dictionary[string,string]]::new([System.StringComparer]::Ordinal)
        $parameters = @()
        foreach ($parameter in $symbol.Parameters) {
            $syntax = $method.ParameterList.Parameters[$parameter.Ordinal]
            if ([string]$parameter.RefKind -ne 'None' -or $parameter.IsParams -or $parameter.IsOptional -or $parameter.HasExplicitDefaultValue -or $syntax.Modifiers.Count -ne 0 -or $syntax.AttributeLists.Count -ne 0 -or $null -ne $syntax.Default) { Refuse 'Parameter modifiers, attributes and defaults are unsupported' }
            $name = [string]$parameter.Name
            if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$' -or $script:parameterTypes.ContainsKey($name)) { Refuse 'Invalid or duplicate ASCII parameter name' }
            $type = Type-Name $parameter.Type
            $script:parameterTypes[$name] = $type
            $parameters += @{name=$name;type=$type}
        }
        if (@($method.DescendantNodesAndSelf()).Count -gt 1000) { Refuse 'Method exceeds 1000 syntax nodes' }
        if ($null -ne $method.ExpressionBody -and $null -eq $method.Body) {
            $expression = $method.ExpressionBody.Expression
            Assert-ExpressionSyntax $expression
            while ($expression -is [Microsoft.CodeAnalysis.CSharp.Syntax.ParenthesizedExpressionSyntax]) { $expression = $expression.Expression }
            $body = Read-Operation ($model.GetOperation($expression,$ct))
        } elseif ($null -ne $method.Body -and $null -eq $method.ExpressionBody) {
            $block = $model.GetOperation($method.Body,$ct)
            Assert-Statement $block
            $body = Read-Statement $block $null
        } else { Refuse 'Method requires an expression body or a pure block' }
        $script:expandedIrNodes = 0
        Assert-IrBudget $body
        if ($body.type -ne $returnType) { Refuse 'Return type mismatch' }
        $answer.units += @{id=$script:methodIds[(Symbol-Key $symbol)];name=[string]$symbol.Name;qualified_name=(Symbol-Key $symbol.ContainingType)+'.'+[string]$symbol.Name;return_type=$returnType;parameters=@($parameters);body=$body;contract=$null}
        }
        $answer.status = 'TRANSLATED'
        $answer.reason = 'SUPPORTED_SUBSET'
    }
} catch [System.NotSupportedException] {
    $answer.reason = 'UNSUPPORTED_SYNTAX'
    $answer.diagnostics = @(@{id='MIA_UNSUPPORTED';severity='Info';message=$_.Exception.Message})
} catch [System.IO.IOException] {
    $answer.reason = 'SOURCE_ERROR'
    $answer.diagnostics = @(@{id='MIA_SOURCE';severity='Error';message=$_.Exception.Message})
} catch [System.ArgumentException] {
    $answer.reason = 'SOURCE_ERROR'
    $answer.diagnostics = @(@{id='MIA_SOURCE';severity='Error';message=$_.Exception.Message})
} catch {
    $answer.reason = 'INTERNAL_ERROR'
    $answer.diagnostics = @(@{id='MIA_INTERNAL';severity='Error';message=$_.Exception.Message})
}
$answer | ConvertTo-Json -Depth 100 -Compress
