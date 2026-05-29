"""Generate Agent Driver prompt icon frames through OpenRouter image models."""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
from urllib.parse import urljoin

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "openai/gpt-5.4-image-2"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OUT_DIR = REPO_ROOT / "generated" / "prompt-icons" / "openrouter"

STYLE_PROMPT = (
    "Create a premium CLI prompt icon for an AI coding agent named Agent Driver. "
    "The image is one frame in an 8-frame looping terminal spinner. "
    "A small circular orb / ring symbol, centered on a transparent background, "
    "designed to look excellent at tiny terminal prompt size. Deep blue to electric "
    "cyan gradient, subtle inner glow, crisp vector-like edges, minimal futuristic "
    "style, friendly but technical. No text, no letters, no logo marks, no external "
    "drop shadow, no background. Square PNG, transparent background, high contrast, "
    "readable at 16x16 and 32x32 pixels."
)

FRAME_PROMPTS = [
    (
        "Frame 1 of 8. Keep the orb/ring perfectly centered. Place the brightest "
        "cyan highlight at the top of the circular ring."
    ),
    (
        "Frame 2 of 8. Keep the exact same icon, size, style, colors, transparent "
        "background, and composition. Move only the brightest cyan highlight to the "
        "upper-right position of the ring."
    ),
    (
        "Frame 3 of 8. Keep everything identical except the animation phase. Move "
        "only the brightest cyan highlight to the right side of the ring."
    ),
    (
        "Frame 4 of 8. Keep everything identical except the animation phase. Move "
        "only the brightest cyan highlight to the lower-right position of the ring."
    ),
    (
        "Frame 5 of 8. Keep everything identical except the animation phase. Move "
        "only the brightest cyan highlight to the bottom of the ring."
    ),
    (
        "Frame 6 of 8. Keep everything identical except the animation phase. Move "
        "only the brightest cyan highlight to the lower-left position of the ring."
    ),
    (
        "Frame 7 of 8. Keep everything identical except the animation phase. Move "
        "only the brightest cyan highlight to the left side of the ring."
    ),
    (
        "Frame 8 of 8. Keep everything identical except the animation phase. Move "
        "only the brightest cyan highlight to the upper-left position of the ring."
    ),
]


def load_local_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def image_bytes_from_url(value: str, client: httpx.Client) -> bytes:
    if value.startswith("data:"):
        _, encoded = value.split(",", 1)
        return base64.b64decode(encoded)

    response = client.get(value)
    response.raise_for_status()
    return response.content


def extract_image_urls(response_payload: dict) -> list[str]:
    try:
        message = response_payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected response payload: {response_payload}") from exc

    images = message.get("images") or []
    image_urls: list[str] = []
    for image in images:
        image_url = image.get("image_url") if isinstance(image, dict) else None
        url = image_url.get("url") if isinstance(image_url, dict) else None
        if url:
            image_urls.append(url)
    return image_urls


def generate_frame(
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
) -> bytes:
    endpoint = urljoin(base_url.rstrip("/") + "/", "chat/completions")
    response = client.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Agent Driver prompt icon generator",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        },
    )
    response.raise_for_status()
    image_urls = extract_image_urls(response.json())
    if not image_urls:
        raise RuntimeError("OpenRouter response did not include generated images.")
    return image_bytes_from_url(image_urls[0], client)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate 8 PNG frames for the Agent Driver CLI prompt icon."
    )
    parser.add_argument("--model", default=os.getenv("AGENT_DRIVER_IMAGE_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--base-url",
        default=os.getenv("AGENT_DRIVER_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    load_local_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    api_key = os.getenv("AGENT_DRIVER_API_KEY")
    if not api_key:
        print("Missing OpenRouter API key. Set AGENT_DRIVER_API_KEY.")
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(args.timeout, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        for index, frame_prompt in enumerate(FRAME_PROMPTS, start=1):
            prompt = f"{STYLE_PROMPT}\n\n{frame_prompt}"
            print(f"Generating frame {index:02d}/08...", flush=True)
            image_bytes = generate_frame(
                client=client,
                api_key=api_key,
                base_url=args.base_url,
                model=args.model,
                prompt=prompt,
            )
            output_path = args.out_dir / f"agent_driver_prompt_orb_frame_{index:02d}.png"
            output_path.write_bytes(image_bytes)
            print(f"Saved {output_path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
