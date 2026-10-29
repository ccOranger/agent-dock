# 第三方依赖许可证清单

> 由 `scripts/license_report.py` 生成，**请勿手工编辑**；重新生成：
> `backend/.venv/bin/python scripts/license_report.py`
> （Windows：`backend\\.venv\\Scripts\\python.exe`）。
>
> 扫描日期：2026-10-29；Python 3.12.14（188 个已安装发行版）、
> Node 274 个已安装包（其中生产依赖 87 个）。

口径说明：Python 列为 `backend/.venv` 实际安装的全部发行版（含传递依赖）；Node 列的「生产」才会随镜像分发，
「开发」只在本机构建 / 测试时使用，不进入产物。

## 许可证汇总

| 许可证 | Python | Node（生产） | Node（开发） |
|---|---:|---:|---:|
| MIT | 91 | 77 | 156 |
| Apache-2.0 | 44 | 1 | 17 |
| BSD-3-Clause | 16 | 4 | 1 |
| BSD | 21 | 0 | 0 |
| ISC | 4 | 1 | 7 |
| BSD-2-Clause | 3 | 2 | 6 |
| PSF-2.0 | 4 | 0 | 0 |
| 0BSD | 1 | 1 | 0 |
| Python-2.0 | 1 | 1 | 0 |
| Unlicense | 1 | 0 | 0 |
| MPL-2.0 | 1 | 0 | 0 |
| CC-BY-NC-SA-4.0 | 1 | 0 | 0 |

## 需要人工确认

下表是白名单（MIT / BSD / Apache-2.0 / ISC / PSF / MPL-2.0）之外的包，发布前逐项确认处置口径。

| 包 | 版本 | 许可证 | 侧 | 结论 |
|---|---|---|---|---|
| hello-agents | 0.2.9 | CC-BY-NC-SA-4.0 | Python | 含非商业条款（NC），商用需单独授权 |

## Python 后端

