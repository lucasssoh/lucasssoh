#!/usr/bin/env python3
"""Draw the profile banner: a pixel-art room under a wide window, with three numbers in the sky.

    python3 banner/generate.py --login lucasssoh    # real numbers, needs GITHUB_TOKEN
    python3 banner/generate.py --sample             # made-up numbers, works offline

Writes banner/day.svg (light theme) and banner/night.svg (dark theme), then draws one card per
entry of projects.json into projects/ and rewrites the README between the projects markers.
Standard library only.
"""

import argparse
import base64
import datetime as dt
import json
import os
import pathlib
import urllib.request

W, H = 1000, 260
HERE = pathlib.Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    year: contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    month: contributionsCollection(from: $from, to: $to) {
      commitContributionsByRepository(maxRepositories: 100) { repository { nameWithOwner } }
      issueContributionsByRepository(maxRepositories: 100) { repository { nameWithOwner } }
      pullRequestContributionsByRepository(maxRepositories: 100) { repository { nameWithOwner } }
      pullRequestReviewContributionsByRepository(maxRepositories: 100) { repository { nameWithOwner } }
    }
  }
}
"""


def fetch(login, token):
    now = dt.datetime.now(dt.timezone.utc)
    body = json.dumps({
        "query": QUERY,
        "variables": {
            "login": login,
            "from": (now - dt.timedelta(days=30)).isoformat(),
            "to": now.isoformat(),
        },
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errors"):
        raise SystemExit(f"GitHub API: {data['errors']}")
    user = data["data"]["user"]

    cal = user["year"]["contributionCalendar"]
    days = [d["contributionCount"] for w in cal["weeks"] for d in w["contributionDays"]]
    # A day without contributions yet does not break the streak until it is over.
    i = len(days) - 1
    if i >= 0 and days[i] == 0:
        i -= 1
    streak = 0
    while i >= 0 and days[i] > 0:
        streak += 1
        i -= 1

    repos = set()
    for kind in ("commit", "issue", "pullRequest", "pullRequestReview"):
        for entry in user["month"][f"{kind}ContributionsByRepository"]:
            repos.add(entry["repository"]["nameWithOwner"])

    return cal["totalContributions"], streak, len(repos)


# ---------------------------------------------------------------------------
# Raster: layers are painted pixel by pixel, then emitted as one path per colour
# ---------------------------------------------------------------------------

def hex_rgb(c):
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def rgb_hex(rgb):
    return "#%02x%02x%02x" % rgb


class Layer:
    def __init__(self, below=None, width=W, height=H):
        self.px = {}            # (x, y) -> "#rrggbb"
        self.below = below      # layers underneath, used to blend translucent paint
        self.width = width
        self.height = height

    def colour_at(self, x, y):
        if (x, y) in self.px:
            return self.px[(x, y)]
        for layer in reversed(self.below or []):
            if (x, y) in layer.px:
                return layer.px[(x, y)]
        return None

    def rect(self, x, y, w, h, colour, alpha=1.0):
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                if alpha >= 1:
                    self.px[(xx, yy)] = colour
                else:
                    under = self.colour_at(xx, yy)
                    if under is None:
                        continue
                    a, b = hex_rgb(colour), hex_rgb(under)
                    self.px[(xx, yy)] = rgb_hex(tuple(round(b[i] + (a[i] - b[i]) * alpha) for i in range(3)))

    def disc(self, cx, cy, r, cell, colour, alpha=1.0):
        for x in range(cx - r, cx + r, cell):
            for y in range(cy - r, cy + r, cell):
                mx, my = x + cell / 2, y + cell / 2
                if (mx - cx) ** 2 + (my - cy) ** 2 <= r * r:
                    self.rect(x, y, cell, cell, colour, alpha)

    def svg(self):
        """Merge horizontal runs, then stack identical runs vertically; one <path> per colour."""
        rows = {}
        for (x, y), c in self.px.items():
            rows.setdefault(y, {})[x] = c
        runs_by_row = {}
        for y, cells in rows.items():
            xs = sorted(cells)
            runs, start, prev, colour = [], None, None, None
            for x in xs:
                c = cells[x]
                if start is not None and x == prev + 1 and c == colour:
                    prev = x
                    continue
                if start is not None:
                    runs.append((start, prev + 1, colour))
                start, prev, colour = x, x, c
            if start is not None:
                runs.append((start, prev + 1, colour))
            runs_by_row[y] = runs
        open_rects, done = {}, []
        for y in range(self.height + 1):
            current = {(a, b, c) for a, b, c in runs_by_row.get(y, [])}
            for key in list(open_rects):
                if key not in current:
                    done.append((key, open_rects.pop(key), y))
            for key in current:
                if key not in open_rects:
                    open_rects[key] = y
        paths = {}
        for (x0, x1, c), y0, y1 in done:
            paths.setdefault(c, []).append(f"M{x0} {y0}h{x1 - x0}v{y1 - y0}h{x0 - x1}z")
        return "".join(f'<path fill="{c}" d="{"".join(sorted(d))}"/>' for c, d in sorted(paths.items()))


# ---------------------------------------------------------------------------
# Pixel type
# ---------------------------------------------------------------------------

DIG = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
}
LET = {
    "A": ["010", "101", "111", "101", "101"], "B": ["110", "101", "110", "101", "110"],
    "C": ["011", "100", "100", "100", "011"], "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "110", "100", "111"], "H": ["101", "101", "111", "101", "101"],
    "I": ["111", "010", "010", "010", "111"], "M": ["101", "111", "111", "101", "101"],
    "N": ["110", "101", "101", "101", "101"], "O": ["010", "101", "101", "101", "010"],
    "P": ["110", "101", "110", "100", "100"], "R": ["110", "101", "110", "101", "101"],
    "S": ["011", "100", "010", "001", "110"], "T": ["111", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "111"], "W": ["101", "101", "111", "111", "101"],
    "Y": ["101", "101", "010", "010", "010"],
}


def text(layer, s, cx, y, px, big, colour):
    font, cw, sp = (DIG, 5, 3) if big else (LET, 3, 2)
    s = str(s).upper()
    width = sum(sp if ch == " " else cw + 1 for ch in s) - 1
    x = cx - round(width * px / 2)
    for ch in s:
        if ch == " ":
            x += sp * px
            continue
        for r, row in enumerate(font.get(ch, [])):
            for k, bit in enumerate(row):
                if bit == "1":
                    layer.rect(x + k * px, y + r * px, px, px, colour)
        x += (cw + 1) * px


def z_glyph(layer, x, y, px, colour):
    for r, row in enumerate(["1111", "0010", "0100", "1111"]):
        for k, bit in enumerate(row):
            if bit == "1":
                layer.rect(x + k * px, y + r * px, px, px, colour)


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

SKIN, SKIN_N = "#dcae88", "#8e6a4e"
HAIR = "#121214"
SWEAT, SWEAT_HI, SWEAT_N, SWEAT_NHI = "#8cc0e4", "#6fa6d0", "#3b5870", "#4c6b86"
JEANS, JEANS_N, SHOE, SHOE_N = "#3a4a68", "#1f2840", "#1a1a1c", "#0c0c0e"
MUG, MUG_RIM = "#fbfaf6", "#d9d5cb"

DAY = dict(
    night=False, bands=["#a6cdec", "#b6d6f0", "#c8e1f4"], bld=["#c9d3dc", "#b9c5cf", "#d3dbe2"], win="#9eb0c1",
    cloud="#ffffff", cloud_shade="#dce8f2", wall="#efe8da", wall_dark="#e4dccb", frame="#d9cfbb", frame_dark="#bfb39b",
    curtain="#d8c9aa", curtain_dark="#c4b28d", ink="#1d3047", dim="#3f5873", desk="#b98b5c", desk_dark="#8f6942",
    mon="#2a2a2c", screen="#1f2633", code=["#8c9ab0", "#c3a46e", "#7fa36a", "#c98a7a"], key="#d9d5cb", key_dark="#bdb8ab",
    books=["#7b8fa3", "#c49a6c", "#6f8a5e", "#a9746a", "#4f5d70"], note="#f3e4a2", lamp="#2a2a2c", bulb="#ece6d2",
    mug="#fbfaf6", mug_dark="#d9d5cb", pouf="#c7765a", pouf_dark="#a85f46", floor="#c9a27a", floor_dark="#ad8560",
    base="#d9cfbb", chair="#3a3a3e", cursor="#e9eef7", steam="#ffffff",
)
NIGHT = dict(
    night=True, bands=["#070b14", "#0b1120", "#10182b"], bld=["#121a29", "#0e1522", "#162033"], win="#e9c46a",
    moon="#efe9d6", star="#e8e4d6", wall="#1c1e26", wall_dark="#16181f", frame="#2c2f39", frame_dark="#22252d",
    curtain="#2e2b2c", curtain_dark="#262324", ink="#eeece4", dim="#a9b0bd", desk="#4b3627", desk_dark="#36271c",
    mon="#0d0e11", screen="#0b0e16", glow="#f2c879", key="#34363e", key_dark="#26282e",
    books=["#3c4654", "#5e4c3a", "#3d4a37", "#57403c", "#2c3440"], note="#8a7f55", lamp="#0d0e11", bulb="#f6d48a",
    mug="#8f8b82", mug_dark="#6c6961", pouf="#5a3a30", pouf_dark="#46302a", floor="#33271e", floor_dark="#251c16",
    base="#2c2f39", chair="#202024", led="#7fb07a",
)

CLOUD = [(60, 64, 30), (108, 44, 42), (160, 58, 34), (198, 74, 22), (28, 78, 20)]
STARS = [(70, 26), (150, 52), (262, 22), (330, 58), (420, 36), (560, 24), (600, 60), (700, 30), (760, 56),
         (840, 20), (930, 86), (120, 96), (480, 104), (820, 100)]


def in_cloud(x, y):
    if y > 96:
        return False
    return any((x - cx) ** 2 + (y - cy) ** 2 <= r * r for cx, cy, r in CLOUD)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

def scene(t, numbers):
    total, streak, repos = numbers
    sky = Layer()
    sky.rect(40, 12, 920, 58, t["bands"][0])
    sky.rect(40, 70, 920, 58, t["bands"][1])
    sky.rect(40, 128, 920, 58, t["bands"][2])
    for x in range(40, 960, 16):
        sky.rect(x, 62, 8, 8, t["bands"][1])
        sky.rect(x + 8, 120, 8, 8, t["bands"][2])
    if t["night"]:
        sky.disc(900, 44, 16, 4, t["moon"])
        sky.disc(907, 39, 14, 4, t["bands"][0])
    bx, k = 40, 0
    while bx < 960:
        w, h = 26 + (k * 13) % 30, 18 + (k * 29) % 40
        ww, top = min(w, 960 - bx), 186 - h
        sky.rect(bx, top, ww, h, t["bld"][k % 3])
        if k % 4 == 1:
            sky.rect(bx + ww // 2 - 1, top - 10, 2, 10, t["bld"][1])
        for wy in range(top + 6, 180, 9):
            for wx in range(bx + 4, bx + ww - 4, 7):
                if (wx * 7 + wy * 3 + k) % 5 < (2 if t["night"] else 3):
                    sky.rect(wx, wy, 3, 4, t["win"])
        bx += w
        k += 1

    cloud = Layer()
    if not t["night"]:
        for gx in range(38):
            for gy in range(17):
                x, y = gx * 6 + 3, gy * 6 + 3
                if in_cloud(x, y):
                    lit = in_cloud(x - 6, y - 6) and in_cloud(x, y + 6)
                    cloud.rect(gx * 6, 14 + gy * 6, 6, 6, t["cloud"] if lit else t["cloud_shade"])

    room = Layer(below=[sky])
    room.rect(0, 0, 1000, 12, t["wall"])
    room.rect(0, 0, 40, 260, t["wall"])
    room.rect(960, 0, 40, 260, t["wall"])
    room.rect(0, 186, 1000, 74, t["wall_dark"])
    room.rect(32, 6, 936, 6, t["frame"])
    room.rect(32, 12, 8, 174, t["frame"])
    room.rect(960, 12, 8, 174, t["frame"])
    room.rect(40, 12, 920, 2, t["frame_dark"])
    room.rect(352, 12, 8, 174, t["frame"])
    room.rect(640, 12, 8, 174, t["frame"])
    room.rect(360, 12, 2, 174, t["frame_dark"])
    room.rect(648, 12, 2, 174, t["frame_dark"])
    room.rect(28, 186, 944, 4, t["frame_dark"])
    for i in range(3):
        c = t["curtain_dark"] if i % 2 else t["curtain"]
        room.rect(8 + i * 8, 2, 8, 192, c)
        room.rect(968 + i * 8, 2, 8, 192, c)
    room.rect(4, 0, 992, 3, t["frame_dark"])
    # parquet
    room.rect(0, 224, 1000, 4, t["base"])
    room.rect(0, 228, 1000, 32, t["floor"])
    for row, fy in enumerate(range(228, 260, 8)):
        room.rect(0, fy + 7, 1000, 1, t["floor_dark"])
        for fx in range((row % 2) * 60, 1000, 120):
            room.rect(fx, fy, 1, 7, t["floor_dark"])
    # the three numbers, one per pane
    for value, label, cx in ((f"{total:,}".replace(",", " "), "contributions this year", 196),
                             (str(streak), "days in a row", 500),
                             (str(repos), "repositories this month", 804)):
        text(room, value, cx, 24, 7, True, t["ink"])
        text(room, label, cx, 84, 3, False, t["dim"])
    # lamp halo at night
    if t["night"]:
        for r, a in ((64, 0.04), (44, 0.06), (28, 0.09), (16, 0.13)):
            room.disc(438, 128, r, 4, t["glow"], a)
        room.rect(400, 194, 84, 2, t["glow"], 0.3)
    # pouf, on the left
    room.rect(104, 214, 104, 36, t["pouf"])
    room.rect(112, 208, 88, 6, t["pouf"])
    room.rect(124, 204, 64, 4, t["pouf"])
    room.rect(104, 244, 104, 6, t["pouf_dark"])
    room.rect(150, 210, 4, 30, t["pouf_dark"])
    room.rect(112, 250, 88, 3, t["pouf_dark"])
    # desk, lamp, books, screen
    room.rect(380, 196, 380, 8, t["desk"])
    room.rect(380, 204, 380, 5, t["desk_dark"])
    room.rect(392, 209, 10, 51, t["desk_dark"])
    room.rect(738, 209, 10, 51, t["desk_dark"])
    room.rect(392, 190, 40, 6, t["lamp"])
    room.rect(410, 128, 4, 62, t["lamp"])
    room.rect(414, 122, 10, 6, t["lamp"])
    room.rect(420, 114, 38, 10, t["lamp"])
    room.rect(428, 124, 20, 4, t["bulb"])
    room.rect(440, 186, 34, 10, t["books"][4])
    room.rect(444, 180, 28, 6, t["books"][1])
    room.rect(448, 176, 18, 4, t["note"])
    room.rect(480, 114, 160, 72, t["mon"])
    room.rect(486, 120, 148, 60, t["screen"])
    room.rect(552, 186, 16, 6, t["mon"])
    room.rect(536, 192, 48, 4, t["mon"])
    if not t["night"]:
        ly = 126
        for indent, length, colour in ((0, 56, 0), (1, 40, 1), (1, 64, 2), (2, 28, 3), (1, 52, 0), (0, 22, 1), (1, 44, 2)):
            room.rect(494 + indent * 10, ly, length, 3, t["code"][colour])
            ly += 7
    room.rect(500, 196, 120, 4, t["key"])
    for x in range(504, 616, 8):
        room.rect(x, 196, 5, 2, t["key_dark"])
    room.rect(628, 196, 10, 4, t["key"])
    if t["night"]:
        room.rect(680, 182, 16, 14, t["mug"])
        room.rect(696, 186, 4, 6, t["mug"])
        room.rect(682, 184, 12, 2, t["mug_dark"])
    # the empty chair: he is never at the computer
    room.rect(534, 206, 52, 6, t["chair"])
    room.rect(534, 170, 52, 36, t["chair"])
    room.rect(556, 212, 8, 40, t["chair"])
    room.rect(536, 252, 48, 4, t["chair"])

    return sky, cloud, room


def standing_body():
    """By day: standing at the window, back to the viewer. Everything but the coffee arm."""
    p = Layer()
    p.rect(830, 236, 8, 10, JEANS)
    p.rect(842, 236, 8, 10, JEANS)
    p.rect(828, 246, 10, 4, SHOE)
    p.rect(842, 246, 10, 4, SHOE)
    p.rect(828, 204, 24, 2, SWEAT)
    p.rect(826, 206, 28, 30, SWEAT)
    # hood down on the back
    p.rect(826, 202, 28, 4, SWEAT_HI)
    p.rect(828, 206, 24, 4, SWEAT_HI)
    p.rect(832, 210, 16, 4, SWEAT_HI)
    p.rect(836, 214, 8, 4, SWEAT_HI)
    p.rect(838, 218, 4, 2, SWEAT_HI)
    p.rect(831, 204, 18, 2, SWEAT)
    p.rect(839, 222, 2, 10, SWEAT_HI)
    p.rect(826, 232, 28, 4, SWEAT_HI)
    p.rect(820, 208, 6, 20, SWEAT)
    p.rect(820, 228, 6, 4, SKIN)
    # hair: long, wide and high
    p.rect(834, 148, 12, 4, HAIR)
    p.rect(826, 152, 28, 4, HAIR)
    p.rect(820, 156, 40, 4, HAIR)
    p.rect(816, 160, 48, 4, HAIR)
    p.rect(812, 164, 56, 36, HAIR)
    p.rect(814, 200, 52, 6, HAIR)
    p.rect(818, 206, 44, 4, HAIR)
    return p


def lerp(a, b, u):
    return a + (b - a) * u


def arm_frame(u):
    """The coffee arm, u = 0 holding the mug at the chest, u = 1 drinking."""
    p = Layer()
    sx, sy = lerp(857, 858, u), lerp(210, 208, u)
    ex, ey = lerp(857, 863, u), lerp(216, 201, u)
    hx, hy = lerp(861, 871, u), lerp(219, 191, u)

    def limb(ax, ay, bx, by):
        n = int(max(abs(bx - ax), abs(by - ay))) + 1
        for i in range(n + 1):
            x, y = lerp(ax, bx, i / n), lerp(ay, by, i / n)
            p.rect(round(x) - 3, round(y) - 3, 6, 6, SWEAT)

    limb(sx, sy, ex, ey)
    limb(ex, ey, hx - 2, hy - 2)
    p.rect(round(hx) - 3, round(hy) - 3, 6, 6, SKIN)
    mx, my = round(hx + 1), round(hy + lerp(-9, -13, u))
    p.rect(mx, my, 10, 12, MUG)
    p.rect(mx + 10, my + 3, 2, 6, MUG)
    p.rect(mx + 1, my + 2, 8, 2, MUG_RIM)
    return p


def sleeper():
    """By night: curled up on the pouf like a cat, hood on."""
    p = Layer()
    p.rect(144, 182, 38, 4, SWEAT_N)
    p.rect(140, 186, 46, 18, SWEAT_N)
    p.rect(142, 204, 42, 4, SWEAT_N)
    p.rect(150, 190, 26, 3, SWEAT_NHI)
    p.rect(172, 198, 14, 8, JEANS_N)
    p.rect(182, 200, 10, 6, SHOE_N)
    p.rect(116, 172, 26, 4, SWEAT_N)
    p.rect(112, 176, 34, 28, SWEAT_N)
    p.rect(116, 204, 26, 4, SWEAT_N)
    p.rect(114, 178, 4, 22, SWEAT_NHI)
    p.rect(118, 174, 20, 2, SWEAT_NHI)
    p.rect(128, 194, 14, 4, HAIR)
    p.rect(130, 198, 10, 6, SKIN_N)
    p.rect(126, 204, 2, 4, SWEAT_NHI)
    return p


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

def visibility_keyframes(name, windows):
    """Opacity 1 inside each (start, end) window of the cycle, in percent; 0 elsewhere."""
    points = {0: 0, 100: 0}
    for a, b in windows:
        points[a] = 1
        points.setdefault(b, 0)
        if b < 100:
            points[b] = 0
    body = "".join(f"{p}%{{opacity:{points[p]}}}" for p in sorted(points))
    return f"@keyframes {name}{{{body}}}"


def ease(u):
    return u * u * (3 - 2 * u)


def build(t, numbers):
    sky, cloud, room = scene(t, numbers)
    css = [
        "@keyframes drift{from{transform:translateX(-240px)}to{transform:translateX(1000px)}}",
        ".drift{animation:drift 150s steps(1240) infinite;animation-delay:-50s}",
        "@keyframes tw{0%,100%{opacity:1}50%{opacity:.25}}",
        ".tw{animation:tw 3.2s steps(8) infinite}",
        "@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}",
        ".blink{animation:blink 1.2s steps(1,end) infinite}",
        ".led{animation:blink 2.4s steps(1,end) infinite}",
    ]
    parts = [sky.svg()]
    if t["night"]:
        for n, (x, y) in enumerate(STARS):
            parts.append(f'<rect class="tw" style="animation-delay:{-n * 0.45:.2f}s" x="{x}" y="{y}" '
                         f'width="3" height="3" fill="{t["star"]}"/>')
    else:
        parts.append(f'<g class="drift">{cloud.svg()}</g>')
    parts.append(room.svg())

    if not t["night"]:
        parts.append(f'<rect class="blink" x="520" y="161" width="6" height="3" fill="{t["cursor"]}"/>')
        parts.append(standing_body().svg())
        # coffee: hold, raise the mug in 8 frames, drink, lower it, hold again (cycle of 8 s)
        css.append("@keyframes steam{0%{transform:translateY(0);opacity:.8}100%{transform:translateY(-14px);opacity:0}}")
        css.append(".steam{animation:steam 2.8s steps(14) infinite}")
        steps = 8
        schedule = {0: [(0, 54), (91, 100)], steps: [(61, 84)]}
        for k in range(1, steps):
            schedule[k] = [(53 + k, 54 + k), (91 - k, 92 - k)]
        for k in range(steps + 1):
            frame = arm_frame(ease(k / steps)).svg()
            if k == 0:
                frame += "".join(
                    f'<rect class="steam" style="animation-delay:{d}s" x="{x}" y="{y}" width="3" height="3" fill="{t["steam"]}"/>'
                    for x, y, d in ((865, 203, 0), (869, 197, -1.4)))
            css.append(visibility_keyframes(f"f{k}", schedule[k]))
            css.append(f".f{k}{{opacity:{1 if k == 0 else 0};animation:f{k} 8s steps(1,end) infinite}}")
            parts.append(f'<g class="f{k}">{frame}</g>')
    else:
        parts.append(f'<rect class="led" x="636" y="182" width="2" height="2" fill="{t["led"]}"/>')
        css.append("@keyframes breathe{0%,100%{transform:translateY(0)}25%,75%{transform:translateY(-1px)}50%{transform:translateY(-2px)}}")
        css.append(".breathe{animation:breathe 3.6s steps(1,end) infinite}")
        parts.append(f'<g class="breathe">{sleeper().svg()}</g>')
        css.append("@keyframes zz{0%{transform:translate(0,0);opacity:0}15%{opacity:1}100%{transform:translate(14px,-30px);opacity:0}}")
        css.append(".z{opacity:0;animation:zz 3.6s steps(30) infinite}")
        for n in range(3):
            z = Layer()
            z_glyph(z, 140 + n * 6, 158 - n * 8, 3 if n == 2 else 2, t["ink"])
            parts.append(f'<g class="z" style="animation-delay:{-n * 1.2:.1f}s">{z.svg()}</g>')

    css.append("@media (prefers-reduced-motion:reduce){*{animation:none!important}}")
    total, streak, repos = numbers
    when = "At night, Lucas sleeps curled up on the pouf." if t["night"] else \
        "By day, Lucas drinks his coffee at the window."
    alt = (f"Pixel-art room under a wide window. {total} contributions this year, {streak} days in a row, "
           f"{repos} repositories this month. {when} The desk chair is empty.")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'shape-rendering="crispEdges" role="img" aria-label="{alt}">'
        f"<title>{alt}</title><style>{''.join(css)}</style>"
        f'<clipPath id="r"><rect width="{W}" height="{H}" rx="10"/></clipPath>'
        f'<g clip-path="url(#r)">{"".join(parts)}</g></svg>\n'
    )


# ---------------------------------------------------------------------------
# Project cards, drawn in the same pixel type as the banner
# ---------------------------------------------------------------------------

SMALL = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010", "P": "110101110100100",
    "Q": "010101101110011", "R": "110101110101101", "S": "011100010001110", "T": "111010010010010",
    "U": "101101101101111", "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111",
    "0": "111101101101111", "1": "010110010010111", "2": "110001010100111", "3": "110001010001110",
    "4": "101101111001001", "5": "111100110001110", "6": "011100111101111", "7": "111001010010010",
    "8": "111101111101111", "9": "111101111001110",
    "-": "000000111000000", ".": "000000000000010", ",": "000000000010100", "'": "010010000000000",
    "·": "000000010000000", ":": "000010000010000", "/": "001001010100100", "(": "001010010010001",
    ")": "100010010010100", "+": "000010111010000", "!": "010010010000010", "?": "110001010000010",
}

CARD_DAY = dict(bg="#f6f3ec", border="#d9cfbb", ink="#1d3047", dim="#3f5873", faint="#7d8899", rule="#cfc6b3")
CARD_NIGHT = dict(bg="#10182b", border="#2c2f39", ink="#eeece4", dim="#a9b0bd", faint="#6f7a8c", rule="#3a3f4c")


def small_width(s, px):
    return len(s) * 4 * px - px


def small_text(layer, s, x, y, px, colour):
    for ch in s.upper():
        glyph = SMALL.get(ch)
        if glyph:
            for i, bit in enumerate(glyph):
                if bit == "1":
                    layer.rect(x + (i % 3) * px, y + (i // 3) * px, px, px, colour)
        x += 4 * px


def wrap(s, max_chars):
    lines, line = [], ""
    for word in s.split():
        if line and len(line) + 1 + len(word) > max_chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


CARD_W, IMG_X, IMG_Y, IMG_W, IMG_H = 480, 6, 6, 468, 263


def card_height(n_lines):
    return 318 + n_lines * 22 + 34


def card_svg(project, palette, n_lines, image_uri):
    h = card_height(n_lines)
    lay = Layer(width=CARD_W, height=h)
    lay.rect(IMG_X, IMG_Y + IMG_H, IMG_W, 2, palette["border"])
    small_text(lay, project["name"], 16, 286, 4, palette["ink"])
    y = 318
    for line in wrap(project["line"], 37):
        small_text(lay, line, 16, y, 3, palette["dim"])
        y += 22
    small_text(lay, project["stack"], 16, h - 26, 2, palette["faint"])
    alt = f'{project["name"]}: {project["line"]}'.replace('"', "'")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CARD_W} {h}" width="{CARD_W}" height="{h}" '
        f'role="img" aria-label="{alt}"><title>{alt}</title>'
        f'<clipPath id="i"><rect x="{IMG_X}" y="{IMG_Y}" width="{IMG_W}" height="{IMG_H}" rx="4"/></clipPath>'
        f'<rect x="1.5" y="1.5" width="{CARD_W - 3}" height="{h - 3}" rx="8" fill="{palette["bg"]}" '
        f'stroke="{palette["border"]}" stroke-width="3"/>'
        f'<image href="{image_uri}" x="{IMG_X}" y="{IMG_Y}" width="{IMG_W}" height="{IMG_H}" '
        f'preserveAspectRatio="xMidYMid slice" clip-path="url(#i)"/>'
        f'<g shape-rendering="crispEdges">{lay.svg()}</g></svg>\n'
    )


def rule_svg(label, palette, align):
    """A pixel label and a dotted rule across the README, like the banner's dithering."""
    w, h, px = 1000, 20, 3
    lay = Layer()
    tw = small_width(label, px)
    if align == "left":
        small_text(lay, label, 0, 2, px, palette["ink"])
        x0, x1 = tw + 16, w
    else:
        tw = small_width(label, 2)
        small_text(lay, label, w - tw, 5, 2, palette["faint"])
        x0, x1 = 0, w - tw - 16
    for x in range(x0, x1 - 3, 8):
        lay.rect(x, 9, 4, 2, palette["rule"])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'shape-rendering="crispEdges" role="img" aria-label="{label}"><title>{label}</title>{lay.svg()}</svg>\n')


