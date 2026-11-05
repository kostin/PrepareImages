#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_images.py — подготовка картинок для товарных карточек / маркетплейса.

Что делает:
  * берёт все картинки из папки, в которой лежит сам скрипт (или из --src);
  * создаёт обработанные копии в подпапке edited/ (или --out);
  * формат JPG, размер 762x1100 px, 200 DPI, режим RGB,
    цветовой профиль sRGB IEC61966-2.1;
  * картинки с другими пропорциями подрезаются по центру до нужных пропорций
    (без полей и без растягивания);
  * удаляются все метаданные: EXIF (в т.ч. GPS), IPTC, XMP, Photoshop-ресурсы,
    комментарии, миниатюры, данные камеры — то есть всё «личное» и служебное;
  * качество JPEG 95 %, субдискретизация 4:4:4 (низкая степень сжатия).

ВАЖНО про папку: по умолчанию обрабатывается та папка, В КОТОРОЙ ЛЕЖИТ САМ
СКРИПТ, а не текущая папка консоли. Поэтому «положить скрипт рядом с
картинками и запустить» работает всегда, откуда бы вы его ни запустили.

Окно консоли не закрывается само: в конце скрипт ждёт любую клавишу
("Нажмите любую кнопку для завершения..."), так что текст ошибок можно
спокойно прочитать и скопировать. Рядом с фотографиями никакие служебные
файлы не создаются. Для автоматизации (планировщик, конвейер) есть
флаг --no-pause.

Про цветовой профиль и DPI: это НЕ пользовательские метаданные, а
обязательные параметры результата, поэтому они, наоборот, записываются в файл —
sRGB кладётся в сегмент APP2 (ICC_PROFILE, 3144 байта, официальный профиль
Hewlett-Packard), а 200 DPI — в штатный заголовок JFIF (APP0). Всё остальное
чистится полностью: пиксели копируются в новый объект Image, и при сохранении
в файл не передаются ни exif, ни iptc, ни xmp, ни comment.

Примеры:
    python prepare_images.py                     # папка скрипта -> ./edited
    python prepare_images.py --src photos        # папка photos -> photos/edited
    python prepare_images.py --recursive         # обойти подпапки
    python prepare_images.py --size 1000x1500 --dpi 300 --quality 92
    python prepare_images.py --profile "C:\\Windows\\System32\\spool\\drivers\\color\\sRGB Color Space Profile.icm"

Запуск на macOS (важно): двойной клик по .py в Finder НЕ запускает скрипт —
macOS открывает файл в редакторе. Нужно запускать из Терминала:

    cd ~/Downloads/photos          # папка, куда положили скрипт и фото
    python3 -m pip install --user Pillow   # один раз, если Pillow нет
    python3 prepare_images.py

Или двойным кликом по файлу run_prepare_images.command (лежит рядом,
macOS открывает его в Терминале и сам ставит недостающий Pillow).

Запуск на Linux:

    python3 -m pip install --user Pillow
    python3 prepare_images.py

Требуется Pillow (pip install Pillow). Цветовые преобразования идут через
LittleCMS, который входит в Pillow; если картинка уже sRGB — конверсия
фактически ничего не меняет (identity), просто перезаписываются пиксели.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
import traceback
from dataclasses import dataclass, field

from PIL import Image, ImageCms, ImageOps

# Если установлен pillow-heif, скрипт читает и HEIC/HEIF (фото с iPhone).
# Без него всё работает как раньше, просто .heic не откроется.
try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:
    pass

# ----------------------------------------------------------------------------
# Параметры по умолчанию (можно переопределить аргументами командной строки)
# ----------------------------------------------------------------------------
DEFAULT_SIZE = "762x1100"      # Ш x В, px
DEFAULT_DPI = 200              # точек на дюйм
DEFAULT_QUALITY = 95           # качество JPEG, %
DEFAULT_SUBSAMPLING = 0        # 0 = 4:4:4 (без прореживания цветовых каналов)
DEFAULT_OUT = "edited"         # подпапка с результатами

# Папка, в которой лежит сам скрипт. Именно она обрабатывается по умолчанию —
# НЕ текущая папка консоли. Иначе при запуске из другого места (или двойным
# кликом, когда рабочая папка другая) скрипт искал бы картинки не там и молча
# ничего не делал.
#
# В собранном виде (PyInstaller) __file__ указывает во временную папку
# распаковки, поэтому там берём папку самого исполняемого файла: пользователь
# кладёт приложение рядом с фотографиями и запускает двойным кликом.
if getattr(sys, "frozen", False):
    PROGRAM_PATH = os.path.abspath(sys.executable)
    SCRIPT_DIR = os.path.dirname(PROGRAM_PATH)
