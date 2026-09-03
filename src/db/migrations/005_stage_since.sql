-- ============================================================
--  Дата начала текущей стадии
--  Обновляется при каждой смене стадии. Показывается на карточке,
--  по ней сортируются колонки доски (новые сверху).
-- ============================================================

ALTER TABLE contracts ADD COLUMN IF NOT EXISTS stage_since timestamptz;

UPDATE contracts
   SET stage_since = coalesce(updated_at, created_at, now())
 WHERE stage_since IS NULL;

ALTER TABLE contracts ALTER COLUMN stage_since SET DEFAULT now();
