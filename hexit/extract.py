"""Dominant-color extraction: weighted k-means++ in CIELAB. Only needs numpy."""
import numpy as np

_M = np.array([[.4124, .3576, .1805], [.2126, .7152, .0722], [.0193, .1192, .9505]])
_MI = np.linalg.inv(_M)
_W = np.array([.95047, 1.0, 1.08883])


def rgb_to_lab(rgb):
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)
    t = lin @ _M.T / _W
    f = np.where(t > .008856, np.cbrt(t), 7.787 * t + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)


def lab_to_rgb(lab):
    fy = (lab[:, 0] + 16) / 116
    f = np.stack([lab[:, 1] / 500 + fy, fy, fy - lab[:, 2] / 200], 1)
    xyz = np.where(f ** 3 > .008856, f ** 3, (f - 16 / 116) / 7.787) * _W
    lin = np.clip(xyz @ _MI.T, 0, 1)
    c = np.where(lin <= .0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - .055)
    return np.rint(np.clip(c, 0, 1) * 255).astype(int)


def _kmeans(X, w, k, rng, iters=20):
    n = len(X)
    k = min(k, n)
    C = [X[rng.choice(n, p=w / w.sum())]]
    d = ((X - C[0]) ** 2).sum(1)
    while len(C) < k:  # k-means++ seeding, weighted by pixel counts
        p = w * d
        if p.sum() <= 0:
            break
        C.append(X[rng.choice(n, p=p / p.sum())])
        d = np.minimum(d, ((X - C[-1]) ** 2).sum(1))
    C = np.array(C)
    prev = None
    for _ in range(iters):
        a = ((X[:, None, :] - C[None]) ** 2).sum(2).argmin(1)
        if prev is not None and (a == prev).all():
            break
        prev = a
        for j in range(len(C)):
            m = a == j
            if m.any():
                C[j] = np.average(X[m], axis=0, weights=w[m])
    return C, np.bincount(a, weights=w, minlength=len(C))


def prepare(rgba, min_alpha=125):
    """Bucket pixels once per image -> (Lab samples, weights). Reused for every k / seed."""
    px = rgba.reshape(-1, 4)
    px = px[px[:, 3] >= min_alpha][:, :3].astype(int)
    if not len(px):
        return None
    # 5-bit-per-channel buckets: denoise and collapse pixels into weighted samples
    key = (px[:, 0] >> 3) << 10 | (px[:, 1] >> 3) << 5 | px[:, 2] >> 3
    _, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    mean = np.stack([np.bincount(inv, px[:, i]) / cnt for i in range(3)], 1)
    return rgb_to_lab(mean), cnt.astype(float)


def cluster(prep, k=6, seed=1, merge_de=8.0):
    """Returns [{'rgb', 'hex', 'pct'}] ranked by pixel share."""
    if prep is None:
        return []
    X, w = prep
    C, n = _kmeans(X, w, k, np.random.default_rng(seed))
    items = sorted([[c, v] for c, v in zip(C, n) if v > 0], key=lambda t: -t[1])
    i = 0
    while i < len(items):  # merge near-duplicates so swatches stay distinct
        j = len(items) - 1
        while j > i:
            if np.linalg.norm(items[i][0] - items[j][0]) < merge_de:
                a, b = items[i], items.pop(j)
                tot = a[1] + b[1]
                a[0], a[1] = (a[0] * a[1] + b[0] * b[1]) / tot, tot
            j -= 1
        i += 1
    items.sort(key=lambda t: -t[1])
    rgb = lab_to_rgb(np.array([t[0] for t in items]))
    total = sum(t[1] for t in items)
    return [{"rgb": tuple(int(v) for v in r), "hex": "#%02X%02X%02X" % tuple(int(v) for v in r),
             "pct": t[1] / total * 100} for r, t in zip(rgb, items)]


def extract(rgba, k=6, seed=1):
    """Convenience: rgba (H, W, 4) uint8 -> palette."""
    return cluster(prepare(rgba), k, seed)
