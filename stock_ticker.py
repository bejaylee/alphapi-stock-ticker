# ============================================================
#  AlphaPi One S · 股票实时看板 (Stock Ticker)
#  ----------------------------------------------------------
#  ESP32-S2 + MicroPython 实时指数看板。
#  特性：按市场识别盘中/休盘，休盘不抓数据；美股显示盘前/盘中/
#  盘后标记；红涨绿跌；北京时间到秒；增量刷新减少闪烁。
#
#  使用方法：
#    1. 修改下方 WIFI_SSID / WIFI_PASS 为你自己的 WiFi
#    2. mpremote connect COM3 fs cp stock_ticker.py :main.py
#       mpremote connect COM3 reset
#    3. 按键 A/B/C/D 切换页面
#
#  运行环境：AlphaPi One S v1.7（ESP32-S2，MicroPython 1.19.1）
#  数据源：腾讯免费行情接口（无需 Key，返回含交易状态码）
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
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASS = "YOUR_WIFI_PASSWORD"
# ===================================

REFRESH_SEC = 2         # 盘中数据刷新间隔（秒）
IDLE_REFRESH_SEC = 30   # 休盘时的探测间隔（秒，只查状态不抓全量）

C_NAME  = 'yellow'
C_UP    = 'red'
C_DOWN  = 'green'
C_FLAT  = 'white'
C_IDLE  = 'white'
S_SIZE  = '小'

# 页面定义：(显示名, 代码, 市场类型)
# 市场类型：CN=A股, KR=韩国, JP=日本, US=美股
# A股/美股用腾讯代码(sh/sz/us前缀)，韩国/日本用东财代码(KS11/N225)
PAGES = [
    [("上证指数", "sh000001", "CN"), ("创业板指", "sz399006", "CN")],
    [("深证成指", "sz399001", "CN"), ("科创50",   "sh000688", "CN")],
    [("韩国综合", "KS11",     "KR"), ("日经225",   "N225",     "JP")],
    [("费城半导体", "usSOXX", "US"), ("纳斯达克",  "usIXIC",   "US")],
]
POS = [(0, 24, 44), (64, 88, 108)]

# 各市场交易时段（北京时间）
# A股: 9:30-11:30, 13:00-15:00
# 韩国: 9:00-15:30
# 日本: 9:00-11:30, 12:30-15:00
# 美股: 盘前4:00-9:30, 盘中9:30-16:00, 盘后16:00-20:00 (均为美东时间)
#       换算北京时间(夏令时+12h, 冬令时+13h): 盘前21:00-4:30/22:00-5:30, 盘中4:30-4:00, 盘后4:00-8:00
#       简化: 美股交易相关时段 北京时间 21:00(夏)/22:00(冬) ~ 次日 04:00(夏)/05:00(冬)
MARKET_HOURS = {
    "CN": [(9, 30, 11, 30), (13, 0, 15, 0)],
    "KR": [(9, 0, 15, 30)],
    "JP": [(9, 0, 11, 30), (12, 30, 15, 0)],
    # 美股用宽范围覆盖盘前到盘后: 21:00~次日04:00 (夏令时近似)
    "US_PRE":  (21, 0),    # 盘前开始（北京时间，夏令时近似）
    "US_OPEN": (4, 30),     # 美东9:30开盘 → 北京夏令时4:30 / 冬令时5:30，用5:30保守
    "US_AFTER": (4, 0),     # 美东16:00盘后 → 北京夏令时4:00 / 冬令时5:00
    "US_END":  (4, 0),      # 美东20:00盘后结束 → 北京夏令时8:00 / 冬令时9:00
}

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

def bj_hour_min():
    """返回北京时间的 (hour, minute, weekday) weekday:1=周一...7=周日"""
    t = time.localtime(time.time() + 8 * 3600)
    return t[3], t[4], t[6]

