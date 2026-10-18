"""常见软件与开发环境的中文用途说明库。

扫描清单 / 打开清单时，自动为每个条目填充 desc（用途说明），
让时间久了的用户也能一眼看出某个软件是做什么用的。
用户可以在界面里手动修改这些说明。

匹配顺序：norm_core(name) 精确键 → 安装标识（winget ID 等）→ 正则模式 → 类别兜底。
宁缺毋滥：识别不准的不写，留给用户自己填，不编造功能。
"""
from __future__ import annotations

import re

from .model import CAT_CONFIG, CAT_LIB, CAT_NPM, CAT_PIP, CAT_STORE, CAT_WSL, norm_core

# ---------------- 精确说明库 ----------------
# key = norm_core(软件名) 或小写安装标识；值为一句中文用途说明
_DB = {
    # --- 本机扫描到的条目 ---
    "git": "Git 分布式版本控制系统，管理代码历史与协作，开发环境的基础组件",
    "office 专业增强版": "微软 Office 办公套件（Word / Excel / PowerPoint 等），文档处理必备",
    "potplayer": "PotPlayer 视频播放器，KMPlayer 原班人马开发，本地视频播放格式支持最全",
    "potplayer-64 bit": "PotPlayer 视频播放器，KMPlayer 原班人马开发，本地视频播放格式支持最全",
    "vlc media player": "VLC 媒体播放器，VideoLAN 出品的开源播放器，几乎支持所有音视频格式",
    "winrar (64 位)": "WinRAR 压缩/解压缩工具，支持 RAR / ZIP 等格式，老牌压缩软件",
    "winrar": "WinRAR 压缩/解压缩工具，支持 RAR / ZIP 等格式，老牌压缩软件",
    "typora": "Typora Markdown 编辑器，所见即所得，写文档/笔记/博客常用",
    "node.js": "Node.js：基于 Chrome V8 的 JavaScript 运行时，前端工程化与后端服务的基础环境",
    "nodejs": "Node.js：基于 Chrome V8 的 JavaScript 运行时，前端工程化与后端服务的基础环境",
    "build of openjdk": "OpenJDK（Eclipse Temurin 发行版）：Java 开发工具包，运行和开发 Java 程序用",
    "temurin": "Eclipse Temurin：OpenJDK 的免费发行版，运行和开发 Java 程序用",
    "nvidia 图形驱动程序": "NVIDIA 显卡驱动程序，游戏/渲染/AI 计算依赖它，重装系统后必须重装",
    "rivo vpn version": "Rivo VPN：网络代理工具，用于访问国际网络",
    "wemp": "WEMP：Windows 平台集成 Web 运行环境（Nginx/MySQL/PHP 一类），本地搭网站/调试 PHP 用",
    "互传": "互传：手机与电脑之间无线互传文件的工具（vivo/OPPO/小米互传联盟）",
    "google chrome": "Google Chrome 谷歌浏览器，最主流的上网浏览器",
    "chrome": "Google Chrome 谷歌浏览器，最主流的上网浏览器",
    "internet download manager": "IDM 多线程下载加速器，嗅探网页视频/文件并提速下载",
    "lav filters": "LAV Filters：开源 DirectShow 解码器，给播放器提供通用的视频/音频解码能力",
    "visio professional": "Microsoft Visio：微软图表绘制软件，画流程图/组织架构图/网络拓扑图",
    "qq": "QQ：腾讯出品的即时通讯软件，聊天/传文件/群组",
    "watt toolkit": "Watt Toolkit（原 Steam++）：Steam 等游戏平台网络加速与社区工具",
    "微信": "微信 PC 客户端：腾讯微信的电脑版，聊天/传文件/小程序",
    "wechat": "微信 PC 客户端：腾讯微信的电脑版，聊天/传文件/小程序",
    "virtualtablet server": "VirtualTablet：把手机/平板当成电脑数位板（手写输入）的服务端",
    "asus smart gesture": "华硕笔记本触控板手势驱动：多指手势、边缘滑动等功能",
    "smart gesture": "触控板手势驱动程序（笔记本厂商预装）",
    "atk package": "ATK Package：华硕热键驱动包，Fn 组合键（音量/亮度/飞行模式等）依赖它",
    "realtek high definition audio driver": "Realtek 声卡驱动程序，电脑发声/耳机/麦克风依赖它",
    "realtek": "Realtek 硬件驱动程序（声卡/网卡类）",
    "visual c++ 2012 redistributable": "微软 VC++ 2012 运行库：较老的软件运行必备组件",
    "visual c++ 2015-2022 redistributable": "微软 VC++ 2015-2022 运行库：大量现代软件运行必备，重装系统后必装",
    "百度网盘": "百度网盘客户端：云端存储、文件同步与分享",
    "deepseek harness": "DeepSeek Harness：DeepSeek 的命令行工具/本地模型运行环境",
    "workbuddy": "WorkBuddy：AI 助手桌面客户端",
    "workbuddysharetarget": "WorkBuddy 的系统分享集成（把文件分享到 WorkBuddy）",
    "qoder cn": "Qoder：阿里出品的 AI 编程助手（Agent 编程环境）",
    "visual studio code": "VS Code：微软轻量级代码编辑器，插件生态最丰富，开发主力工具",
    "vscode": "VS Code：微软轻量级代码编辑器，插件生态最丰富，开发主力工具",
    "traecode cn": "Trae：字节跳动出品的 AI 编程编辑器",
    "trae": "Trae：字节跳动出品的 AI 编程编辑器",
    "夸克": "夸克浏览器：阿里巴巴出品的极简浏览器，带 AI 总结/网盘",
    "microsoftpcmanager": "微软电脑管家：微软官方的系统清理/加速/安全工具",
    "python": "Python 解释器与标准库：编程/脚本/自动化/数据分析的常用语言环境",
    "npm": "npm：Node.js 的包管理器，安装 JS 命令行工具和库",
    "corepack": "corepack：Node.js 自带的包管理器版本管理工具（管理 yarn/pnpm 版本）",
    "@tencent/slidep": "腾讯发布的 npm 命令行工具（幻灯片/文档方向）",
    # --- 常见即时通讯/协作 ---
    "tim": "TIM：腾讯出品的精简版 QQ，主打办公协作",
    "企业微信": "企业微信：腾讯办公协同工具，与微信互通",
    "钉钉": "钉钉：阿里巴巴办公协同平台，考勤/审批/即时通讯",
    "飞书": "飞书：字节跳动办公协同套件，文档/会议/即时通讯",
    "腾讯会议": "腾讯会议：视频会议软件",
    "zoom": "Zoom：国际主流视频会议软件",
    "telegram": "Telegram：注重隐私的跨平台即时通讯工具",
    "discord": "Discord：海外流行的社群语音/文字聊天工具，游戏圈子常用",
    "slack": "Slack：海外企业协作通讯工具",
    # --- 浏览器 ---
    "microsoft edge": "Microsoft Edge：Windows 自带的浏览器（系统组件，一般无需恢复）",
    "firefox": "Firefox 火狐浏览器：Mozilla 出品的开源浏览器",
    # --- 影音娱乐 ---
    "网易云音乐": "网易云音乐：流媒体音乐播放器，侧重歌单与评论社区",
    "qq音乐": "QQ音乐：腾讯出品的流媒体音乐播放器",
    "酷狗音乐": "酷狗音乐：流媒体音乐播放器",
    "喜马拉雅": "喜马拉雅：有声书/播客平台客户端",
    "爱奇艺": "爱奇艺：视频流媒体平台客户端",
    "腾讯视频": "腾讯视频：流媒体视频平台客户端",
    "哔哩哔哩": "哔哩哔哩（B站）：视频/直播/弹幕社区客户端",
    "potplayer mini": "PotPlayer 视频播放器",
    "mpv": "MPV：开源极简命令行播放器，画质与解码能力强",
    "kodi": "Kodi：开源家庭影院播放器，搭建本地影视库常用",
    # --- 下载/传输 ---
    "迅雷": "迅雷：P2P 下载工具，BT/磁力/ed2k 下载",
    "qBittorrent": "qBittorrent：开源 BT 下载客户端，无广告",
    "motrix": "Motrix：开源全能下载器，支持 BT/直链",
    "free download manager": "FDM：免费多线程下载管理器",
    # --- 输入法/办公 ---
    "搜狗输入法": "搜狗输入法：主流中文拼音输入法，词库与云输入",
    "qq输入法": "QQ输入法：腾讯出品的中文输入法",
    "wps office": "WPS Office：金山办公套件，Word/Excel/PPT 的国产替代，免费版够用",
    "福昕阅读器": "福昕 PDF 阅读器：PDF 查看/标注工具",
    "adobe acrobat": "Adobe Acrobat：PDF 编辑/阅读专业软件",
    "cajviewer": "CAJViewer：知网 CAJ/NH 论文格式阅读器，看中文学术论文用",
    # --- 笔记/知识管理 ---
    "obsidian": "Obsidian：本地双链笔记软件，构建个人知识库",
    "notion": "Notion：海外流行的 All-in-one 笔记/数据库/协作工具",
    "印象笔记": "印象笔记：老牌云笔记工具",
    "xmind": "XMind：思维导图软件",
    "anki": "Anki：间隔重复记忆卡软件，背单词/考证神器",
    "calibre": "Calibre：电子书管理转换工具，Kindle 用户必备",
    # --- 压缩/文件管理 ---
    "7-zip": "7-Zip：开源压缩软件，7z/zip 格式，无广告",
    "bandizip": "Bandizip：轻量压缩软件，界面友好",
    "nanazip": "NanaZip：基于 7-Zip 的现代 Windows 压缩工具",
    "everything": "Everything：文件名秒级全文搜索工具，找文件神器",
    "listary": "Listary：文件快速定位与启动增强工具",
    "utools": "uTools：插件化效率工具箱（搜索/翻译/截图/剪贴板）",
    "quicker": "Quicker：快捷动作面板，自动化常用操作",
    "ditto": "Ditto：剪贴板历史管理工具",
    "snipaste": "Snipaste：截图+贴图工具，F1 截图 F3 贴回屏幕",
    "faststone capture": "FSCapture：老牌截图/录屏/取色工具",
    "powertoys": "PowerToys：微软官方高级工具集（分屏/批量重命名/快速启动）",
    "treesize": "TreeSize：磁盘空间分析工具，找出占空间的大文件夹",
    "spacesniffer": "SpaceSniffer：磁盘占用可视化工具",
    # --- 系统工具/安全 ---
    "火绒安全": "火绒安全：轻量无广告的国产杀毒/防护软件",
    "360安全卫士": "360安全卫士：系统清理/防护工具（口碑两极分化，按需）",
    "腾讯电脑管家": "腾讯电脑管家：腾讯出品的系统清理/防护工具",
    "geek uninstaller": "Geek Uninstaller：强制卸载+残留清理工具",
    "revo uninstaller": "Revo Uninstaller：卸载工具，可扫描残留文件和注册表",
    "ccleaner": "CCleaner：系统垃圾清理工具（国外老牌）",
    "driver booster": "Driver Booster：驱动更新工具",
    "驱动精灵": "驱动精灵：驱动检测/安装/更新工具",
    "驱动人生": "驱动人生：驱动检测/安装/更新工具",
    # --- 远程/网络 ---
    "teamviewer": "TeamViewer：远程桌面控制工具",
    "todesk": "ToDesk：国产远程桌面控制工具",
    "向日葵": "向日葵远程控制：国产远程控制工具",
    "rustdesk": "RustDesk：开源自建远程桌面工具",
    "anydesk": "AnyDesk：轻量远程桌面工具",
    "clash": "Clash：规则分流网络代理工具",
    "v2rayn": "V2rayN：V2Ray 代理图形客户端",
    "nekoray": "Nekoray：代理工具客户端",
    "postman": "Postman：API 接口调试工具，前后端联调必备",
    "apifox": "Apifox：API 文档/调试/Mock 一体化平台（国产 Postman）",
    "filezilla": "FileZilla：开源 FTP/SFTP 文件传输客户端",
    "winscp": "WinSCP：Windows 下 SFTP/SCP/FTP 图形客户端",
    "xshell": "Xshell：功能最强的 SSH 终端客户端（商业，个人免费）",
    "mobaxterm": "MobaXterm：SSH/RDP/X11 一体化终端，远程运维神器",
    "putty": "PuTTY：经典轻量 SSH/Telnet 客户端",
    "termius": "Termius：跨平台 SSH 客户端，界面现代",
    # --- 开发环境/语言 ---
    "go": "Go 语言工具链：编译运行 Go 程序",
    "golang": "Go 语言工具链：编译运行 Go 程序",
    "rust": "Rust 语言工具链（rustup）：编译运行 Rust 程序",
    ".net": ".NET 运行时/SDK：运行和开发 .NET 程序",
    "dotnet": ".NET 运行时/SDK：运行和开发 .NET 程序",
    "ruby": "Ruby 解释器与工具链",
    "php": "PHP 解释器与工具链，网站后端常用",
    "perl": "Perl 解释器，文本处理/系统脚本",
    "lua": "Lua 脚本语言，常作嵌入式/游戏脚本",
    "cmake": "CMake：跨平台构建系统生成器，C/C++ 项目编译必备",
    "gradle": "Gradle：JVM 系构建工具，Android/Java 项目常用",
    "maven": "Maven：Java 项目构建与依赖管理工具",
    "docker desktop": "Docker Desktop：容器化运行环境，一键起数据库/中间件",
    "docker": "Docker 容器引擎/桌面版",
    "subversion": "SVN 版本控制系统（集中式，老项目用）",
    "sourcetree": "SourceTree：Git 图形客户端，可视化管理代码仓库",
    "github desktop": "GitHub Desktop：GitHub 官方图形客户端",
    "fork": "Fork：轻量 Git 图形客户端",
    # --- IDE/编辑器 ---
    "visual studio": "Visual Studio：微软全功能 IDE，C++/C#/.NET 开发",
    "intellij idea": "IntelliJ IDEA：JetBrains 出品的 Java 旗舰 IDE",
    "pycharm": "PyCharm：JetBrains 出品的 Python IDE",
    "webstorm": "WebStorm：JetBrains 出品的前端 IDE",
    "clion": "CLion：JetBrains 出品的 C/C++ IDE",
    "goland": "GoLand：JetBrains 出品的 Go IDE",
    "phpstorm": "PhpStorm：JetBrains 出品的 PHP IDE",
    "rider": "Rider：JetBrains 出品的 .NET/C# IDE",
    "datagrip": "DataGrip：JetBrains 出品的数据库管理工具",
    "android studio": "Android Studio：安卓应用开发 IDE",
    "eclipse": "Eclipse：老牌 Java IDE",
    "netbeans": "NetBeans：开源 Java IDE",
    "dev-c++": "Dev-C++：轻量 C/C++ 学习 IDE",
    "code::blocks": "Code::Blocks：开源 C/C++ IDE",
    "sublime text": "Sublime Text：极速启动的轻量代码编辑器",
    "vim": "Vim：终端文本编辑器，效率极高但学习曲线陡",
    "neovim": "Neovim：现代化 Vim 分支，插件生态活跃",
    "notepad++": "Notepad++：轻量文本编辑器，看日志/改配置顺手",
    "hbuilder": "HBuilderX：国产前端 IDE，uni-app 开发常用",
    # --- 数据库 ---
    "mysql": "MySQL 数据库服务端/客户端，最常见的关系型数据库",
    "navicat": "Navicat：数据库图形管理工具，支持 MySQL/Redis 等",
    "dbeaver": "DBeaver：开源通用数据库管理工具",
    "redis": "Redis 内存数据库：缓存/消息队列常用组件",
    "mongodb": "MongoDB 文档数据库及其 Compass 管理工具",
    "sql server": "SQL Server：微软关系型数据库",
    # --- 游戏平台 ---
    "steam": "Steam：全球最大 PC 游戏平台",
    "epic games": "Epic 游戏商城：每周送游戏，虚幻引擎同门",
    "battle.net": "战网：暴雪游戏平台（魔兽/守望/炉石）",
    "wegame": "WeGame：腾讯游戏平台（英雄联盟/穿越火线）",
    "米游社": "米游社：米哈游游戏社区客户端",
    "小黑盒": "小黑盒：Steam 游戏资讯/战绩工具",
    # --- 设计/图像 ---
    "photoshop": "Photoshop：Adobe 图像处理软件，设计/修图标配",
    "illustrator": "Illustrator：Adobe 矢量图形软件",
    "figma": "Figma：在线协作 UI 设计工具，界面设计主流",
    "gimp": "GIMP：开源图像处理软件（免费 PS 替代）",
    "inkscape": "Inkscape：开源矢量绘图软件",
    "paint.net": "Paint.NET：轻量图像编辑器",
    "irfanview": "IrfanView：极速看图工具",
    "blender": "Blender：开源 3D 建模/动画/渲染全能软件",
    "cinema 4d": "C4D：三维动画/建模软件，动态设计常用",
    # --- 硬件/驱动类 ---
    "罗技 g hub": "罗技 G HUB：罗技外设驱动与灯效管理",
    "雷蛇 synapse": "雷云 Synapse：雷蛇外设驱动与灯效管理",
    "海盗船 icue": "iCUE：海盗船硬件灯效/性能管理",
    "wacom": "Wacom 数位板驱动",
    # --- 录屏/直播/剪辑 ---
    "obs studio": "OBS Studio：开源直播/录屏软件，游戏直播与屏幕录制标配",
    "obs": "OBS Studio：开源直播/录屏软件，游戏直播与屏幕录制标配",
    "剪映": "剪映：字节出品的视频剪辑工具，自动字幕/特效一键完成",
    "格式工厂": "格式工厂：音视频/图片多媒体格式转换工具",
    "screentogif": "ScreenToGif：录制屏幕为 GIF 动图的小工具",
    "licecap": "LICEcap：轻量 GIF 屏幕录制工具",
    "sharex": "ShareX：开源截图/录屏/上传一体化工具",
    "bandicam": "Bandicam：游戏/屏幕录制软件",
    "camtasia": "Camtasia：屏幕录制与视频编辑软件",
    "ev录屏": "EV 录屏：国产录屏软件",
    "adobe premiere pro": "Premiere Pro：Adobe 专业视频剪辑软件",
    "adobe after effects": "After Effects：Adobe 特效/动效软件",
    "davinci resolve": "达芬奇调色：免费版功能极强的剪辑/调色/特效软件",
    "vegas pro": "Vegas Pro：老牌视频剪辑软件",
    "edius": "Edius：广播级非线性编辑软件",
    "会声会影": "会声会影：入门级视频编辑软件",
    "爱剪辑": "爱剪辑：入门级视频剪辑软件",
    # --- 虚拟机/容器/运维 ---
    "virtualbox": "VirtualBox：甲骨文出品的免费虚拟机软件",
    "vmware workstation": "VMware Workstation：老牌虚拟机软件",
    "vagrant": "Vagrant：虚拟机开发环境管理工具",
    "docker compose": "Docker Compose：多容器应用编排工具",
    "kubernetes": "Kubernetes：容器编排平台（K8s）",
    "kubectl": "kubectl：Kubernetes 命令行管理工具",
    "helm": "Helm：Kubernetes 应用包管理工具",
    "minikube": "Minikube：本地单节点 Kubernetes",
    "portainer": "Portainer：容器可视化管理面板",
    "terraform": "Terraform：基础设施即代码工具",
    "ansible": "Ansible：无 Agent 自动化运维工具",
    "jenkins": "Jenkins：CI/CD 持续集成服务器",
    "wireshark": "Wireshark：网络抓包分析工具，排查网络问题必备",
    "nmap": "Nmap：网络扫描与安全审计工具",
    "charles": "Charles：HTTP 抓包代理，App/网页接口调试用",
    "fiddler": "Fiddler：HTTP 调试抓包工具",
    "npcap": "Npcap：Windows 抓包驱动库（Wireshark 依赖）",
    "winpcap": "WinPcap：旧版抓包驱动库",
    "burp suite": "Burp Suite：Web 安全测试/渗透工具",
    "postman" : "Postman：API 接口调试工具",
    "insomnia": "Insomnia：API 调试客户端",
    "hoppscotch": "Hoppscotch：开源 API 调试工具",
    "bruno": "Bruno：开源 API 客户端，可 Git 版本管理",
    "apipost": "ApiPost：国产 API 调试/文档工具",
    "eolink": "Eolink：API 全生命周期管理平台",
    # --- 数据库工具 ---
    "tableplus": "TablePlus：现代数据库图形管理工具",
    "heidisql": "HeidiSQL：轻量数据库管理工具",
    "sqlyog": "SQLyog：MySQL 图形管理工具",
    "another redis desktop manager": "Another Redis Desktop Manager：开源 Redis 客户端",
    "redis desktop manager": "Redis Desktop Manager：Redis 可视化管理",
    "redisinsight": "RedisInsight：Redis 官方 GUI 客户端",
    "robomongo": "Robo 3T（Robomongo）：MongoDB 图形客户端",
    "nosqlbooster": "NoSQLBooster：MongoDB 图形 IDE",
    "xftp": "Xftp：SFTP/FTP 文件传输工具（Xshell 同门）",
    "cyberduck": "Cyberduck：跨平台 FTP/S3/网盘客户端",
    "db browser for sqlite": "DB Browser for SQLite：SQLite 可视化管理",
    "azure data studio": "Azure Data Studio：微软跨平台数据库工具",
    "mariadb": "MariaDB：MySQL 的开源分支数据库",
    # --- 版本/构建/包管理 ---
    "chocolatey": "Chocolatey：Windows 包管理器（类 apt），一键装软件",
    "scoop": "Scoop：Windows 命令行包管理器，免安装绿色软件管理",
    "winget": "WinGet：微软官方包管理器，本工具安装软件的主力通道",
    "nvm-windows": "nvm-windows：Node.js 多版本切换管理",
    "nvm": "nvm：Node.js 版本管理工具",
    "fnm": "fnm：Rust 写的极速 Node 版本管理",
    "volta": "Volta：JS 工具链版本管理工具",
    "rustup": "rustup：Rust 工具链安装/版本管理",
    "pyenv": "pyenv：Python 多版本管理",
    "uv": "uv：Rust 写的极速 Python 包/项目管理工具",
    "pipx": "pipx：在隔离环境中安装 Python 命令行工具",
    "conda": "Conda：Anaconda/Miniconda 的包与环境管理",
    "anaconda": "Anaconda：Python 数据科学全家桶发行版",
    "miniconda": "Miniconda：Anaconda 的最小化发行版",
    "poetry": "Poetry：Python 依赖管理与打包工具",
    "pipenv": "Pipenv：Python 虚拟环境与依赖一体管理",
    "composer": "Composer：PHP 依赖管理工具",
    "nuget": "NuGet：.NET 包管理器",
    "vcpkg": "vcpkg：微软 C++ 库包管理器",
    "conan": "Conan：C/C++ 依赖包管理器",
    "hugo": "Hugo：Go 写的静态博客/文档生成器，构建极快",
    "hexo": "Hexo：Node.js 静态博客框架",
    "vitepress": "VitePress：Vite 驱动的静态文档站生成器",
    "vuepress": "VuePress：Vue 静态文档站生成器",
    "docusaurus": "Docusaurus：Facebook 出品的文档站框架",
    "mkdocs": "MkDocs：Python 项目文档生成器",
    "sphinx": "Sphinx：Python 文档生成器，技术文档标准工具",
    "docsify": "Docsify：动态渲染 Markdown 的文档站生成器",
    "gridea": "Gridea：国产静态博客客户端",
    "wordpress": "WordPress：全球最流行的建站程序",
    "typecho": "Typecho：国产轻量博客程序",
    "nginx": "Nginx：高性能 Web 服务器/反向代理",
    "apache http server": "Apache HTTP Server：老牌 Web 服务器",
    "tomcat": "Tomcat：Java Web 应用服务器",
    "caddy": "Caddy：自动 HTTPS 的现代 Web 服务器",
    "frp": "frp：开源内网穿透工具，把内网服务暴露到公网",
    "ngrok": "ngrok：内网穿透工具，本地服务一键公网访问",
    "cpolar": "cpolar：国产内网穿透服务",
    "花生壳": "花生壳：老牌动态域名/内网穿透服务",
    "zerotier": "ZeroTier：虚拟局域网组建工具，异地设备组同一内网",
    "tailscale": "Tailscale：基于 WireGuard 的零配置组网工具",
    "wireguard": "WireGuard：现代高速 VPN 协议",
    "openvpn": "OpenVPN：经典开源 VPN",
    "gost": "GOST：国产多功能代理/隧道工具",
    "sing-box": "sing-box：通用代理内核",
    "xray": "Xray：代理内核（v2ray 社区后继）",
    "v2ray": "V2Ray：代理工具",
    "shadowsocks": "Shadowsocks：经典代理协议及客户端",
    "clash verge": "Clash Verge：Clash 图形客户端",
    "mihomo": "Mihomo（Clash.Meta）：Clash 增强内核",
    "flclash": "FlClash：多平台 Clash 客户端",
    # --- 文件同步/网盘/传输 ---
    "阿里云盘": "阿里云盘：阿里巴巴云存储客户端，下载不限速",
    "腾讯微云": "微云：腾讯出品云存储",
    "天翼云盘": "天翼云盘：中国电信云存储",
    "和彩云": "和彩云：中国移动云存储",
    "坚果云": "坚果云：国内同步体验最好的网盘，支持 WebDAV",
    "onedrive": "OneDrive：微软云存储，与 Windows 深度集成",
    "localsend": "LocalSend：开源局域网跨平台文件互传，无需联网",
    "airdroid": "AirDroid：手机与电脑无线连接/传文件/投屏",
    "scrcpy": "scrcpy：开源 Android 手机投屏/控制工具",
    "nextcloud": "NextCloud：开源自建云存储/协作平台",
    "syncthing": "Syncthing：开源 P2P 持续文件同步工具",
    "resilio sync": "Resilio Sync：基于 BT 协议的 P2P 同步工具",
    "freefilesync": "FreeFileSync：开源文件夹对比同步工具",
    "beyond compare": "Beyond Compare：专业文件/文件夹对比合并工具",
    "winmerge": "WinMerge：开源文件对比合并工具",
    "rclone": "Rclone：命令行网盘同步工具，支持几十种云存储",
    "mountain duck": "Mountain Duck：把网盘/ FTP/S3 挂载为本地磁盘",
    "raidrive": "RaiDrive：把 Google Drive/OneDrive 等挂载为磁盘",
    # --- 下载 ---
    "aria2": "aria2：轻量多线程命令行下载引擎，直链/BT 都能下",
    "xdown": "xdown：IDM 风格的免费多线程下载器",
    "比特彗星": "BitComet：老牌 BT 下载工具",
    # --- 播放器/音乐 ---
    "foobar2000": "Foobar2000：轻量本地音乐播放器，音质党常用",
    "musicbee": "MusicBee：本地音乐管理与播放器",
    "aimp": "AIMP：轻量音乐播放器",
    "mpc-hc": "MPC-HC：经典轻量开源播放器",
    "mpc-be": "MPC-BE：MPC-HC 的增强分支播放器",
    "spotify": "Spotify：海外主流流媒体音乐平台",
    "apple music": "Apple Music：苹果流媒体音乐",
    # --- 输入法/OCR/截图 ---
    "天若ocr": "天若 OCR：截图文字识别工具",
    "pandaocr": "PandaOCR：开源截图 OCR/翻译工具",
    "verycapture": "VeryCapture：截图/录屏/GIF/贴图一体化",
    "pixpin": "PixPin：截图/贴图/长截图工具",
    "ishot": "iShot：Mac 风格截图工具（Windows 移植）",
    # --- 桌面增强 ---
    "eartrumpet": "EarTrumpet：音量精细控制（按应用调音量）",
    "modernflyouts": "ModernFlyouts：现代化音量/亮度弹窗",
    "twinkle tray": "Twinkle Tray：显示器亮度调节工具",
    "translucenttb": "TranslucentTB：任务栏透明化工具",
    "f.lux": "f.lux：护眼软件，随时间自动调节屏幕色温",
    "wallpaper engine": "Wallpaper Engine：动态壁纸软件（Steam 付费）",
    "lively wallpaper": "Lively Wallpaper：开源动态壁纸/视频壁纸",
    "rainmeter": "Rainmeter：桌面美化/信息展示框架",
    "taskbarx": "TaskbarX：任务栏图标居中工具",
    "startallback": "StartAllBack：恢复经典开始菜单/任务栏",
    "explorerpatcher": "ExplorerPatcher：恢复旧版 Windows 任务栏行为",
    "anytxt": "AnyTxt：全文搜索引擎，秒搜文档里的文字内容",
    # --- 设计/图像 ---
    "adobe photoshop": "Photoshop：Adobe 图像处理软件，设计/修图标配",
    "adobe lightroom": "Lightroom：Adobe 照片管理/调色软件",
    "adobe illustrator": "Illustrator：Adobe 矢量图形软件",
    "adobe indesign": "InDesign：Adobe 专业排版软件",
    "coreldraw": "CorelDRAW：老牌矢量绘图软件",
    "clip studio paint": "Clip Studio Paint：漫画/插画绘制软件",
    "paint tool sai": "SAI：轻量绘画软件，线条手感好",
    "krita": "Krita：开源数字绘画软件",
    "firealpaca": "FireAlpaca：轻量绘画软件",
    "美图秀秀": "美图秀秀：傻瓜式修图/美颜工具",
    "2345看图王": "2345 看图王：图片浏览工具",
    "honeyview": "Honeyview：轻量极速看图工具",
    "imageglass": "ImageGlass：开源现代看图工具",
    "qview": "qView：极简开源看图工具",
    "bandizip" : "Bandizip：轻量压缩软件，界面友好",
    "xnview": "XnView：老牌看图/批量转换工具",
    "acdsee": "ACDSee：老牌图片管理软件",
    "topaz": "Topaz 系列：AI 图片放大/降噪工具",
    "upscayl": "Upscayl：开源 AI 图片放大工具",
    # --- 专业/工程/学术 ---
    "autocad": "AutoCAD：二维/三维 CAD 制图软件",
    "solidworks": "SolidWorks：机械设计三维 CAD",
    "creo": "Creo（ProE）：三维 CAD/CAM 软件",
    "catia": "CATIA：汽车/航空工业 CAD 软件",
    "autodesk inventor": "Inventor：Autodesk 三维机械设计",
    "fusion 360": "Fusion 360：Autodesk 云 CAD/CAM 软件",
    "freecad": "FreeCAD：开源参数化三维 CAD",
    "openscad": "OpenSCAD：用代码建模的三维工具",
    "rhino": "Rhino（犀牛）：NURBS 工业设计建模软件",
    "zbrush": "ZBrush：数字雕刻/高模制作软件",
    "cura": "Cura：3D 打印切片软件（Ultimaker 出品）",
    "bambu studio": "Bambu Studio：拓竹 3D 打印机切片软件",
    "prusaslicer": "PrusaSlicer：3D 打印切片软件",
    "arduino ide": "Arduino IDE：单片机开发环境，创客入门必备",
    "platformio": "PlatformIO：VS Code 嵌入式开发平台",
    "keil": "Keil：ARM/51 单片机开发 IDE",
    "stm32cubeide": "STM32CubeIDE：ST 官方单片机开发环境",
    "proteus": "Proteus：电路设计与仿真软件",
    "multisim": "Multisim：NI 电路仿真软件",
    "altium designer": "Altium Designer：专业 PCB 设计软件",
    "kicad": "KiCad：开源 PCB 设计软件",
    "立创eda": "立创EDA：国产在线 PCB 设计工具",
    "unity": "Unity：跨平台游戏引擎，手游/独立游戏主流",
    "unreal engine": "Unreal Engine：虚幻游戏引擎，3A/高画质首选",
    "godot": "Godot：开源轻量游戏引擎",
    "cocos creator": "Cocos Creator：国产游戏引擎，小游戏/H5 常用",
    "rpg maker": "RPG Maker：零代码 RPG 游戏制作工具",
    "ren'py": "Ren'Py：视觉小说/恋爱冒险游戏引擎",
    "matlab": "MATLAB：数学计算/仿真/算法开发环境",
    "mathematica": "Mathematica：符号计算与数学软件",
    "spss": "SPSS：统计分析软件，社科/医学常用",
    "stata": "Stata：计量经济学统计软件",
    "eviews": "EViews：计量经济分析软件",
    "origin": "Origin：科研绘图与数据分析软件",
    "graphpad prism": "GraphPad Prism：科研统计绘图软件",
    "chemdraw": "ChemDraw：化学结构式绘制软件",
    "zotero": "Zotero：开源文献管理工具，写论文必备",
    "endnote": "EndNote：文献管理软件（科睿唯安）",
    "mendeley": "Mendeley：免费文献管理工具",
    "readpaper": "ReadPaper：知网文献阅读管理工具",
    "r": "R 语言：统计分析编程语言环境",
    "rstudio": "RStudio：R 语言集成开发环境",
    "julia": "Julia：高性能科学计算语言",
    "scilab": "Scilab：开源数学计算软件（MATLAB 替代）",
}

