& {
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
    chcp 65001 | Out-Null
    $ErrorActionPreference = "Stop"
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

    $Repo = Split-Path -Parent $PSScriptRoot
    $RepoFullName = "Satodictator/SismoAI-World-Cloud"
    $PolicyPath = Join-Path $Repo "config\notification_policy.json"

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host " ACTIVAR TELEGRAM GRATUITO PARA SISMOAI" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Antes de continuar:" -ForegroundColor Yellow
    Write-Host "1. Cree un bot con @BotFather." -ForegroundColor White
    Write-Host "2. Abra el bot desde cada cuenta de Telegram destinataria." -ForegroundColor White
    Write-Host "3. Pulse START o envíe /start desde cada cuenta." -ForegroundColor White
    Write-Host "4. No comparta el token en chats ni capturas." -ForegroundColor White
    Write-Host ""

    $Gh = $null
    $GhNormal = Get-Command gh.exe -ErrorAction SilentlyContinue
    if ($GhNormal) {
        $Gh = $GhNormal.Source
    } else {
        $GhPortatil = Join-Path $env:USERPROFILE "SismoAI-Tools\GitHubCLI\bin\gh.exe"
        if (Test-Path -LiteralPath $GhPortatil -PathType Leaf) {
            $Gh = $GhPortatil
        }
    }
    if (-not $Gh) {
        throw "No se encontró GitHub CLI."
    }

    $Git = $null
    $GitNormal = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($GitNormal) {
        $Git = $GitNormal.Source
    } else {
        $GitPortatil = Join-Path $env:USERPROFILE "SismoAI-Tools\MinGit\cmd\git.exe"
        if (Test-Path -LiteralPath $GitPortatil -PathType Leaf) {
            $Git = $GitPortatil
        }
    }
    if (-not $Git) {
        throw "No se encontró Git."
    }

    $Confirmacion = Read-Host "Escriba SI cuando ambos destinatarios hayan enviado /start al bot"
    if ($Confirmacion.Trim().ToUpperInvariant() -ne "SI") {
        throw "Activación cancelada. Primero debe enviar /start desde las cuentas destinatarias."
    }

    $TokenSeguro = Read-Host "Pegue aquí el token secreto entregado por BotFather" -AsSecureString
    $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($TokenSeguro)
    $Token = $null

    try {
        $Token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr).Trim()
        if (-not $Token) {
            throw "El token está vacío."
        }

        $Me = Invoke-RestMethod `
            -Uri ("https://api.telegram.org/bot" + $Token + "/getMe") `
            -Method Get `
            -TimeoutSec 30

        if (-not $Me.ok) {
            throw "Telegram no aceptó el token."
        }

        Write-Host ""
        Write-Host "Bot verificado: @$($Me.result.username)" -ForegroundColor Green
        Write-Host "Buscando cuentas que enviaron /start..." -ForegroundColor Yellow

        $Updates = Invoke-RestMethod `
            -Uri ("https://api.telegram.org/bot" + $Token + "/getUpdates?timeout=0&limit=100") `
            -Method Get `
            -TimeoutSec 30

        if (-not $Updates.ok) {
            throw "No se pudieron obtener las conversaciones del bot."
        }

        $Chats = @()

        foreach ($Update in @($Updates.result)) {
            $Mensaje = $null
            if ($Update.message) {
                $Mensaje = $Update.message
            } elseif ($Update.edited_message) {
                $Mensaje = $Update.edited_message
            }

            if ($Mensaje -and $Mensaje.chat -and $Mensaje.chat.type -eq "private") {
                $Chats += [PSCustomObject]@{
                    Id = [string]$Mensaje.chat.id
                    Nombre = (($Mensaje.chat.first_name, $Mensaje.chat.last_name) -join " ").Trim()
                    Usuario = [string]$Mensaje.chat.username
                }
            }
        }

        $Chats = @(
            $Chats |
            Group-Object Id |
            ForEach-Object { $_.Group | Select-Object -First 1 }
        )

        if ($Chats.Count -eq 0) {
            throw "No se detectó ninguna cuenta. Abra el bot, pulse START y vuelva a ejecutar este archivo."
        }

        Write-Host ""
        Write-Host "Cuentas detectadas:" -ForegroundColor Cyan
        foreach ($Chat in $Chats) {
            $Etiqueta = $Chat.Nombre
            if ($Chat.Usuario) {
                $Etiqueta += " (@$($Chat.Usuario))"
            }
            Write-Host "  - $Etiqueta" -ForegroundColor White
        }

        if ($Chats.Count -lt 2) {
            Write-Host ""
            Write-Host "Solo se detectó una cuenta de Telegram." -ForegroundColor Yellow
            Write-Host "Si desea dos cuentas, cancele, envíe /start desde la segunda y repita." -ForegroundColor Yellow
            $Seguir = Read-Host "¿Activar únicamente la cuenta detectada? Escriba SI"
            if ($Seguir.Trim().ToUpperInvariant() -ne "SI") {
                throw "Activación cancelada para esperar la segunda cuenta."
            }
        }

        $ChatIds = ($Chats.Id | Select-Object -Unique) -join ","

        Write-Host ""
        Write-Host "Guardando token y destinatarios como secretos cifrados de GitHub..." -ForegroundColor Yellow

        $Token | & $Gh secret set TELEGRAM_BOT_TOKEN --repo $RepoFullName
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo guardar TELEGRAM_BOT_TOKEN."
        }

        $ChatIds | & $Gh secret set TELEGRAM_CHAT_IDS --repo $RepoFullName
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo guardar TELEGRAM_CHAT_IDS."
        }

        $Policy = Get-Content -LiteralPath $PolicyPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $Policy.enabled = $true
        $Json = $Policy | ConvertTo-Json -Depth 20
        [IO.File]::WriteAllText(
            $PolicyPath,
            $Json + [Environment]::NewLine,
            (New-Object Text.UTF8Encoding($false))
        )

        & $Git -C $Repo config commit.gpgsign false
        & $Git -C $Repo config tag.gpgsign false
        & $Git -C $Repo config user.name "Steven Medina"
        & $Git -C $Repo config user.email "satodictator@users.noreply.github.com"

        & $Git -C $Repo add config/notification_policy.json
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo preparar la política."
        }

        $HayCambios = (& $Git -C $Repo diff --cached --name-only | Out-String).Trim()
        if ($HayCambios) {
            & $Git -C $Repo commit -m "feat: activate private Telegram notifications"
            if ($LASTEXITCODE -ne 0) {
                throw "No se pudo crear el commit de activación."
            }
            & $Git -C $Repo push origin main
            if ($LASTEXITCODE -ne 0) {
                throw "No se pudo publicar la activación."
            }
        }

        Write-Host ""
        Write-Host "Enviando una prueba privada..." -ForegroundColor Yellow
        $SalidaPrueba = & $Gh workflow run test-telegram-notifications.yml --repo $RepoFullName 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo iniciar la prueba de Telegram."
        }
        $TextoPrueba = ($SalidaPrueba | Out-String)
        Write-Host $TextoPrueba

        Write-Host "Actualizando la página pública..." -ForegroundColor Yellow
        $SalidaWorld = & $Gh workflow run world-operations.yml --repo $RepoFullName -f mode=fast 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "La prueba fue iniciada, pero no se pudo iniciar la actualización de la página."
        }
        Write-Host ($SalidaWorld | Out-String)

        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host " TELEGRAM ACTIVADO PARA SISMOAI" -ForegroundColor Green
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Los mensajes quedan permitidos las 24 horas." -ForegroundColor Green
        Write-Host "WhatsApp y llamadas permanecen desactivados porque no existe una vía oficial gratuita." -ForegroundColor Yellow
        Write-Host "La primera ejecución operacional será silenciosa para crear una línea base y evitar avisos antiguos." -ForegroundColor Yellow
        Write-Host ""
    }
    finally {
        if ($Ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
        }
        $Token = $null
        $TokenSeguro = $null
    }
}
