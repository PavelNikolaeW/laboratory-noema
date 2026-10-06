# Приёмка v0.1 — 2026-10-06

Реализован самостоятельный локальный сервис: FastAPI/Pydantic 2, SQLAlchemy/SQLite WAL, durable worker, статический UI, async SDK, локальный CLI экспериментатора. Репозиторий содержит закрытый движок; участнику передаётся только `public/participant.zip` и его token.

Автоматическая проверка: `python -m pytest -q` — **15 passed**. Это 15 сценарных тестов, покрывающих несколько требований каждый. Отдельно выполнены syntax check JS и браузерный informed-пилот. Один warning о deprecated alias в Starlette TestClient не влияет на результаты.

| ID | Результат и свидетельство |
| --- | --- |
| L01 | PASS: `test_engine_invariants`, сохранение массы split/separate/mix, неотрицательность, допуск 1e-12 |
| L02 | PASS: heat сохраняет массу, cool сохраняет активацию |
| L03 | PASS: исходная + активированная порции сохраняют частичную активацию; горячая смесь применяет порог |
| L04 | PASS: параметризованные fixtures — S1 response 73/cost 4, S2 response 100/yield .66/cost 7, S3 fail/pass на разной энергии |
| L05 | PASS: `test_gain_change`, baseline pass, после gain .5 fail, исправленная процедура pass |
| L06 | PASS: два измерения неизменного образца дают 55, revision не меняется |
| L07 | PASS: после heat старое observation остаётся на v1, GET sample v2 не содержит свежих показаний |
| L08 | PASS: одинаковый key возвращает исходный accepted DTO; один расход и один эффект, включая после kill/restart |
| L09 | PASS: другой body или маршрут с тем же key — 409 |
| L10 | PASS: конкурентные heat одной порции дают 202/409; резерв без двойного эффекта |
| L11 | PASS: consumed sample отвергается; дублирование входа mix — 422 |
| L12 | PASS: недостаточные credits/material не принимают операцию и не меняют запас |
| L13 | PASS: rollback при искусственном crash между вычислением и commit; отдельный subprocess SIGKILL после acceptance и recovery |
| L14 | PASS: committed результат сохраняется при создании нового приложения и повторном POST, расход не дублируется |
| L15 | PASS: event sequence/cursor стабильны; повтор страницы; обычная pagination notes сохраняет порядок |
| L16 | PASS: REST/DTO/OpenAPI проверены на отсутствие приватных ключей; UI и SDK содержат только публичные данные; public archive whitelist |
| L17 | PASS: анонимный доступ 401, чужой order 404, HTTP admin/reset отсутствует |
| L18 | PASS: UI различает номинальную массу и measurement; свойства без measurement — «не измерено»; stale observations помечаются |
| L19 | PASS: prediction может привязаться лишь при acceptance новой совпадающей операции; повторная привязка отвергается; edit route отсутствует |
| L20 | PASS: независимые validation repeats используют свежий материал и движок; recipe не принимает заявленные response/yield |
| L21 | PASS: submission принимает только order/recipe, ID продукта/стандарта не принимается; reference DAG сохраняется после split |
| L22 | PASS: snapshot submission остаётся baseline даже если приватная конфигурация изменена до исполнения; CLI subprocess отвергает вмешательство при активной операции, разрешает после завершения |
| L23 | PASS: research/verification отдельны; производственная стоимость хранится в каждом repeat; LLM учитывается харнесом отдельно; CLI metrics считает MAE по quantity/unit |
| L24 | PASS, браузер: после reload сохранены note, prediction, обе порции, наблюдения, recipe и результат двух независимых повторов |
| L25 | PASS, пилот + API: UI измерил контроль 55 и обработанный sample 73; validation response 73/yield 1/cost 4 совпали с HTTP fixtures |
| L26 | PASS: многократные GET не изменяют snapshot; wall time отсутствует в чистых переходах |
| L27 | PASS: NaN/Infinity, precision, range, duplicate refs, extra fields, запрещённые recipe kinds, лимит payload — публичные структурированные отказы |
| L28 | PASS: реальный uvicorn subprocess без харнеса/LLM, SDK poll/resume через TCP; sdk не импортирует lab |

