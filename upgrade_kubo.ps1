param(
    [string]$KuboDir = "C:\Users\davio\Desktop\ipfs\kubo",
    [string]$Version = "v0.7.0"
)

$zipName = "go-ipfs_${Version}_windows-amd64.zip"
$downloadUrl = "https://github.com/ipfs/kubo/releases/download/$Version/$zipName"
$downloadPath = Join-Path $env:TEMP $zipName
$extractDir = Join-Path $env:TEMP "kubo_${Version}_windows-amd64"

Write-Host "Target Kubo install directory: $KuboDir"
Write-Host "Downloading Kubo $Version from $downloadUrl"

if (-Not (Test-Path $KuboDir)) {
    Write-Error "Target directory does not exist: $KuboDir"
    exit 1
}

if (Test-Path $downloadPath) {
    Remove-Item $downloadPath -Force
}

Invoke-WebRequest -Uri $downloadUrl -OutFile $downloadPath

if (Test-Path $extractDir) {
    Remove-Item $extractDir -Recurse -Force
}

Expand-Archive -Path $downloadPath -DestinationPath $extractDir

$sourceExe = Join-Path $extractDir "kubo.exe"
$targetExe = Join-Path $KuboDir "ipfs.exe"
$backupExe = Join-Path $KuboDir "ipfs.exe.bak"

if (-Not (Test-Path $sourceExe)) {
    Write-Error "Downloaded archive did not contain kubo.exe"
    exit 1
}

if (Test-Path $targetExe) {
    Write-Host "Backing up existing ipfs.exe to ipfs.exe.bak"
    Copy-Item -Path $targetExe -Destination $backupExe -Force
}

Copy-Item -Path $sourceExe -Destination $targetExe -Force
Write-Host "Replaced ipfs.exe with Kubo $Version"
Write-Host "You can now run:"
Write-Host "  cd $KuboDir"
Write-Host "  .\ipfs.exe version"
Write-Host "  .\ipfs.exe daemon"