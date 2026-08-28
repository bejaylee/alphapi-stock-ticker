# AlphaPi One S · 股票实时看板（Stock Ticker）

把一块 **AlphaPi One S**（ESP32-S2 少儿编程开发板）改造成 **A股 / 国际指数实时看板**：
彩屏显示实时行情、红涨绿跌、右上角北京时间（精确到秒）、按键切换页面、增量刷新不闪屏。

```
┌────────────────────────┐
│ 上证指数        14:08:15 │
│ 3958.09                │
│ +0.04%                 │
│                        │
│ 创业板指                │
│ 3439.61                │
│ -0.97%                 │
└────────────────────────┘
```

> 主控 ESP32-S2 · MicroPython 1.19.1 · 128×160 ST7789 彩屏 · 免费行情接口（无需 Key）

---

## ✨ 特性

- **4 个页面，按键切换**：A / B / C / D 四个物理按键，一键切换指数组合
- **8 个指数**：上证指数、创业板指、深证成指、科创50、韩国KOSPI、日经225、标普500、纳斯达克100
- **红涨绿跌**：遵循 A 股配色习惯（涨红跌绿）
- **北京时间**：右上角实时显示，精确到秒（NTP 自动校时）
- **增量刷新**：只重画发生变化的数值，名称和不变的数据纹丝不动，大幅减少闪屏
- **2 秒自动刷新**：实测最快约 1–2 秒/次，默认落地 2 秒（兼顾防限流）
- **看门狗兜底**：内置看门狗，异常时自动重启恢复

## 🔌 硬件要求

- **AlphaPi One S v1.7**（ESP32-S2 主控，原生 USB）
- 板载：128×160 ST7789 彩屏、A/B/C/D 四个按键
- 一台电脑（用于刷机），一根 USB 数据线
- 一个 2.4GHz WiFi（设备联网抓行情用）

## 🚀 快速开始

**1. 填入你的 WiFi**

打开 `stock_ticker.py`，修改顶部两行：

```python
WIFI_SSID = "YOUR_WIFI_SSID"      # 改成你的 WiFi 名称
WIFI_PASS = "YOUR_WIFI_PASSWORD"  # 改成你的 WiFi 密码
```

**2. 刷入设备**

安装 [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html)（`pip install mpremote`），然后：

```bash
mpremote connect COM3 fs cp stock_ticker.py :main.py
mpremote connect COM3 reset
```

> 串口号（`COM3`）按你的实际情况修改（Windows 在设备管理器里查看，Mac/Linux 一般是 `/dev/ttyUSB*` 或 `/dev/ttyACM*`）。

**3. 完成**

设备重启后自动连 WiFi、抓行情并显示。

## 🎮 页面与按键

| 按键 | 显示内容 |
|------|----------|
| **A** | 上证指数 · 创业板指 |
| **B** | 深证成指 · 科创50 |
| **C** | 韩国综合（KOSPI） · 日经225 |
| **D** | 标普500 · 纳斯达克100 |

每页显示两个指数：名称（黄）+ 现价 + 涨跌幅（红涨绿跌），右上角为北京时间。

## 📊 数据源

- **东方财富** 免费行情接口：上证、创业板、深证成指、科创50、韩国KOSPI、日经225、标普500（一次批量抓取）
- **腾讯行情** 接口：纳斯达克100
- 均为公开免费接口，**无需注册、无需 API Key**

## 🛠️ 工作原理

```
WiFi 连接 → NTP 校时 → 定时抓取行情(HTTP) → 解析JSON → ST7789 彩屏增量绘制
     ↑                                                        │
     └──────────────  A/B/C/D 按键切换页面  ←─────────────────┘
```

- 用原生 `socket` 发起 HTTP 请求抓取行情，解析 JSON
- 用板载教学库 `printChange231213` 的 `printXy()` 在彩屏上定位绘制
- 维护一份「上次显示内容」缓存，只重画变化的字符位置（增量刷新）

## ⚙️ 自定义

- **换指数**：修改 `PAGES` 列表。格式 `(显示名称, 数据源, 代码)`：
  - 东方财富：数据源填 `"em"`，代码填 secid（沪市 `1.xxxxxx`、深市 `0.xxxxxx`、国际 `100.XXX`）
  - 腾讯：数据源填 `"tx"`，代码填腾讯行情代码（如 `usNDX`）
  - 注意要把新指数的 secid 也加进 `EM_SECIDS` 批量请求里
- **改刷新速度**：修改 `REFRESH_SEC`（秒）。实测 1–2 秒可稳定运行，建议 ≥2 秒以防限流
- **改配色**：修改 `C_UP` / `C_DOWN` / `C_NAME`（支持 `'red'` `'green'` `'yellow'` `'white'` 等）

## ⚠️ 注意事项

- 本程序依赖 AlphaPi One S **板载的 `hal` 和 `printChange231213` 教学库**（随固件自带），**不适用于其他 MicroPython 开发板**。
- 刷机会**覆盖**设备里的 `main.py`。如需保留原程序，刷机前先备份：

  ```bash
  mpremote connect COM3 fs cp :main.py main_backup.py
  ```

- 设备需连接 2.4GHz WiFi（ESP32-S2 不支持 5GHz）。

## 📄 License

MIT License — 随便用，欢迎 Star ⭐ 和 Fork。
