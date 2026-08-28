# -*- coding: utf-8 -*-
"""生成 README 用的 ABCD 四页 ASCII 示意图（改了屏幕布局后跑一下，保持文档同步）。

用法（在仓库根目录执行）：
    python tools/make_pages_ascii.py > pages_ascii.txt
然后把输出贴回 README 的『四页屏幕示意图』代码块。

坐标系与固件严格对应：
  横向 1 字符格 = 8px（ASCII 8px/字符 占 1 格，CJK 16px/字符 占 2 格）→ 160px = 20 格
  纵向 1 行     = 16px（小字号行高）                              → 128px = 8 行

固件坐标：POS=[(4,24,44),(68,88,108)]，Y_DATE/Y_TIME/Y_SESSION=4/20/36，X_RIGHT=76
映射到网格：左列起 col 0；右列 x=76 → col 9.5，图上取 col 10
           name/date → 第 0 行，price/time → 第 1 行，pct/session → 第 2 行
           第二块 name/price/pct → 第 4/5/6 行；第 3、7 行为块间隙与上下边距
"""
import io

W = 20          # 内容宽度（字符格）
H = 8           # 内容高度（行）
SEP = "   "     # 两个屏幕之间的间隔


def cw(s):
    """按终端显示宽度计：CJK 及其符号占 2 格，其余占 1 格"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def cell(row):
    """把一行内容右侧补空格到 W 格，并校验没有超宽"""
    assert cw(row) <= W, "超宽 %d: %r" % (cw(row), row)
    return row + " " * (W - cw(row))


def blank():
    return " " * W


DATE = "2026/08/29"
TIME = "03:36:58"


def page(top_name, top_price, top_pct, bot_name, bot_price, bot_pct, session=None):
    """构造一页的 8 行内容。session 仅 D 页（美股）有。"""
    rows = [
        cell(top_name + " " * (10 - cw(top_name) % 10 if False else 0)),
    ]
    # 逐行手工排版，保证右列固定落在 col 10
    r0 = top_name
    r0 += " " * (10 - cw(r0))
    r0 += DATE
    r1 = top_price
    r1 += " " * (10 - cw(r1))
    r1 += TIME
    r2 = top_pct
    r2 += " " * (10 - cw(r2))
    r2 += session or ""
    return [cell(r0), cell(r1), cell(r2), blank(),
            cell(bot_name), cell(bot_price), cell(bot_pct), blank()]


PAGES = {
    "A": page("上证指数", "3821.55", "+0.85%", "创业板指", "2156.30", "+1.20%"),
    "B": page("深证成指", "11842.60", "-0.42%", "科创50", "1058.77", "+2.13%"),
    "C": page("韩国综合", "3185.42", "+0.61%", "日经225", "42156.08", "-0.33%"),
    "D": page("SOX", "5821.44", "+1.75%", "IXIC", "21450.30", "+0.92%", session="盘中"),
}

TOP = "┌" + "─" * W + "┐"
BOT = "└" + "─" * W + "┘"


def side_by_side(k1, k2, title1, title2):
    out = []
    t1 = title1 + " " * (W + 2 - cw(title1))
    t2 = title2 + " " * (W + 2 - cw(title2))
    out.append((t1 + SEP + t2).rstrip())
    out.append(TOP + SEP + TOP)
    for i in range(H):
        out.append("│" + PAGES[k1][i] + "│" + SEP + "│" + PAGES[k2][i] + "│")
    out.append(BOT + SEP + BOT)
    return out


lines = []
lines += side_by_side("A", "B", "【A 键】A 股", "【B 键】A 股")
lines.append("")
lines += side_by_side("C", "D", "【C 键】亚太", "【D 键】美股")

# 校验方框各行的显示宽度一致（标题行不参与，它是自由文本）
box_lines = [l for l in lines if l and l[0] in "┌│└"]
widths = {cw(l) for l in box_lines}
assert len(widths) == 1, "方框各行宽度不一致: %s\n%s" % (widths, lines)
# 校验信息走 stderr，保证 stdout 只有示意图本身，可直接重定向
import sys
print("方框每行显示宽度: %s 格（一致）" % widths, file=sys.stderr)
assert all(len(v) == H for v in PAGES.values()), "页数行 != %d" % H
print("每页行数: %d 行（一致）" % H, file=sys.stderr)
art = "\n".join(lines)
print(art)