else:
    PROGRAM_PATH = os.path.abspath(__file__)
    SCRIPT_DIR = os.path.dirname(PROGRAM_PATH)

# Расширения, которые пробуем читать. Pillow сам знает, что с ними делать.
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff",
    ".gif", ".ppm", ".pgm", ".pbm", ".tga", ".ico", ".heic", ".heif",
}

# Где искать официальный sRGB IEC61966-2.1 на разных системах.
SRGB_CANDIDATES = [
    r"C:\Windows\System32\spool\drivers\color\sRGB Color Space Profile.icm",
    r"C:\Windows\System32\spool\drivers\color\sRGB Color Space Profile.icc",
    "/usr/share/color/icc/sRGB.icc",
    "/usr/share/color/icc/colord/sRGB.icc",
    "/usr/share/color/icc/sRGB_Color_Space_Profile.icm",
    "/usr/local/share/color/icc/sRGB.icc",
    "/Library/ColorSync/Profiles/sRGB Profile.icc",
    "/System/Library/ColorSync/Profiles/sRGB Profile.icc",
]

# Тот же самый профиль sRGB IEC61966-2.1 (Hewlett-Packard, 3144 байта) прямо
# внутри скрипта: результат не зависит от того, какие профили установлены в
# системе, и собранное приложение не требует ничего доустанавливать.
SRGB_ICC_B64 = (
    "AAAMSExpbm8CEAAAbW50clJHQiBYWVogB84AAgAJAAYAMQAAYWNzcE1TRlQAAAAASUVDIHNS"
    "R0IAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1IUCAgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAARY3BydAAAAVAAAAAzZGVzYwAAAYQAAABsd3RwdAAA"
    "AfAAAAAUYmtwdAAAAgQAAAAUclhZWgAAAhgAAAAUZ1hZWgAAAiwAAAAUYlhZWgAAAkAAAAAU"
    "ZG1uZAAAAlQAAABwZG1kZAAAAsQAAACIdnVlZAAAA0wAAACGdmlldwAAA9QAAAAkbHVtaQAA"
    "A/gAAAAUbWVhcwAABAwAAAAkdGVjaAAABDAAAAAMclRSQwAABDwAAAgMZ1RSQwAABDwAAAgM"
    "YlRSQwAABDwAAAgMdGV4dAAAAABDb3B5cmlnaHQgKGMpIDE5OTggSGV3bGV0dC1QYWNrYXJk"
    "IENvbXBhbnkAAGRlc2MAAAAAAAAAEnNSR0IgSUVDNjE5NjYtMi4xAAAAAAAAAAAAAAASc1JH"
    "QiBJRUM2MTk2Ni0yLjEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAFhZWiAAAAAAAADzUQABAAAAARbMWFlaIAAAAAAAAAAAAAAAAAAAAABYWVog"
    "AAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAA"
    "ts9kZXNjAAAAAAAAABZJRUMgaHR0cDovL3d3dy5pZWMuY2gAAAAAAAAAAAAAABZJRUMgaHR0"
    "cDovL3d3dy5pZWMuY2gAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAZGVzYwAAAAAAAAAuSUVDIDYxOTY2LTIuMSBEZWZhdWx0IFJHQiBjb2xvdXIgc3Bh"
    "Y2UgLSBzUkdCAAAAAAAAAAAAAAAuSUVDIDYxOTY2LTIuMSBEZWZhdWx0IFJHQiBjb2xvdXIg"
    "c3BhY2UgLSBzUkdCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAGRlc2MAAAAAAAAALFJlZmVyZW5j"
    "ZSBWaWV3aW5nIENvbmRpdGlvbiBpbiBJRUM2MTk2Ni0yLjEAAAAAAAAAAAAAACxSZWZlcmVu"
    "Y2UgVmlld2luZyBDb25kaXRpb24gaW4gSUVDNjE5NjYtMi4xAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAB2aWV3AAAAAAATpP4AFF8uABDPFAAD7cwABBMLAANcngAAAAFYWVogAAAAAABM"
    "CVYAUAAAAFcf521lYXMAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAKPAAAAAnNpZyAAAAAA"
    "Q1JUIGN1cnYAAAAAAAAEAAAAAAUACgAPABQAGQAeACMAKAAtADIANwA7AEAARQBKAE8AVABZ"
    "AF4AYwBoAG0AcgB3AHwAgQCGAIsAkACVAJoAnwCkAKkArgCyALcAvADBAMYAywDQANUA2wDg"
    "AOUA6wDwAPYA+wEBAQcBDQETARkBHwElASsBMgE4AT4BRQFMAVIBWQFgAWcBbgF1AXwBgwGL"
    "AZIBmgGhAakBsQG5AcEByQHRAdkB4QHpAfIB+gIDAgwCFAIdAiYCLwI4AkECSwJUAl0CZwJx"
    "AnoChAKOApgCogKsArYCwQLLAtUC4ALrAvUDAAMLAxYDIQMtAzgDQwNPA1oDZgNyA34DigOW"
    "A6IDrgO6A8cD0wPgA+wD+QQGBBMEIAQtBDsESARVBGMEcQR+BIwEmgSoBLYExATTBOEE8AT+"
    "BQ0FHAUrBToFSQVYBWcFdwWGBZYFpgW1BcUF1QXlBfYGBgYWBicGNwZIBlkGagZ7BowGnQav"
    "BsAG0QbjBvUHBwcZBysHPQdPB2EHdAeGB5kHrAe/B9IH5Qf4CAsIHwgyCEYIWghuCIIIlgiq"
    "CL4I0gjnCPsJEAklCToJTwlkCXkJjwmkCboJzwnlCfsKEQonCj0KVApqCoEKmAquCsUK3Arz"
    "CwsLIgs5C1ELaQuAC5gLsAvIC+EL+QwSDCoMQwxcDHUMjgynDMAM2QzzDQ0NJg1ADVoNdA2O"
    "DakNww3eDfgOEw4uDkkOZA5/DpsOtg7SDu4PCQ8lD0EPXg96D5YPsw/PD+wQCRAmEEMQYRB+"
    "EJsQuRDXEPURExExEU8RbRGMEaoRyRHoEgcSJhJFEmQShBKjEsMS4xMDEyMTQxNjE4MTpBPF"
    "E+UUBhQnFEkUahSLFK0UzhTwFRIVNBVWFXgVmxW9FeAWAxYmFkkWbBaPFrIW1hb6Fx0XQRdl"
    "F4kXrhfSF/cYGxhAGGUYihivGNUY+hkgGUUZaxmRGbcZ3RoEGioaURp3Gp4axRrsGxQbOxtj"
    "G4obshvaHAIcKhxSHHscoxzMHPUdHh1HHXAdmR3DHeweFh5AHmoelB6+HukfEx8+H2kflB+/"
    "H+ogFSBBIGwgmCDEIPAhHCFIIXUhoSHOIfsiJyJVIoIiryLdIwojOCNmI5QjwiPwJB8kTSR8"
    "JKsk2iUJJTglaCWXJccl9yYnJlcmhya3JugnGCdJJ3onqyfcKA0oPyhxKKIo1CkGKTgpaymd"
    "KdAqAio1KmgqmyrPKwIrNitpK50r0SwFLDksbiyiLNctDC1BLXYtqy3hLhYuTC6CLrcu7i8k"
    "L1ovkS/HL/4wNTBsMKQw2zESMUoxgjG6MfIyKjJjMpsy1DMNM0YzfzO4M/E0KzRlNJ402DUT"
    "NU01hzXCNf02NzZyNq426TckN2A3nDfXOBQ4UDiMOMg5BTlCOX85vDn5OjY6dDqyOu87LTtr"
    "O6o76DwnPGU8pDzjPSI9YT2hPeA+ID5gPqA+4D8hP2E/oj/iQCNAZECmQOdBKUFqQaxB7kIw"
    "QnJCtUL3QzpDfUPARANER0SKRM5FEkVVRZpF3kYiRmdGq0bwRzVHe0fASAVIS0iRSNdJHUlj"
    "SalJ8Eo3Sn1KxEsMS1NLmkviTCpMcky6TQJNSk2TTdxOJU5uTrdPAE9JT5NP3VAnUHFQu1EG"
    "UVBRm1HmUjFSfFLHUxNTX1OqU/ZUQlSPVNtVKFV1VcJWD1ZcVqlW91dEV5JX4FgvWH1Yy1ka"
    "WWlZuFoHWlZaplr1W0VblVvlXDVchlzWXSddeF3JXhpebF69Xw9fYV+zYAVgV2CqYPxhT2Gi"
    "YfViSWKcYvBjQ2OXY+tkQGSUZOllPWWSZedmPWaSZuhnPWeTZ+loP2iWaOxpQ2maafFqSGqf"
    "avdrT2una/9sV2yvbQhtYG25bhJua27Ebx5veG/RcCtwhnDgcTpxlXHwcktypnMBc11zuHQU"
    "dHB0zHUodYV14XY+dpt2+HdWd7N4EXhueMx5KnmJeed6RnqlewR7Y3vCfCF8gXzhfUF9oX4B"
    "fmJ+wn8jf4R/5YBHgKiBCoFrgc2CMIKSgvSDV4O6hB2EgITjhUeFq4YOhnKG14c7h5+IBIhp"
    "iM6JM4mZif6KZIrKizCLlov8jGOMyo0xjZiN/45mjs6PNo+ekAaQbpDWkT+RqJIRknqS45NN"
    "k7aUIJSKlPSVX5XJljSWn5cKl3WX4JhMmLiZJJmQmfyaaJrVm0Kbr5wcnImc951kndKeQJ6u"
    "nx2fi5/6oGmg2KFHobaiJqKWowajdqPmpFakx6U4pammGqaLpv2nbqfgqFKoxKk3qamqHKqP"
    "qwKrdavprFys0K1ErbiuLa6hrxavi7AAsHWw6rFgsdayS7LCszizrrQltJy1E7WKtgG2ebbw"
    "t2i34LhZuNG5SrnCuju6tbsuu6e8IbybvRW9j74KvoS+/796v/XAcMDswWfB48JfwtvDWMPU"
    "xFHEzsVLxcjGRsbDx0HHv8g9yLzJOsm5yjjKt8s2y7bMNcy1zTXNtc42zrbPN8+40DnQutE8"
    "0b7SP9LB00TTxtRJ1MvVTtXR1lXW2Ndc1+DYZNjo2WzZ8dp22vvbgNwF3IrdEN2W3hzeot8p"
    "36/gNuC94UThzOJT4tvjY+Pr5HPk/OWE5g3mlucf56noMui86Ubp0Opb6uXrcOv77IbtEe2c"
    "7ijutO9A78zwWPDl8XLx//KM8xnzp/Q09ML1UPXe9m32+/eK+Bn4qPk4+cf6V/rn+3f8B/yY"
    "/Sn9uv5L/tz/bf//"
)