Локальный UI запущен на 8765: порт 8000 в текущем окружении также занят сторонним Docker-приложением.

## UI-пилот

Через in-app browser: login → две порции по 10 г → prediction → measurement контроля → heat → cool → measurement второй порции → note → editor → save recipe → submission → reload → восстановленный успешный результат и note. В результате: исходный отклик 55, после обработки 73, независимые повторы 73/100%/4 cr. Это informed-пилот разработчика, не слепое исследование закономерностей.

Проверен viewport 360×800: document width и content scroll width равны 360, горизонтального переполнения страницы нет; таблица и tabs имеют собственный scroll. Снимки: `ui-validation.jpg`, `ui-mobile.jpg`. Controls — нативные кнопки, inputs/selects, labels; focus-visible и live status. Полный специализированный accessibility audit не проводился.

## Уточнения контракта v0.1

- Все коллекции читаются через `{items, next_cursor, has_more}`; добавлены GET recipes/submissions list для восстановления UI. Events используют `{items, next_after, has_more}`.
- Timestamps UTC ISO 8601 с `+00:00`; IDs случайные, не кодируют скрытые значения.
- Idempotent replay возвращает именно первоначальный HTTP-resource snapshot; актуальный статус читается GET по ID.
- Prediction не принимает ID уже существующей target operation. Связь создаётся только новой POST operations после сохранения prediction. Новый prediction на новое повторное измерение той же revision допустим.
- Отдельный verification reserve 200 г на run. Validation использует изолированные свежие порции и snapshot приборов, а не исследовательские sample IDs.
- Автоматическое охлаждение в validation — verification overhead; в производственную стоимость входят только записанные шаги.
- SQLite v0.1: run aggregate вместо нормализованных таблиц. События в том же aggregate — атомарный durable outbox для polling, отдельного внешнего delivery worker нет.
- Payload POST ограничен 64 KiB, тексты и массивы ограничены DTO. Новых POST не более 120/min/run; повтор принятого ключа не попадает в лимит. GET не расходует credits.
- Public schemas генерируются `scripts/export_public.py`; параметрные ограничения объявлены в публичных schemas и паспортах.

## Осталось за границами текущей приёмки

Реальная интеграция с отдельно разрабатываемым харнесом и слепой человеческий/LLM эксперимент не проведены. Noise, wear, SSE, непрерывная физика, Heroes III исключены по спецификации. Нет самостоятельного генератора train/evaluation наборов: S1–S4 являются development fixtures; научную серию нельзя выдавать за подтверждённую оценку на независимых seeds.

Однопроцессный локальный профиль; высокая нагрузка и multiworker deployment не тестировались. Docker image собран; `scripts/docker_smoke.py` прошёл create → recipe → validation → restart → persisted result в отдельном временном volume. Публичный интернет-деплой не выполнялся: нужен выбранный хост и доступ к нему. Local service не требует внешнего аккаунта.

Обоснование выбранного transaction/lifecycle механизма: [SQLite transactions](https://www.sqlite.org/lang_transaction.html), [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).


## Новая попытка из локального UI

Добавлен отдельный local-control listener (8766), доступный только с локальным управляющим cookie и разрешённым Origin. Публичный API не позволяет создавать/reset runs. Создание нового run и запись idempotency mapping выполняются в одной транзакции; предыдущий бюджет, данные и операции сохраняются. Тест `test_local_new_attempt_preserves_old_and_retries` проверяет изоляцию, полный новый бюджет, сохранение старых заметок и порций, отказ без cookie/с чужим Origin и безопасный повтор.

Браузерная проверка: сохранена заметка в тестовом run → кнопка «Начать новую попытку» → 100 credits и пустой блокнот → возврат через переключатель → старая заметка доступна → reload сохраняет выбранную попытку. Screenshot: `ui-new-attempt.jpg`. Выбор порции/заказа теперь хранится отдельно для каждого токена, чтобы другие прохождения и вкладки не передавали чужие ID.
