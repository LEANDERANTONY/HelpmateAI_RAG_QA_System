$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$targetDir = Join-Path $root "static\held_out"
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

$headers = @{
    "User-Agent" = "HelpmateAI evaluation document fetch"
}

$documents = @(
    @{
        Id = "nist-ai-rmf-1-0"
        Url = "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf"
        File = "nist-ai-rmf-1-0.pdf"
        Sha256 = "7576EDB531D9848825814EE88E28B1795D3A84B435B4B797D3670EAFDC4A89F1"
    },
    @{
        Id = "arxiv-2510-03305"
        Url = "https://arxiv.org/pdf/2510.03305"
        File = "arxiv-2510-03305-ml-workflows-climate-modeling.pdf"
        Sha256 = "24FD975209898B092A0B0BA85FB4DBB3BEBA3978288FC797CA8B6BB018B3FB8C"
    },
    @{
        Id = "upenn-learning-environmental-models-thesis-2022"
        Url = "https://core.ac.uk/download/533931293.pdf"
        File = "upenn-learning-environmental-models-thesis-2022.pdf"
        Sha256 = "C542D02E631A5379264AC05C18696EE93A1764EF0FAFEAB72353DA5ED503386D"
    },
    @{
        Id = "fomc-minutes-2026-01-28"
        Url = "https://www.federalreserve.gov/monetarypolicy/files/fomcminutes20260128.pdf"
        File = "fomc-minutes-2026-01-28.pdf"
        Sha256 = "7565DB1BBE4D562B808D50A4150D2B42D00AC87A3ACB1D6D2A9EED5E4974E743"
    },
    @{
        Id = "irena-world-energy-transitions-outlook-2023"
        Url = "https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2023/Jun/IRENA_World_energy_transitions_outlook_2023.pdf"
        File = "irena-world-energy-transitions-outlook-2023.pdf"
        Sha256 = "6C903573C558BF957AAFA69FD17D0E7D0F956ECF9132FE771E9EDB5AF2C91F7E"
    }
)

foreach ($document in $documents) {
    $path = Join-Path $targetDir $document.File
    Write-Host "Downloading $($document.Id)"
    Invoke-WebRequest -Uri $document.Url -Headers $headers -OutFile $path -MaximumRedirection 5
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
    if ($hash -ne $document.Sha256) {
        throw "Hash mismatch for $($document.File). Expected $($document.Sha256), got $hash."
    }
}

Get-ChildItem -Path $targetDir -Filter *.pdf | Select-Object Name, Length