# ----------------------------------------------------------------------------
# Цветовой профиль
# ----------------------------------------------------------------------------
def _embedded_srgb() -> bytes | None:
    """Декодирует встроенный профиль sRGB IEC61966-2.1 (или None, если не вышло)."""
    if not SRGB_ICC_B64:
        return None
    try:
        data = base64.b64decode(SRGB_ICC_B64)
    except Exception:
        return None
    try:
        ImageCms.ImageCmsProfile(io.BytesIO(data))   # профиль читается lcms
    except Exception:
        return None
    return data


def load_srgb_profile(explicit_path: str | None) -> tuple[bytes, str]:
    """Возвращает (байты ICC-профиля, откуда взяли).

    Приоритет: явный --profile -> встроенный sRGB IEC61966-2.1 ->
    системный sRGB -> профиль, собранный LittleCMS.
    """
    if explicit_path:
        if not os.path.isfile(explicit_path):
            raise FileNotFoundError("профиль не найден: %s" % explicit_path)
        with open(explicit_path, "rb") as fh:
            data = fh.read()
        desc = ImageCms.getProfileDescription(ImageCms.getOpenProfile(explicit_path)).strip()
        return data, "%s (%s, %d байт)" % (explicit_path, desc, len(data))

    data = _embedded_srgb()
    if data:
        desc = ImageCms.getProfileDescription(
            ImageCms.ImageCmsProfile(io.BytesIO(data))
        ).strip()
        return data, "встроенный %s (%d байт)" % (desc, len(data))

    for path in SRGB_CANDIDATES:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                candidate = fh.read()
            try:
                desc = ImageCms.getProfileDescription(ImageCms.getOpenProfile(path)).strip()
            except Exception:
                continue
            return candidate, "%s (%s, %d байт)" % (path, desc, len(candidate))

    # Последний резерв: профиль, который LittleCMS собирает сам.
    prof = ImageCms.createProfile("sRGB")
    data = ImageCms.ImageCmsProfile(prof).tobytes()
    return data, "LittleCMS createProfile('sRGB') (%d байт)" % len(data)


