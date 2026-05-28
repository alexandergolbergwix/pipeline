# MHM Pipeline — How to install

This page covers installing the desktop app on **Windows** and **macOS**, and how
to clear the standard "unrecognized app" warning that Windows shows for any
installer that hasn't been signed by a commercial certificate authority. A
Hebrew version follows below.

---

## Windows

### 1. Get the installer

The installer file is a single `.exe` named `MHMPipeline-Setup-<version>.exe`
(for example `MHMPipeline-Setup-0.1.11.exe`). Save it anywhere — Downloads is
fine.

### 2. The "Windows protected your PC" warning

Windows shows this dialog for every installer it doesn't recognize. It does
**not** mean the app is malicious; it means the file isn't signed by a paid
code-signing certificate (the MHM Pipeline is an academic project). Two ways
to proceed — either works:

**Method A — from the warning dialog (fastest):**

1. Click the small **More info** link near the top of the dialog (in Hebrew
   Windows: **מידע נוסף**).
2. A **Run anyway** button appears (in Hebrew: **הפעל בכל זאת**). Click it.
3. The installer continues as normal.

**Method B — unblock the file first (avoids the dialog entirely):**

1. Right-click the `.exe` file → **Properties**.
2. On the **General** tab, at the bottom, tick the **Unblock** checkbox.
3. Click **OK**.
4. Double-click the file — the warning will not appear.

### 3. Run the installer

Accept the license, pick an install folder (the default is fine), and let it
finish. The installer creates **MHM Pipeline** in the Start menu and (if
selected) a desktop shortcut.

### 4. First launch

Open **MHM Pipeline** from the Start menu. On first run the app asks you to
walk through a one-time wizard that downloads the NER model files (~3 GB).
Have your **Gemini API key** ready if you plan to use the AI-verification
step — paste it into **Settings → Credentials & API Keys…** the first time
you open the app.

---

## macOS

Open the `.dmg`, drag **MHM Pipeline** to **Applications**, then launch it
from Launchpad or Spotlight. If macOS warns that the app is from an
unidentified developer, right-click the app icon in Applications → **Open** →
**Open** in the confirm dialog. After this one-time prompt, double-clicking
works normally.

---

## System requirements

|  | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10 (64-bit) / macOS 12 | Windows 11 / macOS 14 |
| **RAM** | 8 GB | 16 GB |
| **Disk** | 12 GB free | 20 GB free |
| **GPU** | none (CPU works) | Apple Silicon (MPS) or NVIDIA CUDA — much faster NER |

The app falls back to CPU automatically when no GPU is available.

---

## Where to get help

- The app's own **Help** menu has topics for every stage.
- Logs are written under `~/Library/Logs/MHMPipeline/` (macOS) or
  `%LOCALAPPDATA%\MHMPipeline\Logs\` (Windows) — send the most recent file
  along with any bug report.

---

# מדריך התקנה — בעברית

מדריך זה מסביר איך להתקין את MHM Pipeline על Windows ו-macOS, ואיך לעקוף את
האזהרה של Windows על "אפליקציה לא מזוהה" — אזהרה שמופיעה לכל קובץ התקנה
שאינו חתום בתעודה דיגיטלית מסחרית.

## Windows

### 1. קבלת קובץ ההתקנה

קובץ ההתקנה הוא קובץ `.exe` בודד בשם `MHMPipeline-Setup-<גרסה>.exe`
(לדוגמה: `MHMPipeline-Setup-0.1.11.exe`). אפשר לשמור אותו בכל מקום — תיקיית
ההורדות בסדר גמור.

### 2. האזהרה "Windows protected your PC"

Windows מציגה את החלון הזה לכל קובץ התקנה שאינה מזהה. **זאת לא תקלה** ולא
סימן לווירוס — פשוט הקובץ אינו חתום בתעודה דיגיטלית בתשלום (MHM Pipeline
הוא פרויקט אקדמי). יש שתי דרכים להמשיך, כל אחת מספיקה:

**שיטה א — מתוך חלון האזהרה (הכי מהיר):**

1. בחלון "Windows protected your PC", ללחוץ על הקישור הקטן **מידע נוסף**
   (More info) שמופיע בחלקו העליון של החלון.
2. ייפתח טקסט נוסף ויופיע כפתור **הפעל בכל זאת** (Run anyway). ללחוץ עליו.
3. ההתקנה תמשיך כרגיל.

**שיטה ב — ביטול חסימה לפני ההפעלה (מונע את האזהרה לחלוטין):**

1. לחיצה ימנית על קובץ ה-`.exe` ובחירה **מאפיינים** (Properties).
2. בלשונית **כללי** (General), בתחתית החלון, לסמן את התיבה **בטל חסימה**
   (Unblock).
3. ללחוץ **אישור** (OK).
4. לחיצה כפולה על הקובץ — האזהרה לא תופיע יותר.

### 3. הפעלת ההתקנה

לקבל את תנאי הרישיון, לבחור תיקיית התקנה (ברירת המחדל בסדר), ולתת לתהליך
להסתיים. ההתקנה יוצרת קיצור דרך **MHM Pipeline** בתפריט התחל ו(אם נבחר) גם
על שולחן העבודה.

### 4. הפעלה ראשונה

לפתוח את **MHM Pipeline** מתפריט התחל. בהפעלה הראשונה האפליקציה מציגה אשף
חד-פעמי שמוריד את קבצי מודל ה-NER (כ-3 ג"ב). אם בכוונתך להשתמש בשלב אימות
ה-AI, יש להחזיק מפתח **Gemini API** מוכן — להדביק אותו בפעם הראשונה דרך
**הגדרות → Credentials & API Keys… (⌘,)**.

## macOS

לפתוח את קובץ ה-`.dmg`, לגרור את **MHM Pipeline** ל**Applications**, ולהפעיל
מ-Launchpad או Spotlight. אם macOS מזהירה שהאפליקציה ממפתח לא מזוהה, לעשות
לחיצה ימנית על הסמל ב-Applications → **פתח** (Open) → **פתח** (Open) בחלון
האישור. אחרי הפעם הראשונה הזו, לחיצה כפולה תעבוד רגיל.

## דרישות מערכת

|  | מינימום | מומלץ |
|---|---|---|
| **מערכת הפעלה** | Windows 10 (64-bit) / macOS 12 | Windows 11 / macOS 14 |
| **זיכרון** | 8 ג"ב | 16 ג"ב |
| **דיסק** | 12 ג"ב פנויים | 20 ג"ב פנויים |
| **GPU** | אין (CPU מספיק) | Apple Silicon (MPS) או NVIDIA CUDA — NER מהיר משמעותית |

האפליקציה עוברת אוטומטית ל-CPU כש-GPU לא זמין.

## עזרה

- בתפריט **Help** של האפליקציה יש נושאי עזרה לכל שלב.
- קבצי לוג נשמרים תחת `~/Library/Logs/MHMPipeline/` (macOS) או
  `%LOCALAPPDATA%\MHMPipeline\Logs\` (Windows) — לשלוח את הקובץ האחרון בכל
  דיווח באג.
