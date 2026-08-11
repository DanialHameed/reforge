# Local API: listen on all interfaces so browsers / other stacks can reach the API reliably.
# Prefer the project venv so deps match requirements.txt.
Set-Location $PSScriptRoot
$uvicorn = Join-Path $PSScriptRoot ".venv\Scripts\uvicorn.exe"
if (Test-Path $uvicorn) {
    & $uvicorn main:app --reload --host 0.0.0.0 --port 8000
}
else {
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
}
