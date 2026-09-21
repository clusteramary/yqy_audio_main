param([ValidateSet('test','demo','ranking','state')][string]$Task = 'demo')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Create a Python 3.9+ venv in .venv first: python -m venv .venv' }
switch ($Task) {
    'test' { & $pythonPath -X utf8 -m unittest discover -s tests -v }
    'demo' { & $pythonPath -X utf8 -m interview_robot replay }
    'ranking' { & $pythonPath -X utf8 -m interview_robot evaluate-ranking }
    'state' { & $pythonPath -X utf8 -m interview_robot evaluate-state }
}
exit $LASTEXITCODE
