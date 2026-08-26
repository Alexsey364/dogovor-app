# -*- coding: utf-8 -*-
"""
Мини-раннер тестов без внешних зависимостей.

Нужен, пока на машине нет pytest. Когда появится — работает и он:
файл test_rules.py написан в обычном стиле pytest.

    python run_tests.py
"""

import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8")

import test_rules  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in vars(test_rules).items()
        if name.startswith("test_") and callable(obj)
    ]
    passed, failed = 0, []

    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed.append((name, traceback.format_exc()))
            print(f"{RED}ПРОВАЛ{RESET}  {name.replace('_', ' ')}")
        else:
            passed += 1
            print(f"{GREEN}ок{RESET}      {name.replace('_', ' ')}")

    print()
    for name, tb in failed:
        print(f"{RED}{'─' * 70}{RESET}")
        print(f"{RED}{name}{RESET}")
        print(f"{DIM}{tb}{RESET}")

    total = len(tests)
    if failed:
        print(f"{RED}Провалено {len(failed)} из {total}{RESET}")
        return 1
    print(f"{GREEN}Все {total} прошли{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
