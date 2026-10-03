"""بنك الأسئلة + استيراد JSON / HTML."""
import json
import time
from html.parser import HTMLParser

from .db import db

MAX_Q, MAX_OPT, MAX_EXPL = 300, 100, 200


def get_question(qid):
    return db.q("SELECT * FROM questions WHERE id=?", (qid,), one=True)


def insert_question(text, opts, correct, expl="", tl=None, pts=None, image=None):
    return db.x(
        "INSERT INTO questions(text,options,correct,explanation,time_limit,points,created_at,image) VALUES(?,?,?,?,?,?,?,?)",
        (text, json.dumps(opts, ensure_ascii=False), correct, expl or "", tl, pts, time.time(), image or None),
    )


def _int_in(v, lo, hi, name):
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} لازم يكون رقم")
    if not lo <= n <= hi:
        raise ValueError(f"{name} لازم يكون بين {lo} و{hi}")
    return n


def validate(it):
    """يتحقق من سؤال واحد (dict) ويرجّعه منضّف، أو يرمي ValueError/TypeError برسالة واضحة."""
    if not isinstance(it, dict):
        raise TypeError("الصف مش كائن JSON")
    text = str(it.get("question") or "").strip()
    if not text:
        raise ValueError("نص السؤال فاضي")
    if len(text) > MAX_Q:
        raise ValueError(f"نص السؤال أطول من {MAX_Q} حرف")
    raw_opts = it.get("options")
    if not isinstance(raw_opts, list):
        raise TypeError("options لازم تكون قائمة")
    opts = [str(o).strip() for o in raw_opts]
    if not 2 <= len(opts) <= 10:
        raise ValueError("عدد الاختيارات لازم من 2 لـ 10")
    if any(not o or len(o) > MAX_OPT for o in opts):
        raise ValueError(f"كل اختيار لازم يكون من 1 لـ {MAX_OPT} حرف")
    ans = it.get("answer", it.get("correct"))
    try:
        ans = int(ans) - 1
    except (TypeError, ValueError):
        raise ValueError("answer ناقص أو مش رقم")
    if not 0 <= ans < len(opts):
        raise ValueError("answer خارج نطاق الاختيارات")
    image = str(it.get("image") or "").strip() or None
    if image and len(image) > 500:
        raise ValueError("رابط الصورة طويل")
    return {
        "text": text,
        "opts": opts,
        "correct": ans,
        "expl": str(it.get("explanation") or "").strip()[:MAX_EXPL],
        "tl": _int_in(it.get("time"), 5, 600, "time"),
        "pts": _int_in(it.get("points"), 1, 1000, "points"),
        "image": image,
    }


# ───────────── HTML ─────────────
class _QParser(HTMLParser):
    """كل سؤال جوه عنصر class="q" (أو "question"):
    <div class="q" data-answer="2" data-time="45" data-points="10" data-image="URL">
      <h3>نص السؤال</h3>
      <ol><li>اختيار</li><li class="correct">الصحيح</li></ol>
      <p class="exp">الشرح</p>
    </div>
    الإجابة الصحيحة: data-answer (من 1) أو class="correct" على الـ li."""

    BLOCKS = ("div", "section", "article")
    HEADS = ("h1", "h2", "h3", "h4", "h5", "h6")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.cur, self.depth = [], None, 0
        self.role = self.role_tag = None
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        cls = set(a.get("class", "").split())
        if self.cur is None:
            if tag in self.BLOCKS and cls & {"q", "question"}:
                self.cur = {"options": [], "correct_idx": None, "text": "", "explanation": "", "attrs": a, "image": None}
                self.depth = 1
            return
        if tag in self.BLOCKS:
            self.depth += 1
        if tag == "img" and not self.cur["image"]:
            self.cur["image"] = a.get("src") or None
        if self.role is None:
            role = None
            if tag == "li":
                role = "opt"
                if "correct" in cls:
                    self.cur["correct_idx"] = len(self.cur["options"])
            elif cls & {"exp", "explanation"}:
                role = "exp"
            elif not self.cur["text"] and (tag in self.HEADS or tag == "p" or cls & {"text", "qt", "question-text"}):
                role = "text"
            if role:
                self.role, self.role_tag, self.buf = role, tag, []

    def handle_data(self, data):
        if self.role:
            self.buf.append(data)

    def handle_endtag(self, tag):
        if self.cur is None:
            return
        if self.role and tag == self.role_tag:
            val = " ".join("".join(self.buf).split())
            if self.role == "opt":
                self.cur["options"].append(val)
            elif self.role == "exp":
                self.cur["explanation"] = val
            else:
                self.cur["text"] = val
            self.role = self.role_tag = None
        if tag in self.BLOCKS:
            self.depth -= 1
            if self.depth == 0:
                self._finish()

    def _finish(self):
        c, a = self.cur, self.cur["attrs"]
        ans = a.get("data-answer") or (c["correct_idx"] + 1 if c["correct_idx"] is not None else None)
        self.items.append(
            {
                "question": c["text"], "options": c["options"], "answer": ans, "explanation": c["explanation"],
                "time": a.get("data-time"), "points": a.get("data-points"), "image": a.get("data-image") or c["image"],
            }
        )
        self.cur = None


def parse_html(raw):
    p = _QParser()
    p.feed(raw)
    p.close()
    if not p.items:
        raise ValueError('مالقيتش أي عنصر class="q" في ملف الـ HTML')
    return p.items


def load_items(raw):
    """يكتشف الصيغة (JSON أو HTML) ويرجّع قائمة dicts خام."""
    s = raw.lstrip()
    if s.startswith(("[", "{")):
        data = json.loads(s)
        if isinstance(data, dict):
            data = data.get("questions", [data])
        if not isinstance(data, list):
            raise ValueError("لازم قائمة أسئلة")
        return data
    return parse_html(raw)


def import_questions(raw):
    """يرجّع (عدد المضاف، عدد المكرر، [رسائل الأخطاء])."""
    items = load_items(raw)
    existing = {(r["text"], r["options"]) for r in db.q("SELECT text, options FROM questions")}
    ok = dup = 0
    errors = []
    for i, it in enumerate(items, 1):
        try:
            v = validate(it)
        except (ValueError, TypeError) as e:
            errors.append(f"#{i}: {e}")
            continue
        key = (v["text"], json.dumps(v["opts"], ensure_ascii=False))
        if key in existing:
            dup += 1
            continue
        existing.add(key)
        insert_question(v["text"], v["opts"], v["correct"], v["expl"], v["tl"], v["pts"], v["image"])
        ok += 1
    return ok, dup, errors
