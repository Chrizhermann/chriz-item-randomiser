import os, re, struct, zlib, json, sys, pickle

SAVE = r"C:\Users\chris\OneDrive\Documents\Baldur's Gate - Enhanced Edition Trilogy\save\000000476-yaga dead"
GAME = r"C:\Games\Baldur's Gate II Enhanced Edition modded"
SCR  = os.path.dirname(os.path.abspath(__file__))

gam = open(os.path.join(SAVE, "BALDUR.gam"), "rb").read()
sav = open(os.path.join(SAVE, "BALDUR.SAV"), "rb").read()

# ---- GAM globals ----
goff = struct.unpack_from("<I", gam, 0x38)[0]
gcnt = struct.unpack_from("<I", gam, 0x3c)[0]
globals_ = {}
for i in range(gcnt):
    base = goff + i*84
    name = gam[base:base+32].split(b"\x00")[0].decode("ascii", "replace").upper()
    val = struct.unpack_from("<i", gam, base+0x28)[0]
    globals_[name] = val
fl = {k: v for k, v in globals_.items() if re.fullmatch(r"FL\d+T\d+", k)}
print(f"GAM: sig={gam[:8]}, globals count={gcnt}, FL-globals={len(fl)}")

# ---- SAV decompress ----
assert sav[:8] == b"SAV V1.0", sav[:8]
entries = {}
p = 8
while p < len(sav):
    nl = struct.unpack_from("<I", sav, p)[0]; p += 4
    name = sav[p:p+nl].split(b"\x00")[0].decode("ascii"); p += nl
    ulen, clen = struct.unpack_from("<II", sav, p); p += 8
    data = zlib.decompress(sav[p:p+clen]); p += clen
    assert len(data) == ulen, name
    entries[name.lower()] = data
print(f"SAV entries: {len(entries)}")
by_ext = {}
for n in entries: by_ext.setdefault(n.rsplit('.',1)[-1], []).append(n)
print({k: len(v) for k, v in by_ext.items()})
print("ARE entries:", sorted(by_ext.get("are", [])))
print("STO entries:", sorted(by_ext.get("sto", [])))

# ---- 2da master list ----
rows = []
for line in open(os.path.join(GAME, r"randomiser\lists\items\base\bg2.2da"), encoding="latin-1"):
    t = line.split()
    if len(t) >= 6 and re.fullmatch(r"\d+", t[3]) and re.fullmatch(r"\d+", t[4]):
        rows.append(dict(item=t[0].lower(), repl=t[1].lower(), src=t[2].lower(),
                         tier=int(t[3]), token=int(t[4]), ident=t[5],
                         chance=t[6] if len(t) > 6 else ""))
print(f"2da rows parsed: {len(rows)}")

# tier/token -> global name (2-digit token)
def gname(tier, token): return f"FL{tier}T{token:02d}"
for r in rows:
    r["glob"] = gname(r["tier"], r["token"])
    r["val"] = fl.get(r["glob"], None)

# ---- resref presence scan helper ----
def scan(data, resref):
    rr = resref.encode()
    if len(rr) < 8:
        pat = re.compile(re.escape(rr) + rb"\x00", re.I)
    else:
        pat = re.compile(re.escape(rr) + rb"(?![0-9A-Za-z])", re.I)
    return len(pat.findall(data))

def where_is(resref):
    hits = []
    if scan(gam, resref): hits.append(("GAM", scan(gam, resref)))
    for n, d in entries.items():
        c = scan(d, resref)
        if c: hits.append((n, c))
    return hits

def obtainable(hits):
    out = []
    for n, c in hits:
        if n == "GAM": out.append(n)
        elif n.endswith(".sto"): out.append(n)
        elif n.endswith(".are") and re.match(r"ar[3456]", n): out.append(n)
    return out

# ---- the 14 claims ----
claims = [
    ("halb10","FL10T06",-1), ("sw2h21","FL10T17",20), ("ax1h10","FL6T11",4),
    ("ax1h16","FL10T01",1), ("sw1h66","FL10T15",18), ("dagg23","FL13T06",-1),
    ("halb05",None,-1), ("halb04","FL4T07",18), ("staf21","FL10T13",12),
    ("sw1h70","FL10T16",10), ("compon15","FL12T15",6), ("ring46","FL11T09",13),
    ("compon08","FL12T08",3), ("sw1h30","FL6T02",-1),
]
print("\n=== 14 CLAIMS: token values + 2da mapping + presence ===")
for item, g, expv in claims:
    row = [r for r in rows if r["item"] == item]
    rinfo = ",".join(f"{r['glob']}(t{r['tier']}/tok{r['token']},src={r['src']},id={r['ident']},ch={r['chance']})" for r in row) or "NO-2DA-ROW"
    actual = {r["glob"]: fl.get(r["glob"]) for r in row}
    if g: actual[g] = fl.get(g)
    hits = where_is(item)
    obt = obtainable(hits)
    print(f"{item:9s} claimed={g}={expv}  2da:{rinfo}  actualvals={actual}  hits={hits}  OBTAINABLE={obt or 'NO'}")

# ---- sanity E ----
print("\n=== SANITY E ===")
for item in ("brac16", "scrl8b"):
    hits = where_is(item)
    print(item, "hits:", hits, "obtainable:", obtainable(hits))

# ---- dump for stage 2 ----
with open(os.path.join(SCR, "stage1.pkl"), "wb") as f:
    pickle.dump(dict(fl=fl, rows=rows, sav_names=sorted(entries)), f)

# also full FL global table for reference
print("\n=== ALL FL GLOBALS (from GAM) ===")
for k in sorted(fl, key=lambda k: (int(re.match(r"FL(\d+)T(\d+)", k).group(1)), int(re.match(r"FL(\d+)T(\d+)", k).group(2)))):
    print(k, fl[k])
