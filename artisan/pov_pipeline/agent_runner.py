#!/usr/bin/env python3
"""Deterministic headless POV agent runner for weaker OpenCode models."""
from __future__ import annotations
import json, os, re, shutil, signal, subprocess, sys, time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
AGENTS_DIR = HERE / "agents"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_TIMEOUT = 1800
TIMEOUTS = {"POV-researcher":2400,"POV-scriptwriter":2400,"POV-image-director":1800,"POV-thumbnail-artist":900,"POV-voice-engineer":1200,"POV-seo-specialist":900,"POV-archive-manager":900}

@dataclass
class AgentResult:
    agent: str; outfile: str; status: str = "pending"; attempts: int = 1
    bytes_written: int = 0; duration_s: float = 0.0; error: str = ""; finished_at: str = ""
    @property
    def ok(self): return self.status in {"done","skipped","dry-run"}

@dataclass
class ChainResult:
    project: str; ok: bool = False; needs_review: bool = False; reason: str = ""
    gate_attempts: int = 0; gate_passed: bool = False; agents: list[AgentResult] = field(default_factory=list)
    def summary(self):
        done=sum(x.status=="done" for x in self.agents); skipped=sum(x.status=="skipped" for x in self.agents)
        failed=[x.agent for x in self.agents if not x.ok]
        text=f"{done} run | {skipped} skipped"
        if failed: text += " | failed: "+", ".join(failed)
        if self.gate_attempts: text += f" | gate {'PASS' if self.gate_passed else 'FAIL'}"
        return text

Notify = Callable[[str,str],None]
GateFn = Callable[[Path],bool]
def stamp(): return datetime.now().astimezone().isoformat(timespec="seconds")
def state_dir(p): d=Path(p)/"state"; d.mkdir(parents=True,exist_ok=True); return d
def log(p,msg,error=False):
    try:
        with (state_dir(p)/"pipeline.log").open("a",encoding="utf-8") as f: f.write(f"{stamp()} [{'error' if error else 'info'}] {msg}\n")
    except OSError: pass
    print(f"[{'error' if error else 'agent'}] {msg}",file=sys.stderr if error else sys.stdout)

def write_manifest(p,agents,status="RUNNING",results=(),stage="agents",source_url="",extra=None):
    p=Path(p); outputs={}
    for _,rel in agents:
        f=p/rel; outputs[rel]={"bytes":f.stat().st_size,"modified":stamp()} if f.exists() else None
    doc={"schema":2,"project":p.name,"project_dir":str(p),"status":status,"stage":stage,"updated_at":stamp(),"agent_order":[a for a,_ in agents],"outputs":outputs,"agents":{r.agent:asdict(r) for r in results},"log":str(state_dir(p)/"pipeline.log")}
    if source_url: doc["source_url"]=source_url
    if extra: doc["extra"]=extra
    (state_dir(p)/"manifest.json").write_text(json.dumps(doc,indent=2),encoding="utf-8")

def resolve_opencode():
    explicit=os.environ.get("POV_OPENCODE_BIN","").strip()
    return explicit if explicit and Path(explicit).exists() else shutil.which("opencode")

def supported_flags(exe):
    try:
        r=subprocess.run([exe,"run","--help"],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=30)
        text=(r.stdout or "")+(r.stderr or "")
        return {x for x in ("--agent","--model","--file","--auto") if x in text}
    except (OSError,subprocess.SubprocessError): return set()

def build_brief(agent,contract,project,outfile,previous,attempt,gate_report):
    project=Path(project).resolve(); prev=str(project/previous) if previous else "none"
    retry=f"\nRETRY {attempt}: rewrite from scratch. Fix this report:\n{gate_report[-3000:]}\n" if attempt>1 else ""
    return f"""You are the {agent} stage in a headless production pipeline. Do the work now. Do not ask questions, explain a plan, or return a summary.{retry}
HARD EXECUTION RULES
1. Work only inside this exact project directory: {project}
2. Read only exact input files named below. Do not use Glob, recursive search, directory discovery, or any path outside the project. STORY_LOGIC_BIBLE.txt is not a separate file; the research notes contain the logic package.
3. Write exactly one non-empty plain-text artifact to: {project/outfile}
4. Create the output parent directory if needed. Do not write alternate files.
5. Never wait for permission, call external services, or run memory commands. Finish by writing the artifact even if an input is imperfect.
6. Follow the contract as a checklist. Prefer explicit headings and short sentences.

PROJECT: {project.name}
PREVIOUS ARTIFACT: {prev}
OUTPUT: {project/outfile}

=== STAGE CONTRACT ===
{contract}
=== END STAGE CONTRACT ===

Write the output file now.
"""

def build_command(exe,brief_file,agent,model):
    flags=supported_flags(exe); cmd=[exe,"run"]
    if "--model" in flags: cmd += ["--model",model]
    if "--agent" in flags: cmd += ["--agent",agent.lower()]
    if "--file" in flags: cmd += ["Execute the attached stage brief exactly.","--file",str(brief_file)]
    else: cmd += [brief_file.read_text(encoding="utf-8")]
    if "--auto" in flags and os.environ.get("POV_OPENCODE_NO_AUTO")!="1": cmd.append("--auto")
    return cmd

