# Makes invented test media with Windows only: spoken WAV files and a plain payment image.
# Nothing here is real, and nothing here is committed. Files go to the clips folder outside the repo.
$clips = Join-Path $HOME "muse-spike\clips"
New-Item -ItemType Directory -Force $clips | Out-Null

Add-Type -AssemblyName System.Speech
Add-Type -AssemblyName System.Drawing

function Say($name, $text) {
    $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
    $speaker.SetOutputToWaveFile((Join-Path $clips $name), $format)
    $speaker.Speak($text)
    $speaker.Dispose()
}

Say "loss.wav" "I already paid thirty eight thousand rupees to that man."
Say "story.wav" "It happened yesterday. I live in Chennai in Tamil Nadu and the money left my SBI account. A woman I met on a dating app showed me profits on a trading app and told me to invest. When I asked to withdraw, she told me to pay a tax first, and then she stopped replying."

$bitmap = New-Object System.Drawing.Bitmap(720, 420)
$canvas = [System.Drawing.Graphics]::FromImage($bitmap)
$canvas.Clear([System.Drawing.Color]::White)
$big = New-Object System.Drawing.Font("Arial", 30, [System.Drawing.FontStyle]::Bold)
$small = New-Object System.Drawing.Font("Arial", 20)
$ink = [System.Drawing.Brushes]::Black
$canvas.DrawString("Payment successful", $big, $ink, 40, 40)
$canvas.DrawString("Paid Rs 38,000", $big, $ink, 40, 110)
$canvas.DrawString("To: scam.one@ybl", $small, $ink, 40, 200)
$canvas.DrawString("UTR 533827652689", $small, $ink, 40, 250)
$canvas.DrawString("31 Oct 2026, 9:14 PM", $small, $ink, 40, 300)
$canvas.Dispose()
$bitmap.Save((Join-Path $clips "payment.png"), [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()

Get-ChildItem $clips | Select-Object Name, Length
