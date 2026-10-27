<#
.SYNOPSIS
    深空之眼（B 服）一键启动：拉起 MuMu 模拟器 -> 连通 ADB -> 按包名启动游戏 -> 校验前台。

.DESCRIPTION
    本脚本是 G:\MBE（MaaPracticeBoilerplate 结构）项目的外围启动器。

    MaaFramework 侧的等价逻辑在 assets/resource/pipeline/start_game.json：
        "启动深空之眼": { "action": "StartApp", "package": "com.yongshi.tenojo.bilibili" }
    StartApp 只负责"在已连通的设备上按包名拉起应用"，它不会启动模拟器本身，
    因此这里补齐"模拟器进程 + ADB 端口"这段准备工作，之后既可以直接跑本脚本，
    也可以交给 MaaPiCli / MFA 去跑上面的 pipeline 节点。

.PARAMETER Package
    目标应用包名。默认深空之眼 B 服：com.yongshi.tenojo.bilibili

.PARAMETER Instance
    MuMu 多开实例编号（0 = 主实例），默认 0。

.PARAMETER MuMuRoot
    MuMu 12 安装根目录，默认按常见路径自动探测。

.PARAMETER Adb
    adb.exe 路径，默认依次尝试 C:\adb\adb.exe、PATH、MuMu 自带 adb。

.PARAMETER TimeoutSec
    等待 Android 系统与 ADB 就绪的最长秒数，默认 180。

.PARAMETER NoStart
    不自动拉起模拟器，假定它已经在运行。

.PARAMETER DryRun
    只打印将要执行的命令，不实际执行（用于确认参数与路径）。

.EXAMPLE
    .\launch_game.ps1

.EXAMPLE
    .\launch_game.ps1 -DryRun

.EXAMPLE
    .\launch_game.ps1 -Package com.yongshi.tenojo -Instance 1
#>
[CmdletBinding()]
param(
    [string]$Package  = "com.yongshi.tenojo.bilibili",
    [string]$Instance = "0",
    [string]$MuMuRoot = "",
    [string]$Adb      = "",
    [int]$TimeoutSec  = 180,
    [switch]$NoStart,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$script:ExitCode = 0

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Text)
    Write-Host "    OK   $Text" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Text)
    Write-Host "    WARN $Text" -ForegroundColor Yellow
}

function Invoke-External {
    param(
        [string]$Exe,
        [string[]]$Arguments
    )
    $pretty = "$Exe $($Arguments -join ' ')"
    Write-Host "    > $pretty" -ForegroundColor DarkGray
    if ($DryRun) { return $null }
    $out = & $Exe @Arguments 2>&1
    return $out
}

function Resolve-MuMuRoot {
    param([string]$Hint)
    if ($Hint) {
        if (Test-Path (Join-Path $Hint "nx_main\MuMuManager.exe")) { return $Hint }
        throw "指定的 -MuMuRoot 下找不到 nx_main\MuMuManager.exe：$Hint"
    }
    $candidates = @(
        "C:\Program Files\Netease\MuMuPlayer-12.0",
        "C:\Program Files\Netease\MuMu Player 12",
        "D:\Program Files\Netease\MuMuPlayer-12.0",
        "E:\Program Files\Netease\MuMuPlayer-12.0"
    )
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c "nx_main\MuMuManager.exe")) { return $c }
    }
    $found = Get-ChildItem "C:\Program Files\Netease" -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName "nx_main\MuMuManager.exe") } |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    throw "未找到 MuMu 12 安装目录，请用 -MuMuRoot 指定（应包含 nx_main\MuMuManager.exe）。"
}

function Resolve-AdbExe {
    param([string]$Hint, [string]$Root)
    if ($Hint) {
        if (Test-Path $Hint) { return (Resolve-Path $Hint).Path }
        throw "指定的 -Adb 路径不存在：$Hint"
    }
    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @("C:\adb\adb.exe", (Join-Path $Root "nx_main\adb.exe"))) {
        if (Test-Path $p) { return $p }
    }
    throw "未找到 adb.exe，请用 -Adb 指定。"
}

function Get-MuMuInstanceInfo {
    param([string]$Manager, [string]$Index)
    $raw = & $Manager info -v $Index 2>$null
    if (-not $raw) { return $null }
    try { return (($raw -join "`n") | ConvertFrom-Json) } catch { return $null }
}

function Wait-Until {
    param(
        [scriptblock]$Condition,
        [int]$TimeoutSec,
        [string]$What,
        [int]$IntervalSec = 2
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Seconds $IntervalSec
    }
    Write-Warn "等待超时（${TimeoutSec}s）：$What"
    return $false
}

# ---------------------------------------------------------------- 0. 解析环境
Write-Step "解析环境"
$MuMuRoot = Resolve-MuMuRoot -Hint $MuMuRoot
$Manager  = Join-Path $MuMuRoot "nx_main\MuMuManager.exe"
$AdbExe   = Resolve-AdbExe -Hint $Adb -Root $MuMuRoot

Write-Ok "MuMu          $MuMuRoot"
Write-Ok "MuMuManager   $Manager"
Write-Ok "adb           $AdbExe"
Write-Ok "包名          $Package"
Write-Ok "实例          $Instance"
if ($DryRun) { Write-Warn "DryRun 模式：以下只打印命令，不会真的执行" }

