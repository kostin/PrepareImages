"""Проверка собранного исполняемого файла PrepareImages.

Создаёт временную папку с картинками разных пропорций, запускает бинарник и
проверяет, что в подпапке edited появились JPEG 762x1100 с 200 DPI, профилем
sRGB и без EXIF, а повторный запуск не размножает файлы.

Использование::

    python tests/smoke_test.py dist/prepare_images.exe
    python tests/smoke_test.py dist/prepare_images
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageCms

TARGET = (762, 1100)
EXPECTED_DPI = 200


def fail(message: str) -> None:
    print(f"ОШИБКА: {message}", file=sys.stderr)
    raise SystemExit(1)


def configure_streams() -> None:
    """UTF-8 вывод: приложение печатает по-русски, а консоль Windows бывает cp866/cp1252."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def make_images(folder: Path) -> dict[str, tuple[int, int]]:
    """Картинки разных пропорций, как в реальной работе."""
    sources = {
        "portrait.png": (900, 1200),      # выше целевых пропорций
        "landscape.jpg": (2600, 1000),    # шире целевых пропорций
        "alpha.png": (600, 900),          # с прозрачностью
        "noise.png": (1524, 2200),        # ровно в пропорциях цели
    }
    for name, size in sources.items():
        mode = "RGBA" if name == "alpha.png" else "RGB"
        image = Image.effect_noise(size, 90).convert("RGB")
        if mode == "RGBA":
            image.putalpha(128)
        image.save(folder / name)
    return sources


def run(binary: Path, folder: Path) -> str:
    process = subprocess.run(
        [str(binary), "--src", str(folder), "--no-pause"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if process.returncode != 0:
        print(process.stdout)
        print(process.stderr, file=sys.stderr)
        fail(f"приложение завершилось с кодом {process.returncode}")
    return process.stdout


def check_one(path: Path) -> None:
    if path.read_bytes()[:3] != b"\xff\xd8\xff":
        fail(f"{path.name} не является JPEG")
    with Image.open(path) as opened:
        opened.load()
        if opened.format != "JPEG":
            fail(f"{path.name}: формат {opened.format}")
        if opened.size != TARGET:
            fail(f"{path.name}: размер {opened.size}, ожидали {TARGET}")
        if opened.mode != "RGB":
            fail(f"{path.name}: режим {opened.mode}, ожидали RGB")
        if len(opened.getexif()) != 0:
            fail(f"{path.name}: остался EXIF ({len(opened.getexif())} тегов)")
        dpi = opened.info.get("dpi")
        if not dpi or any(abs(float(v) - EXPECTED_DPI) > 0.51 for v in dpi[:2]):
            fail(f"{path.name}: DPI {dpi}, ожидали {EXPECTED_DPI}")
        icc = opened.info.get("icc_profile")
        if not icc:
            fail(f"{path.name}: нет встроенного ICC-профиля")
        description = ImageCms.getProfileDescription(
            ImageCms.ImageCmsProfile(io.BytesIO(icc))
        ).strip()
        if "srgb" not in description.lower():
            fail(f"{path.name}: профиль {description!r} не sRGB")


def main(argv: list[str]) -> int:
    configure_streams()
    if len(argv) != 2:
        print(__doc__)
        return 2

    binary = Path(argv[1]).resolve()
    if not binary.is_file():
        fail(f"нет файла {binary}")

    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        sources = make_images(folder)

        output = run(binary, folder)
        print(output)

        edited = folder / "edited"
        if not edited.is_dir():
            fail(f"не создана папка {edited}")

        expected = {name.rsplit(".", 1)[0] + ".jpg" for name in sources}
        present = {path.name for path in edited.iterdir()}
        missing = expected - present
        if missing:
            fail(f"нет файлов {sorted(missing)}; в папке: {sorted(present)}")

        for name in sorted(expected):
            check_one(edited / name)

        first_run = {path.name for path in edited.iterdir()}
        run(binary, folder)
        second_run = {path.name for path in edited.iterdir()}
        if first_run != second_run:
            fail(f"повторный запуск добавил файлы: {sorted(second_run - first_run)}")

    print("SMOKE OK: бинарник работает, размер/DPI/профиль/метаданные в порядке")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
