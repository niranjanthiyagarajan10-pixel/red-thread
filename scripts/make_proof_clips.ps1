# Makes invented test voices for the Proof Call with Windows only. Files go outside the repo and are not for the public demo.
# The answers come from redthread/proof_demo.py, so the words in the clips always match what the demo expects.
$clips = Join-Path $HOME "muse-spike\clips\proof"
New-Item -ItemType Directory -Force $clips | Out-Null
Add-Type -AssemblyName System.Speech

$repo = Split-Path $PSScriptRoot -Parent
$script = "import json; from redthread import proof_demo as d; print(json.dumps({k: d.person(k).answers for k in d.PEOPLE}))"
$env:PYTHONIOENCODING = "utf-8"
Push-Location $repo
$answers = python -c $script | ConvertFrom-Json
Pop-Location

function Say($name, $text, $rate) {
    $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $speaker.Rate = $rate
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
    $speaker.SetOutputToWaveFile((Join-Path $clips $name), $format)
    $speaker.Speak($text)
    $speaker.Dispose()
}

foreach ($kind in @("writer", "stand_in")) {
    foreach ($number in 1..5) {
        Say "$($kind)_$number.wav" $answers.$kind.$number 0
    }
}
Get-ChildItem $clips | Select-Object Name, Length
