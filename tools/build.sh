#!/usr/bin/env bash
# Сборка PrepareImages для macOS (или Linux) в один исполняемый файл.
#
#   ./tools/build.sh                 # сборка
#   PYTHON=python3.12 ./tools/build.sh
#
# Результат: dist/prepare_images — внутрь упакованы Python и Pillow,
# на целевой машине ничего устанавливать не нужно.

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

python_bin="${PYTHON:-python3}"

echo "== Зависимости =="
if ! "$python_bin" -c "import PIL, PyInstaller" >/dev/null 2>&1; then
  echo "Устанавливаю зависимости из requirements.txt..."
  "$python_bin" -m pip install --user --upgrade -r requirements.txt
fi

echo "== Сборка =="
"$python_bin" -m PyInstaller --noconfirm --clean PrepareImages.spec

binary="dist/prepare_images"
if [[ ! -f "$binary" ]]; then
  echo "Не найден $binary" >&2
  exit 1
fi
chmod +x "$binary"

echo
echo "Готово: $binary ($(du -h "$binary" | cut -f1))"
echo "Проверка: python3 tests/smoke_test.py $binary"
