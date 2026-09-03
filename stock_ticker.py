# ============================================================
#  AlphaPi One S · 股票实时看板 (Stock Ticker)
#  ----------------------------------------------------------
#  ESP32-S2 + MicroPython 实时指数看板。
#  特性：按市场识别盘中/休盘，休盘不抓数据；美股显示盘前/盘中/
#  盘后标记；红涨绿跌；北京时间到秒；增量刷新减少闪烁。
#  美股采用 ETF(SOXQ/QQQ) 而非指数，以支持盘前/盘后实时行情。
#
#  使用方法：
#    1. 修改下方 WIFI_SSID / WIFI_PASS 为你自己的 WiFi
#    2. mpremote connect COM3 fs cp stock_ticker.py :main.py
#       mpremote connect COM3 reset
#    3. 按键 A/B/C/D 切换页面
#
#  运行环境：AlphaPi One S v1.7（ESP32-S2，MicroPython 1.19.1）
#  数据源：新浪主力（单请求 8 指数）+ 腾讯/东财备用，自动降级
# ============================================================
import time
import network, socket, machine
try:
    import ujson as json
except ImportError:
    import json
import hal
from machine import WDT
from printChange231213 import clear, printXy, tft
import zlib
import gc

# ===== 必改：填入你的 WiFi 信息 =====
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASS = "YOUR_WIFI_PASSWORD"
# ===================================

REFRESH_SEC = 2         # 盘中数据刷新间隔（秒）
IDLE_REFRESH_SEC = 30   # 休盘时的探测间隔（秒，只查状态不抓全量）

# ===== ESP32-S2 DMA 稳定性调节 =====
# WiFi(lwIP) 与 ST7789 SPI 渲染共用 DMA 资源，在 S2 上会间歇性争抢导致冻结。
# 纯软件无法根除，以下参数用于降低争抢频率、减少堆碎片、加速看门狗恢复。
DMA_SETTLE_MS = 30      # fetch 与 render 之间留给 WiFi 后台 DMA 排空的毫秒数
WDT_TIMEOUT_MS = 15000  # 看门狗超时（毫秒），冻结时自动重启

# 按键去抖：实测 B 键触点抖动在 100ms 内跳变 3 次，read_button 瞬时采样会
# 把抖动放大成"切页迟钝/闪烁"。改为连续采样确认稳定后才判定按下。
DEBOUNCE_COUNT = 5      # 连续 N 次采样都为高才算稳定按下
DEBOUNCE_STEP_MS = 10   # 每次采样间隔（毫秒），总去抖窗口 = 5×10 = 50ms

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
    [("SOXQ",     "usSOXQ",   "US"), ("QQQ",       "usQQQ",    "US")],
]
# 纵向整体下移 4px 以实现上下居中：
#   内容纵向占 y=0~119 共 120px，屏高 128 → 原本上余 0 / 下余 8，偏移后上下各余 4px。
#   块内行间距(4px)与两块之间间距(8px)保持不变。
POS = [(4, 24, 44), (68, 88, 108)]
# 右上角三行 y 坐标：小字号行高 16px，随内容一起下移 4px
# 日期 4~19 / 时间 20~35 / 时段标签 36~51，逐行下移避免重叠
Y_DATE = 4
Y_TIME = 20
Y_SESSION = 36
# 右上角固定列 x（日期 "2026/08/29" = 10 ASCII × 8px = 80px，76+80=156 < 160 不越界）
X_RIGHT = 76

