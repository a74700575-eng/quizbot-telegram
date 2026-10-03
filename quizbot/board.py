"""لوحة الترتيب."""
from .config import MEDALS
from .db import db
from .scoring import ranked
from .ui import esc


def latest_contest():
    r = db.q("SELECT id FROM contests ORDER BY (status='running') DESC, id DESC LIMIT 1", one=True)
    return r["id"] if r else None


def board_rows(cid):
    """نقاط كل فريق في المسابقة. الفرق اللي اتحذفت بعد ما أخدت نقاط بتفضل ظاهرة (n=0)."""
    got = {
        r["team_id"]: {"id": r["team_id"], "name": r["name"], "pts": r["pts"], "ok": r["ok"], "n": 0}
        for r in db.q(
            "SELECT team_id, MAX(team_name) name, SUM(points) pts, SUM(is_correct) ok "
            "FROM results WHERE contest_id=? GROUP BY team_id",
            (cid,),
        )
    }
    for t in db.q(
        "SELECT t.id, t.name, (SELECT COUNT(*) FROM members m WHERE m.team_id=t.id) n FROM teams t"
    ):
        if t["n"] == 0:
            continue
        row = got.setdefault(t["id"], {"id": t["id"], "name": t["name"], "pts": 0, "ok": 0, "n": 0})
        row["name"], row["n"] = t["name"], t["n"]
    return sorted(got.values(), key=lambda r: (-r["pts"], -r["ok"], r["name"]))


def board_text(rows, highlight=None, limit=10, title="🏆 <b>الترتيب</b>", admin=False):
    if not rows:
        return title + "\n\nمفيش فرق لسه."
    out, mine = [title, ""], None
    for i, (rank, r) in enumerate(ranked(rows)):
        medal = MEDALS.get(rank, f"{rank}.")
        extra = (f" · {r['n']} أعضاء" if r["n"] else " · (اتحذف)") if admin else ""
        line = f"{medal} {'👉 ' if r['id'] == highlight else ''}<b>{esc(r['name'])}</b> — {r['pts']} نقطة ({r['ok']}✅){extra}"
        if r["id"] == highlight:
            mine = line
        if i < limit:
            out.append(line)
    if mine and mine not in out:
        out += ["…", mine]
    return "\n".join(out)
