#!/usr/bin/env python3
"""v0.1 发布脚本（跨平台，仅用标准库）。

子命令：
  check   发布前校验：版本一致性、必备文件、仓库卫生、（可选）ruff/pytest/前端构建
  bundle  构建并导出离线镜像包（docker save + 校验和 + 加载脚本 + 离线说明）
  tag     创建并推送 annotated tag（默认 dry-run，加 --push 才执行）

示例：
  python scripts/release.py check
  python scripts/release.py check --skip-tests --skip-build
  python scripts/release.py bundle --dry-run
  python scripts/release.py tag
  python scripts/release.py tag --push
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

REQUIRED_FILES = (
    "LICENSE",
    "NOTICE",
    "README.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    ".gitattributes",
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker/backend.Dockerfile",
    "docker/web.Dockerfile",
    "docker/nginx.conf",
    "backend/.env.example",
    "scripts/init_db.sql",
    "scripts/seed_data.py",
    "scripts/backup.sh",
    "docs/quickstart.md",
    "docs/architecture.md",
    "docs/deployment.md",
    "docs/demo-script.md",
)

# 绝不允许进入版本库的路径片段（密钥、运行数据、构建产物）
FORBIDDEN_TRACKED = (
    ".env",
    "__pycache__/",
    "node_modules/",
    "backend/data/",
    "backend/memory_data/",
    "dist/",
    ".review_diff",
    ".venv/",
)
FORBIDDEN_EXEMPT = ("backend/.env.example", ".env.example")

VERSION_SOURCES = {
    "package.json": ("json", ROOT / "package.json"),
    "frontend/package.json": ("json", ROOT / "frontend" / "package.json"),
    "admin-frontend/package.json": ("json", ROOT / "admin-frontend" / "package.json"),
    "packages/shared/package.json": ("json", ROOT / "packages" / "shared" / "package.json"),
    "backend/pyproject.toml": ("toml", BACKEND / "pyproject.toml"),
    "backend/app/main.py": ("fastapi", BACKEND / "app" / "main.py"),
}

IS_WINDOWS = sys.platform.startswith("win")
PY = sys.executable or ("python" if IS_WINDOWS else "python3")


def _ascii_safe_print() -> None:
    """PowerShell 默认 GBK 控制台会让中文/符号抛 UnicodeEncodeError，统一降级为可替换输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def sh(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """执行命令并捕获输出；不抛异常，由调用方看 returncode。"""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def changelog_version() -> str | None:
    """CHANGELOG 里最新的具体版本（跳过 Unreleased）。"""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for match in re.finditer(r"^## \[?(\d+\.\d+\.\d+)\]?", text, re.MULTILINE):
        return match.group(1)
    return None


def read_version(kind: str, path: Path) -> str | None:
    if not path.exists():
        return None
    if kind == "json":
        return json.loads(path.read_text(encoding="utf-8")).get("version")
    text = path.read_text(encoding="utf-8")
    if kind == "toml":
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    else:
        match = re.search(r'version="([^"]+)"', text)
    return match.group(1) if match else None


def lock_versions(version: str) -> list[str]:
    """锁文件里属于本仓库工作区的 version 行（第三方包的 1.3.0 之类不算）。"""
    path = ROOT / "package-lock.json"
    if not path.exists():
        return ["package-lock.json 缺失"]
    bad: list[str] = []
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, node in data.get("packages", {}).items():
        if key in ("", "frontend", "admin-frontend", "packages/shared", "packages/*"):
            if node.get("version") != version:
                bad.append(f"package-lock.json[{key or '/'}] = {node.get('version')}")
    if data.get("version") != version:
        bad.append(f"package-lock.json(top) = {data.get('version')}")
    return bad


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.warned: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        print(f"[OK]   {label}{f' — {detail}' if detail else ''}")

    def fail(self, label: str, detail: str = "") -> None:
        print(f"[FAIL] {label}{f' — {detail}' if detail else ''}")
        self.failed.append(label)

    def warn(self, label: str, detail: str = "") -> None:
        print(f"[WARN] {label}{f' — {detail}' if detail else ''}")
        self.warned.append(label)

    def step(self, title: str) -> None:
        print(f"\n=== {title} ===")


def check_files(rep: Report) -> str | None:
    rep.step("必备文件")
    missing = [f for f in REQUIRED_FILES if not (ROOT / f).exists()]
    if missing:
        rep.fail("缺少发布必需文件", ", ".join(missing))
    else:
        rep.ok("必备文件齐全", f"{len(REQUIRED_FILES)} 项")

    version = changelog_version()
    if not version:
        rep.fail("CHANGELOG 没有具体版本段落")
        return None
    rep.ok("CHANGELOG 最新版本", version)
    return version


def check_versions(rep: Report, version: str) -> None:
    rep.step("版本一致性")
    mismatched: list[str] = []
    for label, (kind, path) in VERSION_SOURCES.items():
        got = read_version(kind, path)
        line = f"{label}: {got}"
        if got != version:
            mismatched.append(line)
        else:
            print(f"       {line}")
    mismatched += lock_versions(version)
    if mismatched:
        rep.fail(f"以下版本与 CHANGELOG {version} 不一致", "; ".join(mismatched))
    else:
        rep.ok("六处 package/pyproject/FastAPI 与锁文件版本一致", version)


def check_hygiene(rep: Report, require_clean: bool) -> None:
    rep.step("仓库卫生")
    if not shutil.which("git"):
        rep.warn("未找到 git，跳过卫生检查")
        return

    tracked = sh(["git", "ls-files"]).stdout.splitlines()
    offenders = [
        p for p in tracked
        if any(p.startswith(f) or f in p for f in FORBIDDEN_TRACKED)
        and not any(p.endswith(e) or p == e for e in FORBIDDEN_EXEMPT)
    ]
    # .env 只禁精确同名（backend/.env.example 已在豁免里）
    offenders += [p for p in tracked if Path(p).name == ".env"]
    if offenders:
        rep.fail("版本库中存在不应入库的文件", ", ".join(sorted(set(offenders))[:8]))
    else:
        rep.ok("无密钥/运行数据/构建产物入库", f"已跟踪 {len(tracked)} 个文件")

    ws = sh(["git", "diff", "--check"])
    if ws.returncode != 0:
        rep.fail("git diff --check 有告警", ws.stdout.strip()[:200])
    else:
        rep.ok("git diff --check 干净")

    status = sh(["git", "status", "--porcelain"]).stdout.strip()
    if status:
        count = len(status.splitlines())
        if require_clean:
            rep.fail("工作树不干净", f"{count} 处未提交变更")
        else:
            rep.warn("工作树有未提交变更（发布前应提交）", f"{count} 处")
    else:
        rep.ok("工作树干净")


def check_tests(rep: Report) -> None:
    rep.step("后端质量门槛（ruff + pytest）")
    if not (BACKEND / ".venv").exists() and not shutil.which("ruff"):
        rep.warn("backend/.venv 不存在且 PATH 无 ruff，跳过质量门槛")
        return
    py = BACKEND / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    interpreter = str(py if py.exists() else (PY))
    lint = sh([interpreter, "-m", "ruff", "check", "app", "tests"], cwd=BACKEND)
    lint_last = (lint.stdout or lint.stderr).strip().splitlines()
    if lint.returncode != 0:
        rep.fail("ruff 未通过", lint_last[-1] if lint_last else "")
    else:
        rep.ok("ruff 通过")
    # 固定 --basetemp：Windows 下 pytest 默认在 %TEMP% 建目录，部分环境 os.scandir 被拒（
    # PermissionError: [WinError 5]）会让整套用例在 fixture 阶段报 ERROR；该目录已在 .gitignore 中
    test = sh(
        [interpreter, "-m", "pytest", "tests", "-q", "--basetemp=.pytest_tmp"], cwd=BACKEND
    )
    tail = [line for line in (test.stdout or "").splitlines() if " passed" in line or " failed" in line]
    if test.returncode != 0:
        rep.fail("pytest 未通过", tail[-1] if tail else "见上方输出")
    else:
        rep.ok("pytest 通过", tail[-1] if tail else "")


def check_build(rep: Report) -> None:
    rep.step("前端构建")
    # Windows 上 which 解析到 npm.cmd，但 CreateProcess 只会补 .exe，
    # 直接传 "npm" 会 FileNotFoundError；统一用解析后的完整路径调用
    npm = shutil.which("npm")
    if not npm:
        rep.fail("未找到 npm，无法验证前端构建")
        return
    build = sh([npm, "run", "build"])
    build_last = (build.stdout or build.stderr).strip().splitlines()
    if build.returncode != 0:
        rep.fail("前端构建未通过", build_last[-1] if build_last else "")
    else:
        rep.ok("两端构建通过")


def cmd_check(args: argparse.Namespace) -> int:
    rep = Report()
    print(f"发布前校验：{ROOT}")
    version = check_files(rep)
    if version:
        check_versions(rep, version)
    check_hygiene(rep, require_clean=not args.allow_dirty)
    if not args.skip_tests:
        check_tests(rep)
    if not args.skip_build:
        check_build(rep)

    rep.step("结论")
    if rep.failed:
        print(f"不可发布：{len(rep.failed)} 项失败，{len(rep.warned)} 项告警")
        for item in rep.failed:
            print(f"  - {item}")
        return 1
    print(f"校验通过：0 项失败，{len(rep.warned)} 项告警")
    for item in rep.warned:
        print(f"  - {item}")
    return 0


def compose_images(compose_files: list[str]) -> list[str]:
    cmd = ["docker", "compose"]
    for f in compose_files:
        cmd += ["-f", f]
    cmd += ["config", "--images"]
    res = sh(cmd)
    if res.returncode != 0:
        raise SystemExit(f"无法列出 Compose 镜像：{(res.stderr or res.stdout).strip()}")
    return sorted({line.strip() for line in res.stdout.splitlines() if line.strip()})


OFFLINE_OVERRIDE = """# 由 scripts/release.py bundle 生成：离线环境不再构建，直接使用预置镜像。
# 用法：docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d --no-build
services:
  backend:
    image: {backend}
    build: !reset null
  web:
    image: {web}
    build: !reset null
"""

OFFLINE_README = """离线 / 内网部署包（AgentDock v{version}）
================================================

1. 拷贝本目录到可联网机器之外的目标主机（U 盘 / 内网文件服务器均可）。

2. 目标主机加载镜像（需要 Docker Engine + Compose v2.24.4+）：
   Linux:   bash load-images.sh
   Windows: powershell -File load-images.ps1

3. 校验镜像包完整性（可选但建议）：
   sha256sum -c SHA256SUMS.txt        # Linux
   Get-FileHash SHA256SUMS.txt -Algorithm SHA256   # Windows

4. 准备配置：把 docker-compose.yml 与 backend.env.example 放到同一目录，
   cp backend.env.example .env
   必填：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID、EMBED_*、JWT_SECRET（openssl rand -base64 48）
   演示数据：SEED_DEMO=true（否则只创建一个管理员，随机密码在 backend 首次启动日志打印一次）

5. 启动（离线一律加 --no-build，避免尝试联网构建）：
   docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d --no-build
   docker compose ps    # postgres / qdrant / redis / backend / web 均应为 healthy

6. 入口：http://<host>:8080/ 客户对话端；http://<host>:8080/admin/ 运营台

模型接入说明（离线环境重点）
---------------------------
平台不含内置 mock 模型模式，对话与向量必须能访问一个 OpenAI 兼容端点：

- 对话模型：LLM_BASE_URL 指向内网推理服务（vLLM / Ollama / Xinference / LM Studio 的 OpenAI 兼容接口）。
- 向量模型：EMBED_MODEL_TYPE=dashscope 且设置 EMBED_BASE_URL 时，框架按 OpenAI 兼容
  POST {{base_url}}/embeddings 调用，因此同样可指向内网 embedding 服务。
  EMBED_MODEL_TYPE=local（sentence-transformers）需要额外安装依赖并预置模型权重，当前镜像未包含。
  EMBED_MODEL_TYPE=tfidf 可完全离线（零依赖、质量最低），但换用会导致向量维度变化，需清空 Qdrant 集合重建。
- 首次向量维度以实际 embedder 为准，切换 embedding 方案后请重建索引。

详见 docs/deployment.md 的「离线 / 内网部署」章节与 docs/faq.md。
"""

LOAD_SH = """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "加载镜像包（$(du -h images.tar | cut -f1)）..."
docker load -i images.tar
echo "完成，本地镜像："
docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'smart-cs|pgvector|qdrant|redis' | sort
"""

LOAD_PS1 = """$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "加载镜像包 images.tar ..."
docker load -i images.tar
Write-Host "完成，本地镜像："
docker images --format "{{.Repository}}:{{.Tag}}" | Select-String "smart-cs|pgvector|qdrant|redis"
"""


def cmd_bundle(args: argparse.Namespace) -> int:
    version = changelog_version()
    if not version:
        print("CHANGELOG 无具体版本，无法命名离线包")
        return 1
    out = Path(args.out).resolve() / f"agentdock-offline-v{version}"
    compose_files = args.compose_file or ["docker-compose.yml"]
    images = compose_images(compose_files)
    backend_img = next((i for i in images if i.endswith("backend")), "smart-cs-backend")
    web_img = next((i for i in images if i.endswith("web")), "smart-cs-web")

    print(f"离线包版本 v{version}")
    print(f"输出目录：{out}")
    print("镜像清单：")
    for i in images:
        print(f"  - {i}")
    if args.dry_run:
        print("\n--dry-run：未构建、未导出。去掉 --dry-run 执行实际打包。")
        return 0

    if not shutil.which("docker"):
        print("未找到 docker 命令")
        return 1

    out.mkdir(parents=True, exist_ok=True)
    if not args.no_build:
        cmd = ["docker", "compose"]
        for f in compose_files:
            cmd += ["-f", f]
        cmd += ["build"]
        build = sh(cmd)
        print(build.stdout[-2000:] or build.stderr[-2000:])
        if build.returncode != 0:
            print("镜像构建失败，已中止")
            return 1

    tar = out / "images.tar"
    save = sh(["docker", "save", "-o", str(tar), *images])
    if save.returncode != 0:
        print(f"docker save 失败：{(save.stderr or save.stdout).strip()[:400]}")
        return 1
    print(f"已导出 {tar.name}：{tar.stat().st_size / 1024 / 1024:.0f} MB")

    (out / "docker-compose.offline.yml").write_text(
        OFFLINE_OVERRIDE.format(backend=backend_img, web=web_img), encoding="utf-8"
    )
    (out / "README-OFFLINE.txt").write_text(
        OFFLINE_README.format(version=version), encoding="utf-8"
    )
    (out / "load-images.sh").write_text(LOAD_SH, encoding="utf-8")
    (out / "load-images.ps1").write_text(LOAD_PS1, encoding="utf-8")
    shutil.copy2(ROOT / "docker-compose.yml", out / "docker-compose.yml")
    shutil.copy2(BACKEND / ".env.example", out / "backend.env.example")

    digest = hashlib.sha256(tar.read_bytes()).hexdigest()
    entries = [f"{digest}  images.tar"]
    small_files = (
        "docker-compose.yml",
        "docker-compose.offline.yml",
        "backend.env.example",
        "load-images.sh",
        "load-images.ps1",
        "README-OFFLINE.txt",
    )
    for name in small_files:
        p = out / name
        entries.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}")
    (out / "SHA256SUMS.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")

    print("\n离线包内容：")
    for p in sorted(out.iterdir()):
        size = f"{p.stat().st_size / 1024 / 1024:.1f} MB" if p.is_file() else "dir"
        print(f"  {p.name:32} {size}")
    print(f"\nsha256(images.tar) = {digest}")
    print("校验：sha256sum -c SHA256SUMS.txt")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    version = changelog_version()
    if not version:
        print("CHANGELOG 无具体版本，无法打 tag")
        return 1
    tag = f"v{version}"
    if f"## [{version}]" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        print(f"CHANGELOG 缺少 [{version}] 段落")
        return 1
    exists = sh(["git", "tag", "-l", tag]).stdout.strip()
    if exists and not args.force:
        print(f"tag {tag} 已存在（--force 可删除重建）")
        return 1

    head = sh(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    message = f"AgentDock v{version}\n\nSee CHANGELOG.md for details."
    commands = [["git", "tag", "-a", "-f", "-m", message, tag, head]] if exists else [
        ["git", "tag", "-a", "-m", message, tag, head]
    ]
    if args.push:
        commands.append(["git", "push", "--force" if exists else "-u", "origin", tag])

    print(f"目标 tag：{tag} → {head}")
    for cmd in commands:
        printable = " ".join(f'"{c}"' if " " in c or len(c) > 40 else c for c in cmd)
        print(f"  $ {printable}")
    if not args.push:
        print("\n未指定 --push：只创建本地 tag 前请再确认，加 --push 才推送到 origin。")

    for cmd in commands:
        res = sh(cmd)
        if res.returncode != 0:
            print(f"执行失败：{(res.stderr or res.stdout).strip()[:300]}")
            return 1
    print(f"\n{tag} 已{'创建并推送' if args.push else '创建（本地）'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentDock 发布脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="发布前校验")
    p_check.add_argument("--skip-tests", action="store_true", help="跳过 ruff + pytest")
    p_check.add_argument("--skip-build", action="store_true", help="跳过前端构建")
    p_check.add_argument("--allow-dirty", action="store_true", help="允许工作树有未提交变更")
    p_check.set_defaults(func=cmd_check)

    p_bundle = sub.add_parser("bundle", help="导出离线镜像包")
    p_bundle.add_argument("--out", default=str(ROOT / "dist"), help="输出根目录（默认 dist/）")
    p_bundle.add_argument("--compose-file", action="append", help="Compose 文件，可多次指定")
    p_bundle.add_argument("--no-build", action="store_true", help="只导出已有镜像，不重新构建")
    p_bundle.add_argument("--dry-run", action="store_true", help="只打印计划，不构建不导出")
    p_bundle.set_defaults(func=cmd_bundle)

    p_tag = sub.add_parser("tag", help="创建 annotated tag（默认 dry-run 提示，需 --push 才推送）")
    p_tag.add_argument("--push", action="store_true", help="推送到 origin")
    p_tag.add_argument("--force", action="store_true", help="tag 已存在时覆盖")
    p_tag.set_defaults(func=cmd_tag)
    return parser


def main(argv: list[str] | None = None) -> int:
    _ascii_safe_print()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
