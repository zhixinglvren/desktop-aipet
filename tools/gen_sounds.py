# -*- coding: utf-8 -*-
"""生成 7 种提示音 WAV 到 sounds/ 目录（相对本脚本上级）。

设计约束：
- 纯标准库（wave / struct / math），离线可用，无第三方依赖。
- 用 Windows 内置 winsound.PlaySound 播放，输出 16-bit PCM 单声道 WAV。
- 每段带快速指数衰减包络，避免起止爆音（click）。
- 整体风格模仿苹果手机短信提示音：短促、清脆、电子感、带轻微谐波。

7 种（序号 0.wav~6.wav 与 aipet.py 的 SOUND_NAMES 顺序一致）：
  0 和弦 / 1 极光 / 2 脉冲 / 3 圆圈 / 4 鸟鸣 / 5 电报 / 6 类似QQ上线

author: wangxin49245
"""
import math
import os
import struct
import wave

SR = 44100


def _env(n, decay=3.0, attack=0.005):
    """快速指数衰减包络，attack 段线性抬升防起爆音。"""
    out = []
    a = max(1, int(attack * SR))
    for i in range(n):
        if i < a:
            out.append(i / a)
        else:
            out.append(math.exp(-decay * ((i - a) / SR)))
    return out


def _cat(*segs):
    out = []
    for s in segs:
        out += s
    return out


def _render(samples):
    frames = bytearray()
    for s in samples:
        v = max(-1.0, min(1.0, s))
        frames += struct.pack("<h", int(v * 32767))
    return bytes(frames)


def _write(path, samples):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(_render(samples))


def _silence(dur):
    return [0.0] * int(round(dur * SR))


def _tone(freq, dur, gain=0.3, decay=3.0, harmonics=None):
    """单频 + 可选谐波，快速衰减（清脆）。"""
    n = int(round(dur * SR))
    env = _env(n, decay=decay)
    hs = harmonics or [(1.0, freq)]
    buf = [0.0] * n
    for amp, f in hs:
        for i in range(n):
            buf[i] += amp * math.sin(2 * math.pi * f * i / SR)
    total = sum(a for a, _ in hs) or 1
    return [gain * env[i] * buf[i] / total for i in range(n)]


def _chirp(f0, f1, dur, gain=0.28, decay=3.0):
    """频率线性滑音（鸟鸣 / 上扬感）。相位累加保证连续。"""
    n = int(round(dur * SR))
    env = _env(n, decay=decay)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        f = f0 + (f1 - f0) * t / dur
        phase += 2 * math.pi * f / SR
        out.append(math.sin(phase))
    return [gain * env[i] * out[i] for i in range(n)]


def _pulse(freq, dur, gain=0.28, rate=9.0, decay=2.0):
    """方波振幅调制脉冲串（脉冲 / 电报感）。"""
    n = int(round(dur * SR))
    env = _env(n, decay=decay)
    out = []
    for i in range(n):
        t = i / SR
        carrier = math.sin(2 * math.pi * freq * t)
        mod = 1.0 if math.sin(2 * math.pi * rate * t) >= 0 else 0.0
        out.append(carrier * mod)
    return [gain * env[i] * out[i] for i in range(n)]


def _chord(freqs, dur, gain=0.26, decay=2.5):
    """多频同时发声（和弦）。"""
    n = int(round(dur * SR))
    env = _env(n, decay=decay)
    buf = [0.0] * n
    for f in freqs:
        for i in range(n):
            buf[i] += math.sin(2 * math.pi * f * i / SR)
    total = len(freqs) or 1
    return [gain * env[i] * buf[i] / total for i in range(n)]


def _seq(notes, gain=0.3, decay=3.2):
    """音符序列 [(freq, dur), ...] 依次拼接，每音清脆衰减。"""
    return _cat(*[_tone(f, d, gain=gain, decay=decay) for f, d in notes])


# (序号, 采样, 中文名) —— 顺序与 aipet.py 的 SOUND_NAMES 保持一致
SOUNDS = [
    (0, _chord([523.25, 659.25, 783.99, 1046.50], 0.50, 0.26), "和弦"),
    (1, _seq([(659.25, 0.12), (783.99, 0.12), (987.77, 0.24)], 0.26), "极光"),
    (2, _pulse(880.0, 0.40, 0.26, rate=9.0), "脉冲"),
    (3, _tone(784.0, 0.50, 0.30, decay=2.5,
              harmonics=[(1.0, 784.0), (0.3, 1568.0), (0.12, 2352.0)]), "圆圈"),
    (4, _cat(_chirp(1800.0, 2600.0, 0.12, 0.28),
             _silence(0.05),
             _chirp(2000.0, 2950.0, 0.12, 0.28)), "鸟鸣"),
    (5, _cat(_pulse(1300.0, 0.09, 0.30, rate=11.0, decay=1.0),
             _silence(0.05),
             _pulse(820.0, 0.16, 0.30, rate=7.0, decay=1.0)), "电报"),
    (6, _seq([(523.25, 0.09), (659.25, 0.09), (783.99, 0.09),
              (1046.50, 0.18)], 0.32), "类似QQ上线"),
]


def main():
    out_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "sounds"))
    os.makedirs(out_dir, exist_ok=True)
    names = []
    for idx, samples, name in SOUNDS:
        path = os.path.join(out_dir, "{}.wav".format(idx))
        _write(path, samples)
        names.append(name)
        print("生成 {} -> {}".format(path, name))
    print("完成，共 {} 种提示音：{}".format(len(names), "、".join(names)))


if __name__ == "__main__":
    main()