# ---------------- 正则模式 ----------------
# 匹配 norm_core 后的名称；模板可用 {g1} {g2} 引用分组
_PATTERNS = [
    (r"visual c\+\+.*redistributable", "微软 VC++ 运行库：大量软件运行必备组件，重装系统后需要补装"),
    (r"^visual c\+\+ (\d{4})", "微软 VC++ {g1} 运行库：较老的软件运行必备组件"),
    (r"python (\d+)\.(\d+)", "Python {g1}.{g2} 解释器与标准库：编程/脚本/自动化常用环境"),
    (r"python", "Python 解释器与标准库：编程/脚本/自动化常用环境"),
    (r"openjdk|temurin|jdk|jre", "Java 开发工具包（JDK）：运行和开发 Java 程序"),
    (r"node\.?js", "Node.js：JavaScript 运行时，前端工程化与后端服务的基础环境"),
    (r"\bgo\b|golang", "Go 语言工具链：编译运行 Go 程序"),
    (r"rust|cargo", "Rust 语言工具链（rustup/cargo）"),
    (r"\.net|dotnet", ".NET 运行时/SDK：运行和开发 .NET 程序"),
    (r"git", "Git 分布式版本控制系统，管理代码历史与协作"),
    (r"docker", "Docker 容器运行环境"),
    (r"mysql", "MySQL 数据库"),
    (r"postgresql|postgres", "PostgreSQL 数据库"),
    (r"nvidia|geforce", "NVIDIA 显卡驱动：游戏/渲染/AI 计算依赖"),
    (r"amd radeon", "AMD 显卡驱动"),
    (r"intel.*graphics", "Intel 核显驱动"),
    (r"realtek", "Realtek 硬件驱动（声卡/网卡类）"),
    (r"winrar", "WinRAR 压缩/解压缩工具，支持 RAR/ZIP 格式"),
    (r"potplayer", "PotPlayer 视频播放器，本地视频播放格式支持最全"),
    (r"vlc", "VLC 媒体播放器：开源播放器，支持几乎所有音视频格式"),
    (r"\bqq\b", "QQ：腾讯即时通讯软件"),
    (r"wechat|微信", "微信 PC 客户端：腾讯微信的电脑版"),
    (r"trae", "Trae：字节跳动出品的 AI 编程编辑器"),
    (r"virtualtablet", "VirtualTablet：把手机/平板变成电脑数位板的服务端"),
    (r"smart gesture", "触控板手势驱动程序（笔记本厂商预装）"),
    (r"\batk\b", "ATK Package：华硕热键驱动包，Fn 组合键依赖它"),
    (r"office|word|excel|powerpoint", "微软 Office 办公套件：Word/Excel/PowerPoint 等"),
    (r"visio", "Microsoft Visio：图表/流程图绘制软件"),
    (r"steam", "Steam：全球最大 PC 游戏平台"),
    (r"epic", "Epic 游戏商城"),
    (r"chrome", "Google Chrome 谷歌浏览器"),
    (r"firefox", "Firefox 火狐浏览器"),
    (r"\bedge\b", "Microsoft Edge 浏览器（Windows 自带）"),
    (r"node", "Node.js 相关工具"),
    (r"npm|yarn|pnpm", "JavaScript 包管理器：安装管理 npm 全局包"),
    (r"typescript", "TypeScript：带类型的 JavaScript 超集，前端主流语言"),
    (r"eslint", "ESLint：JS/TS 代码检查工具"),
    (r"prettier", "Prettier：代码格式化工具"),
    (r"webpack", "webpack：前端模块打包工具"),
    (r"vite", "Vite：现代前端构建工具，开发服务器极快"),
    (r"esbuild", "esbuild：极速 JS 打包/压缩工具"),
    (r"babel", "Babel：JavaScript 编译器（语法降级）"),
    (r"requests", "requests：Python 最流行的 HTTP 请求库"),
    (r"\bnumpy\b", "NumPy：Python 科学计算基础库"),
    (r"\bpandas\b", "pandas：Python 数据分析库（表格处理）"),
    (r"django", "Django：Python 全功能 Web 框架"),
    (r"flask", "Flask：Python 轻量 Web 框架"),
    (r"fastapi", "FastAPI：Python 高性能 API 框架"),
    (r"torch|pytorch", "PyTorch：深度学习框架"),
    (r"tensorflow", "TensorFlow：深度学习框架"),
    (r"jupyter", "Jupyter：交互式笔记本，数据分析/教学常用"),
    (r"matplotlib", "Matplotlib：Python 绘图库"),
    (r"scrapy", "Scrapy：Python 爬虫框架"),
    (r"selenium", "Selenium：浏览器自动化测试/爬虫工具"),
    (r"pip", "pip：Python 包管理器，安装管理 Python 包"),
    (r"poetry", "Poetry：Python 依赖管理与打包工具"),
    (r"black|flake8|mypy|isort", "Python 代码质量工具（格式化/检查）"),
    (r"virtualenv|venv", "Python 虚拟环境工具"),
    (r"pytest", "pytest：Python 测试框架"),
]

