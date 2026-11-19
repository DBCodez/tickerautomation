# Persistent Read-Only UI Automation & WinRT OCR Sidecar (PowerShell 5.1+ STA)
# Speaks single-line UTF-8 JSON over stdin/stdout so queries run in ~15-150ms
# with zero cold-start overhead and zero external Python packages.
# STRICT SAFETY: Read-only queries ONLY. Never invokes buttons or mutates controls.

$ErrorActionPreference = "Stop"
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Align PowerShell STA thread to Per-Monitor DPI v2 (-4) so mixed-DPI monitors match physical coords 1:1
try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class DpiBridge {
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
}
"@ -ErrorAction SilentlyContinue
    $null = [DpiBridge]::SetProcessDpiAwarenessContext([IntPtr](-4))
    $null = [DpiBridge]::SetThreadDpiAwarenessContext([IntPtr](-4))
} catch {}

$uiaReady  = $false
$ocrReady  = $false
$ocrLang   = ""
$ocrMaxDim = 4096
$ocrEngine = $null
$script:WinRtTimedOut = $false

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Drawing
    $uiaReady = $true
} catch {
    $uiaReady = $false
}

# Load WinRT Windows.Media.Ocr & Windows.Graphics.Imaging if available on Windows 10/11
try {
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]

    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskMethods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }
    $script:AsTaskGeneric = $asTaskMethods[0]

    function Await-WinRt($WinRtTask, [Type]$ResultType, [int]$WaitMs = 2200) {
        $effMs = [Math]::Max(150, $WaitMs)
        $generic = $script:AsTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $generic.Invoke($null, @($WinRtTask))
        if (-not $netTask.Wait($effMs)) {
            $script:WinRtTimedOut = $true
            throw "WINRT_TIMEOUT"
        }
        return $netTask.GetAwaiter().GetResult()
    }

    try {
        $md = [int][Windows.Media.Ocr.OcrEngine]::MaxImageDimension
        if ($md -ge 1024) { $ocrMaxDim = $md }
    } catch {}

    $ocrEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $ocrEngine) {
        $lang = New-Object Windows.Globalization.Language("en-US")
        if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) {
            $ocrEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        }
    }
    if ($null -ne $ocrEngine) {
        $ocrReady = $true
        $ocrLang = $ocrEngine.RecognizerLanguage.LanguageTag
    }
} catch {
    $ocrReady = $false
}

function Get-ElementSummary($el) {
    if ($null -eq $el) {
        return @{ found = $false }
    }
    $cur = $el.Current
    $ctName = ""
    try { $ctName = $cur.ControlType.ProgrammaticName } catch {}
    $valText = $null
    $hasValuePattern = $false
    try {
        $vpObj = $null
        if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vpObj)) {
            $hasValuePattern = $true
            $valText = $vpObj.Current.Value
        }
    } catch {}

    $rc = $cur.BoundingRectangle
    $rectArr = @(0, 0, 0, 0)
    if (-not [double]::IsInfinity($rc.Left) -and -not [double]::IsNaN($rc.Left)) {
        $rectArr = @([int]$rc.Left, [int]$rc.Top, [int]$rc.Width, [int]$rc.Height)
    }

    $parCt = ""
    $parAutoId = ""
    $parName = ""
    $parRectArr = @(0, 0, 0, 0)
    try {
        $parEl = [System.Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($el)
        if ($null -ne $parEl) {
            $parCur = $parEl.Current
            try { $parCt = [string]$parCur.ControlType.ProgrammaticName } catch {}
            try { $parAutoId = [string]$parCur.AutomationId } catch {}
            try { $parName = [string]$parCur.Name } catch {}
            try {
                $prc = $parCur.BoundingRectangle
                if (-not [double]::IsInfinity($prc.Left) -and -not [double]::IsNaN($prc.Left)) {
                    $parRectArr = @([int]$prc.Left, [int]$prc.Top, [int]$prc.Width, [int]$prc.Height)
                }
            } catch {}
        }
    } catch {}

    return @{
        found                = $true
        control_type         = [string]$ctName
        automation_id        = [string]$cur.AutomationId
        name                 = [string]$cur.Name
        class_name           = [string]$cur.ClassName
        process_id           = [int]$cur.ProcessId
        is_enabled           = [bool]$cur.IsEnabled
        is_offscreen         = [bool]$cur.IsOffscreen
        has_value_pattern    = [bool]$hasValuePattern
        value                = if ($null -ne $valText) { [string]$valText } else { "" }
        rect                 = $rectArr
        parent_control_type  = [string]$parCt
        parent_automation_id = [string]$parAutoId
        parent_name          = [string]$parName
        parent_rect          = $parRectArr
    }
}

