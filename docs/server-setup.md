# Подготовка сервера и доступа

Три шага. Первые два делаете вы — там нужен пароль администратора,
а я пароли не ввожу. Третий делаю я.

---

## 1. Включить SSH на сервере

На сервере, **PowerShell от имени администратора**:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

Проверить, что поднялось:

```powershell
Get-Service sshd; Get-NetFirewallRule -Name sshd | Select-Object Enabled
```

Должно быть `Running` и `True`.

---

## 2. Положить мой ключ

**Вот тут подводный камень.** Если учётная запись входит в группу «Администраторы»,
Windows игнорирует привычный `~/.ssh/authorized_keys` и читает **только** общий файл
`C:\ProgramData\ssh\administrators_authorized_keys`. Причём с жёсткими правами:
если доступ к файлу есть у кого-то кроме `Administrators` и `SYSTEM`,
sshd молча его не примет и вход будет отвергнут без внятной ошибки.

### Если заходить под администратором

```powershell
$key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGvzrCDk+m6TB7lgBJc/jZSZn+DJ906ts8IcCEqCq3ud claude-code-dogovory-server"
$f = "$env:ProgramData\ssh\administrators_authorized_keys"
Add-Content -Path $f -Value $key -Encoding UTF8
icacls $f /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
```

### Если заводите отдельную учётку без прав администратора — так правильнее

```powershell
$user = "dogovor"
$key  = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGvzrCDk+m6TB7lgBJc/jZSZn+DJ906ts8IcCEqCq3ud claude-code-dogovory-server"
$d    = "C:\Users\$user\.ssh"
New-Item -ItemType Directory -Force $d | Out-Null
Add-Content -Path "$d\authorized_keys" -Value $key -Encoding UTF8
icacls "$d\authorized_keys" /inheritance:r /grant "${user}:F" /grant "SYSTEM:F"
```

Отдельная учётка лучше: приложению права администратора не нужны,
а если ключ утечёт, чужой получит только папку сервиса, а не весь сервер.

**Отпечаток ключа** — сверьте, что положили именно мой:

```
SHA256:qBfO0tn+dDNVMEINggmtfcn0PdfyxODtStazhEuam+g
```

---

## 3. Дать мне адрес

Напишите в чат IP или имя сервера и под какой учёткой заходить.
Дальше я подключаюсь сам и ставлю Python, PostgreSQL и приложение.

Проверить со своей машины можно так — должно спросить и пустить без пароля:

```bash
ssh -i ~/.ssh/id_ed25519_server dogovor@АДРЕС_СЕРВЕРА "hostname; ver"
```

---

## GitHub

Отдельный ключ, только для GitHub. Добавьте его в свою учётную запись:
**github.com → Settings → SSH and GPG keys → New SSH key**, тип Authentication key.

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIILh7jGy7u11+OhAin4IWCzyWrLWyrAGu3b+S2fz4pz9 aleksey364@gmail.com
```

Затем создайте пустой **приватный** репозиторий (без README, без .gitignore —
они уже есть) и пришлите его адрес. Имя предлагаю `dogovor-app`.

Проверка, что ключ принят:

```bash
ssh -T git@github.com
```

Ответ «Hi <логин>! You've successfully authenticated» — значит готово.

---

## Что важно помнить про доступ наружу

Приложение работает **только во внутренней сети**. Порт 22 и порт приложения
наружу не пробрасываем. Если понадобится заходить из дома — это делается через
уже настроенный VPN, а не публикацией портов в интернет.
