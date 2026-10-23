# SPDX-License-Identifier: LGPL-2.1-or-later  (algorithms ported from Wine; see NOTICE)
"""Pixel-exact Windows GDI rasterisation for the Turtle canvas.

The original Turtle System draws with GDI (LineTo, Ellipse, Polygon, SetPixel, ExtFloodFill).
These routines port the algorithms of Wine's DIB engine (dlls/win32u/dibdrv and region.c),
which reproduces Windows' pixel output, so shapes here match the original pixel for pixel.

All drawing happens on a numpy uint32 array of 0xFFRRGGBB pixels (a QImage RGB32 view).
Pens are PS_SOLID with round end caps and joins, as created by Delphi's TPen.
"""

import math

# ---------------------------------------------------------------- regions

class Region:
    """A set of pixels stored as sorted, non-overlapping [x0, x1) spans per row."""

    __slots__ = ("rows",)

    def __init__(self):
        self.rows = {}

    @classmethod
    def rect(cls, left, top, right, bottom):
        r = cls()
        if left < right:
            for y in range(top, bottom):
                r.rows[y] = [(left, right)]
        return r

    def add_span(self, y, x0, x1):
        if x0 >= x1:
            return
        spans = self.rows.get(y)
        if not spans:
            self.rows[y] = [(x0, x1)]
            return
        a, b = spans[-1]
        if x0 > b:                      # common case: a new span to the right
            spans.append((x0, x1))
            return
        if len(spans) == 1 and x0 >= a:  # extends the only span
            spans[0] = (a, max(b, x1))
            return
        out = []
        placed = False
        for a, b in spans:
            if b < x0:
                out.append((a, b))
            elif a > x1:
                if not placed:
                    out.append((x0, x1))
                    placed = True
                out.append((a, b))
            else:
                x0, x1 = min(a, x0), max(b, x1)
        if not placed:
            out.append((x0, x1))
        self.rows[y] = out

    def union(self, other, dx=0, dy=0):
        for y, spans in other.rows.items():
            for a, b in spans:
                self.add_span(y + dy, a + dx, b + dx)

    def subtract(self, other):
        for y, cut in other.rows.items():
            spans = self.rows.get(y)
            if not spans:
                continue
            out = []
            for a, b in spans:
                pieces = [(a, b)]
                for c, d in cut:
                    nxt = []
                    for p, q in pieces:
                        if d <= p or c >= q:
                            nxt.append((p, q))
                        else:
                            if p < c:
                                nxt.append((p, c))
                            if d < q:
                                nxt.append((d, q))
                    pieces = nxt
                out.extend(pieces)
            if out:
                self.rows[y] = out
            else:
                del self.rows[y]

    def contains(self, x, y):
        return any(a <= x < b for a, b in self.rows.get(y, ()))

    def paint(self, pixels, colour):
        h, w = pixels.shape
        value = 0xFF000000 | (colour & 0xFFFFFF)
        for y, spans in self.rows.items():
            if 0 <= y < h:
                for a, b in spans:
                    a, b = max(a, 0), min(b, w)
                    if a < b:
                        pixels[y, a:b] = value


