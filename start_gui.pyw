#!/usr/bin/env python3
"""start_gui.pyw - 双击启动图形界面（无任何 cmd 黑窗口）

.pyw 文件由 Windows 关联到 pythonw.exe（无控制台版 Python）运行，
双击后直接弹出图形界面，全程不出现命令行窗口。

若本文件双击无效（.pyw 关联被改），可改用桌面快捷方式「微信读书导出工具」，
或运行: python -c "import powershell 创建快捷方式" / 直接 pythonw gui.py
"""
import os
import sys

# 保证无论从哪里启动都能找到同目录下的 gui.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui

if __name__ == "__main__":
    gui.main()
