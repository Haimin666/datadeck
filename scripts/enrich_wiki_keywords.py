#!/usr/bin/env python3
"""Extract focused Wiki evidence by keyword for manual business review."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/Users/lbc/.agents/skills/confluence/scripts")
from api import ConfluenceAPI, load_config  # noqa: E402


ROOT_ID = "70331927"
KEYWORDS = ["征信", "指标", "口径", "规则", "上报", "逾期", "呆账", "贷款", "数据", "数据库", "流程", "监控"]
OUT = Path("data_material/delivery")


def main() -> None:
    cookie = os.getenv("CONFLUENCE_COOKIE")
    if not cookie:
        raise SystemExit("CONFLUENCE_COOKIE is required; it is never written to disk")
    config = load_config()
    config["cookie_header"] = cookie
    config["process_images"] = False
    api = ConfluenceAPI(config)
    records = []
    seen = set()
    for keyword in KEYWORDS:
        hits = api.search(keyword, limit=100, content_type="page", parent_id=ROOT_ID)
        for hit in hits:
            page_id = str(hit.get("id", ""))
            key = (keyword, page_id)
            if key in seen:
                continue
            seen.add(key)
            snippets = api.search_in_page(page_id, keyword, context_lines=3)
            records.append({
                "keyword": keyword,
                "page_id": page_id,
                "title": hit.get("title", ""),
                "url": hit.get("url", ""),
                "snippets": snippets[:10],
                "status": "needs_review",
            })
    records.sort(key=lambda x: (x["keyword"], x["page_id"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "wiki_keyword_extract.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    lines = ["# Wiki 关键词业务证据提取", "", "> 所有命中内容均为候选证据，需结合版本、代码实现和业务人员确认。", ""]
    for item in records:
        lines.extend([f"## {item['keyword']} · {item['title']}", f"- 页面 ID：`{item['page_id']}`", f"- 来源：{item['url']}", ""])
        for snippet in item["snippets"]:
            lines.extend([f"### 片段 {snippet.get('position', '')}", snippet.get("content", ""), ""])
    (OUT / "wiki_keyword_extract.md").write_text("\n".join(lines), encoding="utf-8")
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "keywords": KEYWORDS, "evidence_records": len(records), "pages": len({x["page_id"] for x in records})}
    (OUT / "wiki_keyword_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
