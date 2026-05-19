"""Generate deterministic Agent Driver prompt icon PNG frames.

This intentionally avoids image-model generation for frame-to-frame consistency:
the ring geometry is identical in every frame and only the cyan highlight rotates.
"""

from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "generated" / "prompt-icons" / "procedural"


Color = tuple[float, float, float, float]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    t = clamp((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def angle_delta(left: float, right: float) -> float:
    delta = (left - right + math.pi) % (2.0 * math.pi) - math.pi
    return abs(delta)


def mix_channel(left: float, right: float, amount: float) -> float:
    return left * (1.0 - amount) + right * amount


def mix_color(left: Color, right: Color, amount: float) -> Color:
    return tuple(mix_channel(a, b, amount) for a, b in zip(left, right))


def pixel_shader(x: float, y: float, *, size: int, frame: int, frames: int) -> Color:
    center = (size - 1) / 2.0
    dx = x - center
    dy = y - center
    distance = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)
    highlight_angle = -math.pi / 2.0 + (2.0 * math.pi * frame / frames)

    radius = size * 0.325
    thickness = size * 0.105
    ring_edge = abs(distance - radius)
    ring_fill = 1.0 - smoothstep(thickness * 0.42, thickness * 0.58, ring_edge)
    if ring_fill <= 0.0:
        outer_glow = 1.0 - smoothstep(thickness * 0.70, thickness * 1.55, ring_edge)
        if outer_glow <= 0.0:
            return (0.0, 0.0, 0.0, 0.0)
        glow_color = (0.02, 0.40, 1.0, 0.20 * outer_glow)
        return glow_color

    sweep = math.exp(-((angle_delta(angle, highlight_angle) / 0.47) ** 2))
    tail = math.exp(-((angle_delta(angle, highlight_angle - 0.72) / 0.92) ** 2))
    bottom_shadow = 0.18 * smoothstep(-0.15, 0.9, math.sin(angle))
    radial_sheen = 1.0 - smoothstep(thickness * 0.08, thickness * 0.30, ring_edge)

    deep_blue: Color = (0.005, 0.12, 0.48, 1.0)
    electric_blue: Color = (0.00, 0.36, 1.0, 1.0)
    cyan: Color = (0.02, 0.95, 1.0, 1.0)
    white_cyan: Color = (0.78, 1.0, 1.0, 1.0)

    color = mix_color(deep_blue, electric_blue, 0.42 + 0.18 * math.cos(angle - 0.5))
    color = mix_color(color, cyan, clamp(0.18 * tail + 0.82 * sweep))
    color = mix_color(color, white_cyan, clamp(0.48 * sweep + 0.18 * radial_sheen))
    color = mix_color(color, deep_blue, bottom_shadow)

    alpha = ring_fill * (0.86 + 0.14 * radial_sheen)
    return (color[0], color[1], color[2], alpha)


def render_frame(*, size: int, samples: int, frame: int, frames: int) -> bytes:
    pixels = bytearray()
    sample_count = samples * samples
    for py in range(size):
        for px in range(size):
            premul_r = premul_g = premul_b = alpha = 0.0
            for sy in range(samples):
                for sx in range(samples):
                    sample_x = px + (sx + 0.5) / samples
                    sample_y = py + (sy + 0.5) / samples
                    r, g, b, a = pixel_shader(
                        sample_x,
                        sample_y,
                        size=size,
                        frame=frame,
                        frames=frames,
                    )
                    premul_r += r * a
                    premul_g += g * a
                    premul_b += b * a
                    alpha += a

            alpha /= sample_count
            if alpha > 0.0:
                r = premul_r / sample_count / alpha
                g = premul_g / sample_count / alpha
                b = premul_b / sample_count / alpha
            else:
                r = g = b = 0.0

            pixels.extend(
                (
                    round(clamp(r) * 255),
                    round(clamp(g) * 255),
                    round(clamp(b) * 255),
                    round(clamp(alpha) * 255),
                )
            )
    return bytes(pixels)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, *, size: int, rgba: bytes) -> None:
    scanlines = bytearray()
    stride = size * 4
    for offset in range(0, len(rgba), stride):
        scanlines.append(0)
        scanlines.extend(rgba[offset : offset + stride])

    payload = b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            png_chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0),
            ),
            png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9)),
            png_chunk(b"IEND", b""),
        )
    )
    path.write_bytes(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic blue prompt-orb PNG frames."
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--size", type=int, default=128)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--samples", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for frame in range(args.frames):
        rgba = render_frame(
            size=args.size,
            samples=args.samples,
            frame=frame,
            frames=args.frames,
        )
        output_path = args.out_dir / f"agent_driver_prompt_orb_frame_{frame + 1:02d}.png"
        write_png(output_path, size=args.size, rgba=rgba)
        print(f"Saved {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
