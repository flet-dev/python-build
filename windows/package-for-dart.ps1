param(
  [Parameter(Mandatory = $true)]
  [string]$PythonVersion,

  [Parameter(Mandatory = $true)]
  [string]$PythonVersionShort
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = $env:GITHUB_WORKSPACE
if (-not $workspace) {
  $workspace = Split-Path -Parent $PSScriptRoot
}

$srcRoot = Join-Path $workspace "windows\build"
$srcArchive = Join-Path $srcRoot "Python-$PythonVersion.tgz"
$srcDir = Join-Path $srcRoot "Python-$PythonVersion"
$pcbuildDir = Join-Path $srcDir "PCbuild\amd64"

$packageRoot = Join-Path $workspace "windows\python-windows-for-dart-$PythonVersionShort"
$zipPath = Join-Path $workspace "windows\python-windows-for-dart-$PythonVersionShort.zip"
$excludeListPath = Join-Path $workspace "windows\python-windows-dart.exclude"
$keepImportLibs = @("python3.lib", "python3_d.lib", "python312.lib", "python312_d.lib")

New-Item -ItemType Directory -Force -Path $srcRoot | Out-Null

Write-Host "Downloading CPython source $PythonVersion"
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/Python-$PythonVersion.tgz" -OutFile $srcArchive
tar -xf $srcArchive -C $srcRoot

Push-Location $srcDir
cmd /c "PCbuild\build.bat -e -p x64 -c Release"
cmd /c "PCbuild\build.bat -e -p x64 -c Debug"
Pop-Location

Remove-Item -Recurse -Force $packageRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$packageRoot\DLLs", "$packageRoot\include", "$packageRoot\Lib", "$packageRoot\libs", "$packageRoot\Scripts" | Out-Null

Copy-Item -Path "$srcDir\Include\*" -Destination "$packageRoot\include" -Recurse -Force
Copy-Item -Path "$srcDir\Lib\*" -Destination "$packageRoot\Lib" -Recurse -Force

# pyconfig.h is generated/platform-specific and lives under PC on Windows builds.
$pyconfigHeader = Join-Path $srcDir "PC\pyconfig.h"
if (-not (Test-Path $pyconfigHeader)) {
  throw "Missing required header: $pyconfigHeader"
}
Copy-Item -Path $pyconfigHeader -Destination "$packageRoot\include\pyconfig.h" -Force

# Root binaries and symbols.
foreach ($name in @("LICENSE.txt", "NEWS.txt")) {
  $src = Join-Path $srcDir $name
  if (Test-Path $src) {
    Copy-Item -Path $src -Destination $packageRoot -Force
  }
}

$rootFiles = @(
  "python3.dll",
  "python3_d.dll",
  "python312.dll",
  "python312_d.dll",
  "python312_d.pdb",
  "python_d.pdb",
  "pythonw_d.pdb"
)
foreach ($name in $rootFiles) {
  $src = Join-Path $pcbuildDir $name
  if (Test-Path $src) {
    Copy-Item -Path $src -Destination $packageRoot -Force
  }
}

foreach ($name in @("vcruntime140.dll", "vcruntime140_1.dll")) {
  $fromBuild = Join-Path $pcbuildDir $name
  $fromSystem = Join-Path "$env:WINDIR\System32" $name
  if (Test-Path $fromBuild) {
    Copy-Item -Path $fromBuild -Destination $packageRoot -Force
  } elseif (Test-Path $fromSystem) {
    Copy-Item -Path $fromSystem -Destination $packageRoot -Force
  }
}

# Extension modules and supporting DLLs.
Get-ChildItem -Path $pcbuildDir -Filter "*.pyd" -File | Copy-Item -Destination "$packageRoot\DLLs" -Force
Get-ChildItem -Path $pcbuildDir -Filter "*.dll" -File |
  Where-Object { $_.Name -notin @("python3.dll", "python3_d.dll", "python312.dll", "python312_d.dll", "vcruntime140.dll", "vcruntime140_1.dll") } |
  Copy-Item -Destination "$packageRoot\DLLs" -Force
foreach ($name in $keepImportLibs) {
  $src = Join-Path $pcbuildDir $name
  if (Test-Path $src) {
    Copy-Item -Path $src -Destination "$packageRoot\libs" -Force
  }
}

# Cleanup using exclude list.
if (-not (Test-Path $excludeListPath)) {
  throw "Exclude list not found: $excludeListPath"
}
$excludePatterns = Get-Content $excludeListPath |
  ForEach-Object { $_.Trim() } |
  Where-Object { $_ -and -not $_.StartsWith("#") }
foreach ($pattern in $excludePatterns) {
  $fullPattern = Join-Path $packageRoot $pattern
  $matches = Get-ChildItem -Path $fullPattern -Force -ErrorAction SilentlyContinue
  foreach ($item in $matches) {
    Remove-Item -Path $item.FullName -Recurse -Force
  }
}

# Match existing packaging behavior: bytecode-only stdlib.
py -3 -m compileall -b "$packageRoot\Lib"
Get-ChildItem -Path "$packageRoot\Lib" -Recurse -File -Include *.py,*.typed | Remove-Item -Force
Get-ChildItem -Path "$packageRoot\Lib" -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

# Fail fast if required layout entries are missing.
$requiredEntries = @(
  "$packageRoot\DLLs",
  "$packageRoot\include",
  "$packageRoot\Lib",
  "$packageRoot\libs",
  "$packageRoot\Scripts",
  "$packageRoot\python3.dll",
  "$packageRoot\python3_d.dll",
  "$packageRoot\python312.dll",
  "$packageRoot\python312_d.dll",
  "$packageRoot\python312_d.pdb",
  "$packageRoot\python_d.pdb",
  "$packageRoot\pythonw_d.pdb"
)
foreach ($entry in $requiredEntries) {
  if (-not (Test-Path $entry)) {
    Get-ChildItem $packageRoot
    throw "Missing required package entry: $entry"
  }
}

Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
7z a $zipPath "$packageRoot\*"
