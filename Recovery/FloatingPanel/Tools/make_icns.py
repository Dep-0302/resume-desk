#!/usr/bin/env python3
"""Package the project's PNG iconset into a macOS .icns file without Xcode."""

from __future__ import annotations

import struct
import sys
from pathlib import Path


# ICNS stores one PNG payload for each available pixel dimension.  The
# duplicated @2x filenames in an iconset map to the same physical rendition,
# so this list deliberately uses each size once.
ICON_CHUNKS = (
    ("icon_16x16.png", "icp4", 16),
    ("icon_16x16@2x.png", "icp5", 32),
    ("icon_32x32@2x.png", "icp6", 64),
    ("icon_128x128.png", "ic07", 128),
    ("icon_128x128@2x.png", "ic08", 256),
    ("icon_256x256@2x.png", "ic09", 512),
    ("icon_512x512@2x.png", "ic10", 1024),
)


def png_dimension(data: bytes, path: Path) -> int:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"不是有效 PNG：{path}")
    width, height = struct.unpack(">II", data[16:24])
    if width != height:
        raise ValueError(f"图标必须是正方形：{path}")
    return width


def build(iconset_dir: Path, output_path: Path) -> None:
    chunks: list[bytes] = []
    for filename, chunk_type, expected_dimension in ICON_CHUNKS:
        source = iconset_dir / filename
        data = source.read_bytes()
        if png_dimension(data, source) != expected_dimension:
            raise ValueError(f"图标尺寸不符合 {expected_dimension}×{expected_dimension}：{source}")
        chunks.append(chunk_type.encode("ascii") + struct.pack(">I", len(data) + 8) + data)

    payload = b"".join(chunks)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + payload)
    temporary_path.replace(output_path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("用法：make_icns.py ICONSET目录 输出.icns")
    build(Path(sys.argv[1]), Path(sys.argv[2]))
