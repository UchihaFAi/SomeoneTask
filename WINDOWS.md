# Пошаговая инструкция для Windows

Всё делается в PowerShell. Открыть его: `Win + X` → **Terminal** (или **Windows PowerShell**).
Права администратора не нужны.

> **Состояние на 12.10.2026.** Последний релиз Comlink — **v4.5.0 от 7 августа 2026**,
> новых за два месяца не выходило. **Windows-бинарник этого релиза не запускается**
> (падает с `V8 rejected the bytecode cache`), файлы релиза с момента публикации не
> перезаливали, issue в проекте никто не завёл. Поэтому основной путь ниже —
> **Docker**: тот же код, но собранный под Linux, где дефекта нет. Подробности
> о баге — в конце документа.

---

## Шаг 1. Установить Python

Проверьте, есть ли он уже:

```powershell
python --version
```

Если версия не вывелась (или открылся Microsoft Store) — поставьте Python с
[python.org/downloads](https://www.python.org/downloads/). В установщике
обязательно отметьте галочку **«Add python.exe to PATH»** на первом экране,
иначе команда `python` не заработает.

После установки закройте и откройте PowerShell заново, затем проверьте снова.
Нужен Python 3.9 или новее — более свежий не требуется.

> Если `python` ругается, но работает `py` — дальше везде подставляйте `py`
> вместо `python`.

---

## Шаг 2. Поднять Comlink

Два пути. Начните с варианта А.

### Вариант А — Docker (рекомендуется)

Контейнер собирается под Linux, поэтому баг Windows-сборки его не касается.
Образ `latest` соответствует тому же релизу v4.5.0.

Поставьте [Docker Desktop](https://www.docker.com/products/docker-desktop/),
дождитесь, пока в трее он покажет **Running**, затем:

```powershell
docker run -d --name swgoh-comlink -p 3000:3000 `
  -e APP_NAME=my-roster-export `
  ghcr.io/swgoh-utils/swgoh-comlink:latest
```

Обратная кавычка ``` ` ``` в конце строк — это перенос команды в PowerShell.
Можно и одной строкой без них.

Контейнер работает в фоне, отдельное окно держать открытым не нужно. Полезное:

```powershell
docker logs swgoh-comlink          # посмотреть журнал
docker stop swgoh-comlink          # остановить
docker start swgoh-comlink         # запустить снова
docker rm -f swgoh-comlink         # удалить совсем
```

### Вариант Б — бинарник (если Docker ставить не хочется)

v4.5.0 под Windows нерабочий, поэтому берём предыдущий релиз
[v4.4.2](https://github.com/swgoh-utils/swgoh-comlink/releases/tag/v4.4.2)
(3 августа 2026, с обновлением под TU42 — по игровым данным актуален).
В разделе **Assets** скачайте `swgoh-comlink-win-4.4.2.zip`.

```powershell
mkdir "$HOME\swgoh"
cd "$HOME\swgoh"
# переместите скачанный zip в эту папку, затем:
Unblock-File .\swgoh-comlink-win-4.4.2.zip
Expand-Archive .\swgoh-comlink-win-4.4.2.zip -DestinationPath .
Get-ChildItem *.exe
```

`Unblock-File` снимает метку «скачано из интернета», иначе Windows не даст
запустить. Последняя команда покажет имя exe — дальше подставляйте своё.

Сначала быстрая проверка, что бинарник вообще стартует:

```powershell
.\swgoh-comlink-4.4.2.exe --check
```

Прошла — запускайте сервис (**это окно оставьте открытым**, закроете —
Comlink остановится):

```powershell
.\swgoh-comlink-4.4.2.exe --name my-roster-export --port 3000
```

Точка с обратным слэшем `.\` обязательна — без неё PowerShell не запускает
файлы из текущей папки. Что может всплыть:

* **«Windows protected your PC» (SmartScreen)** → **More info** → **Run anyway**.
  Обычная реакция на неподписанный бинарник.
* **Запрос Windows Firewall** — хватит доступа в **частной** сети. Можно и
  отклонить: Comlink слушает localhost, на локальные подключения это не влияет.
* **Антивирус удалил файл** — добавьте папку в исключения и распакуйте заново.

Если и v4.4.2 падает с той же ошибкой про байткод — попробуйте v4.4.1, а потом
всё-таки вариант А.

---

## Шаг 3. Убедиться, что Comlink отвечает

В **новом** окне PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:3000/metadata -Method Post
```

Должны вернуться игровые метаданные, среди них `latestGamedataVersion`.
Ошибка подключения — Comlink не поднялся: смотрите `docker logs swgoh-comlink`
(вариант А) или окно с бинарником (вариант Б).

> Порт 3000 занят? В варианте А поменяйте первое число: `-p 3100:3000`.
> В варианте Б запустите с `--port 3100`. И там и там дальше добавляйте к
> скрипту `--url http://localhost:3100`.

---

## Шаг 4. Скачать скрипт

Если установлен git:

```powershell
cd "$HOME"
git clone -b claude/md-file-actions-6eixrc https://github.com/UchihaFAi/SomeoneTask.git
cd SomeoneTask
```

Если git не установлен — скачайте [ZIP-архив ветки](https://github.com/UchihaFAi/SomeoneTask/archive/refs/heads/claude/md-file-actions-6eixrc.zip),
распакуйте и перейдите в папку:

```powershell
cd "$HOME\Downloads\SomeoneTask-claude-md-file-actions-6eixrc"
```

---

## Шаг 5. Установить зависимость

```powershell
python -m pip install -r requirements.txt
```

Ставится одна библиотека — `requests`.

> Жалуется на права — добавьте `--user`:
> `python -m pip install --user -r requirements.txt`
>
> Пишет, что `requests` уже есть, но скрипт его не видит — значит `pip` и
> `python` от разных установок Python. Команда выше (`python -m pip`) как раз
> от этого и страхует: она ставит ровно в тот интерпретатор, которым запускается
> скрипт.

Необязательно, но можно сразу убедиться, что всё цело:

```powershell
python -m unittest discover -s tests
```

Ожидается `OK` и 26 пройденных тестов. Сеть и Comlink для них не нужны.

---

## Шаг 6. Выгрузить ростер

Ally Code — ваш номер в игре (Профиль → Ally Code), **девять цифр без дефисов**:

```powershell
python fetch_roster.py 123456789
```

Скрипт по шагам напечатает: проверку Comlink, сохранение сырого ответа,
загрузку игровых данных (может занять минуту — файл большой), сборку и статистику.

Чтобы не светить код в истории команд:

```powershell
$env:SWGOH_ALLY_CODE = "123456789"
python fetch_roster.py
```

---

## Шаг 7. Забрать результат

| Файл | Что это |
|---|---|
| `roster_raw.json` | сырой ответ игрового API |
| `roster_clean.json` | **персонажи — это грузить в Claude Project** |
| `ships_clean.json` | корабли |

```powershell
explorer .                                            # открыть папку
python -m json.tool roster_clean.json > $null; if ($?) { "OK" }   # проверить JSON
```

---

## Шаг 8. Что прислать мне

Пришлите **`roster_raw.json`** — сверю реальные имена полей с тем, что ожидает
скрипт, поправлю парсинг под вашу версию API и соберу финальный `roster_clean.json`.

Заодно посмотрите в выводе строку **«Всего зет»** и сравните с количеством зет
в игре. Расходится — напишите оба числа, поправлю смещение тиров.

---

## Если что-то пошло не так

| Симптом | Что делать |
|---|---|
| `python` не распознан | Python не в PATH — переустановите с галочкой «Add python.exe to PATH» или используйте `py` |
| `ModuleNotFoundError: requests` | Ставьте через `python -m pip install -r requirements.txt`, см. Шаг 5 |
| `V8 rejected the bytecode cache` | Битая сборка v4.5.0 — Docker или v4.4.2, см. Шаг 2 |
| `.\swgoh-comlink...exe` не распознан | Не та папка (`cd "$HOME\swgoh"`) или другое имя exe (`Get-ChildItem *.exe`) |
| `docker` не распознан | Docker Desktop не установлен или не запущен — дождитесь статуса **Running** в трее |
| `Comlink недоступен` | Контейнер/окно не работает — `docker logs swgoh-comlink` или перезапустите Шаг 2 |
| `/data недоступен` | Не критично: скрипт продолжит, но `zetas`/`omicrons` будут `null`, а корабли не отделятся |
| Долго висит на игровых данных | Так и должно быть — это десятки мегабайт. Ждите или запустите с `--no-gamedata` |
| Ошибка про ally code | Только девять цифр, без дефисов и пробелов |

Скрипт сохраняет `roster_raw.json` **до** любой обработки, так что даже если
разбор упадёт — данные уже на диске и перезапрашивать игру не придётся.

---

## Приложение: что не так с Windows-бинарником

```
Error: [pkg] V8 rejected the bytecode cache for C:\snapshot\dist\main.js.
This usually means the binary was built with mismatched host/target V8
(cross-platform bytecode).
```

Comlink упакован утилитой `pkg`: она компилирует JavaScript в V8-байткод на
машине сборки и зашивает его в `.exe` вместе с рантаймом Node. Байткод жёстко
привязан к конкретной сборке V8. Судя по сообщению, Windows-версию v4.5.0
собирали кросс-платформенно — встроенный V8 такой байткод не принимает, и
процесс падает на загрузке `main.js`, ещё до старта веб-сервера.

Это дефект сборки релиза, а не ошибка настройки. Правами, антивирусом или
переменными окружения не лечится — до сети дело просто не доходит.

Проверено 12.10.2026: релиз v4.5.0 остаётся последним, его файлы не
перезаливались с 7 августа, открытых issue в репозитории нет. То есть само
собой это не починится — пользуйтесь Docker или v4.4.2.
