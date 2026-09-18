#!/bin/sh
# Таблица «Образ | Версия» для описания выпуска.
#
# Задача выпуска ничего не ставит из сети — это её единственное свойство,
# ради которого она отделена от проверок. Поэтому разбор здесь на awk,
# а не на Python с pyyaml: иначе в задаче с правом записи в репозиторий
# пришлось бы выполнять установку пакетов. По той же причине здесь
# только POSIX sh и латинские имена: этот код должен работать в любой
# оболочке, которая окажется /bin/sh.
#
# Раньше то же делалось однострочником прямо в workflow:
#
#     grep -hoP '(image:|DOCKER_MODS=)\s*\K\S+'
#
# и выпуск 1.1.0 получил в таблице строку «, | ,». Виноват был комментарий
# в media/compose.yaml со словами «он ищет поля image:, а здесь» — после
# «image:» стояла запятая, её и приняли за образ. Теперь ключ ищется
# в начале строки: у комментария там решётка, и до ключа дело не доходит.
#
# Сверяется с разбором через pyyaml в tests/test_release_images.py: тест
# требует, чтобы этот скрипт и scripts/check_image_updates.py видели
# ровно один и тот же список образов.
set -eu

if [ "$#" -eq 0 ]; then
  echo "использование: $0 <compose.yaml> [...]" >&2
  exit 2
fi

echo '| Образ | Версия |'
echo '|---|---|'

awk '
  {
    line = $0
    if (match(line, /^[[:space:]]*image:[[:space:]]*/)) {
      ref = substr(line, RSTART + RLENGTH)
    } else if (match(line, /^[[:space:]]*-[[:space:]]*(DOCKER_MODS|UNIVERSAL_MODS)=/)) {
      ref = substr(line, RSTART + RLENGTH)
    } else {
      next
    }
    sub(/[[:space:]]+#.*$/, "", ref)   # комментарий в конце строки
    sub(/[[:space:]]+$/, "", ref)
    gsub(/["'"'"']/, "", ref)
    # В DOCKER_MODS модов может быть несколько, разделитель — вертикальная черта.
    parts = split(ref, part, "|")
    for (i = 1; i <= parts; i++) {
      if (part[i] != "") print part[i]
    }
  }
' "$@" | sort -u | while IFS= read -r ref; do
  case "${ref##*/}" in
    *:*) ;;
    *)
      echo "::error::у образа $ref нет тега" >&2
      exit 1
      ;;
  esac
  printf '| `%s` | `%s` |\n' "${ref%:*}" "${ref##*:}"
done
