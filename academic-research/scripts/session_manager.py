#!/usr/bin/env python3
"""Session状态管理脚本 - JSON文件持久化，支持多轮对话状态保持"""

import argparse
import json
import os
import sys
import time
import uuid


SESSION_DIR = os.path.join(os.path.expanduser("~"), ".academic_research_sessions")


def _ensure_dir():
    os.makedirs(SESSION_DIR, exist_ok=True)


def _session_path(session_id):
    return os.path.join(SESSION_DIR, f"{session_id}.json")


def create_session(topic, metadata=None):
    """创建新会话"""
    _ensure_dir()
    session_id = f"sess_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    session = {
        "session_id": session_id,
        "topic": topic,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": "initialized",
        "metadata": metadata or {},
        "search_results": [],
        "filtered_results": {"classic": [], "frontier": []},
        "confirmed_papers": [],
        "confirmed_ids": [],          # paper_id列表，用于去重
        "downloaded_pdfs": {},       # {paper_id: {status, path, source, error, retry_count}}
        "parsed_contents": {},       # {paper_id: parsed_json_path}
        "failed_papers": {},         # {paper_id: {reason, retry_count, last_attempt, mirrors_tried}}
        "snowballing_results": [],    # 引用追溯补充的论文
        "zotero_items": {},          # {paper_id: item_key}
        "cross_paper_comparisons": {},  # {paper_id: comparison_note}
    }
    with open(_session_path(session_id), "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return session


def load_session(session_id):
    """加载会话"""
    path = _session_path(session_id)
    if not os.path.exists(path):
        return {"error": f"会话不存在: {session_id}"}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_session(session_id, updates):
    """更新会话（合并更新，保留未覆盖的字段）"""
    path = _session_path(session_id)
    if not os.path.exists(path):
        return {"error": f"会话不存在: {session_id}"}
    with open(path, "r", encoding="utf-8") as f:
        session = json.load(f)
    session.update(updates)
    session["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return session


def update_state(session_id, new_state, extra=None):
    """更新会话状态机
    状态流:
      initialized -> searching -> filtering -> confirming
      -> parsing -> storing -> snowballing -> completed
    """
    valid_states = ["initialized", "searching", "filtering", "confirming",
                    "parsing", "storing", "snowballing", "completed"]
    if new_state not in valid_states:
        return {"error": f"无效状态: {new_state}, 有效值: {valid_states}"}
    updates = {"state": new_state}
    if extra:
        updates.update(extra)
    return save_session(session_id, updates)


def list_sessions():
    """列出所有会话"""
    _ensure_dir()
    sessions = []
    for fname in sorted(os.listdir(SESSION_DIR), reverse=True):
        if fname.endswith(".json"):
            path = os.path.join(SESSION_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                sessions.append({
                    "session_id": s.get("session_id", ""),
                    "topic": s.get("topic", ""),
                    "state": s.get("state", ""),
                    "created_at": s.get("created_at", ""),
                    "updated_at": s.get("updated_at", ""),
                    "confirmed_count": len(s.get("confirmed_papers", [])),
                    "failed_count": len(s.get("failed_papers", {})),
                    "snowballing_count": len(s.get("snowballing_results", [])),
                })
            except Exception:
                pass
    return {"total": len(sessions), "sessions": sessions}


def delete_session(session_id):
    """删除会话"""
    path = _session_path(session_id)
    if not os.path.exists(path):
        return {"error": f"会话不存在: {session_id}"}
    os.remove(path)
    return {"status": "deleted", "session_id": session_id}


def add_search_results(session_id, results):
    """追加检索结果到会话"""
    session = load_session(session_id)
    if "error" in session:
        return session
    existing_ids = {p.get("paper_id") or p.get("doi") for p in session.get("search_results", [])}
    new_papers = results if isinstance(results, list) else results.get("papers", [])
    added = []
    for p in new_papers:
        pid = p.get("paper_id") or p.get("doi")
        if pid not in existing_ids:
            session.setdefault("search_results", []).append(p)
            existing_ids.add(pid)
            added.append(pid)
    return save_session(session_id, {
        "search_results": session["search_results"],
        "state": "searching",
        "added_count": len(added),
    })


def add_confirmed_papers(session_id, paper_ids):
    """记录用户确认的论文"""
    session = load_session(session_id)
    if "error" in session:
        return session
    all_papers = (session.get("search_results", [])
                   + session.get("filtered_results", {}).get("classic", [])
                   + session.get("filtered_results", {}).get("frontier", []))
    paper_map = {p.get("paper_id") or p.get("doi"): p for p in all_papers}
    confirmed = session.get("confirmed_papers", [])
    confirmed_ids = set(session.get("confirmed_ids", []))
    added = []
    for pid in paper_ids:
        if pid not in confirmed_ids and pid in paper_map:
            confirmed.append(paper_map[pid])
            confirmed_ids.add(pid)
            added.append(pid)
    return save_session(session_id, {
        "confirmed_papers": confirmed,
        "confirmed_ids": list(confirmed_ids),
        "state": "confirming",
        "added": added,
    })


def mark_pdf_downloaded(session_id, paper_id, status, path=None, source=None, error=None):
    """标记PDF下载结果"""
    session = load_session(session_id)
    if "error" in session:
        return session
    record = {
        "status": status,   # "success" | "failed"
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if path:
        record["path"] = path
    if source:
        record["source"] = source
    if error:
        record["error"] = error
        # 自动加入failed_papers
        failed = session.get("failed_papers", {})
        if paper_id not in failed:
            failed[paper_id] = {
                "reason": error,
                "retry_count": 0,
                "last_attempt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "mirrors_tried": [],
            }
        else:
            failed[paper_id]["retry_count"] = failed[paper_id].get("retry_count", 0) + 1
            failed[paper_id]["last_attempt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        session["failed_papers"] = failed

    session.setdefault("downloaded_pdfs", {})[paper_id] = record
    return save_session(session_id, {"downloaded_pdfs": session["downloaded_pdfs"]})


def get_failed_papers(session_id):
    """获取需要重试的论文列表"""
    session = load_session(session_id)
    if "error" in session:
        return session
    failed = session.get("failed_papers", {})
    return {
        "total": len(failed),
        "papers": [
            {
                "paper_id": pid,
                "retry_count": info.get("retry_count", 0),
                "last_attempt": info.get("last_attempt", ""),
                "mirrors_tried": info.get("mirrors_tried", []),
                "reason": info.get("reason", ""),
            }
            for pid, info in failed.items()
        ]
    }


def clear_failed_paper(session_id, paper_id):
    """清除失败记录（重试成功后调用）"""
    session = load_session(session_id)
    if "error" in session:
        return session
    failed = session.get("failed_papers", {})
    if paper_id in failed:
        del failed[paper_id]
        session["failed_papers"] = failed
    return save_session(session_id, {"failed_papers": failed})


def add_snowballing_results(session_id, papers):
    """追加引用追溯结果"""
    session = load_session(session_id)
    if "error" in session:
        return session
    existing_ids = {p.get("paper_id") or p.get("doi")
                    for p in session.get("snowballing_results", [])}
    added = []
    for p in papers:
        pid = p.get("paper_id") or p.get("doi")
        if pid not in existing_ids:
            session.setdefault("snowballing_results", []).append(p)
            existing_ids.add(pid)
            added.append(pid)
    return save_session(session_id, {
        "snowballing_results": session["snowballing_results"],
        "state": "snowballing",
        "added_count": len(added),
    })


def save_cross_paper_comparison(session_id, paper_id, comparison_text):
    """保存文献对比笔记"""
    session = load_session(session_id)
    if "error" in session:
        return session
    session.setdefault("cross_paper_comparisons", {})[paper_id] = comparison_text
    return save_session(session_id, {"cross_paper_comparisons": session["cross_paper_comparisons"]})


def get_session_summary(session_id):
    """获取会话完整摘要（用于恢复/汇报）"""
    session = load_session(session_id)
    if "error" in session:
        return session
    downloaded = session.get("downloaded_pdfs", {})
    parsed = session.get("parsed_contents", {})
    failed = session.get("failed_papers", {})
    confirmed = session.get("confirmed_papers", [])
    zotero = session.get("zotero_items", {})
    return {
        "session_id": session_id,
        "topic": session.get("topic", ""),
        "state": session.get("state", ""),
        "progress": {
            "confirmed_total": len(confirmed),
            "downloaded_success": sum(1 for v in downloaded.values() if v.get("status") == "success"),
            "downloaded_failed": len(failed),
            "parsed_count": len(parsed),
            "zotero_items": len(zotero),
            "snowballing_added": len(session.get("snowballing_results", [])),
        },
        "failed_papers": [
            {"paper_id": pid, "retry_count": info.get("retry_count", 0), "reason": info.get("reason", "")}
            for pid, info in failed.items()
        ],
        "updated_at": session.get("updated_at", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Session状态管理（增强断点续传）")
    parser.add_argument("--operation", required=True,
                        choices=["create", "load", "save", "update_state",
                                 "list", "delete", "add_search_results",
                                 "add_confirmed_papers", "mark_downloaded",
                                 "get_failed", "clear_failed",
                                 "add_snowballing", "save_comparison",
                                 "summary"],
                        help="操作类型")
    parser.add_argument("--session_id", help="会话ID")
    parser.add_argument("--topic", help="研究主题(create时使用)")
    parser.add_argument("--state", help="新状态(update_state时使用)")
    parser.add_argument("--data", help="JSON数据字符串")
    parser.add_argument("--data_file", help="JSON数据文件路径")
    parser.add_argument("--paper_ids", help="确认的论文ID列表(JSON数组字符串)")
    parser.add_argument("--paper_id", help="单篇论文ID")
    parser.add_argument("--download_status", help="下载状态(success/failed)")
    parser.add_argument("--download_path", help="下载文件路径")
    parser.add_argument("--download_source", help="下载来源(unpaywall/scihub)")
    parser.add_argument("--download_error", help="下载错误信息")
    parser.add_argument("--comparison_text", help="对比笔记内容")
    args = parser.parse_args()

    data = None
    if args.data:
        data = json.loads(args.data)
    elif args.data_file:
        with open(args.data_file, "r", encoding="utf-8") as f:
            data = json.load(f)

    if args.operation == "create":
        result = create_session(args.topic or "未命名研究", data)
    elif args.operation == "load":
        if not args.session_id:
            result = {"error": "需要 --session_id"}
        else:
            result = load_session(args.session_id)
    elif args.operation == "save":
        if not args.session_id or not data:
            result = {"error": "需要 --session_id 和 --data"}
        else:
            result = save_session(args.session_id, data)
    elif args.operation == "update_state":
        if not args.session_id or not args.state:
            result = {"error": "需要 --session_id 和 --state"}
        else:
            result = update_state(args.session_id, args.state, data)
    elif args.operation == "list":
        result = list_sessions()
    elif args.operation == "delete":
        if not args.session_id:
            result = {"error": "需要 --session_id"}
        else:
            result = delete_session(args.session_id)
    elif args.operation == "add_search_results":
        if not args.session_id or not data:
            result = {"error": "需要 --session_id 和 --data"}
        else:
            result = add_search_results(args.session_id, data)
    elif args.operation == "add_confirmed_papers":
        if not args.session_id or not args.paper_ids:
            result = {"error": "需要 --session_id 和 --paper_ids"}
        else:
            pids = json.loads(args.paper_ids)
            result = add_confirmed_papers(args.session_id, pids)
    elif args.operation == "mark_downloaded":
        if not args.session_id or not args.paper_id:
            result = {"error": "需要 --session_id 和 --paper_id"}
        else:
            result = mark_pdf_downloaded(
                args.session_id, args.paper_id,
                args.download_status or "unknown",
                args.download_path, args.download_source, args.download_error)
    elif args.operation == "get_failed":
        if not args.session_id:
            result = {"error": "需要 --session_id"}
        else:
            result = get_failed_papers(args.session_id)
    elif args.operation == "clear_failed":
        if not args.session_id or not args.paper_id:
            result = {"error": "需要 --session_id 和 --paper_id"}
        else:
            result = clear_failed_paper(args.session_id, args.paper_id)
    elif args.operation == "add_snowballing":
        if not args.session_id or not data:
            result = {"error": "需要 --session_id 和 --data"}
        else:
            papers = data if isinstance(data, list) else data.get("papers", [])
            result = add_snowballing_results(args.session_id, papers)
    elif args.operation == "save_comparison":
        if not args.session_id or not args.paper_id or not args.comparison_text:
            result = {"error": "需要 --session_id --paper_id 和 --comparison_text"}
        else:
            result = save_cross_paper_comparison(args.session_id, args.paper_id, args.comparison_text)
    elif args.operation == "summary":
        if not args.session_id:
            result = {"error": "需要 --session_id"}
        else:
            result = get_session_summary(args.session_id)
    else:
        result = {"error": f"未知操作: {args.operation}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
