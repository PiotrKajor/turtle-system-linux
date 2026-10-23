"""Retro pixel-art logo variants (style of logo 10)."""
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QPen, QFont
from PySide6.QtCore import Qt, QRectF
from PySide6.QtSvg import QSvgRenderer

app = QGuiApplication.instance() or QGuiApplication([])
S = 512


def pixel_turtle(source, w, palette=None):
    """The turtle shrunk to w pixels wide; optionally quantised to a few palette colours."""
    if source.endswith(".svg"):
        big = QImage(400, 400, QImage.Format.Format_ARGB32); big.fill(Qt.GlobalColor.transparent)
        bp = QPainter(big); QSvgRenderer("src/" + source).render(bp, QRectF(0, 0, 400, 400)); bp.end()
        xs = [x for x in range(400) for y in range(0, 400, 4) if big.pixelColor(x, y).alpha() > 20]
        ys = [y for y in range(400) for x in range(0, 400, 4) if big.pixelColor(x, y).alpha() > 20]
        big = big.copy(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    else:
        big = QImage("src/" + source)
    small = big.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
    small = small.convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            if c.alpha() < 110:
                small.setPixelColor(x, y, QColor(0, 0, 0, 0)); continue
            if palette:
                lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255
                idx = min(len(palette) - 1, int(lum * len(palette)))
                small.setPixelColor(x, y, QColor(palette[idx]))
            else:
                c.setAlpha(255); small.setPixelColor(x, y, c)
    return small


def tile(bg, border, turtle_img, text, text_col, scanlines=False, radius=40, text_px=84,
         turtle_rect=(76, 70, 360, 288), pixel_border=False):
    img = QImage(S, S, QImage.Format.Format_ARGB32); img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if pixel_border:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        u = 16
        p.fillRect(QRectF(8 + u, 8, S - 16 - 2 * u, S - 16), QColor(border))     # stepped frame
        p.fillRect(QRectF(8, 8 + u, S - 16, S - 16 - 2 * u), QColor(border))
        p.fillRect(QRectF(8 + 2 * u, 8 + u, S - 16 - 4 * u, S - 16 - 2 * u), QColor(bg))
        p.fillRect(QRectF(8 + u, 8 + 2 * u, S - 16 - 2 * u, S - 16 - 4 * u), QColor(bg))
    else:
        p.setPen(QPen(QColor(border), 10)); p.setBrush(QColor(bg))
        p.drawRoundedRect(QRectF(8, 8, S - 16, S - 16), radius, radius)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    bx, by, bw, bh = turtle_rect
    k = min(bw / turtle_img.width(), bh / turtle_img.height())
    w, h = turtle_img.width() * k, turtle_img.height() * k
    p.drawImage(QRectF(bx + (bw - w) / 2, by + (bh - h) / 2, w, h), turtle_img)
    if text:
        f = QFont("Liberation Mono"); f.setPixelSize(text_px); f.setBold(True)
        p.setFont(f); p.setPen(QColor(text_col))
        p.drawText(QRectF(0, 360, S, 110), Qt.AlignmentFlag.AlignCenter, text)
    if scanlines:
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, 70))
        for y in range(20, S - 20, 6):
            p.drawRect(QRectF(20, y, S - 40, 2))
    p.end()
    return img


if __name__ == "__main__":
    GB = ["#0f380f", "#306230", "#8bac0f", "#9bbc0f"]
    AMBER = ["#3a1f00", "#a65e00", "#ffb000", "#ffd27a"]
    GREEN = ["#06260f", "#1f7a35", "#33ff66", "#b8ffc8"]
    C64 = ["#40318d", "#7869c4", "#a5a5ff", "#ffffff"]
    CGA = ["#000000", "#ff55ff", "#55ffff", "#ffffff"]
    MONO = ["#000000", "#555555", "#aaaaaa", "#ffffff"]

    variants = [
        (tile("#0f3b1f", "#7ccf4a", pixel_turtle("noto_turtle.png", 40), "> TS_", "#7ccf4a"),
         "10 (oryginał)"),
        (tile("#04140a", "#33ff66", pixel_turtle("noto_turtle.png", 40, GREEN), "> TS_", "#33ff66",
              scanlines=True), "Zielony terminal + scanlines"),
        (tile("#1a0d00", "#ffb000", pixel_turtle("noto_turtle.png", 40, AMBER), "> TS_", "#ffb000",
              scanlines=True), "Bursztynowy CRT"),
        (tile("#9bbc0f", "#0f380f", pixel_turtle("noto_turtle.png", 36, GB[::-1]), "TURTLE", "#0f380f",
              radius=28, text_px=70), "Game Boy"),
        (tile("#40318d", "#7869c4", pixel_turtle("fluent_color.svg", 40, C64), "READY.", "#a5a5ff",
              radius=6, text_px=72), "Commodore 64"),
        (tile("#000000", "#55ffff", pixel_turtle("fluent_color.svg", 40, CGA), "> TS_", "#ff55ff",
              radius=6), "CGA"),
        (tile("#0f3b1f", "#7ccf4a", pixel_turtle("noto_turtle.png", 40), "> TS_", "#7ccf4a",
              pixel_border=True), "10 z pikselową ramką"),
        (tile("#ffffff", "#000000", pixel_turtle("noto_turtle.png", 40, MONO), "> TS_", "#000000",
              radius=20), "Czarno-biały (Mac 1984)"),
        (tile("#0f3b1f", "#7ccf4a", pixel_turtle("noto_turtle.png", 28), None, None,
              turtle_rect=(56, 96, 400, 320)), "10 bez napisu, grubszy piksel"),
        (tile("#101820", "#7ccf4a", pixel_turtle("fluent_color.svg", 40), "> TS_", "#7ccf4a",
              scanlines=True), "Fluent piksel, ciemny"),
    ]

    sheet = QImage(5 * 300 + 20, 2 * 340 + 20, QImage.Format.Format_RGB32); sheet.fill(QColor("#d9dde3"))
    sp = QPainter(sheet); sp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for n, (im, label) in enumerate(variants, 1):
        im.save(f"retro_{n:02d}.png")
        col, row = (n - 1) % 5, (n - 1) // 5
        x0, y0 = 20 + col * 300, 20 + row * 340
        sp.drawImage(QRectF(x0, y0, 260, 260), im)
        f = QFont("Liberation Sans"); f.setPixelSize(17); f.setBold(True); sp.setFont(f)
        sp.setPen(QColor("#202020"))
        sp.drawText(QRectF(x0 - 15, y0 + 268, 290, 50), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    f"R{n}: {label}")
    sp.end(); sheet.save("retro_overview.png")
    print("ok")
