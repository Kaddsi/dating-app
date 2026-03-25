# 📦 Как открыть проект в VS Code

## Шаг 1: Скачать архив
Скачайте файл `premium-dating-complete.tar.gz`

---

## Шаг 2: Распаковать

### Windows:
1. Установите **7-Zip** (https://www.7-zip.org/)
2. Правой кнопкой на файл → 7-Zip → Extract Here
3. Получится папка `premium-dating-twa/`

### macOS / Linux:
```bash
tar -xzf premium-dating-complete.tar.gz
cd premium-dating-twa
```

---

## Шаг 3: Открыть в VS Code

### Способ 1 (через меню):
1. Откройте VS Code
2. File → Open Folder
3. Выберите папку `premium-dating-twa`

### Способ 2 (через терминал):
```bash
code premium-dating-twa
```

---

## Шаг 4: Что вы увидите

```
📁 PREMIUM-DATING-TWA
├── 📖 README.md              ← Начните отсюда!
├── 📖 РУКОВОДСТВО.md          ← Полное руководство на русском
├── 📖 БЫСТРЫЙ_СТАРТ.md        ← Инструкция для новичков
├── 📖 СТРУКТУРА_ПРОЕКТА.md    ← Описание всех файлов
│
├── 🎨 premium-dating-app.html ← ДЕМО! Откройте в браузере
│
├── 🐍 bot/                    ← Telegram бот
├── 🔧 backend/                ← REST API
├── ⚛️  frontend/               ← React приложение
└── 🗄️  database/               ← SQL схема
```

---

## Шаг 5: Установить расширения VS Code (рекомендуется)

VS Code предложит установить расширения. Нажмите **Install**:

✅ **Python** - для bot/ и backend/  
✅ **ES7+ React** - для frontend/  
✅ **Tailwind CSS IntelliSense** - для стилей  
✅ **SQLTools** - для работы с БД  

Или установите вручную: `Ctrl+Shift+X` → поиск → Install

---

## Шаг 6: Первый запуск

### 🎨 Самый простой способ (ДЕМО):
1. Откройте `premium-dating-app.html`
2. Правая кнопка → Open with Live Server
3. Или просто дважды кликните файл

**Готово!** Приложение откроется в браузере! 🎉

### 🔧 Полный запуск (с Backend):

**1. База данных:**
```bash
# Установите PostgreSQL
# Windows: https://www.postgresql.org/download/windows/
# macOS: brew install postgresql
# Linux: sudo apt install postgresql

# Создайте базу
createdb dating_db
psql dating_db < database/schema.sql
```

**2. Telegram Bot:**
```bash
cd bot

# Создайте виртуальное окружение
python -m venv venv

# Активируйте
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Установите зависимости
pip install -r requirements.txt

# Настройте .env (скопируйте из .env.example)
# Вставьте токен от @BotFather

# Запустите
python main.py
```

**3. Frontend:**
```bash
cd frontend

# Установите зависимости
npm install

# Запустите dev-сервер
npm run dev
```

Откроется на http://localhost:5173

---

## 📂 Навигация по проекту в VS Code

### Основные файлы:

**Хотите понять логику блокировки?**
→ Откройте: `bot/middlewares/phone_verification.py`

**Хотите увидеть UI свайпов?**
→ Откройте: `frontend/src/components/Discovery.jsx`

**Хотите посмотреть схему БД?**
→ Откройте: `database/schema.sql`

**Нужна инструкция?**
→ Откройте: `РУКОВОДСТВО.md`

---

## 🔍 Полезные команды VS Code

### Поиск по всему проекту:
`Ctrl+Shift+F` (Windows/Linux)  
`Cmd+Shift+F` (macOS)

### Открыть файл:
`Ctrl+P` → начните печатать имя файла

### Терминал:
`Ctrl+`` (backtick)

### Разделить экран:
`Ctrl+\` - открыть файл рядом

---

## 🐛 Если что-то не работает

### Проблема: "Python не найден"
**Решение:** Установите Python 3.11+ с python.org

### Проблема: "npm не найден"
**Решение:** Установите Node.js с nodejs.org

### Проблема: "PostgreSQL не запускается"
**Решение:** 
- Windows: Проверьте Services → PostgreSQL
- macOS: `brew services start postgresql`
- Linux: `sudo systemctl start postgresql`

### Проблема: "Модуль не найден"
**Решение Python:** `pip install -r requirements.txt`  
**Решение Node:** `npm install`

---

## 💡 Советы для работы

### 1. Используйте несколько терминалов
- Терминал 1: `python bot/main.py` (бот)
- Терминал 2: `npm run dev` (фронтенд)
- Терминал 3: для команд

### 2. Установите форматтеры
- Python: Black, Pylint
- JavaScript: Prettier, ESLint

### 3. Используйте Git
```bash
git init
git add .
git commit -m "Initial commit"
```

---

## 📚 Что читать дальше

1. ✅ `СТРУКТУРА_ПРОЕКТА.md` - понять что где лежит
2. ✅ `БЫСТРЫЙ_СТАРТ.md` - запустить за 5 минут
3. ✅ `РУКОВОДСТВО.md` - полное понимание проекта
4. ✅ `DEPLOYMENT.md` - выложить в продакшн

---

## 🎉 Готово!

Теперь у вас открыт весь проект в VS Code!

**Начните с открытия `premium-dating-app.html`** - это полностью рабочее демо!

Удачи! 🚀
