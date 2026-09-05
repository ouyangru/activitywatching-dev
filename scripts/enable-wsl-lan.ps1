[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string]$RemoteAddress = 'LocalSubnet'
)

$ErrorActionPreference = 'Stop'
$wslCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
$ruleName = "ActivityTimelineWsl$Port"

$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from a PowerShell window opened as Administrator.'
}

$existing = Get-NetFirewallHyperVRule -Name $ruleName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    $ruleParameters = @{
        Name = $ruleName
        DisplayName = "Activity Timeline WSL TCP $Port"
        Direction = 'Inbound'
        VMCreatorId = $wslCreatorId
        Protocol = 'TCP'
        LocalPorts = $Port
        RemoteAddresses = $RemoteAddress
    }
    New-NetFirewallHyperVRule @ruleParameters | Out-Null
    Write-Host "Created a Hyper-V firewall rule for TCP $Port from $RemoteAddress."
} else {
    Write-Host "The Hyper-V firewall rule already exists: $ruleName"
}

$addresses = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike '127.*' -and
        $_.InterfaceAlias -notlike '*WSL*' -and
        $_.InterfaceAlias -notlike '*VMware*' -and
        $_.PrefixOrigin -ne 'WellKnown'
    } |
    Select-Object -ExpandProperty IPAddress

Write-Host 'Connect the phone and PC to the same LAN, then try these URLs in the Android app:'
foreach ($address in $addresses) {
    Write-Host "  http://${address}:$Port"
}