# ------------------------------------------------------- 1. 确保模拟器已启动
Write-Step "确保 MuMu 实例 $Instance 已启动"
$info = Get-MuMuInstanceInfo -Manager $Manager -Index $Instance
if ($NoStart) {
    Write-Ok "指定了 -NoStart，跳过拉起"
} elseif ($info -and $info.is_android_started) {
    Write-Ok "Android 已在运行（player_state=$($info.player_state)）"
} else {
    Write-Ok "实例未就绪，拉起中"
    Invoke-External -Exe $Manager -Arguments @("control", "-v", $Instance, "launch") | Out-Null
    if ($DryRun) {
        Write-Host "    (DryRun: 跳过等待)"
    } else {
        $started = Wait-Until -TimeoutSec $TimeoutSec -What "Android 系统启动" -Condition {
            $i = Get-MuMuInstanceInfo -Manager $Manager -Index $Instance
            return ($i -and $i.is_android_started)
        }
        if (-not $started) {
            throw "模拟器在 ${TimeoutSec}s 内没有进入 Android 就绪状态，请手动检查 MuMu。"
        }
    }
}

# ------------------------------------------------------------- 2. 取 ADB 端口
Write-Step "获取实例 $Instance 的 ADB 端口"
if ($DryRun) {
    Write-Host "    > $Manager adb -v $Instance" -ForegroundColor DarkGray
    $Serial = "<host:port>"
} else {
    $adbRaw = (& $Manager adb -v $Instance) -join "`n"
    Write-Host "    $($adbRaw -replace "`n", ' ')" -ForegroundColor DarkGray
    $adbInfo = $adbRaw | ConvertFrom-Json
    $Serial  = "$($adbInfo.adb_host):$($adbInfo.adb_port)"
}
Write-Ok "设备序列号 $Serial"

# --------------------------------------------------------- 3. 连接并等待就绪
Write-Step "连接 ADB 并等待设备在线"
Invoke-External -Exe $AdbExe -Arguments @("connect", $Serial) | Out-Null
if ($DryRun) {
    Write-Host "    (DryRun: 跳过等待)"
    $online = $true
} else {
    $online = Wait-Until -TimeoutSec $TimeoutSec -What "adb 设备可达" -Condition {
        $state = (& $AdbExe -s $Serial get-state 2>$null) -join ""
        return ($state -match "device")
    }
    if (-not $online) {
        throw "adb 无法连通 $Serial，请检查模拟器设置里的「ADB 调试」是否开启。"
    }
}
Write-Ok "设备在线 $Serial"

# ------------------------------------------------------ 4. 解析启动 Activity
Write-Step "解析 $Package 的启动 Activity"
$Activity = $null
if (-not $DryRun) {
    $resolved = & $AdbExe -s $Serial shell cmd package resolve-activity --brief $Package 2>$null
    $Activity = ($resolved | Where-Object { $_ -match "/" } | Select-Object -Last 1)
}
if (-not $Activity -and -not $DryRun) {
    Write-Warn "resolve-activity 未返回结果，改用 monkey 方式启动"
} elseif ($Activity) {
    Write-Ok $Activity
}

# ----------------------------------------------------------- 5. 按包名启动
Write-Step "启动 $Package"
if ($Activity) {
    Invoke-External -Exe $AdbExe -Arguments @("-s", $Serial, "shell", "am", "start", "-n", $Activity) | Out-Null
} else {
    Invoke-External -Exe $AdbExe -Arguments @(
        "-s", $Serial, "shell", "monkey", "-p", $Package,
        "-c", "android.intent.category.LAUNCHER", "1"
    ) | Out-Null
}

# --------------------------------------------------------- 6. 校验前台进程
Write-Step "校验应用已在前台"
$running = $false
if (-not $DryRun) {
    $running = Wait-Until -TimeoutSec 60 -What "$Package 进入前台" -IntervalSec 3 -Condition {
        $top = & $AdbExe -s $Serial shell "dumpsys activity activities | grep -m1 -E 'ResumedActivity|mResumedActivity'" 2>$null
        if (-not $top) {
            $dump = & $AdbExe -s $Serial shell dumpsys activity activities 2>$null
            $hit = $dump | Select-String -Pattern $Package -SimpleMatch | Select-Object -First 1
            return [bool]$hit
        }
        return (($top -join "") -match [regex]::Escape($Package))
    }
}

if ($DryRun) {
    Write-Step "DryRun 结束"
} elseif ($running) {
    Write-Host ""
    Write-Host "启动完成：$Package 已在前台运行。" -ForegroundColor Green
} else {
    Write-Warn "进程已拉起，但 60s 内没在前台检测到 $Package（可能仍在加载，去看一眼窗口）。"
    $script:ExitCode = 1
}

Write-Host ""
Write-Host "提示：MaaFramework 内部用的是同一套包名，节点见 assets\resource\pipeline\start_game.json" -ForegroundColor DarkGray
Write-Host "      在 MFA / MaaPiCli 里选「启动游戏」任务即可复用该节点。" -ForegroundColor DarkGray

exit $script:ExitCode
