-- ============================================================
--  Редактируемые роли и матрица прав
--  Раньше матрица была зашита в шаблоне. Теперь — в базе,
--  и её меняет администратор через интерфейс.
-- ============================================================

-- роли уже есть (roles); добавим признак встроенной, чтобы не удалить нужную
ALTER TABLE roles ADD COLUMN IF NOT EXISTS is_builtin boolean NOT NULL DEFAULT false;
UPDATE roles SET is_builtin = true
 WHERE code IN ('manager', 'lawyer', 'head', 'accountant', 'admin');

-- возможности (строки матрицы)
CREATE TABLE IF NOT EXISTS capabilities (
    code text PRIMARY KEY,
    name text NOT NULL,
    ord  int  NOT NULL DEFAULT 100
);

INSERT INTO capabilities (code, name, ord) VALUES
 ('create_contract',   'Создать договор',            10),
 ('view_contracts',    'Видеть договоры',            20),
 ('edit_contract',     'Править договор',            30),
 ('review_decide',     'Решать по замечаниям',       40),
 ('approve_send',      'Согласовать и отправить',    50),
 ('execution',         'Вести исполнение и акты',    60),
 ('payments',          'Вести платежи',              70),
 ('terminate',         'Расторгнуть, изменить срок', 80),
 ('delete_contract',   'Удалить договор',            90),
 ('reports',           'Отчёты',                    100),
 ('manage_users',      'Учётные записи',            110),
 ('manage_permissions','Настройка прав ролей',      120),
 ('view_journal',      'Журнал действий',           130)
ON CONFLICT (code) DO NOTHING;

-- матрица: роль × возможность → уровень
--   none  — нельзя
--   own   — только свои
--   dept  — свой отдел
--   view  — только просмотр
--   all   — все
--   yes   — да (полный доступ)
CREATE TABLE IF NOT EXISTS role_permissions (
    role_code text NOT NULL REFERENCES roles(code) ON DELETE CASCADE,
    cap_code  text NOT NULL REFERENCES capabilities(code) ON DELETE CASCADE,
    level     text NOT NULL DEFAULT 'none'
        CHECK (level IN ('none','own','dept','view','all','yes')),
    PRIMARY KEY (role_code, cap_code)
);

-- значения по умолчанию (та же матрица, что была в макете)
INSERT INTO role_permissions (role_code, cap_code, level) VALUES
 ('manager','create_contract','yes'),   ('manager','view_contracts','dept'),
 ('manager','edit_contract','own'),     ('manager','review_decide','none'),
 ('manager','approve_send','none'),     ('manager','execution','own'),
 ('manager','payments','none'),         ('manager','terminate','none'),
 ('manager','delete_contract','none'),  ('manager','reports','dept'),
 ('manager','manage_users','none'),     ('manager','manage_permissions','none'),
 ('manager','view_journal','none'),

 ('lawyer','create_contract','yes'),    ('lawyer','view_contracts','all'),
 ('lawyer','edit_contract','all'),      ('lawyer','review_decide','yes'),
 ('lawyer','approve_send','yes'),       ('lawyer','execution','all'),
 ('lawyer','payments','view'),          ('lawyer','terminate','yes'),
 ('lawyer','delete_contract','none'),   ('lawyer','reports','all'),
 ('lawyer','manage_users','none'),      ('lawyer','manage_permissions','none'),
 ('lawyer','view_journal','yes'),

 ('head','create_contract','none'),     ('head','view_contracts','all'),
 ('head','edit_contract','none'),       ('head','review_decide','none'),
 ('head','approve_send','none'),        ('head','execution','none'),
 ('head','payments','view'),            ('head','terminate','none'),
 ('head','delete_contract','none'),     ('head','reports','all'),
 ('head','manage_users','none'),        ('head','manage_permissions','none'),
 ('head','view_journal','yes'),

 ('accountant','create_contract','none'),('accountant','view_contracts','view'),
 ('accountant','edit_contract','none'), ('accountant','review_decide','none'),
 ('accountant','approve_send','none'),  ('accountant','execution','none'),
 ('accountant','payments','yes'),       ('accountant','terminate','none'),
 ('accountant','delete_contract','none'),('accountant','reports','view'),
 ('accountant','manage_users','none'),  ('accountant','manage_permissions','none'),
 ('accountant','view_journal','none'),

 ('admin','create_contract','yes'),     ('admin','view_contracts','all'),
 ('admin','edit_contract','yes'),       ('admin','review_decide','yes'),
 ('admin','approve_send','yes'),        ('admin','execution','all'),
 ('admin','payments','yes'),            ('admin','terminate','yes'),
 ('admin','delete_contract','yes'),     ('admin','reports','all'),
 ('admin','manage_users','yes'),        ('admin','manage_permissions','yes'),
 ('admin','view_journal','yes')
ON CONFLICT (role_code, cap_code) DO NOTHING;
