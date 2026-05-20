from __future__ import annotations

import re
from bidprep.phase2.schemas import ClientFile

# 粗分类：规则层，供报表与后续扩展 LLM 使用
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("企业资质类", ("营业执照", "执业", "资质证书", "法人", "登记", "统一社会信用")),
    ("人员身份证明类", ("身份证", "身份证明", "户籍")),
    ("财税社保类", ("税收", "纳税", "完税", "社保", "缴费", "保障资金", "财务报告", "审计报告", "财务报表")),
    ("授权声明类", ("授权", "委托", "声明", "承诺函", "公函")),
    ("信用报告类", ("信用", "征信", "失信", "承诺书", "信用承诺")),
    ("报价响应类", ("报价", "一览表", "响应", "偏离", "谈判")),
    ("技术方案类", ("方案", "技术", "实施", "审计方案", "服务方案")),
    ("合同文件类", ("合同", "约定书", "协议书")),
]


def classify_client_file(client: ClientFile, text: str) -> str:
    blob = f"{client.original_filename}\n{client.rel_path}\n{text[:6000]}"
    blob_l = blob
    for label, kws in _RULES:
        for kw in kws:
            if kw in blob_l:
                return label
    return "其他类"


def apply_classification(clients: list[ClientFile]) -> None:
    for c in clients:
        c.coarse_category = classify_client_file(c, c.extracted_text)


def split_material_phrases(name: str) -> list[str]:
    """将清单名称拆成若干可能出现在文件名中的短语。"""
    parts = re.split(r"[、，,/|；;（）()【】\[\]及和与或]", name)
    out: list[str] = []
    for p in parts:
        s = p.strip()
        if len(s) >= 2:
            out.append(s)
    if not out and name.strip():
        out.append(name.strip())
    return out