def record_outcome(project,chain,model):
    try:
        milo=shutil.which("milo") or shutil.which("mylo")
        if not milo: return
        status="PASS" if chain.ok else "REVIEW" if chain.needs_review else "FAIL"
        text=f"POV chain {chain.project}: {status} - {chain.summary()}"
        if chain.reason: text += f" ({chain.reason})"
        subprocess.run([milo,"remember",text,"--project","pov-pipeline"],
                       capture_output=True,timeout=30)
    except (OSError,subprocess.SubprocessError): pass

def kill_tree(proc):
    try:
        if os.name=="nt": subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],capture_output=True,timeout=20)
        else: os.killpg(os.getpgid(proc.pid),signal.SIGKILL)
    except (OSError,subprocess.SubprocessError):
        try: proc.kill()
        except OSError: pass

def dispatch(project,agent,outfile,previous,model,timeout,attempt,gate_report,dry_run):
    p=Path(project); target=p/outfile; prompt=AGENTS_DIR/f"{agent}.md"; result=AgentResult(agent,outfile,attempts=attempt)
    if not prompt.exists(): result.status,result.error="failed",f"missing contract: {prompt}"; return result
    target.parent.mkdir(parents=True,exist_ok=True); briefs=state_dir(p)/"briefs"; briefs.mkdir(exist_ok=True)
    brief_file=briefs/f"{agent}.attempt{attempt}.brief.md"
    brief_file.write_text(build_brief(agent,prompt.read_text(encoding="utf-8"),p,outfile,previous,attempt,gate_report),encoding="utf-8")
    exe=resolve_opencode()
    if not exe: result.status,result.error="failed","opencode is not on PATH"; return result
    if dry_run: result.status,result.finished_at="dry-run",stamp(); return result
    logfile=state_dir(p)/"runs"/f"{agent}.attempt{attempt}.log"; logfile.parent.mkdir(exist_ok=True); started=time.time()
    try:
        with logfile.open("ab") as out:
            proc=subprocess.Popen(build_command(exe,brief_file,agent,model),cwd=str(p),stdout=out,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0,start_new_session=os.name!="nt")
            while proc.poll() is None:
                if time.time()-started>=timeout: kill_tree(proc); proc.wait(); result.status,result.error="timeout","agent timed out"; return result
                time.sleep(1)
    except (OSError,subprocess.SubprocessError) as exc: result.status,result.error="failed",str(exc); return result
    result.duration_s=round(time.time()-started,1); result.finished_at=stamp()
    if not target.exists() or target.stat().st_size==0: result.status,result.error="failed",f"{outfile} was not written"
    else: result.status,result.bytes_written="done",target.stat().st_size
    return result

def run_agent_chain(project_dir,agents,*,agents_dir=AGENTS_DIR,gate_fn=None,gate_after="POV-scriptwriter",gate_retries=None,model=None,agent_override=None,timeout=None,use_memory=True,notify=None,source_url="",dry_run=False):
    del agents_dir,agent_override
    p=Path(project_dir); p.mkdir(parents=True,exist_ok=True); model=model or os.environ.get("POV_OPENCODE_MODEL",DEFAULT_MODEL)
    gate_retries=3 if gate_retries is None else gate_retries; timeout=timeout or int(os.environ.get("POV_AGENT_TIMEOUT","0") or 0) or None
    chain=ChainResult(p.name); previous=None
    print("\n"+"="*60+f"\n  POV AGENT CHAIN (Nemotron, {len(agents)} stages)\n"+"="*60)
    for agent,outfile in agents:
        write_manifest(p,agents,results=chain.agents,source_url=source_url); target=p/outfile
        if target.exists() and target.stat().st_size:
            res=AgentResult(agent,outfile,status="skipped",bytes_written=target.stat().st_size,finished_at=stamp())
        else:
            res=None
            for attempt in range(1,4):
                res=dispatch(p,agent,outfile,previous,model,timeout or TIMEOUTS.get(agent,DEFAULT_TIMEOUT),attempt,"",dry_run)
                if res.ok: break
            chain.agents.append(res)
            if not res.ok:
                chain.reason=f"{agent} failed: {res.error}"; chain.needs_review=True; log(p,chain.reason,True); write_manifest(p,agents,"NEEDS_REVIEW",chain.agents,source_url=source_url)
                if notify:
                    try: notify("agent.failed",chain.reason)
                    except Exception: pass
                if use_memory: record_outcome(p,chain,model)
                return chain
        chain.agents.append(res); previous=outfile; print(f"[{agent}] {res.status} {outfile} ({res.bytes_written} bytes)")
        if gate_fn and agent==gate_after and res.status=="done" and not dry_run:
            passed=False
            for n in range(1,gate_retries+2):
                chain.gate_attempts=n; passed=bool(gate_fn(p))
                if passed: break
                if n<=gate_retries:
                    res=dispatch(p,agent,outfile,previous,model,timeout or TIMEOUTS.get(agent,DEFAULT_TIMEOUT),n+1,"Script gate failed. Rewrite the file to satisfy every gate.",dry_run); chain.agents.append(res)
                    if not res.ok: break
            chain.gate_passed=passed
            if not passed:
                chain.reason="script gate failed after retries"; chain.needs_review=True; write_manifest(p,agents,"NEEDS_REVIEW",chain.agents,source_url=source_url)
                if use_memory: record_outcome(p,chain,model)
                return chain
    chain.ok=True; write_manifest(p,agents,"OK",chain.agents,source_url=source_url); log(p,"chain complete")
    if use_memory: record_outcome(p,chain,model)
    return chain
""