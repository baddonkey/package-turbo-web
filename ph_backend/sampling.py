"""Stichprobe: random selection of dossiers for manual checks (user documentation 14.2)."""

import random

from .sip import SipPackage

ABSOLUTE = "absolute"
PERCENT = "percent"
DEFAULT_MODE = ABSOLUTE
DEFAULT_SIZE = 10


def draw_sample(pkg: SipPackage, mode: str = DEFAULT_MODE, size: int = DEFAULT_SIZE, rng=None) -> list[dict]:
    dossiers = [d for d in pkg.iter_kind("dossier") if pkg.kind(d) == "dossier"]
    if mode == PERCENT:
        count = round(len(dossiers) * max(0, min(size, 100)) / 100)
    else:
        count = max(0, size)
    chosen = (rng or random.SystemRandom()).sample(dossiers, min(count, len(dossiers)))
    order = {id(d): i for i, d in enumerate(dossiers)}
    chosen.sort(key=lambda d: order[id(d)])
    return [
        {"object": "Dossier", "reference": pkg.text(d, "aktenzeichen"), "objectName": pkg.text(d, "titel"),
         "key": pkg.key(d)}
        for d in chosen
    ]
