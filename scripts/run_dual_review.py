#!/usr/bin/env python3
from __future__ import annotations
import email.utils, json, os, shutil, smtplib, subprocess, sys, time, urllib.parse, urllib.request, uuid
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)
LOG = OUT / "run.log"

def log(s):
    line = f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {s}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f: f.write(line + "\n")

def run(cmd, cwd=None, env=None, timeout=1800, check=True):
    log("$ " + " ".join(map(str, cmd)))
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout)
    if p.stdout: log(p.stdout[-10000:])
    if check and p.returncode: raise RuntimeError(f"exit={p.returncode}: {' '.join(map(str, cmd))}")
    return p

def clone(url, target):
    if target.exists(): shutil.rmtree(target)
    run(["git", "clone", "--depth", "1", url, str(target)], timeout=600)
    sha = run(["git", "rev-parse", "HEAD"], cwd=target).stdout.strip()
    (OUT / f"{target.name}_commit.txt").write_text(sha + "\n", encoding="utf-8")
    return sha

def api(url, token, method="GET", body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None: headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r: raw = r.read()
    return json.loads(raw.decode("utf-8")) if raw else {}

def latest_md(folder, after):
    fs = [p for p in folder.rglob("*.md") if p.is_file() and p.stat().st_mtime >= after]
    return max(fs, key=lambda p:p.stat().st_mtime) if fs else None

def run_dsa(repo):
    env = os.environ.copy()
    env.update({
        "PYTHONUTF8":"1", "PYTHONIOENCODING":"utf-8", "STOCK_LIST":"600519",
        "MARKET_REVIEW_REGION":"cn", "MARKET_REVIEW_ENABLED":"true",
        "TRADING_DAY_CHECK_ENABLED":"true", "REPORT_TYPE":"full", "REPORT_LANGUAGE":"zh",
        "MAX_WORKERS":"1", "LOG_LEVEL":"INFO", "ENABLE_CHIP_DISTRIBUTION":"false",
        "MODELFLARE_KEY":os.getenv("DSA_MODELFLARE_KEY",""),
        "AIHUBMIX_KEY":os.getenv("DSA_AIHUBMIX_KEY",""),
        "GEMINI_API_KEY":os.getenv("DSA_GEMINI_API_KEY",""),
        "OPENAI_API_KEY":os.getenv("DSA_OPENAI_API_KEY",""),
        "OPENAI_BASE_URL":os.getenv("DSA_OPENAI_BASE_URL",""),
        "OPENAI_MODEL":os.getenv("DSA_OPENAI_MODEL",""),
        "TUSHARE_TOKEN":os.getenv("DSA_TUSHARE_TOKEN",""),
        "EMAIL_SENDER":"", "EMAIL_PASSWORD":"", "EMAIL_RECEIVERS":"",
    })
    if not any(env.get(k) for k in ("MODELFLARE_KEY","AIHUBMIX_KEY","GEMINI_API_KEY","OPENAI_API_KEY")):
        return "failed: missing DSA model API secret", None
    reports = repo / "reports"; reports.mkdir(exist_ok=True)
    started = time.time()
    p = run([sys.executable, "main.py", "--market-review"], cwd=repo, env=env, timeout=2400, check=False)
    report = latest_md(reports, started - 5)
    if p.returncode: return f"failed: DSA exit={p.returncode}", report
    if not report: return "partial: no Markdown file found in DSA reports/", None
    dest = OUT / f"DSA_market_review_{date.today().isoformat()}.md"
    shutil.copy2(report, dest)
    return "success", dest

def run_vibe(repo, override):
    key = os.getenv("VIBE_DEEPSEEK_API_KEY","").strip()
    if not key: return "failed: missing DEEPSEEK_API_KEY", None, override or ""
    data = repo / ".local"; data.mkdir(exist_ok=True)
    cache = ROOT / ".cache" / "vibe-local"
    if cache.exists(): shutil.copytree(cache, data, dirs_exist_ok=True)
    (data / "config.json").write_text(json.dumps({
        "provider":{"profile":"deepseek"},
        "defaults":{"model":"deepseek-chat","max_retries":2,"gate_retries":2,
                    "turn_timeout_min":8,"fetch_timeout_sec":120}
    }, ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    token = uuid.uuid4().hex + uuid.uuid4().hex
    env.update({"DEEPSEEK_API_KEY":key,"VRA_PROVIDER":"deepseek","VRA_API_TOKEN":token,
                "VRA_API_PORT":"8770","VRA_SCHEDULER":"off","PYTHONUTF8":"1","PYTHONIOENCODING":"utf-8"})
    req = repo / ".agents/skills/data-access/scripts/requirements.txt"
    if req.exists(): run([sys.executable,"-m","pip","install","-r",str(req)],cwd=repo,timeout=900)
    orch = repo / "orchestrator"
    run(["npm","install","--no-audit","--no-fund"],cwd=orch,timeout=900)
    api_log = (OUT/"vibe_api.log").open("w",encoding="utf-8")
    proc = subprocess.Popen(["node","src/api.ts","--port","8770"],cwd=orch,env=env,stdout=api_log,stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:8770"
    try:
        ready = False
        for _ in range(60):
            if proc.poll() is not None: raise RuntimeError("Vibe API exited before health check")
            try: api(base+"/health",token,timeout=5); ready=True; break
            except Exception: time.sleep(2)
        if not ready: raise RuntimeError("Vibe API health check timed out")
        view = api(base+"/view/shortline_review",token,timeout=180)
        trade_day = override or str(view.get("date") or "")
        if not trade_day: raise RuntimeError("Vibe did not resolve a completed trading date")
        started = api(base+"/view/shortline_review",token,method="POST",
                      body={"action":"start","date":trade_day},timeout=180)
        run_id = str(started.get("id") or "")
        if not run_id: raise RuntimeError("Vibe start action returned no run id")
        log(f"Vibe review started: {trade_day}, run={run_id}")
        final = None
        for _ in range(90):
            state = api(base+"/view/shortline_review?date="+urllib.parse.quote(trade_day),token,timeout=60)
            final = next((x for x in (state.get("runs") or []) if str(x.get("id"))==run_id),None)
            if final and final.get("done"): break
            time.sleep(20)
        if not final or not final.get("done"): raise RuntimeError("Vibe AI review timed out")
        ledger = api(base+"/ledger/shortline_review",token,timeout=60)
        records = (ledger.get("records") or {}).get("shortline_review") or []
        saved = next((x for x in records if str(x.get("id"))==run_id),None)
        if not saved:
            stages = final.get("stages") or []
            text = "\n".join(f"## {x.get('label')} ({x.get('status')})\n{x.get('text') or x.get('error','')}" for x in stages)
            dest = OUT/f"Vibe_shortline_review_{trade_day}_incomplete.md"
            dest.write_text(f"# Vibe AStock {trade_day}\n\n未形成已保存版本\n\n{text}\n",encoding="utf-8")
            return "partial: Vibe review did not persist a complete version",dest,trade_day
        lines = [f"# Vibe AStock 情绪复盘 {trade_day}","",
                 f"- AI：{saved.get('ai','unknown')}",f"- 数据 settled：{saved.get('settled')}",""]
        for x in saved.get("sections") or []:
            lines += [f"## {x.get('label',x.get('id',''))}",x.get("text") or f"（{x.get('status')}）",""]
        if saved.get("gaps"): lines += ["## 数据缺口",*[f"- {g}" for g in saved["gaps"]],""]
        dest=OUT/f"Vibe_shortline_review_{trade_day}.md"
        dest.write_text("\n".join(lines),encoding="utf-8")
        return "success",dest,trade_day
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()
        api_log.close()
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(data, cache, dirs_exist_ok=True, ignore=shutil.ignore_patterns("api.token"))

def send_mail(subject, body, attachments):
    sender=os.getenv("REVIEW_FROM","zeurdfish@gmail.com").strip()
    recipient=os.getenv("REVIEW_TO","zeurdfish@gmail.com").strip()
    password=os.getenv("GMAIL_APP_PASSWORD","").replace(" ","")
    if not password: raise RuntimeError("missing GMAIL_APP_PASSWORD (Google App Password)")
    msg=EmailMessage()
    msg["From"]=email.utils.formataddr(("A股项目复盘",sender))
    msg["To"]=recipient; msg["Subject"]=subject; msg.set_content(body)
    for p in attachments:
        if p and p.exists():
            msg.add_attachment(p.read_bytes(),maintype="text",subtype="markdown",filename=p.name)
    with smtplib.SMTP_SSL("smtp.gmail.com",465,timeout=30) as smtp:
        smtp.login(sender,password); smtp.send_message(msg)
    log(f"Email sent to {recipient}")

def main():
    override=os.getenv("TRADE_DATE","").strip() or None
    dsa=ROOT/"_upstream_dsa"; vibe=ROOT/"_upstream_vibe"
    statuses={}; attachments=[]; trade_day=override or date.today().isoformat()
    try:
        clone("https://github.com/ZhuLinsen/daily_stock_analysis.git",dsa)
        statuses["daily_stock_analysis"],report=run_dsa(dsa)
        if report and report.exists(): attachments.append(report)
    except Exception as e: statuses["daily_stock_analysis"]=f"failed: {type(e).__name__}: {e}"; log(str(e))
    try:
        clone("https://github.com/simonlin1212/vibe-astock.git",vibe)
        statuses["vibe-astock"],report,vibe_day=run_vibe(vibe,override)
        if vibe_day: trade_day=vibe_day
        if report and report.exists(): attachments.append(report)
    except Exception as e: statuses["vibe-astock"]=f"failed: {type(e).__name__}: {e}"; log(str(e))
    run_url=os.getenv("GITHUB_SERVER_URL","https://github.com")+"/"+os.getenv("GITHUB_REPOSITORY","")+"/actions/runs/"+os.getenv("GITHUB_RUN_ID","")
    full=all(statuses.get(k)=="success" for k in ("daily_stock_analysis","vibe-astock"))
    label="完整" if full else ("部分完成" if attachments else "失败")
    subject=f"[A股项目复盘] [{trade_day}] {label}"
    body=(f"交易日：{trade_day}\n\n"
          f"daily_stock_analysis：{statuses.get('daily_stock_analysis')}\n"
          f"vibe-astock：{statuses.get('vibe-astock')}\n\n"
          f"Actions：{run_url}\n"
          f"上游提交号：\n"
          f"- DSA：{(OUT/'_upstream_dsa_commit.txt').read_text().strip() if (OUT/'_upstream_dsa_commit.txt').exists() else '未获取'}\n"
          f"- Vibe：{(OUT/'_upstream_vibe_commit.txt').read_text().strip() if (OUT/'_upstream_vibe_commit.txt').exists() else '未获取'}\n\n"
          "附件为项目实际生成的原始复盘报告。缺失的项目没有用外部搜索代写。\n")
    (OUT/"status.json").write_text(json.dumps({"trade_date":trade_day,"statuses":statuses,"run_url":run_url},ensure_ascii=False,indent=2),encoding="utf-8")
    send_mail(subject,body,attachments)
    return 0 if full else 2

if __name__=="__main__":
    try: sys.exit(main())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        sys.exit(1)
