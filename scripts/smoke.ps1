[CmdletBinding()]
param(
    [string]$WebUrl = $(if ($env:WEB_URL) { $env:WEB_URL } else { "http://localhost:3000" }),
    [string]$ApiUrl = $(if ($env:API_URL) { $env:API_URL } else { "http://localhost:8000" }),
    [string]$ObjectStoreUrl = $(if ($env:OBJECT_STORE_URL) { $env:OBJECT_STORE_URL } else { "http://localhost:9000" }),
    [string]$SmokeWebOrigin = $(if ($env:SMOKE_WEB_ORIGIN) { $env:SMOKE_WEB_ORIGIN } else { "http://localhost:3000" }),
    [string]$SmokeAdminEmail = $(if ($env:SMOKE_ADMIN_EMAIL) { $env:SMOKE_ADMIN_EMAIL } else { "" }),
    [string]$SmokeAdminPassword = $(if ($env:SMOKE_ADMIN_PASSWORD) { $env:SMOKE_ADMIN_PASSWORD } else { "" }),
    [int]$TimeoutSeconds = $(if ($env:SMOKE_TIMEOUT_SECONDS) { [int]$env:SMOKE_TIMEOUT_SECONDS } else { 180 })
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

function Wait-HttpReady {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Uri
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host ("{0,-18} ready" -f $Name)
                return
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    docker compose ps | Out-Host
    throw "$Name did not become ready within $TimeoutSeconds seconds ($Uri)"
}

function Wait-ContainerHealthy {
    param([Parameter(Mandatory)][string]$Service)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $containerId = (docker compose ps --all --quiet $Service 2>$null | Select-Object -First 1)
        if ($containerId) {
            $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $containerId 2>$null
            if ($health -eq "healthy") {
                Write-Host ("{0,-18} healthy" -f $Service)
                return
            }
            if ($health -in @("unhealthy", "exited", "dead")) {
                docker compose logs --tail=80 $Service | Out-Host
                throw "$Service entered terminal state: $health"
            }
        }
        Start-Sleep -Seconds 2
    }

    docker compose ps | Out-Host
    throw "$Service did not become healthy within $TimeoutSeconds seconds"
}

function Wait-OneShotCompleted {
    param([Parameter(Mandatory)][string]$Service)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $containerId = (docker compose ps --all --quiet $Service 2>$null | Select-Object -First 1)
        if ($containerId) {
            $status = docker inspect --format '{{.State.Status}}' $containerId 2>$null
            $exitCode = docker inspect --format '{{.State.ExitCode}}' $containerId 2>$null
            if ($status -eq "exited" -and $exitCode -eq "0") {
                Write-Host ("{0,-18} completed" -f $Service)
                return
            }
            if ($status -in @("exited", "dead")) {
                docker compose logs --tail=80 $Service | Out-Host
                throw "$Service did not complete successfully: $status $exitCode"
            }
        }
        Start-Sleep -Seconds 2
    }

    docker compose logs --tail=80 $Service | Out-Host
    throw "$Service did not complete within $TimeoutSeconds seconds"
}

function Assert-ContainerRunning {
    param([Parameter(Mandatory)][string]$Service)

    $containerId = (docker compose ps --all --quiet $Service 2>$null | Select-Object -First 1)
    if (-not $containerId) {
        throw "$Service container was not created"
    }
    $status = docker inspect --format '{{.State.Status}}' $containerId
    if ($status -ne "running") {
        docker compose logs --tail=80 $Service | Out-Host
        throw "$Service is not running: $status"
    }
    Write-Host ("{0,-18} running" -f $Service)
}

function Assert-ObjectStoreCors {
    $uri = "$ObjectStoreUrl/northstar-cors-check/probe"
    foreach ($method in @("GET", "HEAD", "PUT", "POST")) {
        $allowed = Invoke-WebRequest -UseBasicParsing -Method Options -Uri $uri -TimeoutSec 10 -Headers @{
            Origin = $SmokeWebOrigin
            "Access-Control-Request-Method" = $method
            "Access-Control-Request-Headers" = "content-type,x-amz-meta-sha256"
        }
        if ($allowed.Headers["Access-Control-Allow-Origin"] -ne $SmokeWebOrigin) {
            throw "Object storage did not allow $method from the configured smoke-test web origin"
        }
    }

    $denied = Invoke-WebRequest -UseBasicParsing -Method Options -Uri $uri -TimeoutSec 10 -Headers @{
        Origin = "https://cors-deny-check.invalid"
        "Access-Control-Request-Method" = "PUT"
    }
    if ($denied.Headers["Access-Control-Allow-Origin"]) {
        throw "Object storage unexpectedly allowed an untrusted smoke-test origin"
    }
    Write-Host ("{0,-18} restricted" -f "Object CORS")
}

