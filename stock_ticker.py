# ============================================================
#  AlphaPi One S · 股票实时看板 (Stock Ticker)
#  ----------------------------------------------------------
#  把 AlphaPi One S（ESP32-S2 少儿编程开发板）改造成 A股/国际
#  指数实时看板：4 个按键切换页面、红涨绿跌、右上角北京时间
#  （到秒）、增量刷新减少闪烁、默认 2 秒自动刷新。
#
#  使用方法：
#    1. 修改下方 WIFI_SSID / WIFI_PASS 为你自己的 WiFi
#    2. 用 mpremote 刷入设备：
#         mpremote connect COM3 fs cp stock_ticker.py :main.py
#         mpremote connect COM3 reset
#    3. 按键 A/B/C/D 切换指数页面
#
#  运行环境：AlphaPi One S v1.7（ESP32-S2，MicroPython 1.19.1，
#            依赖板载 hal / printChange231213 教学库）
#  数据源：东方财富 + 腾讯（免费行情接口，无需 Key）
# ============================================================
import time
import network, socket, machine
try:
    import ujson as json
except ImportError:
    import json
import hal
from machine import WDT
from printChange231213 import clear, printXy

# ===== 必改：填入你的 WiFi 信息 =====
WIFI_SSID = "YOUR_WIFI_SSID"      # WiFi 名称
WIFI_PASS = "YOUR_WIFI_PASSWORD"  # WiFi 密码
# ===================================

REFRESH_SEC = 2         # 数据刷新间隔（秒）

C_NAME = 'yellow'
C_UP   = 'red'
C_DOWN = 'green'
C_FLAT = 'white'
S_SIZE = '小'

PAGES = [
    [("上证指数", "em", "1.000001"), ("创业板指", "em", "0.399006")],
    [("深证成指", "em", "0.399001"), ("科创50",   "em", "1.000688")],
    [("韩国综合", "em", "100.KS11"), ("日经225",   "em", "100.N225")],
    [("标普500",  "em", "100.SPX"),  ("纳斯达克100", "tx", "usNDX")],
]
POS = [(0, 20, 40), (64, 84, 104)]

_DNS = {}

def log(msg):
    try:
        f = open("status.txt", "w")
        f.write(msg)
        f.close()
    except Exception:
        pass

def connect_wifi():
    w = network.WLAN(network.STA_IF)
    w.active(True)
    if not w.isconnected():
        w.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(30):
            if w.isconnected():
                return True
            time.sleep(0.5)
    try:
        w.config(pm=0)
    except Exception:
        pass
    return w.isconnected()

def sync_time():
    try:
        import ntptime
        ntptime.host = "ntp.aliyun.com"
        ntptime.settime()
        return True
    except Exception:
        return False

def beijing_time():
    try:
        t = time.localtime(time.time() + 8 * 3600)
        return "%02d:%02d:%02d" % (t[3], t[4], t[5])
    except Exception:
        return "--:--:--"

def dechunk(body):
    out = b""
    while body:
        nl = body.find(b"\r\n")
        if nl < 0:
            break
        line = body[:nl].strip()
        try:
            size = int(line.decode(), 16)
        except Exception:
            break
        if size == 0:
            break
        out += body[nl + 2: nl + 2 + size]
        body = body[nl + 2 + size + 2:]
    return out