# ---------------- 类别兜底 ----------------
# 只在完全没匹配上时使用，保证每个条目都有解释
_CATEGORY_FALLBACK = {
    CAT_LIB: "系统运行库/驱动组件：供其他程序调用的底层组件，重装系统后不会自动回来，建议保留勾选",
    CAT_WSL: "WSL Linux 子系统发行版：在 Windows 内运行的 Linux 环境",
    CAT_STORE: "微软商店应用：新系统可从商店重新获取；如不认识可取消勾选，不影响其他软件恢复",
    CAT_CONFIG: "个人配置文件备份：恢复后会覆盖新系统的对应配置文件",
    CAT_NPM: "npm 全局命令行工具：通过 npm -g 安装，需先装好 Node.js",
    CAT_PIP: "Python 第三方包：通过 pip 安装，需先装好 Python",
}


def _target_keys(target: str) -> list:
    """从安装标识提取候选键：整体小写、末段、包名（npm/pip 的 name@ver）。"""
    t = (target or "").strip().lower()
    if not t:
        return []
    keys = [t]
    if "." in t:
        keys.append(t.rsplit(".", 1)[-1])
    if "@" in t:  # npm/pip: name@1.2.3 → name
        keys.append(t.split("@")[-1] if t.startswith("@") else t.split("@")[0])
        if t.startswith("@"):
            keys.append("@" + t.split("@")[1].split("@")[0])
    return [k for k in keys if k]


