#!/usr/bin/env python3
"""
AI検索 露出モニター（給湯器交換の匠）

複数のAI（Gemini / ChatGPT / Claude / Perplexity）に検索付きで質問し、
・当社が言及されたか / 何番目に推薦されたか
・類似名称の他社と混同されていないか
・電話番号などの情報が正しいか
を記録し、週次サマリーと改善提案を作ります。

使い方:
  python monitor.py            # 本番実行
  python monitor.py --dry-run  # APIを呼ばずダミーデータで動作確認
  python monitor.py --limit 2  # 質問を先頭2問だけにして試す
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
JST = dt.timezone(dt.timedelta(hours=9))
TIMEOUT = 180

LOG_HEADER = [
    "日付", "AI", "質問", "当社言及", "当社順位", "推薦された業者（順）",
    "類似名称混同", "回答内の電話番号", "要確認", "参照URL", "回答本文", "推定コスト(USD)", "エラー", "当社サイト参照",
]
SUMMARY_HEADER = ["日付", "AI", "質問数", "言及数", "言及率", "平均順位", "混同件数", "要確認件数", "合計コスト(USD)",
                  "当社サイト参照数"]
AI_LABEL = {"gemini": "Gemini", "openai": "ChatGPT", "claude": "Claude", "perplexity": "Perplexity"}


# ------------------------------------------------------------
# 共通
# ------------------------------------------------------------
def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def post_json(url: str, headers: dict, body: dict, retries: int = 2) -> dict:
    last = None
    for i in range(retries + 1):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504) and i < retries:
                time.sleep(10 * (i + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        except requests.RequestException as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"通信エラー: {last}")


def cost(cfg: dict, provider: str, tin: int, tout: int, searches: int) -> float:
    p = cfg.get("pricing", {}).get(provider, {})
    return round(
        tin / 1e6 * p.get("input_per_m", 0)
        + tout / 1e6 * p.get("output_per_m", 0)
        + searches * p.get("per_search", 0),
        5,
    )


# ------------------------------------------------------------
# 各AIへの質問（検索付き）
# 戻り値: {"text": 回答, "urls": [参照URL], "cost": USD}
# ------------------------------------------------------------
def ask_gemini(cfg, q):
    key = os.environ["GEMINI_API_KEY"]
    model = cfg["providers"]["gemini"]["model"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    d = post_json(
        url,
        {"x-goog-api-key": key, "Content-Type": "application/json"},
        {"contents": [{"parts": [{"text": q}]}], "tools": [{"google_search": {}}]},
    )
    cand = (d.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    gm = cand.get("groundingMetadata", {})
    # Geminiの参照URLは転送用URLなので、タイトル（ドメイン名）も並べて残す
    urls = [
        f'{c["web"].get("title", "")} {c["web"].get("uri", "")}'.strip()
        for c in gm.get("groundingChunks", []) if "web" in c
    ]
    um = d.get("usageMetadata", {})
    searches = 1 if gm.get("webSearchQueries") else 0
    return {
        "text": text,
        "urls": urls,
        "cost": cost(cfg, "gemini", um.get("promptTokenCount", 0), um.get("candidatesTokenCount", 0), searches),
    }


def ask_openai(cfg, q):
    key = os.environ["OPENAI_API_KEY"]
    d = post_json(
        "https://api.openai.com/v1/responses",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": cfg["providers"]["openai"]["model"],
            "input": q,
            "tools": [{
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "JP", "region": "Osaka"},
            }],
        },
    )
    text, urls, searches = "", [], 0
    for item in d.get("output", []):
        if item.get("type") == "web_search_call":
            searches += 1
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
                    for a in c.get("annotations", []):
                        if a.get("url"):
                            urls.append(a["url"])
    u = d.get("usage", {})
    return {"text": text, "urls": urls,
            "cost": cost(cfg, "openai", u.get("input_tokens", 0), u.get("output_tokens", 0), searches)}


def ask_claude(cfg, q):
    key = os.environ["ANTHROPIC_API_KEY"]
    pc = cfg["providers"]["claude"]
    d = post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        {
            "model": pc["model"],
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": q}],
            "tools": [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": pc.get("max_searches", 3),
                "user_location": {"type": "approximate", "country": "JP", "region": "Osaka",
                                  "timezone": "Asia/Tokyo"},
            }],
        },
    )
    text, urls = "", []
    for b in d.get("content", []):
        if b.get("type") == "text":
            text += b.get("text", "")
            for c in b.get("citations") or []:
                if c.get("url"):
                    urls.append(c["url"])
        elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            urls += [r["url"] for r in b["content"] if r.get("url")]
    u = d.get("usage", {})
    searches = (u.get("server_tool_use") or {}).get("web_search_requests", 0)
    return {"text": text, "urls": urls,
            "cost": cost(cfg, "claude", u.get("input_tokens", 0), u.get("output_tokens", 0), searches)}


def ask_perplexity(cfg, q):
    key = os.environ["PERPLEXITY_API_KEY"]
    d = post_json(
        "https://api.perplexity.ai/v1/agent",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": cfg["providers"]["perplexity"]["model"],
            "input": q,
            "tools": [{"type": "web_search"}],
            "max_output_tokens": 2000,
        },
    )
    text, urls = "", []
    for item in d.get("output", []):
        if item.get("type") == "search_results":
            urls += [r["url"] for r in item.get("results", []) if r.get("url")]
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
    u = d.get("usage", {})
    c = (u.get("cost") or {}).get("total_cost")
    if c is None:
        n = sum((v or {}).get("invocation", 0) for v in (u.get("tool_calls_details") or {}).values())
        c = cost(cfg, "perplexity", u.get("input_tokens", 0), u.get("output_tokens", 0), n)
    return {"text": text, "urls": urls, "cost": round(c, 5)}


ASKERS = {"gemini": ("GEMINI_API_KEY", ask_gemini), "openai": ("OPENAI_API_KEY", ask_openai),
          "claude": ("ANTHROPIC_API_KEY", ask_claude), "perplexity": ("PERPLEXITY_API_KEY", ask_perplexity)}


# ------------------------------------------------------------
# 回答の分析
# ------------------------------------------------------------
PHONE_RE = re.compile(r"0\d{1,4}[-‐－ー−(（]?\d{1,4}[-‐－ー−)）]?\d{3,4}")


def norm_phone(s: str) -> str:
    return re.sub(r"\D", "", s)


def gemini_json(cfg, prompt: str, schema: dict) -> dict:
    """検索なしのGeminiでJSONを返させる（分析用・安い）"""
    key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['analyzer']['model']}:generateContent"
    d = post_json(url, {"x-goog-api-key": key, "Content-Type": "application/json"}, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0},
    })
    parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return json.loads("".join(p.get("text", "") for p in parts) or "{}")


def extract_businesses(cfg, question: str, answer: str) -> list[str]:
    if not answer.strip() or not os.environ.get("GEMINI_API_KEY"):
        return []
    prompt = (
        "次の文章は、ある質問に対するAIアシスタントの回答です。\n"
        "回答の中で紹介・推薦されている『業者・会社・店舗・サービス名』を、登場した順に抜き出してください。\n"
        "比較サイトやポータルサイト（ミツモア、くらしのマーケット等）も1つの名前として含めてください。\n"
        "メーカー名（リンナイ、ノーリツ等）や一般名詞は含めないでください。名前は回答の表記のまま書いてください。\n\n"
        f"【質問】{question}\n\n【回答】\n{answer[:8000]}"
    )
    schema = {"type": "OBJECT", "properties": {"businesses": {"type": "ARRAY", "items": {"type": "STRING"}}},
              "required": ["businesses"]}
    try:
        return [b.strip() for b in gemini_json(cfg, prompt, schema).get("businesses", []) if b.strip()][:15]
    except Exception as e:  # 分析が失敗しても記録は続ける
        print(f"  [業者名の抽出に失敗] {e}", file=sys.stderr)
        return []


def is_ours(name: str, brand: dict) -> bool:
    n = name.lower()
    if any(s.lower() in n for s in brand.get("similar_names", [])) and "給湯器交換の匠" not in name:
        return False
    return any(b.lower() in n for b in brand["names"] + brand.get("domains", []))


def analyze(cfg, question: str, res: dict, businesses: list[str] | None = None) -> dict:
    brand = cfg["brand"]
    text = res.get("text", "")
    low = text.lower()
    urls_low = "\n".join(res.get("urls", [])).lower()

    # 言及＝回答本文に当社名/ドメインが出た。参照＝当社サイトが情報源として使われた
    mentioned = any(b.lower() in low for b in brand["names"] + brand.get("domains", []))
    cited = any(d.lower() in urls_low for d in brand.get("domains", []))
    # 「給湯の匠」は「給湯器交換の匠」と別物。混同判定は当社名を除いてから行う
    stripped = (low + "\n" + urls_low).replace("給湯器交換の匠", "")
    confused = any(s.lower() in stripped for s in brand.get("similar_names", []) + brand.get("similar_domains", []))

    if businesses is None:
        businesses = extract_businesses(cfg, question, text)
    rank = next((i + 1 for i, b in enumerate(businesses) if is_ours(b, brand)), None)
    if rank is None and mentioned and businesses:
        rank = "リスト外で言及"

    phones = sorted({p for p in PHONE_RE.findall(text) if len(norm_phone(p)) >= 10})
    official = {norm_phone(p) for p in brand.get("official_phones", []) if p}
    flags = []
    if confused:
        flags.append("類似名称の他社が登場")
    if mentioned and official:
        wrong = [p for p in phones if norm_phone(p) not in official]
        if wrong:
            flags.append("公式と違う電話番号: " + ", ".join(wrong))
    if mentioned:
        cities = brand.get("official_cities", [])
        if cities and "店舗" in question and not any(c in text for c in cities):
            flags.append("店舗所在地（寝屋川市・八尾市）が回答にない")

    return {
        "mentioned": mentioned, "cited": cited, "rank": rank, "businesses": businesses,
        "confused": confused, "phones": phones, "flags": flags,
    }


# ------------------------------------------------------------
# ダミー（--dry-run 用）
# ------------------------------------------------------------
def fake_answer(provider: str, q: str, i: int) -> dict:
    samples = [
        "大阪で給湯器交換なら、1. 給湯器交換の匠（Sanwatec合同会社、寝屋川市・八尾市）電話 0120-111-222、"
        "2. 交換できるくん、3. ミツモアで比較 がおすすめです。",
        "おすすめは 1. 交換できるくん 2. 給湯の匠 3. 生活堂 です。地元業者も検討しましょう。",
        "寝屋川市では地元の業者が便利です。くらしのマーケットで探す方法もあります。",
    ]
    return {"text": samples[(hash(provider) + i) % 3], "urls": ["https://sanwatec.net/"], "cost": 0.004}


def fake_businesses(answer: str) -> list[str]:
    return re.findall(r"\d\. ([^\s、（(]+)", answer)


# ------------------------------------------------------------
# 出力（CSV・スプレッドシート）
# ------------------------------------------------------------
def append_csv(path: Path, header: list, rows: list[list]):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def send_sheet(sheet: str, header: list, rows: list[list]):
    url, token = os.environ.get("SHEETS_WEBHOOK_URL"), os.environ.get("SHEETS_TOKEN", "")
    if not url or not rows:
        return
    try:
        r = requests.post(url, json={"token": token, "sheet": sheet, "header": header, "rows": rows},
                          timeout=60, allow_redirects=True)
        ok = r.status_code == 200 and '"ok":true' in r.text.replace(" ", "")
        print(f"  スプレッドシート「{sheet}」へ {len(rows)} 行送信: {'成功' if ok else '失敗 ' + r.text[:200]}")
    except Exception as e:
        print(f"  スプレッドシート送信エラー: {e}", file=sys.stderr)


def previous_summary(today: str) -> dict:
    """前回実行分の言及率を読む（週次比較用）"""
    p = RESULTS / "summary.csv"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["日付"] != today]
    if not rows:
        return {}
    last = max(r["日付"] for r in rows)
    return {r["AI"]: r for r in rows if r["日付"] == last} | {"_date": last}


# ------------------------------------------------------------
# 改善提案
# ------------------------------------------------------------
def write_report(cfg, today, summary_rows, log_rows, prev, use_ai=True) -> str:
    comp = {}
    for r in log_rows:
        for b in filter(None, r[5].split(" > ")):
            if not is_ours(b, cfg["brand"]):
                comp[b] = comp.get(b, 0) + 1
    top_comp = sorted(comp.items(), key=lambda x: -x[1])[:15]

    lines = [f"# AI検索 露出レポート {today}", "", "## AI別の結果"]
    for s in summary_rows:
        p = prev.get(s[1]) if prev else None
        diff = f"（前回 {p['言及率']}）" if p else ""
        lines.append(f"- {s[1]}: 言及率 {s[4]}{diff} / 平均順位 {s[5]} / 当社サイト参照 {s[9]}件 / 混同 {s[6]}件 / 要確認 {s[7]}件")
    lines += ["", "## よく推薦されている他社・サイト", *[f"- {n}（{c}回）" for n, c in top_comp]]
    flagged = [r for r in log_rows if r[8]]
    if flagged:
        lines += ["", "## 要確認の回答"] + [f"- [{r[1]}] {r[2]} → {r[8]}" for r in flagged[:20]]
    notfound = [f"[{r[1]}] {r[2]}" for r in log_rows if r[3] == "なし" and not r[12]]
    if notfound:
        lines += ["", "## 当社が出なかった質問"] + [f"- {x}" for x in notfound[:30]]

    if use_ai and os.environ.get("GEMINI_API_KEY"):
        prompt = (
            "あなたは大阪の地域密着の給湯器交換会社「給湯器交換の匠」（運営: Sanwatec合同会社、寝屋川本店・大阪店(八尾市)）"
            "のWeb担当のアドバイザーです。以下は、ChatGPT・Gemini・Claude・Perplexityに一般客の質問をして、"
            "当社が推薦されたかを集計した結果です。\n\n"
            "この結果から、AIの回答で当社が推薦されやすくなるための改善提案を、優先度の高い順に5つまで、"
            "日本語の箇条書きで具体的に書いてください。各提案には『なぜ（結果のどこからそう言えるか）』を1行添えてください。\n"
            "守ること:\n"
            "- ブランド名は必ず「給湯器交換の匠」と書き、「給湯の匠」と略さない（「給湯の匠」は類似名称の別会社）\n"
            "- 他社を批判する施策や、ランキング記事・比較記事への掲載依頼は提案しない（当社の方針）\n"
            "- 当社サイト（sanwatec.net / sanwacreate.net / kyutokitakumi.jp）、Googleビジネスプロフィール、Bing Places、"
            "口コミ、地域ページ、構造化データ、自社の施工事例など、自社で実行できる施策に絞る\n"
            "- 結果から言えないことは推測で断定しない\n\n"
            + "\n".join(lines)
        )
        schema = {"type": "OBJECT", "properties": {"advice": {"type": "STRING"}}, "required": ["advice"]}
        try:
            advice = gemini_json(cfg, prompt, schema).get("advice", "")
            lines += ["", "## 改善提案（AIによる自動分析）", advice]
        except Exception as e:
            lines += ["", f"（改善提案の作成に失敗: {e}）"]

    report = "\n".join(lines)
    out = RESULTS / "reports" / f"{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    return report


# ------------------------------------------------------------
# メイン
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="APIを呼ばずダミーで動作確認")
    ap.add_argument("--limit", type=int, default=0, help="質問数を制限（お試し用）")
    args = ap.parse_args()

    cfg = load_config()
    today = dt.datetime.now(JST).strftime("%Y-%m-%d")
    questions = [q for q in cfg["questions"] if q][: args.limit or None]
    repeats = max(1, int(cfg.get("repeats", 1)))

    active = []
    for name, (env, _) in ASKERS.items():
        if not cfg["providers"].get(name, {}).get("enabled", True):
            continue
        if args.dry_run or os.environ.get(env):
            active.append(name)
        else:
            print(f"::notice::{AI_LABEL[name]}: APIキー（{env}）が未登録のためスキップ")
    if not active:
        print("::error::使えるAIがありません。リポジトリの Settings → Secrets and variables → Actions に"
              " GEMINI_API_KEY などのAPIキーを登録してください。")
        sys.exit(1)
    print(f"{today} 実行: AI={', '.join(AI_LABEL[a] for a in active)} / 質問 {len(questions)}問 × {repeats}回")

    def run_provider(name):
        rows = []
        for q in questions:
            for i in range(repeats):
                err, res = "", {"text": "", "urls": [], "cost": 0}
                try:
                    res = fake_answer(name, q, len(rows)) if args.dry_run else ASKERS[name][1](cfg, q)
                except Exception as e:
                    err = str(e)[:300]
                if err:
                    a = {"mentioned": False, "cited": False, "rank": None, "businesses": [], "confused": False,
                         "phones": [], "flags": []}
                else:
                    a = analyze(cfg, q, res, fake_businesses(res["text"]) if args.dry_run else None)
                rows.append([
                    today, AI_LABEL[name], q,
                    "あり" if a["mentioned"] else ("エラー" if err else "なし"),
                    a["rank"] or "", " > ".join(a["businesses"]),
                    "あり" if a["confused"] else "", ", ".join(a["phones"]), " / ".join(a["flags"]),
                    "\n".join(dict.fromkeys(res.get("urls", [])))[:2000],
                    res.get("text", "")[:3000], res.get("cost", 0), err,
                    "あり" if a["cited"] else "",
                ])
                mark = "◎" if a["mentioned"] else ("×" if err else "－")
                print(f"  {mark} [{AI_LABEL[name]}] {q[:30]}  順位:{a['rank'] or '-'}  {err[:80]}")
                if err:
                    print(f"::warning::{AI_LABEL[name]} エラー: {err[:250]}".replace("\n", " "))
        return rows

    with ThreadPoolExecutor(max_workers=len(active)) as ex:
        log_rows = [r for rows in ex.map(run_provider, active) for r in rows]

    summary_rows = []
    for name in active:
        rs = [r for r in log_rows if r[1] == AI_LABEL[name] and not r[12]]
        hit = [r for r in rs if r[3] == "あり"]
        ranks = [r[4] for r in hit if isinstance(r[4], int)]
        summary_rows.append([
            today, AI_LABEL[name], len(rs), len(hit),
            f"{(len(hit) / len(rs) * 100) if rs else 0:.0f}%",
            f"{sum(ranks) / len(ranks):.1f}" if ranks else "-",
            sum(1 for r in rs if r[6]), sum(1 for r in rs if r[8]),
            round(sum(r[11] for r in log_rows if r[1] == AI_LABEL[name]), 4),
            sum(1 for r in rs if r[13]),
        ])

    prev = previous_summary(today)
    append_csv(RESULTS / "log.csv", LOG_HEADER, log_rows)
    append_csv(RESULTS / "summary.csv", SUMMARY_HEADER, summary_rows)
    report = write_report(cfg, today, summary_rows, log_rows, prev, use_ai=not args.dry_run)

    send_sheet("回答ログ", LOG_HEADER, log_rows)
    send_sheet("週次サマリー", SUMMARY_HEADER, summary_rows)
    send_sheet("レポート", ["日付", "内容"], [[today, report]])

    total = sum(r[11] for r in log_rows)
    print(f"::notice::完了 " + " / ".join(f"{s[1]} 言及{s[3]}/{s[2]}" for s in summary_rows))
    print(f"\n完了。推定コスト合計 ${total:.3f}（約{total * 150:.0f}円）")
    print("\n" + report)


if __name__ == "__main__":
    main()
