-- Замещение сотрудников: на время отсутствия задачи/визы фактически
-- ведёт заместитель. Храним заместителя и период отсутствия.
ALTER TABLE users ADD COLUMN IF NOT EXISTS substitute_id int REFERENCES users(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS absent_from date;
ALTER TABLE users ADD COLUMN IF NOT EXISTS absent_to   date;
