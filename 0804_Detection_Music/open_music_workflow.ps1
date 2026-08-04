$page = Join-Path $PSScriptRoot "music_generation_workflow\frontend\index.html"
if (-not (Test-Path -LiteralPath $page)) {
    throw "未找到音乐工作流前端：$page"
}

Start-Process -FilePath $page