def round_rect_region(left, top, right, bottom, ellipse_width, ellipse_height):
    """NtGdiCreateRoundRectRgn."""
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top
    right -= 1
    bottom -= 1
    ellipse_width = min(right - left, abs(ellipse_width))
    ellipse_height = min(bottom - top, abs(ellipse_height))
    if ellipse_width < 2 or ellipse_height < 2:
        return Region.rect(left, top, right, bottom)

    lefts = [0] * (ellipse_height + 1)
    rights = [0] * (ellipse_height + 1)
    a = ellipse_width - 1
    b = ellipse_height - 1
    asq = 8 * a * a
    bsq = 8 * b * b
    dx = 4 * b * b * (1 - a)
    dy = 4 * a * a * (1 + (b % 2))
    err = dx + dy + a * a * (b % 2)
    x = 0
    y = ellipse_height // 2
    lefts[y], rights[y] = left, right
    while x <= ellipse_width // 2:
        e2 = 2 * err
        if e2 >= dx:
            x += 1
            dx += bsq
            err += dx
        if e2 <= dy:
            y += 1
            dy += asq
            err += dy
            if y <= ellipse_height:
                lefts[y], rights[y] = left + x, right - x
    region = Region()
    for i in range(ellipse_height // 2):
        region.add_span(top + i, lefts[b - i], rights[b - i])
    for i in range(ellipse_height // 2, ellipse_height):
        row_top = bottom - ellipse_height + i
        if i == ellipse_height // 2:
            # the middle band extends to the top of the rectangle
            for yy in range(top + ellipse_height // 2, row_top + 1):
                region.add_span(yy, lefts[i], rights[i])
        else:
            region.add_span(row_top, lefts[i], rights[i])
    return region


def elliptic_region(left, top, right, bottom):
    return round_rect_region(left, top, right, bottom, right - left, bottom - top)


# ---------------------------------------------------------------- polygon scan conversion

class _Edge:
    __slots__ = ("ymax", "minor", "d", "m", "m1", "incr1", "incr2", "clockwise")


def _cdiv(a, b):
    """C integer division (truncates towards zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _bres_init(e, dy, x1, x2):
    e.minor = x1
    dx = x2 - x1
    if dx < 0:
        e.m = _cdiv(dx, dy)
        e.m1 = e.m - 1
        e.incr1 = -2 * dx + 2 * dy * e.m1
        e.incr2 = -2 * dx + 2 * dy * e.m
        e.d = 2 * e.m * dy - 2 * dx - 2 * dy
    else:
        e.m = _cdiv(dx, dy)
        e.m1 = e.m + 1
        e.incr1 = 2 * dx - 2 * dy * e.m1
        e.incr2 = 2 * dx - 2 * dy * e.m
        e.d = -2 * e.m * dy + 2 * dx


def _bres_incr(e):
    if e.m1 > 0:
        if e.d > 0:
            e.minor += e.m1
            e.d += e.incr1
        else:
            e.minor += e.m
            e.d += e.incr2
    else:
        if e.d >= 0:
            e.minor += e.m1
            e.d += e.incr1
        else:
            e.minor += e.m
            e.d += e.incr2


def _insert_sorted(lst, edge):
    for i, other in enumerate(lst):
        if other.minor >= edge.minor:
            lst.insert(i, edge)
            return
    lst.append(edge)


def polygon_region(points):
    """create_polypolygon_region for one polygon, ALTERNATE fill mode."""
    n = len(points)
    if (n == 4 or (n == 5 and points[4] == points[0])) and (
            (points[0][1] == points[1][1] and points[1][0] == points[2][0]
             and points[2][1] == points[3][1] and points[3][0] == points[0][0]) or
            (points[0][0] == points[1][0] and points[1][1] == points[2][1]
             and points[2][0] == points[3][0] and points[3][1] == points[0][1])):
        return Region.rect(min(points[0][0], points[2][0]), min(points[0][1], points[2][1]),
                           max(points[0][0], points[2][0]), max(points[0][1], points[2][1]))
    table = {}
    ymin, ymax = None, None
    prev = points[-1]
    for cur in points:
        if prev[1] > cur[1]:
            bottom, top, clockwise = prev, cur, False
        else:
            bottom, top, clockwise = cur, prev, True
        prev = cur
        if bottom[1] == top[1]:
            continue
        e = _Edge()
        e.clockwise = clockwise
        e.ymax = bottom[1] - 1
        _bres_init(e, bottom[1] - top[1], top[0], bottom[0])
        _insert_sorted(table.setdefault(top[1], []), e)
        ymin = top[1] if ymin is None else min(ymin, top[1])
        ymax = bottom[1] if ymax is None else max(ymax, bottom[1])
    region = Region()
    if ymin is None:
        return region
    active = []
    for y in range(ymin, ymax):
        for e in table.get(y, ()):
            _insert_sorted(active, e)
        first = True
        left = 0
        for e in active:
            if first:
                left = e.minor
            elif left != e.minor:
                region.add_span(y, left, e.minor)
            first = not first
        # next scanline: drop finished edges, step the others, re-sort
        kept = []
        for e in active:
            if e.ymax != y:
                _bres_incr(e)
                kept.append(e)
        active = kept
        i = 0
        while i < len(active):
            e = active[i]
            j = 0
            while j < i and active[j].minor <= e.minor:
                j += 1
            if j < i:
                active.pop(i)
                active.insert(j, e)
            i += 1
    return region


# ---------------------------------------------------------------- pens

def _round(f):
    return int(f + 0.5) if f > 0 else int(f - 0.5)


class Pen:
    def __init__(self, width, colour):
        self.width = max(1, int(width))
        self.colour = colour & 0xFFFFFF
        self._cap = None

    @property
    def wide(self):
        return self.width > 1

    def round_cap(self):
        if self._cap is None:
            w = self.width
            self._cap = elliptic_region(-(w // 2), -(w // 2), (w + 1) // 2 + 1, (w + 1) // 2 + 1)
        return self._cap


def cosmetic_line(pixels, x1, y1, x2, y2, colour):
    """solid_pen_line: one-pixel line from (x1,y1), excluding (x2,y2)."""
    h, w = pixels.shape
    value = 0xFF000000 | (colour & 0xFFFFFF)
    if y1 == y2:
        a, b = x1, x2
        if a > b:
            a, b = b + 1, a + 1
        if 0 <= y1 < h:
            a, b = max(a, 0), min(b, w)
            if a < b:
                pixels[y1, a:b] = value
        return
    if x1 == x2:
        a, b = y1, y2
        if a > b:
            a, b = b + 1, a + 1
        if 0 <= x1 < w:
            a, b = max(a, 0), min(b, h)
            if a < b:
                pixels[a:b, x1] = value
        return
    dx, dy = x2 - x1, y2 - y1
    adx, ady = abs(dx), abs(dy)
    if dy > 0:
        octant = (1 if dx > dy else 2) if dx > 0 else (4 if -dx > dy else 3)
    else:
        octant = (5 if -dx > -dy else 6) if dx < 0 else (8 if dx > -dy else 7)
    mask = 1 << (octant - 1)
    bias = 1 if mask & 0xB4 else 0
    x_inc = 1 if mask & 0xC3 else -1
    y_inc = 1 if mask & 0x0F else -1
    x, y = x1, y1
    if mask & 0x99:
        add1, add2 = 2 * ady - 2 * adx, 2 * ady
        err, length = 2 * ady - adx, adx
        for _ in range(length):
            if 0 <= x < w and 0 <= y < h:
                pixels[y, x] = value
            if err + bias > 0:
                y += y_inc
                err += add1
            else:
                err += add2
            x += x_inc
    else:
        add1, add2 = 2 * adx - 2 * ady, 2 * adx
        err, length = 2 * adx - ady, ady
        for _ in range(length):
            if 0 <= x < w and 0 <= y < h:
                pixels[y, x] = value
            if err + bias > 0:
                x += x_inc
                err += add1
            else:
                err += add2
            y += y_inc


def _wide_segment(pen, total, p1, p2, dx, dy):
    """wide_line_segment without square caps; returns False for an empty segment."""
    w = pen.width
    if dx == 0 and dy == 0:
        return False
    if dy == 0:
        top = p1[1] - w // 2
        left, right = min(p1[0], p2[0]), max(p1[0], p2[0])
        for y in range(top, top + w):
            total.add_span(y, left, right)
    elif dx == 0:
        left = p1[0] - w // 2
        for y in range(min(p1[1], p2[1]), max(p1[1], p2[1])):
            total.add_span(y, left, left + w)
    else:
        # the segment's shape depends only on (dx, dy, width): cache it relative to p1
        key = (dx, dy, w)
        shape = _SEGMENT_CACHE.get(key)
        if shape is None:
            shape = _slanted_segment(w, dx, dy)
            if len(_SEGMENT_CACHE) > 20000:
                _SEGMENT_CACHE.clear()
            _SEGMENT_CACHE[key] = shape
        total.union(shape, p1[0], p1[1])
    return True


_SEGMENT_CACHE = {}


def _slanted_segment(w, dx, dy):
    """Region of a slanted wide-pen segment from (0, 0) to (dx, dy)."""
    length = math.hypot(dx, dy)
    width_x = w * abs(dy) / length
    width_y = w * abs(dx) / length
    nx, ny = _round(width_x / 2), _round(width_y / 2)
    wx, wy = _round((width_x + 1) / 2), _round((width_y + 1) / 2)
    if dx < 0:
        wy, ny = -wy, -ny
    if dy < 0:
        nx, ny, wx, wy = -wx, wy, -nx, ny
    pts = [(-nx, ny), (wx, -wy), (dx + wx, dy - wy), (dx - nx, dy + ny)]
    return polygon_region(pts)


def wide_lines(pen, points, close):
    """wide_pen_lines: the region covered by a wide pen along the points."""
    pts = list(points)
    while len(pts) > 2 and pts[0] == pts[1]:
        pts.pop(0)
    while len(pts) > 2 and pts[-1] == pts[-2]:
        pts.pop()
    total = Region()
    cap = pen.round_cap()
    n = len(pts)
    if close:
        count, first, last = n, pts[0], pts[0]
    else:
        count, first, last = n - 1, pts[0], pts[-1]
        total.union(cap, *first)
        total.union(cap, *last)
    if count == 1:
        p1, p2 = pts[0], pts[1 % n]
        _wide_segment(pen, total, first, last, p2[0] - p1[0], p2[1] - p1[1])
        return total
    p1, p2 = pts[0], pts[1 % n]
    _wide_segment(pen, total, first, p2, p2[0] - p1[0], p2[1] - p1[1])
    for i in range(1, count - 1):
        p1, p2 = pts[i % n], pts[(i + 1) % n]
        if _wide_segment(pen, total, p1, p2, p2[0] - p1[0], p2[1] - p1[1]):
            total.union(cap, *p1)
    p1, p2 = pts[(count - 1) % n], pts[count % n]
    _wide_segment(pen, total, p1, last, p2[0] - p1[0], p2[1] - p1[1])
    total.union(cap, *p1)
    if close:
        total.union(cap, *last)
    return total


def pen_lines(pixels, pen, points, close):
    if pen.wide:
        wide_lines(pen, points, close).paint(pixels, pen.colour)
    else:
        for a, b in zip(points, points[1:]):
            cosmetic_line(pixels, a[0], a[1], b[0], b[1], pen.colour)
        if close:
            cosmetic_line(pixels, points[-1][0], points[-1][1], points[0][0], points[0][1],
                          pen.colour)


# ---------------------------------------------------------------- GDI calls

def line_to(pixels, pen, x1, y1, x2, y2):
    pen_lines(pixels, pen, [(x1, y1), (x2, y2)], False)


def polyline(pixels, pen, points):
    if len(points) >= 2:
        pen_lines(pixels, pen, points, False)


def _ellipse_first_quadrant(width, height):
    a, b = width - 1, height - 1
    asq, bsq = 8 * a * a, 8 * b * b
    dx = 4 * b * b * (1 - a)
    dy = 4 * a * a * (1 + (b % 2))
    err = dx + dy + a * a * (b % 2)
    x, y = a, height // 2
    out = []
    while x >= width // 2:
        e2 = 2 * err
        out.append((x, y))
        if e2 >= dx:
            x -= 1
            dx += bsq
            err += dx
        if e2 <= dy:
            y += 1
            dy += asq
            err += dy
    return out


def ellipse(pixels, pen, brush, left, top, right, bottom):
    """Ellipse() via dibdrv_RoundRect; pen or brush may be None (null pen / hollow brush)."""
    left, right = min(left, right), max(left, right)
    top, bottom = min(top, bottom), max(top, bottom)
    if left == right or top == bottom:
        return
    ew, eh = right - left, bottom - top
    if ew <= 2 or eh <= 2:
        rectangle(pixels, pen, brush, left, top, right, bottom)
        return
    quad = _ellipse_first_quadrant(ew, eh)
    pts = [(right - ew + x, top + eh - 1 - y) for x, y in quad]
    count = len(pts)
    end = 2 * count - 1
    if ew % 2 and ew == right - left:
        end -= 1
    full = pts + [None] * (end + 1 - count)
    for i in range(count):
        full[end - i] = (left + right - 1 - pts[i][0], pts[i][1])
    pts = full[:end + 1]
    count = len(pts)
    end = 2 * count - 1
    if eh % 2 and eh == bottom - top:
        end -= 1
    full = pts + [None] * (end + 1 - count)
    for i in range(count):
        full[end - i] = (pts[i][0], top + bottom - 1 - pts[i][1])
    pts = full[:end + 1]
    interior = round_rect_region(left, top, right + 1, bottom + 1, ew, eh) if brush is not None \
        else None
    if pen is not None and pen.wide:
        outline = wide_lines(pen, pts, True)
        if interior is not None:
            interior.subtract(outline)
            interior.paint(pixels, brush)
        outline.paint(pixels, pen.colour)
    else:
        if interior is not None:
            interior.paint(pixels, brush)
        if pen is not None:
            pen_lines(pixels, pen, pts, True)


def rectangle(pixels, pen, brush, left, top, right, bottom):
    """Rectangle() (used by Ellipse for degenerate sizes)."""
    left, right = min(left, right), max(left, right)
    top, bottom = min(top, bottom), max(top, bottom)
    if left == right or top == bottom:
        return
    r, b = right - 1, bottom - 1
    pts = [(r, top), (left, top), (left, b), (r, b)]
    if pen is not None and pen.wide:
        outline = wide_lines(pen, pts, True)
        if brush is not None:
            interior = Region.rect(left, top, r, b)
            interior.subtract(outline)
            interior.paint(pixels, brush)
        outline.paint(pixels, pen.colour)
    else:
        if brush is not None:
            pw = pen.width if pen is not None else 0
            Region.rect(left + (pw + 1) // 2, top + (pw + 1) // 2,
                        r - pw // 2, b - pw // 2).paint(pixels, brush)
        if pen is not None:
            pen_lines(pixels, pen, pts, True)


def polygon(pixels, pen, brush, points):
    """Polygon() via dibdrv_PolyPolygon, ALTERNATE fill mode."""
    if len(points) < 2:
        return
    interior = polygon_region(points) if brush is not None else None
    if pen is not None and pen.wide:
        outline = wide_lines(pen, points, True)
        if interior is not None:
            interior.subtract(outline)
            interior.paint(pixels, brush)
        outline.paint(pixels, pen.colour)
    else:
        if interior is not None:
            interior.paint(pixels, brush)
        if pen is not None:
            pen_lines(pixels, pen, points, True)


def flood_fill(pixels, x, y, colour, border=None, surface=None):
    """ExtFloodFill: FLOODFILLBORDER when `border` is given, else FLOODFILLSURFACE.

    `border` / `surface` are full 32-bit pixel values: like GDI, the comparison includes the
    alpha byte, so never-painted background pixels only ever match themselves."""
    import numpy as np
    h, w = pixels.shape
    if not (0 <= x < w and 0 <= y < h):
        return
    mask = pixels != border if border is not None else pixels == surface
    if not mask[y, x]:
        return
    filled = np.zeros_like(mask)
    stack = [(x, y)]
    while stack:
        x, y = stack.pop()
        if not mask[y, x] or filled[y, x]:
            continue
        row = mask[y]
        gaps = np.flatnonzero(~row[:x])
        left = gaps[-1] + 1 if gaps.size else 0
        gaps = np.flatnonzero(~row[x:])
        right = x + gaps[0] if gaps.size else w
        filled[y, left:right] = True
        for ny in (y - 1, y + 1):
            if 0 <= ny < h:
                seg = mask[ny, left:right] & ~filled[ny, left:right]
                if seg.any():
                    starts = np.flatnonzero(seg & ~np.concatenate(([False], seg[:-1])))
                    stack.extend((left + int(s), ny) for s in starts)
    pixels[filled] = 0xFF000000 | (colour & 0xFFFFFF)
