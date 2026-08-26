import os, re, struct, zlib, pickle, json
from collections import defaultdict

SAVE = r"C:\Users\chris\OneDrive\Documents\Baldur's Gate - Enhanced Edition Trilogy\save\000000476-yaga dead"
GAME = r"C:\Games\Baldur's Gate II Enhanced Edition modded"
OV = os.path.join(GAME, "override")
SCR = os.path.dirname(os.path.abspath(__file__))

gam = open(os.path.join(SAVE, "BALDUR.gam"), "rb").read()
sav = open(os.path.join(SAVE, "BALDUR.SAV"), "rb").read()
entries = {}
p = 8
while p < len(sav):
    nl = struct.unpack_from("<I", sav, p)[0]; p += 4
    name = sav[p:p+nl].split(b"\x00")[0].decode(); p += nl
    ulen, clen = struct.unpack_from("<II", sav, p); p += 8
    entries[name.lower()] = zlib.decompress(sav[p:p+clen]); p += clen

goff, gcnt = struct.unpack_from("<II", gam, 0x38)
GV = {}
for i in range(gcnt):
    b = goff + i*84
    GV[gam[b:b+32].split(b"\x00")[0].decode("ascii","replace").upper()] = struct.unpack_from("<i", gam, b+0x28)[0]

def load2da(path):
    rows = []
    for line in open(path, encoding="latin-1"):
        t = line.split()
        if len(t) >= 6 and re.fullmatch(r"\d+", t[3]) and re.fullmatch(r"\d+", t[4]):
            rows.append(dict(item=t[0].lower(), repl=t[1].lower(), src=t[2].lower(),
                             tier=int(t[3]), token=int(t[4]),
                             glob=f"FL{int(t[3])}T{int(t[4]):02d}"))
    return rows
bg2 = load2da(os.path.join(GAME, r"randomiser\lists\items\base\bg2.2da"))
bg1 = load2da(os.path.join(GAME, r"randomiser\lists\items\base\bg1.2da"))
print("bg1 tiers:", sorted({r["tier"] for r in bg1}), " bg2 tiers:", sorted({r["tier"] for r in bg2}))
overlap = {r["glob"] for r in bg1} & {r["glob"] for r in bg2}
print("namespace overlap bg1/bg2:", sorted(overlap) or "NONE")

# ---------- parse all fl*.bcs delivery blocks ----------
blocks = []            # dicts: file, slot(from filename), value, token(norm), item, target
tok2blocks = defaultdict(list)
for fn in os.listdir(OV):
    m = re.match(r"(fl(\d+)t(\d+))\.bcs$", fn.lower())
    if not m: continue
    txt = open(os.path.join(OV, fn), "r", encoding="latin-1").read()
    for chunk in re.split(r"RS\nCR\n", txt):
        g = re.search(r'16399 (\d+) 0 0 0 "GLOBAL(fl\d+t\d+)"', chunk)
        if not g: continue
        val = int(g.group(1)); tokraw = g.group(2)
        tm = re.match(r"fl(\d+)t(\d+)", tokraw)
        tok = f"FL{int(tm.group(1))}T{int(tm.group(2)):02d}"
        ex = re.search(r'16397 0 0 0 0 "" "" OB\n[0 ]+"([^"]*)"OB', chunk)
        target = ex.group(1) if ex else None
        it = None
        gi = re.search(r'\n140OB\n.*?0 0 0 0 0"(\w+)" "" AC', chunk, re.S)
        if gi: it = gi.group(1)
        else:
            ci = re.findall(r'\d+ \d+ \d+ \d+ \d+"(\w+)" "" AC', chunk)
            ci = [x for x in ci if not x.upper().startswith("GLOBAL")]
            if ci: it = ci[0]
        b = dict(file=fn.lower(), slotname=m.group(1), slot=int(m.group(3)),
                 slottier=int(m.group(2)), value=val, token=tok, item=it, target=target)
        blocks.append(b); tok2blocks[tok].append(b)
print("bcs files parsed, total blocks:", len(blocks))

# ---------- ARE actor parsing ----------
def actors(d):
    off = struct.unpack_from("<I", d, 0x54)[0]
    cnt = struct.unpack_from("<H", d, 0x58)[0]
    out = []
    for i in range(cnt):
        b = off + i*0x110
        nm = d[b:b+32].split(b"\x00")[0].decode("latin-1")
        rr = d[b+0x80:b+0x88].split(b"\x00")[0].decode("latin-1").lower()
        out.append((nm, rr))
    return out

ov_are = {}          # area -> [(name, resref)]
flcre_loc = defaultdict(list)   # 'fl10t1' -> [areas]
for fn in os.listdir(OV):
    if not fn.lower().endswith(".are"): continue
    try:
        d = open(os.path.join(OV, fn), "rb").read()
        if d[:4] != b"AREA": continue
        a = actors(d)
    except Exception as e:
        print("ARE parse fail:", fn, e); continue
    ar = fn.lower()[:-4]
    ov_are[ar] = a
    for nm, rr in a:
        if re.fullmatch(r"fl\d+t\d+", rr): flcre_loc[rr].append(ar)
