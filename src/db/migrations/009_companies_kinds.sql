-- ============================================================
--  Фаза 2. Две компании-владельца, виды работ, реквизиты
--  заказчиков (для автоподстановки в договоры), поддержка лет.
--
--  Вся работа ведётся от двух наших компаний: ИП Цырульников и
--  ООО «Римейк». По ним нужно разделять и учитывать договоры.
-- ============================================================

-- ---- Наши компании (владельцы договоров) с реквизитами ----
CREATE TABLE IF NOT EXISTS owner_companies (
    id           serial PRIMARY KEY,
    name         text NOT NULL,
    short_name   text,
    inn          text, kpp text, ogrn text,
    address      text,
    bank_name    text, bank_bik text, bank_account text, corr_account text,
    director     text,          -- ФИО руководителя
    signatory    text,          -- как подписывает: «Индивидуальный предприниматель», «Генеральный директор»
    phone        text, email text,
    is_active    boolean NOT NULL DEFAULT true,
    ord          int NOT NULL DEFAULT 100
);

INSERT INTO owner_companies (name, short_name, signatory, ord) VALUES
 ('ИП Цырульников',  'ИП Цырульников', 'Индивидуальный предприниматель', 10),
 ('ООО «Римейк»',    'ООО «Римейк»',   'Генеральный директор',          20)
ON CONFLICT DO NOTHING;

-- какой из наших компаний принадлежит договор
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS owner_company_id int REFERENCES owner_companies(id);
CREATE INDEX IF NOT EXISTS contracts_owner_idx ON contracts(owner_company_id);

-- ---- Виды работ, которых не хватало (из реальных папок) ----
INSERT INTO contract_types (code, name, default_advance_pct, warranty_months, notes) VALUES
 ('poverka',       'Поверка приборов учёта',   NULL, NULL, 'Поверка/диагностика УУТЭ, обычно 100% предоплата'),
 ('obsluzhivanie', 'Обслуживание',             NULL, 12,   'Абонентское обслуживание узлов учёта'),
 ('proekt',        'Проектные работы',         NULL, NULL, 'Проектирование узлов учёта')
ON CONFLICT (code) DO NOTHING;

-- ---- Реквизиты заказчиков (для автоподстановки в договоры) ----
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS bank_name    text;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS bank_bik     text;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS bank_account text;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS corr_account text;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS director     text;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS signatory    text;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS phone        text;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS email        text;

-- право «Наши компании» в матрице (правит администратор/уполномоченный)
INSERT INTO capabilities (code, name, ord) VALUES
 ('manage_companies', 'Наши компании и реквизиты', 115)
ON CONFLICT (code) DO NOTHING;
INSERT INTO role_permissions (role_code, cap_code, level) VALUES
 ('admin','manage_companies','yes'), ('lawyer','manage_companies','none'),
 ('head','manage_companies','none'), ('manager','manage_companies','none'),
 ('accountant','manage_companies','none')
ON CONFLICT (role_code, cap_code) DO NOTHING;
