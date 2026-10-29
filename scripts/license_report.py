#!/usr/bin/env python
"""生成 / 校验第三方依赖的许可证清单。

用法（必须用后端虚拟环境跑，否则扫不到 Python 依赖）：

    backend\\.venv\\Scripts\\python.exe scripts\\license_report.py
    backend/.venv/bin/python scripts/license_report.py --check

- 不带参数：重写 `docs/third-party-licenses.md`。
- `--check`：只校验，出现非宽松许可证（GPL/AGPL 等）或无法识别的许可证时非零退出。

清单口径：
- Python 侧列 `backend/.venv` 里**实际安装**的全部发行版（含传递依赖），并标注是否
  `backend/requirements.txt` 的直接依赖；
- Node 侧列 `package-lock.json` 口径下的 `node_modules` 实际安装版本，标注生产 / 开发依赖，
  生产依赖才是随镜像分发的部分。

脚本只读本地已安装元数据，不联网。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "third-party-licenses.md"
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
LOCKFILE = ROOT / "package-lock.json"

# 宽松 / 弱著佐权：可直接随产品分发（MPL 仅要求修改其自身文件时开源）
ALLOWED = {
    "MIT",
    "MIT-0",
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "PSF-2.0",
    "Python-2.0",
    "MPL-2.0",
    "Unlicense",
    "CC0-1.0",
    "Zlib",
    "0BSD",
}
# 强著佐权：需要人工确认（本项目不链接此类库）
COPYLEFT = ("GPL", "AGPL", "LGPL", "EUPL", "SSPL", "CPAL", "CC-BY-SA")

_CLASSIFIERS = {
    "MIT": "MIT",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
}

_PATTERNS = (
    # 顺序即优先级：先判最具体的组合写法，再判断单个关键词
    (re.compile(r"0BSD", re.I), "0BSD"),
    (re.compile(r"CC[- ]BY[- ]NC", re.I), "CC-BY-NC-SA-4.0"),
    (re.compile(r"CC[- ]BY", re.I), "CC-BY-4.0"),
    (re.compile(r"NonCommercial", re.I), "NONCOMMERCIAL"),
    (re.compile(r"Python[- ]2\.0", re.I), "Python-2.0"),
    (re.compile(r"\bMIT\b", re.I), "MIT"),
    (re.compile(r"Apache\s*(License)?[^0-9]*2\.0", re.I), "Apache-2.0"),
    (re.compile(r"\bBSD[ -]?3", re.I), "BSD-3-Clause"),
    (re.compile(r"\bBSD[ -]?2", re.I), "BSD-2-Clause"),
    (re.compile(r"\bBSD\b", re.I), "BSD"),
    (re.compile(r"\bISC\b", re.I), "ISC"),
    (re.compile(r"MPL[ -]?2\.0|Mozilla Public License", re.I), "MPL-2.0"),
    (re.compile(r"LGPL|Lesser General Public", re.I), "LGPL"),
    (re.compile(r"AGPL|Affero", re.I), "AGPL"),
    (re.compile(r"GPL|General Public License", re.I), "GPL"),
    (re.compile(r"Python Software Foundation|PSF", re.I), "PSF-2.0"),
    (re.compile(r"Unlicense", re.I), "Unlicense"),
    (re.compile(r"\bCC0", re.I), "CC0-1.0"),
    (re.compile(r"\bZlib\b", re.I), "Zlib"),
)


def normalize_license(raw: str | None) -> str:
    """把各种写法（SPDX、分类器、整段许可证正文）归一成短标识。"""
    if not raw:
        return "UNKNOWN"
    text = " ".join(raw.split())
    if len(text) <= 40:
        for pattern, name in _PATTERNS:
            match = pattern.search(text)
            if match:
                return name
    else:
        # 整段正文：先按关键词找，顺序上把 LGPL/AGPL 放在 GPL 之前
        for pattern, name in _PATTERNS:
            if pattern.search(text):
                return name
    return "UNKNOWN"


def python_license(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return normalize_license(expr)
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            tail = classifier.split(" :: ")[-1]
            # 分类器写法五花八门（"MIT License" / "MIT"），统一过一遍关键词匹配
            return _CLASSIFIERS.get(tail) or normalize_license(tail)
    return normalize_license(meta.get("License"))


def direct_requirements() -> set[str]:
    names: set[str] = set()
    if not REQUIREMENTS.exists():
        return names
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[\s;]", line, maxsplit=1)[0].strip()
        if name:
            names.add(re.sub(r"[-_.]+", "-", name).lower())
    return names


def _from_node_modules(dist: metadata.Distribution) -> bool:
    """从仓库根运行时 sys.path 含 cwd，node_modules 里的 JS 包会被当成 Python 发行版。"""
    try:
        return "node_modules" in Path(dist.locate_file("")).parts
    except (AttributeError, OSError):
        return False


def scan_python() -> list[dict[str, str]]:
    direct = direct_requirements()
    rows = []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name") or ""
        if not name or _from_node_modules(dist):
            continue
        key = re.sub(r"[-_.]+", "-", name).lower()
        rows.append(
            {
                "name": name,
                "version": dist.version,
                "license": python_license(dist),
                "direct": "是" if key in direct else "",
            }
        )
    rows.sort(key=lambda row: row["name"].lower())
    return rows


def _node_package_jsons() -> list[Path]:
    found: list[Path] = []
    for base in (ROOT, ROOT / "frontend", ROOT / "admin-frontend", ROOT / "packages" / "shared"):
        if not (base / "node_modules").is_dir():
            continue
        for pattern in (
            "node_modules/*/package.json",
            "node_modules/@*/*/package.json",
            "node_modules/*/node_modules/*/package.json",
            "node_modules/*/node_modules/@*/*/package.json",
        ):
            found.extend(base.glob(pattern))
    return found


def _node_license(pkg: dict) -> str:
    license_field = pkg.get("license")
    if isinstance(license_field, dict):
        license_field = license_field.get("type")
    if isinstance(license_field, str) and license_field.strip():
        return normalize_license(license_field)
    licenses = pkg.get("licenses")
    if isinstance(licenses, list) and licenses:
        first = licenses[0]
        if isinstance(first, dict):
            first = first.get("type")
        if isinstance(first, str):
            return normalize_license(first)
    return "UNKNOWN"


def scan_node() -> list[dict[str, str]]:
    """读 package-lock v3：每个包都带 license 与 dev 标记，比 npm ls 可靠且不需联网。"""
    try:
        packages = json.loads(LOCKFILE.read_text(encoding="utf-8")).get("packages") or {}
    except (OSError, json.JSONDecodeError):
        return []

    collected: dict[tuple[str, str], dict[str, str]] = {}
    for path, meta in packages.items():
        if not path.startswith("node_modules/") or not isinstance(meta, dict):
            continue
        version = str(meta.get("version") or "")
        if not version:
            continue  # workspace / link 占位，不参与分析
        name = path.split("node_modules/")[-1]
        license_field = meta.get("license")
        collected[(name, version)] = {
            "license": normalize_license(license_field) if isinstance(license_field, str) else "UNKNOWN",
            "usage": "开发" if meta.get("dev") or meta.get("devOptional") else "生产",
        }

    # 锁文件里偶尔没写 license（老包），回退到实际安装目录的 package.json
    if any(row["license"] == "UNKNOWN" for row in collected.values()):
        for pkg_path in _node_package_jsons():
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = (pkg.get("name"), str(pkg.get("version") or ""))
            if collected.get(key, {}).get("license") == "UNKNOWN":
                collected[key]["license"] = _node_license(pkg)

    rows = [{"name": name, "version": version, **info} for (name, version), info in collected.items()]
    rows.sort(key=lambda row: (row["name"].lower(), row["version"]))
    return rows

def summarize(rows: list[dict[str, str]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["license"]] = summary.get(row["license"], 0) + 1
    return dict(sorted(summary.items(), key=lambda item: (-item[1], item[0])))


def violations(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """白名单之外一律拦下：宁可多问一句，也不默认「大概是 MIT」。"""
    return [row for row in rows if row["license"] not in ALLOWED]


def _reason(license_id: str) -> str:
    if license_id == "UNKNOWN":
        return "元数据里没有可识别的许可证，需要查包内 LICENSE 文件"
    if license_id.startswith(("CC-BY-NC", "NONCOMMERCIAL")):
        return "含非商业条款（NC），商用需单独授权"
    if license_id.startswith(COPYLEFT):
        return "强/弱著佐权，与 Apache-2.0 分发需逐项确认"
    return "不在宽松许可白名单内"


def render(py_rows: list[dict[str, str]], node_rows: list[dict[str, str]]) -> str:
    py_sum, node_sum = summarize(py_rows), summarize(node_rows)
    keys = sorted(set(py_sum) | set(node_sum), key=lambda key: -(py_sum.get(key, 0) + node_sum.get(key, 0)))
    prod = sum(1 for row in node_rows if row["usage"] == "生产")
    lines = [
        "# 第三方依赖许可证清单",
        "",
        "> 由 `scripts/license_report.py` 生成，**请勿手工编辑**；重新生成：",
        "> `backend/.venv/bin/python scripts/license_report.py`",
        "> （Windows：`backend\\\\.venv\\\\Scripts\\\\python.exe`）。",
        ">",
        f"> 扫描日期：{date.today().isoformat()}；Python {sys.version.split()[0]}（{len(py_rows)} 个已安装发行版）、",
        f"> Node {len(node_rows)} 个已安装包（其中生产依赖 {prod} 个）。",
        "",
        "口径说明：Python 列为 `backend/.venv` 实际安装的全部发行版（含传递依赖）；Node 列的「生产」才会随镜像分发，",
        "「开发」只在本机构建 / 测试时使用，不进入产物。",
        "",
        "## 许可证汇总",
        "",
        "| 许可证 | Python | Node（生产） | Node（开发） |",
        "|---|---:|---:|---:|",
    ]
    for key in keys:
        node_prod = sum(1 for row in node_rows if row["license"] == key and row["usage"] == "生产")
        node_dev = sum(1 for row in node_rows if row["license"] == key and row["usage"] != "生产")
        lines.append(f"| {key} | {py_sum.get(key, 0)} | {node_prod} | {node_dev} |")

    flagged = [(row, "Python") for row in violations(py_rows)]
    flagged += [(row, "Node") for row in violations(node_rows)]
    if flagged:
        lines += [
            "",
            "## 需要人工确认",
            "",
            "下表是白名单（MIT / BSD / Apache-2.0 / ISC / PSF / MPL-2.0）之外的包，发布前逐项确认处置口径。",
            "",
            "| 包 | 版本 | 许可证 | 侧 | 结论 |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {row['name']} | {row['version']} | {row['license']} | {side} | {_reason(row['license'])} |"
            for row, side in flagged
        ]

    lines += [
        "",
        "## Python 后端",
        "",
        "| 包 | 版本 | 许可证 | `requirements.txt` 直接依赖 |",
        "|---|---|---|---|",
    ]
    lines += [f"| {r['name']} | {r['version']} | {r['license']} | {r['direct']} |" for r in py_rows]

    lines += ["", "## Node / 前端", "", "| 包 | 版本 | 许可证 | 归属 |", "|---|---|---|---|"]
    lines += [f"| {r['name']} | {r['version']} | {r['license']} | {r['usage']} |" for r in node_rows]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 / 校验第三方依赖许可证清单")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()

    py_rows, node_rows = scan_python(), scan_node()
    if len(py_rows) < 30:
        print("[警告] Python 发行版数量异常偏少，可能没在后端虚拟环境里运行。", file=sys.stderr)

    bad = violations(py_rows) + violations(node_rows)
    if args.check:
        if bad:
            print(f"发现 {len(bad)} 个需要人工确认的许可证：")
            for row in bad:
                print(f"  - {row['name']} {row['version']}: {row['license']}")
            return 1
        print(f"许可证校验通过：{len(py_rows)} 个 Python 发行版、{len(node_rows)} 个 Node 包均为宽松许可。")
        return 0

    OUTPUT.write_text(render(py_rows, node_rows), encoding="utf-8")
    print(f"已写入 {OUTPUT.relative_to(ROOT)}：Python {len(py_rows)} 个、Node {len(node_rows)} 个。")
    if bad:
        print(f"[注意] {len(bad)} 个包需要人工确认：", file=sys.stderr)
        for row in bad:
            print(f"  - {row['name']} {row['version']}: {row['license']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
