#!/usr/bin/env python3
"""生成 app.ico，直接基于桌宠机器人主题首帧 pets/robot/normal_1.png。

注意：原图是低分辨率像素画（72x72），如果直接 thumbnail 到大尺寸，
机器人会只占画布中央一小点，导致桌面快捷方式/任务栏大图标模式下看不清。
因此这里先按透明度裁剪到内容包围盒，再按比例放大填充到每个目标尺寸。
"""

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "pets" / "robot" / "normal_1.png"
OUT_ICO = ROOT / "app.ico"
OUT_PNG = ROOT / "app.png"
SIZES = [256, 128, 64, 48, 32, 16]


def _content_bbox(src: Image.Image, margin: int = 2) -> tuple:
    """返回非透明内容的包围盒，可外加少量边距。"""
    if src.mode == "RGBA":
        alpha = src.split()[-1]
    elif src.mode == "LA":
        alpha = src.split()[-1]
    elif src.mode == "P":
        src = src.convert("RGBA")
        alpha = src.split()[-1]
    else:
        # 无透明通道则视为全图内容
        return (0, 0, src.width, src.height)
    bbox = alpha.getbbox()
    if not bbox:
        return (0, 0, src.width, src.height)
    x1, y1, x2, y2 = bbox
    x1 = max(0, x1 - margin)
    y1 = max(0, y1 - margin)
    x2 = min(src.width, x2 + margin)
    y2 = min(src.height, y2 + margin)
    return (x1, y1, x2, y2)


def _make_size(src: Image.Image, bbox: tuple, size: int) -> Image.Image:
    """把内容裁剪后等比缩放，填充到目标画布（保留约 92%  fill）。"""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cropped = src.crop(bbox)

    # 留 4% 边距，避免贴边显得拥挤
    pad = max(1, int(size * 0.04))
    max_w = size - 2 * pad
    max_h = size - 2 * pad

    ratio = min(max_w / cropped.width, max_h / cropped.height)
    new_w = max(1, int(cropped.width * ratio))
    new_h = max(1, int(cropped.height * ratio))

    # LANCZOS 对低分辨率像素画 upscale 会轻微平滑，但比保持原尺寸 tiny 更清晰可辨
    frame = cropped.resize((new_w, new_h), Image.LANCZOS)
    x = (size - new_w) // 2
    y = (size - new_h) // 2
    canvas.paste(frame, (x, y), frame)
    return canvas


def main() -> None:
    if not SRC.exists():
        raise FileNotFoundError(f"源图不存在: {SRC}")
    src = Image.open(SRC).convert("RGBA")
    bbox = _content_bbox(src)
    print(f"Source {SRC}: size={src.size}, content_bbox={bbox}")

    imgs = [_make_size(src, bbox, s) for s in SIZES]
    # Pillow 12.x 保存多帧 ICO 时 append_images 可能失效，这里先生成 PNG
    # 备用图标，再尝试多帧 ICO；若 ICO 仍为单帧，安装器会回退到 PNG 设置窗口图标。
    imgs[0].save(
        OUT_ICO,
        format="ICO",
        sizes=[(img.width, img.height) for img in imgs],
        append_images=imgs[1:],
    )
    # 同时生成一张 256x256 PNG，供 tkinter iconphoto 使用（更可靠）。
    imgs[0].save(OUT_PNG, format="PNG")
    print(f"Generated {OUT_ICO} and {OUT_PNG} from {SRC} with sizes {SIZES}")


if __name__ == "__main__":
    main()
