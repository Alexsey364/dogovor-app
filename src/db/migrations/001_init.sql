-- ============================================================
--  Договорной контур — начальная схема
--  PostgreSQL 14+
--
--  Правила предметной области выведены из 62 договоров 2026 года
--  и зашиты в справочники, а не в код: их меняет администратор,
--  а не программист.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- нечёткий поиск по названиям
CREATE EXTENSION IF NOT EXISTS unaccent;     -- поиск без учёта ё/е

-- ------------------------------------------------------------
--  Люди и права
-- ------------------------------------------------------------

CREATE TABLE roles (
    code        text PRIMARY KEY,            -- manager | lawyer | head | accountant | admin
    name        text NOT NULL,
    description text
);

CREATE TABLE departments (
    id   serial PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE users (
    id            serial PRIMARY KEY,
    login         text NOT NULL UNIQUE,
    full_name     text NOT NULL,
    role_code     text NOT NULL REFERENCES roles(code),
    department_id int REFERENCES departments(id),
    password_hash text NOT NULL,
    must_change_password boolean NOT NULL DEFAULT true,
    is_active     boolean NOT NULL DEFAULT true,
    last_login_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Тонкая настройка прав поверх роли.
-- Запрет всегда сильнее разрешения, кроме роли admin.
CREATE TABLE permission_rules (
    id          serial PRIMARY KEY,
    criterion   text NOT NULL,               -- department | contract_type | amount | stage | advance | personal
    condition   jsonb NOT NULL,              -- {"op":">","value":1000000}
    applies_to  jsonb NOT NULL,              -- {"role":"manager"} | {"user_id":7} | {"department_id":2}
    effect      text NOT NULL,               -- allow | deny | require_approval
    action      text,                        -- что именно разрешаем/запрещаем
    approvers   jsonb,                       -- ["head","lawyer"] — порядок согласования
    valid_from  date NOT NULL DEFAULT current_date,
    valid_to    date,                        -- NULL = бессрочно
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT permission_rules_effect_chk
        CHECK (effect IN ('allow','deny','require_approval'))
);

-- ------------------------------------------------------------
--  Контрагенты и объекты
-- ------------------------------------------------------------

CREATE TABLE counterparties (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    short_name  text,
    inn         text,
    kpp         text,
    ogrn        text,
    kind        text NOT NULL DEFAULT 'commercial',
    -- commercial | budget | uk_tsj | government | individual
    address     text,
    notes       text,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT counterparties_kind_chk
        CHECK (kind IN ('commercial','budget','uk_tsj','government','individual'))
);

CREATE UNIQUE INDEX counterparties_inn_uniq ON counterparties(inn) WHERE inn IS NOT NULL;
CREATE INDEX counterparties_name_trgm ON counterparties USING gin (name gin_trgm_ops);

-- Объект = адрес. Отдельная сущность потому, что по одному адресу
-- бывает несколько договоров: на Полушкина 51 их три.
CREATE TABLE objects (
    id           serial PRIMARY KEY,
    address      text NOT NULL,
    settlement   text,                       -- Уссурийск, Черниговка, Кавалерово…
    description  text,                       -- «учебный корпус № 3», «правое крыло»
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX objects_address_trgm ON objects USING gin (address gin_trgm_ops);

-- ------------------------------------------------------------
--  Типы работ — здесь живут правила аванса и гарантии
-- ------------------------------------------------------------

CREATE TABLE contract_types (
    code                text PRIMARY KEY,
    name                text NOT NULL,
    default_advance_pct numeric(5,2),        -- 30.00 или 70.00
    warranty_months     int,                 -- 12 | 24 | 36 | 60
    template_id         int,                 -- заполняется ниже, после templates
    notes               text
);

-- Значения ниже — не выдумка, а то, что фактически соблюдается
-- во всех 62 договорах без единого отклонения.
INSERT INTO contract_types (code, name, default_advance_pct, warranty_months, notes) VALUES
 ('montazh_uute', 'Монтаж узла учёта тепловой энергии', 70.00, 36, 'Обычный коммерческий договор'),
 ('kapremont',    'Капитальный ремонт',                 30.00, 60, 'Гарантия 5 лет'),
 ('budget_works', 'Работы для бюджетного заказчика',    30.00, 24, 'Школы, сады, администрации, КГБУ'),
 ('sborka',       'Сборка узла на нашей площадке',      NULL,  12, 'Заказчик забирает и монтирует сам'),
 ('rekonstrukciya','Реконструкция узла ГВС',            70.00, 36, NULL),
 ('postavka',     'Поставка оборудования',              70.00, 36, NULL),
 ('goskontrakt',  'Государственный контракт',           NULL,  NULL, '44-ФЗ или 223-ФЗ, условия из контракта');

-- ------------------------------------------------------------
--  Журнал номеров — отвечает на вопрос «где М 45»
-- ------------------------------------------------------------

CREATE TABLE contract_numbers (
    id           serial PRIMARY KEY,
    year         int  NOT NULL,
    seq          int  NOT NULL,              -- сквозной номер: 1..60
    month        int  NOT NULL CHECK (month BETWEEN 1 AND 12),
    number_text  text NOT NULL,              -- «М 45-07-2026»
    issued_to    int  REFERENCES users(id),
    issued_at    timestamptz NOT NULL DEFAULT now(),
    contract_id  int,                        -- NULL = номер выдан, договор не заведён
    void_reason  text,                       -- почему номер остался пустым
    UNIQUE (year, seq)
);

COMMENT ON TABLE contract_numbers IS
  'Номер выдаётся здесь и только здесь. Пустая строка с contract_id IS NULL '
  'сразу видна в отчёте — так пропуск вроде М 45 не потеряется молча.';

-- ------------------------------------------------------------
--  Договоры
-- ------------------------------------------------------------

CREATE TABLE contracts (
    id                serial PRIMARY KEY,
    number_id         int UNIQUE REFERENCES contract_numbers(id),
    number_text       text NOT NULL,
    external_number   text,                  -- «26-ЕП 56-38» у госконтрактов
    ikz               text,                  -- идентификационный код закупки

    type_code         text NOT NULL REFERENCES contract_types(code),
    counterparty_id   int  NOT NULL REFERENCES counterparties(id),
    object_id         int  REFERENCES objects(id),
    subject           text NOT NULL,

    signed_on         date,                  -- NULL = не подписан (случай М 12)
    valid_from        date,
    valid_to          date,

    amount            numeric(14,2),
    vat_rate          numeric(5,2) DEFAULT 5.00,
    advance_amount    numeric(14,2),
    advance_pct       numeric(5,2),

    work_days         int,                   -- срок работ
    work_days_kind    text DEFAULT 'calendar' CHECK (work_days_kind IN ('calendar','working')),
    work_starts_from  text,                  -- 'advance' | 'signing' — от чего считается

    warranty_months   int,
    commissioning_date date,                 -- дата ввода в эксплуатацию
    warranty_until    date,                  -- считается триггером ниже из даты ввода

    has_penalty       boolean,               -- есть ли неустойка за просрочку оплаты
    auto_renewal      boolean NOT NULL DEFAULT false,
    renewal_notice_days int,

    stage             text NOT NULL DEFAULT 'draft',
    -- draft | internal_review | legal_review | at_counterparty
    -- | in_progress | completed | warranty | archived | cancelled | on_hold

    responsible_id    int REFERENCES users(id),
    department_id     int REFERENCES departments(id),
    folder_path       text,                  -- путь к папке на сервере
    created_by        int REFERENCES users(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT contracts_stage_chk CHECK (stage IN (
        'draft','internal_review','legal_review','at_counterparty',
        'in_progress','completed','warranty','archived','cancelled','on_hold')),
    CONSTRAINT contracts_advance_chk
        CHECK (advance_amount IS NULL OR amount IS NULL OR advance_amount <= amount)
);

CREATE INDEX contracts_stage_idx        ON contracts(stage);
CREATE INDEX contracts_counterparty_idx ON contracts(counterparty_id);
CREATE INDEX contracts_signed_idx       ON contracts(signed_on);
CREATE INDEX contracts_warranty_idx     ON contracts(warranty_until)
    WHERE warranty_until IS NOT NULL;

-- Конец гарантии считается от даты ввода в эксплуатацию, а не от даты договора.
-- Триггер вместо генерируемого столбца: date + interval PostgreSQL не признаёт
-- immutable, а тут та же логика работает надёжно на любой версии.
CREATE OR REPLACE FUNCTION contracts_set_warranty() RETURNS trigger AS $$
BEGIN
    IF NEW.commissioning_date IS NOT NULL AND NEW.warranty_months IS NOT NULL THEN
        NEW.warranty_until := (NEW.commissioning_date
                               + make_interval(months => NEW.warranty_months))::date;
    ELSE
        NEW.warranty_until := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER contracts_warranty BEFORE INSERT OR UPDATE ON contracts
    FOR EACH ROW EXECUTE FUNCTION contracts_set_warranty();

ALTER TABLE contract_numbers
    ADD CONSTRAINT contract_numbers_contract_fk
    FOREIGN KEY (contract_id) REFERENCES contracts(id);

COMMENT ON COLUMN contracts.commissioning_date IS
  'Гарантия считается отсюда, а не от даты договора — так написано в текстах. '
  'Пока поле пустое, warranty_until не вычисляется, и договор попадает '
  'в отчёт «гарантия без точки отсчёта».';

COMMENT ON COLUMN contracts.signed_on IS
  'NULL означает, что договор не подписан. Реальный случай: М 12-02-2026, '
  'в шапке пустые прочерки вместо даты, документ пролежал полгода.';

-- ------------------------------------------------------------
--  Связи между договорами
-- ------------------------------------------------------------

CREATE TABLE contract_links (
    id          serial PRIMARY KEY,
    parent_id   int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    child_id    int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    link_type   text NOT NULL,
    -- reissue    — аннулирован и перевыпущен (М 27 → М 49)
    -- supplement — дополнительное соглашение
    -- annex      — приложение, смета, спецификация
    -- same_object— работы по тому же адресу
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (parent_id, child_id, link_type),
    CONSTRAINT contract_links_type_chk
        CHECK (link_type IN ('reissue','supplement','annex','same_object')),
    CONSTRAINT contract_links_no_self CHECK (parent_id <> child_id)
);

-- ------------------------------------------------------------
--  Файлы и версии
-- ------------------------------------------------------------

CREATE TABLE contract_files (
    id           serial PRIMARY KEY,
    contract_id  int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    version      int NOT NULL DEFAULT 1,
    kind         text NOT NULL,
    -- contract | estimate | ks2 | ks3 | act | supplement | protocol | scan | other
    file_name    text NOT NULL,
    file_path    text NOT NULL,              -- путь на сервере, файл не копируется в БД
    size_bytes   bigint,
    sha256       text,
    has_text_layer boolean,                  -- false = скан, нужно распознавание
    extracted_text text,                     -- для полнотекстового поиска
    uploaded_by  int REFERENCES users(id),
    uploaded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT contract_files_kind_chk CHECK (kind IN
        ('contract','estimate','ks2','ks3','act','supplement','protocol','scan','other'))
);

CREATE INDEX contract_files_contract_idx ON contract_files(contract_id);

-- Полнотекстовый поиск по содержимому договоров.
-- 'russian' даёт нормальную морфологию: «поверка» найдётся по «поверке».
ALTER TABLE contract_files
    ADD COLUMN text_search tsvector
    GENERATED ALWAYS AS (to_tsvector('russian'::regconfig, coalesce(extracted_text,''))) STORED;

CREATE INDEX contract_files_fts ON contract_files USING gin (text_search);

-- ------------------------------------------------------------
--  Исполнение: этапы, акты, платежи
-- ------------------------------------------------------------

CREATE TABLE stages (
    id           serial PRIMARY KEY,
    contract_id  int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    ord          int NOT NULL,
    name         text NOT NULL,
    volume       text,                       -- «14 узлов», «1 блок-секция»
    planned_on   date,
    actual_on    date,
    amount       numeric(14,2),
    is_done      boolean NOT NULL DEFAULT false,
    UNIQUE (contract_id, ord)
);

CREATE TABLE payments (
    id           serial PRIMARY KEY,
    contract_id  int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    stage_id     int REFERENCES stages(id),
    kind         text NOT NULL DEFAULT 'final',  -- advance | stage | final
    condition    text,                       -- «10 календарных дней с выставления счёта»
    planned_on   date,
    amount       numeric(14,2) NOT NULL,
    paid_on      date,
    paid_amount  numeric(14,2),
    direction    text NOT NULL DEFAULT 'incoming' CHECK (direction IN ('incoming','outgoing')),
    CONSTRAINT payments_kind_chk CHECK (kind IN ('advance','stage','final'))
);

CREATE INDEX payments_planned_idx ON payments(planned_on) WHERE paid_on IS NULL;

-- ------------------------------------------------------------
--  Проверка договора
-- ------------------------------------------------------------

CREATE TABLE review_findings (
    id           serial PRIMARY KEY,
    contract_id  int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    severity     text NOT NULL CHECK (severity IN ('critical','warning','info')),
    rule_code    text NOT NULL,              -- см. src/review/rules.py
    title        text NOT NULL,
    detail       text,
    clause       text,                       -- «пункт 4.2»
    resolution   text CHECK (resolution IN ('accepted','rejected')),
    resolved_by  int REFERENCES users(id),
    resolved_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX review_findings_open_idx ON review_findings(contract_id)
    WHERE resolution IS NULL;

-- ------------------------------------------------------------
--  Обсуждение и журнал
-- ------------------------------------------------------------

CREATE TABLE comments (
    id           serial PRIMARY KEY,
    contract_id  int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    author_id    int REFERENCES users(id),
    body         text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Журнал только пополняется. Ни удаления, ни правки — даже администратором.
CREATE TABLE audit_log (
    id           bigserial PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    user_id      int REFERENCES users(id),
    action       text NOT NULL,
    entity       text NOT NULL,
    entity_id    int,
    before       jsonb,
    after        jsonb,
    ip           inet
);

CREATE INDEX audit_log_entity_idx ON audit_log(entity, entity_id, at DESC);

REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;

CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Журнал действий не изменяется и не удаляется';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

-- ------------------------------------------------------------
--  Шаблоны
-- ------------------------------------------------------------

CREATE TABLE templates (
    id           serial PRIMARY KEY,
    name         text NOT NULL,
    type_code    text REFERENCES contract_types(code),
    file_path    text NOT NULL,              -- docx с переменными
    variables    jsonb,                      -- какие поля подставляются
    based_on     text,                       -- «собран из М 04-02-2026»
    used_count   int NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE contract_types
    ADD CONSTRAINT contract_types_template_fk
    FOREIGN KEY (template_id) REFERENCES templates(id);

-- ------------------------------------------------------------
--  Базовые роли
-- ------------------------------------------------------------

INSERT INTO roles (code, name, description) VALUES
 ('manager',   'Менеджер',     'Заводит договоры своего отдела, ведёт исполнение'),
 ('lawyer',    'Юрист',        'Видит все, решает по замечаниям, согласует и отправляет'),
 ('head',      'Руководитель', 'Видит всё, только чтение и согласование'),
 ('accountant','Бухгалтер',    'Суммы, авансы и платежи'),
 ('admin',     'Администратор','Может всё, кроме правки журнала действий');
