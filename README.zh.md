<!-- 言語 / Language -->
[日本語](README.md) · [English](README.en.md) · **中文**

# TBH Tools — Task Bar Hero 非官方工具集

为 **Task Bar Hero** 玩家准备的免费工具。分两类：
在浏览器里直接用的 **网页工具**（无需安装），以及在游戏中即时显示价格的 Windows 程序 **MarketLens**。

---

## 🌐 网页工具 ＝ 免安装，点链接即用

### ▶ 主页： **https://ghostsharkrobotics.github.io/tbh-tools/**

无需下载。收藏后在电脑或手机上都能用。也可直接打开下面的工具：

| 工具 | 功能 |
|---|---|
| [🏆 最强配装器](https://ghostsharkrobotics.github.io/tbh-tools/tbh-best-build.html) | 为各部位选择装备 / 宝石 / 雕刻 / 铭文，自动计算 DPS。点「最强」会穷举全职业 × 全装备，自动套用 DPS 最高的配装。 |
| [🔍 物品搜索](https://ghostsharkrobotics.github.io/tbh-tools/tbh-gem-search.html) | 按**效果或名称搜索**装备、宝石、雕刻、铭文和特殊属性。带市场价格，中/日/英，可排序。 |
| [💰 捡漏finder](https://ghostsharkrobotics.github.io/tbh-tools/tbh-deals.html) | 按「强度 ÷ 当前最低价」排序，找出低于行情的在售。直达 Steam 市场。 |
| [🧱 制作素材](https://ghostsharkrobotics.github.io/tbh-tools/tbh-crafting.html) | 按层级和部位查制作（魔方）配方与所需素材。每个素材带价格与直链。 |
| [📦 关卡掉落](https://ghostsharkrobotics.github.io/tbh-tools/tbh-stage-drops.html) | 哪个关卡的哪个宝箱掉什么，带概率。支持「在哪刷？」反向查询。 |
| [⚡ 经验效率](https://ghostsharkrobotics.github.io/tbh-tools/tbh-exp.html) | 按英雄等级、经超等级修正后的经验给关卡排序。输入通关时间可按每小时经验排序。 |
| [🛠 配装模拟器](https://ghostsharkrobotics.github.io/tbh-tools/tbh-build-simulator.html) | 由装备、宝石、增益计算 DPS。 |
| [📊 DPS计算器](https://ghostsharkrobotics.github.io/tbh-tools/tbh-dps.html) | 简单的 DPS 计算。 |
| [📖 规格备注](https://ghostsharkrobotics.github.io/tbh-tools/tbh-info.html) | 合成/制作的产出种类与概率、DLC 条件、宝箱掉率。 |

价格数据由 GitHub Actions 每日自动更新。

---

## 🖥 TBH MarketLens（Windows 程序）

游戏中，**把光标对准物品并按一个键**，就会弹出小卡片显示它的 Steam 市场价格（最低价 + 中位价）。无需打字、无需 Alt‑Tab。界面支持 日本語 / English / 中文。

### ⬇ 下载方法（面向不熟悉 GitHub 的人）

1. 打开下载页 → **[📥 Releases 页面](https://github.com/GhostSharkRobotics/tbh-marketlens/releases)**
2. 在最新版本的 **「Assets」** 中，点击以 **`TBH-MarketLens`** 开头的 **`.zip`** 文件下载
3. **右键 zip → 全部解压**
4. 双击解压文件夹里的 **`TBH MarketLens.exe`** 启动

> 💡 若 Windows 提示 **「Windows 已保护你的电脑」**，点 **更多信息 → 仍要运行**（因为程序未签名，属正常现象）。

启动后常驻在系统托盘（时钟附近）。首次运行会显示用法。可在 **托盘 → 设置** 更改触发键和界面语言（默认触发键是鼠标的「后退/侧」键）。

### 安全吗？（反作弊）

是的。MarketLens 是**完全独立的程序**，只做三件事：①截取自己/桌面画面并识别文字，②监听你设定的快捷键，③向 Steam 公开价格 API 查询价格。它**从不读写游戏内存、不注入任何东西、完全不接触游戏进程**，也没有任何加速/时间操作。详情见 [dist-README.md](dist-README.md)。

---

## 开发者备注

- 网页工具是**内嵌数据的自包含 HTML**（可离线运行），由 `tbh-data.json` 等通过构建脚本生成。
- MarketLens 源码为 `tbh-price-ocr.py`；发布版位于独立仓库 [tbh-marketlens](https://github.com/GhostSharkRobotics/tbh-marketlens) 的 Releases。
- 价格通过 GitHub Actions 每日自动更新。

---

*Task Bar Hero 的粉丝制作的非官方工具。 by **Ghost Shark Robotics** — [☕ Ko-fi](https://ko-fi.com/ghostsharkrobotics)*