function Run-OcrOnBitmap([System.Drawing.Bitmap]$bmp, [double]$scaleFactor, [System.Diagnostics.Stopwatch]$sw, [int]$deadlineMs) {
    $ms = New-Object System.IO.MemoryStream
    try {
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Bmp)
        $ras = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        try {
            $writer = New-Object Windows.Storage.Streams.DataWriter($ras.GetOutputStreamAt(0))
            $writer.WriteBytes($ms.ToArray())
            $remMs = [Math]::Max(180, $deadlineMs - [int]$sw.ElapsedMilliseconds)
            $null = Await-WinRt ($writer.StoreAsync()) ([uint32]) $remMs
            $null = $writer.DetachStream()
            $writer.Dispose()
            $ras.Seek(0)

            $remMs = [Math]::Max(180, $deadlineMs - [int]$sw.ElapsedMilliseconds)
            $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder]) $remMs
            $remMs = [Math]::Max(180, $deadlineMs - [int]$sw.ElapsedMilliseconds)
            $softBmp = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap]) $remMs
            try {
                $remMs = [Math]::Max(180, $deadlineMs - [int]$sw.ElapsedMilliseconds)
                $ocrRes = Await-WinRt ($ocrEngine.RecognizeAsync($softBmp)) ([Windows.Media.Ocr.OcrResult]) $remMs
                $wordList = New-Object 'System.Collections.Generic.List[object]'
                $chromeLines = New-Object 'System.Collections.Generic.List[int]'
                $li = 0
                foreach ($ln in $ocrRes.Lines) {
                    if ([string]$ln.Text -match '(?i)(thinkorswim|flexible\s+grid|\[build)') {
                        $chromeLines.Add($li)
                    }
                    $wi = 0
                    foreach ($wd in $ln.Words) {
                        $br = $wd.BoundingRect
                        $wordList.Add(@{
                            text     = [string]$wd.Text
                            line_idx = $li
                            word_idx = $wi
                            rect     = @(
                                [math]::Round(($br.X / $scaleFactor), 1),
                                [math]::Round(($br.Y / $scaleFactor), 1),
                                [math]::Round(($br.Width / $scaleFactor), 1),
                                [math]::Round(($br.Height / $scaleFactor), 1)
                            )
                        })
                        $wi += 1
                    }
                    $li += 1
                }
                return @{
                    text         = [string]$ocrRes.Text
                    words        = @($wordList.ToArray())
                    chrome_lines = @($chromeLines.ToArray())
                }
            } finally {
                if (-not $script:WinRtTimedOut -and $null -ne $softBmp) { $softBmp.Dispose() }
            }
        } finally {
            if (-not $script:WinRtTimedOut -and $null -ne $ras) { $ras.Dispose() }
        }
    } finally {
        if (-not $script:WinRtTimedOut -and $null -ne $ms) { $ms.Dispose() }
    }
}

