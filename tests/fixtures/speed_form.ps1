Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Jev Speed Fixture'
$form.ClientSize = New-Object System.Drawing.Size(850, 580)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 12)
$script:fields = @()
for ($i = 1; $i -le 12; $i++) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = ('Field{0:D2}' -f $i)
    $label.Location = New-Object System.Drawing.Point(20, (15 + ($i - 1) * 39))
    $label.Size = New-Object System.Drawing.Size(120, 30)
    $form.Controls.Add($label)
    $box = New-Object System.Windows.Forms.TextBox
    $box.AccessibleName = $label.Text
    $box.Location = New-Object System.Drawing.Point(150, (15 + ($i - 1) * 39))
    $box.Size = New-Object System.Drawing.Size(400, 30)
    $form.Controls.Add($box)
    $script:fields += $box
}
$status = New-Object System.Windows.Forms.Label
$status.Text = 'Ready'
$status.Location = New-Object System.Drawing.Point(20, 515)
$status.Size = New-Object System.Drawing.Size(650, 40)
$form.Controls.Add($status)
$save = New-Object System.Windows.Forms.Button
$save.Text = 'Save'
$save.Location = New-Object System.Drawing.Point(650, 30)
$save.Size = New-Object System.Drawing.Size(140, 45)
$save.Add_Click({
    $valid = $true
    for ($i = 0; $i -lt 12; $i++) {
        if ($script:fields[$i].Text -cne ('VALUE{0:D2}' -f ($i + 1))) { $valid = $false }
    }
    $status.Text = if ($valid) { 'Saved 12 fields' } else { 'Incorrect field contents' }
})
$form.Controls.Add($save)
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
$form.Dispose()
