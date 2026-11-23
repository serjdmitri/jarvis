"""Mesh checks used by the Mac helper (scan check) and by the Go (printable parts). Standard library only. Reads STL (binary / ascii), OBJ, PLY (ascii) and GLB.
-> {"triangles", "bbox": (min, max) | None, "open_edges", "watertight", "textured", "size_cm": (x, y, z) | None, "units": "..."}"""
import json
import re
import struct
import zipfile


def _stl(data):
    tris = []
    if data[:5] == b"solid" and b"facet" in data[:600]:
        v = [tuple(map(float, m)) for m in re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)]
        tris = [tuple(v[i:i + 3]) for i in range(0, len(v) - 2, 3)]
    elif len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):
            for i in range(n):
                o = 84 + 50 * i + 12
                tris.append(tuple(struct.unpack("<3f", data[o + 12 * k:o + 12 * k + 12]) for k in range(3)))
    return tris, False


def _obj(data):
    v, tris = [], []
    for ln in data.decode("utf-8", "ignore").splitlines():
        p = ln.split()
        if not p:
            continue
        if p[0] == "v" and len(p) >= 4:
            v.append(tuple(float(x) for x in p[1:4]))
        elif p[0] == "f" and len(p) >= 4:
            idx = [int(x.split("/")[0]) for x in p[1:]]
            idx = [i - 1 if i > 0 else len(v) + i for i in idx]
            for k in range(1, len(idx) - 1):
                try:
                    tris.append((v[idx[0]], v[idx[k]], v[idx[k + 1]]))
                except IndexError:
                    pass
    return tris, bool(re.search(rb"^vt ", data, re.M)) and bool(re.search(rb"mtllib", data))


def _ply(data):
    head, _, body = data.partition(b"end_header")
    h = head.decode("ascii", "ignore")
    if "format ascii" not in h:
        return [], False
    nv = int(re.search(r"element vertex (\d+)", h).group(1))
    lines = body.decode("ascii", "ignore").split("\n")[1:]
    v = [tuple(float(x) for x in ln.split()[:3]) for ln in lines[:nv]]
    tris = []
    for ln in lines[nv:]:
        p = ln.split()
        if p and p[0].isdigit():
            idx = [int(x) for x in p[1:1 + int(p[0])]]
            for k in range(1, len(idx) - 1):
                tris.append((v[idx[0]], v[idx[k]], v[idx[k + 1]]))
    return tris, "u" in h and "texture" in h.lower()


def _glb(data):
    """Triangle COUNT and texture presence only (positions stay inside the binary buffer)."""
    if data[:4] != b"glTF":
        return None, False, 0
    ln = struct.unpack("<I", data[12:16])[0]
    j = json.loads(data[20:20 + ln].decode("utf-8", "ignore"))
    n = 0
    for m in j.get("meshes", []):
        for pr in m.get("primitives", []):
            acc = j["accessors"][pr["indices"]] if "indices" in pr else j["accessors"][pr["attributes"]["POSITION"]]
            n += acc.get("count", 0) // 3
    return j, bool(j.get("images") or j.get("textures")), n


def check(data, ext):
    ext = ext.lower().lstrip(".")
    out = {"triangles": 0, "bbox": None, "open_edges": None, "watertight": None, "textured": False, "size_cm": None, "units": ""}
    if ext == "glb":
        j, tex, n = _glb(data)
        out.update(triangles=n, textured=tex, units="metres (the glTF standard)")
        return out
    if ext == "usdz":
        try:
            names = zipfile.ZipFile(__import__("io").BytesIO(data)).namelist()
            out.update(textured=any(x.lower().endswith((".png", ".jpg", ".jpeg")) for x in names), units="not read (USDZ is only saved)")
        except zipfile.BadZipFile:
            pass
        return out
    tris, tex = {"stl": _stl, "obj": _obj, "ply": _ply}.get(ext, lambda d: ([], False))(data)
    out.update(triangles=len(tris), textured=tex)
    if not tris:
        return out
    xs, ys, zs = zip(*[p for t in tris for p in t])
    lo, hi = (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
    out["bbox"] = (lo, hi)
    key = lambda p: (round(p[0], 4), round(p[1], 4), round(p[2], 4))
    edges = {}
    for t in tris:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            e = tuple(sorted((key(t[a]), key(t[b]))))
            edges[e] = edges.get(e, 0) + 1
    out["open_edges"] = sum(1 for c in edges.values() if c == 1)
    out["watertight"] = out["open_edges"] == 0 and all(c % 2 == 0 for c in edges.values())      # every edge belongs to closed shells (touching solids share edges in fours; slicers merge them)
    d = [hi[i] - lo[i] for i in range(3)]
    scale = 100.0 if ext in ("obj", "ply") and max(d) < 20 else 0.1          # obj/ply are usually metres; stl is usually millimetres: this is an ASSUMPTION and is reported
    out["size_cm"] = tuple(round(x * scale, 1) for x in d)
    out["units"] = "assumed metres" if scale == 100.0 else "assumed millimetres"
    return out


def scan_verdict(c):
    """One plain sentence about a scan."""
    if not c["triangles"]:
        return "I could not read triangles from that file."
    if c["open_edges"] and c["open_edges"] >= 3 and c["open_edges"] > c["triangles"] * 0.002:
        return f"There are holes ({c['open_edges']} open edges): scan that part again, often the underside."
    bits = []
    if c["textured"] is False and c["triangles"]:
        bits.append("no colour texture was found")
    return "Scan looks complete" + (" (" + ", ".join(bits) + ")" if bits else "") + (f"; about {c['size_cm'][0]} x {c['size_cm'][1]} x {c['size_cm'][2]} cm ({c['units']})." if c["size_cm"] else ".")
