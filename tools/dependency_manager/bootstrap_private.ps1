# Private CPython archive bootstrap for source CLI; no system Python discovery/MSI.
function Get-CoatSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() }
    finally { $hash.Dispose(); $stream.Dispose() }
}

function Get-CoatInventoryDigest($Files) {
    $names = [string[]]@($Files.PSObject.Properties | ForEach-Object { $_.Name })
    [Array]::Sort($names, [StringComparer]::Ordinal)
    $builder = [Text.StringBuilder]::new()
    foreach ($name in $names) {
        $record = $Files.PSObject.Properties[$name].Value
        [void]$builder.Append($name).Append([char]0).Append($record.sha256).Append([char]0).Append([string]$record.size).Append([char]10)
    }
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($builder.ToString())))).Replace("-", "").ToLowerInvariant() }
    finally { $hash.Dispose() }
}

function Assert-CoatArchiveName([string]$Name) {
    if (-not $Name -or $Name.StartsWith("/") -or $Name.Contains("\") -or $Name -match '[\x00-\x1f]') {
        Stop-CoatBootstrap "Unsafe private Python archive path."
    }
    foreach ($part in $Name.TrimEnd("/").Split("/")) {
        if (-not $part -or $part -in @(".", "..") -or $part -match '[:<>"|?*]' -or
            $part.EndsWith(".") -or $part.EndsWith(" ") -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)') {
            Stop-CoatBootstrap "Unsafe private Python archive path."
        }
    }
}

function Test-CoatPrivateRuntime([string]$Directory, $Manifest) {
    [void](Assert-CoatUserPath $Directory (Split-Path -Parent $Directory))
    $markerPath = Join-Path $Directory ".coating-python.json"
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { Stop-CoatBootstrap "Existing private runtime has no ownership marker; preserved." }
    $markerInfo = Get-Item -LiteralPath $markerPath -Force
    if (($markerInfo.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $markerInfo.Length -gt 2097152) { Stop-CoatBootstrap "Private runtime marker is linked or exceeds size bound." }
    $marker = [IO.File]::ReadAllText($markerPath) | ConvertFrom-Json
    $artifact = $Manifest.artifacts.x86_64
    if ($marker.schema_version -ne 1 -or $marker.version -ne $Manifest.version -or
        $marker.package_sha256 -cne $artifact.sha256 -or (Get-CoatInventoryDigest $marker.files) -cne $artifact.inventory_sha256) {
        Stop-CoatBootstrap "Private runtime ownership/inventory is invalid; preserved."
    }
    $observed = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Directory)
    while ($pending.Count -gt 0) {
        foreach ($path in [IO.Directory]::EnumerateFileSystemEntries($pending.Pop())) {
            $attributes = [IO.File]::GetAttributes($path)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Stop-CoatBootstrap "Private runtime contains a link or reparse point; preserved." }
            if (($attributes -band [IO.FileAttributes]::Directory) -ne 0) { $pending.Push($path); continue }
            $relative = $path.Substring($Directory.Length + 1).Replace("\", "/")
            if ($relative -eq ".coating-python.json") { continue }
            $record = $marker.files.PSObject.Properties[$relative]
            if (-not $record) {
                if ($relative -notmatch '/__pycache__/[^/]+\.pyc$') { Stop-CoatBootstrap "Unexpected private runtime file; preserved." }
                continue
            }
            $info = [IO.FileInfo]::new($path)
            if ($info.Length -ne $record.Value.size -or (Get-CoatSha256 $path) -cne $record.Value.sha256) { Stop-CoatBootstrap "Private runtime file changed; preserved." }
            [void]$observed.Add($relative)
        }
    }
    if ($observed.Count -ne @($marker.files.PSObject.Properties).Count) { Stop-CoatBootstrap "Private runtime file is missing; preserved." }
}

