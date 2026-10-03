"""حالة المسابقة الشغالة (في الذاكرة) والقفل المشترك."""
import asyncio

LOCK = asyncio.Lock()
RT: dict = {}  # RT["c"] = المسابقة الشغالة حاليًا
