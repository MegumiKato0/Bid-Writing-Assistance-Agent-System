from __future__ import annotations

import re
from typing import Any, Optional

from bidprep.schemas import ContactBlock, ProjectBasicInfo


def _search(pattern: str, text: str, flags: int = 0) -> Optional[re.Match]:
    return re.search(pattern, text, flags)


def extract_rules(full_text: str) -> tuple[ProjectBasicInfo, dict[str, Any], ContactBlock, ContactBlock]:
    """
    规则兜底：返回 (project_basic, raw_hits, purchaser_contact, agency_contact)。
    raw_hits 供 meta 追溯。
    """
    t = full_text.replace("\r", "")
    hits: dict[str, Any] = {}
    pb = ProjectBasicInfo()
    pc = ContactBlock()
    ac = ContactBlock()

    m = _search(r"项目编号[：:\s]*([A-Za-z0-9\-]+)", t)
    if m:
        pb.project_number = m.group(1).strip()
        hits["project_number"] = m.group(0)[:200]

    m = _search(r"项目名称[：:\s]*([^\n]+)", t)
    if m:
        name = re.sub(r"\s+", " ", m.group(1).strip())
        if len(name) > 2:
            pb.project_name = name[:500]
            hits["project_name"] = m.group(0)[:300]

    m = _search(r"采购人[：:\s]*([^\n]+)", t)
    if m:
        pb.purchaser = m.group(1).strip()[:300]
        hits["purchaser"] = m.group(0)[:200]

    m = _search(r"代理机构[：:\s]*([^\n]+)", t)
    if m:
        pb.agency = m.group(1).strip()[:300]
        hits["agency"] = m.group(0)[:200]

    for label, attr in [
        ("最高限价", "max_price"),
        ("最高控制价", "max_price"),
        ("预算金额", "max_price"),
    ]:
        if getattr(pb, "max_price"):
            break
        m = _search(rf"{label}[：:\s]*([^\n]+)", t)
        if m:
            pb.max_price = m.group(1).strip()[:120]
            hits[attr] = m.group(0)[:200]

    m = _search(r"代理服务费[：:\s]*([^\n]+)", t)
    if m:
        pb.agency_fee = m.group(1).strip()[:120]
        hits["agency_fee"] = m.group(0)[:200]

    m = _search(r"谈判保证金|投标保证金|保证金[：:\s]*([^\n]+)", t)
    if m:
        pb.bid_bond = m.group(1).strip()[:120]
        hits["bid_bond"] = m.group(0)[:200]

    m = _search(r"响应文件截止时间[：:\s]*([^\n]+)", t)
    if m:
        pb.bid_deadline = m.group(1).strip()[:200]
        hits["bid_deadline"] = m.group(0)[:250]

    m = _search(
        r"(?:提交到|递交到|提交至|递交至|送达)[：:\s]*([^\n]+)|"
        r"响应文件.*?提交到([^\n]+)",
        t,
    )
    if m:
        loc = (m.group(1) or m.group(2) or "").strip()
        if loc:
            pb.submit_location = loc[:400]
            hits["submit_location"] = m.group(0)[:300]

    m = _search(r"谈判开始时间[：:\s]*([^\n]+)", t)
    if m:
        pb.negotiation_time = m.group(1).strip()[:200]
        hits["negotiation_time"] = m.group(0)[:250]

    # 采购人联系块（简化：九、采购人 后若干行）
    sec = _search(
        r"九[、．.]?\s*采购人[：:\s]*([^\n]+)\s*\n\s*地址[：:\s]*([^\n]+)\s*\n\s*联系人[：:\s]*([^\n]+)",
        t,
    )
    if sec:
        pc.unit = sec.group(1).strip()[:300]
        pc.address = sec.group(2).strip()[:300]
        rest = sec.group(3)
        pm = _search(r"联系方式[：:\s]*([0-9+\-\s]+)", rest) or _search(r"([0-9]{6,})", rest)
        pc.contact = rest.split("联系方式")[0].replace("：", "").strip()[:80] if rest else None
        if pm:
            pc.phone = pm.group(1).strip()[:80]
        hits["purchaser_contact_block"] = sec.group(0)[:400]

    sec2 = _search(
        r"十[、．.]?\s*代理机构[：:\s]*([^\n]+)\s*\n\s*地址[：:\s]*([^\n]+)\s*\n\s*联系人[：:\s]*([^\n]+)",
        t,
    )
    if sec2:
        ac.unit = sec2.group(1).strip()[:300]
        ac.address = sec2.group(2).strip()[:300]
        rest = sec2.group(3)
        ph = _search(r"联系方式[：:\s]*([0-9+\-\s]+)", rest)
        em = _search(r"邮箱[：:\s]*([^\s\n]+)", rest)
        ac.contact = rest.split("联系方式")[0].replace("：", "").strip()[:80] if rest else None
        if ph:
            ac.phone = ph.group(1).strip()[:80]
        if em:
            ac.email = em.group(1).strip()[:120]
        hits["agency_contact_block"] = sec2.group(0)[:400]

    return pb, hits, pc, ac