def picture(day, night, alt, width, href=None):
    img = (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="{night}">\n'
           f'  <img src="{day}" alt="{alt}" width="{width}">\n</picture>')
    if href:
        indented_img = "\n  ".join(img.splitlines())
        return f'<a href="{href}">\n  {indented_img}\n</a>'
    return img


def write_projects(root):
    projects = json.loads((root / "projects.json").read_text())
    out = root / "projects"
    out.mkdir(exist_ok=True)
    n_lines = max(len(wrap(p["line"], 37)) for p in projects)
    for p in projects:
        image = root / p["image"]
        mime = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}[image.suffix[1:].lower()]
        uri = f"data:{mime};base64,{base64.b64encode(image.read_bytes()).decode()}"
        for name, palette in (("day", CARD_DAY), ("night", CARD_NIGHT)):
            (out / f'{p["key"]}-{name}.svg').write_text(card_svg(p, palette, n_lines, uri))
    for name, palette in (("day", CARD_DAY), ("night", CARD_NIGHT)):
        (out / f"heading-{name}.svg").write_text(rule_svg("What I'm building at the moment", palette, "left"))
        (out / f"footer-{name}.svg").write_text(rule_svg("shared as it goes", palette, "right"))

    rows = ['<p>\n  ' + picture("projects/heading-day.svg", "projects/heading-night.svg",
                                  "What I'm building at the moment", "100%").replace("\n", "\n  ") + '\n</p>']
    for i in range(0, len(projects), 2):
        cells = [picture(f'projects/{p["key"]}-day.svg', f'projects/{p["key"]}-night.svg',
                         f'{p["name"]}: {p["line"]}', "49%", p["url"]) for p in projects[i:i + 2]]
        rows.append('<p>\n  ' + '\n  &nbsp;'.join(cell.replace("\n", "\n  ") for cell in cells) + '\n</p>')
    rows.append('<p>\n  ' + picture("projects/footer-day.svg", "projects/footer-night.svg",
                                    "Shared as it goes", "100%").replace("\n", "\n  ") + '\n</p>')
    block = "\n".join(rows)

    readme = root / "README.md"
    text_ = readme.read_text()
    start, end = "<!-- projects:start -->", "<!-- projects:end -->"
    head, rest = text_.split(start, 1)
    _, tail = rest.split(end, 1)
    readme.write_text(f"{head}{start}\n{block}\n{end}{tail}")
    return len(projects)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--login", default="lucasssoh")
    ap.add_argument("--sample", action="store_true", help="made-up numbers, no network")
    ap.add_argument("--out", default=str(HERE), help="banner output folder (default: banner/)")
    args = ap.parse_args()

    if args.sample:
        numbers = (1234, 12, 5)
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is not set (or use --sample)")
        numbers = fetch(args.login, token)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, palette in (("day", DAY), ("night", NIGHT)):
        (out / f"{name}.svg").write_text(build(palette, numbers))
    print(f"{numbers[0]} contributions, {numbers[1]} days in a row, {numbers[2]} repositories")
    print(f"{write_projects(HERE.parent)} project cards")


if __name__ == "__main__":
    main()
