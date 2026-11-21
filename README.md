# PDF 工具箱（PdfToolBox）

一个 Windows 上的本地 PDF 小工具，两个页签搞定两件事：**批量打印 PDF** 和 **图片转 PDF**。全程本地处理、不联网、不上传文件，不依赖 WPS / Acrobat 等任何付费软件。

## 功能特性

### 1. 批量打印（PDF）
- 把多个 PDF（或整个文件夹）拖进窗口，一键静默打印，不需要任何会员。
- 可选打印机、份数、页范围（如 `1-3,5`）、是否双面。
- 实时进度条与逐文件日志，失败会写明原因（文件损坏 / 禁止打印 / 打印机离线等）。
- 可中途「停止」，已发送的文件会打完，其余不再发送。
- 打印引擎为 [SumatraPDF](https://github.com/sumatrapdfreader/sumatrapdf)（免费、开源、静默）。

### 2. 图片转 PDF
- 多张图片按列表顺序合并成一个 PDF（列表顺序即页序，可上移/下移/按名称排序）。
- 支持格式：jpg / jpeg / png / bmp / tif / tiff / webp / gif。
- 页面模式：
  - **A4（自动横竖，等比留白）** —— 适合直接打印，横图自动转横向页；
  - **原图尺寸（按 300 DPI 计）** —— 保持原始尺寸。
- 质量三档：高（95）/ 标准（88）/ 小体积（75）。
- **无损通道**（可选）：JPEG 原样嵌入、PNG 无损压缩，画质零损失，体积约等于原图总和。
- **逐个导出**（可选）：每张图各生成一个 PDF，文件名沿用原图名，重名自动加 `_2` 序号。
- 透明 PNG 自动铺白底后再合成。

## 目录结构

```
PdfToolBox/
├── pdf_toolbox.py       # 主程序（GUI + 引擎 + 自检）
├── build.bat            # PyInstaller 打包脚本
├── PrintPDF.ps1         # 命令行批量打印（PowerShell 版）
├── PrintPDF.bat         # PrintPDF.ps1 的启动器
├── config.example       # 配置文件样例（可提交）
├── config.ini           # 本机真实配置（已忽略，需自行创建）
├── 使用说明.txt          # 面向终端用户的使用说明
└── README.md
```

## 快速开始

### 方式一：直接跑源码

需要本机装有 Python 3.9+（推荐 3.11+），并安装依赖：

```bash
pip install pillow img2pdf tkinterdnd2
python pdf_toolbox.py
```

> 依赖是**可选降级**的：没有 `Pillow` 则图片转 PDF 不可用；没有 `img2pdf` 则无损模式禁用；没有 `tkinterdnd2` 则拖放失效（仍可用「添加文件」按钮）。都不会导致程序崩溃。

### 方式二：运行打包好的 exe

用 `build.bat` 打包出 `dist/PdfToolbox.exe`（单文件），双击即可运行，无需安装 Python。

## 配置

需要用到外部 SumatraPDF 时，把它的完整路径写入配置文件。**本机配置文件为 `config.ini`，不随仓库上传**；仓库只保留 `config.example` 样例。

```bash
# 1. 复制样例
copy config.example config.ini

# 2. 编辑 config.ini，填入真实路径
#    sumatra_path=C:\path\to\SumatraPDF.exe
```

格式：`key=value`（等号前后不加空格，`#` 开头为注释）。目前只有一项：

| 键 | 说明 |
|---|---|
| `sumatra_path` | SumatraPDF 可执行文件的完整路径（批量打印引擎） |

打印引擎查找优先级（任一步命中即停止）：

1. 环境变量 `SUMATRA_EXE`
2. `config.ini` 里的 `sumatra_path`
3. `SumatraPath.txt`（旧版单行配置，向后兼容）
4. 程序同级目录下的 `SumatraPDF*.exe`
5. 常见安装路径（`%LOCALAPPDATA%\SumatraPDF`、`Program Files` 等）
6. 系统 `PATH`

## 打包

运行 `build.bat`，产出单文件版 `dist/PdfToolbox.exe`（内嵌 SumatraPDF 引擎）。

构建依赖：`pip install pyinstaller`，并确认 `config.ini` 已就绪（脚本会自动读取其中的 `sumatra_path` 用于打包进 exe）。

## 自检

无需打开界面即可验证环境与引擎是否正常：

```bash
python pdf_toolbox.py --selftest <输出文件路径>
```

输出包含版本、依赖、打印机枚举、打印冒烟、PDF 合成 / 无损 / 逐个导出等检查项，末尾 `RESULT: OK` 表示通过（退出码 0）。

## 命令行批量打印（PrintPDF.ps1）

不想开 GUI 时，也可以用 PowerShell 脚本批量打印：

```powershell
.\PrintPDF.ps1                                    # 交互式选择文件夹
.\PrintPDF.ps1 -Folder "D:\invoices" -Recurse     # 递归打印某目录
.\PrintPDF.ps1 -Folder "D:\invoices" -Printer "HP LaserJet M404" -Copies 2
.\PrintPDF.ps1 -Folder "D:\invoices" -DryRun      # 只列出会打印什么，不真的打印
```

同样读取 `config.ini` / `SumatraPath.txt` 定位 SumatraPDF，打印结果写入 `PrintLog_*.txt` 日志。

## 技术栈

- **语言 / GUI**：Python 3 + tkinter
- **拖放**：tkinterdnd2
- **图片处理**：Pillow
- **无损转 PDF**：img2pdf
- **打印引擎**：SumatraPDF
- **打包**：PyInstaller

## 依赖清单

| 依赖 | 必需 | 用途 |
|---|---|---|
| Python 3.9+ / tkinter | ✅ | 程序本体 |
| Pillow | 可选 | 图片转 PDF（标准通道） |
| img2pdf | 可选 | 图片转 PDF（无损通道） |
| tkinterdnd2 | 可选 | 拖放文件 |
| SumatraPDF | 打印时 | 批量打印引擎 |

## 许可与致谢

- SumatraPDF：GPLv3，[项目主页](https://github.com/sumatrapdfreader/sumatrapdf)
- 本项目仅调用 SumatraPDF 的命令行打印能力，未修改其源码。