function Capture-And-Ocr([int]$x, [int]$y, [int]$w, [int]$h, [int]$reqScale, [string]$mode, [int]$deadlineMs = 2200) {
    if (-not $ocrReady -or $w -lt 8 -or $h -lt 8) {
        return @{ ok = $false; error = "OCR unavailable or invalid rect" }
    }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $scale = if ($reqScale -ge 1 -and $reqScale -le 3) { $reqScale } else { 3 }
    while ($scale -gt 1 -and (($w * $scale) -gt $ocrMaxDim -or ($h * $scale) -gt $ocrMaxDim)) {
        $scale -= 1
    }
    if ($w -gt $ocrMaxDim -or $h -gt $ocrMaxDim) {
        return @{ ok = $false; error = "Rect exceeds OcrEngine.MaxImageDimension ($ocrMaxDim)" }
    }

    $srcBmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $upBmp  = $null
    $invBmp = $null
    try {
        $gSrc = [System.Drawing.Graphics]::FromImage($srcBmp)
        try {
            $gSrc.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)), [System.Drawing.CopyPixelOperation]::SourceCopy)
        } finally {
            $gSrc.Dispose()
        }

        $workBmp = $srcBmp
        if ($scale -gt 1) {
            $upBmp = New-Object System.Drawing.Bitmap(($w * $scale), ($h * $scale), [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
            $gUp = [System.Drawing.Graphics]::FromImage($upBmp)
            try {
                $gUp.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $gUp.DrawImage($srcBmp, 0, 0, ($w * $scale), ($h * $scale))
            } finally {
                $gUp.Dispose()
            }
            $workBmp = $upBmp
        }

        $txtNorm = ""
        $wdsNorm = @()
        $chrNorm = @()
        if ($mode -ne "inverted") {
            $resNormal = Run-OcrOnBitmap $workBmp ([double]$scale) $sw $deadlineMs
            $txtNorm = [string]$resNormal.text
            $wdsNorm = $resNormal.words
            $chrNorm = $resNormal.chrome_lines
        }

        $txtInv = ""
        $wdsInv = @()
        $chrInv = @()
        if ($mode -ne "normal") {
            $bw = $workBmp.Width
            $bh = $workBmp.Height
            $invBmp = New-Object System.Drawing.Bitmap($bw, $bh, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
            $cm = New-Object System.Drawing.Imaging.ColorMatrix
            $cm.Matrix00 = -1; $cm.Matrix11 = -1; $cm.Matrix22 = -1
            $cm.Matrix33 = 1;  $cm.Matrix40 = 1;  $cm.Matrix41 = 1; $cm.Matrix42 = 1; $cm.Matrix44 = 1
            $ia = New-Object System.Drawing.Imaging.ImageAttributes
            $ia.SetColorMatrix($cm)
            $gInv = [System.Drawing.Graphics]::FromImage($invBmp)
            try {
                $destRect = New-Object System.Drawing.Rectangle(0, 0, $bw, $bh)
                $gInv.DrawImage($workBmp, $destRect, 0, 0, $bw, $bh, [System.Drawing.GraphicsUnit]::Pixel, $ia)
            } finally {
                $gInv.Dispose()
                $ia.Dispose()
            }
            $resInverted = Run-OcrOnBitmap $invBmp ([double]$scale) $sw $deadlineMs
            $txtInv = [string]$resInverted.text
            $wdsInv = $resInverted.words
            $chrInv = $resInverted.chrome_lines
        }

        return @{
            ok              = $true
            crop_w          = $w
            crop_h          = $h
            scale_used      = $scale
            text_normal     = $txtNorm
            words_normal    = $wdsNorm
            chrome_normal   = $chrNorm
            text_inverted   = $txtInv
            words_inverted  = $wdsInv
            chrome_inverted = $chrInv
        }
    } finally {
        if (-not $script:WinRtTimedOut) {
            if ($null -ne $srcBmp) { $srcBmp.Dispose() }
            if ($null -ne $upBmp)  { $upBmp.Dispose() }
            if ($null -ne $invBmp) { $invBmp.Dispose() }
        }
    }
}

while ($null -ne ($line = [Console]::In.ReadLine())) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0) { continue }
    $reqId = 0
    try {
        $req = $trimmed | ConvertFrom-Json
        if ($null -ne $req.id) { $reqId = [int]$req.id }
        $cmd = [string]$req.cmd
        if ($cmd -eq "quit") {
            break
        } elseif ($cmd -eq "ping") {
            $resp = @{
                ok          = $true
                uia_ready   = $uiaReady
                ocr_ready   = $ocrReady
                ocr_lang    = $ocrLang
                ocr_max_dim = $ocrMaxDim
            }
        } elseif ($cmd -eq "uia_focused") {
            if (-not $uiaReady) {
                $resp = @{ ok = $false; error = "UIA not loaded" }
            } else {
                $el = [System.Windows.Automation.AutomationElement]::FocusedElement
                $resp = @{ ok = $true; element = (Get-ElementSummary $el) }
            }
        } elseif ($cmd -eq "uia_at_point") {
            if (-not $uiaReady) {
                $resp = @{ ok = $false; error = "UIA not loaded" }
            } else {
                $pt = New-Object System.Windows.Point([double]$req.x, [double]$req.y)
                $el = [System.Windows.Automation.AutomationElement]::FromPoint($pt)
                $resp = @{ ok = $true; element = (Get-ElementSummary $el) }
            }
        } elseif ($cmd -eq "ocr_rect") {
            $sc = if ($null -ne $req.scale) { [int]$req.scale } else { 3 }
            $md = if ($null -ne $req.mode -and [string]$req.mode -ne "") { [string]$req.mode } else { "both" }
            $dlMs = if ($null -ne $req.deadline_ms) { [int]$req.deadline_ms } else { 2200 }
            $resp = Capture-And-Ocr ([int]$req.x) ([int]$req.y) ([int]$req.w) ([int]$req.h) $sc $md $dlMs
        } else {
            $resp = @{ ok = $false; error = "Unknown cmd: $cmd" }
        }
    } catch {
        if ($script:WinRtTimedOut) {
            $errJson = (@{ id = $reqId; ok = $false; error = "WinRT OCR timeout" } | ConvertTo-Json -Compress)
            [Console]::Out.WriteLine($errJson)
            [Console]::Out.Flush()
            break
        }
        $resp = @{ ok = $false; error = $_.Exception.Message }
    }
    $resp["id"] = $reqId
    $jsonOut = $resp | ConvertTo-Json -Compress -Depth 6
    [Console]::Out.WriteLine($jsonOut)
    [Console]::Out.Flush()
}