def market_status(mkt):
    """返回市场状态: 'pre'(盘前) / 'open'(盘中) / 'after'(盘后) / 'closed'(休盘)"""
    h, m, wd = bj_hour_min()
    cur_min = h * 60 + m
    is_weekend = wd in (0, 6)  # MicroPython weekday: 0=周一...6=周日

    if mkt == "CN":
        if is_weekend:
            return "closed"
        for sh, sm, eh, em in MARKET_HOURS["CN"]:
            if sh * 60 + sm <= cur_min <= eh * 60 + em:
                return "open"
        return "closed"

    if mkt == "KR":
        if is_weekend:
            return "closed"
        sh, sm, eh, em = MARKET_HOURS["KR"][0]
        if sh * 60 + sm <= cur_min <= eh * 60 + em:
            return "open"
        return "closed"

    if mkt == "JP":
        if is_weekend:
            return "closed"
        for sh, sm, eh, em in MARKET_HOURS["JP"]:
            if sh * 60 + sm <= cur_min <= eh * 60 + em:
                return "open"
        return "closed"

    if mkt == "US":
        # 美股：周末休盘；工作日 21:00(盘前) ~ 次日04:00(盘后结束)
        # 跨日处理：21:00~24:00 和 00:00~04:00
        if is_weekend:
            return "closed"
        # 盘前: 21:00 ~ 04:30(次日)
        # 盘中: 04:30 ~ 04:00(次日) -- 简化：4:30~16:00美东 → 用状态码精确判断
        # 盘后: 16:00~20:00美东 → 04:00~08:00北京(夏)
        # 宽范围：21:00~次日08:00 算交易相关时段
        if cur_min >= 21 * 60 or cur_min <= 8 * 60:
            # 在交易相关时段内，用接口状态码细分
            return "trading_range"
        return "closed"

    return "closed"

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

def parse_tx(raw):
    """解析腾讯行情返回，返回 (status_code, price, chg, pct) 或 None"""
    try:
        body = raw.split(b'="')[1].rsplit(b'"', 1)[0]
        f = body.split(b'~')
        status = int(f[0].decode())
        price = float(f[3].decode())
        chg = float(f[31].decode())
        pct = float(f[32].decode())
        return status, price, chg, pct
    except Exception:
        return None

# 批量请求多个腾讯代码（一次HTTP请求，用逗号分隔）
def fetch_tx_batch(codes):
    """codes: list of str like ['sh000001','usIXIC']。返回 {code: (status,price,chg,pct,name)}"""
    result = {}
    qstr = ",".join(codes)
    try:
        raw = http_get("qt.gtimg.cn", "/q=" + qstr)
        parts = raw.split(b';')
        for part in parts:
            part = part.strip()
            if not part or b'="' not in part:
                continue
            try:
                varname, rest = part.split(b'="', 1)
                val = rest.rsplit(b'"', 1)[0]
                if not val:
                    continue
                f = val.split(b'~')
                if len(f) < 33:
                    continue
                # varname 形如 b'v_sh000001'，取 _ 后面的代码
                code = varname.split(b'_', 1)[1].decode()
                status = int(f[0].decode())
                price = float(f[3].decode())
                chg = float(f[31].decode())
                pct = float(f[32].decode())
                result[code] = (status, price, chg, pct, "")   # 名称用PAGES里的，不解析gbk
            except Exception:
                continue
    except Exception:
        pass
    return result

# A股+美股用腾讯（带状态码），韩国+日本用东方财富延迟站
TX_CODES = ["sh000001", "sz399006", "sz399001", "sh000688", "usSOXX", "usIXIC"]
EM_SECIDS = "100.KS11,100.N225"   # 韩国+日本

def fetch_all():
    """返回 (data_dict, any_trading)
    data = {腾讯代码: (status,price,chg,pct,name)} ∪ {东财代码: (price,chg,pct)}"""
    data = {}
    any_trading = False
    # 1) 腾讯批量（A股+美股）
    tx = fetch_tx_batch(TX_CODES)
    for code, (st, p, c, pct, n) in tx.items():
        data[code] = (st, p, c, pct, n)
        if st != 1:
            any_trading = True
    # 2) 东方财富延迟站（韩国+日本），无状态码，用本地时段判断
    try:
        path = "/api/qt/ulist.np/get?fltt=2&secids=" + EM_SECIDS + "&fields=f2,f3,f4,f12"
        d = json.loads(http_get("push2delay.eastmoney.com", path).decode("utf-8"))
        for it in d["data"]["diff"]:
            code = it["f12"]     # KS11 / N225
            price = it["f2"]
            chg = it["f4"]
            pct = it["f3"]
            data[code] = (200, price, chg, pct, "")   # 状态码用200占位，实际交易状态靠本地时段判断
    except Exception:
        pass
    return data, any_trading

