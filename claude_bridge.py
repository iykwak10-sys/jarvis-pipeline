#!/usr/bin/env python3
"""Claude Code <-> Telegram bridge.

텔레그램에서 맥미니의 Claude Code(Agent SDK)를 원격으로 구동하는 중계 데몬.

- 화이트리스트된 chat_id만 허용
- 프로젝트별 git worktree 격리 (main 브랜치 보호)
- 세션 지속(resume)으로 멀티턴 대화 유지
- permission_mode=bypassPermissions (worktree 격리 전제)

ENV (.env):
  CLAUDE_BRIDGE_BOT_TOKEN   전용 봇 토큰 (없으면 JARVIS_BOT_TOKEN 사용 — 단일 폴러일 때만)
  CLAUDE_BRIDGE_CHAT_ID     허용 chat_id (없으면 JARVIS_CHAT_ID)
  CLAUDE_BRIDGE_PROJECTS    프로젝트 루트 (기본 ~/)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("claude_bridge")

BOT_TOKEN = os.getenv("CLAUDE_BRIDGE_BOT_TOKEN") or os.getenv("JARVIS_BOT_TOKEN") or ""
ALLOWED_CHAT = str(os.getenv("CLAUDE_BRIDGE_CHAT_ID") or os.getenv("JARVIS_CHAT_ID") or "")
PROJECTS_ROOT = Path(os.getenv("CLAUDE_BRIDGE_PROJECTS") or Path.home()).expanduser()
TG_LIMIT = 3900  # 4096 안전 마진


@dataclass
class ChatState:
    project: Path | None = None        # 선택한 원본 프로젝트
    workdir: Path | None = None        # 실제 작업 디렉터리 (worktree 또는 프로젝트)
    branch: str | None = None
    session_id: str | None = None      # Claude resume용
    busy: bool = False
    cancel: bool = False


STATE: dict[int, ChatState] = {}


def st(chat_id: int) -> ChatState:
    return STATE.setdefault(chat_id, ChatState())


# ---------------------------------------------------------------- helpers


def authorized(update: Update) -> bool:
    return ALLOWED_CHAT and str(update.effective_chat.id) == ALLOWED_CHAT


async def reply(update: Update, text: str, **kw):
    """4096자 제한 대응 청킹 전송."""
    for i in range(0, len(text), TG_LIMIT):
        await update.effective_message.reply_text(text[i : i + TG_LIMIT], **kw)


def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def is_git_repo(path: Path) -> bool:
    code, _ = run_git(["rev-parse", "--is-inside-work-tree"], path)
    return code == 0


def make_worktree(project: Path) -> tuple[Path, str]:
    """프로젝트가 git repo면 격리 worktree 생성, 아니면 원본 경로 반환."""
    if not is_git_repo(project):
        return project, ""
    ts = time.strftime("%Y%m%d-%H%M%S")
    branch = f"tg/{ts}"
    wt_dir = project / ".claude" / "worktrees" / f"tg-{ts}"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_git(["worktree", "add", "-b", branch, str(wt_dir)], project)
    if code != 0:
        log.warning("worktree add 실패, 원본에서 실행: %s", out)
        return project, ""
    return wt_dir, branch


def list_projects() -> list[Path]:
    out = []
    for p in sorted(PROJECTS_ROOT.iterdir()):
        if p.is_dir() and not p.name.startswith("."):
            out.append(p)
    return out[:40]


_SKIP_DIRS = {
    "node_modules", "venv", ".venv", "__pycache__", ".git", "Library",
    "site-packages", ".cache", "dist", "build", ".next", "worktrees",
}


def find_projects(name: str, max_depth: int = 3) -> list[Path]:
    """PROJECTS_ROOT 하위를 깊이 제한으로 탐색해 이름이 일치하는 디렉터리를 찾는다.
    완전일치(대소문자 무시) 우선, git repo 우선 정렬."""
    name_l = name.lower()
    exact: list[Path] = []
    partial: list[Path] = []

    def walk(base: Path, depth: int):
        if depth > max_depth:
            return
        try:
            entries = list(base.iterdir())
        except (PermissionError, OSError):
            return
        for p in entries:
            if not p.is_dir() or p.name.startswith(".") or p.name in _SKIP_DIRS:
                continue
            nl = p.name.lower()
            if nl == name_l:
                exact.append(p)
            elif name_l in nl:
                partial.append(p)
            walk(p, depth + 1)

    walk(PROJECTS_ROOT, 1)
    hits = exact if exact else partial
    # git repo를 앞쪽으로
    hits.sort(key=lambda p: (not (p / ".git").exists(), len(str(p))))
    return hits[:20]


# ---------------------------------------------------------------- commands


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await reply(update, "⛔ 허용되지 않은 chat_id 입니다.")
        return
    await reply(
        update,
        "🤖 *Claude Code 브리지*\n\n"
        "이 봇으로 맥미니의 Claude Code를 원격 구동합니다.\n\n"
        "*명령어*\n"
        "/projects — 프로젝트 목록\n"
        "/cd <이름> — 프로젝트 선택(자동 worktree 격리)\n"
        "/pwd — 현재 작업 위치\n"
        "/new — 대화 세션 초기화\n"
        "/stop — 실행 중단\n"
        "/diff — 변경사항 보기\n"
        "/commit <메시지> — 커밋\n\n"
        "그 외 일반 메시지는 Claude에게 그대로 전달됩니다.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_projects(update: Update, _ctx):
    if not authorized(update):
        return
    projs = list_projects()
    lines = [f"📁 `{PROJECTS_ROOT}` 하위 프로젝트:", ""]
    for p in projs:
        git = "🌿" if (p / ".git").exists() else "  "
        lines.append(f"{git} {p.name}")
    lines += [
        "",
        "선택: `/cd <이름>` — 중첩 폴더도 이름만으로 자동 검색됩니다.",
        "예: `/cd jarvis-pipeline` 또는 `/cd 01_Execution_Field/jarvis-pipeline`",
    ]
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_cd(update: Update, ctx):
    if not authorized(update):
        return
    if not ctx.args:
        await reply(update, "사용법: /cd <프로젝트이름>")
        return
    name = " ".join(ctx.args).strip()
    # 1) 절대/상대(중첩) 경로 직접 입력 지원
    if name.startswith("/") or name.startswith("~"):
        target = Path(name).expanduser()
    else:
        target = (PROJECTS_ROOT / name).expanduser()
    # 2) 직접 경로가 없으면 이름으로 재귀 검색 (git repo 우선)
    if not target.is_dir():
        cands = await asyncio.to_thread(find_projects, name)
        if len(cands) == 1:
            target = cands[0]
        elif len(cands) > 1:
            lines = [f"❓ '{name}' 후보가 여러 개입니다. 정확히 골라주세요:", ""]
            for p in cands[:15]:
                rel = p.relative_to(PROJECTS_ROOT) if PROJECTS_ROOT in p.parents else p
                lines.append(f"`/cd {rel}`")
            await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
            return
        else:
            await reply(update, f"❌ '{name}' 프로젝트를 찾지 못했습니다.\n`/projects` 로 목록을 보거나 전체 경로로 `/cd <경로>` 하세요.")
            return
    s = st(update.effective_chat.id)
    await update.effective_chat.send_action(ChatAction.TYPING)
    workdir, branch = await asyncio.to_thread(make_worktree, target)
    s.project, s.workdir, s.branch, s.session_id = target, workdir, branch, None
    extra = f"\n🌿 worktree 브랜치: `{branch}`" if branch else "\n⚠️ git repo 아님 — 원본에서 직접 실행"
    await reply(
        update,
        f"✅ 선택: *{target.name}*\n작업 위치: `{workdir}`{extra}\n\n이제 메시지를 보내면 작업을 시작합니다.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_pwd(update: Update, _ctx):
    if not authorized(update):
        return
    s = st(update.effective_chat.id)
    if not s.workdir:
        await reply(update, "선택된 프로젝트 없음. /projects 로 고르세요.")
        return
    await reply(
        update,
        f"📍 `{s.workdir}`\n🌿 `{s.branch or '(원본)'}`\n🧵 session: `{s.session_id or '신규'}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_new(update: Update, _ctx):
    if not authorized(update):
        return
    st(update.effective_chat.id).session_id = None
    await reply(update, "🧵 새 대화 세션을 시작합니다.")


async def cmd_stop(update: Update, _ctx):
    if not authorized(update):
        return
    st(update.effective_chat.id).cancel = True
    await reply(update, "⏹ 중단 요청됨.")


async def cmd_diff(update: Update, _ctx):
    if not authorized(update):
        return
    s = st(update.effective_chat.id)
    if not s.workdir:
        await reply(update, "프로젝트 먼저 선택하세요 (/projects).")
        return
    _, out = await asyncio.to_thread(run_git, ["status", "--short"], s.workdir)
    _, stat = await asyncio.to_thread(run_git, ["diff", "--stat"], s.workdir)
    body = (out or "(변경 없음)") + "\n\n" + (stat or "")
    await reply(update, f"📝 변경사항\n```\n{body[:3500]}\n```", parse_mode=ParseMode.MARKDOWN)


async def cmd_commit(update: Update, ctx):
    if not authorized(update):
        return
    s = st(update.effective_chat.id)
    if not s.workdir:
        await reply(update, "프로젝트 먼저 선택하세요.")
        return
    msg = " ".join(ctx.args) or "chore: update via telegram bridge"
    await asyncio.to_thread(run_git, ["add", "-A"], s.workdir)
    code, out = await asyncio.to_thread(run_git, ["commit", "-m", msg], s.workdir)
    await reply(update, f"{'✅' if code == 0 else '❌'} commit\n```\n{out[:1500]}\n```", parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------- core: run claude


async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await reply(update, "⛔ 허용되지 않은 chat_id 입니다.")
        return
    s = st(update.effective_chat.id)
    if not s.workdir:
        await reply(update, "먼저 /projects → /cd <이름> 으로 프로젝트를 선택하세요.")
        return
    if s.busy:
        await reply(update, "⏳ 이전 작업이 아직 진행 중입니다. /stop 으로 중단할 수 있어요.")
        return

    prompt = update.effective_message.text
    s.busy = True
    s.cancel = False
    options = ClaudeAgentOptions(
        cwd=str(s.workdir),
        permission_mode="bypassPermissions",
        resume=s.session_id,
    )

    await update.effective_chat.send_action(ChatAction.TYPING)
    buffer: list[str] = []
    last_flush = time.monotonic()

    async def flush():
        nonlocal buffer, last_flush
        if buffer:
            await reply(update, "".join(buffer))
            buffer = []
            last_flush = time.monotonic()

    try:
        async for message in query(prompt=prompt, options=options):
            if s.cancel:
                await reply(update, "⏹ 사용자 요청으로 중단했습니다.")
                break
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        buffer.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        name = block.name
                        detail = ""
                        if name == "Bash":
                            detail = (block.input or {}).get("command", "")[:120]
                        elif name in ("Edit", "Write", "Read"):
                            detail = (block.input or {}).get("file_path", "")
                        await flush()
                        await reply(update, f"🔧 `{name}` {detail}", parse_mode=ParseMode.MARKDOWN)
                # 길어지면 중간 flush
                if sum(len(b) for b in buffer) > TG_LIMIT or time.monotonic() - last_flush > 8:
                    await flush()
            elif isinstance(message, ResultMessage):
                s.session_id = message.session_id
                await flush()
                cost = getattr(message, "total_cost_usd", None)
                turns = getattr(message, "num_turns", None)
                tail = "✅ 완료"
                if cost is not None:
                    tail += f" · ${cost:.4f}"
                if turns is not None:
                    tail += f" · {turns}턴"
                await reply(update, tail)
        await flush()
    except Exception as exc:  # noqa: BLE001
        log.exception("query 실패")
        await reply(update, f"❌ 오류: {exc}")
    finally:
        s.busy = False


# ---------------------------------------------------------------- main


def main():
    if not BOT_TOKEN:
        raise SystemExit("CLAUDE_BRIDGE_BOT_TOKEN(또는 JARVIS_BOT_TOKEN)이 .env에 없습니다.")
    if not ALLOWED_CHAT:
        raise SystemExit("CLAUDE_BRIDGE_CHAT_ID(또는 JARVIS_CHAT_ID)가 .env에 없습니다.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("pwd", cmd_pwd))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("diff", cmd_diff))
    app.add_handler(CommandHandler("commit", cmd_commit))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Claude bridge 시작. projects_root=%s allowed_chat=%s", PROJECTS_ROOT, ALLOWED_CHAT)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