# ===== 涨停 Logo（艺术字，1-bit 双平面） =====
# 数据格式：zlib 压缩后 = [描边平面 288B][主体平面 288B]，每行 8 字节(64px/8)，高位在前
LOGO_W, LOGO_H = 64, 36
# 开机页居中位置（屏幕 160×128）
LOGO_CX, LOGO_CY = (160 - LOGO_W) // 2, (128 - LOGO_H) // 2
LOGO_ZT = (
    b'\x78\xda\x95\xd0\x41\x4a\x23\x41\x14\x06\xe0\x3f\xa1\xb1\x6a\x91'
    b'\xe9\x6a\x77\x0d\xc6\x4a\x8a\x1c\xc0\x04\x17\x0e\x43\x9b\x68\xf7'
    b'\x11\xdc\x0f\xe4\x06\xba\xcb\x22\xa4\x2a\x06\x34\x0b\xd1\x1b\x08'
    b'\x7d\x8f\x1e\x26\xe9\xbe\x87\xc4\x1b\xf4\xb2\x16\xd2\xe5\xab\x28'
    b'\xc2\xac\x06\xdf\xe2\x7d\xfc\x8b\x9f\x2a\x1e\xf0\xef\x84\x58\xd0'
    b'\xb6\x38\x72\xa5\x14\xcb\x07\x8c\x4c\x7a\xca\xb7\x01\xfa\x26\x9d'
    b'\x72\x7d\xff\x36\x44\x7a\x19\x03\x41\x6c\xd2\x2a\x32\x08\x7e\xd8'
    b'\xec\xce\xcb\x78\xd6\x19\x39\xf7\xd6\xb5\x69\xac\xfa\x88\xce\x4c'
    b'\x1a\x79\x95\xc9\x86\x53\xb2\x6f\x32\x77\x39\x68\x48\xd3\xda\x52'
    b'\x1e\x3e\x99\xd6\x92\x3c\x5e\x7f\xc8\x7c\x76\xae\x81\x49\x7b\x4b'
    b'\xa0\x2d\x4c\x16\x79\xb9\xc9\xe2\x9c\x8c\x4d\xc5\x73\xe1\xca\xc8'
    b'\x56\x1d\xe5\x5d\x57\x77\x6a\x83\xf0\x70\x5d\xae\xd4\x05\xf8\x21'
    b'\xaf\xb6\xb9\xae\xec\x28\x2e\x2f\x72\xa4\x50\xf5\x66\x9a\xa3\x84'
    b'\xc2\xe6\x46\x89\x5b\xef\x95\xe2\x07\x18\x2c\x5f\x5f\x14\x0f\xf0'
    b'\x6b\x75\x02\xc5\x3b\x08\x9b\x9f\x98\x08\x8b\xef\x4e\x80\x84\xf6'
    b'\x35\x42\x5d\x74\x18\xdd\xab\xa7\x0b\xc9\xcc\x0c\xc2\x15\x63\xe6'
    b'\x9c\x65\xba\x48\x84\x76\xb6\xcd\x8a\x99\x17\xac\xa8\x43\x5f\x64'
    b'\x7f\x76\xb2\xe7\x76\x5c\x7f\x28\x75\xb1\xf1\x52\x1f\xe3\x6e\x8b'
    b'\xd4\xcd\x98\xb2\xb8\xd6\xcd\x82\x6c\x7f\x0a\xaf\xef\x53\x6f\x41'
    b'\x77\x86\x2e\xf6\xd2\x3b\xbb\xdf\x7b\x93\x5a\xd6\x48\x04\xfb\x72'
    b'\x26\x27\xae\x0e\x59\x32\xff\x74\x2c\xe9\xcf\x21\x3b\xff\xeb\x95'
    b'\x62\xf2\xbc\xd7\x4d\x1e\x25\xe6\xde\x07\xc9\x2c\xba\x0b\x09\x6f'
    b'\x30\x8f\xc8\xfa\xbf\x77\x78\x07\x04\xa7\xa5\xad'
)

# Logo 颜色（tft.color(r,g,b) 返回 RGB565 整数）
C_LOGO_EDGE = tft.color(96, 0, 0)   # 暗红描边
C_LOGO_MAIN = tft.color(255, 60, 60) # 亮红主体


def _draw_logo_at(cx, cy):
    """在指定坐标(cx,cy)用 tft.pixel 绘制"涨停"logo。
    解压两个 1-bit 平面，逐像素画点（避开字节序问题）。"""
    buf = zlib.decompress(LOGO_ZT)
    row_bytes = (LOGO_W + 7) // 8
    for plane_off, color in ((0, C_LOGO_EDGE), (288, C_LOGO_MAIN)):
        base = plane_off
        for row in range(LOGO_H):
            rb = base + row * row_bytes
            for col in range(LOGO_W):
                if buf[rb + (col >> 3)] & (0x80 >> (col & 7)):
                    tft.pixel((cx + col, cy + row), color)


def draw_logo_centered():
    """开机页居中大 logo（仅开机页使用，数据页不显示 logo）"""
    _draw_logo_at(LOGO_CX, LOGO_CY)

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