def http_get(host, path):
    if host not in _DNS:
        _DNS[host] = socket.getaddrinfo(host, 80)[0][-1]
    s = socket.socket()
    try:
        s.settimeout(5)
        s.connect(_DNS[host])
        s.send(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n" % (path, host)).encode())
        buf = b""
        while True:
            c = s.recv(1024)
            if not c:
                break
            buf += c
        head, _, body = buf.partition(b"\r\n\r\n")
        if b"chunked" in head.lower():
            body = dechunk(body)
        return body
    finally:
        try:
            s.close()
        except Exception:
            pass

EM_SECIDS = "1.000001,0.399006,0.399001,1.000688,100.SPX,100.N225,100.KS11"

def fetch_all():
    data = {}
    try:
        path = "/api/qt/ulist.np/get?fltt=2&secids=" + EM_SECIDS + "&fields=f2,f3,f4,f12,f14"
        d = json.loads(http_get("push2.eastmoney.com", path).decode("utf-8"))
        for it in d["data"]["diff"]:
            data[it["f12"]] = (it["f2"], it["f4"], it["f3"])   # 用代码(f12)作key，避免名称不一致
    except Exception:
        pass
    try:
        f = http_get("qt.gtimg.cn", "/q=usNDX").split(b'~')
        data["纳斯达克100"] = (float(f[3].decode()), float(f[31].decode()), float(f[32].decode()))
    except Exception:
        pass
    return data

# 增量绘制缓存：记录每个位置上次画的内容，只重画发生变化的位置，减少闪烁
_last = {}

def _draw(key, text, x, y, color):
    if _last.get(key) == (text, color):
        return                      # 内容没变，不重画
    _last[key] = (text, color)
    printXy(text, x, y, S_SIZE, color)

def render(data, page, tstr, force_clear):
    global _last
    if force_clear:
        clear()
        _last = {}
    _draw('t', tstr, 76, 0, C_FLAT)   # 右上角时间（右移，避免与左侧名称重叠）
    for k in range(2):
        name, src, code = PAGES[page][k]
        ny, py, cy = POS[k]
        _draw((k, 'n'), name, 2, ny, C_NAME)
        if src == "tx":
            key = name                    # 腾讯接口用名称取
        else:
            key = code.split(".")[1]      # 东方财富用代码(f12)取
        rec = data.get(key)
        if rec is None:
            _draw((k, 'p'), "%-9s" % "--", 2, py, C_FLAT)
            _draw((k, 'c'), "%-8s" % "", 2, cy, C_FLAT)
            continue
        price, chg, pct = rec
        c = C_UP if pct > 0 else (C_DOWN if pct < 0 else C_FLAT)
        _draw((k, 'p'), "%-9s" % ("%.2f" % price), 2, py, c)
        sign = "+" if pct >= 0 else ""
        _draw((k, 'c'), "%-8s" % ("%s%.2f%%" % (sign, pct)), 2, cy, c)

def read_button():
    try:
        for k, pin in hal.key_map.items():
            if pin.value() == 1:
                return k
    except Exception:
        pass
    return 0

def main():
    clear()
    printXy("wifi...", 4, 60, S_SIZE, C_NAME)
    wifi_ok = connect_wifi()
    if wifi_ok:
        sync_time()
    if not wifi_ok:
        clear()
        printXy("WiFi FAIL", 4, 60, S_SIZE, C_UP)
    print("BOOT wifi_ok=%s" % wifi_ok)
    page = 0
    last_btn = 0
    btn_seen = 0
    start = time.time()
    last_log = 0
    wdt = None
    try:
        wdt = WDT(timeout=20000)
    except Exception:
        wdt = None
    # 主循环前：首次抓数据 + 清屏整页绘制
    data = fetch_all()
    last_fetch = time.time()
    render(data, page, beijing_time(), True)
    last_sec = int(time.time())
    while True:
        if wdt is not None:
            wdt.feed()
        btn = read_button()
        if btn and btn != last_btn:
            btn_seen = btn
            page = btn - 1
            render(data, page, beijing_time(), True)   # 切页：清屏整页重画
            print("KEY:%d->pg%d" % (btn, page))
        last_btn = btn
        now = time.time()
        if now - last_fetch >= REFRESH_SEC:
            _t = time.time()
            data = fetch_all()
            _cost = time.time() - _t
            last_fetch = now
            print("FETCH ok:%d t:%s cost:%.1fs" % (len(data), beijing_time(), _cost))
        # 每秒做一次增量刷新（只重画变化的数值，不变不动）
        cur_sec = int(now)
        if cur_sec != last_sec:
            render(data, page, beijing_time(), False)
            last_sec = cur_sec
        if now - last_log >= 30:
            log("run:%ds pg:%d btn:%d ok:%d" % (int(now - start), page, btn_seen, len(data)))
            print("RUN %ds pg%d btn%d ok%d" % (int(now - start), page, btn_seen, len(data)))
            last_log = now
        time.sleep(0.1)

main()