| 包 | 版本 | 许可证 | `requirements.txt` 直接依赖 |
|---|---|---|---|
| a2a-sdk | 1.1.5 | Apache-2.0 |  |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 |  |
| aiohttp | 3.14.3 | MIT | 是 |
| aiologic | 0.17.1 | ISC |  |
| aiosignal | 1.4.0 | Apache-2.0 |  |
| alembic | 1.13.2 | MIT | 是 |
| annotated-doc | 0.0.5 | MIT |  |
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| asyncpg | 0.30.0 | Apache-2.0 | 是 |
| attrs | 26.1.0 | MIT |  |
| Authlib | 1.8.0 | BSD |  |
| bcrypt | 4.0.1 | Apache-2.0 | 是 |
| beartype | 0.22.9 | MIT |  |
| beautifulsoup4 | 4.12.3 | MIT | 是 |
| blis | 1.3.3 | BSD |  |
| cachetools | 5.5.0 | MIT | 是 |
| catalogue | 2.0.10 | MIT |  |
| certifi | 2026.7.22 | MPL-2.0 |  |
| cffi | 2.1.1 | MIT |  |
| charset-normalizer | 3.5.1 | MIT |  |
| click | 8.5.0 | BSD-3-Clause |  |
| cloudpathlib | 0.25.0 | MIT |  |
| cloudpickle | 3.1.2 | BSD |  |
| cobble | 0.1.4 | BSD |  |
| colorama | 0.4.6 | BSD |  |
| coloredlogs | 15.0.1 | MIT |  |
| confection | 1.3.3 | MIT |  |
| coverage | 7.16.1 | Apache-2.0 |  |
| cryptography | 50.0.1 | Apache-2.0 |  |
| culsans | 0.11.0 | ISC |  |
| cyclopts | 4.25.3 | Apache-2.0 |  |
| cymem | 2.0.13 | MIT |  |
| dashscope | 1.27.6 | Apache-2.0 | 是 |
| defusedxml | 0.7.1 | PSF-2.0 |  |
| diskcache | 5.6.3 | Apache-2.0 |  |
| distro | 1.9.0 | Apache-2.0 |  |
| dnspython | 2.8.0 | ISC |  |
| docker | 7.2.0 | Apache-2.0 |  |
| docstring_parser | 0.18.0 | MIT |  |
| ecdsa | 0.19.2 | MIT |  |
| email-validator | 2.3.0 | Unlicense |  |
| exceptiongroup | 1.3.1 | MIT |  |
| fastapi | 0.141.1 | MIT | 是 |
| fastmcp | 2.13.3 | Apache-2.0 | 是 |
| filelock | 4.0.1 | MIT |  |
| flatbuffers | 25.12.19 | Apache-2.0 |  |
| frozenlist | 1.8.0 | Apache-2.0 |  |
| fsspec | 2026.9.0 | BSD-3-Clause |  |
| google-api-core | 2.38.0 | Apache-2.0 |  |
| google-auth | 2.58.0 | Apache-2.0 |  |
| googleapis-common-protos | 1.75.3 | Apache-2.0 |  |
| greenlet | 3.5.6 | MIT |  |
| grpcio | 1.84.0 | Apache-2.0 |  |
| grpcio-tools | 1.84.0 | Apache-2.0 |  |
| h11 | 0.16.0 | MIT |  |
| h2 | 4.4.1 | MIT |  |
| hello-agents | 0.2.9 | CC-BY-NC-SA-4.0 | 是 |
| hf-xet | 1.6.0 | Apache-2.0 |  |
| hpack | 4.2.0 | MIT |  |
| httpcore | 1.0.9 | BSD-3-Clause |  |
| httptools | 0.8.0 | MIT |  |
| httpx | 0.28.1 | BSD | 是 |
| httpx-sse | 0.4.3 | MIT |  |
| huggingface_hub | 1.32.0 | Apache-2.0 | 是 |
| humanfriendly | 10.0 | MIT |  |
| hyperframe | 6.1.0 | MIT |  |
| idna | 3.20 | BSD-3-Clause |  |
| iniconfig | 2.3.0 | MIT |  |
| Jinja2 | 3.1.6 | BSD |  |
| jiter | 0.17.0 | MIT |  |
| joblib | 1.6.0 | BSD-3-Clause |  |
| joserfc | 1.7.5 | BSD |  |
| json-rpc | 1.15.0 | MIT |  |
| jsonschema | 4.26.0 | MIT |  |
| jsonschema-path | 0.5.0 | Apache-2.0 |  |
| jsonschema-specifications | 2025.9.1 | MIT |  |
| loguru | 0.7.3 | MIT | 是 |
| lxml | 6.1.3 | BSD-3-Clause |  |
| magika | 0.6.3 | Apache-2.0 |  |
| Mako | 1.4.3 | MIT |  |
| mammoth | 1.11.0 | BSD |  |
| Markdown | 3.7 | BSD | 是 |
| markdown-it-py | 4.2.0 | MIT |  |
| markdownify | 1.2.3 | MIT |  |
| markitdown | 0.1.8 | MIT | 是 |
| MarkupSafe | 3.0.3 | BSD-3-Clause |  |
| mcp | 1.22.0 | MIT |  |
| mdurl | 0.1.2 | MIT |  |
| mpmath | 1.3.0 | BSD |  |
| multidict | 6.9.1 | Apache-2.0 |  |
| murmurhash | 1.0.15 | MIT |  |
| narwhals | 2.26.0 | MIT |  |
| neo4j | 6.3.1 | Python-2.0 |  |
| networkx | 3.7 | BSD-3-Clause |  |
| numpy | 2.5.3 | 0BSD |  |
| onnxruntime | 1.20.1 | MIT |  |
| openai | 1.109.1 | Apache-2.0 |  |
| openapi-pydantic | 0.5.1 | MIT |  |
| opentelemetry-api | 1.44.0 | Apache-2.0 |  |
| packaging | 26.3 | Apache-2.0 |  |
| passlib | 1.7.4 | BSD | 是 |
| pathable | 0.6.0 | Apache-2.0 |  |
| pathvalidate | 3.3.1 | MIT |  |
| pdfminer.six | 20260107 | MIT |  |
| pdfplumber | 0.11.10 | MIT |  |
| pgvector | 0.3.2 | MIT | 是 |
| pillow | 12.3.0 | MIT |  |
| platformdirs | 4.11.12 | MIT |  |
| pluggy | 1.6.0 | MIT |  |
| portalocker | 2.10.1 | BSD |  |
| preshed | 3.0.13 | MIT |  |
| prometheus_client | 0.21.0 | Apache-2.0 | 是 |
| propcache | 0.5.4 | Apache-2.0 |  |
| proto-plus | 1.28.4 | Apache-2.0 |  |
| protobuf | 7.36.2 | BSD |  |
| py-key-value-aio | 0.3.0 | Apache-2.0 |  |
| py-key-value-shared | 0.3.0 | Apache-2.0 |  |
| pyasn1 | 0.6.4 | BSD-2-Clause |  |
| pyasn1_modules | 0.4.2 | BSD |  |
| pycparser | 3.0 | BSD-3-Clause |  |
| pydantic | 2.13.5 | MIT | 是 |
| pydantic-settings | 2.15.0 | MIT | 是 |
| pydantic_core | 2.46.5 | MIT |  |
| Pygments | 2.21.0 | BSD-2-Clause |  |
| PyJWT | 2.14.0 | MIT |  |
| pypdf | 5.0.1 | BSD | 是 |
| pypdfium2 | 5.14.0 | Apache-2.0 |  |
| pyperclip | 1.11.0 | BSD |  |
| pyreadline3 | 3.5.6 | BSD |  |
| pytest | 8.3.3 | MIT | 是 |
| pytest-asyncio | 0.24.0 | Apache-2.0 | 是 |
| pytest-cov | 6.0.0 | MIT | 是 |
| python-dateutil | 2.9.0.post0 | BSD | 是 |
| python-docx | 1.1.2 | MIT | 是 |
| python-dotenv | 1.2.3 | BSD-3-Clause | 是 |
| python-jose | 3.3.0 | MIT | 是 |
| python-multipart | 0.0.32 | Apache-2.0 | 是 |
| pytz | 2026.3.post1 | MIT |  |
| pywin32 | 312 | PSF-2.0 |  |
| PyYAML | 6.0.3 | MIT |  |
| qdrant-client | 1.9.1 | Apache-2.0 | 是 |
| redis | 5.0.8 | MIT | 是 |
| referencing | 0.37.0 | MIT |  |
| regex | 2026.9.10 | Apache-2.0 |  |
| requests | 2.34.2 | Apache-2.0 |  |
| rich | 15.0.0 | MIT |  |
| rich-rst | 2.1.0 | MIT |  |
| rpds-py | 2026.6.3 | MIT |  |
| rsa | 4.9.1 | Apache-2.0 |  |
| ruff | 0.17.1 | MIT |  |
| scikit-learn | 1.9.1 | BSD-3-Clause |  |
| scipy | 1.18.1 | BSD |  |
| setuptools | 84.0.0 | MIT |  |
| shellingham | 1.5.4 | ISC |  |
| six | 1.17.0 | MIT |  |
| smart_open | 8.0.1 | MIT |  |
| sniffio | 1.3.1 | MIT |  |
| soupsieve | 2.9.2 | MIT |  |
| spacy | 3.8.16 | MIT |  |
| spacy-legacy | 3.0.12 | MIT |  |
| spacy-loggers | 1.0.5 | MIT |  |
| SQLAlchemy | 2.0.35 | MIT | 是 |
| srsly | 2.5.3 | MIT |  |
| sse-starlette | 3.4.11 | BSD-3-Clause |  |
| starlette | 1.7.0 | BSD-3-Clause |  |
| structlog | 26.1.0 | MIT | 是 |
| sympy | 1.14.0 | BSD |  |
| tenacity | 9.0.0 | Apache-2.0 | 是 |
| testcontainers | 4.8.2 | Apache-2.0 | 是 |
| thinc | 8.3.13 | MIT |  |
| threadpoolctl | 3.7.0 | BSD-3-Clause |  |
| tiktoken | 0.14.0 | MIT |  |
| tqdm | 4.70.1 | MIT |  |
| typer | 0.27.2 | MIT |  |
| typing-inspection | 0.4.4 | MIT |  |
| typing_extensions | 4.16.0 | PSF-2.0 |  |
| urllib3 | 2.8.0 | MIT |  |
| uv | 0.12.18 | MIT | 是 |
| uvicorn | 0.53.0 | BSD-3-Clause | 是 |
| wasabi | 1.1.3 | MIT |  |
| watchfiles | 1.3.0 | MIT |  |
| weasel | 1.0.0 | MIT |  |
| websocket-client | 1.9.2 | Apache-2.0 |  |
| websockets | 17.1 | BSD-3-Clause |  |
| win32_setctime | 1.2.0 | MIT |  |
| wrapt | 2.4.1 | BSD-2-Clause |  |
| yarl | 1.25.1 | Apache-2.0 |  |