def to_srgb_rgb(im: Image.Image, srgb_bytes: bytes, warnings: list[str]) -> Image.Image:
    """Приводит картинку к RGB в sRGB: раскладывает прозрачность на белый,
    при необходимости конвертирует из другого профиля (AdobeRGB, CMYK, Gray и т.п.)."""
    # 1. Прозрачность -> белый фон (иначе JPEG станет чёрным в прозрачных местах).
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        im = bg.convert("RGB")
        warnings.append("прозрачность уложена на белый фон")
    elif im.mode != "RGB":
        im = im.convert("RGB")

    # 2. Цветовое преобразование в sRGB, если в источнике другой профиль.
    icc = im.info.get("icc_profile")
    if icc:
        try:
            src_prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst_prof = ImageCms.ImageCmsProfile(io.BytesIO(srgb_bytes))
            intent = getattr(ImageCms.Intent, "RELATIVE_COLORIMETRIC", 1)
            im = ImageCms.profileToProfile(
                im, src_prof, dst_prof, outputMode="RGB", renderingIntent=intent
            )
            warnings.append("профиль источника приведён к sRGB")
        except Exception as exc:  # битый/экзотический профиль — просто игнорируем его
            warnings.append("профиль источника не применён (%s), считаем sRGB" % exc)
    return im


