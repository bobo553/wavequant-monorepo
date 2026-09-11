param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$OutputDir
)
$ErrorActionPreference = 'Stop'
$taskSource = (Resolve-Path -LiteralPath $Source).Path
if (Test-Path -LiteralPath $OutputDir) { throw 'Choose a new extraction directory.' }
$taskOutput = (New-Item -ItemType Directory -Path $OutputDir).FullName
$taskBefore = (Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash
$taskPpt = $null
$taskDeck = $null
function Get-ShapeTexts($Shapes) {
    foreach ($taskShape in $Shapes) {
        if ($taskShape.Type -eq 6) { Get-ShapeTexts $taskShape.GroupItems }
        if ($taskShape.HasTextFrame -eq -1 -and $taskShape.TextFrame.HasText -eq -1) {
            [pscustomobject]@{name=$taskShape.Name; left=$taskShape.Left; top=$taskShape.Top; text=$taskShape.TextFrame.TextRange.Text}
        }
        if ($taskShape.HasTable -eq -1) {
            for ($taskRow=1; $taskRow -le $taskShape.Table.Rows.Count; $taskRow++) {
                for ($taskCol=1; $taskCol -le $taskShape.Table.Columns.Count; $taskCol++) {
                    [pscustomobject]@{name="table_${taskRow}_${taskCol}"; text=$taskShape.Table.Cell($taskRow,$taskCol).Shape.TextFrame.TextRange.Text}
                }
            }
        }
    }
}
try {
    # Document object model only: read-only, no editing window, no Save on source.
    $taskPpt = New-Object -ComObject PowerPoint.Application
    $taskDeck = $taskPpt.Presentations.Open($taskSource, -1, 0, 0)
    $taskSlides = @()
    foreach ($taskSlide in $taskDeck.Slides) {
        $taskImage = Join-Path $taskOutput ('slide_{0:D3}.png' -f $taskSlide.SlideIndex)
        $taskSlide.Export($taskImage, 'PNG', 1440, 1080)
        $taskSlides += [pscustomobject]@{slide=$taskSlide.SlideIndex; hidden=$taskSlide.SlideShowTransition.Hidden; shapes=@(Get-ShapeTexts $taskSlide.Shapes); notes=@(Get-ShapeTexts $taskSlide.NotesPage.Shapes); image=$taskImage}
    }
    [pscustomobject]@{source=$taskSource; sha256=$taskBefore; count=$taskSlides.Count; slides=$taskSlides} |
        ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $taskOutput 'slides.json') -Encoding utf8
    $taskSlides | ForEach-Object { "PAGE $($_.slide)`n$(($_.shapes.text) -join "`n")`n" }
}
finally {
    if ($null -ne $taskDeck) { $taskDeck.Close() }
    if ($null -ne $taskPpt -and $taskPpt.Presentations.Count -eq 0) { $taskPpt.Quit() }
    if ((Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash -ne $taskBefore) { throw 'Source file hash changed.' }
}
