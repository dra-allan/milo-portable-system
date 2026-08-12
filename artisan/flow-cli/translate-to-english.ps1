# Milo Flow CLI English migration
# Copyright (c) 2026 Daada Allan
# Run from artisan/flow-cli after reviewing the replacements.
$ErrorActionPreference = 'Stop'
$files = @('gen.ts','job.ts','media.ts','_shared.ts') | ForEach-Object { Join-Path $PSScriptRoot $_ }
$replacements = [ordered]@{
  '用 Google Omni 生成视频（纯文字 / 多参考图，--refs 引用别名 / 路径 / mediaId）' = 'Generate a video with Google Omni (text or multiple references; --refs accepts aliases, paths, or media IDs)'
  '生成提示词' = 'Generation prompt'
  '视频时长秒数：4 / 6 / 8 / 10' = 'Video length in seconds: 4 / 6 / 8 / 10'
  '一次生成的样本数；每多 1 个加一份积分' = 'Number of samples to generate; each extra sample costs another credit charge'
  '项目 ID；不传则用 flow project-use 设置的默认值或当前页面' = 'Project ID; defaults to flow project-use or the current page'
  '强制指定模型；默认根据 --refs/--refVideo 自动选。友好别名：edit / t2v-8s / r2v-8s 等' = 'Force a model; selected automatically from --refs/--refVideo. Aliases include edit, t2v-8s, and r2v-8s'
  '随机种子，默认随机' = 'Random seed; random by default'
  '只算积分不真发请求' = 'Calculate credits without submitting'
  '跳过 dry-run 确认，直接提交（agent 用）' = 'Skip dry-run confirmation and submit immediately'
  '提交前先 reload Flow 页面（拿 fresh reCAPTCHA / session，规避 PUBLIC_ERROR_UNUSUAL_ACTIVITY 风控）' = 'Reload the Flow page before submission to refresh reCAPTCHA and session state'
  '参考图列表，逗号分隔；每个 token 可以是：别名 / 本地图片路径 / mediaId UUID。示例：--refs cat,./bg.jpg,9a42af9d-... （含路径会自动上传去重）' = 'Reference images, comma-separated. Each token can be an alias, local image path, or media ID UUID'
  '参考视频（触发视频编辑模式 abra_edit，40 积分固定）。可填别名/本地视频路径/mediaId UUID。例：--refVideo ./clip.mp4 --prompt "改成晚上"' = 'Reference video for abra_edit editing mode (fixed 40 credits). Accepts an alias, local path, or media ID UUID'
  '状态' = 'Status'; '任务ID(短)' = 'Job ID (short)'; '消耗积分' = 'Credits'; '余额' = 'Balance'; '备注' = 'Notes'
  '未指定项目 ID。请先运行 flow project-use 或在 Flow 网页打开一个项目' = 'No project ID. Run flow project-use or open a Flow project in Chrome first'
  '参考视频 1 段（' = '1 reference video ('; '）→ 视频编辑模式' = ') → video editing mode'
  '参考图 ' = 'Reference images '; ' 张（' = ' ('; '）' = ')'; '无参考素材' = 'No reference media'
  '🔍 试算' = '🔍 Dry run'; '⚠️ 待确认' = '⚠️ Awaiting confirmation'; '如果真发' = 'if submitted'; '试算模式未提交；' = 'Dry run; nothing submitted;'; '加 --yes 才真正提交；' = 'Add --yes to submit;'; '加 --yes 真发；不带 --yes 也只是预览不扣费' = 'Add --yes to submit; without it this is only a preview'; '确认无误 → 复用同样参数加 --yes 提交' = 'Review the preview, then repeat with --yes to submit'; '已进队列' = 'Queued'
  '查询某个生成任务的当前状态（按任务 ID）' = 'Show the current status of a generation job'; '等待视频生成任务结束（轮询，直到完成 / 失败 / 超时）' = 'Wait for a video generation job to finish'; '下载已生成完成的视频 mp4 到本地' = 'Download a completed MP4'; '列出当前项目的视频生成任务（按时间倒序）' = 'List video generation jobs for the current project'
  '未找到' = 'Not found'; '任务可能还在排队，或不在当前项目里' = 'The job may still be queued or may not belong to the current project'; '项目 ID' = 'Project ID'; '默认' = 'Default'; '完整 UUID' = 'full UUID'; '保存路径（含 .mp4 文件名）' = 'Output path, including the .mp4 filename'; '返回条数，按时间倒序' = 'Number of results, newest first'; '过滤状态：done(已完成) / failed(失败) / pending(等待中)；不传显示全部' = 'Filter: done, failed, or pending; omit to show all'; '不截断提示词。配合 `-f csv` 可看完整内容' = 'Do not truncate prompts; use with -f csv for full text'; '下载：' = 'Download: '; '本地预览：' = 'Preview locally: '
  '文件不存在: ' = 'File not found: '; '上传响应缺少 media.name: ' = 'Upload response is missing media.name: '; '上传图片或视频到 Flow（自动 sha256 去重；视频走 chunked resumable）' = 'Upload an image or video to Flow (SHA-256 dedupe; chunked resumable upload for video)'; '本地图片路径' = 'Local image or video path'; '别名（gen --refs 时可直接用别名引用，跨命令持久）' = 'Alias for later --refs use'; '别名' = 'Alias'; '原文件' = 'Original file'; '尺寸' = 'Dimensions'; '处理方式' = 'Upload mode'; '缓存复用' = 'Cache reused'; '新上传' = 'New upload'; '文件锁超时' = 'File lock timed out'; '另一个进程可能卡住了' = 'Another process may be stuck'; '参考素材 token 为空' = 'Reference media token is empty'; '只接受图片' = 'Only images are accepted'; '视频请用 --ref-video' = 'Use --ref-video for videos'; '无法解析参考素材' = 'Unable to resolve reference media'; '不是 UUID、本地文件，也不是当前项目下的别名' = 'not a UUID, local file, or alias in the current project'; '参考素材' = 'reference media'; '不能为空' = ' cannot be empty'; '只接受视频文件' = 'Only video files are accepted'; '图片请用 --refs' = 'Use --refs for images'; '无法解析视频' = 'Unable to resolve video'; '列表' = 'List'; '上传时间' = 'Uploaded'; '引用：' = 'Use: '; '建议下次加' = 'Tip: add'; '让后续' = 'so later'; '用别名' = 'can use the alias'; '列出已上传/缓存的图片素材（按当前项目隔离；含别名映射）' = 'List uploaded and cached project media'; '在 gen 时引用：' = 'Use in gen: '; '未拿到 access_token；请确保 Chrome 已登录 Flow' = 'Could not read an access token; make sure Chrome is signed in to Flow'; '未能从 page 获取 labs.google cookie；Flow 可能未登录' = 'Could not read labs.google cookies; Flow may not be signed in'; '请用 --projectId 提供完整的项目 UUID' = 'Provide a full project UUID with --projectId'; '等待文件锁超时' = 'File lock timed out'; '项目' = 'project'
}
foreach ($file in $files) {
  $text = Get-Content -Raw -LiteralPath $file
  foreach ($pair in $replacements.GetEnumerator()) { $text = $text.Replace($pair.Key, $pair.Value) }
  Set-Content -LiteralPath $file -Value $text -Encoding utf8NoBOM
  Write-Host "Translated $([IO.Path]::GetFileName($file))"
}
Write-Host 'Review the diff, then run npm install/build checks before committing.'
