#!/usr/bin/env python3
"""create_shortcut.py - 一键创建桌面快捷方式「微信读书导出工具」

快捷方式直接指向 pythonw.exe 运行 gui.py，双击启动图形界面，
全程不会出现任何 cmd 黑窗口（.bat 双击必然闪现控制台，故提供本方案）。

用法:
    python create_shortcut.py
"""
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def find_pythonw():
    """优先使用项目虚拟环境内的 pythonw，其次系统 PATH 中的"""
    venv_pyw = os.path.join(BASE, ".venv", "Scripts", "pythonw.exe")
    if os.path.isfile(venv_pyw):
        return venv_pyw
    return shutil.which("pythonw")


def main():
    pyw = find_pythonw()
    if not pyw:
        print("❌ 未找到 pythonw.exe，请确认已安装 Python 并勾选加入 PATH")
        sys.exit(1)

    # PowerShell 通过 WScript.Shell COM 创建快捷方式；中文路径需保存为带 BOM 的 UTF-8 供 PS 5.1 正确读取
    def q(s):
        return s.replace("'", "''")

    ps = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$desktop = [Environment]::GetFolderPath('Desktop')",
        "$lnk = Join-Path $desktop '微信读书导出工具.lnk'",
        "$ws = New-Object -ComObject WScript.Shell",
        "$s = $ws.CreateShortcut($lnk)",
        "$s.TargetPath = '%s'" % q(pyw),
        "$s.Arguments = '\"%s\"'" % q(os.path.join(BASE, "gui.py")),
        "$s.WorkingDirectory = '%s'" % q(BASE),
        "$s.IconLocation = '%s,0'" % q(pyw),
        "$s.Description = 'WeRead Book Exporter'",
        "$s.Save()",
        "Write-Output \"OK: $lnk\"",
    ])
    tmp = os.path.join(BASE, "_mkshortcut.ps1")
    with open(tmp, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(ps)

    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp],
            capture_output=True, timeout=30,
        )
        out = r.stdout or b""
        for enc in ("utf-8", "gbk"):
            try:
                text = out.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = out.decode("utf-8", errors="replace")
        print(text.strip())
        if r.returncode != 0:
            print("❌ 快捷方式创建失败，退出码 %s" % r.returncode)
            print((r.stderr or b"").decode("gbk", errors="replace"))
            sys.exit(1)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print("🎉 桌面快捷方式已创建/更新：微信读书导出工具（双击即可无黑窗口启动图形界面）")


if __name__ == "__main__":
    main()