# ===== DNS 兜底 =====
# 部分路由器 DHCP 不下发 DNS 服务器（w.ifconfig() 第 4 项为 0.0.0.0）。
# 此时所有 getaddrinfo 都会抛 OSError(-202)，后果是：NTP 校时失败（日期停在
# 出厂默认 2000/01/01，星期也不对进而把所有市场判成休盘）+ 三个行情源全挂。
# 因此连上 WiFi 后主动校验 DNS，不可用就依次试公共 DNS，用"能否解析出行情
# 域名"作为判据，而不是只看配置项是否非零。
DNS_FALLBACKS = ["223.5.5.5", "119.29.29.29", "114.114.114.114", "8.8.8.8"]
DNS_PROBE_HOST = "qt.gtimg.cn"


def _dns_works():
    """当前 DNS 能否解析出 Probe 主机"""
    try:
        addr = socket.getaddrinfo(DNS_PROBE_HOST, 80)[0][-1]
        return bool(addr and addr[0])
    except Exception:
        return False


def ensure_dns(w):
    """确保 DNS 可用，返回最终生效的 DNS；全部失败返回 '0.0.0.0'"""
    ip, mask, gw, dns = w.ifconfig()
    if dns and dns != "0.0.0.0" and _dns_works():
        return dns
    # 优先试公共 DNS，最后退回网关（不少路由器会代转发 DNS 查询）
    for cand in DNS_FALLBACKS + ([gw] if gw and gw != "0.0.0.0" else []):
        try:
            w.ifconfig((ip, mask, gw, cand))
            if _dns_works():
                print("DNS fallback -> %s" % cand)
                return cand
        except Exception:
            continue
    print("DNS fallback FAILED")
    return "0.0.0.0"


def connect_wifi():
    w = network.WLAN(network.STA_IF)
    w.active(True)
    if not w.isconnected():
        w.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(30):
            if w.isconnected():
                break
            time.sleep(0.5)
    try:
        w.config(pm=0)
    except Exception:
        pass
    ok = w.isconnected()
    if ok:
        ensure_dns(w)
    return ok


def sync_time():
    """多 NTP 源依次尝试，任意一个成功即可"""
    for host in ("ntp.aliyun.com", "pool.ntp.org", "cn.pool.ntp.org"):
        try:
            import ntptime
            ntptime.host = host
            ntptime.settime()
            print("NTP ok via %s" % host)
            return True
        except Exception as e:
            print("NTP fail %s: %r" % (host, e))
    return False

def beijing_time():
    try:
        t = time.localtime(time.time() + 8 * 3600)
        return "%02d:%02d:%02d" % (t[3], t[4], t[5])
    except Exception:
        return "--:--:--"

def beijing_date():
    """北京时间日期，格式 yyyy/mm/dd（10 个 ASCII 字符）"""
    try:
        t = time.localtime(time.time() + 8 * 3600)
        return "%04d/%02d/%02d" % (t[0], t[1], t[2])
    except Exception:
        return "----/--/--"

def bj_hour_min():
    """返回北京时间的 (hour, minute, weekday) weekday:0=周一...6=周日"""
    t = time.localtime(time.time() + 8 * 3600)
    return t[3], t[4], t[6]

def us_et_offset():
    """返回美东时区偏移(秒)。EDT=-4h(夏令时), EST=-5h(冬令时)。近似判断"""
    t = time.gmtime(time.time())
    m, d = t[1], t[2]
    if 4 <= m <= 10:
        return -4 * 3600
    if m <= 2 or m == 12:
        return -5 * 3600
    if m == 3:
        return -4 * 3600 if d >= 8 else -5 * 3600
    if m == 11:
        return -5 * 3600 if d >= 8 else -4 * 3600
    return -4 * 3600

def us_session():
    """返回美股时段: 'pre'盘前 / 'open'盘中 / 'after'盘后 / 'closed'休盘（基于美东时间）"""
    off = us_et_offset()
    et = time.gmtime(time.time() + off)
    wd = et[6]              # 0=周一...6=周日
    hm = et[3] * 60 + et[4]
    if wd in (5, 6):        # 周末
        return "closed"
    if 240 <= hm < 570:     # 4:00-9:30 盘前
        return "pre"
    if 570 <= hm < 960:     # 9:30-16:00 盘中
        return "open"
    if 960 <= hm < 1200:    # 16:00-20:00 盘后
        return "after"
    return "closed"

def us_session_label():
    """返回美股时段中文标签"""
    return {"pre": "盘前", "open": "盘中", "after": "盘后", "closed": "休盘"}.get(us_session(), "休盘")