# ----------------------------------------------------------------------------
# Геометрия: вписать в размер с обрезкой лишнего (crop-to-fill)
# ----------------------------------------------------------------------------
def fit_cover(im: Image.Image, target_w: int, target_h: int) -> tuple[Image.Image, str]:
    """Масштабирует картинку так, чтобы она полностью покрыла target_w x target_h,
    и обрезает лишнее по центру. Пропорции сохраняются, полей не появляется."""
    w, h = im.size
    if (w, h) == (target_w, target_h):
        return im, "уже нужного размера"

    scale = max(target_w / float(w), target_h / float(h))
    new_w = max(target_w, int(round(w * scale)))
    new_h = max(target_h, int(round(h * scale)))
    resized = im.resize((new_w, new_h), Image.LANCZOS, reducing_gap=3.0)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    cropped = resized.crop((left, top, left + target_w, top + target_h))

    if (new_w, new_h) == (target_w, target_h):
        note = "масштаб %.1f%%, без обрезки" % (scale * 100)
    else:
        note = "масштаб %.1f%%, обрезано %dpx по ширине и %dpx по высоте" % (
            scale * 100, new_w - target_w, new_h - target_h
        )
    return cropped, note


# ----------------------------------------------------------------------------
# Сохранение без метаданных
# ----------------------------------------------------------------------------
def save_clean_jpeg(
    im: Image.Image,
    dst_path: str,
    quality: int,
    dpi: int,
    subsampling: int,
    srgb_bytes: bytes,
) -> None:
    """Пишет JPEG, в котором нет ничего, кроме пикселей, таблиц JPEG,
    профиля sRGB (APP2) и DPI в заголовке JFIF (APP0).

    Ключевой момент: пиксели переносятся в совершенно новый объект Image,
    у которого нет ни info, ни exif, ни прочих атрибутов источника, поэтому
    даже случайно унаследовать метаданные невозможно.
    """
    w, h = im.size
    clean = Image.new("RGB", (w, h))
    source_data = (
        im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()
    )
    clean.putdata(list(source_data))

    # exif / comment / icc_profile/iptc/xmp намеренно НЕ передаём,
    # кроме sRGB-профиля, который является требуемым параметром результата.
    clean.save(
        dst_path,
        format="JPEG",
        quality=quality,
        subsampling=subsampling,
        optimize=True,
        progressive=False,
        dpi=(dpi, dpi),
        icc_profile=srgb_bytes,
    )
    clean.close()


# ----------------------------------------------------------------------------
# Проверка результата
# ----------------------------------------------------------------------------
FORBIDDEN_MARKERS = {
    0xE1: "APP1 (EXIF/XMP)",
    0xE3: "APP3",
    0xE4: "APP4",
    0xE5: "APP5",
    0xE6: "APP6",
    0xE7: "APP7",
    0xE8: "APP8",
    0xE9: "APP9",
    0xEA: "APP10",
    0xEB: "APP11",
    0xEC: "APP12 (Ducky/Photoshop)",
    0xED: "APP13 (IPTC/Photoshop)",
    0xEE: "APP14 (Adobe)",
    0xEF: "APP15",
    0xFE: "COM (комментарий)",
}


