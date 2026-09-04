-- Дата актуальности реквизитов: если по контрагенту больше не было
-- договоров, считаем реквизиты из последнего договора крайними.
ALTER TABLE counterparties  ADD COLUMN IF NOT EXISTS requisites_date date;
ALTER TABLE owner_companies ADD COLUMN IF NOT EXISTS requisites_date date;