## Node / 前端

| 包 | 版本 | 许可证 | 归属 |
|---|---|---|---|
| @babel/helper-string-parser | 7.29.7 | MIT | 生产 |
| @babel/helper-validator-identifier | 7.29.7 | MIT | 生产 |
| @babel/parser | 7.29.9 | MIT | 生产 |
| @babel/types | 7.29.8 | MIT | 生产 |
| @ctrl/tinycolor | 4.2.1 | MIT | 生产 |
| @element-plus/icons-vue | 2.3.2 | MIT | 生产 |
| @esbuild/aix-ppc64 | 0.21.5 | MIT | 开发 |
| @esbuild/android-arm | 0.21.5 | MIT | 开发 |
| @esbuild/android-arm64 | 0.21.5 | MIT | 开发 |
| @esbuild/android-x64 | 0.21.5 | MIT | 开发 |
| @esbuild/darwin-arm64 | 0.21.5 | MIT | 开发 |
| @esbuild/darwin-x64 | 0.21.5 | MIT | 开发 |
| @esbuild/freebsd-arm64 | 0.21.5 | MIT | 开发 |
| @esbuild/freebsd-x64 | 0.21.5 | MIT | 开发 |
| @esbuild/linux-arm | 0.21.5 | MIT | 开发 |
| @esbuild/linux-arm64 | 0.21.5 | MIT | 开发 |
| @esbuild/linux-ia32 | 0.21.5 | MIT | 开发 |
| @esbuild/linux-loong64 | 0.21.5 | MIT | 开发 |
| @esbuild/linux-mips64el | 0.21.5 | MIT | 开发 |
| @esbuild/linux-ppc64 | 0.21.5 | MIT | 开发 |
| @esbuild/linux-riscv64 | 0.21.5 | MIT | 开发 |
| @esbuild/linux-s390x | 0.21.5 | MIT | 开发 |
| @esbuild/linux-x64 | 0.21.5 | MIT | 开发 |
| @esbuild/netbsd-x64 | 0.21.5 | MIT | 开发 |
| @esbuild/openbsd-x64 | 0.21.5 | MIT | 开发 |
| @esbuild/sunos-x64 | 0.21.5 | MIT | 开发 |
| @esbuild/win32-arm64 | 0.21.5 | MIT | 开发 |
| @esbuild/win32-ia32 | 0.21.5 | MIT | 开发 |
| @esbuild/win32-x64 | 0.21.5 | MIT | 开发 |
| @eslint-community/eslint-utils | 4.10.1 | MIT | 开发 |
| @eslint-community/regexpp | 4.12.2 | MIT | 开发 |
| @eslint/config-array | 0.21.2 | Apache-2.0 | 开发 |
| @eslint/config-helpers | 0.4.2 | Apache-2.0 | 开发 |
| @eslint/core | 0.17.0 | Apache-2.0 | 开发 |
| @eslint/eslintrc | 3.3.7 | MIT | 开发 |
| @eslint/js | 9.39.5 | MIT | 开发 |
| @eslint/object-schema | 2.1.7 | Apache-2.0 | 开发 |
| @eslint/plugin-kit | 0.4.1 | Apache-2.0 | 开发 |
| @floating-ui/core | 1.8.0 | MIT | 生产 |
| @floating-ui/dom | 1.8.0 | MIT | 生产 |
| @floating-ui/utils | 0.2.12 | MIT | 生产 |
| @humanfs/core | 0.19.2 | Apache-2.0 | 开发 |
| @humanfs/node | 0.16.8 | Apache-2.0 | 开发 |
| @humanfs/types | 0.15.0 | Apache-2.0 | 开发 |
| @humanwhocodes/module-importer | 1.0.1 | Apache-2.0 | 开发 |
| @humanwhocodes/retry | 0.4.3 | Apache-2.0 | 开发 |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT | 生产 |
| @napi-rs/lzma-linux-x64-gnu | 1.5.1 | MIT | 开发 |
| @playwright/test | 1.64.1 | Apache-2.0 | 开发 |
| @popperjs/core | 2.11.8 | MIT | 生产 |
| @rollup/rollup-android-arm-eabi | 4.64.12 | MIT | 开发 |
| @rollup/rollup-android-arm64 | 4.64.12 | MIT | 开发 |
| @rollup/rollup-darwin-arm64 | 4.64.12 | MIT | 开发 |
| @rollup/rollup-darwin-x64 | 4.64.12 | MIT | 开发 |
| @rollup/rollup-freebsd-arm64 | 4.64.12 | MIT | 开发 |
| @rollup/rollup-freebsd-x64 | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-arm-gnueabihf | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-arm-musleabihf | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-arm64-gnu | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-arm64-musl | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-loong64-gnu | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-loong64-musl | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-ppc64-gnu | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-ppc64-musl | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-riscv64-gnu | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-riscv64-musl | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-s390x-gnu | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-x64-gnu | 4.64.12 | MIT | 开发 |
| @rollup/rollup-linux-x64-musl | 4.64.12 | MIT | 开发 |
| @rollup/rollup-openbsd-x64 | 4.64.12 | MIT | 开发 |
| @rollup/rollup-openharmony-arm64 | 4.64.12 | MIT | 开发 |
| @rollup/rollup-win32-arm64-msvc | 4.64.12 | MIT | 开发 |
| @rollup/rollup-win32-ia32-msvc | 4.64.12 | MIT | 开发 |
| @rollup/rollup-win32-x64-gnu | 4.64.12 | MIT | 开发 |
| @rollup/rollup-win32-x64-msvc | 4.64.12 | MIT | 开发 |
| @types/estree | 1.0.9 | MIT | 开发 |
| @types/json-schema | 7.0.15 | MIT | 开发 |
| @types/lodash | 4.17.26 | MIT | 生产 |
| @types/lodash-es | 4.17.12 | MIT | 生产 |
| @types/node | 26.6.5 | MIT | 开发 |
| @types/web-bluetooth | 0.0.21 | MIT | 生产 |
| @vitejs/plugin-vue | 5.2.4 | MIT | 开发 |
| @vitest/expect | 2.1.9 | MIT | 开发 |
| @vitest/mocker | 2.1.9 | MIT | 开发 |
| @vitest/pretty-format | 2.1.9 | MIT | 开发 |
| @vitest/runner | 2.1.9 | MIT | 开发 |
| @vitest/snapshot | 2.1.9 | MIT | 开发 |
| @vitest/spy | 2.1.9 | MIT | 开发 |
| @vitest/utils | 2.1.9 | MIT | 开发 |
| @volar/language-core | 2.4.15 | MIT | 开发 |
| @volar/source-map | 2.4.15 | MIT | 开发 |
| @volar/typescript | 2.4.15 | MIT | 开发 |
| @vue/compiler-core | 3.5.44 | MIT | 生产 |
| @vue/compiler-dom | 3.5.44 | MIT | 生产 |
| @vue/compiler-sfc | 3.5.44 | MIT | 生产 |
| @vue/compiler-ssr | 3.5.44 | MIT | 生产 |
| @vue/compiler-vue2 | 2.7.16 | MIT | 开发 |
| @vue/devtools-api | 6.6.4 | MIT | 生产 |
| @vue/language-core | 2.2.12 | MIT | 开发 |
| @vue/reactivity | 3.5.44 | MIT | 生产 |
| @vue/runtime-core | 3.5.44 | MIT | 生产 |
| @vue/runtime-dom | 3.5.44 | MIT | 生产 |
| @vue/server-renderer | 3.5.44 | MIT | 生产 |
| @vue/shared | 3.5.44 | MIT | 生产 |
| @vueuse/core | 15.0.0 | MIT | 生产 |
| @vueuse/metadata | 15.0.0 | MIT | 生产 |
| @vueuse/shared | 15.0.0 | MIT | 生产 |
| acorn | 8.19.0 | MIT | 开发 |
| acorn-jsx | 5.3.2 | MIT | 开发 |
| agent-base | 6.0.2 | MIT | 生产 |
| ajv | 6.15.0 | MIT | 开发 |
| alien-signals | 1.0.13 | MIT | 开发 |
| ansi-styles | 4.3.0 | MIT | 开发 |
| argparse | 2.0.1 | Python-2.0 | 生产 |
| assertion-error | 2.0.1 | MIT | 开发 |
| async-validator | 4.2.5 | MIT | 生产 |
| asynckit | 0.4.0 | MIT | 生产 |
| axios | 1.20.0 | MIT | 生产 |
| balanced-match | 1.0.2 | MIT | 开发 |
| brace-expansion | 1.1.21 | MIT | 开发 |
| brace-expansion | 2.1.7 | MIT | 开发 |
| cac | 6.7.14 | MIT | 开发 |
| call-bind-apply-helpers | 1.0.2 | MIT | 生产 |
| callsites | 3.1.0 | MIT | 开发 |
| chai | 5.3.3 | MIT | 开发 |
| chalk | 4.1.2 | MIT | 开发 |
| check-error | 2.1.3 | MIT | 开发 |
| color-convert | 2.0.1 | MIT | 开发 |
| color-name | 1.1.4 | MIT | 开发 |
| combined-stream | 1.0.8 | MIT | 生产 |
| concat-map | 0.0.1 | MIT | 开发 |
| cross-spawn | 7.0.6 | MIT | 开发 |
| csstype | 3.2.3 | MIT | 生产 |
| dayjs | 1.11.23 | MIT | 生产 |
| de-indent | 1.0.2 | MIT | 开发 |
| debug | 4.4.3 | MIT | 生产 |
| deep-eql | 5.0.2 | MIT | 开发 |
| deep-is | 0.1.4 | MIT | 开发 |
| delayed-stream | 1.0.0 | MIT | 生产 |
| dunder-proto | 1.0.1 | MIT | 生产 |
| echarts | 6.1.0 | Apache-2.0 | 生产 |
| element-plus | 2.14.7 | MIT | 生产 |
| entities | 4.5.0 | BSD-2-Clause | 生产 |
| entities | 7.0.1 | BSD-2-Clause | 生产 |
| es-define-property | 1.0.1 | MIT | 生产 |
| es-errors | 1.3.0 | MIT | 生产 |
| es-module-lexer | 1.7.0 | MIT | 开发 |
| es-object-atoms | 1.1.2 | MIT | 生产 |
| es-set-tostringtag | 2.1.0 | MIT | 生产 |
| esbuild | 0.21.5 | MIT | 开发 |
| escape-string-regexp | 4.0.0 | MIT | 开发 |
| eslint | 9.39.5 | MIT | 开发 |
| eslint-scope | 8.4.0 | BSD-2-Clause | 开发 |
| eslint-visitor-keys | 3.4.3 | Apache-2.0 | 开发 |
| eslint-visitor-keys | 4.2.1 | Apache-2.0 | 开发 |
| espree | 10.4.0 | BSD-2-Clause | 开发 |
| esquery | 1.7.0 | BSD-3-Clause | 开发 |
| esrecurse | 4.3.0 | BSD-2-Clause | 开发 |
| estraverse | 5.3.0 | BSD-2-Clause | 开发 |
| estree-walker | 2.0.2 | MIT | 生产 |
| estree-walker | 3.0.3 | MIT | 开发 |
| esutils | 2.0.3 | BSD-2-Clause | 开发 |
| expect-type | 1.4.0 | Apache-2.0 | 开发 |
| fast-deep-equal | 3.1.3 | MIT | 开发 |
| fast-json-stable-stringify | 2.1.0 | MIT | 开发 |
| fast-levenshtein | 2.0.6 | MIT | 开发 |
| file-entry-cache | 8.0.0 | MIT | 开发 |
| find-up | 5.0.0 | MIT | 开发 |
| flat-cache | 4.0.1 | MIT | 开发 |
| flatted | 3.4.4 | ISC | 开发 |
| follow-redirects | 1.16.1 | MIT | 生产 |
| form-data | 4.0.6 | MIT | 生产 |
| fsevents | 2.3.3 | MIT | 开发 |
| function-bind | 1.1.2 | MIT | 生产 |
| get-intrinsic | 1.3.0 | MIT | 生产 |
| get-proto | 1.0.1 | MIT | 生产 |
| glob-parent | 6.0.2 | ISC | 开发 |
| globals | 14.0.0 | MIT | 开发 |
| gopd | 1.2.0 | MIT | 生产 |
| has-flag | 4.0.0 | MIT | 开发 |
| has-symbols | 1.1.0 | MIT | 生产 |
| has-tostringtag | 1.0.2 | MIT | 生产 |
| hasown | 2.0.4 | MIT | 生产 |
| he | 1.2.0 | MIT | 开发 |
| highlight.js | 11.12.0 | BSD-3-Clause | 生产 |
| https-proxy-agent | 5.0.1 | MIT | 生产 |
| ignore | 5.3.2 | MIT | 开发 |
| import-fresh | 3.3.1 | MIT | 开发 |
| imurmurhash | 0.1.4 | MIT | 开发 |
| is-extglob | 2.1.1 | MIT | 开发 |
| is-glob | 4.0.3 | MIT | 开发 |
| isexe | 2.0.0 | ISC | 开发 |
| js-yaml | 4.3.2 | MIT | 开发 |
| json-buffer | 3.0.1 | MIT | 开发 |
| json-schema-traverse | 0.4.1 | MIT | 开发 |
| json-stable-stringify-without-jsonify | 1.0.1 | MIT | 开发 |
| keyv | 4.5.4 | MIT | 开发 |
| levn | 0.4.1 | MIT | 开发 |
| linkify-it | 5.0.2 | MIT | 生产 |
| locate-path | 6.0.0 | MIT | 开发 |
| lodash | 4.18.1 | MIT | 生产 |
| lodash-es | 4.18.1 | MIT | 生产 |
| lodash-unified | 1.0.3 | MIT | 生产 |
| lodash.merge | 4.6.2 | MIT | 开发 |
| loupe | 3.2.1 | MIT | 开发 |
| magic-string | 0.30.21 | MIT | 生产 |
| markdown-it | 14.3.3 | MIT | 生产 |
| math-intrinsics | 1.1.0 | MIT | 生产 |
| mdurl | 2.1.0 | MIT | 生产 |
| memoize-one | 6.0.0 | MIT | 生产 |
| mime-db | 1.52.0 | MIT | 生产 |
| mime-types | 2.1.35 | MIT | 生产 |
| minimatch | 3.1.5 | ISC | 开发 |
| minimatch | 9.0.9 | ISC | 开发 |
| ms | 2.1.3 | MIT | 生产 |
| muggle-string | 0.4.1 | MIT | 开发 |
| nanoid | 3.3.20 | MIT | 生产 |
| nanoid | 5.1.16 | MIT | 生产 |
| natural-compare | 1.4.0 | MIT | 开发 |
| normalize-wheel-es | 1.2.0 | BSD-3-Clause | 生产 |
| optionator | 0.9.4 | MIT | 开发 |
| p-limit | 3.1.0 | MIT | 开发 |
| p-locate | 5.0.0 | MIT | 开发 |
| parent-module | 1.0.1 | MIT | 开发 |
| path-browserify | 1.0.1 | MIT | 开发 |
| path-exists | 4.0.0 | MIT | 开发 |
| path-key | 3.1.1 | MIT | 开发 |
| pathe | 1.1.2 | MIT | 开发 |
| pathval | 2.0.1 | MIT | 开发 |
| picocolors | 1.1.1 | ISC | 生产 |
| pinia | 2.3.1 | MIT | 生产 |
| playwright | 1.64.1 | Apache-2.0 | 开发 |
| playwright-core | 1.64.1 | Apache-2.0 | 开发 |
| postcss | 8.5.31 | MIT | 生产 |
| prelude-ls | 1.2.1 | MIT | 开发 |
| prettier | 3.9.10 | MIT | 开发 |
| proxy-from-env | 2.1.0 | MIT | 生产 |
| punycode | 2.3.1 | MIT | 开发 |
| punycode.js | 2.3.1 | MIT | 生产 |
| resolve-from | 4.0.0 | MIT | 开发 |
| rollup | 4.64.12 | MIT | 开发 |
| shebang-command | 2.0.0 | MIT | 开发 |
| shebang-regex | 3.0.0 | MIT | 开发 |
| siginfo | 2.0.0 | ISC | 开发 |
| source-map-js | 1.2.2 | BSD-3-Clause | 生产 |
| stackback | 0.0.2 | MIT | 开发 |
| std-env | 3.10.0 | MIT | 开发 |
| strip-json-comments | 3.1.1 | MIT | 开发 |
| supports-color | 7.2.0 | MIT | 开发 |
| tinybench | 2.9.0 | MIT | 开发 |
| tinyexec | 0.3.2 | MIT | 开发 |
| tinypool | 1.1.1 | MIT | 开发 |
| tinyrainbow | 1.2.0 | MIT | 开发 |
| tinyspy | 3.0.2 | MIT | 开发 |
| tslib | 2.3.0 | 0BSD | 生产 |
| type-check | 0.4.0 | MIT | 开发 |
| typescript | 5.9.3 | Apache-2.0 | 开发 |
| uc.micro | 2.1.0 | MIT | 生产 |
| undici-types | 8.9.0 | MIT | 开发 |
| uri-js | 4.4.1 | BSD-2-Clause | 开发 |
| vite | 5.4.21 | MIT | 开发 |
| vite-node | 2.1.9 | MIT | 开发 |
| vitest | 2.1.9 | MIT | 开发 |
| vscode-uri | 3.2.0 | MIT | 开发 |
| vue | 3.5.44 | MIT | 生产 |
| vue-component-type-helpers | 3.3.13 | MIT | 生产 |
| vue-demi | 0.14.10 | MIT | 生产 |
| vue-router | 4.6.4 | MIT | 生产 |
| vue-tsc | 2.2.12 | MIT | 开发 |
| which | 2.0.2 | ISC | 开发 |
| why-is-node-running | 2.3.0 | MIT | 开发 |
| word-wrap | 1.2.5 | MIT | 开发 |
| yocto-queue | 0.1.0 | MIT | 开发 |
| zrender | 6.1.0 | BSD-3-Clause | 生产 |