def jpeg_markers(path: str) -> list[int]:
    """Список кодов сегментов маркеров JPEG (E0=APP0, DB=квантование, DA=данные...)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"\xff\xd8":
        return []
    i, markers = 2, []
    while i < len(data) - 1:
        if data[i] != 0xFF:
            break
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        m = data[i]
        i += 1
        markers.append(m)
        if m == 0xDA:  # начало энтропийно-кодированных данных
            break
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            continue
        length = int.from_bytes(data[i:i + 2], "big")
        i += length
    return markers


def verify_output(path: str, size: tuple[int, int], dpi: int, expect_profile: bool) -> list[str]:
    """Возвращает список проблем (пустой = всё хорошо)."""
    problems = []
    with Image.open(path) as im:
        im.load()
        if im.format != "JPEG":
            problems.append("формат %s, а не JPEG" % im.format)
        if im.size != size:
            problems.append("размер %dx%d, а нужен %dx%d" % (im.size[0], im.size[1], size[0], size[1]))
        if im.mode != "RGB":
            problems.append("режим %s, а нужен RGB" % im.mode)
        if len(im.getexif()):
            problems.append("в EXIF осталось %d тегов" % len(im.getexif()))

        saved_dpi = im.info.get("dpi")
        if not saved_dpi or not all(abs(float(v) - dpi) < 0.51 for v in saved_dpi[:2]):
            problems.append("DPI %s, а нужен %d" % (saved_dpi, dpi))

        icc = im.info.get("icc_profile")
        if expect_profile:
            if not icc:
                problems.append("нет встроенного ICC-профиля")
            else:
                try:
                    desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
                except Exception:
                    desc = "?"
                if "srgb" not in desc.lower():
                    problems.append("профиль %r не sRGB" % desc)

    bad = [FORBIDDEN_MARKERS[m] for m in jpeg_markers(path) if m in FORBIDDEN_MARKERS]
    if bad:
        problems.append("лишние сегменты: %s" % ", ".join(bad))
    return problems


# ----------------------------------------------------------------------------
# Обход папки и основная логика
# ----------------------------------------------------------------------------
@dataclass
class Stats:
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def collect_sources(src_dir: str, out_dir: str, recursive: bool) -> list[str]:
    """Собирает пути к картинкам, пропуская папку результатов."""
    out_abs = os.path.abspath(out_dir)
    found: list[str] = []

    def walk(directory: str) -> None:
        try:
            entries = sorted(os.listdir(directory))
        except OSError as exc:
            print("  ! не читается папка %s: %s" % (directory, exc))
            return
        for name in entries:
            path = os.path.join(directory, name)
            if os.path.isdir(path):
                if recursive and os.path.abspath(path) != out_abs:
                    walk(path)
                continue
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS and os.path.isfile(path):
                found.append(path)

    walk(src_dir)
    return found


def unique_target_name(stem: str, used: set) -> str:
    """Подбирает имя внутри одного запуска: photo.jpg, photo_2.jpg, ...

    Уже существующие файлы в edited/ намеренно не учитываются: повторный
    запуск должен перезаписывать результаты, а не плодить копии _2, _3.
    Суффикс появляется только когда в исходной папке лежат тёзки с разными
    расширениями (например, photo.jpg и photo.png).
    """
    candidate = stem + ".jpg"
    n = 2
    while candidate.lower() in used:
        candidate = "%s_%d.jpg" % (stem, n)
        n += 1
    used.add(candidate.lower())
    return candidate


def process_one(
    src_path: str,
    dst_path: str,
    size: tuple[int, int],
    dpi: int,
    quality: int,
    subsampling: int,
    srgb_bytes: bytes,
) -> tuple[str, list[str]]:
    """Обрабатывает один файл. Возвращает (пояснение по геометрии, замечания)."""
    notes: list[str] = []
    with Image.open(src_path) as opened:
        opened.load()
        src_format, src_mode, src_size = opened.format, opened.mode, opened.size
        icc_desc = ""
        if opened.info.get("icc_profile"):
            try:
                icc_desc = ImageCms.getProfileDescription(
                    ImageCms.ImageCmsProfile(io.BytesIO(opened.info["icc_profile"]))
                ).strip()
            except Exception:
                icc_desc = "нечитаемый"

        # EXIF-поворот применяем ДО обрезки, иначе кадр будет повёрнут неправильно.
        try:
            im = ImageOps.exif_transpose(opened)
        except Exception:
            im = opened
        if im is not opened:
            notes.append("применён поворот по EXIF")

        im = to_srgb_rgb(im, srgb_bytes, notes)
        fitted, geometry = fit_cover(im, size[0], size[1])
        save_clean_jpeg(fitted, dst_path, quality, dpi, subsampling, srgb_bytes)

    notes.insert(0, "%s %s %dx%d%s" % (
        src_format, src_mode, src_size[0], src_size[1],
        (" | профиль источника: %s" % icc_desc) if icc_desc else "",
    ))
    notes.append(geometry)
    return geometry, notes


def parse_size(text: str) -> tuple[int, int]:
    parts = text.lower().replace("х", "x").split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("размер указывается как ШxВ, например 762x1100")
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError("размер указывается как ШxВ, например 762x1100")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("ширина и высота должны быть больше нуля")
    return w, h


def human_size(num_bytes: int | None) -> str:
    """Размер файла по-человечески: 812 Б / 192 КБ / 1.9 МБ."""
    if num_bytes is None:
        return "размер н/д"
    if num_bytes < 1024:
        return "%d Б" % num_bytes
    kb = num_bytes / 1024.0
    if kb < 1023.5:            # чтобы 1 048 575 Б не превращались в «1024 КБ»
        return "%.0f КБ" % kb
    return "%.1f МБ" % (num_bytes / (1024.0 * 1024.0))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Картинки из папки скрипта -> edited/: JPG, 762x1100, 200 DPI, sRGB, q95, без метаданных.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "По умолчанию обрабатывается папка, в которой лежит сам скрипт,\n"
            "результаты кладутся в её подпапку edited.\n"
            "Картинки с другими пропорциями подрезаются по центру, поля не добавляются."
        ),
    )
    p.add_argument("--src", default=None,
                   help="папка с исходниками (по умолчанию: папка, где лежит скрипт)")
    p.add_argument("--out", default=DEFAULT_OUT, help="подпапка для результатов (по умолчанию: edited)")
    p.add_argument("--size", default=DEFAULT_SIZE, type=parse_size, help="ШxВ в пикселях (по умолчанию: 762x1100)")
    p.add_argument("--dpi", default=DEFAULT_DPI, type=int, help="разрешение, DPI (по умолчанию: 200)")
    p.add_argument("--quality", default=DEFAULT_QUALITY, type=int, help="качество JPEG 1..100 (по умолчанию: 95)")
    p.add_argument("--subsampling", default=DEFAULT_SUBSAMPLING, type=int, choices=(0, 1, 2),
                   help="0=4:4:4, 1=4:2:2, 2=4:2:0 (по умолчанию: 0)")
    p.add_argument("--profile", default=None, help="путь к ICC-профилю sRGB (по умолчанию: системный)")
    p.add_argument("--recursive", action="store_true", help="обходить вложенные папки")
    p.add_argument("--no-verify", action="store_true", help="не проверять результат после сохранения")
    p.add_argument("--no-pause", action="store_true",
                   help="не ждать клавишу в конце (для автоматизации, без окна)")
    p.add_argument("--clean", action="store_true",
                   help="перед обработкой удалить прежние картинки из папки результатов")
    return p


def clean_output_dir(out_dir: str) -> int:
    """Удаляет прежние картинки из папки результатов (только файлы-картинки).

    Нужно, когда исходники переименовали или удалили: иначе в edited/ остаются
    файлы от прошлых запусков, которых уже нет среди источников.
    """
    removed = 0
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                try:
                    os.remove(os.path.join(root, name))
                    removed += 1
                except OSError:
                    pass
    return removed


PAUSE_MESSAGE = "Нажмите любую кнопку для завершения..."


def pause_any_key(message: str = PAUSE_MESSAGE) -> None:
    """Ждёт любую клавишу, чтобы окно консоли не закрылось до прочтения вывода.

    Выполняется, только если есть настоящая консоль (stdin — терминал):
    при запуске из планировщика, из IDE или по конвейеру ничего не блокируем.
    Никаких файлов при этом не создаётся.
    """
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return
    except Exception:
        return

    try:
        sys.stdout.write("\n" + message + "\n")
        sys.stdout.flush()
    except Exception:
        pass

    try:
        if os.name == "nt":
            import msvcrt  # любая клавиша, без Enter
            while msvcrt.kbhit():   # выкидываем случайные нажатия из буфера
                msvcrt.getwch()
            msvcrt.getwch()
        else:
            input()
    except Exception:
        # Консоли может не быть (перенаправленный ввод) — молча выходим.
        try:
            input()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_arg_parser().parse_args(argv)

    # Не падаем на выводе в консоль с «узкой» кодировкой (cp866/cp1251 и пр.).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    # По умолчанию работаем с папкой, где лежит сам скрипт, а не с текущей
    # папкой консоли: именно из-за этого прошлый запуск «ничего не сделал».
    src_dir = os.path.abspath(args.src) if args.src else SCRIPT_DIR
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(src_dir, args.out)
    size = args.size if isinstance(args.size, tuple) else parse_size(args.size)

    print("Скрипт:      %s" % PROGRAM_PATH)
    print("Исходники:   %s" % src_dir)
    print("Результат:   %s" % out_dir)
    print("Запуск из:   %s" % os.getcwd())
    print("Python:      %s%s" % (sys.version.split()[0],
                                 " (собранное приложение)" if getattr(sys, "frozen", False) else ""))

    if not os.path.isdir(src_dir):
        print("ОШИБКА: папка с исходниками не найдена: %s" % src_dir)
        return 2
    if not (1 <= args.quality <= 100):
        print("ОШИБКА: качество должно быть в диапазоне 1..100")
        return 2

    try:
        srgb_bytes, srgb_source = load_srgb_profile(args.profile)
    except Exception as exc:
        print("ОШИБКА: не удалось загрузить цветовой профиль: %s" % exc)
        return 2

    print("Параметры:   JPG, %dx%d px, %d DPI, RGB, q%d, subsampling %s" % (
        size[0], size[1], args.dpi, args.quality,
        {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}[args.subsampling],
    ))
    print("Профиль:     %s" % srgb_source)
    print()

    sources = collect_sources(src_dir, out_dir, args.recursive)
    if not sources:
        print("Картинок в папке не найдено: %s" % src_dir)
        print("Положите скрипт в папку с картинками или укажите её: --src \"путь\\к\\папке\"")
        return 0

    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        print("ОШИБКА: не удалось создать папку результатов %s: %s" % (out_dir, exc))
        return 2

    if args.clean:
        removed = clean_output_dir(out_dir)
        print("Очищено прежних файлов в %s: %d" % (out_dir, removed))
        print()

    stats = Stats()
    used_names: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    total_bytes = 0

    for src_path in sources:
        rel = os.path.relpath(src_path, src_dir)
        rel_dir = os.path.dirname(rel)
        target_dir = os.path.join(out_dir, rel_dir) if rel_dir else out_dir
        os.makedirs(target_dir, exist_ok=True)

        stem = os.path.splitext(os.path.basename(src_path))[0]
        dst_name = unique_target_name(stem, used_names)
        dst_path = os.path.join(target_dir, dst_name)

        try:
            _, notes = process_one(
                src_path, dst_path, size, args.dpi, args.quality,
                args.subsampling, srgb_bytes,
            )
            size_bytes = os.path.getsize(dst_path) if os.path.exists(dst_path) else None
            problems = [] if args.no_verify else verify_output(dst_path, size, args.dpi, True)
            if problems:
                stats.failed += 1
                rows.append(("FAIL", rel, dst_name,
                             "%s | %s | %s" % (notes[-1], human_size(size_bytes), "; ".join(problems))))
            else:
                stats.ok += 1
                total_bytes += size_bytes or 0
                check = "проверено" if args.no_verify is False else "без проверки"
                rows.append(("OK", rel, dst_name,
                             "%s | %s | %s" % (notes[-1], human_size(size_bytes), check)))
        except Exception as exc:
            stats.failed += 1
            rows.append(("FAIL", rel, dst_name, "%s: %s" % (type(exc).__name__, exc)))
            if os.environ.get("PREPARE_IMAGES_DEBUG"):
                traceback.print_exc()

    width_src = max([len(r[1]) for r in rows] + [8])
    width_dst = max([len(r[2]) for r in rows] + [10])
    for status, rel, dst_name, note in rows:
        print("[%s] %-*s -> %-*s  %s" % (status, width_src, rel, width_dst, dst_name, note))

    print()
    print("Готово: успешно %d, с ошибками %d, пропущено %d" % (stats.ok, stats.failed, stats.skipped))
    if stats.ok:
        print("Суммарный размер результата: %s" % human_size(total_bytes))
    if stats.failed:
        print("Часть файлов не обработана — смотрите строки [FAIL] выше.")
    print("Результаты лежат в: %s" % out_dir)
    return 0 if stats.failed == 0 else 1


def run(argv: list[str] | None = None) -> int:
    """Запускает main и всегда оставляет окно открытым до нажатия клавиши."""
    argv = list(sys.argv[1:] if argv is None else argv)
    no_pause = "--no-pause" in argv or bool(os.environ.get("PREPARE_IMAGES_NO_PAUSE"))
    show_help = any(a in ("-h", "--help") for a in argv)

    try:
        code = main(argv)
    except SystemExit as exc:      # argparse: --help, неверные аргументы
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        code = 130
    except BaseException:
        # Любая непредвиденная ошибка должна остаться на экране, а не исчезнуть.
        traceback.print_exc()
        print("\nНепредвиденная ошибка — текст выше можно скопировать.")
        code = 1

    if not no_pause and not show_help:
        pause_any_key()
    return code


if __name__ == "__main__":
    raise SystemExit(run())