def market_status(mkt):
    """返回市场状态: 'open'(盘中) / 'closed'(休盘)；美股返回 us_session 的细粒度结果"""
    h, m, wd = bj_hour_min()
    cur_min = h * 60 + m
    is_weekend = wd in (5, 6)   # 周六=5, 周日=6

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
        return us_session()

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

def _recv_headers(s):
    """读到响应头结束，返回 (head, 头部之后已经顺带收到的正文片段)

    注意 recv 返回的字节里可能已经包含了部分正文，必须交还给调用方，
    否则这部分数据会丢。
    """
    buf = b""
    while b"\r\n\r\n" not in buf:
        c = s.recv(256)
        if not c:
            break
        buf += c
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _read_body(s, head, rest):
    """按响应头把正文读完。

    关键坑：新浪会**无视**请求里的 `Connection: close`，回 `Connection:
    Keep-Alive` 且不主动断连。老写法是 `while True: recv(); if not c:
    break`，即"一直读到对端关闭为止" —— 于是即便 Content-Length 指定的
    正文早就收齐了，也仍会死等到 socket 超时（实测每轮白等 3 秒后抛
    OSError(116)，导致主力源被误判为失败）。腾讯/东财正常只是因为它们
    肯主动断连，属于侥幸。

    因此这里优先按 Content-Length 精确收包，chunked 读到 0 长度块为止，
    两者都没有时才退回"读到对端关闭"。
    """
    hl = head.lower()
    if b"transfer-encoding: chunked" in hl:
        body = rest
        while b"\r\n0\r\n\r\n" not in body and b"\r\n0\r\n" not in body:
            c = s.recv(256)
            if not c:
                break
            body += c
        return dechunk(body)

    n = -1
    i = hl.find(b"content-length:")
    if i >= 0:
        j = hl.find(b"\r\n", i)
        try:
            n = int(hl[i + 15:j].strip())
        except Exception:
            n = -1
    if n >= 0:
        body = rest
        while len(body) < n:
            c = s.recv(256)
            if not c:
                break
            body += c
        return body[:n]

    body = rest
    while True:
        c = s.recv(256)
        if not c:
            break
        body += c
    return body


