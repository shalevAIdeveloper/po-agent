# PO Agent — מדריך התחלה (לפי הסדר מהלוח)

הסדר כאן הולך לפי התרשימים שלך:
**רעיון ← Claude Plan ← Claude Execute ← CLAUDE.md + Obsidian ← Repo (main/dev) ← GitHub ← UX-UI ← Supabase (dev/prod) ← Railway**
ובכל פיצ'ר: **feature ← בדיקות מקומיות ← dev ← בדיקות בענן ← main ← פרודקשן.**

---

## שלב 0 — התקנות (PowerShell, פעם אחת)

```powershell
winget install Git.Git
winget install GitHub.cli
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12
winget install astral-sh.uv
winget install Docker.DockerDesktop      # נדרש ל-Supabase מקומי
npm install -g pnpm supabase
npm install -g @anthropic-ai/claude-code
winget install Obsidian.Obsidian
```

אחרי ההתקנה: פתח PowerShell חדש, הרץ `gh auth login` והתחבר ל-GitHub.

## שלב 1 — פתיחת הריפו

```powershell
cd ~\Projects            # או כל תיקייה שתבחר
# חלץ לכאן את התיקייה po-agent מה-zip
cd po-agent
git init -b main
git add -A; git commit -m "chore: project starter kit"
gh repo create po-agent --private --source . --push
git checkout -b dev; git push -u origin dev
```

ב-GitHub: **Settings ← Branches ← Add rule** ל-`main` ול-`dev`: חובה PR, וחובה שה-CI יעבור.

## שלב 2 — Obsidian

ב-Obsidian: **Open folder as vault** ← בוחרים את `po-agent\docs`.
מעכשיו ה-SPEC, ההתקדמות וההחלטות (ADRs) הם המוח של הפרויקט, ו-Claude מעדכן אותם בכל משימה.

## שלב 3 — פתיחת Claude Code

```powershell
cd ~\Projects\po-agent
claude
```

**אל תריץ `/init`.** כבר כתבתי לך CLAUDE.md מלא, ו-`/init` עלול לדרוס אותו. במקום זה, הפרומפט הראשון:

> קרא את CLAUDE.md, docs/SPEC.md ו-docs/progress.md. תסכם לי במשפטים קצרים מה המוצר, מה עקרונות האבטחה, ומה ה-milestone הנוכחי. אל תכתוב קוד.

ככה תוודא ש-Claude "הבין" את הפרויקט לפני שהוא נוגע במשהו.

---

## הלולאה שחוזרת על עצמה בכל משימה (Plan ← Execute)

1. **Plan:** לחץ `Shift+Tab` פעמיים כדי להיכנס ל-Plan mode, ואז:
   > השתמש ב-subagent `chief` כדי לתכנן את [המשימה]. אני רוצה משימות קטנות, קבצים, בדיקות וסיכונים.
2. אתה קורא, מתקן ומאשר.
3. **Execute:**
   > צור ענף feature/[שם] מ-dev. בצע את משימה 1 עם ה-subagent `dev`. כתוב בדיקות והרץ אותן.
4. **בדיקה:**
   > הרץ את `qa` על השינויים, ואחר כך את `sec` על ה-diff.
5. **סגירה:**
   > עדכן את docs/progress.md, תעשה commit בפורמט Conventional Commits, push ופתח PR ל-dev.

---

## הפרומפטים לפי ה-milestones

### M0 — יסודות
**0.1 שלד ה-API:**
> צור את apps/api: פרויקט uv עם Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, asyncpg, structlog, ruff, mypy ו-pytest. כולל endpoint `/health`, טעינת הגדרות מ-env עם pydantic-settings, ובדיקה ל-`/health`. תתכנן קודם.

**0.2 שלד ה-Web:**
> צור את apps/web: Next.js App Router, TypeScript strict, Tailwind, shadcn/ui, ESLint, Vitest ו-Playwright. דף בית זמני בלבד, בלי עיצוב עדיין. הוסף scripts: lint, typecheck, test, test:e2e.

