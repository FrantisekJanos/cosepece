"""Zvuková stopa Reelu: efekty podle událostí šablony (window.audioCues) a tichý podkres.

Všechno se syntetizuje v numpy (žádné samply ani hudba třetích stran), se stejným seedem vyjde stejný soubor.
Výstup je WAV 48 kHz stereo 16 bit, předmasterovaný zhruba na -13 LUFS se špičkami pod -3 dBFS;
konečnou hlasitost (-14 LUFS, true peak) dorovná ffmpeg loudnorm při renderu (loudnorm_filter).
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 48000
BPM = 104
PREMASTER_LUFS = -13.0   # o něco hlasitěji než cíl, ať loudnorm jen lineárně ubírá
CEILING = 10 ** (-3.0 / 20)   # strop špiček předmasteru
TARGET_LUFS = -14.0
BED_RMS = 0.11       # efektivní hodnota podkresu před masterem (nese integrovanou hlasitost)
FX_LEVEL = 0.5       # úroveň efektů vůči podkresu
TARGET_TP = -2.0   # rezerva na překmity po kódování do AAC (požadavek je -1 dBTP)

# kořeny basu po taktech (d moll: D, D, B, C) a akcenty osminových not
ROOTS = (73.42, 73.42, 58.27, 65.41)
ACCENTS = (1.0, 0.4, 0.6, 0.4, 0.8, 0.4, 0.6, 0.5)


# ---------- stavební kameny ----------

def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def _band_noise(rng: np.random.Generator, n: int, lo: float, hi: float) -> np.ndarray:
    """Šum omezený na pásmo lo–hi Hz (strmost 4. řádu), jednotková efektivní hodnota."""
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = 1e-6
    spec *= 1 / (1 + (lo / f) ** 4) / (1 + (f / hi) ** 4)
    out = np.fft.irfft(spec, n)
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-12)


def _sweep_noise(rng: np.random.Generator, freq: np.ndarray, q: float = 1.6) -> np.ndarray:
    """Šum přes pásmovou propust s proměnnou střední frekvencí (stavový filtr), jednotková špička."""
    x = rng.standard_normal(len(freq))
    f = 2 * np.sin(np.pi * np.minimum(freq, SR / 7) / SR)
    damp = 1 / q
    low = band = 0.0
    out = np.empty(len(freq))
    for i in range(len(freq)):
        low += f[i] * band
        band += f[i] * (x[i] - low - damp * band)
        out[i] = band
    return out / (np.max(np.abs(out)) + 1e-12)


def _chirp(n: int, f0: float, f1: float, tau: float) -> np.ndarray:
    """Sinus s frekvencí klesající exponenciálně z f0 na f1."""
    freq = f1 + (f0 - f1) * np.exp(-_t(n) / tau)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR)


def _decay(n: int, tau: float, attack: float = 0.001) -> np.ndarray:
    t = _t(n)
    return np.exp(-t / tau) * np.minimum(1.0, t / attack)


def _fade_tail(x: np.ndarray, seconds: float = 0.01) -> np.ndarray:
    k = min(len(x), int(seconds * SR))
    if k:
        x[-k:] *= np.linspace(1, 0, k)
    return x


def _add(buf: np.ndarray, t: float, mono: np.ndarray, gain: float = 1.0, pan=0.0) -> None:
    """Přimíchá mono zvuk od času t; pan -1 (vlevo) až 1 (vpravo), číslo nebo průběh."""
    start = int(round(t * SR))
    if start < 0:
        mono, start = mono[-start:], 0
        if isinstance(pan, np.ndarray):
            pan = pan[-len(mono):]
    n = min(len(mono), len(buf) - start)
    if n <= 0:
        return
    if isinstance(pan, np.ndarray):
        pan = pan[:n]
    angle = (pan + 1) * np.pi / 4
    buf[start:start + n, 0] += mono[:n] * gain * np.cos(angle)
    buf[start:start + n, 1] += mono[:n] * gain * np.sin(angle)


# ---------- efekty ----------

def _fx_whoosh(rng, cue):
    n = int(0.52 * SR)
    x = np.linspace(0, 1, n)
    freq = 250 * (2800 / 250) ** np.sin(np.pi * x) * (1 + 1.2 * x)
    env = np.sin(np.pi * x) ** 3
    snd = _sweep_noise(rng, freq) * env * 0.55 + np.sin(2 * np.pi * 58 * _t(n)) * env * 0.12
    side = 1 if cue.get("pos") else -1
    return -0.26, snd, side * np.linspace(-0.7, 0.7, n)


def _fx_tick(rng, cue):
    n = int(0.05 * SR)
    pos = float(cue.get("pos", 0.5))
    snd = np.sin(2 * np.pi * 1700 * (1 + 0.45 * pos) * _t(n)) * _decay(n, 0.006, 0.0003)
    snd += 0.5 * _band_noise(rng, n, 3000, 9000) * _decay(n, 0.002, 0.0002)
    return 0.0, snd * 0.32, float(rng.uniform(-0.15, 0.15))


def _fx_hit(rng, cue):
    n = int(0.45 * SR)
    snd = _chirp(n, 95, 48, 0.04) * _decay(n, 0.09) * 0.75
    snd += _band_noise(rng, n, 200, 1200) * _decay(n, 0.012) * 0.25
    return 0.0, _fade_tail(snd), 0.0


def _fx_stamp(rng, cue):
    n = int(0.5 * SR)
    t = _t(n)
    # úder: hluboké tělo, dřevěné klepnutí o desku (tlumený tón + šum) a ostrý nástup
    snd = _chirp(n, 170, 55, 0.035) * _decay(n, 0.08) * 1.0
    snd += np.sin(2 * np.pi * 310 * t) * _decay(n, 0.022) * 0.55
    snd += _band_noise(rng, n, 180, 1400) * _decay(n, 0.02) * 0.3
    snd += _band_noise(rng, n, 2000, 6000) * _decay(n, 0.004, 0.0003) * 0.2
    snd = np.tanh(snd * 2.0) * 1.05   # měkké omezení drží špičku úderu nízko vůči jeho energii
    # krátké zašustění papíru hned po dopadu (do 170 ms)
    grain = np.abs(_band_noise(rng, n, 20, 90))
    paper = _band_noise(rng, n, 2500, 8000) * grain * (t / 0.03) * np.exp(-t / 0.03)
    paper[t > 0.17] = 0
    k = int(0.17 * SR)
    paper[k - 960:k] *= np.linspace(1, 0, 960)
    snd += paper / (np.max(np.abs(paper)) + 1e-12) * 0.2
    return 0.0, _fade_tail(snd), float(rng.uniform(-0.1, 0.1))


def _fx_type(rng, cue):
    n = int(0.06 * SR)
    snd = _band_noise(rng, n, 1200, 4500) * _decay(n, 0.005, 0.0003)
    snd += 0.4 * np.sin(2 * np.pi * rng.uniform(200, 240) * _t(n)) * _decay(n, 0.012)
    pos = float(cue.get("pos", 0.5))
    return 0.0, snd * 0.2 * rng.uniform(0.65, 1.0), -0.35 + 0.7 * pos + float(rng.uniform(-0.1, 0.1))


def _fx_highlight(rng, cue):
    n = int(max(0.2, float(cue.get("dur", 0.9))) * SR)
    x = np.linspace(0, 1, n)
    freq = 2400 + 900 * x + 150 * np.sin(2 * np.pi * 9 * x)
    env = np.minimum(1, np.minimum(x, 1 - x) * n / (0.05 * SR))
    return 0.0, _sweep_noise(rng, freq, 3.0) * env * 0.11, np.linspace(-0.45, 0.45, n)


def _fx_drop(rng, cue):
    n = int(0.16 * SR)
    pos = float(cue.get("pos", 0.5))
    snd = np.sin(2 * np.pi * 400 * 2 ** rng.uniform(-0.12, 0.12) * _t(n)) * _decay(n, 0.03, 0.0005)
    snd += 0.6 * _band_noise(rng, n, 300, 1800) * _decay(n, 0.008, 0.0003)
    return 0.0, _fade_tail(snd) * 0.32, -0.6 + 1.2 * pos


def _fx_line(rng, cue):
    n = int(0.11 * SR)
    snd = _band_noise(rng, n, 1500, 6000) * np.hanning(n)
    return 0.0, snd * 0.06, -0.3 + 0.6 * float(cue.get("pos", 0.5))


def _fx_slide(rng, cue):
    n = int(max(0.2, float(cue.get("dur", 0.5))) * SR)
    x = np.linspace(0, 1, n)
    env = np.sin(np.pi * x ** 0.7) ** 2
    return 0.0, _sweep_noise(rng, 600 * (2600 / 600) ** x, 1.2) * env * 0.16, np.linspace(0.5, -0.2, n)


def _fx_strike(rng, cue):
    n = int(0.24 * SR)
    x = np.linspace(0, 1, n)
    env = np.sin(np.pi * x) ** 2
    return 0.0, _sweep_noise(rng, 1400 * (4800 / 1400) ** x, 2.0) * env * 0.28, np.linspace(-0.5, 0.5, n)


def _fx_pop(rng, cue):
    n = int(0.09 * SR)
    snd = np.sin(2 * np.pi * 330 * _t(n)) * _decay(n, 0.02, 0.0005)
    snd += 0.4 * _band_noise(rng, n, 800, 3000) * _decay(n, 0.004, 0.0003)
    return 0.0, _fade_tail(snd) * 0.25, -0.2 + 0.4 * float(cue.get("pos", 0.5))


def _fx_outro(rng, cue):
    # závěrečný hluboký úder s dlouhým dozvukem
    n = int(2.6 * SR)
    t = _t(n)
    snd = (np.sin(2 * np.pi * 36.71 * t) * 0.6 + np.sin(2 * np.pi * 73.42 * t) * 0.5) * _decay(n, 0.75, 0.004)
    snd += _band_noise(rng, n, 100, 700) * _decay(n, 0.05) * 0.2
    return 0.0, _fade_tail(snd, 0.3) * 0.75, 0.0


FX = {
    "whoosh": _fx_whoosh, "tick": _fx_tick, "hit": _fx_hit, "stamp": _fx_stamp, "type": _fx_type,
    "highlight": _fx_highlight, "drop": _fx_drop, "line": _fx_line, "slide": _fx_slide,
    "strike": _fx_strike, "pop": _fx_pop, "outro": _fx_outro,
}


def _reverb(rng: np.random.Generator, bus: np.ndarray) -> np.ndarray:
    """Krátký prostor: konvoluce s doznívajícím šumem (pro každý kanál jiným)."""
    n_ir = int(0.9 * SR)
    size = 1 << (len(bus) + n_ir).bit_length()
    out = np.empty_like(bus)
    pre = int(0.015 * SR)
    for ch in range(2):
        ir = _band_noise(rng, n_ir, 300, 6000) * np.exp(-_t(n_ir) / 0.22)
        ir = np.concatenate([np.zeros(pre), ir])
        ir /= np.sqrt(np.sum(ir ** 2))
        wet = np.fft.irfft(np.fft.rfft(bus[:, ch], size) * np.fft.rfft(ir, size), size)
        out[:, ch] = wet[:len(bus)]
    return out


# ---------- podkres ----------

def _pluck(freq: float, n: int) -> np.ndarray:
    """Tlumený basový tón: vyšší harmonické doznívají rychleji."""
    t = _t(n)
    snd = np.zeros(n)
    for h in range(1, 7):
        snd += np.sin(2 * np.pi * freq * h * t) / h ** 1.3 * np.exp(-t / (0.16 / h ** 0.8))
    return snd * np.minimum(1, t / 0.012)


def _bed(rng: np.random.Generator, n: int, t_outro: float) -> np.ndarray:
    """Podkres: dron v d moll, basový pulz po osminách (104 BPM), měkký úder na 1. a 3. dobu, tiché tikání."""
    bed = np.zeros((n, 2))
    t = _t(n)
    beat = 60 / BPM
    eighth = beat / 2

    # dron: D, A, F s pomalým dýcháním, kanály lehce rozladěné
    for freq, amp, rate in ((73.42, 0.1, 0.11), (146.83, 0.3, 0.17), (220.0, 0.3, 0.13), (293.66, 0.16, 0.09), (349.23, 0.18, 0.07), (440.0, 0.07, 0.05)):
        phase = rng.uniform(0, 2 * np.pi)
        lfo = 0.7 + 0.3 * np.sin(2 * np.pi * rate * t + phase)
        for ch, det in ((0, -0.25), (1, 0.25)):
            bed[:, ch] += np.sin(2 * np.pi * (freq + det) * t + phase) * amp * lfo * 0.22

    note_len = int(eighth * 1.6 * SR)
    k = 0
    while k * eighth < t_outro - 0.05:
        bar, step = divmod(k, 8)
        root = ROOTS[bar % len(ROOTS)]
        # bas zdvojený o oktávu výš, ať je slyšet i z reproduktoru telefonu
        freq = root * (2 if step == 7 else 1)
        _add(bed, k * eighth, 0.45 * _pluck(freq, note_len) + _pluck(freq * 2, note_len), 0.3 * ACCENTS[step])
        if step in (0, 4):
            m = int(0.22 * SR)
            _add(bed, k * eighth, _chirp(m, 70, 44, 0.03) * _decay(m, 0.06, 0.003), 0.22)
        if step % 2 == 1:
            m = int(0.03 * SR)
            _add(bed, k * eighth, _band_noise(rng, m, 5000, 10000) * _decay(m, 0.004, 0.0005), 0.035,
                 0.4 if step % 4 == 1 else -0.4)
        k += 1

    # závěr: pulz končí před poslední scénou, dron doznívá do ticha
    fade = np.ones(n)
    a, b = int((t_outro + 0.3) * SR), n - int(0.15 * SR)
    if b > a >= 0:
        fade[a:b] = 0.5 * (1 + np.cos(np.linspace(0, np.pi, b - a)))
        fade[b:] = 0
    fade[:int(0.4 * SR)] *= np.linspace(0, 1, int(0.4 * SR)) ** 0.5
    return bed * fade[:, None]


# ---------- hlasitost ----------

def _k_weight(x: np.ndarray) -> np.ndarray:
    """K-vážení podle ITU-R BS.1770 pro 48 kHz, spočítané ve frekvenční oblasti."""
    z = np.exp(-2j * np.pi * np.fft.rfftfreq(len(x), 1.0))
    h = (1.53512485958697 - 2.69169618940638 * z + 1.19839281085285 * z ** 2) \
        / (1 - 1.69065929318241 * z + 0.73248077421585 * z ** 2)
    h *= (1 - 2 * z + z ** 2) / (1 - 1.99004745483398 * z + 0.99007225036621 * z ** 2)
    return np.fft.irfft(np.fft.rfft(x) * h, len(x))


def integrated_lufs(stereo: np.ndarray) -> float:
    """Integrovaná hlasitost (BS.1770, bloky 400 ms po 100 ms, absolutní a relativní brána)."""
    power = sum(_k_weight(stereo[:, ch]) ** 2 for ch in range(2))
    csum = np.concatenate([[0.0], np.cumsum(power)])
    block, hop = int(0.4 * SR), int(0.1 * SR)
    starts = np.arange(0, len(power) - block + 1, hop)
    if not len(starts):
        return -70.0
    z = (csum[starts + block] - csum[starts]) / block
    loud = -0.691 + 10 * np.log10(z + 1e-12)
    z = z[loud > -70]
    if not len(z):
        return -70.0
    gate = -0.691 + 10 * np.log10(np.mean(z)) - 10
    z = z[-0.691 + 10 * np.log10(z) > gate]
    return float(-0.691 + 10 * np.log10(np.mean(z)))


def _limit(x: np.ndarray, ceiling: float) -> np.ndarray:
    """Omezovač špiček s předstihem: zesílení se počítá po 1 ms blocích, drží 40 ms a vyhlazuje se."""
    blk = SR // 1000
    nb = math.ceil(len(x) / blk)
    peak = np.zeros(nb * blk)
    peak[:len(x)] = np.max(np.abs(x), axis=1)
    need = np.minimum(1.0, ceiling / (peak.reshape(nb, blk).max(axis=1) + 1e-12))
    hold = 40
    padded = np.concatenate([np.ones(hold), need, np.ones(hold)])
    held = np.lib.stride_tricks.sliding_window_view(padded, 2 * hold + 1).min(axis=1)
    kernel = np.hanning(hold + 1)
    smooth = np.convolve(np.concatenate([np.ones(hold), held, np.ones(hold)]), kernel / kernel.sum(), "same")[hold:-hold]
    gain = np.interp(np.arange(len(x)), (np.arange(nb) + 0.5) * blk, smooth)
    return np.clip(x * gain[:, None], -ceiling, ceiling)


def _master(mix: np.ndarray) -> np.ndarray:
    """Dorovná předmaster na PREMASTER_LUFS se špičkami pod CEILING."""
    out = mix
    gain = 1.0
    for _ in range(4):
        gain *= 10 ** ((PREMASTER_LUFS - integrated_lufs(out)) / 20)
        out = _limit(mix * gain, CEILING)
    return out


# ---------- veřejné rozhraní ----------

def build_soundtrack(cues: list[dict], duration_s: float, out_wav: Path, *, seed: int = 0) -> Path:
    """Sestaví zvukovou stopu (WAV 48 kHz stereo) dlouhou duration_s z událostí šablony."""
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * SR))
    cues = sorted((c for c in cues if c.get("type") in FX), key=lambda c: (float(c["t"]), c["type"]))
    outro = [float(c["t"]) for c in cues if c["type"] == "outro"]
    t_outro = outro[0] if outro else max(0.0, duration_s - 3.0)

    bed = _bed(rng, n, t_outro)
    fx = np.zeros((n, 2))
    duck = np.ones(n)
    for cue in cues:
        offset, snd, pan = FX[cue["type"]](rng, cue)
        t = float(cue["t"])
        _add(fx, t + offset, snd, float(cue.get("gain", 1.0)), pan)
        if cue["type"] in ("stamp", "hit", "outro"):
            # podkres úderu ustoupí: 40 ms před ním rychle klesne, pak se zvolna vrací
            depth = 0.85 if cue["type"] == "stamp" else 0.6
            pre, m = int(0.04 * SR), int(0.5 * SR)
            dip = np.concatenate([np.linspace(0, 1, pre, endpoint=False), np.exp(-np.arange(m) / (0.14 * SR))])
            a = int(t * SR) - pre
            lo, hi = max(0, a), min(n, a + len(dip))
            if hi > lo:
                duck[lo:hi] = np.minimum(duck[lo:hi], 1 - depth * dip[lo - a:hi - a])
    fx += 0.12 * _reverb(rng, fx)
    fx *= FX_LEVEL

    # podkres drží stálou úroveň; měkké omezení mu srazí špičky, ať nad ním zbyde místo pro údery
    bed *= BED_RMS / (np.sqrt(np.mean(bed ** 2)) + 1e-12)
    bed = np.tanh(bed / (3.0 * BED_RMS)) * 3.0 * BED_RMS
    mix = _master(bed * duck[:, None] + fx)
    edge = int(0.01 * SR)
    mix[:edge] *= np.linspace(0, 1, edge)[:, None]
    mix[-edge:] *= np.linspace(1, 0, edge)[:, None]

    pcm = np.round(mix * 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out_wav


def loudnorm_filter(wav: Path, *, target_i: float = TARGET_LUFS, target_tp: float = TARGET_TP) -> str:
    """Změří WAV prvním průchodem loudnorm a vrátí filtr pro druhý (lineární) průchod."""
    base = f"loudnorm=I={target_i}:TP={target_tp}:LRA=11"
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af", base + ":print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    found = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", run.stderr)
    if not found:
        raise RuntimeError("ffmpeg loudnorm nevrátil měření")
    m = json.loads(found[-1])
    return (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def measure_loudness(path: Path) -> dict:
    """Integrovaná hlasitost (LUFS) a true peak (dBTP) souboru podle ffmpeg ebur128."""
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    summary = run.stderr[run.stderr.rindex("Summary:"):]
    return {
        "lufs": float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)),
        "lra": float(re.search(r"LRA:\s+(-?[\d.]+) LU", summary).group(1)),
        "true_peak": float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary).group(1)),
    }
