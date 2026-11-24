$ErrorActionPreference = 'Stop'
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
& python -B (Join-Path $PSScriptRoot 'generate_materials.py') --check --emit-checks
if ($LASTEXITCODE -ne 0) { throw 'Offline MCP material consistency check failed.' }
$manifest = Get-Content -LiteralPath (Join-Path $repoRoot '.planning/bootstrap/mcp-schema-checks/manifest.json') -Raw | ConvertFrom-Json
$failures = @()
foreach ($case in $manifest.checks) {
    $payload = Get-Content -LiteralPath $case.file -Raw
    $actual = Test-Json -Json $payload -SchemaFile $case.schema -ErrorAction SilentlyContinue
    if ([bool]$actual -ne [bool]$case.expected_valid) {
        $failures += $case.id
        Write-Output ('FAIL ' + $case.id + ' expected=' + $case.expected_valid + ' actual=' + $actual)
    }
}
if ($failures.Count -gt 0) { throw ('MCP schema checks failed: ' + ($failures -join ', ')) }
$groups = $manifest.checks | Group-Object -Property group | ForEach-Object { $_.Name + '=' + $_.Count }
Write-Output ('PASS schema checks: ' + $manifest.checks.Count + ' (' + ($groups -join ', ') + ')')
Write-Output ('Non-snapshot projection not_applicable: ' + $manifest.non_snapshot_projection.Count)
Write-Output 'Only offline schema/projection/data evidence; no SDK, MCP service, client, scope or DPAPI executed.'
