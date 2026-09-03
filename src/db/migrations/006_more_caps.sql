-- ============================================================
--  Отдельные права на действия, которые раньше были «зашиты»
--  Теперь смена стадии, загрузка файлов и ведение исполнения
--  настраиваются в матрице как самостоятельные возможности.
-- ============================================================

INSERT INTO capabilities (code, name, ord) VALUES
 ('change_stage', 'Менять стадию договора', 35),
 ('manage_files', 'Загружать файлы',        37)
ON CONFLICT (code) DO NOTHING;

-- значения по умолчанию для новых прав
INSERT INTO role_permissions (role_code, cap_code, level) VALUES
 ('manager','change_stage','yes'),  ('manager','manage_files','yes'),
 ('lawyer','change_stage','yes'),   ('lawyer','manage_files','yes'),
 ('head','change_stage','none'),    ('head','manage_files','none'),
 ('accountant','change_stage','none'),('accountant','manage_files','none'),
 ('admin','change_stage','yes'),    ('admin','manage_files','yes')
ON CONFLICT (role_code, cap_code) DO NOTHING;
