Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Jev Regression Fixture'
$form.Size = New-Object System.Drawing.Size(850, 650)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 12)
$name = New-Object System.Windows.Forms.TextBox
$name.AccessibleName = 'Name'
$name.Location = New-Object System.Drawing.Point(20, 20)
$name.Size = New-Object System.Drawing.Size(350, 30)
$form.Controls.Add($name)
$status = New-Object System.Windows.Forms.Label
$status.Text = 'Ready'
$status.Location = New-Object System.Drawing.Point(20, 100)
$status.Size = New-Object System.Drawing.Size(700, 35)
$form.Controls.Add($status)
$save = New-Object System.Windows.Forms.Button
$save.Text = 'Save'
$save.Location = New-Object System.Drawing.Point(400, 20)
$save.Size = New-Object System.Drawing.Size(120, 40)
$save.Add_Click({ $status.Text = 'Saved successfully' })
$form.Controls.Add($save)
$reset = New-Object System.Windows.Forms.Button
$reset.Text = 'Reset'
$reset.Location = New-Object System.Drawing.Point(540, 20)
$reset.Size = New-Object System.Drawing.Size(120, 40)
$reset.Add_Click({ $name.Clear(); $status.Text = 'Ready' })
$form.Controls.Add($reset)
for ($i = 0; $i -lt 48; $i++) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = 'Item ' + $i
    $label.Location = New-Object System.Drawing.Point((20 + ($i % 6) * 130), (160 + [math]::Floor($i / 6) * 45))
    $label.Size = New-Object System.Drawing.Size(125, 30)
    $form.Controls.Add($label)
}
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 600000
$timer.Add_Tick({ $form.Close() })
$timer.Start()
$reveal = New-Object System.Windows.Forms.Timer
$reveal.Interval = 300
$reveal.Add_Tick({
    $reveal.Stop()
    $form.Hide()
    $form.Show()
})
$reveal.Start()
[void]$form.ShowDialog()
$reveal.Dispose()
$timer.Dispose()
$form.Dispose()
