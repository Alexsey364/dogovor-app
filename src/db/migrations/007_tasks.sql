-- ============================================================
--  Задачи и поручения — как в СЭД: задача прямо из карточки
--  договора, с исполнителем и сроком. Контроль сроков — в
--  разделе «Сроки» и в счётчике меню.
-- ============================================================

CREATE TABLE IF NOT EXISTS tasks (
    id           serial PRIMARY KEY,
    contract_id  int REFERENCES contracts(id) ON DELETE CASCADE,
    title        text NOT NULL,
    detail       text,
    assignee_id  int REFERENCES users(id),
    created_by   int REFERENCES users(id),
    due_on       date,
    priority     text NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low','normal','high')),
    is_done      boolean NOT NULL DEFAULT false,
    done_at      timestamptz,
    done_by      int REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tasks_assignee_open_idx ON tasks(assignee_id) WHERE is_done = false;
CREATE INDEX IF NOT EXISTS tasks_contract_idx ON tasks(contract_id);
CREATE INDEX IF NOT EXISTS tasks_due_idx ON tasks(due_on) WHERE is_done = false;

-- возможность в матрице прав (появится строкой на «Права ролей»)
INSERT INTO capabilities (code, name, ord) VALUES
 ('tasks', 'Задачи и поручения', 65)
ON CONFLICT (code) DO NOTHING;

-- по умолчанию: все ведут задачи, бухгалтер — только свои
INSERT INTO role_permissions (role_code, cap_code, level) VALUES
 ('manager','tasks','all'),  ('lawyer','tasks','all'),
 ('head','tasks','all'),     ('accountant','tasks','own'),
 ('admin','tasks','yes')
ON CONFLICT (role_code, cap_code) DO NOTHING;