def _lookup(core: str):
    """在 DB 与模式表中查找说明；core 为已归一化的字符串。"""
    if not core:
        return ""
    if core in _DB:
        return _DB[core]
    # 子串匹配：按键最长优先（anaconda 先于 conda）；中文键允许 2 字，ASCII 键至少 3 字防误伤
    def key_ok(k: str) -> bool:
        has_cjk = any("一" <= c <= "鿿" for c in k)
        return len(k) >= (2 if has_cjk else 3)
    for key, desc in sorted(_DB.items(), key=lambda kv: -len(kv[0])):
        if key_ok(key) and (key in core or core in key):
            return desc
    for pat, tpl in _PATTERNS:
        m = re.search(pat, core)
        if m:
            try:
                return tpl.format(g1=m.group(1), g2=m.group(2) if m.lastIndex >= 2 else "")
            except (IndexError, KeyError):
                return tpl
    return ""


def describe(name: str = "", target: str = "", category: str = "", method: str = "") -> str:
    """为一个条目生成用途说明；识别不出时按类别给兜底说明，再不行返回空串。"""
    ncore = norm_core(name)
    # npm/pip 条目名形如 "npm: pkg"，先剥掉前缀
    if ncore.startswith(("npm: ", "pip: ")):
        ncore = ncore.split(": ", 1)[1]
    desc = _lookup(ncore)
    if desc:
        return desc
    for tk in _target_keys(target):
        desc = _lookup(norm_core(tk))
        if desc:
            return desc
    return _CATEGORY_FALLBACK.get(category, "")


def fill_descriptions(items) -> int:
    """给条目列表填充 desc（只填空的，不覆盖用户已写的）。返回填充条数。"""
    n = 0
    for it in items:
        if not getattr(it, "desc", ""):
            d = describe(it.name, it.target, it.category, it.method)
            if d:
                it.desc = d
                n += 1
    return n
