#!/usr/bin/env python3
"""全文获取脚本 - Unpaywall Open Access优先 + Sci-Hub回退"""

import argparse
import json
import os
import sys
import time

from coze_workload_identity import requests

SKILL_ID = "7635302993344233472"

# Sci-Hub镜像列表(按可用性排序)
SCIHUB_MIRRORS = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
]


def fetch_unpaywall(doi, email):
    """通过Unpaywall查询Open Access全文URL"""
    if not doi:
        return None
    url = f"https://api.unpaywall.org/v2/{doi}"
    params = {"email": email}
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        best_oa = data.get("best_oa_location") or {}
        oa_url = best_oa.get("url_for_pdf") or best_oa.get("url_for_landing_page")
        if oa_url:
            return {
                "url": oa_url,
                "source": "unpaywall",
                "oa_status": data.get("oa_status", ""),
                "is_oa": data.get("is_oa", False),
            }
    except Exception:
        pass
    return None


def fetch_scihub(doi):
    """通过Sci-Hub获取全文PDF URL"""
    if not doi:
        return None
    for mirror in SCIHUB_MIRRORS:
        try:
            url = f"{mirror}/{doi}"
            resp = requests.get(url, timeout=20, allow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            # 解析Sci-Hub页面提取PDF链接
            html = resp.text
            # Sci-Hub通常在iframe或embed标签中嵌入PDF
            import re
            pdf_patterns = [
                r'(?:iframe|embed)\s+src="([^"]+\.pdf[^"]*)"',
                r'(?:iframe|embed)\s+src="([^"]+)"',
                r'href="([^"]+\.pdf[^"]*)"',
            ]
            for pattern in pdf_patterns:
                matches = re.findall(pattern, html)
                if matches:
                    pdf_url = matches[0]
                    if pdf_url.startswith("//"):
                        pdf_url = "https:" + pdf_url
                    elif pdf_url.startswith("/"):
                        pdf_url = mirror + pdf_url
                    return {"url": pdf_url, "source": "scihub", "mirror": mirror}
        except Exception:
            continue
    return None


def download_pdf(url, output_path, source_name="unknown"):
    """下载PDF文件到指定路径"""
    try:
        resp = requests.get(url, timeout=60, stream=True,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return {"error": f"下载失败: HTTP {resp.status_code}", "source": source_name}

        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type and not url.endswith(".pdf"):
            # 可能不是PDF,尝试保存并检查
            pass

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        file_size = os.path.getsize(output_path)
        if file_size < 1000:
            os.remove(output_path)
            return {"error": "下载文件过小,可能不是有效PDF", "source": source_name}

        return {
            "status": "success",
            "file_path": output_path,
            "file_size": file_size,
            "source": source_name,
        }
    except Exception as e:
        return {"error": f"下载异常: {str(e)}", "source": source_name}


def main():
    parser = argparse.ArgumentParser(description="获取学术论文全文PDF")
    parser.add_argument("--doi", help="论文DOI号")
    parser.add_argument("--url", help="直接提供PDF下载URL")
    parser.add_argument("--output_dir",
                        default=os.environ.get("ACADEMIC_PDF_DIR", "./academic_pdfs"),
                        help="输出目录(可通过环境变量ACADEMIC_PDF_DIR覆盖)")
    parser.add_argument("--filename", help="输出文件名(不含路径)")
    parser.add_argument("--email", default="research@academic.edu", help="Unpaywall查询用邮箱")
    parser.add_argument("--prefer_open_access", action="store_true", default=True,
                        help="优先尝试Open Access(默认开启)")
    parser.add_argument("--scihub_fallback", action="store_true", default=True,
                        help="Open Access失败时回退到Sci-Hub(默认开启)")
    args = parser.parse_args()

    if not args.doi and not args.url:
        result = {"error": "请提供 --doi 或 --url 参数"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 直接URL下载
    if args.url:
        filename = args.filename or args.url.split("/")[-1].split("?")[0] or "paper.pdf"
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        output_path = os.path.join(args.output_dir, filename)
        result = download_pdf(args.url, output_path, "direct_url")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # DOI检索下载
    filename = args.filename or f"{args.doi.replace('/', '_')}.pdf"
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    output_path = os.path.join(args.output_dir, filename)

    pdf_url = None
    source = None

    # Step 1: Unpaywall Open Access
    if args.prefer_open_access:
        oa_result = fetch_unpaywall(args.doi, args.email)
        if oa_result:
            pdf_url = oa_result["url"]
            source = f"unpaywall({oa_result.get('oa_status', '')})"

    # Step 2: Sci-Hub回退
    if not pdf_url and args.scihub_fallback:
        sh_result = fetch_scihub(args.doi)
        if sh_result:
            pdf_url = sh_result["url"]
            source = f"scihub({sh_result.get('mirror', '')})"

    if not pdf_url:
        result = {
            "error": "无法获取全文PDF",
            "doi": args.doi,
            "tried": ["unpaywall", "scihub"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 下载PDF
    result = download_pdf(pdf_url, output_path, source)
    result["doi"] = args.doi
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
