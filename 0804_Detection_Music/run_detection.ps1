param(
    [string]$Port = "COM4",
    [switch]$NoGui
)

$entryPoint = Join-Path $PSScriptRoot "detection_music_runtime\python\main.py"
$arguments = @($entryPoint, "--port", $Port)
if ($NoGui) {
    $arguments += "--no-gui"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    & $python.Source @arguments
    exit $LASTEXITCODE
}

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pythonLauncher) {
    & $pythonLauncher.Source -3 @arguments
    exit $LASTEXITCODE
}

throw "未找到 Python。请先安装 Python 3，并安装 detection_music_runtime\requirements.txt。"

