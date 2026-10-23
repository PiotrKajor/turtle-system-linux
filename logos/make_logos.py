"""Render the 10 logo variants (512x512 PNG) from the Fluent (MIT) and Noto (Apache 2.0) turtles."""
import math, sys
from PySide6.QtGui import (QGuiApplication, QImage, QPainter, QColor, QPen, QBrush, QFont,
                           QLinearGradient, QRadialGradient, QPainterPath, QPolygonF)
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtSvg import QSvgRenderer
sys.path.insert(0, "..")
from colours import COLOURS

app = QGuiApplication([])
S = 512
SRC = "src/"

def turtle(p, kind, rect):
    rect = QRectF(*rect)
    if kind.endswith(".svg"):
        QSvgRenderer(SRC + kind).render(p, rect)
    else:
        img = QImage(SRC + kind)
        p.drawImage(rect, img)

def canvas():
    img = QImage(S, S, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    return img, p

def rounded(p, color, radius=96, inset=8, border=None, bw=0):
    p.setPen(QPen(QColor(border), bw) if border else Qt.PenStyle.NoPen)
    p.setBrush(color if isinstance(color, (QBrush, QLinearGradient, QRadialGradient)) else QColor(color))
    p.drawRoundedRect(QRectF(inset, inset, S - 2 * inset, S - 2 * inset), radius, radius)

def font(px, bold=True, family="Liberation Sans"):
    f = QFont(family); f.setPixelSize(px); f.setBold(bold); return f

logos = []

# 1. Fluent 3D on deep blue
img, p = canvas()
g = QLinearGradient(0, 0, 0, S); g.setColorAt(0, QColor("#2a3cb0")); g.setColorAt(1, QColor("#121a5c"))
rounded(p, QBrush(g))
turtle(p, "fluent_3d.png", (66, 66, 380, 380))
p.end(); logos.append((img, "Fluent 3D, granat"))

# 2. Noto on the original's cyan title-bar colour
img, p = canvas()
rounded(p, "#00e5ff", border="#008080", bw=14)
turtle(p, "noto_turtle.png", (70, 110, 372, 296))
p.end(); logos.append((img, "Noto, cyjan jak pasek oryginału"))

# 3. Fluent flat turtle drawing its path on squared paper
img, p = canvas()
rounded(p, "#ffffff")
clip = QPainterPath(); clip.addRoundedRect(QRectF(8, 8, S - 16, S - 16), 96, 96)
p.save(); p.setClipPath(clip)
p.setPen(QPen(QColor("#e3ebf7"), 2))
for k in range(40, S, 36):
    p.drawLine(k, 0, k, S); p.drawLine(0, k, S, k)
p.restore()
p.setPen(QPen(QColor("#c8d4e8"), 6)); p.setBrush(Qt.BrushStyle.NoBrush)
p.drawRoundedRect(QRectF(8, 8, S - 16, S - 16), 96, 96)
path = QPainterPath(QPointF(70, 440))
path.lineTo(70, 250); path.lineTo(200, 250); path.lineTo(200, 380); path.lineTo(310, 380)
p.setPen(QPen(QColor("#1a3cff"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
p.drawPath(path)
turtle(p, "fluent_flat.svg", (250, 250, 230, 230))
p.end(); logos.append((img, "Fluent flat, rysowana ścieżka"))

# 4. Fluent colour turtle in a canvas frame with blue rulers
img, p = canvas()
rounded(p, "#f0f0f0", radius=60)
p.setPen(QPen(QColor("#808080"), 4)); p.setBrush(QColor("white"))
p.drawRect(QRectF(92, 92, 380, 380))
p.setPen(QColor("#1a3cff")); p.setFont(font(30, False))
for i, t in enumerate(("0", "500", "999")):
    p.drawText(QRectF(92 + i * 190 - 50, 46, 100, 40), Qt.AlignmentFlag.AlignCenter, t)
    p.drawText(QRectF(10, 92 + i * 190 - 20, 76, 40), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, t)
turtle(p, "fluent_color.svg", (122, 122, 320, 320))
p.end(); logos.append((img, "Fluent color w canvasie z linijkami"))

# 5. Noto turtle on a Sierpinski triangle
img, p = canvas()
rounded(p, "#fffbe8", border="#222222", bw=8)
def sier(a, b, c, d):
    if d == 0:
        p.drawPolygon(QPolygonF([a, b, c])); return
    ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
    sier(a, ab, ca, d - 1); sier(ab, b, bc, d - 1); sier(ca, bc, c, d - 1)
p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#1b1b1b"))
sier(QPointF(256, 40), QPointF(470, 440), QPointF(42, 440), 5)
turtle(p, "noto_turtle.png", (140, 300, 232, 185))
p.end(); logos.append((img, "Noto + trójkąt Sierpińskiego"))

# 6. High contrast: white turtle on black with yellow ring
img, p = canvas()
rounded(p, "#000000", border="#ffff00", bw=16)
hc = QImage(400, 400, QImage.Format.Format_ARGB32); hc.fill(Qt.GlobalColor.transparent)
hp = QPainter(hc); QSvgRenderer(SRC + "fluent_hc.svg").render(hp, QRectF(0, 0, 400, 400)); hp.end()
for y in range(400):
    for x in range(400):
        a = QColor(hc.pixelColor(x, y)).alpha()
        if a: hc.setPixelColor(x, y, QColor(255, 255, 255, a))
p.drawImage(QRectF(56, 56, 400, 400), hc)
p.end(); logos.append((img, "Wysoki kontrast"))

# 7. Round badge with gradient and name
img, p = canvas()
g = QRadialGradient(256, 200, 300); g.setColorAt(0, QColor("#7ee787")); g.setColorAt(1, QColor("#0f6e3a"))
p.setPen(QPen(QColor("#0b4f2a"), 10)); p.setBrush(QBrush(g)); p.drawEllipse(QRectF(16, 16, 480, 480))
turtle(p, "fluent_3d.png", (106, 60, 300, 300))
p.setPen(QColor("white")); p.setFont(font(64))
p.drawText(QRectF(0, 350, S, 90), Qt.AlignmentFlag.AlignCenter, "TURTLE")
p.end(); logos.append((img, "Odznaka z napisem"))

# 8. Code braces { turtle }
img, p = canvas()
rounded(p, "#1e1f22", radius=80)
p.setPen(QColor("#ff7b72")); p.setFont(font(300, True, "Liberation Mono"))
p.drawText(QRectF(-10, 0, 200, S), Qt.AlignmentFlag.AlignCenter, "{")
p.drawText(QRectF(322, 0, 200, S), Qt.AlignmentFlag.AlignCenter, "}")
turtle(p, "fluent_flat.svg", (136, 146, 240, 240))
p.end(); logos.append((img, "Kod { 🐢 } na ciemnym tle"))

# 9. Spiral of the 50 named Turtle colours
img, p = canvas()
rounded(p, "#101018", radius=96)
x, y, ang = 256.0, 256.0, 0.0
for i in range(1, 140):
    nx, ny = x + i * 1.9 * math.sin(math.radians(ang)), y - i * 1.9 * math.cos(math.radians(ang))
    p.setPen(QPen(QColor.fromRgb(COLOURS[i % 50][1]), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if 30 < nx < 482 and 30 < ny < 482 and 30 < x < 482 and 30 < y < 482:
        p.drawLine(QPointF(x, y), QPointF(nx, ny))
    x, y, ang = nx, ny, ang + 91
turtle(p, "fluent_color.svg", (166, 166, 180, 180))
p.end(); logos.append((img, "Spirala z 50 kolorów Turtle"))

# 10. Retro pixel turtle (Noto, pixelated) with TS
img, p = canvas()
rounded(p, "#0f3b1f", radius=40, border="#7ccf4a", bw=10)
small = QImage(SRC + "noto_turtle.png").scaled(40, 32, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
p.drawImage(QRectF(76, 70, 360, 288), small)
p.setPen(QColor("#7ccf4a")); p.setFont(font(84, True, "Liberation Mono"))
p.drawText(QRectF(0, 360, S, 110), Qt.AlignmentFlag.AlignCenter, "> TS_")
p.end(); logos.append((img, "Retro piksel"))

sheet = QImage(5 * 300 + 20, 2 * 360 + 20, QImage.Format.Format_RGB32); sheet.fill(QColor("#d9dde3"))
sp = QPainter(sheet); sp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
sp.setRenderHint(QPainter.RenderHint.Antialiasing)
for n, (im, label) in enumerate(logos, 1):
    im.save(f"logo_{n:02d}.png")
    col, row = (n - 1) % 5, (n - 1) // 5
    x0, y0 = 20 + col * 300, 20 + row * 360
    sp.drawImage(QRectF(x0, y0, 260, 260), im)
    sp.setPen(QColor("#202020")); sp.setFont(font(19))
    sp.drawText(QRectF(x0 - 10, y0 + 266, 280, 30), Qt.AlignmentFlag.AlignCenter, f"{n}")
    sp.setFont(font(15, False))
    sp.drawText(QRectF(x0 - 15, y0 + 292, 290, 40), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, label)
sp.end(); sheet.save("logos_overview.png")
print("ok")