function Save-CoatPrivateArchive($Artifact, [string]$Destination, $Proxy, [bool]$Mirror) {
    $sources = @()
    if ($Mirror) { $sources += [string]$Artifact.mirrors[0].url }
    $sources += [string]$Artifact.url
    $allowed = @([string]$Artifact.url, [string]$Artifact.mirrors[0].url)
    foreach ($source in $sources) {
        for ($attempt = 1; $attempt -le 2; $attempt++) {
            $part = $Destination + "." + [Guid]::NewGuid().ToString("N") + ".part"
            $response = $null; $output = $null
            try {
                Write-CoatBootstrap ("Python download attempt " + $attempt + "/2: " + $source + "; connect 10s, idle read 15s, no total-duration cutoff.")
                $uri = [Uri]$source
                for ($redirect = 0; $redirect -le 5; $redirect++) {
                    if ($uri.AbsoluteUri -cnotin $allowed -or $uri.Scheme -ne "https" -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
                        Stop-CoatBootstrap "Private Python redirect outside approved HTTPS sources."
                    }
                    $request = [Net.HttpWebRequest]::Create($uri)
                    $request.Proxy = $Proxy
                    $request.AllowAutoRedirect = $false
                    $request.ProtocolVersion = [version]"1.1"
                    $request.Timeout = 10000
                    $request.ReadWriteTimeout = 15000
                    $response = $request.GetResponse()
                    if ([int]$response.StatusCode -in @(301,302,303,307,308)) {
                        $next = $response.Headers["Location"]
                        $response.Dispose(); $response = $null
                        if (-not $next -or $redirect -eq 5) { Stop-CoatBootstrap "Private Python redirect limit exceeded." }
                        $uri = [Uri]::new($uri, $next)
                        continue
                    }
                    if ([int]$response.StatusCode -ne 200) { Stop-CoatBootstrap "Private Python download requires HTTP 200." }
                    break
                }
                if ($response.ContentLength -gt 134217728) { Stop-CoatBootstrap "Private Python download exceeds 128 MiB." }
                $output = [IO.File]::Open($part, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                $inputStream = $response.GetResponseStream()
                try {
                    $buffer = [byte[]]::new(65536); $total = 0L
                    while (($count = $inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                        $total += $count
                        if ($total -gt 134217728) { Stop-CoatBootstrap "Private Python download exceeds 128 MiB." }
                        $output.Write($buffer,0,$count)
                    }
                } finally { $inputStream.Dispose() }
                $output.Dispose(); $output = $null
                if ($response.ContentLength -ge 0 -and $total -ne $response.ContentLength) { Stop-CoatBootstrap "Private Python download was truncated." }
                Assert-CoatInstallerHash $part $Artifact.sha256
                [IO.File]::Move($part, $Destination)
                Write-CoatBootstrap ("Python download verified: " + $total + " bytes; pinned official SHA256 matched.")
                return
            } catch {
                Write-CoatBootstrap "Python download failed (network/TLS, idle read, size or hash); partial file will not be executed."
                if ($attempt -eq 2 -and $source -eq $sources[-1]) { throw }
                if ($attempt -eq 2) { Write-CoatBootstrap "Python mirror exhausted; falling back to official NuGet." }
            } finally {
                if ($output) { $output.Dispose() }
                if ($response) { $response.Dispose() }
                if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }
            }
        }
    }
}

function Expand-CoatPrivateArchive([string]$Archive, [string]$Staging, $Manifest) {
    Assert-CoatInstallerHash $Archive $Manifest.artifacts.x86_64.sha256
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    $files = [ordered]@{}
    try {
        if ($zip.Entries.Count -gt 10000) { Stop-CoatBootstrap "Private runtime archive has too many members." }
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $expanded = 0L
        foreach ($entry in $zip.Entries) {
            Assert-CoatArchiveName $entry.FullName
            if (-not $seen.Add($entry.FullName.TrimEnd("/"))) { Stop-CoatBootstrap "Duplicate/Windows-alias private runtime archive path." }
            if ((($entry.ExternalAttributes -shr 16) -band 61440) -eq 40960) { Stop-CoatBootstrap "Linked private runtime archive member." }
            $expanded += $entry.Length
            if ($entry.Length -gt 67108864 -or $expanded -gt 268435456) { Stop-CoatBootstrap "Expanded private runtime exceeds size bound." }
        }
        foreach ($entry in $zip.Entries) {
            if (-not $entry.FullName.StartsWith("tools/", [StringComparison]::Ordinal) -or $entry.FullName.EndsWith("/")) { continue }
            $relative = $entry.FullName.Substring(6)
            $target = Join-Path $Staging $relative.Replace("/", [IO.Path]::DirectorySeparatorChar)
            $target = [IO.Path]::GetFullPath($target)
            if (-not $target.StartsWith($Staging + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { Stop-CoatBootstrap "Private runtime extraction escaped staging." }
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $target))
            $out = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $inputStream = $entry.Open()
            try {
                $buffer = [byte[]]::new(65536); $written = 0L
                while (($count = $inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                    $written += $count
                    if ($written -gt $entry.Length -or $written -gt 67108864) { Stop-CoatBootstrap "Private runtime member exceeds declared size." }
                    $out.Write($buffer,0,$count)
                }
            } finally { $inputStream.Dispose(); $out.Dispose() }
            if ((Get-Item -LiteralPath $target).Length -ne $entry.Length) { Stop-CoatBootstrap "Truncated private runtime archive member." }
            $files[$relative] = @{size = $entry.Length; sha256 = Get-CoatSha256 $target}
        }
    } finally { $zip.Dispose() }
    if ((Get-CoatInventoryDigest ([pscustomobject]$files)) -cne $Manifest.artifacts.x86_64.inventory_sha256) {
        Stop-CoatBootstrap "Private runtime inventory does not match reviewed official NuGet."
    }
    $marker = @{schema_version=1; version=$Manifest.version; package_sha256=$Manifest.artifacts.x86_64.sha256; files=$files}
    [IO.File]::WriteAllText((Join-Path $Staging ".coating-python.json"), ($marker | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
}

function Install-CoatPrivateRuntime($Manifest, [string]$Directory, [string]$ProjectRoot, $Proxy, [bool]$Mirror) {
    [void](Assert-CoatUserPath $Directory $ProjectRoot)
    if (Test-Path -LiteralPath $Directory) {
        Test-CoatPrivateRuntime $Directory $Manifest
        return Test-CoatPython (Join-Path $Directory "python.exe") "x86_64" $false
    }
    Write-CoatBootstrap ("Project-private Python " + $Manifest.version + " x64. No system Python, EXE/MSI, registration or PATH change.")
    if ((Read-Host "Download and extract the pinned official NuGet package into this project? [y/N]") -notin @("y","Y","yes","YES")) { Stop-CoatBootstrap "Private Python deployment cancelled." }
    $parent = Split-Path -Parent $Directory
    [void][IO.Directory]::CreateDirectory($parent)
    $lockPath = Join-Path $parent "runtime-install.lock"
    $lock = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $temporary = Join-Path $parent ("coat-runtime-" + [Guid]::NewGuid().ToString("N"))
    try {
        [void](Assert-CoatUserPath $temporary $ProjectRoot)
        [void][IO.Directory]::CreateDirectory($temporary)
        $archive = Join-Path $temporary "python.nupkg"
        $staging = Join-Path $temporary "python"
        [void][IO.Directory]::CreateDirectory($staging)
        Save-CoatPrivateArchive $Manifest.artifacts.x86_64 $archive $Proxy $Mirror
        Expand-CoatPrivateArchive $archive $staging $Manifest
        $identity = Test-CoatPython (Join-Path $staging "python.exe") "x86_64" $false
        if (-not $identity -or $identity.version -ne $Manifest.version) { Stop-CoatBootstrap "Private Python post-extraction verification failed." }
        [void](Assert-CoatUserPath $Directory $ProjectRoot)
        [IO.Directory]::Move($staging, $Directory)
        return Test-CoatPython (Join-Path $Directory "python.exe") "x86_64" $false
    } finally {
        [void](Assert-CoatUserPath $temporary $ProjectRoot)
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
        $lock.Dispose()
        Remove-Item -LiteralPath $lockPath -Force
    }
}
