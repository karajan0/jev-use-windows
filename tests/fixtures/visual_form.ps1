Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Jev Visual Regression Fixture'
$form.ClientSize = New-Object System.Drawing.Size(800, 520)
$form.StartPosition = 'CenterScreen'
$form.KeyPreview = $true
$canvas = New-Object System.Windows.Forms.Panel
$canvas.Dock = 'Fill'
$canvas.BackColor = [System.Drawing.Color]::FromArgb(15, 15, 25)
$canvas.GetType().GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'Instance,NonPublic').SetValue($canvas, $true, $null)
$form.Controls.Add($canvas)
$status = New-Object System.Windows.Forms.Label
$status.Text = 'Ready'
$status.ForeColor = [System.Drawing.Color]::White
$status.Font = New-Object System.Drawing.Font('Segoe UI', 18)
$status.Location = New-Object System.Drawing.Point(20, 20)
$status.Size = New-Object System.Drawing.Size(700, 50)
$canvas.Controls.Add($status)
$icon = [System.Drawing.Image]::FromFile((Join-Path $PSScriptRoot 'launch.png'))
$drop = [System.Drawing.Image]::FromFile((Join-Path $PSScriptRoot 'dropzone.png'))
$script:iconX = 150
$script:ticks = 0
$script:moving = $true
$script:dragging = $false
$script:keyStarted = $null
$canvas.Add_Paint({
    param($sender, $event)
    $event.Graphics.DrawImageUnscaled($icon, $script:iconX, 160)
    $event.Graphics.DrawImageUnscaled($drop, 650, 160)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(($script:ticks * 13 % 255), 80, 160))
    $event.Graphics.FillRectangle($brush, 20, 340, 760, 140)
    $brush.Dispose()
})
$canvas.Add_MouseDown({
    param($sender, $event)
    if ($event.X -ge $script:iconX -and $event.X -lt ($script:iconX + 48) -and $event.Y -ge 160 -and $event.Y -lt 208) {
        $script:dragging = $true
        $script:moving = $false
    }
})
$canvas.Add_MouseUp({
    param($sender, $event)
    if ($script:dragging) {
        if ($event.X -ge 650 -and $event.X -lt 698 -and $event.Y -ge 160 -and $event.Y -lt 208) {
            $status.Text = 'Dropped successfully'
        } else {
            $status.Text = 'Launched successfully'
        }
        $script:dragging = $false
    }
})
$form.Add_KeyDown({
    param($sender, $event)
    if ($event.KeyCode -eq [System.Windows.Forms.Keys]::Space -and $null -eq $script:keyStarted) {
        $script:keyStarted = [System.Diagnostics.Stopwatch]::StartNew()
    }
})
$form.Add_KeyUp({
    param($sender, $event)
    if ($event.KeyCode -eq [System.Windows.Forms.Keys]::Space -and $null -ne $script:keyStarted) {
        if ($script:keyStarted.ElapsedMilliseconds -ge 100) { $status.Text = 'Held successfully' }
        $script:keyStarted = $null
    }
})
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 80
$timer.Add_Tick({
    $script:ticks++
    if ($script:moving -and $script:ticks % 20 -eq 0) { $script:iconX = 500 - $script:iconX }
    $canvas.Invalidate()
    if ($script:ticks -gt 7500) { $form.Close() }
})
$timer.Start()
$reveal = New-Object System.Windows.Forms.Timer
$reveal.Interval = 300
$reveal.Add_Tick({ $reveal.Stop(); $form.Hide(); $form.Show() })
$reveal.Start()
[void]$form.ShowDialog()
$timer.Dispose()
$reveal.Dispose()
$icon.Dispose()
$drop.Dispose()
$form.Dispose()
