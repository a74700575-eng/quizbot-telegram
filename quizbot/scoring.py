"""دوال حساب النقاط البحتة (من غير تليجرام) عشان تتختبر بسهولة."""
from collections import defaultdict


def majority(votes, leader_choice=None):
    """votes: [(choice, ts)] → (الاختيار الفائز، وقت الصوت اللي حسم الأغلبية).
    لو تعادل: يفوز اختيار قائد الفريق لو كان من المتعادلين، وإلا الاختيار اللي وصل لأعلى عدد أسرع."""
    by = defaultdict(list)
    for ch, ts in votes:
        by[ch].append(ts)
    if not by:
        return None, None
    top = max(len(v) for v in by.values())
    tied = {ch: sorted(v) for ch, v in by.items() if len(v) == top}
    if len(tied) > 1 and leader_choice in tied:
        ch = leader_choice
    else:
        ch = min(tied, key=lambda c: (tied[c][top - 1], c))
    return ch, tied[ch][top - 1]


def speed_bonus(points, bonus_pct, elapsed, total):
    """مكافأة بتقل خطيًا من bonus_pct% من النقاط (لو الفريق حسم فورًا) لحد صفر (عند آخر ثانية)."""
    if bonus_pct <= 0 or total <= 0 or points <= 0:
        return 0
    frac = max(0.0, min(1.0, 1 - elapsed / total))
    return round(points * bonus_pct / 100 * frac)


def participation_ok(voters, members, min_pct):
    return voters > 0 and members > 0 and voters * 100 >= min_pct * members


def ranked(rows):
    """يضيف الترتيب (التعادل في النقاط والإجابات الصح = نفس المركز)."""
    out, rank, prev = [], 0, None
    for i, r in enumerate(rows):
        key = (r["pts"], r["ok"])
        if key != prev:
            rank, prev = i + 1, key
        out.append((rank, r))
    return out


def bar(left, total, width=10):
    total = max(total, 1)
    filled = max(0, min(width, round(width * left / total)))
    return "▰" * filled + "▱" * (width - filled)
