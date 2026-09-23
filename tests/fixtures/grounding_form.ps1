Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Jev Grounding Fixture'
$form.ClientSize = New-Object System.Drawing.Size(800, 520)
$form.StartPosition = 'CenterScreen'
$panel = New-Object System.Windows.Forms.Panel
$panel.Dock = 'Fill'
$panel.BackColor = [System.Drawing.Color]::FromArgb(35, 35, 35)
$form.Controls.Add($panel)
$status = New-Object System.Windows.Forms.Label
$status.Text = 'Ready'
$status.ForeColor = [System.Drawing.Color]::White
$status.Font = New-Object System.Drawing.Font('Segoe UI', 16)
$status.Location = New-Object System.Drawing.Point(20, 460)
$status.Size = New-Object System.Drawing.Size(700, 40)
$panel.Controls.Add($status)
$font = New-Object System.Drawing.Font('Segoe UI', 12)
$pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 2)
$panel.Add_Paint({
    param($sender, $event)
    for ($i = 0; $i -lt 90; $i++) {
        $x = 10 + ($i % 10) * 78
        $y = 10 + [math]::Floor($i / 10) * 38
        $event.Graphics.DrawString(('Item ' + $i), $font, [System.Drawing.Brushes]::White, $x, $y)
    }
    $event.Graphics.DrawString('Draft', $font, [System.Drawing.Brushes]::White, 620, 395)
    $event.Graphics.DrawLine($pen, 690, 400, 704, 414)
    $event.Graphics.DrawLine($pen, 704, 400, 690, 414)
})
$panel.Add_MouseUp({
    param($sender, $event)
    if ($event.X -ge 687 -and $event.X -le 707 -and $event.Y -ge 397 -and $event.Y -le 417) {
        $status.Text = 'Closed successfully'
    }
})
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 600000
$timer.Add_Tick({ $form.Close() })
$timer.Start()
$reveal = New-Object System.Windows.Forms.Timer
$reveal.Interval = 300
$reveal.Add_Tick({ $reveal.Stop(); $form.Hide(); $form.Show() })
$reveal.Start()
[void]$form.ShowDialog()
$reveal.Dispose()
$timer.Dispose()
$font.Dispose()
$pen.Dispose()
$form.Dispose()
