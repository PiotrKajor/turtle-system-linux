"""Detailed variants of retro logo R2 (green phosphor terminal)."""
from PySide6.QtGui import (QGuiApplication, QImage, QPainter, QColor, QPen, QFont, QRadialGradient,
                           QLinearGradient, QBrush, QPainterPath)
from PySide6.QtCore import Qt, QRectF
from make_retro import pixel_turtle

S = 512
GREEN6 = ["#04200c", "#0b4a1d", "#14802f", "#22c24a", "#4dff7a", "#c8ffd6"]
GREEN8 = ["#031a09", "#073314", "#0e5221", "#167430", "#1f9a40", "#2fd158", "#6aff8e", "#d6ffe0"]


def blur(img, radius):
    """Cheap glow: downscale and upscale smoothly."""
    w, h = img.width(), img.height()
    small = img.scaled(max(1, w // radius), max(1, h // radius), Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def screen_layer(turtle, text, text_px, cursor, turtle_box):
    """The glowing content: pixel turtle + text, on a transparent layer."""
    layer = QImage(S, S, QImage.Format.Format_ARGB32); layer.fill(Qt.GlobalColor.transparent)
    p = QPainter(layer)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    bx, by, bw, bh = turtle_box
    k = min(bw / turtle.width(), bh / turtle.height())
    w, h = turtle.width() * k, turtle.height() * k
    p.drawImage(QRectF(bx + (bw - w) / 2, by + (bh - h) / 2, w, h), turtle)
    if text:
        f = QFont("Liberation Mono"); f.setPixelSize(text_px); f.setBold(True)
        p.setFont(f); p.setPen(QColor("#4dff7a"))
        adv = p.fontMetrics().horizontalAdvance(text)
        block = text_px * 0.55 + 6 if cursor else 0
        x0 = (S - adv - block) / 2                  # centre text and cursor together
        p.drawText(QRectF(x0, 362, adv + 2, 100), Qt.AlignmentFlag.AlignVCenter, text)
        if cursor:
            p.fillRect(QRectF(x0 + adv + 6, 382, text_px * 0.55, text_px * 0.75), QColor("#4dff7a"))
    p.end()
    return layer


def logo(detail, turtle_w, palette, text="> TS_", text_px=76, cursor=True, bezel=False,
         turtle_box=(70, 64, 372, 296)):
    img = QImage(S, S, QImage.Format.Format_ARGB32); img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    screen = QRectF(8, 8, S - 16, S - 16)
    if bezel:                                   # beige monitor case around the screen
        g = QLinearGradient(0, 0, 0, S); g.setColorAt(0, QColor("#d9d2bd")); g.setColorAt(1, QColor("#a39a80"))
        p.setPen(QPen(QColor("#6e6650"), 4)); p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(6, 6, S - 12, S - 12), 56, 56)
        screen = QRectF(40, 40, S - 80, S - 80)
    clip = QPainterPath(); clip.addRoundedRect(screen, 48 if bezel else 40, 48 if bezel else 40)
    # dark phosphor screen with a slightly brighter centre
    g = QRadialGradient(screen.center(), screen.width() * 0.7)
    g.setColorAt(0, QColor("#062a12")); g.setColorAt(1, QColor("#010a04"))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g)); p.drawPath(clip)
    p.save(); p.setClipPath(clip)
    turtle = pixel_turtle("noto_turtle.png", turtle_w, palette)
    content = screen_layer(turtle, text, text_px, cursor, turtle_box)
    if bezel:                                   # shrink content into the smaller screen
        p.translate(screen.x(), screen.y()); p.scale(screen.width() / S, screen.height() / S)
    if detail >= 2:                             # phosphor glow under the sharp pixels
        glow = blur(content, 10)
        p.setOpacity(0.85); p.drawImage(0, 0, glow); p.drawImage(0, 0, glow); p.setOpacity(1)
    p.drawImage(0, 0, content)
    if bezel:
        p.resetTransform()
    # scanlines
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, 90 if detail >= 2 else 70))
    step = 4 if detail >= 2 else 6
    y = screen.top()
    while y < screen.bottom():
        p.drawRect(QRectF(screen.left(), y, screen.width(), step / 2)); y += step
    if detail >= 3:                             # vignette: darker curved edges of the tube
        v = QRadialGradient(screen.center(), screen.width() * 0.62)
        v.setColorAt(0.0, QColor(0, 0, 0, 0)); v.setColorAt(0.75, QColor(0, 0, 0, 30))
        v.setColorAt(1.0, QColor(0, 0, 0, 170))
        p.setBrush(QBrush(v)); p.drawRect(screen)
        r = QLinearGradient(screen.topLeft(), screen.center())   # glass reflection
        r.setColorAt(0, QColor(255, 255, 255, 46)); r.setColorAt(1, QColor(255, 255, 255, 0))
        refl = QPainterPath(); refl.addEllipse(QRectF(screen.left() - 60, screen.top() - 120,
                                                     screen.width() * 0.9, screen.height() * 0.55))
        p.setBrush(QBrush(r)); p.drawPath(refl)
    p.restore()
    # bright green rim
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#33ff66" if not bezel else "#2a2a22"), 10 if not bezel else 6))
    p.drawPath(clip)
    if bezel:                                   # power LED
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#4dff7a"))
        p.drawEllipse(QRectF(S - 70, S - 30, 16, 10))
    p.end()
    return img


if __name__ == "__main__":
    app = QGuiApplication.instance() or QGuiApplication([])
    variants = [
        (logo(1, 40, ["#06260f", "#1f7a35", "#33ff66", "#b8ffc8"], cursor=False), "R2 (obecny)"),
        (logo(2, 64, GREEN6), "A: 64 px, 6 odcieni, poświata, kursor"),
        (logo(3, 80, GREEN8), "B: 80 px, 8 odcieni, winieta, refleks"),
        (logo(3, 80, GREEN8, text="> TURTLE_", text_px=58), "C: jak B, napis TURTLE"),
        (logo(3, 96, GREEN8, bezel=True), "D: 96 px w obudowie monitora"),
        (logo(3, 96, GREEN8, text=None, cursor=False, turtle_box=(50, 80, 412, 352)),
         "E: 96 px bez napisu (do małej ikony)"),
    ]
    sheet = QImage(3 * 340 + 20, 2 * 380 + 20, QImage.Format.Format_RGB32); sheet.fill(QColor("#d9dde3"))
    sp = QPainter(sheet); sp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for n, (im, label) in enumerate(variants):
        im.save(f"r2_{'0ABCDE'[n]}.png")
        col, row = n % 3, n // 3
        x0, y0 = 30 + col * 340, 20 + row * 380
        sp.drawImage(QRectF(x0, y0, 300, 300), im)
        f = QFont("Liberation Sans"); f.setPixelSize(17); f.setBold(True); sp.setFont(f)
        sp.setPen(QColor("#202020"))
        sp.drawText(QRectF(x0 - 10, y0 + 306, 320, 50), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, label)
    sp.end(); sheet.save("r2_overview.png")
    print("ok")
