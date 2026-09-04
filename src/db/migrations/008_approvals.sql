-- ============================================================
--  Маршрут согласования (визирование) — как в СЭД.
--  Последовательная цепочка визирующих: у каждого шага свой
--  исполнитель, статус и отметка времени. Активный шаг —
--  первый непройденный. Всё пишется в журнал.
-- ============================================================

CREATE TABLE IF NOT EXISTS approvals (
    id           serial PRIMARY KEY,
    contract_id  int NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    ord          int NOT NULL,
    approver_id  int REFERENCES users(id),
    status       text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),
    comment      text,
    decided_at   timestamptz,
    created_by   int REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS approvals_contract_idx ON approvals(contract_id, ord);