function Assert-PresignedUploadFlow {
    if (-not $SmokeAdminEmail -and -not $SmokeAdminPassword) {
        Write-Host ("{0,-18} skipped (set SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD)" -f "Upload flow")
        return
    }
    if (-not $SmokeAdminEmail -or -not $SmokeAdminPassword) {
        throw "Set both SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD, or neither"
    }

    $apiBase = "$($WebUrl.TrimEnd('/'))/api/v1"
    $authorization = $null
    $sourceId = $null
    try {
        $login = Invoke-RestMethod -Method Post -Uri "$apiBase/auth/login" -TimeoutSec 30 -ContentType "application/json" -Body (
            @{ email = $SmokeAdminEmail; password = $SmokeAdminPassword } | ConvertTo-Json -Compress
        )
        if (-not $login.accessToken) {
            throw "Login response did not contain an access token"
        }
        $authorization = @{ Authorization = "Bearer $($login.accessToken)" }

        $agents = @(Invoke-RestMethod -Method Get -Uri "$apiBase/agents" -TimeoutSec 30 -Headers $authorization)
        if ($agents.Count -eq 0) {
            throw "No agent is available for the authenticated upload smoke check"
        }

        $fileBytes = [System.Text.Encoding]::UTF8.GetBytes("Northstar presigned POST integration smoke.")
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $checksumSha256 = [System.BitConverter]::ToString($sha256.ComputeHash($fileBytes)).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $sha256.Dispose()
        }
        $presign = Invoke-RestMethod -Method Post -Uri "$apiBase/uploads/presign" -TimeoutSec 30 -Headers $authorization -ContentType "application/json" -Body (
            @{
                filename = "infra-smoke.txt"
                contentType = "text/plain"
                sizeBytes = $fileBytes.Length
                checksumSha256 = $checksumSha256
            } | ConvertTo-Json -Compress
        )
        if ($presign.method -ne "POST" -or -not $presign.objectKey.StartsWith("staging/")) {
            throw "Upload presign response did not satisfy the POST staging contract"
        }

        $client = [System.Net.Http.HttpClient]::new()
        $client.Timeout = [TimeSpan]::FromSeconds(30)
        $multipart = [System.Net.Http.MultipartFormDataContent]::new()
        try {
            [void]$client.DefaultRequestHeaders.TryAddWithoutValidation("Origin", $SmokeWebOrigin)
            foreach ($field in $presign.fields.PSObject.Properties) {
                $fieldContent = [System.Net.Http.StringContent]::new([string]$field.Value)
                $multipart.Add($fieldContent, $field.Name)
            }
            # S3-compatible POST handlers require the file part after all signed fields.
            $fileContent = [System.Net.Http.ByteArrayContent]::new($fileBytes)
            $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("text/plain")
            $multipart.Add($fileContent, "file", "infra-smoke.txt")

            $uploadResponse = $client.PostAsync([string]$presign.url, $multipart).GetAwaiter().GetResult()
            try {
                if (-not $uploadResponse.IsSuccessStatusCode) {
                    throw "Presigned object upload failed with HTTP $([int]$uploadResponse.StatusCode)"
                }
                if (-not $uploadResponse.Headers.Contains("Access-Control-Allow-Origin")) {
                    throw "Presigned object upload response omitted Access-Control-Allow-Origin"
                }
                $allowedOrigins = @($uploadResponse.Headers.GetValues("Access-Control-Allow-Origin"))
                if ($SmokeWebOrigin -notin $allowedOrigins) {
                    throw "Presigned object upload response did not allow the configured smoke-test origin"
                }
            }
            finally {
                $uploadResponse.Dispose()
            }
        }
        finally {
            $multipart.Dispose()
            $client.Dispose()
        }

        $source = Invoke-RestMethod -Method Post -Uri "$apiBase/agents/$($agents[0].id)/knowledge" -TimeoutSec 30 -Headers $authorization -ContentType "application/json" -Body (
            @{
                name = "Infrastructure upload smoke"
                kind = "file"
                objectKey = $presign.objectKey
            } | ConvertTo-Json -Compress
        )
        $sourceId = [string]$source.id

        $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
        while ([DateTimeOffset]::UtcNow -lt $deadline) {
            $sources = @(Invoke-RestMethod -Method Get -Uri "$apiBase/agents/$($agents[0].id)/knowledge" -TimeoutSec 30 -Headers $authorization)
            $current = $sources | Where-Object { [string]$_.id -eq $sourceId } | Select-Object -First 1
            if ($current.status -eq "ready") {
                Write-Host ("{0,-18} promoted and ingested" -f "Upload flow")
                return
            }
            if ($current.status -eq "failed") {
                throw "Uploaded smoke source entered failed state"
            }
            Start-Sleep -Seconds 2
        }
        throw "Uploaded smoke source did not finish within $TimeoutSeconds seconds"
    }
    finally {
        if ($sourceId -and $authorization) {
            try {
                Invoke-RestMethod -Method Delete -Uri "$apiBase/knowledge/$sourceId" -TimeoutSec 30 -Headers $authorization | Out-Null
            }
            catch {
                Write-Warning "Could not remove the authenticated upload smoke source"
            }
        }
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Required command not found: docker"
}

foreach ($service in @("postgres", "redis", "rabbitmq", "kafka", "minio")) {
    Wait-ContainerHealthy -Service $service
}
Wait-OneShotCompleted -Service "migrate"
Wait-OneShotCompleted -Service "minio-init"
Assert-ObjectStoreCors

foreach ($service in @("api", "web", "worker")) {
    Wait-ContainerHealthy -Service $service
}
foreach ($service in @("job-dispatcher", "outbox-relay", "analytics-consumer", "object-cleaner")) {
    Assert-ContainerRunning -Service $service
}

Wait-HttpReady -Name "API liveness" -Uri "$ApiUrl/health/live"
Wait-HttpReady -Name "API readiness" -Uri "$ApiUrl/health/ready"
Wait-HttpReady -Name "Web application" -Uri "$WebUrl/healthz"
Assert-PresignedUploadFlow

docker compose ps --status running
if ($LASTEXITCODE -ne 0) {
    throw "docker compose ps failed"
}

Write-Host "Smoke checks passed."
