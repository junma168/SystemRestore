"""生成应用图标 assets/icon.ico 与 assets/icon.png（纯 Python，无第三方依赖）。"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

SS = 3               # 超采样倍数（抗锯齿）
BASE = 256           # 基准尺寸
TOP = (59, 130, 246)     # #3b82f6
BOTTOM = (29, 64, 175)   # #1e40af
WHITE = (255, 255, 255)
FEATHER = 1.2        # 边缘软化宽度（超采样像素）


def _rect_cov(x: float, y: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """矩形覆盖率（0..1），带边缘软化。"""
    d = min(x - x0, x1 - x, y - y0, y1 - y)
    return min(1.0, max(0.0, d / FEATHER))


def _round_rect_cov(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> float:
    """圆角矩形覆盖率：内部为 1，边缘按距离软化。"""
    d_edge = min(x - x0, x1 - x, y - y0, y1 - y)
    if d_edge <= -FEATHER:
        return 0.0
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    d = min(r - (dx * dx + dy * dy) ** 0.5, d_edge)
    return min(1.0, max(0.0, d / FEATHER))


def _tri_cov(x: float, y: float, p1, p2, p3) -> float:
    """三角形覆盖率（跨边框宽度 1 像素的软化边）。"""
    def edge(a, b, px, py):
        return (px - a[0]) * (b[1] - a[1]) - (py - a[1]) * (b[0] - a[0])
    d1, d2, d3 = edge(p1, p2, x, y), edge(p2, p3, x, y), edge(p3, p1, x, y)
    if (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0):
        return 1.0
    return 0.0   # 三角形外；抗锯齿由超采样降采样负责


def render(size: int) -> bytes:
    """渲染指定尺寸的 RGBA 位图。"""
    ss = size * SS
    out = bytearray(ss * ss * 4)
    margin = ss * 0.04
    r = ss * 0.22
    # 箭头几何（归一化坐标 0..1，y 向下；使用时换算为超采样像素）
    shaft_n = (0.435, 0.16, 0.565, 0.56)                    # 箭杆
    head_n = ((0.5, 0.84), (0.24, 0.52), (0.76, 0.52))      # 箭头三角
    tray_n = (0.30, 0.88, 0.70, 0.93)                       # 底部托盘线
    shaft = tuple(c * ss for c in shaft_n)
    head = tuple((px * ss, py * ss) for px, py in head_n)
    tray = tuple(c * ss for c in tray_n)
    i = 0
    for yy in range(ss):
        v = yy / ss
        for xx in range(ss):
            u = xx / ss
            body = _round_rect_cov(xx, yy, margin, margin, ss - margin, ss - margin, r)
            if body <= 0:
                out[i:i + 4] = b"\x00\x00\x00\x00"
                i += 4
                continue
            # 竖向渐变
            t = v
            col = (
                int(TOP[0] + (BOTTOM[0] - TOP[0]) * t),
                int(TOP[1] + (BOTTOM[1] - TOP[1]) * t),
                int(TOP[2] + (BOTTOM[2] - TOP[2]) * t),
            )
            arrow = max(
                _rect_cov(xx, yy, *shaft),
                _tri_cov(xx, yy, *head),
                _rect_cov(xx, yy, *tray) * 0.85,
            )
            if arrow > 0:
                col = tuple(int(col[k] + (WHITE[k] - col[k]) * min(1.0, arrow)) for k in range(3))
            out[i:i + 4] = bytes((col[0], col[1], col[2], int(255 * min(1.0, body))))
            i += 4
    # 盒式降采样
    if SS > 1:
        small = bytearray(size * size * 4)
        for y in range(size):
            for x in range(size):
                acc = [0, 0, 0, 0]
                for dy in range(SS):
                    for dx in range(SS):
                        p = ((y * SS + dy) * ss + (x * SS + dx)) * 4
                        for k in range(4):
                            acc[k] += out[p + k]
                q = (y * size + x) * 4
                for k in range(4):
                    small[q + k] = acc[k] // (SS * SS)
        return bytes(small)
    return bytes(out)


def write_png(path: Path, w: int, h: int, pixels: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + pixels[y * w * 4:(y + 1) * w * 4] for y in range(h))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def write_ico(path: Path, entries: list[tuple[int, bytes]]) -> None:
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    dir_bytes, blobs = b"", b""
    for size, data in entries:
        dir_bytes += struct.pack("<BBBBHHII", size & 0xFF, size & 0xFF, 0, 0, 1, 32,
                                 len(data), offset)
        blobs += data
        offset += len(data)
    path.write_bytes(header + dir_bytes + blobs)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    base = render(BASE)
    write_png(ASSETS / "icon.png", BASE, BASE, base)
    # 由 256 位图面积平均缩小出各尺寸
    sizes = [16, 24, 32, 48, 64, 128, 256]
    entries = []
    for s in sizes:
        if s == BASE:
            data = base
        else:
            small = bytearray(s * s * 4)
            ratio = BASE / s
            for y in range(s):
                for x in range(s):
                    x0, y0 = int(x * ratio), int(y * ratio)
                    x1, y1 = max(x0 + 1, int((x + 1) * ratio)), max(y0 + 1, int((y + 1) * ratio))
                    acc = [0, 0, 0, 0]
                    n = 0
                    for yy in range(y0, y1):
                        for xx in range(x0, x1):
                            p = (yy * BASE + xx) * 4
                            for k in range(4):
                                acc[k] += base[p + k]
                            n += 1
                    q = (y * s + x) * 4
                    for k in range(4):
                        small[q + k] = acc[k] // max(n, 1)
            data = bytes(small)
        import io
        buf = io.BytesIO()
        # 先写 PNG 再取字节
        tmp = ASSETS / f"_tmp_{s}.png"
        write_png(tmp, s, s, data)
        entries.append((s, tmp.read_bytes()))
        tmp.unlink()
    write_ico(ASSETS / "icon.ico", entries)
    print(f"已生成 {ASSETS / 'icon.ico'}（{len(entries)} 个尺寸）和 {ASSETS / 'icon.png'}")


if __name__ == "__main__":
    main()
