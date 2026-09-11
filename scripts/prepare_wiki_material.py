#!/usr/bin/env python3
"""Download a Confluence page tree into reviewable Markdown material."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_SCRIPTS = Path("/Users/lbc/.agents/skills/confluence/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))
from api import ConfluenceAPI, load_config  # noqa: E402


ROOT_ID = "70331927"
OUTPUT_DIR = Path("data_material/wiki") / ROOT_ID


def safe_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", value).strip(" .")
    return (value or "untitled")[:120]


def flatten(nodes: list[dict], parent_id: str) -> list[dict]:
    result = []
    for node in nodes:
        item = {
            "id": str(node.get("id", "")),
            "title": node.get("title", ""),
            "level": int(node.get("level", 1)),
            "parent_id": parent_id,
            "url": node.get("url", ""),
        }
        result.append(item)
        result.extend(flatten(node.get("children", []), item["id"]))
    return result


def main() -> None:
    base_url = os.getenv("CONFLUENCE_BASE_URL")
    cookie = os.getenv("CONFLUENCE_COOKIE")
    if not cookie:
        raise SystemExit("CONFLUENCE_COOKIE is required; it is never written to disk")

    config = load_config()
    if base_url:
        config["base_url"] = base_url
    config["cookie_header"] = cookie
    config["process_images"] = False
    api = ConfluenceAPI(config)

    root = api.get_page(ROOT_ID)
    if not root:
        raise SystemExit(f"Cannot read root page {ROOT_ID}")

    tree = api.get_children_tree(ROOT_ID, depth=-1)
    pages = [{"id": ROOT_ID, "title": root.get("title", "征信"), "level": 0,
              "parent_id": None, "url": root.get("url", "")}]
    pages.extend(flatten(tree, ROOT_ID))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "root_id": ROOT_ID,
        "base_url": api.base_url,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(pages),
        "pages": [],
    }
    used_names: set[str] = set()
    for page in pages:
        detail = api.get_page(page["id"])
        record = {**page, "status": "ok" if detail else "failed", "content_length": 0}
        if detail:
            content = detail.get("content_text", "")
            record["content_length"] = len(content)
            stem = safe_name(f"{page['id']}_{page['title']}")
            if stem in used_names:
                stem = f"{stem}_{page['id']}"
            used_names.add(stem)
            output_path = OUTPUT_DIR / f"{stem}.md"
            frontmatter = {
                "source_type": "wiki",
                "page_id": page["id"],
                "title": page["title"],
                "parent_id": page["parent_id"],
                "level": page["level"],
                "url": detail.get("url", page.get("url", "")),
                "version": detail.get("version", 0),
                "last_modified": detail.get("lastModified", ""),
            }
            output_path.write_text(
                "---\n" + json.dumps(frontmatter, ensure_ascii=False, indent=2)
                + "\n---\n\n" + content.strip() + "\n",
                encoding="utf-8",
            )
            record["path"] = str(output_path)
        manifest["pages"].append(record)

    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ok = sum(1 for page in manifest["pages"] if page["status"] == "ok")
    print(f"downloaded={ok}/{len(pages)} output={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