_last = {}

def _draw(key, text, x, y, color):
    if _last.get(key) == (text, color):
        return
    _last[key] = (text, color)
    printXy(text, x, y, S_SIZE, color)

def us_session_label(status):
    """根据腾讯状态码返回美股时段标记"""
    # 腾讯状态码：1=休盘, 200=正常交易, 其他=盘前/盘后等
    if status == 1:
        return "休盘"
    elif status == 200:
        return "盘中"
    else:
        return "盘前/后"

def render(data, page, tstr, force_clear):
    global _last
    if force_clear:
        clear()
        _last = {}
    _draw('t', tstr, 76, 0, C_FLAT)
    for k in range(2):
        name, code, mkt = PAGES[page][k]
        ny, py, cy = POS[k]
        _draw((k, 'n'), name, 2, ny, C_NAME)
        rec = data.get(code)
        if rec is None:
            _draw((k, 'p'), "%-9s" % "--", 2, py, C_FLAT)
            _draw((k, 'c'), "%-8s" % "", 2, cy, C_FLAT)
            continue
        status, price, chg, pct, rname = rec
        if mkt == "US":
            # 美股：显示时段标记 + 价格 + 涨跌幅
            label = us_session_label(status)
            _draw((k, 's'), "%-5s" % label, 2, py, C_FLAT)
            c = C_UP if pct > 0 else (C_DOWN if pct < 0 else C_FLAT)
            _draw((k, 'p'), "%-9s" % ("%.2f" % price), 2, cy, c)
        elif mkt in ("KR", "JP"):
            # 韩国/日本：东财无状态码，用本地时段判断
            ms = market_status(mkt)
            if ms == "closed":
                _draw((k, 'p'), "%-9s" % "休盘", 2, py, C_IDLE)
                _draw((k, 'c'), "%-8s" % "", 2, cy, C_FLAT)
            else:
                c = C_UP if pct > 0 else (C_DOWN if pct < 0 else C_FLAT)
                _draw((k, 'p'), "%-9s" % ("%.2f" % price), 2, py, c)
                sign = "+" if pct >= 0 else ""
                _draw((k, 'c'), "%-8s" % ("%s%.2f%%" % (sign, pct)), 2, cy, c)
        else:
            # A股：用腾讯状态码
            if status == 1:
                _draw((k, 'p'), "%-9s" % "休盘", 2, py, C_IDLE)
                _draw((k, 'c'), "%-8s" % "", 2, cy, C_FLAT)
            else:
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
    # 首次抓取
    data, trading = fetch_all()
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
            render(data, page, beijing_time(), True)
            print("KEY:%d->pg%d" % (btn, page))
        last_btn = btn
        now = time.time()
        # 根据是否有交易决定刷新频率：有交易2秒，全休盘30秒
        interval = REFRESH_SEC if trading else IDLE_REFRESH_SEC
        if now - last_fetch >= interval:
            _t = time.time()
            data, trading = fetch_all()
            _cost = time.time() - _t
            last_fetch = now
            print("FETCH ok:%d trade:%s t:%s cost:%.1fs" % (len(data), trading, beijing_time(), _cost))
        cur_sec = int(now)
        if cur_sec != last_sec:
            render(data, page, beijing_time(), False)
            last_sec = cur_sec
        if now - last_log >= 30:
            log("run:%ds pg:%d btn:%d ok:%d trade:%s" % (int(now - start), page, btn_seen, len(data), trading))
            print("RUN %ds pg%d btn%d ok%d trade%s" % (int(now - start), page, btn_seen, len(data), trading))
            last_log = now
        time.sleep(0.1)

main()
