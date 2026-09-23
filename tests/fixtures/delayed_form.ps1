Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Jev Delayed Control Fixture'
$form.Size = New-Object System.Drawing.Size(620, 260)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 12)
$name = New-Object System.Windows.Forms.TextBox
$name.AccessibleName = 'Name'
$name.Location = New-Object System.Drawing.Point(20, 20)
$name.Size = New-Object System.Drawing.Size(260, 35)
$form.Controls.Add($name)
$status = New-Object System.Windows.Forms.Label
$status.Text = 'Ready'
$status.Location = New-Object System.Drawing.Point(20, 100)
$status.Size = New-Object System.Drawing.Size(560, 50)
$form.Controls.Add($status)
$prepare = New-Object System.Windows.Forms.Button
$prepare.Text = 'Prepare'
$prepare.Location = New-Object System.Drawing.Point(300, 20)
$prepare.Size = New-Object System.Drawing.Size(120, 40)
$form.Controls.Add($prepare)
$save = New-Object System.Windows.Forms.Button
$save.Text = 'Save'
$save.Location = New-Object System.Drawing.Point(440, 20)
$save.Size = New-Object System.Drawing.Size(120, 40)
$save.Visible = $false
$form.Controls.Add($save)
$script:saves = 0
$save.Add_Click({
    $script:saves++
    if ($name.Text -eq 'Exact test value' -and $script:saves -eq 1) {
        $status.Text = 'Saved once'
    } else { $status.Text = 'Incorrect input or repeated save' }
})
$reveal = New-Object System.Windows.Forms.Timer
$reveal.Interval = 1500
$reveal.Add_Tick({ $reveal.Stop(); $save.Visible = $true; $status.Text = 'Save available' })
$prepare.Add_Click({ $prepare.Enabled = $false; $status.Text = 'Preparing'; $reveal.Start() })
$expiry = New-Object System.Windows.Forms.Timer
$expiry.Interval = 60000
$expiry.Add_Tick({ $form.Close() })
$expiry.Start()
$show = New-Object System.Windows.Forms.Timer
$show.Interval = 300
$show.Add_Tick({ $show.Stop(); $form.Hide(); $form.Show() })
$show.Start()
try { [void]$form.ShowDialog() }
finally { $show.Dispose(); $reveal.Dispose(); $expiry.Dispose(); $form.Dispose() }
