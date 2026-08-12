# Пошаговая инструкция для Windows

Всё делается в PowerShell. Открыть его: `Win + X` → **Terminal** (или **Windows PowerShell**).
Права администратора не нужны.

Понадобятся два окна PowerShell: в первом крутится Comlink, во втором запускается скрипт.

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
Нужен Python 3.9 или новее.

> Если `python` ругается, но работает `py` — дальше везде подставляйте `py`
> вместо `python`.

---

## Шаг 2. Скачать Comlink

1. Откройте [страницу релизов](https://github.com/swgoh-utils/swgoh-comlink/releases).
2. В последнем релизе (на момент написания — **v4.5.0**) разверните **Assets**.
3. Скачайте **`swgoh-comlink-win-4.5.0.zip`** — это версия под Windows.
   (`linux`/`macos` архивы и `openapi.*` вам не нужны.)

---

## Шаг 3. Распаковать и снять блокировку

Windows помечает скачанные из интернета файлы как заблокированные. Создадим
папку, распакуем туда и снимем метку:

```powershell
mkdir "$HOME\swgoh"
cd "$HOME\swgoh"
```

Переместите скачанный zip в эту папку (`C:\Users\<вы>\swgoh`), затем:

```powershell
Unblock-File .\swgoh-comlink-win-4.5.0.zip
Expand-Archive .\swgoh-comlink-win-4.5.0.zip -DestinationPath .
Get-ChildItem *.exe
```

Последняя команда покажет имя исполняемого файла — что-то вида
`swgoh-comlink-4.5.0.exe`. **Запомните его**, дальше подставляйте своё имя.

---

## Шаг 4. Запустить Comlink

В **первом** окне PowerShell:

```powershell
cd "$HOME\swgoh"
.\swgoh-comlink-4.5.0.exe --name my-roster-export --port 3000
```

Точка с обратным слэшем `.\` в начале обязательна — без неё PowerShell не
запускает файлы из текущей папки.

Что может произойти:

* **«Windows protected your PC» (SmartScreen).** Нажмите **More info** →
  **Run anyway**. Это стандартная реакция на неподписанный бинарник.
* **Запрос Windows Firewall.** Достаточно доступа в **частной** сети —
  Comlink слушает только localhost. Можно и отклонить: на локальные
  подключения это не влияет.
* **Антивирус удалил файл.** Добавьте папку `C:\Users\<вы>\swgoh` в исключения
  и распакуйте заново.

Если всё хорошо — в окне появятся строки о запуске сервиса. **Оставьте это
окно открытым.** Закроете — Comlink остановится.

### Ошибка «V8 rejected the bytecode cache»

```
Error: [pkg] V8 rejected the bytecode cache for C:\snapshot\dist\main.js.
This usually means the binary was built with mismatched host/target V8
(cross-platform bytecode).
```

Это **дефект сборки релиза**, а не ошибка настройки у вас. Бинарник собран
упаковщиком `pkg`, который превращает JS в V8-байткод на машине сборки. Если
Windows-версию собирали кросс-платформенно (на Linux-раннере), встроенный V8
отказывается такой байткод принимать — и процесс падает ещё до старта сервиса.
Настройками, правами или антивирусом это не лечится.

**Вариант 1 — предыдущий релиз (быстрее всего).**
Возьмите [v4.4.2](https://github.com/swgoh-utils/swgoh-comlink/releases/tag/v4.4.2)
(вышел 3 августа 2026, за четыре дня до v4.5.0, с обновлением под TU42 — то
есть актуален по игровым данным) и распакуйте `swgoh-comlink-win-4.4.2.zip`
тем же способом. Быстрая проверка, что бинарник вообще стартует:

```powershell
.\swgoh-comlink-4.4.2.exe --check
```

Если снова падает с той же ошибкой — попробуйте v4.4.1 или v4.3.0, либо
переходите к варианту 2.

**Вариант 2 — Docker (надёжнее всего).**
Официальный README проекта рекомендует именно контейнер, и он собирается под
Linux — проблема с байткодом там не возникает в принципе. Поставьте
[Docker Desktop](https://www.docker.com/products/docker-desktop/), затем:

```powershell
docker run -d --name swgoh-comlink -p 3000:3000 `
  -e APP_NAME=my-roster-export `
  ghcr.io/swgoh-utils/swgoh-comlink:latest
```

Проверка:

```powershell
Invoke-RestMethod -Uri http://localhost:3000/metadata -Method Post
```

Порт тот же, поэтому скрипт из Шага 7 запускается без изменений. Отдельное
окно PowerShell держать не нужно — контейнер работает в фоне. Остановить и
удалить потом:

```powershell
docker stop swgoh-comlink; docker rm swgoh-comlink
```

Проверка из **второго** окна PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:3000/metadata -Method Post
```

Должны вернуться игровые метаданные (среди них `latestGamedataVersion`).
Если вместо этого ошибка подключения — Comlink не поднялся, смотрите первое окно.

> Порт 3000 занят? Запустите с `--port 3100` и дальше добавляйте к скрипту
> `--url http://localhost:3100`.

---

## Шаг 5. Скачать скрипт

Во **втором** окне PowerShell.

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

## Шаг 6. Установить зависимость

```powershell
python -m pip install -r requirements.txt
```

Ставится одна библиотека — `requests`.

> Если pip жалуется на права — добавьте `--user`:
> `python -m pip install --user -r requirements.txt`

---

## Шаг 7. Выгрузить ростер

Ally Code — это ваш номер в игре (Профиль → Ally Code), **девять цифр без дефисов**:

```powershell
python fetch_roster.py 123456789
```

Скрипт по шагам напечатает: проверку Comlink, сохранение сырого ответа,
загрузку игровых данных (может занять минуту — файл большой), сборку и статистику.

Альтернатива, если не хотите светить код в истории команд:

```powershell
$env:SWGOH_ALLY_CODE = "123456789"
python fetch_roster.py
```

---

## Шаг 8. Забрать результат

В папке появятся:

| Файл | Что это |
|---|---|
| `roster_raw.json` | сырой ответ игрового API |
| `roster_clean.json` | **персонажи — это грузить в Claude Project** |
| `ships_clean.json` | корабли |

Открыть папку в проводнике:

```powershell
explorer .
```

Проверить, что JSON валиден:

```powershell
python -m json.tool roster_clean.json > $null; if ($?) { "OK" }
```

---

## Шаг 9. Что прислать мне

Пришлите **`roster_raw.json`** — я сверю реальные имена полей с тем, что
ожидает скрипт, поправлю парсинг под вашу версию API и соберу финальный
`roster_clean.json`.

Заодно посмотрите в выводе строку **«Всего зет»** и сравните с количеством
зет в игре. Если расходится — напишите оба числа, поправлю смещение тиров.

---

## Если что-то пошло не так

| Симптом | Что делать |
|---|---|
| `python` не распознан | Python не в PATH — переустановите с галочкой «Add python.exe to PATH» или используйте `py` |
| `.\swgoh-comlink...exe` не распознан | Вы не в той папке (`cd "$HOME\swgoh"`) или имя exe другое (`Get-ChildItem *.exe`) |
| `V8 rejected the bytecode cache` | Битая сборка релиза — возьмите v4.4.2 или Docker, см. Шаг 4 |
| `Comlink недоступен` | Первое окно закрыто или Comlink упал — перезапустите Шаг 4 |
| `/data недоступен` | Не критично: скрипт продолжит, но `zetas`/`omicrons` будут `null`, а корабли не отделятся |
| Скрипт долго висит на игровых данных | Так и должно быть — это десятки мегабайт. Ждите или запустите с `--no-gamedata` |
| Ошибка про ally code | Только девять цифр, без дефисов и пробелов |

Скрипт сохраняет `roster_raw.json` **до** любой обработки, так что даже если
разбор упадёт — данные уже на диске и перезапрашивать игру не придётся.