print("override areas parsed:", len(ov_are), " distinct fl-cre placements:", len(flcre_loc))

sav_are = {}
for n, d in entries.items():
    if n.endswith(".are") and d[:4] == b"AREA":
        sav_are[n[:-4]] = actors(d)
print("saved areas parsed:", len(sav_are))

def alive_in_saved(cre, area):
    """saved-area actor resref has first char replaced by '*'"""
    want = "*" + cre[1:]
    return any(rr == want or rr == cre for nm, rr in sav_are.get(area, []))

# ---------- item presence ----------
def scan(data, resref):
    rr = resref.encode()
    pat = re.compile(re.escape(rr) + (rb"\x00" if len(rr) < 8 else rb"(?![0-9A-Za-z])"), re.I)
    return bool(pat.search(data))

def where_is(resref):
    hits = []
    if scan(gam, resref): hits.append("GAM")
    for n, d in entries.items():
        if scan(d, resref): hits.append(n)
    return hits

def obtainable(hits):
    return [h for h in hits if h == "GAM" or h.endswith(".sto")
            or (h.endswith(".are") and re.match(r"ar[3456]", h))]

FORGE_OK = set("""hamm07 belt08 scrlag sw1h54a sw1h54b blun12 blun14a blun14b blun14c
blun30b chan16 compon18 compon02 compon10 bow19a compon19 xbow15""".split())

LIST14 = {"halb10","sw2h21","ax1h10","ax1h16","sw1h66","dagg23","halb05","halb04",
          "staf21","sw1h70","compon15","ring46","compon08","sw1h30"}

def slot_status(tier, val):
    """status of delivery slot creature fl<tier>t<val>"""
    cre = f"fl{tier}t{val}"
    areas = flcre_loc.get(cre, [])
    if not areas: return cre, areas, "NO-PLACEMENT-FOUND"
    st = []
    for ar in areas:
        if ar in sav_are:
            st.append(f"{ar}:visited:{'ALIVE' if alive_in_saved(cre, ar) else 'GONE'}")
        else:
            reach = "ToB-reachable" if re.match(r"ar[3456]", ar) else "UNREACHABLE-era"
            st.append(f"{ar}:unvisited:{reach}")
    return cre, areas, ";".join(st)

results = []
for rows, tag in ((bg2, "BG2"), (bg1, "BG1")):
    for r in rows:
        v = GV.get(r["glob"])
        line = dict(tag=tag, **{k: r[k] for k in ("item","glob","tier","token","src")}, val=v)
        if v is None:
            hits = where_is(r["item"])
            line["mode"] = "NOVAR"
            line["hits"] = hits
            line["obtain"] = obtainable(hits)
        elif v == -1:
            hits = where_is(r["item"])
            line["mode"] = "DELIVERED"
            line["hits"] = hits
            line["obtain"] = obtainable(hits)
        elif v == 0:
            line["mode"] = "ZERO"
        else:
            bl = [b for b in tok2blocks.get(r["glob"], []) if b["value"] == v]
            line["mode"] = "PENDING"
            line["block"] = [(b["file"], b["item"], b["target"]) for b in bl]
            cre, areas, st = slot_status(r["tier"], v)
            line["slotcre"] = cre; line["slotstatus"] = st
            allblocks = [(b["file"], b["value"]) for b in tok2blocks.get(r["glob"], [])]
            line["anyblocks"] = allblocks
        results.append(line)

with open(os.path.join(SCR, "sweep.json"), "w") as f:
    json.dump(results, f, indent=1)

# ---------- report problem candidates ----------
print("\n===== PENDING tokens (positive value) =====")
for l in results:
    if l["mode"] != "PENDING": continue
    onlist = "ON-LIST" if l["item"] in LIST14 else "NOT-ON-LIST"
    ok = bool(l["block"])
    print(f"{l['tag']} {l['item']:9s} {l['glob']:8s}={l['val']:>3} {onlist:11s} "
          f"block={'YES' if ok else 'NO'} {l['block'] if ok else 'anyblocks=' + str(l['anyblocks'])} "
          f"slot={l.get('slotcre')} status={l.get('slotstatus')}")

print("\n===== DELIVERED (-1) tokens with item NOT obtainable =====")
for l in results:
    if l["mode"] != "DELIVERED" or l["obtain"]: continue
    onlist = "ON-LIST" if l["item"] in LIST14 else "NOT-ON-LIST"
    forge = "FORGE-OK" if l["item"] in FORGE_OK else ""
    print(f"{l['tag']} {l['item']:9s} {l['glob']:8s} {onlist:11s} {forge:8s} hits={l['hits']}")

print("\n===== NOVAR rows with item not obtainable in save =====")
for l in results:
    if l["mode"] != "NOVAR" or l["obtain"]: continue
    forge = "FORGE-OK" if l["item"] in FORGE_OK else ""
    print(f"{l['tag']} {l['item']:9s} {l['glob']:8s} {forge:8s} hits={l['hits']}")

pickle.dump(dict(blocks=blocks, flcre_loc=dict(flcre_loc), sav_are_names=sorted(sav_are)),
            open(os.path.join(SCR, "stage2.pkl"), "wb"))