def http_get(host, path):
    if host not in _DNS:
        _DNS[host] = socket.getaddrinfo(host, 80)[0][-1]
    s = socket.socket()
    try:
        s.settimeout(5)
        s.connect(_DNS[host])
        s.send(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n" % (path, host)).encode())
        head, rest = _recv_headers(s)
        return _read_body(s, head, rest)
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
    """codes: list of str like ['sh000001','usQQQ']。返回 {code: (status,price,chg,pct,name)}"""
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
                code = varname.split(b'_', 1)[1].decode()
                status = int(f[0].decode())
                price = float(f[3].decode())
                chg = float(f[31].decode())
                pct = float(f[32].decode())
                result[code] = (status, price, chg, pct, "")
            except Exception:
                continue
    except Exception:
        pass
    return result

# ============= 数据源配置 =============
# 新浪代码 → 统一内部code
# 美股用 ETF 而非指数：指数(IXIC/SOX)交易所盘前盘后不计算，新浪 gb_ 的
# 盘前盘后数据段恒为空，只能显示收盘价；ETF(QQQ/SOXQ)盘前盘后有真实成交，
# 配合 fetch_sina 的 ext 段解析才能真正拿到盘前盘后实时行情。
SINA_CODES = ["s_sh000001", "s_sz399006", "s_sz399001", "s_sh000688", "b_KOSPI", "int_nikkei", "gb_qqq", "gb_soxq"]
# 新浪code → 内部code 映射
SINA_MAP = {
    "s_sh000001": "sh000001", "s_sz399006": "sz399006",
    "s_sz399001": "sz399001", "s_sh000688": "sh000688",
    "b_KOSPI": "KS11", "int_nikkei": "N225",
    "gb_qqq": "usQQQ", "gb_soxq": "usSOXQ",
}
# 腾讯备用
TX_CODES = ["sh000001", "sz399006", "sz399001", "sh000688", "usSOXQ", "usQQQ"]
# 东财备用
EM_SECIDS = "100.KS11,100.N225"

# 数据源状态：0=未尝试,1=可用,-1=失败
_source_status = {"sina": 0, "tx": 0, "em": 0}

def _num(bs):
    """把一段纯 ASCII 数字字节转成 float。

    行情报文里的数字都是 ASCII，逐字段解码即可绕开中文（详见 fetch_sina
    中关于 latin-1 的说明）。
    """
    return float(bs.decode())


def fetch_sina():
    """新浪单请求8个指数。返回 {内部code: (price, chg, pct)} 或空dict"""
    result = {}
    qstr = ",".join(SINA_CODES)
    try:
        s = socket.socket()
        # 5s：新浪 Keep-Alive 的场景下已在 _read_body 里按 Content-Length 精确
        # 收包，正常几百毫秒就结束；这里只是兜住真正的网络异常
        s.settimeout(5)
        addr = socket.getaddrinfo("hq.sinajs.cn", 80)[0][-1]
        s.connect(addr)
        # 新浪必须带 Referer：实测不带时服务器连一个字节都不回（直接超时）
        req = "GET /list=%s HTTP/1.1\r\nHost: hq.sinajs.cn\r\nReferer: http://finance.sina.com.cn\r\nConnection: close\r\n\r\n" % qstr
        s.send(req.encode())
        # 新浪回 Connection: Keep-Alive 且不断连，必须按 Content-Length 收
        head, rest = _recv_headers(s)
        body = _read_body(s, head, rest)
        s.close()
        # 全程按 bytes 处理，只对每个数字字段单独解码。
        #
        # 不能整包 body.decode("latin-1")：新浪正文是 GB18030，含中文指数名
        # （如 "\xb4\xf3\xc5\xcc"）。而本固件的 MicroPython（737320dbc-dirty
        # / ESP32-S2）**没有真正的 latin-1** —— 对不认识的 codec 名会静默
        # 退回 utf-8 语义，于是凡 >=0x80 的字节一律抛 UnicodeError，整个
        # 新浪源被误判为死亡。数字字段本来就是纯 ASCII，逐字段解码即可。
        for line in body.split(b"\n"):
            if b'="' not in line:
                continue
            try:
                varpart, val = line.split(b'="', 1)
                val = val.rstrip(b'";\r\n')
                if not val:
                    continue
                # varpart 形如 b'var hq_str_s_sh000001'。
                # 必须剥掉 'hq_str_' 前缀而不是取最后一个 '_' 之后的部分：
                # 后者会把 's_sh000001' 砍成 'sh000001'、'gb_qqq' 砍成
                # 'qqq'，而 SINA_MAP 的键是完整代码，查表全落空 -> 新浪源
                # 一个指数都取不到，被整体判死（实测 8 条全 MISS）。
                varname = varpart.split()[-1]
                sina_code = varname[len(b"hq_str_"):].decode()
                inner_code = SINA_MAP.get(sina_code)
                if not inner_code:
                    continue
                fields = val.split(b",")
                if sina_code.startswith("s_"):
                    # A股简版: 名称,现价,涨跌额,涨跌幅,...
                    price = _num(fields[1])
                    pct = _num(fields[3])
                    chg = _num(fields[2])
                elif sina_code.startswith("gb_"):
                    # 美股: 名称,现价,涨跌幅%,时间,涨跌额,...
                    price = _num(fields[1])
                    pct = _num(fields[2])
                    chg = _num(fields[4])
                    # 盘前/盘后：新浪把 extended session 数据放在 [21]~[24]，
                    # 而 [1]/[2]/[4] 冻结在正式收盘价。实测(2026-09-03 盘后)
                    # gb_soxx 曾出现 [1] 锁死 501.44 而 [21] 一路跌到 497.28
                    # (盘后 -4.16%)，只读 [1] 就永远显示"不动的收盘价"。
                    #   [21]=盘前/盘后最新价 [22]=涨跌幅%(相对收盘) [23]=涨跌额
                    #   [24]=盘前/盘后时间戳 [25]=收盘时间戳      [26]=昨收
                    # 涨跌幅用昨收[26]自算，与盘中口径一致（[22]是相对收盘）。
                    # 指数盘前盘后无行情 -> [21] 恒为 0 天然回退收盘；现已改用
                    # ETF，仍保留此回退以防 ETF 盘前无成交或未来换回指数。
                    try:
                        ext_px = _num(fields[21])
                        prev = _num(fields[26])
                        if ext_px > 0 and prev > 0:
                            price = ext_px
                            chg = ext_px - prev
                            pct = chg / prev * 100.0
                    except Exception:
                        pass
                elif sina_code == "b_KOSPI":
                    # 韩国: 名称,现价,涨跌额,涨跌幅,...
                    price = _num(fields[1])
                    chg = _num(fields[2])
                    pct = _num(fields[3])
                elif sina_code == "int_nikkei":
                    # 日经: 名称,现价,涨跌额,涨跌幅
                    price = _num(fields[1])
                    chg = _num(fields[2])
                    pct = _num(fields[3])
                else:
                    continue
                result[inner_code] = (price, chg, pct)
            except Exception:
                continue
    except Exception:
        pass
    return result

def _log_src():
    """打印实际生效的数据源，便于串口排查（1=在用, -1=失败, 0=未尝试）"""
    print("SRC sina=%d tx=%d em=%d" % (_source_status["sina"],
                                       _source_status["tx"],
                                       _source_status["em"]))


# 各页面用到的全部内部 code（用于检测缺失并触发备用源补齐）
ALL_CODES = ["sh000001", "sz399006", "sz399001", "sh000688", "KS11", "N225", "usSOXQ", "usQQQ"]

def fetch_all():
    """返回 (data_dict, any_trading)
    主力=新浪(单请求8个)，备用=腾讯+东财。自动切换。
    data = {内部code: (status, price, chg, pct, "")}
    status: 新浪/东财无状态码用200占位；腾讯用真实状态码

    容错策略：新浪偶发会缺 gb_qqq/gb_soxq 中某条（返回 7 条甚至 6 条），
    旧逻辑只看 len>=6 就整体采用并 return，导致 D 页缺 key 显示横杠。
    现改为：新浪可用时先落数据，再检查全量缺失项，缺 A股/美股用腾讯补、
    缺韩日用东财补，补不齐才整页回退。"""
    global _source_status
    data = {}
    any_trading = False

    # 主力：新浪
    if _source_status["sina"] >= 0:
        sina_data = fetch_sina()
        if len(sina_data) >= 6:   # 至少6个成功才算可用
            _source_status["sina"] = 1
            for code, (p, c, pct) in sina_data.items():
                data[code] = (200, p, c, pct, "")   # 新浪无状态码，用200占位
            # 检测缺失项，用备用源补齐（不因个别缺失就整页弃用新浪）
            missing = [c for c in ALL_CODES if c not in data]
            if missing:
                # 腾讯能补 A股(sh/sz) 和美股(us)；东财能补韩日(KS11/N225)
                # 注意：本固件 MicroPython 的 str.startswith 不接受 tuple 参数
                # （传 tuple 会报 can't convert 'tuple' object to str），用切片判断。
                tx_needed = [c for c in missing if c[:2] in ("sh", "sz", "us")]
                em_needed = [c for c in missing if c in ("KS11", "N225")]
                if tx_needed:
                    tx = fetch_tx_batch(tx_needed)
                    for code, (st, p, c, pct, n) in tx.items():
                        data[code] = (st, p, c, pct, n)
                        if st != 1:
                            any_trading = True
                    # 腾讯补到的，更新状态
                    if tx:
                        _source_status["tx"] = 1
                if em_needed:
                    _em_fill(data, em_needed)
            # 新浪无法判断休盘，用本地时段推算
            any_trading = check_any_trading()
            _log_src()
            return data, any_trading
        else:
            # 关键：新浪失败时要把腾讯重新置为"待尝试"。
            # 否则一旦新浪曾经成功过（那时会把 tx 置 -1），之后新浪偶发失败
            # 就会导致腾讯分支被跳过，只剩东财，A股和美股全缺。
            _source_status["sina"] = -1
            if _source_status["tx"] < 0:
                _source_status["tx"] = 0

    # 备用1：腾讯(A股+美股)
    if _source_status["tx"] >= 0:
        tx = fetch_tx_batch(TX_CODES)
        if len(tx) >= 4:
            _source_status["tx"] = 1
            for code, (st, p, c, pct, n) in tx.items():
                data[code] = (st, p, c, pct, n)
                if st != 1:
                    any_trading = True
        else:
            _source_status["tx"] = -1

    # 备用2：东财延迟站(韩国+日本)
    _em_fill(data, ["KS11", "N225"])

    _log_src()
    return data, any_trading

def _em_fill(data, codes):
    """从东财延迟站补齐指定 code（韩国/日本），失败不影响主流程"""
    if not codes:
        return
    try:
        path = "/api/qt/ulist.np/get?fltt=2&secids=" + EM_SECIDS + "&fields=f2,f3,f4,f12"
        d = json.loads(http_get("push2delay.eastmoney.com", path).decode("utf-8"))
        for it in d["data"]["diff"]:
            code = it["f12"]
            if code in codes:
                data[code] = (200, it["f2"], it["f4"], it["f3"], "")
                _source_status["em"] = 1
    except Exception:
        _source_status["em"] = -1

def check_any_trading():
    """根据本地时段判断是否有任何市场在交易"""
    for _, _, mkt in [item for page in PAGES for item in page]:
        if market_status(mkt) != "closed":
            return True
    return False

_last = {}

def _draw(key, text, x, y, color):
    if _last.get(key) == (text, color):
        return
    _last[key] = (text, color)
    printXy(text, x, y, S_SIZE, color)

def render(data, page, tstr, force_clear):
    global _last
    if force_clear:
        clear()
        _last = {}
        # 数据页不画 logo（logo 只在开机页居中显示）
    # 右上角三行：日期 / 北京时间 / 美股时段标签（时段标签仅 D 页显示）
    _draw('d', beijing_date(), X_RIGHT, Y_DATE, C_FLAT)
    _draw('t', tstr, X_RIGHT, Y_TIME, C_FLAT)
    # 判断当前页是否有美股
    has_us = any(mkt == "US" for _, _, mkt in PAGES[page])
    if has_us:
        # 美股时段标签用本地时间判断（新浪无状态码）
        _draw('us', "%-5s" % us_session_label(), X_RIGHT, Y_SESSION, C_FLAT)
    else:
        _draw('us', "%-5s" % "", X_RIGHT, Y_SESSION, C_FLAT)
    # 两个指数：名称/价格/涨跌幅 三行
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
        # 所有市场统一显示：价格 + 涨跌幅（休盘时也是收盘价）
        c = C_UP if pct > 0 else (C_DOWN if pct < 0 else C_FLAT)
        _draw((k, 'p'), "%-9s" % ("%.2f" % price), 2, py, c)
        sign = "+" if pct >= 0 else ""
        _draw((k, 'c'), "%-8s" % ("%s%.2f%%" % (sign, pct)), 2, cy, c)

def read_button():
    """读按键，带软件去抖。检测到高电平后连续采样确认稳定才返回键号，
    否则视为触点抖动忽略（实测 B 键 100ms 内跳变 3 次，见 key_debug 采样）。"""
    try:
        for k, pin in hal.key_map.items():
            if pin.value() == 1:
                ok = True
                for _ in range(DEBOUNCE_COUNT):
                    time.sleep_ms(DEBOUNCE_STEP_MS)
                    if pin.value() != 1:
                        ok = False
                        break
                if ok:
                    return k
    except Exception:
        pass
    return 0

def main():
    clear()
    draw_logo_centered()  # 开机页居中显示"涨停"logo
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
        wdt = WDT(timeout=WDT_TIMEOUT_MS)
    except Exception:
        wdt = None
    # 首次抓取
    data, trading = fetch_all()
    last_fetch = time.time()
    print("BOOT-FETCH ok:%d trade:%s" % (len(data), trading))
    gc.collect()
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
            # fetch 后立即回收 socket/HTTP 缓冲，避免 render 阶段分配 SPI
            # 传输缓冲时踩到碎片化堆（ESP32-S2 DMA 冻结的高频诱因）
            gc.collect()
            if wdt is not None:
                wdt.feed()
            print("FETCH ok:%d trade:%s t:%s cost:%.1fs free:%d" % (len(data), trading, beijing_time(), _cost, gc.mem_free()))
        cur_sec = int(now)
        if cur_sec != last_sec:
            # render 前留点时间让 WiFi 后台 DMA 排空，再收一次碎片
            if DMA_SETTLE_MS:
                time.sleep_ms(DMA_SETTLE_MS)
            gc.collect()
            render(data, page, beijing_time(), False)
            last_sec = cur_sec
        if now - last_log >= 30:
            log("run:%ds pg:%d btn:%d ok:%d trade:%s" % (int(now - start), page, btn_seen, len(data), trading))
            print("RUN %ds pg%d btn%d ok%d trade%s" % (int(now - start), page, btn_seen, len(data), trading))
            last_log = now
        time.sleep(0.1)

main()