**0.3 Supabase מקומי וסכמה בסיסית:**
> הפעל `supabase init`. צור מיגרציה ראשונה: organizations, memberships, extensions pgvector ו-pg_trgm, עם RLS ופוליסות מבוססות memberships. כתוב בדיקה שמוכיחה שמשתמש מארגון A לא רואה נתונים של ארגון B.

**0.4 אימות משתמשים:**
> חבר Supabase Auth ב-web (התחברות במייל). ב-API, אמת את ה-JWT וזהה את ה-org_id לפי membership. השתמש ב-subagent `sec` בסוף.

**0.5 סביבות ענן (עם `devops`):**
> השתמש ב-subagent `devops`: תדריך אותי צעד אחר צעד ליצור שני פרויקטי Supabase (po-agent-dev ו-po-agent-prod) ופרויקט Railway עם שתי סביבות ושלושה services: web, api, worker. דיפלוי dev מענף dev, ו-prod מענף main. כתוב runbook ל-docs/runbooks/environments.md.

### M1 — מנוע חילוץ (הלב של המוצר)
> בנה את סכמת ה-Pydantic לחילוץ PO לפי SPEC §5 F2, ממשק LLMClient, ואת extraction service שמשתמש ב-prompts/extract_po/v1.md. תשמור את extractions עם model, prompt_version, latency ו-cost.

> צור 10 קבצי PO סינתטיים, PDF ותמונה, עם expected.json לכל אחד ב-evals/fixtures. כתוב את evals/run.py שמחשב דיוק ברמת השדה ושומר baseline.

### M2 — קטלוג והתאמת מק"טים
> בנה ייבוא קטלוג ולקוחות מ-CSV/XLSX עם dry-run, ואת 4 שלבי ההתאמה לפי SPEC §F3, כולל עדכון sku_mappings בכל אישור.

### M3 — עיצוב (ה-UX-UI מהלוח)
**3.1 שפה עיצובית:**
> השתמש ב-subagent `ux-ui`. הצע 3 כיווני עיצוב שונים למערכת: צבע, טיפוגרפיה, תחושה. לכל כיוון תן שם, הסבר קצר ופלטה. אל תבנה עדיין.

**3.2 Design tokens:**
> בנה את הכיוון שבחרתי כ-design tokens (light/dark), טיפוגרפיה ורכיבי בסיס, ועמוד /design-system שמציג את כולם.

**3.3 מסכים:**
> בנה את מסך ה-Inbox, ואחר כך את מסך ה-Review המפוצל (מסמך משמאל, הזמנה מימין) עם קיצורי המקלדת מ-SPEC §F5. לכל מסך: loading, empty ו-error.

### M4–M7
המשך לפי הטבלה ב-SPEC §14, באותה לולאה: Plan ← Execute ← qa ← sec ← PR.

---

## על ה-Alpha team שבתרשים

| בתרשים | אצלנו |
|---|---|
| chief | `.claude/agents/chief.md` |
| devops | `.claude/agents/devops.md` |
| sec | `.claude/agents/sec.md` |
| Dev (Claude Code) | `.claude/agents/dev.md` |
| UX-UI | `.claude/agents/ux-ui.md` |
| Dev (Grok) | לא subagent ב-Claude Code. אפשר להשתמש בו לדעה שנייה: מדביקים לו diff או תוכנית ומבקשים ביקורת |
| (תוספת שלי) qa | `.claude/agents/qa.md` |

## כללי ברזל
- לא עושים push ישירות ל-dev או ל-main. תמיד דרך PR.
- לא מכניסים לגיט מסמכי לקוחות אמיתיים או סודות.
- כל שינוי סכמה עובר דרך מיגרציה חדשה.
- תוכנית לפני קוד, בדיקות לפני merge.
