# MCP Python SDK & deployment — ground truth for the X32 OSC MCP server

Researched 2026-09-19. Every fact carries a SOURCE line. Anything not verified against a primary source is marked **UNCONFIRMED**.

**Independent verification pass (2026-09-19, second researcher):** every PyPI number was re-pulled from `https://pypi.org/pypi/<pkg>/json`, every SDK signature/line number re-read from `modelcontextprotocol/python-sdk@main`, every emulator claim re-read from `pmaillot/X32-Behringer@master` (`X32.c`, `X32_Command.c`, `Makefile`, `X32port.h`, `README.md`), plus the Claude Code / MCP / uv / python.org / pytest-asyncio pages. Lines tagged `VERIFIED:` were confirmed; `VERIFIED-CORRECTED:` were wrong and have been fixed in place; the collected list is in **§ Verification log** at the end.

Primary sources actually read (raw files downloaded and inspected, not summarised from memory):

- PyPI JSON (`https://pypi.org/pypi/<pkg>/json`) for `mcp`, `mcp-types`, and every transitive dependency listed in §2.
- `modelcontextprotocol/python-sdk` @ `main` (v2 line): `README.md`, `pyproject.toml`, `mkdocs.yml`, `docs/whats-new.md`, `docs/migration.md`, `docs/deprecated.md`, `docs/get-started/{first-steps,real-host,testing}.md`, `docs/servers/{tools,structured-output,resources,prompts,handling-errors}.md`, `docs/handlers/{context,lifespan,progress,logging}.md`, `docs/run/index.md`, `src/mcp/server/{__init__,__main__,fastmcp,context}.py`, `src/mcp/server/mcpserver/{__init__,server,context}.py`, and 46 `docs_src/**/tutorial*.py` example files.
- `pmaillot/X32-Behringer` @ `master`: `README.md`, `X32.c` (the emulator), `Makefile`, `X32port.h`; Maillot's site `https://sites.google.com/site/patrickmaillot/x32`; Google Drive file pages for the emulator zip and the OSC PDF.
- `code.claude.com/docs/en/mcp`, `modelcontextprotocol.io/docs/develop/connect-local-servers`, `docs.astral.sh/uv/...`, `docs.python.org/3/using/windows.html`, `python.org/downloads/windows/`, `devguide.python.org/versions/`, `pytest-asyncio.readthedocs.io` (configuration + changelog), `termux/termux-packages` build scripts, `aio-libs/aiohttp` + `python-websockets/websockets` `setup.py`, `Eutalix/android-pydantic-core` README.

---

## 0. Decisions in one screen

| Topic | Decision | Why (see section) |
|---|---|---|
| SDK | `mcp>=2.2,<3` (latest **2.2.0**, released 2026-09-07). Python **>=3.10**. | §1.1 |
| Import | `from mcp.server import MCPServer` and `from mcp.server.mcpserver import Context`. **`mcp.server.fastmcp` no longer exists** (raises `ModuleNotFoundError` on purpose). | §1.2 |
| Errors | `raise ToolError("...")` (model sees message). Never `return` an error string. | §1.4.6 |
| Logging | `logging.getLogger(__name__)` to stderr. **`ctx.info()` is deprecated** (SEP-2577) and emits `MCPDeprecationWarning` (a `UserWarning`). | §1.7 |
| Lifespan | `@asynccontextmanager`, passed as `MCPServer(..., lifespan=...)`; runs **once**; `mcp.run()` enters `anyio.run(...)` on the **asyncio** backend, so `asyncio.create_task()` inside the lifespan is valid. | §1.8 |
| Run | `mcp.run()` (default transport `stdio`) under `if __name__ == "__main__":`; `python -m x32mcp` via `x32mcp/__main__.py`; console script via `[project.scripts]`. | §1.9 |
| Termux | Native Termux **cannot** be pure-Python: `pydantic-core` (Rust) is mandatory, plus `rpds-py` (Rust) and `cryptography` (via `pyjwt[crypto]`). Use `pkg install python-rpds-py python-cryptography rust` + source build of pydantic-core, or run under `proot-distro` (glibc, manylinux aarch64 wheels just work). | §2 |
| Dashboard WS | `websockets` (pure-Python wheel `py3-none-any`, optional C speedup, **requires Python >=3.11** from 17.0). | §2.4 |
| Host config | `%APPDATA%\Claude\claude_desktop_config.json` (`command`/`args`/`env`, absolute `.exe` path, doubled backslashes); Claude Code `.mcp.json` with `"type": "stdio"`; `claude mcp add --transport stdio --scope project x32 -- <cmd>`. | §3 |
| Tests | `pytest 9.1.1` + `pytest-asyncio 1.4.0`, `asyncio_mode = "auto"` in `[tool.pytest.ini_options]`; or the SDK's own `@pytest.mark.anyio` pattern with `Client(mcp)`. | §4 |
| Python on Windows | `uv` (no admin): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then `uv python install 3.12`. python.org's last 3.12 **installer** is 3.12.10 (2025-04-08); 3.12 is security-only until 2028-10. Beware the 0-byte Store stub `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`. | §5 |
| Emulator | Maillot `X32_windows.zip` (Google Drive), binary `X32` v0.88 source; **UDP port hard-coded 10023**, `-i <ip>` to pick the bind address, no `-p`; persists to `.X32res.rc` on `/shutdown` (or `/-stat/lock ,i 2`); meters are fake zero-filled blobs; **`/batchsubscribe`, `/formatsubscribe`, `/renew` are NOT implemented** (no reply); no scenes/snippets. | §6 |

---

## 1. The official Python SDK (`mcp`)

### 1.1 Version, Python floor, dependencies

- **Latest release: `mcp` 2.2.0**, uploaded `2026-09-07T16:06:19`, wheel `mcp-2.2.0-py3-none-any.whl` (pure Python) + sdist.
- **`requires_python = ">=3.10"`**. Classifiers list 3.10, 3.11, 3.12, 3.13, 3.14.
- Release train (last 15 on PyPI): `1.28.1, 1.29.0, 1.29.1, 1.30.0, 2.0.0a1, 2.0.0a2, 2.0.0a3, 2.0.0b1, 2.0.0b2, 2.0.0rc1, 2.0.0, 2.0.1, 2.1.0, 2.1.1, 2.2.0`. **`1.30.0` is the newest 1.x** (the `v1.x` branch continues to receive security fixes and is documented at `https://py.sdk.modelcontextprotocol.io/v1/`).
- `requires_dist` of 2.2.0, verbatim from PyPI:

```
anyio>=4.10; python_version >= "3.14"
anyio>=4.9; python_version < "3.14"
httpx2>=2.5.0
jsonschema>=4.20.0
mcp-types==2.2.0
opentelemetry-api>=1.28.0
pydantic>=2.12.0
pyjwt[crypto]>=2.10.1
python-multipart>=0.0.9
pywin32>=311; sys_platform == "win32"
sse-starlette>=3.0.0
starlette>=0.27; python_version < "3.14"
starlette>=0.48.0; python_version >= "3.14"
typing-extensions>=4.13.0
typing-inspection>=0.4.1
uvicorn>=0.31.1; sys_platform != "emscripten"
python-dotenv>=1.0.0; extra == "cli"
typer>=0.16.0; extra == "cli"
rich>=13.9.4; extra == "rich"
```

- VERIFIED: PyPI JSON re-pulled 2026-09-19 — `info.version` = `2.2.0`, `requires_python` = `>=3.10`, wheel `mcp-2.2.0-py3-none-any.whl` uploaded `2026-09-07`, `requires_dist` byte-for-byte as listed above; `mcp-types` 2.2.0 depends only on `pydantic>=2.12.0`, `typing-extensions>=4.13.0`; classifiers 3.10–3.14; README line 39 "Python 3.10+."; `pyproject.toml` line 6 `requires-python = ">=3.10"`.
- Install: `uv add "mcp[cli]"` or `pip install "mcp[cli]"`. The `cli` extra adds the `mcp` command (`mcp dev`, `mcp run`, `mcp install`, `mcp version`); plain `mcp` is enough for a server that is launched with `python -m x32mcp`. Note (added by verifier): the `cli` extra's `typer>=0.16.0` depends on **`rich>=13.8.0`** (PyPI `typer` 0.27.2 `requires_dist`), and `rich` changes the SDK's default log handler — see §1.7.
- Pin guidance from the README: "`pip install mcp` now installs 2.x, keep a `<2` upper bound ... until you've migrated" — conversely, for a **new** v2 project use `mcp>=2,<3` (the migration guide's own example).
- `[project.scripts] mcp = "mcp.cli:app [cli]"` — the SDK's own console-script definition (useful as a template).

SOURCE: `https://pypi.org/pypi/mcp/json` (fields `info.version`, `info.requires_python`, `info.requires_dist`, `releases`); `python-sdk/README.md` ("Requirements: Python 3.10+", "Installation"); `python-sdk/pyproject.toml` lines 1–30 and `[project.scripts]`; `docs/migration.md` § "Dependency floors raised and new required dependencies" (table: anyio `>=4.9`/`>=4.10`, pydantic `>=2.12`, sse-starlette `>=3.0.0`, typing-extensions `>=4.13.0`, pywin32 `>=311`, opentelemetry-api new `>=1.28.0`, mcp-types exact-pinned, httpx/httpx-sse removed, httpx2 new `>=2.5.0`, `ws` extra removed).

### 1.2 `FastMCP` → `MCPServer` (the import path moved; the old one is gone)

Verbatim from `docs/whats-new.md`:

> The high-level server class was renamed, and its module with it. This is the first thing every v1 server hits, because the old import path is gone rather than deprecated:
> ```python
> from mcp.server import MCPServer  # v1: from mcp.server.fastmcp import FastMCP
> mcp = MCPServer("Demo")  # v1: FastMCP("Demo")
> ```
> ... everything under `mcp.server.fastmcp.*` now lives under `mcp.server.mcpserver.*`, `ctx.fastmcp` is `ctx.mcp_server`, `get_context()` is gone (declare a `ctx: Context` parameter instead), and the exception base `FastMCPError` is `MCPServerError`.

The shim `src/mcp/server/fastmcp.py` (16 lines) is **only** an error message:

```python
"""Removed in mcp 2: `FastMCP` is now `mcp.server.mcpserver.MCPServer`. ..."""
_MESSAGE = (
    "No module named 'mcp.server.fastmcp'. This is mcp 2.x, where FastMCP was renamed to MCPServer "
    "(from mcp.server.mcpserver import MCPServer) and other APIs changed; see the migration guide at "
    "https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver "
    "or pin 'mcp<2' to keep running v1 code."
)
raise ModuleNotFoundError(_MESSAGE, name=__name__)
```

Public import table (v2):

| Symbol | Import |
|---|---|
| `MCPServer` | `from mcp.server import MCPServer` (also `from mcp.server.mcpserver import MCPServer`). **There is no `from mcp import MCPServer`.** |
| `Context` | `from mcp.server.mcpserver import Context` |
| `ToolError`, `ResourceError`, `ResourceNotFoundError`, `MCPServerError` | `from mcp.server.mcpserver.exceptions import ...` |
| `MCPError` (protocol error; v1 `McpError`) | `from mcp import MCPError` |
| `MCPDeprecationWarning` | `from mcp import MCPDeprecationWarning` |
| `Image`, `Audio` | `from mcp.server.mcpserver import Image, Audio` |
| `Message`, `UserMessage`, `AssistantMessage` | `from mcp.server.mcpserver.prompts.base import ...` (also re-exported from `mcp.server.mcpserver`) |
| `Prompt` (for `add_prompt`) | `from mcp.server.mcpserver.prompts import Prompt` |
| `ToolAnnotations`, `INVALID_PARAMS`, `TextContent`, `CallToolResult`, ... | `from mcp.types import ...` (permanent alias of the standalone `mcp_types` package; attributes are **snake_case**: `is_error`, `input_schema`, `mime_type`) |
| `Client` (tests) | `from mcp import Client` |

`src/mcp/server/__init__.py` (7 lines, verbatim):

```python
from .caching import CacheHint
from .context import ServerRequestContext
from .lowlevel import NotificationOptions, Server
from .mcpserver import MCPServer
from .models import InitializationOptions
__all__ = ["CacheHint", "Server", "ServerRequestContext", "MCPServer", "NotificationOptions", "InitializationOptions"]
```

VERIFIED: `src/mcp/server/fastmcp.py` (16 lines, `raise ModuleNotFoundError(_MESSAGE, name=__name__)`), `src/mcp/server/__init__.py` (7 lines, exactly as quoted), `src/mcp/server/mcpserver/__init__.py` `__all__` (exports `MCPServer`, `Context`, `Image`, `Audio`, `Message`, `UserMessage`, `AssistantMessage`, `Icon`, `Resolve`, `Elicit`, `Sample`, `ListRoots`, ...), `src/mcp/__init__.py` lines 65/71 (`from .client.client import Client`; `from .shared.exceptions import MCPDeprecationWarning, MCPError, UrlElicitationRequiredError`), `src/mcp/server/mcpserver/exceptions.py` (`MCPServerError`, `ResourceError`, `ResourceNotFoundError`, `UnexpectedResourceError`, `ToolError`, `UnexpectedToolError`, `InvalidSignature`), `docs/whats-new.md` lines 14–22 (quote verbatim), `docs/get-started/first-steps.md` line 51.

SOURCE: `docs/whats-new.md` § "`FastMCP` is now `MCPServer`"; `docs/migration.md` § "`FastMCP` renamed to `MCPServer`" (import list: `Image`/`Audio` from `mcp.server.mcpserver`; `Message`/`UserMessage`/`AssistantMessage` from `mcp.server.mcpserver.prompts.base`; `ToolError`/`ResourceError` and `MCPServerError` from `mcp.server.mcpserver.exceptions`); `docs/get-started/first-steps.md` tip ("There is no `from mcp import MCPServer`"); `src/mcp/server/fastmcp.py`; `src/mcp/server/__init__.py`; `src/mcp/server/mcpserver/__init__.py` `__all__`.

### 1.3 Creating a server — constructor signature (verbatim, `src/mcp/server/mcpserver/server.py` lines 158–186)

```python
class MCPServer(Generic[LifespanResultT]):
    def __init__(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        website_url: str | None = None,
        icons: list[Icon] | None = None,
        version: str = "",
        auth_server_provider: OAuthAuthorizationServerProvider[Any, Any, Any] | None = None,
        token_verifier: TokenVerifier | None = None,
        *,
        tools: list[Tool] | None = None,
        resources: list[Resource] | None = None,
        extensions: Sequence[Extension] | None = None,
        debug: bool = False,
        log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO",
        warn_on_duplicate_resources: bool = True,
        warn_on_duplicate_tools: bool = True,
        warn_on_duplicate_prompts: bool = True,
        dependencies: list[str] | None = None,
        lifespan: Callable[[MCPServer[LifespanResultT]], AbstractAsyncContextManager[LifespanResultT]] | None = None,
        auth: AuthSettings | None = None,
        resource_security: ResourceSecurity = DEFAULT_RESOURCE_SECURITY,
        request_state_security: RequestStateSecurity | None = None,
        cache_hints: Mapping[CacheableMethod, CacheHint] | None = None,
        subscriptions: SubscriptionBus | None = None,
        middleware: Sequence[ServerMiddleware[Any]] | None = None,
    ):
```

Gotchas (all from `docs/migration.md`):

- **Positional order changed**: `name, title, description, instructions, ...`. `MCPServer("Demo", "text")` silently puts the text in `title`. Always `MCPServer("x32mcp", instructions="...")` — keep only `name` positional.
- Default name is now `"mcp-server"` (was `"FastMCP"`); it is what clients show as `serverInfo.name`.
- `version` defaults to `""` (v1 reported the SDK version). Pass `version="0.1.0"`.
- **Transport options (`host`, `port`, `streamable_http_path`, `json_response`, `stateless_http`, ...) are NOT constructor args**; `MCPServer("x", port=9000)` → `TypeError: MCPServer.__init__() got an unexpected keyword argument 'port'`. They belong on `run()` (§1.9).
- `MCP_*` environment variables / `.env` are **not** read anymore (`Settings` is a plain pydantic model; `pydantic-settings` is no longer a dependency). Read env yourself and pass values in.
- `log_level=` is handed to `logging.basicConfig()` when the object is constructed (root logger, stderr handler) — see §1.7.

VERIFIED: `server.py` line 157 `class MCPServer(Generic[LifespanResultT]):`, line 158 `def __init__(`, parameter list identical to the block above; line 263 `configure_logging(self.settings.log_level)`; `name or "mcp-server"` passed to the lowlevel `Server`; `docs/migration.md` headings at lines 689, 711, 736, 744, 750, 834 match the gotchas listed.

SOURCE: `server.py` lines 158–217; `docs/migration.md` §§ "`MCPServer` constructor: `title`, `description`, and `version` added to the positional parameters", "Default server name changed from `FastMCP` to `mcp-server`", "Unversioned servers report an empty version", "Transport-specific parameters moved from MCPServer constructor to run()/app methods", "`MCP_*` environment variables and `.env` files are no longer read"; `docs/run/index.md` § "Server settings".

### 1.4 `@mcp.tool()`

Decorator signature (verbatim, `server.py` lines 660–669):

```python
def tool(
    self,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    annotations: ToolAnnotations | None = None,
    icons: list[Icon] | None = None,
    meta: dict[str, Any] | None = None,
    structured_output: bool | None = None,
) -> Callable[[_CallableT], _CallableT]:
```

Programmatic equivalent: `mcp.add_tool(fn, name=None, title=None, description=None, annotations=None, icons=None, meta=None, structured_output=None) -> None` (lines 609–619) and `mcp.remove_tool(name)`.

#### 1.4.1 Name / description / schema derivation

- **name** = function name; **description** = docstring; **input schema** = JSON Schema (2020-12, generated by Pydantic) from the type hints. `docs_src/tools/tutorial001.py`:

```python
@mcp.tool()
def search_books(query: str, limit: int) -> str:
    """Search the catalog by title or author."""
    return f"Found 3 books matching {query!r} (showing up to {limit})."
```

→ advertised in `tools/list` as

```json
{"type": "object",
 "properties": {"query": {"title": "Query", "type": "string"}, "limit": {"title": "Limit", "type": "integer"}},
 "required": ["query", "limit"], "title": "search_booksArguments"}
```

- `name=` / `description=` on the decorator override the derived values.

#### 1.4.2 Defaults → optional; `Annotated[..., Field(...)]`; `Literal`; `Optional`

- `limit: int = 10` removes `limit` from `required` and adds `"default": 10`.
- `docs_src/tools/tutorial003.py` (verbatim):

```python
from typing import Annotated, Literal
from pydantic import Field

@mcp.tool()
def search_books(
    query: Annotated[str, Field(description="Title or author to search for.")],
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum number of results.")] = 10,
    genre: Literal["fiction", "non-fiction", "poetry"] | None = None,
) -> str:
```

`Field(ge=1, le=50)` lands as `"minimum": 1, "maximum": 50`; `Literal[...]` becomes an enum; `X | None = None` is an optional nullable argument. Constraint violations are rejected **before the function runs** and returned to the model as a tool error (`Input should be less than or equal to 50`), so do not re-validate your own hints.

- A **Pydantic `BaseModel` parameter** (`def add_book(book: Book)`) is nested as a `$defs` reference and arrives as a validated instance.

#### 1.4.3 Sync vs async

> "If a tool does I/O ... declare it `async def` and `await` inside it. The SDK awaits it. A plain `def` tool works too: the SDK runs it in a thread so it never blocks the server."

v2 behaviour change: sync handlers run via `anyio.to_thread.run_sync()` on a **worker thread** — `asyncio.get_running_loop()` inside a sync tool raises `RuntimeError`, and sync tools may run concurrently. For the X32 server (asyncio OSC client, shared state) **make every tool `async def`** so it stays on the event-loop thread.

#### 1.4.4 Return value → `content` + `structured_content` ("Structured Output")

> "the return type annotation is the output schema."

| Return annotation | `output_schema` | `structured_content` | `content` (what the model reads) |
|---|---|---|---|
| `str`, `int`, `float`, `bool`, `bytes`, `None` (scalars) | wrapped: `{"properties": {"result": {...}}, "required": ["result"], ...}` | `{"result": <value>}` | `TextContent(text=str(value))` (e.g. `"17"`) |
| `BaseModel` | the model's schema, no wrapper | the object field-for-field | the same object serialised as JSON text |
| `TypedDict` / `@dataclass` / any class with annotated body | same as BaseModel | dict | JSON text |
| `dict[str, X]` | `{"additionalProperties": {...}, "type": "object"}`, not wrapped | the dict | JSON text |
| `list[X]`, `tuple`, unions, `Optional[...]` | wrapped in `{"result": ...}` with `$defs` | `{"result": [...]}` | **one `TextContent` per item** for lists |
| `TextContent` / `Image` / `Audio` / `EmbeddedResource` (alone, in a list/tuple/Sequence, or in a union) | none (opted out automatically) | `None` | the blocks as given |
| `CallToolResult` | passed through untouched (incl. `_meta`) | as built | as built |
| class **without** annotations on its body | **silently none** — model sees the object's `repr` | `None` | `"<server.Station object at 0x...>"` |

- Returning a plain `dict` from a `-> WeatherData` tool is fine: validation is on the value. Whatever you return is **validated against the output schema before it leaves**; a mismatch is an `is_error=True` result `Error executing tool <name>` with the `ValidationError` in the server log at `ERROR`.
- `@mcp.tool(structured_output=False)` → text-only (no schema, no wrapping). `structured_output=True` makes an unserialisable return type an **import-time** error.
- Worked example (from the docs): `def get_temperature(city: str) -> int` returning `17` → `result.content == [TextContent(text="17")]`, `result.structured_content == {"result": 17}`, and `output_schema == {"properties": {"result": {"title": "Result", "type": "integer"}}, "required": ["result"], "title": "get_temperatureOutput", "type": "object"}`.

#### 1.4.5 Names, titles, annotations

```python
from mcp.types import ToolAnnotations
@mcp.tool(title="Search the catalog", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def search_books(query: str) -> str: ...
```

Hints: `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint` (snake_case in Python, camelCase on the wire). They are hints, not security.

#### 1.4.6 Raising errors from a tool (three paths)

| You raise | Model sees | Wire | Server log |
|---|---|---|---|
| `ToolError("No book titled 'X'")` (`mcp.server.mcpserver.exceptions`) | `is_error=True`, `content=[TextContent(text="Error executing tool get_author: No book titled 'X' ...")]`, `structured_content=None` | normal `tools/call` result | one `INFO` line, no traceback |
| `MCPError(code=INVALID_PARAMS, message="...")` (`from mcp import MCPError`; codes from `mcp.types`) | nothing — the **request itself fails** | JSON-RPC error `{"code": -32602, "message": "..."}` (code/message/data forwarded verbatim; **v1 turned this into `isError=True`**, v2 does not) | — |
| any other exception (`KeyError`, `ValidationError`, ...) | `is_error=True`, `content=[TextContent(text="Error executing tool get_author")]` — **message withheld** | normal result | `ERROR` with traceback: `Tool 'get_author' raised an unexpected exception` |

Rule from the docs: "could a smarter model have avoided this? Yes → `ToolError`. No → `MCPError`." And: "Never `return` an error message from a tool. A returned string has `is_error=False`."

VERIFIED: `server.py` lines 609–619 (`add_tool`), 649 (`remove_tool`), 660–669 (`tool`) — signatures identical; `docs_src/tools/tutorial003.py` identical to the quoted snippet; `docs/servers/tools.md` line 31 (`search_booksArguments`), 105 (`"minimum": 1, "maximum": 50`), 113 (`Input should be less than or equal to 50`), 139 ("the SDK runs it in a thread"); `docs/servers/structured-output.md` lines 22–34 (`get_temperatureOutput`, `{"result": 17}`), 117–146 (list wrapper, "two `TextContent` blocks, one per item"), 160–170 (`additionalProperties`, `dict[int, float]` falls back to wrapper), 210 (`structured_output=False`), 229–230 (silent `repr` for unannotated classes), 247; `docs/servers/handling-errors.md` lines 23, 38, 54–61 (`INVALID_PARAMS` is `-32602`), 80, 101–106, 118–128 (`ResourceNotFoundError` → `-32602`, `ResourceError` → `-32603`); `docs/migration.md` headings "Sync handler functions now run on a worker thread" (line 909), "`MCPError` raised from an `@mcp.tool()` handler now surfaces as a JSON-RPC error" (line 984).

SOURCE: `docs/servers/tools.md` (all subsections), `docs/servers/structured-output.md` (all subsections, the JSON schema examples are copied verbatim), `docs/servers/handling-errors.md`, `docs/migration.md` §§ "Sync handler functions now run on a worker thread", "`MCPError` raised from an `@mcp.tool()` handler now surfaces as a JSON-RPC error", "What is unchanged on `MCPServer`" (tool return handling rules); `docs_src/tools/tutorial00{1..5}.py`, `docs_src/structured_output/tutorial00{1..9}.py`, `docs_src/handling_errors/tutorial00{1..4}.py`; `server.py` lines 609–669.

### 1.5 `@mcp.resource(uri)` — static and templated

Signature (verbatim, `server.py` lines 779–791):

```python
def resource(
    self,
    uri: str,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    mime_type: str | None = None,
    icons: list[Icon] | None = None,
    annotations: Annotations | None = None,
    meta: dict[str, Any] | None = None,
    security: ResourceSecurity | None = None,
) -> Callable[[_CallableT], _CallableT]:
```

- **Static**: `@mcp.resource("config://app") def get_config() -> str` → listed in `resources/list` as `{"name": "get_config", "uri": "config://app", "description": "<docstring>", "mimeType": "text/plain"}`; read result `TextResourceContents(uri="config://app", mime_type="text/plain", text=...)`. The function runs on `resources/read` only, never on list.
- **Template**: `@mcp.resource("users://{user_id}/profile") def get_user_profile(user_id: str) -> str` → listed under `resources/templates/list` as `{"uriTemplate": "users://{user_id}/profile", ...}`; the concrete URI the client asked for is echoed in the result. Placeholder names **must equal** parameter names or the decorator raises at import time: `ValueError: Mismatch between URI parameters {'user_id'} and function parameters {'user'}`. Templates are **real RFC 6570** in v2 (`{+path}`, `{?q,lang}`), matching is exact, and path traversal in extracted values is rejected by default (the `security=` kwarg is new in v2).
- **Return types**: `str` → text as-is; `bytes` → `BlobResourceContents` (base64 in `blob`); anything else JSON-serialisable (dict, list, model, dataclass) → JSON text. `mime_type` defaults to `"text/plain"` and is **never guessed** — label JSON resources `mime_type="application/json"`.
- A resource function may take a bare `ctx: Context` parameter (not `Context[T]`, see §1.8).
- Missing resource: `raise ResourceNotFoundError("...")` → protocol error `-32602` with `{"uri": "..."}` in `data` (SEP-2164). `ResourceError` → `-32603` with your message. Resources have **no** `is_error` half-result.
- URI type is now `str` (v1 used `AnyUrl`), so custom schemes like `x32://ch/01` are fine.
- Ready-made classes: `TextResource`, `BinaryResource`, `FileResource`, `HttpResource`, `DirectoryResource` in `mcp.server.mcpserver.resources`, registered with `mcp.add_resource(...)`.

VERIFIED: `server.py` lines 771 (`add_resource`), 779–791 (`resource` signature identical); `docs/servers/resources.md` lines 30/72 (`"mimeType": "text/plain"`), 90 (`ValueError: Mismatch between URI parameters {'user_id'} and function parameters {'user'}`), 95 (RFC 6570, `{+path}`, `{?q,lang}`), 117 (`bytes` → `BlobResourceContents`), 121 (`mime_type` "defaults to `text/plain`. The SDK never inspects what you return to guess it"); `docs/migration.md` headings at lines 368 (URI `AnyUrl` → `str`), 1019 (SEP-2164), 1056 (template matching).

SOURCE: `docs/servers/resources.md`; `docs/servers/handling-errors.md` § "A resource that doesn't exist"; `docs/whats-new.md` ("URI templates are real RFC 6570 now"); `docs/migration.md` §§ "Resource URI type changed from `AnyUrl` to `str`", "Resource not found returns `-32602` ..."; `docs_src/resources/tutorial00{1,2,3}.py`, `docs_src/handling_errors/tutorial003.py`; `server.py` lines 779–791.

### 1.6 `@mcp.prompt()`

Signature (verbatim, `server.py` lines 959–965): `def prompt(self, name=None, title=None, description=None, icons=None)`.

- Return `str` → **one user message**; return `list[Message]` of `UserMessage(...)` / `AssistantMessage(...)` (each accepts `str`, a content block, an `EmbeddedResource`, or `Image`/`Audio`) to seed a multi-turn conversation.
- Arguments are a flat list of **named strings** (no JSON schema): `{"name": "code", "description": "...", "required": true}`; `Annotated[str, Field(description=...)]` supplies the description; a default makes it optional.
- A missing required argument fails the whole request with JSON-RPC `-32603` ("Internal server error"; the reason `Missing required arguments: {'code'}` is in the server log).
- Runtime changes: `mcp.add_prompt(Prompt.from_function(fn, name=..., description=...))`, `mcp.remove_prompt(name)`, then `await ctx.notify_prompts_changed()` (2026-era listeners) **and** `await ctx.session.send_prompt_list_changed()` (legacy client).

VERIFIED: `server.py` lines 940 (`add_prompt`), 948 (`remove_prompt`), 959–965 (`prompt(self, name=None, title=None, description=None, icons=None)`); `docs/servers/prompts.md` lines 59–66 (`-32603`, `Missing required arguments: {'code'}`), 182–183 (`Prompt.from_function`, `notify_prompts_changed` + `send_prompt_list_changed`, "Call both"), and the return-type rules (str → one user message; `UserMessage`/`AssistantMessage` accept `str`, a content block, `Image`, `Audio`, `EmbeddedResource`; "There is no JSON Schema here. Prompt arguments are a flat list of named string values.").

SOURCE: `docs/servers/prompts.md`; `docs_src/prompts/tutorial00{1..6}.py`; `server.py` lines 940–965.

### 1.7 `Context` injection, progress, logging, notifications

Import: `from mcp.server.mcpserver import Context`. Add a parameter **annotated** `Context` to any tool/resource/prompt; the name is irrelevant, it never appears in the input schema, and there is **no ambient `get_context()`** any more (removed in v2) — pass `ctx` down to helpers explicitly.

High-level `Context` API (index of `src/mcp/server/mcpserver/context.py`, 384 lines; `class Context(BaseModel, Generic[LifespanContextT, RequestT])`):

| Member | Notes |
|---|---|
| `ctx.request_id -> str` | id of the current request |
| `ctx.request_context -> ServerRequestContext` | raw record; `ctx.request_context.lifespan_context` is what the lifespan yielded; `.session`, `.meta`, `.protocol_version` also live there. Raises `ValueError("Context is not available outside of a request")` when there is no request. |
| `ctx.session` | `ServerSession`: `await ctx.session.send_tool_list_changed()`, `send_resource_list_changed()`, `send_prompt_list_changed()`, `send_resource_updated(uri)`, `send_ping()`, `report_progress(...)`, `list_roots()` (deprecated), `create_message()` (deprecated) |
| `await ctx.report_progress(progress: float, total: float | None = None, message: str | None = None) -> None` | absolute value (not a delta), must increase; **no-op if the caller sent no progress token** — report unconditionally. Body (line 121): `await self.request_context.session.report_progress(progress, total, message)` |
| `await ctx.read_resource(uri) -> Iterable[ReadResourceContents]` | read your own resource; items have `.content` and `.mime_type`; raises `ResourceNotFoundError` / `ResourceError` |
| `await ctx.notify_tools_changed()`, `notify_prompts_changed()`, `notify_resources_changed()`, `notify_resource_updated(uri)` | publish to every 2026-07-28 `subscriptions/listen` stream (the `session.send_*` calls only reach a legacy client) |
| `await ctx.elicit(message, schema)`, `await ctx.elicit_url(...)` | legacy-only push elicitation (raises `NoBackChannelError` on a 2026 connection); prefer `Resolve(...)` parameters |
| `ctx.headers -> Mapping[str, str] | None` | `None` on stdio |
| `ctx.protocol_version`, `ctx.client_capabilities`, `ctx.input_responses`, `ctx.request_state` | per-request metadata |
| `ctx.mcp_server -> MCPServer` | v1 `ctx.fastmcp` |
| `await ctx.log(level, data, logger=None)`, `await ctx.debug(data)`, `ctx.info(data)`, `ctx.warning(data)`, `ctx.error(data)` | **deprecated** (`@deprecated("The logging capability is deprecated as of 2026-07-28 (SEP-2577).", category=MCPDeprecationWarning)`); parameter renamed `message` → `data`, `extra=` removed; still works on 2025-era sessions, warns every call |
| `ctx.close_sse_stream()`, `ctx.close_standalone_sse_stream()` | HTTP only |

Logging, verbatim from `docs/handlers/logging.md`:

> "Log from a tool the way you log from any other Python function: with the standard library. ... The 2026-07-28 revision of the spec **deprecates that capability and does not replace it**"
> "For a stdio server ... The host launched your server as a subprocess and is reading MCP messages from its **stdout**. Standard error is yours."
> "Don't `print()` in a stdio server. `print` writes to **stdout**, and stdout belongs to the protocol. While serving, the SDK diverts stdout that is actually *flushed* to stderr, so it can't corrupt the wire, but a `print()` in a block-buffered process usually sits unflushed ... until the interpreter drains it at exit, straight onto the protocol stream."
> "You don't have to call `logging.basicConfig()` yourself. Constructing an `MCPServer` already did, with a handler pointed at standard error, at the level you pass as `log_level=` ... The default is `"INFO"`. `logging.basicConfig()` never replaces handlers that already exist. If you configure logging yourself before creating the server, your configuration wins."

Deprecation mechanics: `MCPDeprecationWarning` subclasses `UserWarning` (prints by default, no `-W` flag needed). Silence: `warnings.filterwarnings("ignore", category=MCPDeprecationWarning)`. Turn into test failures: `filterwarnings = ["error::mcp.MCPDeprecationWarning"]` in pytest config.

**What `MCPServer()` actually installs as the log handler** (added by verifier; verbatim `src/mcp/server/mcpserver/utilities/logging.py`):

```python
def configure_logging(level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO") -> None:
    handlers: list[logging.Handler] = []
    try:
        from rich.console import Console
        from rich.logging import RichHandler
        handlers.append(RichHandler(console=Console(stderr=True), rich_tracebacks=True))
    except ImportError:  # pragma: no cover
        pass
    if not handlers:  # pragma: no cover
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=level, format="%(message)s", handlers=handlers)
```

So with `mcp[cli]` installed (typer → rich) you get a `RichHandler` on stderr (colour is auto-disabled when stderr is not a TTY, e.g. under Claude Desktop, but the layout is Rich's: time/level columns, wrapped lines); with plain `mcp` you get a bare `logging.StreamHandler()` (stderr by default) with format `%(message)s` — **no logger name, no level, no timestamp**. If you want a conventional format in `mcp-server-x32.log`, call `logging.basicConfig(format=..., stream=sys.stderr)` yourself *before* constructing `MCPServer` (basicConfig never replaces existing handlers).

VERIFIED: `src/mcp/server/mcpserver/context.py` — `class Context(BaseModel, Generic[LifespanContextT, RequestT])` line 32; `report_progress` line 113 with body `await self.request_context.session.report_progress(progress, total, message)` at line 121; `@deprecated("The logging capability is deprecated as of 2026-07-28 (SEP-2577).", category=MCPDeprecationWarning)` on `log` (257), `debug` (366), `info` (371), `warning` (376), `error` (381), each `(self, data: Any, *, logger_name: str | None = None)`; `notify_tools_changed` 129, `notify_prompts_changed` 133, `notify_resources_changed` 137, `notify_resource_updated` 141, `read_resource` 151, `elicit` 189, `elicit_url` 222, `headers` 282, `request_id` 292, `protocol_version` 297, `input_responses` 302, `request_state` 311, `client_capabilities` 319, `session` 329, `close_sse_stream` 333, `close_standalone_sse_stream` 350; `"Context is not available outside of a request"` at lines 91/98/126. `src/mcp/server/context.py` `class ServerRequestContext` line 31 with fields `session`, `lifespan_context`, `protocol_version`, `meta`. `src/mcp/server/session.py`: `send_resource_updated` 173, `create_message` 183+, `list_roots` 317, `send_ping` 419, `report_progress` 426, `send_progress_notification` 436, `send_resource_list_changed` 457, `send_tool_list_changed` 461, `send_prompt_list_changed` 465. `docs/handlers/logging.md` lines 5, 31, 36–38, 46, 50 (quotes verbatim); `docs/deprecated.md` lines 35, 113, 120, 146–148; `docs/handlers/progress.md` line 90 ("`report_progress` is a **no-op when the caller didn't ask").

SOURCE: `docs/handlers/context.md`, `docs/handlers/progress.md`, `docs/handlers/logging.md`, `docs/deprecated.md` (table + "Silencing the warning"), `docs/migration.md` §§ "`MCPServer.get_context()` removed", "`MCPServer`'s `Context` logging: `message` renamed to `data`, `extra` removed", "`ProgressContext` and `progress()` context manager removed"; `src/mcp/server/mcpserver/context.py` lines 32–382 (method index printed above); `docs_src/context/tutorial00{1,2,3}.py`, `docs_src/progress/tutorial00{1,2}.py`, `docs_src/logging/tutorial001.py`.

### 1.8 Lifespan (startup hook) and a background asyncio task

Contract, verbatim from `docs/handlers/lifespan.md`:

> "A lifespan is an `@asynccontextmanager` that receives the server and `yield`s **one object**. Whatever you yield is available to every handler for as long as the server runs."
> "The lifespan runs **once**. It is entered when the server starts (before the first request) and exited when the server stops."
> "There is always a lifespan. If you don't pass one, the SDK's default yields an empty `dict`, so `ctx.request_context.lifespan_context` is `{}`, never `None`."
> "`Context[AppContext]` is a **tool-only** spelling. Put it on an `@mcp.resource()` or `@mcp.prompt()` function and every call to that handler fails ... `Context is not available outside of a request`. In resources and prompts, write the bare `ctx: Context`."

Streamable-HTTP note (irrelevant for stdio but recorded): in v2 the lifespan is entered **once at manager startup** (v1: once per session / per request under `stateless_http=True`). "Lifespans that set up process-wide state (connection pools, caches, background tasks) are unaffected."

Which event loop? `MCPServer.run()` (verbatim, `server.py` lines 415–421):

```python
match transport:
    case "stdio":
        anyio.run(self.run_stdio_async)
    case "sse":
        anyio.run(lambda: self.run_sse_async(**kwargs))
    case "streamable-http":
        anyio.run(lambda: self.run_streamable_http_async(**kwargs))
```

`anyio.run()` with no `backend=` argument uses anyio's default backend, **asyncio** (`anyio.run(func, *args, backend="asyncio", ...)` — anyio API; the SDK passes no backend). VERIFIED: `agronholm/anyio@master` `src/anyio/_core/_eventloop.py` line 38–43: `def run(func, *args, backend: str = "asyncio", backend_options: dict[str, Any] | None = None)`; `server.py` lines 416–421 (`case "stdio": anyio.run(self.run_stdio_async)`), 1065–1072 (`run_stdio_async` body as quoted); `src/mcp/server/__main__.py` line 24 `anyio.run(main, backend="trio")` is the low-level demo only. `docs/handlers/lifespan.md` lines 22, 55, 68, 97, 100 — quotes verbatim; `docs_src/lifespan/tutorial001.py` identical to the pattern cited under SOURCE. So inside the lifespan `asyncio.get_running_loop()` / `asyncio.create_task()` / python-osc's `AsyncIOOSCUDPServer(..., asyncio.get_running_loop())` all work. (Only the low-level `python -m mcp.server` demo in `src/mcp/server/__main__.py` uses `anyio.run(main, backend="trio")`; `MCPServer` does not.)

**Minimal complete server** (v2 API, lifespan starts a background asyncio task, a tool awaits it). Layout: `x32mcp/__init__.py` + `x32mcp/__main__.py`.

```python
# x32mcp/__init__.py
"""Minimal MCPServer: a lifespan-started asyncio task and tools that await it."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

log = logging.getLogger(__name__)  # goes to stderr; MCPServer() already called logging.basicConfig()


@dataclass
class AppState:
    values: dict[str, float] = field(default_factory=dict)
    updated: asyncio.Event = field(default_factory=asyncio.Event)
    poller: asyncio.Task[None] | None = None


async def _poll(state: AppState) -> None:
    """Background task (stand-in for the OSC /xremote receive loop)."""
    n = 0
    try:
        while True:
            await asyncio.sleep(1.0)
            n += 1
            state.values["/ch/01/mix/fader"] = (n % 11) / 10
            state.updated.set()
            state.updated.clear()
    except asyncio.CancelledError:
        log.info("poller cancelled")
        raise
    except Exception:
        log.exception("poller crashed")  # asyncio tasks are unsupervised: log it yourself


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[AppState]:
    state = AppState()
    state.poller = asyncio.create_task(_poll(state), name="x32-poller")  # loop is asyncio (anyio default backend)
    log.info("startup complete")
    try:
        yield state  # every request sees this object as ctx.request_context.lifespan_context
    finally:
        state.poller.cancel()
        with suppress(asyncio.CancelledError):
            await state.poller
        log.info("shutdown complete")


mcp = MCPServer(
    "x32mcp",
    instructions="Control a Behringer X32 mixer over OSC.",
    version="0.1.0",
    lifespan=lifespan,
)


@mcp.tool()
async def get_value(address: str, ctx: Context[AppState]) -> float:
    """Last value seen for an OSC address (0.0-1.0)."""
    state = ctx.request_context.lifespan_context
    if address not in state.values:
        raise ToolError(f"No value cached for {address!r} yet")
    return state.values[address]


@mcp.tool()
async def wait_for_update(ctx: Context[AppState], timeout_s: float = 5.0) -> dict[str, float]:
    """Block until the background task publishes the next update, then return all values."""
    state = ctx.request_context.lifespan_context
    try:
        await asyncio.wait_for(state.updated.wait(), timeout_s)
    except TimeoutError:  # asyncio.TimeoutError is an alias of TimeoutError on 3.11+
        raise ToolError(f"No update within {timeout_s}s")
    await ctx.report_progress(1, 1, "update received")  # no-op if the client did not ask for progress
    return dict(state.values)


@mcp.resource("x32://state", mime_type="application/json")
def state_resource(ctx: Context) -> dict[str, float]:  # resources take the BARE Context
    """Snapshot of cached values."""
    return dict(ctx.request_context.lifespan_context.values)


def main() -> None:
    mcp.run(transport="stdio")  # blocks; equivalent to mcp.run()
```

```python
# x32mcp/__main__.py
from x32mcp import main

if __name__ == "__main__":
    main()
```

Alternative supervised form (anyio task group; same loop, but a crash in the task propagates on shutdown):

```python
import anyio

@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[AppState]:
    state = AppState()
    async with anyio.create_task_group() as tg:
        tg.start_soon(_poll, state)
        try:
            yield state
        finally:
            tg.cancel_scope.cancel()
```

Facts this example relies on: `lifespan=` on the constructor (line 179); `ctx.request_context.lifespan_context` (docs + `context.py` line 95); `Context[AppState]` tool-only typing; `asyncio` backend (lines 415–421); `report_progress` no-op semantics; `ToolError` semantics (§1.4.6); `python -m pkg` executes `pkg/__main__.py` (standard Python).

SOURCE: `docs/handlers/lifespan.md`; `docs_src/lifespan/tutorial00{1,2}.py` (verbatim pattern: `@asynccontextmanager async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]: ... try: yield AppContext(db=db) finally: await db.disconnect()` and `mcp = MCPServer("Bookshop", lifespan=app_lifespan)`; tool `def count_books(genre: str, ctx: Context[AppContext])` reading `ctx.request_context.lifespan_context.db`); `docs/migration.md` § "Streamable HTTP: lifespan now entered once at manager startup"; `server.py` lines 366–421 and 1065–1072 (`run_stdio_async`: `async with stdio_server() as (read_stream, write_stream): await self._lowlevel_server.run(read_stream, write_stream, self._lowlevel_server.create_initialization_options())`).

### 1.9 Running: `mcp.run(transport="stdio")`, `python -m x32mcp`, console scripts, the `mcp` CLI

`run()` overloads (verbatim, `server.py` lines 366–404):

```python
@overload
def run(self, transport: Literal["stdio"] = ...) -> None: ...
@overload
def run(self, transport: Literal["sse"], *, host: str = ..., port: int = ..., sse_path: str = ..., message_path: str = ...,
        max_request_body_size: int = ..., transport_security: TransportSecuritySettings | None = ...) -> None: ...
@overload
def run(self, transport: Literal["streamable-http"], *, host: str = ..., port: int = ..., streamable_http_path: str = ...,
        json_response: bool = ..., stateless_http: bool = ..., event_store: EventStore | None = ..., retry_interval: int | None = ...,
        max_request_body_size: int = ..., session_idle_timeout: float | None = ..., max_sessions: int | None = ...,
        transport_security: TransportSecuritySettings | None = ...) -> None: ...
def run(self, transport: Literal["stdio", "sse", "streamable-http"] = "stdio", **kwargs: Any) -> None:
    """Run the MCP server. Note this is a synchronous function. ..."""
```

- `run()` is synchronous and blocks; default transport is `stdio`; HTTP defaults `host="127.0.0.1"`, `port=8000`, path `/mcp`. `mount_path` is removed. SSE is superseded ("You don't.").
- Keep `run()` under `if __name__ == "__main__":` — `mcp dev`, `mcp run`, `mcp install` and tests **import** the module.
- Running a stdio server by hand prints nothing and waits on stdin; that silence is correct (`Ctrl-C` to stop).
- **stdout is the wire.** See §1.7 for the flush caveat. Log with `logging`.

`python -m x32mcp`: needs `x32mcp/__main__.py` (standard `runpy` behaviour — `python -m pkg` runs `pkg.__main__`). With a flat layout and `uv run` from the project root this works even without installing the package (cwd is `sys.path[0]` for `-m`); with a `src/` layout or when run from elsewhere the package must be installed, which `uv sync` does only when a `[build-system]` is declared (or `tool.uv.package = true`).

Console script (PEP 621; the SDK uses the same mechanism for its own `mcp` command):

```toml
[project.scripts]
x32mcp = "x32mcp:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

After `uv sync` this yields `.venv\Scripts\x32mcp.exe` on Windows (`uv run x32mcp`).

The `mcp` CLI (needs the `cli` extra):

- `uv run mcp dev server.py [--with pkg] [--with-editable .]` — MCP Inspector over stdio (needs `npx` on PATH).
- `uv run mcp run server.py[:object]` — imports the file, finds a module-level `mcp` / `server` / `app`, calls `run()`; only forwards `--transport`. **`mcp run` only understands `MCPServer`.**
- `uv run mcp install server.py [--name NAME] [-v KEY=VALUE] [-f .env]` — writes Claude Desktop config (§3.1). Fails with `Claude app not found` if the config directory does not exist yet.
- `mcp version`.
- v2 change: `mcp dev`/`mcp install` pin the spawned `uv run --with mcp[cli]==<installed>` environment to your SDK version.

VERIFIED: `server.py` lines 366–404 (overloads and `def run(self, transport: Literal["stdio", "sse", "streamable-http"] = "stdio", **kwargs: Any)` identical); `docs/run/index.md` lines 13 (`sse` → "You don't."), 28 (`if __name__ == "__main__":` rationale), 66–67 (defaults `127.0.0.1`, `8000`, `/mcp`), 86 (`TypeError: MCPServer.__init__() got an unexpected keyword argument 'port'`), 116 (`npx` needed for `mcp dev`), 129–138 (`mcp install`, "Claude Desktop is the only host `mcp install` knows"); `pyproject.toml` line 31 `mcp = "mcp.cli:app [cli]"`, line 116 `requires = ["hatchling", "uv-dynamic-versioning"]`, 117 `build-backend = "hatchling.build"`.

SOURCE: `docs/run/index.md` (all sections, incl. the option list and the `TypeError` warning); `docs/get-started/real-host.md` § "One server, every host" and "It doesn't show up"; `docs_src/run/tutorial00{1,2,3}.py`; `docs_src/real_host/tutorial001.py`; `server.py` lines 366–421; `python-sdk/pyproject.toml` `[project.scripts]` and `[build-system]` (`hatchling`); Python docs for `-m` / `__main__` (standard library behaviour, not re-fetched).

### 1.10 Testing the server in-process

Verbatim test from `docs/get-started/testing.md` (uses anyio's pytest plugin, which ships with `anyio`, a hard dependency of `mcp`):

```python
import pytest
from inline_snapshot import snapshot
from mcp import Client
from mcp.types import CallToolResult, TextContent
from server import mcp

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c

@pytest.mark.anyio
async def test_call_add_tool(client: Client):
    result = await client.call_tool("add", {"a": 1, "b": 2})
    result.meta = None  # drop the server identity stamp in `_meta`
    assert result == snapshot(CallToolResult(content=[TextContent(type="text", text="3")], structured_content={"result": 3}))
```

- `Client(mcp)` connects in memory, no subprocess; it is **era-neutral** (negotiates 2026-07-28 by default; pass `mode="legacy"` to test push elicitation/sampling and drop `raise_exceptions=True` there).
- `raise_exceptions=True` only un-sanitises failures **outside** a tool body; a tool exception is still an `is_error=True` result — assert on the result, use `caplog` for the traceback.
- The same fixtures work under `pytest-asyncio` with `asyncio_mode = "auto"` (the SDK runs on the asyncio backend either way). VERIFIED-CORRECTED (resolves former UNCONFIRMED #8): the SDK **does not mention `pytest-asyncio` anywhere** — `grep -ri pytest-asyncio` over `python-sdk/pyproject.toml` and `docs/**` returns nothing; the SDK's own `[tool.pytest.ini_options]` (pyproject line 256) relies on anyio's plugin and `filterwarnings = ["error", ...]`. So pytest-asyncio is neither endorsed nor discouraged; it is simply not what they use. It works because `Client(mcp)` only needs *a* running asyncio loop. Do not enable both plugins on the same `async def` test without marking; pick one.

VERIFIED: `docs/get-started/testing.md` lines 49 (`def anyio_backend():`), 55 (`Client(mcp, raise_exceptions=True)`), 77–99 (`raise_exceptions` semantics, "era-neutral", `mode="legacy"`); `docs_src/testing/tutorial001.py` (`MCPServer("Calculator")`, `add(a: int, b: int) -> int`).

SOURCE: `docs/get-started/testing.md`; `docs/servers/handling-errors.md` info box on `raise_exceptions`; `docs/whats-new.md` warning "This is the one place a ported v1 server changes behavior".

### 1.11 Deprecated / removed in v2 (what not to use)

- **Removed**: `mcp.server.fastmcp` (import error), `FastMCP`, `FastMCPError`, `MCPServer.get_context()`, `mount_path=`, transport kwargs on the constructor, `MCP_*` env config, `McpError` (→ `MCPError(code, message, data)`), the WebSocket transport and `mcp[ws]` extra, experimental Tasks API, `mcp.shared.progress` (`ProgressContext`/`progress()`), `mcp.shared.version`, `mcp.shared.session`, `streamablehttp_client` spelling, low-level `Server` decorator handlers (now constructor `on_*` params), camelCase attribute names (`isError` → `is_error`, `inputSchema` → `input_schema`, `mimeType` → `mime_type`; wire JSON unchanged), `ping` (removed from the 2026-07-28 protocol), `logging/setLevel`, client→server progress.
- **Deprecated but working (warn with `MCPDeprecationWarning`)**: protocol logging `ctx.log/debug/info/warning/error`, roots (`ctx.session.list_roots()`), server-initiated sampling (`ctx.session.create_message()`) — the last two also *fail* on a 2026 connection with `NoBackChannelError`; SDK helpers `FuncMetadata.call_fn_with_arg_validation()`, `AuthSettings(resource_server_url=...)` without `validate_token_resource=`, OAuth providers without `issuer=` (removed in 3.0).
- Behaviour changes without an import error: sync handlers on worker threads; `MCPError` in a tool is a protocol error; results validated before leaving; `httpx2` replaces `httpx` (TLS via OS trust store / `truststore`); RFC 6570 templates; lifespan once per process.

VERIFIED: `docs/whats-new.md` line 144 (WebSocket transport + `mcp[ws]` extra removed), 146 (`mcp.shared.version`, `mcp.shared.progress`, `mcp.shared.session` removed; `mcp.types` kept as alias), 185–189 (`ping` removed; `logging/setLevel` and client `notifications/roots/list_changed` removed; progress now server→client only), 138 (`httpx2` + `truststore` OS trust store); `docs/migration.md` dependency-floor table lines 64–101 (anyio `>=4.9`/`>=4.10`, pydantic `>=2.12`, sse-starlette `>=3.0.0`, typing-extensions `>=4.13.0`, pywin32 `>=311`, opentelemetry-api `>=1.28.0`, mcp-types exact pin, httpx/httpx-sse removed, httpx2 `>=2.5.0`, `ws` extra removed) and headings 558 (`McpError` → `MCPError`), 882 (`get_context()` removed), 1141 (`message` → `data`, `extra` removed), 1184 (`ProgressContext` removed), 2011 (Tasks removed), 2065 (`streamablehttp_client` removed), 287 (camelCase → snake_case).

SOURCE: `docs/whats-new.md` §§ "Behavior that changes without an import error", "Removed outright", "Roots, sampling, and protocol logging are deprecated; `ping` is removed"; `docs/deprecated.md` (tables); `docs/migration.md` headings list.

---

## 2. Pure-Python feasibility (aarch64 / Termux) — dependency audit

Method: for each package, PyPI JSON `releases[latest]` filenames were classified as `py3-none-any` (pure) vs platform-tagged wheels, and `linux_aarch64` (manylinux) wheels noted. "Pure fallback" = whether an sdist build without a compiler/Rust succeeds.

### 2.1 `mcp` 2.2.0 dependency tree

| Package | Latest | `requires_python` | Pure wheel? | Platform wheels | Notes |
|---|---|---|---|---|---|
| mcp | 2.2.0 | >=3.10 | **Y** | 0 | |
| mcp-types | 2.2.0 | >=3.10 | Y | 0 | depends only on `pydantic>=2.12.0`, `typing-extensions>=4.13.0` |
| pydantic | 2.13.5 | >=3.9 | Y | 0 | **hard-pins `pydantic-core==2.46.5`** (exact) |
| **pydantic-core** | 2.49.0 (pydantic 2.13.5 needs 2.46.5) | >=3.10 | **N (Rust)** | 136 incl. manylinux/musllinux aarch64 | **no pure-Python fallback exists**; sdist needs Rust + maturin |
| typing-inspection | 0.4.4 | >=3.10 | Y | 0 | |
| anyio | 4.15.1 | >=3.10 | Y | 0 | |
| httpx2 | 2.13.0 | >=3.10 | Y | 0 | deps: `httpcore2==2.13.0` (pure), `truststore>=0.10` (pure), `idna`, `anyio` |
| jsonschema | 4.26.0 | >=3.10 | Y | 0 | deps: attrs, jsonschema-specifications, referencing, **rpds-py** |
| **rpds-py** | 2026.6.3 | >=3.11 | **N (Rust)** | 115 incl. aarch64 | no pure fallback; sdist needs Rust |
| opentelemetry-api | 1.44.0 | >=3.10 | Y | 0 | + importlib-metadata (pure) |
| pyjwt[crypto] | 2.14.0 | >=3.9 | Y | 0 | the `crypto` extra pulls **cryptography** |
| **cryptography** | 50.0.1 | >=3.9 (!=3.9.0/1) | **N (Rust+C)** | 45 incl. aarch64 | needs Rust + OpenSSL to build; **cffi** (C) too |
| python-multipart | 0.0.32 | >=3.10 | Y | 0 | |
| pywin32 | 312 | >=3.9 | N | 21 (win32/win_amd64/win_arm64; **no sdist**) | **Windows only** (`sys_platform == "win32"`); cp312/cp313 win_amd64 wheels exist |
| sse-starlette | 3.4.11 | >=3.10 | Y | 0 | |
| starlette | 1.6.0 | >=3.10 | Y | 0 | |
| uvicorn | 0.53.0 | >=3.10 | Y | 0 | deps click, h11 (pure) |
| typing-extensions | 4.16.0 | >=3.9 | Y | 0 | |

VERIFIED (PyPI JSON re-pulled 2026-09-19, latest-release wheel filenames classified): mcp 2.2.0 pure; mcp-types 2.2.0 pure; pydantic 2.13.5 (`>=3.9`, pure, `requires_dist` contains `pydantic-core==2.46.5`); pydantic_core 2.49.0 (`>=3.10`, **no** `py3-none-any`, 136 platform wheels of which 19 are `aarch64`, sdist present); rpds-py 2026.6.3 (`>=3.11`, no pure wheel, 115 platform / 16 aarch64); jsonschema 4.26.0 pure (deps `attrs>=22.2.0`, `jsonschema-specifications>=2023.03.6`, `referencing>=0.28.4`, `rpds-py>=0.25.0`); cryptography 50.0.1 (`!=3.9.0,!=3.9.1,>=3.9`, no pure wheel, 45 platform / 14 aarch64); PyJWT 2.14.0 pure, `cryptography>=3.4.0; extra == "crypto"`; httpx2 2.13.0, httpcore2 2.13.0, truststore 0.10.4, anyio 4.15.1, opentelemetry-api 1.44.0, python-multipart 0.0.32, sse-starlette 3.4.11, starlette 1.6.0, uvicorn 0.53.0, typing-extensions 4.16.0, typing-inspection 0.4.4 — all pure `py3-none-any`; pywin32 312 (`>=3.9`) has exactly 21 wheels (`cp39`–`cp315` × `win32`/`win_amd64`/`win_arm64`) and no sdist.

**Verdict**: `mcp` itself and 90% of its tree are pure Python, but **three transitive dependencies are compiled with no pure-Python fallback: `pydantic-core` (mandatory), `rpds-py` (via `jsonschema`), `cryptography` (+`cffi`, via `pyjwt[crypto]`)**. Is there a pure-Python pydantic? No: pydantic v2 is a thin wrapper over `pydantic-core`; the `pydantic.v1` shim still requires the `pydantic` v2 distribution and therefore `pydantic-core`. The mcp pin `pydantic>=2.12.0` excludes pydantic v1.

### 2.2 Termux (native, bionic libc) options

Facts:

- Termux's `python` package is **3.14.6** (`TERMUX_PKG_VERSION="3.14.6"`), which satisfies every floor above (`websockets>=17` needs 3.11).
- manylinux aarch64 wheels **do not install** on native Termux (bionic, not glibc); pip falls back to the sdist → needs `rust` (`pkg install rust`) and, for `pydantic-core`, a source build of `maturin` first. Community reports: ~10 min per build, memory-hungry on 4 GB phones.
- Termux ships prebuilt **`python-rpds-py` 2026.6.3** (`packages/python-rpds-py/build.sh`, depends `python, python-pip`) and **`python-cryptography` 50.0.1** (`packages/python-cryptography/build.sh`, depends `openssl, python, python-pip`). There is **no** `python-pydantic-core`, `python-pydantic`, `python-jsonschema`, `python-yaml`, `python-aiohttp`, `python-multidict`, `python-yarl`, `python-frozenlist`, or `maturin` package (all 404 in `termux-packages/master`).
- Third-party prebuilt wheels: `pip install pydantic-core --extra-index-url https://eutalix.github.io/android-pydantic-core/` — built for aarch64/armv7/x86_64/x86; the README badge says **Python 3.9–3.13** (hard-codes `/data/data/com.termux/files/usr/lib`). VERIFIED-CORRECTED (resolves former UNCONFIRMED #7): the **Eutalix releases page** (`github.com/Eutalix/android-pydantic-core/releases`) has only four tags — v2.41.5, v2.42.0, v2.45.0, **v2.46.3 (2026-05-01, newest)** — and v2.46.3 ships wheels for **cp310, cp311, cp312 only** (`linux_aarch64/armv7l/i686/x86_64` tags), i.e. **no cp313 and no cp314**, and not the `2.46.5` that pydantic 2.13.5 pins. The actively maintained **fork `Goplr/android-pydantic-core`** (index `https://Goplr.github.io/android-pydantic-core/`, `pip install pydantic-core --extra-index-url https://Goplr.github.io/android-pydantic-core/`) has releases **v2.48.0, v2.46.5 and v2.41.5, all published 2026-09-05**, README "Python Versions: 3.9 – 3.14 candidates, auto-pruned per release", and states "Python 3.13+ wheels carry a dual tag (`linux_<arch>` + `android_24_<abi>`, PEP 738)"; the v2.46.5 release lists 26 assets. **Still UNCONFIRMED**: the actual asset file names of Goplr v2.46.5 (the GitHub page's asset list failed to render and the API was rate-limited from this IP), so whether a `cp314-...-linux_aarch64.android_24_arm64_v8a.whl` really exists must be checked with `pip download pydantic-core==2.46.5 --extra-index-url https://Goplr.github.io/android-pydantic-core/ --no-deps` on the phone. The wheel version must equal the exact `pydantic-core==` pin of the pydantic release pip selects (2.46.5 for pydantic 2.13.5).

Recommended recipes:

1. **proot-distro (easiest, fully binary)**: `pkg install proot-distro && proot-distro install ubuntu && proot-distro login ubuntu`, then inside: `apt install python3 python3-venv`, `python3 -m venv ~/x32 && ~/x32/bin/pip install mcp python-osc pyyaml websockets`. glibc → manylinux `aarch64` wheels for pydantic-core, rpds-py, cryptography install directly. UDP works inside proot (Wi-Fi to the X32 is fine). **UNCONFIRMED**: whether Claude Desktop/Code can launch a proot process as a stdio server (irrelevant if the Termux side is a remote/HTTP deployment).
2. **Native Termux**: `pkg install python python-pip rust binutils openssl python-cryptography python-rpds-py`; then `pip install --no-build-isolation maturin` (source build) and `pip install mcp` (builds pydantic-core ~10 min; set `CARGO_BUILD_JOBS=2` on low-RAM devices — **UNCONFIRMED** flag, from community reports). Verify with `python -c "import pydantic_core, rpds, cryptography"`.

VERIFIED: `termux-packages/master/packages/python/build.sh` `TERMUX_PKG_VERSION="3.14.6"`; `python-rpds-py/build.sh` `TERMUX_PKG_VERSION="2026.6.3"`, `TERMUX_PKG_DEPENDS="python, python-pip"`; `python-cryptography/build.sh` `TERMUX_PKG_VERSION="50.0.1"`, `TERMUX_PKG_DEPENDS="openssl, python, python-pip"`; HTTP 404 re-confirmed for `python-pydantic-core`, `python-pydantic`, `maturin`, `python-yaml`, `python-jsonschema`.

SOURCE: PyPI JSON for every package above (wheel filenames, `requires_dist`); `https://raw.githubusercontent.com/termux/termux-packages/master/packages/{python,python-rpds-py,python-cryptography}/build.sh` (`TERMUX_PKG_VERSION`), HTTP 404 for the packages listed as absent; `pydantic/pydantic-core#855` ("can not compile pydantic_core from termux"), `Eutalix/android-pydantic-core` README (install command, "Python Versions: 3.9, 3.10, 3.11, 3.12, 3.13", architectures, Termux lib path), `davepotts.software/.../pydantic-in-termux-install-rust-and-some-patience.html`, `bd-loser/colab-mcp-termux` BUILD-FROM-SOURCE.md, `googlecolab/google-colab-cli#131` (Termux `pkg install python-cryptography python-rpds-py` workaround).

### 2.3 The other project dependencies

| Package | Latest | `requires_python` | Compiled? | Pure fallback | Notes |
|---|---|---|---|---|---|
| **python-osc** | 1.10.2 | >=3.10 | No | n/a | "a pure python library that has no external dependencies"; only a `py3-none-any` wheel. Provides `SimpleUDPClient`, `Dispatcher`, `AsyncIOOSCUDPServer` (`create_serve_endpoint()`), `OscMessageBuilder`, `OscBundleBuilder`; arg types "int, int64, float, string, double, MIDI, timestamps, blob, nil". |
| **PyYAML** | 6.0.3 | >=3.8 | Optional C ext (LibYAML bindings) | **Yes** — pure-Python parser/emitter is the default; `--with-libyaml` / `--without-libyaml` build flags; `yaml.CLoader`/`CDumper` only when bindings exist | 72 platform wheels + sdist; the sdist builds without libyaml. Not in Termux repo; pip sdist build OK (needs only a C compiler if it tries the ext; it skips when libyaml is absent). |
| **websockets** | 17.1 (2026-08-26) | **>=3.11** (17.0 dropped 3.10; 16.1 is last for 3.10) | Optional `websockets.speedups` C ext | **Yes** — `setup.py`: `Extension("websockets.speedups", ..., optional=os.environ.get("BUILD_EXTENSION") != "yes")`; `BUILD_EXTENSION=no` skips it; a `py3-none-any` wheel is published alongside 146 platform wheels | Legacy asyncio API removed in 17.0; use `from websockets.asyncio.server import serve` / `from websockets.asyncio.client import connect`. |
| **aiohttp** | 3.14.3 | >=3.10 | C exts by default; `setup.py`: `NO_EXTENSIONS = bool(os.environ.get("AIOHTTP_NO_EXTENSIONS"))` ... `build_type = "Pure" if NO_EXTENSIONS else "Accelerated"` | Yes via env var at build time, but **no pure wheel is published** (118 platform wheels only) → sdist build on Termux, plus its deps multidict/yarl/frozenlist/propcache (each has a pure wheel and `*_NO_EXTENSIONS` env var: `MULTIDICT_NO_EXTENSIONS`, `YARL_NO_EXTENSIONS`, `FROZENLIST_NO_EXTENSIONS`, `PROPCACHE_NO_EXTENSIONS`) | Heavier; 9 deps. |
| stdlib only | — | — | — | — | `http.server` + hand-rolled RFC 6455 is possible but not worth it. |

**Recommendation for the dashboard WebSocket: `websockets`** — one dependency, pure-Python wheel, optional speedups, asyncio-native (`async with serve(handler, "127.0.0.1", 8765): await asyncio.Future()`). Requires Python ≥3.11 (fine for 3.12 on Windows and 3.14 on Termux). If you need to serve static HTML too, either serve it from a tiny `http.server` thread/`asyncio.start_server`, or fall back to `aiohttp` (which does both) with `AIOHTTP_NO_EXTENSIONS=1` when building from source.

Compatibility summary for `requires-python = ">=3.12"`: every package above supports 3.12 (pydantic-core >=3.10, rpds-py >=3.11, websockets >=3.11, pytest 9 >=3.10, pytest-asyncio 1.4 >=3.10).

VERIFIED (PyPI JSON 2026-09-19): python-osc 1.10.2 (`>=3.10`, `requires_dist: None`, single `py3-none-any` wheel + sdist, uploaded 2026-04-02); PyYAML 6.0.3 (`>=3.8`, 72 platform wheels incl. 15 aarch64, sdist, **no** `py3-none-any` wheel — the pure path is the sdist build); websockets 17.1 (`>=3.11`, `requires_dist: None`, `py3-none-any` wheel **plus** 146 platform wheels incl. 15 aarch64, uploaded 2026-08-26; changelog: 17.0 "requires Python ≥ 3.11", 16.1 last for 3.10, 16.0 "requires Python ≥ 3.10"); aiohttp 3.14.3 (`>=3.10`, **no** pure wheel, 118 platform / 12 aarch64, sdist); multidict 6.9.0, yarl 1.25.1, frozenlist 1.8.0, propcache 0.5.4 — each publishes a `py3-none-any` wheel alongside platform wheels.

SOURCE: PyPI JSON (`python-osc`, `pyyaml`, `websockets`, `aiohttp`, `multidict`, `yarl`, `frozenlist`, `propcache`, `aiosignal`, `aiohappyeyeballs`); `attwad/python-osc/README.rst`; `yaml/pyyaml/README.md` ("--with-libyaml", "--without-libyaml", `yaml.CLoader`); `python-websockets/websockets` `setup.py` lines 20–38 and `pyproject.toml` (`requires-python = ">=3.11"`), changelog (17.0 "requires Python ≥ 3.11", "16.1 is the last version supporting Python 3.10"; 16.0 "requires Python ≥ 3.10"); `aio-libs/aiohttp/setup.py` lines 15–102; `aio-libs/multidict/setup.py` line 7; `aio-libs/{yarl,frozenlist,propcache}/packaging/pep517_backend/_backend.py` (`PURE_PYTHON_ENV_VAR`).

---

## 3. Host configuration

### 3.1 Claude Desktop — `claude_desktop_config.json` (Windows)

- Location: **`%APPDATA%\Claude\claude_desktop_config.json`** (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`). Open it from the app: Claude menu → **Settings…** → **Developer** tab → **Edit Config** (creates the file if missing).
- Keys per server: `command` (string), `args` (array), `env` (object). Absolute paths; JSON needs **doubled backslashes**. **Fully quit** Claude Desktop and reopen after editing (config is read at launch).
- Logs: `%APPDATA%\Claude\logs\mcp.log` (connections) and `%APPDATA%\Claude\logs\mcp-server-<NAME>.log` (**your server's stderr**). View: `type "%APPDATA%\Claude\logs\mcp*.log"`.
- The docs' Windows example (verbatim):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\username\\Desktop", "C:\\Users\\username\\Downloads"]
    }
  }
}
```

- Known Windows pitfall from the same page: an `ENOENT` error mentioning `${APPDATA}` in a path is fixed by adding `"env": {"APPDATA": "C:\\Users\\user\\AppData\\Roaming\\"}` to the entry (Claude Desktop starts servers with a minimal environment; "Your shell's environment is not there").
- Claude Desktop spawns the command directly (no shell) — **UNCONFIRMED** in docs, but consistent with "if a host can't find `uv` ... replace the bare `uv` with the absolute path from `where uv`". Practical consequence on Windows: a bare `"command": "python"` resolves through PATH and can hit the Store stub (§5.3). Use the venv's `python.exe` or `uv.exe` by absolute path.
- VERIFIED-CORRECTED: the sentence "Your shell's environment is not there" is **not** on the MCP `connect-local-servers` page; the actual wording is in the SDK's `docs/get-started/real-host.md` lines 96–97: "Claude Desktop starts your server in its own process, so your shell's environment variables are not there. `uv run mcp install server.py -v API_KEY=abc123` (or `-f .env`) records them in the entry's `env` field." and line 29: "a host launches your server from *its* working directory with a near-empty environment, not from your shell." The `connect-local-servers` page contributes only the `${APPDATA}`/ENOENT accordion.
- VERIFIED: `modelcontextprotocol.io/docs/develop/connect-local-servers` — file locations (`%APPDATA%\Claude\claude_desktop_config.json`, `~/Library/Application Support/Claude/claude_desktop_config.json`), Settings… → Developer → Edit Config ("creates a new configuration file if one doesn't exist"), the Windows JSON block (identical), "completely quit Claude Desktop and restart it", logs `%APPDATA%\Claude\logs` with `mcp.log` and `mcp-server-SERVERNAME.log` ("the stderr output from the named server"), `type "%APPDATA%\Claude\logs\mcp*.log"`, the `"APPDATA": "C:\\Users\\user\\AppData\\Roaming\\"` fix. SDK `real-host.md` line 165 confirms `mcp-server-<NAME>.log` / `mcp.log` under `%APPDATA%\Claude\logs`; line 72 shows `"mcp[cli]==2.0.0"` in the generated entry (the pin follows the installed SDK version).
- Local check on this machine (2026-09-19, read-only): `%APPDATA%\Claude\claude_desktop_config.json` exists.

Recommended entry for this project (venv form):

```json
{
  "mcpServers": {
    "x32": {
      "command": "C:\\Users\\jim\\src\\x32mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "x32mcp"],
      "env": {"X32_HOST": "192.168.0.64", "PYTHONUNBUFFERED": "1"}
    }
  }
}
```

uv form (what `mcp install` writes, adapted; `--directory` is uv's global "run from this project" option):

```json
{
  "mcpServers": {
    "x32": {
      "command": "C:\\Users\\jim\\.local\\bin\\uv.exe",
      "args": ["run", "--directory", "C:\\Users\\jim\\src\\x32mcp", "x32mcp"],
      "env": {"X32_HOST": "192.168.0.64"}
    }
  }
}
```

The SDK's own generated entry, verbatim from `docs/get-started/real-host.md`:

```json
{"mcpServers": {"Bookshop": {"command": "/absolute/path/to/uv",
  "args": ["run", "--frozen", "--with", "mcp[cli]==2.0.0", "mcp", "run", "/absolute/path/to/server.py"]}}}
```

VERIFIED-CORRECTED (resolves former UNCONFIRMED #1): a `cwd` key is **not supported / silently ignored** by both hosts. Evidence: `anthropics/claude-code` issue **#17565** "[BUG] MCP config: cwd field in .mcp.json is completely ignored" ("The server always starts in the directory where Claude Code was launched"); issue **#42883** ".mcp.json silently ignores unsupported fields (e.g. cwd) — should warn"; issue **#75266** "Claude Code desktop app launches MCP servers with cwd set to $HOME instead of the session's project directory" — states the `cwd` field in `claude_desktop_config.json` is also ignored, **closed as not planned**, workaround = wrapper script that `cd`s first. Neither `code.claude.com/docs/en/mcp.md` nor `plugins-reference.md` documents a `cwd` field (stdio fields documented: `command`, `args`, `env`). Community blog posts showing `"cwd": ...` in `claude_desktop_config.json` are not authoritative. **Do not use `cwd`**: pass `--directory` to uv, or use absolute paths and make the server independent of its working directory (e.g. resolve `X32MCP_CONFIG` from `env`).

SOURCE: `https://modelcontextprotocol.io/docs/develop/connect-local-servers` (file locations, Settings → Developer → Edit Config, Windows JSON, restart, logs, `${APPDATA}` accordion); `docs/get-started/real-host.md` (Claude Desktop entry, "Fully quit Claude Desktop", log file names, `where uv` tip, `-v KEY=VALUE`/`-f .env`); `docs/run/index.md` § "The `mcp` command".

### 3.2 Claude Code — project `.mcp.json`

Format (from `code.claude.com/docs/en/mcp`):

```json
{
  "mcpServers": {
    "server-name": {
      "type": "stdio",
      "command": "/path/to/command",
      "args": ["arg1", "arg2"],
      "env": {"KEY": "value"}
    }
  }
}
```

- `type`: `"stdio"` | `"http"` | `"sse"` | `"ws"`. "Claude Code reads an entry with no `type` as a stdio server" — so `type` is optional for stdio but write it explicitly; an entry with `url` and no `type` is an error (`MCP server "<name>" has a "url" but no "type"`).
- Environment expansion in `command`, `args`, `env`, `url`, `headers`: `${VAR}` and `${VAR:-default}`.
- Scopes: **local** (default; stored in `~/.claude.json` under `projects["<path>"].mcpServers`), **project** (`.mcp.json` at the repo root, check it in; Claude Code prompts for approval; `claude mcp reset-project-choices` resets), **user** (`~/.claude.json`, all projects). Precedence: local > project > user > plugin > claude.ai connectors; same-named entries override entirely.

Project file for this server:

```json
{
  "mcpServers": {
    "x32": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "${X32MCP_DIR:-.}", "x32mcp"],
      "env": {"X32_HOST": "${X32_HOST:-192.168.0.64}"}
    }
  }
}
```

`claude mcp add` syntax (verbatim shapes):

```
claude mcp add [options] <name> -- <command> [args...]
claude mcp add --transport stdio <name> -- <command> [args...]
claude mcp add --env KEY=value --transport stdio <name> -- <command> [args...]
claude mcp add --scope local|project|user --transport stdio <name> -- <command> [args...]
```

- Everything after `--` is the launch command, untouched. `--env` may repeat. Default scope `local`.
- This project: `claude mcp add --transport stdio --scope project --env X32_HOST=192.168.0.64 x32 -- uv run --directory C:\Users\jim\src\x32mcp x32mcp` (bash/cmd). In PowerShell prefer `claude mcp add-json x32 '{"type":"stdio","command":"uv","args":["run","--directory","C:\\Users\\jim\\src\\x32mcp","x32mcp"],"env":{"X32_HOST":"192.168.0.64"}}'` — mind PowerShell's own quoting of the JSON argument.
- Other commands: `claude mcp list`, `claude mcp get <name>`, `claude mcp remove <name> [--scope ...]`, `claude mcp add-json <name> '<json>'`, `claude mcp add-from-claude-desktop` (**"only works on macOS and Windows Subsystem for Linux (WSL)"**), `claude mcp serve`, `/mcp` inside a session, `claude mcp reset-project-choices`.
- The SDK's own example: `claude mcp add bookshop -- uv run --with "mcp[cli]" mcp run /absolute/path/to/server.py`.
- **Windows note**: the current page has no `cmd /c` guidance. Older Claude Code docs told native-Windows users to wrap `npx` as `cmd /c npx ...` (**UNCONFIRMED** whether still needed; irrelevant when `command` is an `.exe` such as `python.exe`/`uv.exe`). VERIFIED (re-fetched `code.claude.com/docs/en/mcp.md` 2026-09-19): the page contains **no** Windows-specific `cmd /c` or `npx` note.
- VERIFIED: `.mcp.json` structure, `type` values `stdio`/`http`/`sse`/`ws`, `${VAR}` / `${VAR:-default}` expansion in `command`, `args`, `env`, `url`, `headers`, the `MCP server "<name>" has a "url" but no "type"` error and "Claude Code reads an entry with no `type` as a stdio server", scopes table (local → `~/.claude.json` project entry; project → `.mcp.json`; user → `~/.claude.json` top-level), precedence local > project > user > plugin > claude.ai connectors, `--` separator ("Everything after `--` is passed to the server untouched"), "Transport defaults to `stdio` if not specified", `add-from-claude-desktop` supported only on macOS and WSL. Added detail: short flags `-t` (`--transport`), `-e` (`--env`), `-s` (`--scope`), `-H` (`--header`); further subcommands `claude mcp login <name> [--no-browser] [--callback-port <port>]`, `claude mcp logout <name>`, `claude mcp add-json <name> '<json>' [--client-secret] [--scope <scope>]`; the page warns that credential-looking variables (`ANTHROPIC_API_KEY`, `NPM_TOKEN`, `HTTPS_PROXY`, ...) read as empty when expanded into remote-server URLs/headers, and an unset variable with no default leaves the literal `${VAR}` text and a warning.
- Local check on this machine (2026-09-19): the `claude` CLI is not on PATH (`claude --version` fails), so `claude mcp add` cannot be exercised here; use the `.mcp.json` file form.

SOURCE: `https://code.claude.com/docs/en/mcp` and `.../mcp.md` (JSON structure, expansion syntax, scope storage, command list, the "no `type`" rule, WSL-only import); `docs/get-started/real-host.md` § "Claude Code"; the VS Code / Cursor variants on the same page (`.vscode/mcp.json` uses `servers` + `type`; `.cursor/mcp.json` uses `mcpServers`).

---

## 4. pytest and pytest-asyncio

- **pytest 9.1.1** (`requires_python >=3.10`; deps `pluggy<2,>=1.5`, `iniconfig`, `packaging`, `pygments`, `colorama` on win32).
- **pytest-asyncio 1.4.0** (2026-05-26): `requires_python >=3.10`; **`pytest<10,>=8.4`** ("Updated minimum supported pytest version to v8.4.0"); "Overriding the `event_loop_policy` fixture is deprecated. Use the `pytest_asyncio_loop_factories` hook instead."
- History that matters: **1.0.0 (2025-05-26) removed the deprecated `event_loop` fixture**; 1.3.0 dropped Python 3.9 and added pytest 9 support; 1.2.0 added `--asyncio-debug`/`asyncio_debug`.
- Configuration options (from `docs/reference/configuration.rst`):
  - `asyncio_mode`: `auto` | `strict` (**default `strict`**). CLI `--asyncio-mode=auto`; CLI wins over the file.
  - `asyncio_default_fixture_loop_scope`: `function|class|module|package|session`; "When this configuration option is unset, it defaults to the fixture scope" (older 0.24–0.26 emitted a warning when unset).
  - `asyncio_default_test_loop_scope`: same values; default `function`.
  - `asyncio_debug`: `true|false`.
- pyproject form (pytest's standard `[tool.pytest.ini_options]` table; values are strings):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
asyncio_default_test_loop_scope = "function"
filterwarnings = ["error::mcp.MCPDeprecationWarning"]
testpaths = ["tests"]
```

With `asyncio_mode = "auto"`, every `async def test_*` and every `async` fixture runs on asyncio without `@pytest.mark.asyncio`. Note the SDK's own pattern uses `@pytest.mark.anyio` + an `anyio_backend` fixture (§1.10); both work, just don't mix them on one test.

VERIFIED: PyPI `pytest` 9.1.1, `requires_python >=3.10`, `requires_dist` = `colorama>=0.4; sys_platform == "win32"`, `exceptiongroup>=1; python_version < "3.11"`, `iniconfig>=1.0.1`, `packaging>=22`, `pluggy<2,>=1.5`, `pygments>=2.7.2`, `tomli>=1; python_version < "3.11"`; PyPI `pytest-asyncio` 1.4.0 uploaded 2026-05-26, `requires_python >=3.10`, `requires_dist` = `backports-asyncio-runner<2,>=1.1; python_version < "3.11"`, `pytest<10,>=8.4`, `typing-extensions>=4.12; python_version < "3.13"`; changelog entries 1.4.0 (2026-05-26: `event_loop_policy` override deprecated → `pytest_asyncio_loop_factories` hook; "Minimum supported pytest version to v8.4.0"), 1.3.0 (2025-11-10: dropped Python 3.9, "Support for pytest 9"), 1.2.0 (2025-09-12: `--asyncio-debug`), 1.1.0 (2025-07-16), 1.0.0 (2025-05-26: removed "The deprecated event_loop fixture"); configuration page: `asyncio_mode` default `strict`, `asyncio_default_fixture_loop_scope` "defaults to the fixture scope" when unset, `asyncio_default_test_loop_scope` default `function`, `asyncio_debug`, CLI `--asyncio-mode` takes precedence over the file.

SOURCE: PyPI JSON for `pytest`, `pytest-asyncio`; `pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html` and raw `docs/reference/configuration.rst`; `.../reference/changelog.html` (1.4.0, 1.3.0, 1.2.0, 1.1.0, 1.0.0, 0.26.0 entries); `docs/deprecated.md` (pytest `filterwarnings` tip).

---

## 5. Python & uv on Windows (no admin)

### 5.1 uv

- Install (per-user, no admin; installs to **`%USERPROFILE%\.local\bin`** as `uv.exe`, `uvx.exe`, `uvw.exe`):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# pinned: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/0.12.17/install.ps1 | iex"
```

  Alternatives: `winget install --id=astral-sh.uv -e`, `scoop install main/uv`, `pip install uv`, `pipx install uv`. Upgrade: `uv self update`. Current version referenced by the docs: **0.12.17** (PyPI `uv` 0.12.17). The docs do not mention admin rights; the installer writes only under the user profile.
- Python: `uv python install 3.12` (also `uv python install`, `uv python install 3.11 3.12`, `uv python list`, `uv python upgrade 3.12`, `uv python pin 3.12` → `.python-version`, `uv python dir` prints the install dir, `uv python dir --bin` the executables dir). Installs a versioned `python3.12.exe` into the bin dir; `uv python install 3.12 --default` (experimental) adds `python`/`python3` names. On Windows managed Pythons are **registered per PEP 514**, so `py -V:Astral/CPython3.12.x` works. Install directory: `UV_PYTHON_INSTALL_DIR`, default under `%APPDATA%\uv\`. VERIFIED-CORRECTED (resolves former UNCONFIRMED #3, with a residual doc/source conflict): the **docs** (`docs.astral.sh/uv/reference/storage/`) say managed Pythons live in a `python/` subdirectory of the persistent data directory, whose Windows default is stated as `%APPDATA%\uv\data` (→ `%APPDATA%\uv\data\python`); the **source at `astral-sh/uv@main`** says otherwise: `crates/uv-dirs/src/lib.rs` `user_state_dir()` = `choose_base_strategy().data_dir().join("uv")` with no `data` segment, etcetera's Windows `data_dir()` = `%APPDATA%` (`lunacookies/etcetera` `src/base_strategy/windows.rs` line 194–196), and `crates/uv-python/src/managed.rs` `from_settings()` uses `StateStore::from_settings(None)?.bucket(StateBucket::ManagedPython)` (comment: "e.g., `~/.local/uv/python`"), `crates/uv-state/src/lib.rs` `from_settings()` prefers `legacy_user_state_dir()` **if it already exists**, else `user_state_dir()`, else `.uv`. So a fresh install on current uv resolves to **`%APPDATA%\uv\python`**, while a machine with an older `%APPDATA%\uv\data` directory keeps using it. `uv python dir` remains the authoritative answer. (uv was not installed on this machine — `uv --version` fails — so it could not be printed here.)
- Discovery order: managed installs → `python`/`python3`/`python3.x` on PATH → Windows registry and Microsoft Store interpreters.
- Project: `uv venv --python 3.12` (auto-downloads), `uv sync` (creates `.venv`, installs deps + the project **only if a `[build-system]` exists**, or `tool.uv.package = true`), `uv lock`, `uv run <cmd>`, `uv add "mcp[cli]"`, `uv add --dev pytest`. Dev deps go in `[dependency-groups] dev = [...]` (PEP 735); `[tool.uv] dev-dependencies` is the legacy spelling. `requires-python` in `[project]` drives interpreter selection.

Skeleton `pyproject.toml` for this project:

```toml
[project]
name = "x32mcp"
version = "0.1.0"
description = "Behringer X32 OSC control as an MCP server"
requires-python = ">=3.12"
dependencies = ["mcp>=2.2,<3", "python-osc>=1.10", "pyyaml>=6.0", "websockets>=17"]

[project.scripts]
x32mcp = "x32mcp:main"

[dependency-groups]
dev = ["pytest>=9", "pytest-asyncio>=1.4", "inline-snapshot"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

VERIFIED: installation page — `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, pinned `https://astral.sh/uv/0.12.17/install.ps1`, install location `$HOME\.local\bin\` (`uv.exe`, `uvx.exe`, `uvw.exe`), `winget install --id=astral-sh.uv -e`, `scoop install main/uv`, `pip install uv`, `pipx install uv`, `uv self update`, no admin-rights statement; PyPI `uv` 0.12.17 uploaded 2026-09-18. `concepts/python-versions/`: managed Pythons "are registered with the Windows registry per PEP 514" (example `uv python install 3.13.1` → `py -V:Astral/CPython3.13.1`), discovery order (1) `UV_PYTHON_INSTALL_DIR` managed installs, (2) `python`/`python3`/`python3.x` on PATH (`python.exe` on Windows), (3) Windows registry and Microsoft Store interpreters; "uv only installs a *versioned* executable by default" (e.g. `python3.13`), `--default` (experimental) adds `python`/`python3`. `concepts/projects/config/`: "uv uses the presence of a build system to determine if a project contains a package that should be installed", `tool.uv.package = true|false` overrides, "Using the entry point tables requires a build system to be defined." **UNCONFIRMED (minor)**: whether the Windows versioned executable is literally `python3.12.exe` — the docs only show the Unix name.

SOURCE: `https://docs.astral.sh/uv/getting-started/installation/`; `.../guides/install-python/`; `.../concepts/python-versions/` (PEP 514 registry, `--default`, discovery order, `.python-version`, `uv venv --python 3.12`); `.../reference/storage/`; `.../concepts/projects/config/` (`[build-system]` rule, `tool.uv.package`, `[dependency-groups]`, `[project.scripts]`); PyPI `uv` 0.12.17; `python-sdk/pyproject.toml` (`[tool.uv] required-version = ">=0.9.5"`, `[dependency-groups]`).

### 5.2 python.org installer

- python.org now recommends the **Python install manager** (`pymanager`, currently **26.3**): `py install 3.12` / `pymanager install 3.12`; it is a per-user MSIX tool that provides `python`, `py`, `pythonw`, `pyw`, `pymanager` commands. Latest stable CPython listed: **3.14.7 (2026-08-05)**.
- The traditional 3.12 installer still exists: **3.12.10 (2025-04-08) is the last 3.12.x with a "Windows installer (64-bit)"** — 3.12 is in the **security** phase (source-only releases) with EOL **2028-10** (PEP 693). Its per-user ("just for me") default dir is `%LocalAppData%\Programs\Python\Python312`; tick "Add python.exe to PATH". No admin needed for per-user installs.
- The `py` launcher: `py -3.12`, `py -0` / `py --list`, `py -0p` / `--list-paths` (shows every registered interpreter with paths — the quickest way to see what `python` will *not* tell you).
- Because uv's managed builds track newer 3.12.x patch releases than python.org's last installer, `uv python install 3.12` is the better choice on Windows.

VERIFIED: `python.org/downloads/windows/` — "Python 3.14.7 - Aug. 5, 2026", "Python install manager 26.3" (June 30, 2026), Python 3.12.10 (April 8, 2025) is the newest 3.12.x on that page and the last with a "Windows installer (64-bit)"; `devguide.python.org/versions/` — 3.12 | PEP 693 | security | 2023-10-02 | 2028-10; 3.13 | PEP 719 | bugfix | 2029-10; 3.14 | PEP 745 | bugfix | 2030-10; 3.15 | PEP 790 | prerelease | first release 2026-10-01. `docs.python.org/3/using/windows.html` — `DefaultJustForMeTargetDir` = `%LocalAppData%\Programs\Python\PythonXY[-32|-64]`; "the `--list`, `--list-paths`, `-0` and `-0p` commands (e.g. `py -0p`) are retained"; install-manager commands `py install` / `pymanager`.

SOURCE: `https://www.python.org/downloads/windows/` (release list, "Latest Python 3 Release - Python 3.14.7", "Latest Python install manager - Python install manager 26.3"); `https://devguide.python.org/versions/` ("3.12 | PEP 693 | security | 2023-10-02 | 2028-10"); `https://docs.python.org/3/using/windows.html` (`DefaultJustForMeTargetDir`, PATH option, `py -0p`, install manager commands, app execution alias troubleshooting).

### 5.3 The Microsoft Store `python.exe` stub — how `python` on PATH can be a trap

- Windows 10/11 ships **App Execution Aliases** `python.exe` and `python3.exe` in **`%LOCALAPPDATA%\Microsoft\WindowsApps\`**. They are **0-byte NTFS reparse points**; with no Python installed, running `python` opens the Microsoft Store (or, with arguments, prints the message below). VERIFIED-CORRECTED (resolves former UNCONFIRMED #4) — exact wording as reproduced by multiple independent sources (bobbyhadz, discuss.python.org thread 80479, microsoft/PTVS#7829):

```
Python was not found; run without arguments to install from the Microsoft Store, or disable this shortcut from Settings > Manage App Execution Aliases.
```

and, when run without arguments in a non-interactive context: `Python was not found but can be installed from the Microsoft Store: https://go.microsoft.com/fwlink?linkID=2082640`. (Capitalisation of "App Execution Aliases" varies slightly between reproductions.)

Local evidence on this machine (2026-09-19, read-only): `Get-Item "$env:LOCALAPPDATA\Microsoft\WindowsApps\python.exe" -Force` → `Length = 0`, `Attributes = Archive, ReparsePoint`; `where.exe python` → `C:\Python38\python.exe` **then** `C:\Users\jim\AppData\Local\Microsoft\WindowsApps\python.exe`; `py -0p` → only ` -3.8-64  C:\Python38\python.exe`; `(Get-Command python).Source` → `C:\Python38\python.exe`. So on this PC `python` is a real but ancient **3.8** (below `mcp`'s `>=3.10` floor) and the Store stub is second on PATH — a fresh 3.12 (uv or python.org) is required, and the host config must point at it by absolute path. `%UserProfile%\AppData\Local\Microsoft\WindowsApps` is on the user PATH by default, **after** other user entries, so a python.org/uv install that adds itself to PATH earlier wins; a subprocess launched by Claude Desktop with a trimmed PATH may not.
- Detection (PowerShell):

```powershell
$p = (Get-Command python -ErrorAction SilentlyContinue).Source
$isStub = $p -and ($p -like '*\Microsoft\WindowsApps\python.exe') -and ((Get-Item $p).Length -eq 0)
where.exe python        # lists every python.exe on PATH in order
py -0p                  # real interpreters registered with the launcher
python -c "import sys; print(sys.executable)"   # errors / opens Store if the stub won
```

  Rule used by several installers: treat a `python.exe` **directly under `WindowsApps`** with **0 bytes** as the stub; a real Store Python lives one level deeper (`WindowsApps\PythonSoftwareFoundation.Python.3.x_...\python.exe`).
- Disable: Settings → Apps → Advanced app settings → **App execution aliases** → turn off "App Installer python.exe / python3.exe" (python docs: "Click Start, open 'Manage app execution aliases'").
- For the MCP host config, sidestep the whole issue by using the absolute venv `python.exe` or `uv.exe` (§3).

SOURCE: `https://docs.python.org/3/using/windows.html` (troubleshooting "opens the Store app", `%UserProfile%\AppData\Local\Microsoft\WindowsApps` on PATH); `https://michielvoo.net/2023/02/12/windows-11-app-execution-aliases.html` (0-byte reparse points); `https://www.tiraniddo.dev/2019/09/overview-of-windows-execution-aliases.html`; `unslothai/unsloth#5959` and `trmv2007-bot/Agentuse#3` (detection heuristics: path directly under WindowsApps + 0-byte length; `Get-Command python` succeeds for the stub); `learn.microsoft.com/en-us/answers/questions/4121307/...` and `.../4291355/...`.

---

## 6. Maillot X32 emulator for Windows

### 6.1 Where it is, what it is called

- Download (Maillot's site, "X32" page): **Windows** `X32_windows.zip` → `https://drive.google.com/file/d/1kxs9ZanmZfrubBQ2rGJ3xVN7UKZ2ObT0/view` (Google Drive page title "X32_windows.zip - Google Drive"); **Mac** `X32mac.zip` → `https://drive.google.com/file/d/1-3h2gqjbpyQAsQS03_vVorETw6plrdG7/view`. Site entry dated **May 28, 2022**. Zip contents (file names inside, size) **UNCONFIRMED** — Google Drive requires an interactive download; expect `X32.exe`.
- The OSC document: **`X32_OSC.pdf`** ("Unofficial X32/M32 OSC Remote Protocol", updated **June 29, 2021** per the site) → `https://drive.google.com/file/d/1Yt_S1mpPt3CAzeq3Dnpe_IqctQ-1GlTz/view`; the repo README also points at `http://www.academia.edu/9709659/UNOFFICIAL_X32_OSC_REMOTE_PROTOCOL`.
- VERIFIED: site page — `X32_windows.zip` and `X32mac.zip` links (same Drive ids), "Updated May 28, 2022", "X32 emulator only manages OSC commands, no MIDI, no Audio, no USB.", OSC PDF link (same Drive id) "updated June 29, 2021"; no DLL or run instructions on the site.
- Source: `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32.c` (5,661 lines; banner `"X32 - v0.88 - An X32 Emulator - (c)2014-2019 Patrick-Gilles Maillot"`; `#define XVERSION "4.06"` — it reports firmware 4.06). VERIFIED: `wc -l` = 5661; banner at line 946; `#define XVERSION "4.06"` line 70; `MAX_CLIENTS 4` line 79; `MAX_METERS 17` line 80; `XREMOTE_TIME 11` line 81; revision log lines 19–39 (0.87 "Partial fix for /meters command", 0.88 "Fix for /meters/5 and /meter/6 timefactor control", 0.78 "includes FW4.0 capabilities, and includes /-stat/lock ,i 2 as a shutdown command"). Command tables are `#include`d from `X32Channel.h`, `X32CfgMain.h`, `X32PrefStat.h`, `X32Auxin.h`, `X32Fxrtn.h`, `X32Bus.h`, `X32Mtx.h`, `X32Dca.h`, `X32Fx.h`, `X32Output.h`, `X32Headamp.h`, `X32Show.h`, `X32Misc.h`, `X32Libs.h` ("More than 10k commands!"). **UNCONFIRMED** whether the May-2022 Windows zip is exactly v0.88 or a later private build.

Site description (verbatim): "With no particular attempt at optimization, this tool parses and manages X32 OSC commands (as a real X32 would), keeps up to 4 xremote clients updated - Of course no sound, and even if all 32 Channels, 16 Sends, 8 FXreturns, 8 Aux, 6 Matrix, 8 DCA, Main and all FX parameters are fully implemented along with multi-client xremote update and many more, not all X32 commands are supported (and that's not the goal), but the emulator is handy for developing X32 applications." and "Note [been asked this several times]: X32 emulator only manages OSC commands, no MIDI, no Audio, no USB."

### 6.2 Running it — command line (verbatim from `X32.c` `main()`, `getopt(argc, argv, "i:d:v:x:b:f:r:m:h")`)

```
usage: X32 [-i <IP address>] - default: first IP available on system
 [-d 0/1, debug option] - default: 0
 [-v 0/1, verbose option] - default: 1
 The options below apply in conjunction with -v 1
     [-x 0/1, echoes incoming verbose for /xremote] - default: 0
     [-b 0/1, echoes incoming verbose for /batchsubscribe] - default: 0
     [-f 0/1, echoes incoming verbose for /formatsubscribe] - default: 0
     [-r 0/1, echoes incoming verbose for /renew] - default: 0
     [-m 0/1, echoes incoming verbose for /meters] - default: 0

 The (non-Behringer) command "/shutdown" will save data and quit
```

- **Port is hard-coded**: `strcpy(Xport_str, "10023");` (comment: `// 10023: X32 desk, 10024: XAir18`). There is **no `-p` option**; the emulator always binds UDP **10023** on the address given by `-i` (or, without `-i`, the first address `gethostbyname(gethostname())` returns on Windows — on a multi-adapter PC this may be the wrong NIC; use `-i`). It binds one specific address, not `0.0.0.0`, so for a local test run `X32.exe -i 127.0.0.1` and point the client at `127.0.0.1:10023`; on the console it prints `Listening to port: 10023, X32 IP = <ip>`. VERIFIED: `getopt(argc, argv, "i:d:v:x:b:f:r:m:h")` line 878; usage `printf`s lines 907–915 (text identical to the block above); `strcpy(Xport_str, "10023")` line 930 with the comment at 929; `getaddrinfo(Xip_str, Xport_str, &hints, …)` + `bind()` loop lines 958–970, `AF_INET`/`SOCK_DGRAM`/`IPPROTO_UDP` hints; `printf("Listening to port: %s, X32 IP = %s\n", …)` line 976; `getmyIP()` (Windows) lines 1087–1099 = `gethostname()` → `gethostbyname()` → first `h_addr_list` entry; `WSAStartup(MAKEWORD(2, 2), …)` line 939. Inference (not tested): because the bind address is whatever string `getaddrinfo` resolves, `X32.exe -i 0.0.0.0` should bind all interfaces, but `/xinfo` and `/status` would then report `0.0.0.0` as the console IP (they echo `Xip_str`), which confuses clients that connect back to the reported address — prefer a concrete address. The README's usage block (lines 279–288) predates 0.75 and omits `-i`; the C source is authoritative.
- Main loop: non-blocking `select()` with a 10 ms timeout; each datagram is dispatched on its first 4 bytes through `Xheader[]` (below); meter blobs are re-sent on their own timers (`XDeltaMeters[i] = 50000` µs = 50 ms default interval).
- VERIFIED: `select()` with `timeout.tv_usec = 10000` (10 ms) lines 994–1000; per-datagram dispatch compares the first 4 bytes as one `int` against `Xheader[i].header.icom` (line 1046: `// single int test!`); `XDeltaMeters[i] = 50000` lines 926/4845.
- Persistence: on `/shutdown` (`function_shutdown` → `X32Shutdown()`), it writes **`.X32res.rc`** in the **current working directory** (`fopen(".X32res.rc", "w")`, prints `saving init file...`) and exits; on start `X32Init()` reads the same file (`Reading init file...`) or prints `X32 resource file does not exist, create one with '/shutdown' command`. README: "You should run the X32 a first time and issue a "/shutdown" command from a connected client; this will create a file preserving all X32 parameters ... Make sure you end your client sessions with "/shutdown"". Without `/shutdown` all changes are lost (there is no periodic save). VERIFIED: `X32Shutdown()` lines 5395–5402 (`fopen(".X32res.rc", "w")`, `printf("saving init file...")`), `X32Init()` lines 5538–5545 (`fopen(".X32res.rc", "r")`, `printf("Reading init file...")`), line 979–980 (`X32 resource file does not exist, create one with '/shutdown' command`), README line 307.
- Windows build: MinGW-32, `#ifdef __WIN32__` → winsock2 with `WSAStartup`. VERIFIED-CORRECTED: the repo `Makefile` (header: "Copyright (C) 2019 Javier Iglesias", a **Linux** makefile) has `CC=gcc`, `LIBS := m X32`, `DIRS := X32lib`, `CFLAGS=-O0 -g3 -Wall -IX32lib`, `LDFLAGS=-LX32lib -lm -lX32`, target `X32: X32lib` → `gcc $(CFLAGS) -o build/X32 X32.c -LX32lib -lm -lX32`; there is **no `-lX32_lib` and no `-lws2_32`** in it. For Windows the README (line 32) says "Windows based programs have to be linked with their respective libraries under Windows (w2_32, gdi32, comdlg32, etc.)" and (line 34) "The basic MinGW-32 environment and tools are sufficient"; the author builds in Eclipse + MinGW, not with this Makefile. README line 27: "generally simple C programs, static linked applications". Extra DLL requirements for the shipped exe: **UNCONFIRMED** (a MinGW build may need `libgcc_s_dw2-1.dll`/`libwinpthread-1.dll` only if not linked statically; check with `dumpbin /dependents X32.exe` or just run it).
- Companion tool for manual testing: `X32_Command.exe`. VERIFIED-CORRECTED: `X32_Command.c` usage (lines 274–281) is `usage: X32_command [-i X32 console ipv4 address] [-d 0/1, [0], debug option] [-v 0/1 [1], verbose option] [-k 0/1 [1], keyboard mode on] [-t int [10], delay between batch commands in ms] [-s file, reads X32node formatted data lines from 'file'] [-f file, sets batch mode on, getting input data from 'file']` and prints `default IP is 192.168.1.62` — **but the code never uses that address**: with no `-i` (`Xip_str[0] == 0`, lines 325–338) it calls `getmyIP()`, sets the last octet to `0xff` (`Xip.sin_addr.S_un.S_un_b.s_b4 = 0xff; // Local [/24] broadcast address`) and enables `SO_BROADCAST`, i.e. it **broadcasts to the local /24**; the README (line 91) says `default IP is 192.168.0.64`, which is stale. Always pass `-i <emulator ip>` (e.g. `X32_Command.exe -i 127.0.0.1`). It uses a 500 ms `select()` timeout. Type `/info ,`, `/ch/01/mix/fader ,f 0.5`, `/node ,s ch/01/config`, `xremote on`, `/shutdown`. The README shows the emulator dialog for those exact commands, e.g. `X->,   28 B: /ch/01/mix/fader~~~~,f~~[0.5000]` and `X->,   40 B: node~~~~,s~~/ch/01/config "" 0 OFF 0~~~~`.
- **Second shutdown path** (added by verifier): `function_stat()` (line 4580–4587): `if (strstr(r_buf, "/lock")) { // shutdown is /-stat/lock ,i 2 ... if (r_buf[19] == 2) return X32Shutdown(); }` — so sending `/-stat/lock ,i 2` (the real console's "power down" OSC) also saves `.X32res.rc` and exits the emulator. Your server should never send `/-stat/lock ,i 2` to the emulator unless it means to stop it.

### 6.3 What it answers (dispatch table, verbatim `Xheader[]`)

`/shu`→`function_shutdown`, `/inf`→`function_info`, `/xin`→`function_xinfo`, `/sta`→`function_status`, `/xre`→`function_xremote`, `/nod`→`function_node`, `/` (slash command)→`function_slash`, `/con`→config, `/mai`→main, `/-pr`→prefs, `/-st`→stat, `/-ur`→urec, `/ch/`, `/aux`, `/fxr`, `/bus`, `/mtx`, `/dca`, `/fx/`, `/out`, `/hea`(headamp), `/met`→`function_meters`, `/-ha`, `/ins`, `/-sh`→show, `/ren`→renew, `/cop`→copy, `/add`, `/loa`→load, `/sav`→save, `/del`→delete, `/uns`→unsubscribe, `/-us`, `/und`, `/-ac`→action, `/-li`→libs, `/sho`→showdump.

VERIFIED: `X32header Xheader[]` lines 490–528, 37 entries exactly as listed (the slash entry is `{ {"/\0\0\0" }, &function_slash }`, so it matches only a bare `/` command; `/-ha`, `/ins`, `/-us` map to `function_misc`; `/und` maps to a generic `function`); `Xmeters[]` lines 531–549 = `/meters/0` … `/meters/16`. **A datagram whose first four bytes match no entry is silently dropped**: `whoto` is reset to 0 each loop (line 997), the `while (i < Xheader_max)` search (lines 1043–1051) leaves `whoto = 0` and `s_len = 0`, and `Xsend()` (lines 1156–1189) sends only `if ((who_to & S_SND) && s_len)` / `if ((who_to & S_REM) && s_len)`.

Concrete replies (from the C):

- `/info` → `/info ,ssss "V2.07" "<-prefs/name or 'X32 Emulator'>" "X32" "4.06"` (server version string is `V2.07`, console model `X32`, firmware `XVERSION` 4.06). `/xinfo` → `,ssss <ip> <name> "X32" "4.06"`. `/status` → `/status ,sss "active" <ip> <name>`. All "send reply only to requesting client" (`S_SND`).
- `/xremote`: registers the sender (IP:port) in `X32Client[MAX_CLIENTS=4]` with expiry `time(NULL) + XREMOTE_TIME` (**11 s**; `X32port.h` uses 9/10 s for clients); refreshes on repeat; replaces an expired slot; silently ignores a 5th client. `/unsubscribe` removes the sender. Parameter changes from one client are echoed to the other valid xremote clients ("if a data is not changed, it will not be sent to remote clients", 0.71). VERIFIED: `function_xremote()` lines 3106–3136 (update existing → `time(NULL) + XREMOTE_TIME`; else first `vlid == 0` slot; else first slot with `xrem < time(NULL)`; else `return 0; // no room for new clients!`); `function_unsubscribe()` lines 3092–3100 sets `vlid = 0`; `Xsend()` `S_REM` branch (lines 1170–1189) sends to every other valid client whose `xrem > time(NULL)`; `X32port.h` `XPORT 10023`, `XREMOTE_TIMEOUT 9`, `XREMOTE_TIMEOUT10 10`.
- `/info` reply VERIFIED at lines 3050–3058: `/info ,ssss "V2.07" <name|"X32 Emulator"> "X32" "4.06"`; `/xinfo` lines 3062–3071: `,ssss <Xip_str> <name> "X32" "4.06"`; `/status` lines 3075–3086: `,sss "active" <Xip_str> <name>`; all `return S_SND`.
- `/meters`: `/meters/0` … `/meters/16` are recognised (`Xmeters[]`, `MAX_METERS 17`); `function_meters` parses `/meters ,s /meters/N` and `/meters ,si /meters/N <timefactor>`; `Xprepmeter(i, count, ...)` builds a **fake, zero-filled `,b` blob** ("prepare (fake) meters/i command reply", buffer `ZMemory`'d) with the float counts hard-coded per meter id: `/meters/0` 70, `/1` 96, `/2` 49, `/3` 22, `/4` 82, `/5` 27, `/6` 4, `/7` 16, `/8` 6, `/9` 32, `/10` 32, `/11` 5, `/12` 4, **`/13` 48, `/14` 80, `/15` 50, `/16` 48** (VERIFIED-CORRECTED: the 13–16 counts were omitted; lines 4879–4927). Blob layout: 4-byte big-endian blob length, then a **little-endian** int32 count, then `count` little-endian float32 — matching the real console's format; values are all 0.0. VERIFIED from `Xprepmeter()` lines 4819–4861, verbatim:

```c
	ZMemory(&Xbuf_meters[i][0], 512);			// Prepare buffer (set to all 0's)
	memcpy(&Xbuf_meters[i][0], buf, 16);          // "/meters/N\0..,b\0\0" header (16 bytes)
	endian.ii = (l + 1) * 4;		// actual blob content length (in bytes)
	Xbuf_meters[i][16] = endian.cc[3];   // big-endian OSC blob size at bytes 16..19
	Xbuf_meters[i][17] = endian.cc[2];
	Xbuf_meters[i][18] = endian.cc[1];
	Xbuf_meters[i][19] = endian.cc[0];
	Lbuf_meters[i] = endian.ii + 20;		// length of the whole message (in bytes)
	endian.ii = l; // number of floats (32-bit)
	Xbuf_meters[i][20] = endian.cc[0];   // LITTLE-endian float count at bytes 20..23
	Xbuf_meters[i][21] = endian.cc[1];
	Xbuf_meters[i][22] = endian.cc[2];
	Xbuf_meters[i][23] = endian.cc[3];
	gettimeofday (&XTimerMeters[i], NULL);		// get time
	XInterMeters[i] = XTimerMeters[i];			// keep initial time for inter-timers
	XTimerMeters[i].tv_sec += 10;				// keep valid for 10s
	XActiveMeters |= (1 << i);					// set meter to active
	XClientMeters[i] = *Client_ip_pt;			// remember requesting IP client TODO: not the right approach
```

  Worked example: `/meters/0` → `l = 70` → blob size field = `(70+1)*4 = 284 = 0x0000011C` written as bytes `00 00 01 1C`; count field = `70 = 0x46` written as `46 00 00 00`; whole datagram = `284 + 20 = 304` bytes (16-byte header + 4-byte size + 4-byte count + 70×4 zero floats). Further verified behaviour: a meter subscription **stays active for 10 s** (`tv_sec += 10`) after each request, exactly like the real console (renew with `/renew` or re-send `/meters`); default interval 50 ms; with `,si` the time factor is read big-endian from `r_buf[k+24..k+27]` and clamped — `if ((endian.ii < 1) || (endian.ii > 99)) endian.ii = 1;` → interval `50000 * tf` µs; **only one requesting client per meter id** is remembered (`XClientMeters[i]`, TODO comment), so a second client subscribing to the same `/meters/N` steals the stream; the resend loop (lines 1064–1080) sends `Xbuf_meters[i]` whenever `xmeter_time > XInterMeters[i]` and clears the active bit when the 10 s timer expires. Note `/renew` does **not** extend the meter timer in the emulator (see next bullet), so re-send `/meters` every <10 s when testing against it. Changelog: "0.87: Partial fix for /meters command", "0.88: Fix for /meters/5 and /meter/6 timefactor control". So the emulator is fine for exercising meter *subscription plumbing* but never produces non-zero levels.
- `/batchsubscribe`, `/formatsubscribe`, `/renew`: VERIFIED-CORRECTED (resolves former UNCONFIRMED #6) — **none of the three is implemented**. The `/bat` and `/for` prefixes appear only in the main loop's *verbose-echo* branch (lines 1016–1025: `else if (strncmp(r_buf, "/bat", 4) == 0) { if (X_batch) { Xfdump("->X", …) } }`, same for `/for` with `X_format`), which decides whether to print the incoming packet; the real dispatch is the `Xheader[]` scan, which has **no** `/bat` or `/for` entry, so `whoto` stays 0 and nothing is sent back (see "silently dropped" above). `/ren` **is** in `Xheader[]`, but `function_renew()` (lines 4954–4958) is verbatim:

```c
int function_renew() {
//
// Ignored for now / Todo
	return S_SND;
}
```

  With `s_len == 0` the `S_SND` flag sends nothing. Consequences for the X32 server: `/batchsubscribe`/`/formatsubscribe`-based meter or parameter subscriptions and `/renew`-based keep-alive **cannot be tested against the emulator** — use `/meters ,si /meters/N tf` re-sent every ≤9 s for meter plumbing and `/xremote` (re-sent every ≤9 s) for parameter change notifications.
- Not implemented (comments in `X32.c`; VERIFIED at lines 1186, 4516, 4842, 5022–5023, 5105, 5124, 5141, 5216): `// TODO: 'scene', 'libfx', 'librout' should be implemented`, `// 'snippet' is not supported on X32`, `// TODO: implement 'libfx' and 'librout'`, `/load`/`/save` for scenes partially ("`TODO: do a proper implementation of the function`"), some `/-action` parameters ("last two <string><int> parameters are ignored"), no `/-stat/solosw` aggregation (`TODO`). README: "Some X32 commands are not implemented (lack of time mostly), some are also not present as this program was developed before FW2.14 was released."
- It accepts both OSC-1.1 style empty type tags (`/info ,`) and the legacy untagged form (`/info`) (0.40/0.41).

SOURCE: `https://sites.google.com/site/patrickmaillot/x32` (emulator paragraph, links, dates); Google Drive view pages (titles `X32_windows.zip`, `X32_OSC.pdf`); `X32-Behringer/README.md` lines 20–50 (build), 78–140 (X32_Command usage), 270–310 (X32 usage block, dialog example, `/shutdown` note); `X32-Behringer/X32.c` lines 1–40 (revision log), 69–81 (`#define`s: `XVERSION "4.06"`, `MAX_CLIENTS 4`, `MAX_METERS 17`, `XREMOTE_TIME 11`), 490–530 (`Xheader[]`), 531–551 (`Xmeters[]`), 869–1000 (`main()`, `getopt`, usage text, `Xport_str "10023"`, `getaddrinfo`/`bind`, `X32Init` message), 1040–1090 (dispatch loop, meter timers), 3050–3140 (`function_info`, `function_xinfo`, `function_status`, `function_unsubscribe`, `function_xremote`), 4820–4920 (`Xprepmeter`, `function_meters`), 5022–5023 / 5105 / 5124 / 5141 / 5216 (TODO comments), 5371–5420 (`function_shutdown`, `X32Shutdown`, `.X32res.rc`), 5538–5545 (`X32Init`, `.X32res.rc`); `X32-Behringer/X32port.h` (`XPORT 10023`, `XREMOTE_TIMEOUT 9/10`); `X32-Behringer/Makefile` (`gcc`, `-lX32 -lm`, targets incl. `X32`).

---

## 7. UNCONFIRMED items (collected) — status after verification

1. ~~Whether `claude_desktop_config.json` supports a `cwd` key~~ → **RESOLVED: not supported, silently ignored** in both Claude Desktop and Claude Code `.mcp.json` (claude-code issues #17565, #42883, #75266; not in any official field list). See §3.1.
2. Whether Claude Code on native Windows still needs `cmd /c` wrapping for `npx`-style commands — **still UNCONFIRMED**; re-fetched page has no Windows note; irrelevant for `python.exe`/`uv.exe` commands.
3. ~~Exact default of uv's managed-Python directory on Windows~~ → **RESOLVED with a caveat**: current uv source → `%APPDATA%\uv\python` on a fresh machine; an existing legacy `%APPDATA%\uv\data` is kept; the docs page still says `%APPDATA%\uv\data`. `uv python dir` is authoritative. See §5.1.
4. ~~Exact text of the Store-stub message~~ → **RESOLVED**: "Python was not found; run without arguments to install from the Microsoft Store, or disable this shortcut from Settings > Manage App Execution Aliases." (plus the fwlink line). See §5.3.
5. Contents/file names inside `X32_windows.zip`, whether it is v0.88, and whether the exe needs MinGW runtime DLLs — **still UNCONFIRMED** (Google Drive interactive download; the site says only "Updated May 28, 2022").
6. ~~How the emulator dispatches `/batchsubscribe` and `/formatsubscribe`~~ → **RESOLVED: it does not**; `/bat`/`/for` only gate verbose echo, no `Xheader[]` entry → silently dropped; `/renew` is a no-op stub. See §6.3.
7. ~~Whether the Eutalix index publishes CPython 3.14 wheels~~ → **RESOLVED for Eutalix: no** (newest v2.46.3, cp310–cp312 only). The **Goplr fork** claims 3.9–3.14 with PEP 738 dual tags and has a v2.46.5 release (2026-09-05, 26 assets) — **asset names still UNCONFIRMED**; verify with `pip download` on the device. See §2.2.
8. ~~Whether the python-sdk docs endorse `pytest-asyncio`~~ → **RESOLVED: they never mention it**; they use anyio's pytest plugin. Either works. See §1.10.
9. `CARGO_BUILD_JOBS`/memory advice for building pydantic-core on a phone — **still UNCONFIRMED** (community reports, not measured).
10. Little-endian byte order inside the emulator's meter blobs → **RESOLVED for the count field** (code writes `endian.cc[0]` first at byte 20, i.e. little-endian; the OSC blob *size* at bytes 16–19 is big-endian); the float payload is all zeros so its byte order is unobservable in the emulator, but the real console is little-endian per the OSC protocol document. See §6.3.
11. (new, minor) Whether uv's Windows versioned executable is named `python3.12.exe` — docs show only the Unix `python3.12`.

## 8. Sources (URLs)

- https://pypi.org/pypi/mcp/json · https://pypi.org/pypi/mcp-types/json · https://pypi.org/pypi/pydantic/json · https://pypi.org/pypi/pydantic-core/json · https://pypi.org/pypi/rpds-py/json · https://pypi.org/pypi/jsonschema/json · https://pypi.org/pypi/cryptography/json · https://pypi.org/pypi/pyjwt/json · https://pypi.org/pypi/httpx2/json · https://pypi.org/pypi/httpcore2/json · https://pypi.org/pypi/truststore/json · https://pypi.org/pypi/pywin32/json · https://pypi.org/pypi/python-osc/json · https://pypi.org/pypi/pyyaml/json · https://pypi.org/pypi/websockets/json · https://pypi.org/pypi/aiohttp/json (+ multidict, yarl, frozenlist, propcache, aiosignal, aiohappyeyeballs) · https://pypi.org/pypi/pytest/json · https://pypi.org/pypi/pytest-asyncio/json · https://pypi.org/pypi/uv/json (and the rest of the tree listed in §2.1)
- https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md · .../pyproject.toml · .../mkdocs.yml · .../docs/whats-new.md · .../docs/migration.md · .../docs/deprecated.md · .../docs/get-started/{index,installation,first-steps,real-host,testing}.md · .../docs/servers/{index,tools,structured-output,resources,uri-templates,prompts,handling-errors}.md · .../docs/handlers/{index,context,lifespan,progress,logging,dependencies}.md · .../docs/run/{index,legacy-clients}.md · .../docs/troubleshooting.md · .../src/mcp/server/{__init__,__main__,fastmcp,context,runner}.py · .../src/mcp/server/mcpserver/{__init__,server,context}.py · .../docs_src/{context,dependencies,first_steps,handling_errors,lifespan,logging,progress,prompts,real_host,resources,run,structured_output,testing,tools}/tutorial*.py
- https://py.sdk.modelcontextprotocol.io/ (documentation home, referenced by the README)
- https://sites.google.com/site/patrickmaillot/x32 · https://drive.google.com/file/d/1kxs9ZanmZfrubBQ2rGJ3xVN7UKZ2ObT0/view · https://drive.google.com/file/d/1-3h2gqjbpyQAsQS03_vVorETw6plrdG7/view · https://drive.google.com/file/d/1Yt_S1mpPt3CAzeq3Dnpe_IqctQ-1GlTz/view · https://api.github.com/repos/pmaillot/X32-Behringer/contents/ · https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/{README.md,X32.c,Makefile,X32port.h}
- https://code.claude.com/docs/en/mcp · https://code.claude.com/docs/en/mcp.md · https://modelcontextprotocol.io/docs/develop/connect-local-servers
- https://pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html · https://raw.githubusercontent.com/pytest-dev/pytest-asyncio/main/docs/reference/configuration.rst · https://pytest-asyncio.readthedocs.io/en/latest/reference/changelog.html
- https://docs.astral.sh/uv/getting-started/installation/ · https://docs.astral.sh/uv/guides/install-python/ · https://docs.astral.sh/uv/concepts/python-versions/ · https://docs.astral.sh/uv/reference/storage/ · https://docs.astral.sh/uv/concepts/projects/config/
- https://docs.python.org/3/using/windows.html · https://www.python.org/downloads/windows/ · https://devguide.python.org/versions/
- https://raw.githubusercontent.com/termux/termux-packages/master/packages/{python,python-rpds-py,python-cryptography}/build.sh · https://github.com/pydantic/pydantic-core/issues/855 · https://github.com/Eutalix/android-pydantic-core · https://davepotts.software/mobile/2025/11/04/pydantic-in-termux-install-rust-and-some-patience.html · https://github.com/bd-loser/colab-mcp-termux/blob/main/docs/BUILD-FROM-SOURCE.md · https://github.com/googlecolab/google-colab-cli/issues/131
- https://raw.githubusercontent.com/aio-libs/aiohttp/master/setup.py · https://raw.githubusercontent.com/aio-libs/multidict/master/setup.py · https://raw.githubusercontent.com/aio-libs/{yarl,frozenlist,propcache}/master/packaging/pep517_backend/_backend.py · https://raw.githubusercontent.com/python-websockets/websockets/main/{setup.py,pyproject.toml} · https://websockets.readthedocs.io/en/stable/project/changelog.html · https://raw.githubusercontent.com/attwad/python-osc/master/README.rst · https://raw.githubusercontent.com/yaml/pyyaml/main/README.md
- https://michielvoo.net/2023/02/12/windows-11-app-execution-aliases.html · https://www.tiraniddo.dev/2019/09/overview-of-windows-execution-aliases.html · https://github.com/unslothai/unsloth/pull/5959 · https://github.com/trmv2007-bot/Agentuse/pull/3 · https://learn.microsoft.com/en-us/answers/questions/4121307/how-to-remove-python-exe-from-the-windowsapps-dire · https://learn.microsoft.com/en-us/answers/questions/4291355/are-python-exe-and-python3-exe-present-under-app-e
- Added by the verification pass: https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32_Command.c · https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/src/mcp/server/mcpserver/utilities/logging.py · .../src/mcp/server/context.py · .../src/mcp/server/session.py · .../src/mcp/server/mcpserver/exceptions.py · https://raw.githubusercontent.com/agronholm/anyio/master/src/anyio/_core/_eventloop.py · https://pypi.org/pypi/typer/json · https://raw.githubusercontent.com/astral-sh/uv/main/crates/uv-dirs/src/lib.rs · .../crates/uv-python/src/managed.rs · .../crates/uv-state/src/lib.rs · https://raw.githubusercontent.com/lunacookies/etcetera/master/src/base_strategy/windows.rs · https://github.com/Eutalix/android-pydantic-core/releases · https://github.com/Goplr/android-pydantic-core (+ /releases, /releases/tag/v2.46.5) · https://github.com/anthropics/claude-code/issues/17565 · .../issues/42883 · .../issues/75266 · https://code.claude.com/docs/en/plugins-reference.md · https://bobbyhadz.com/blog/python-was-not-found-run-without-arguments-to-install · https://discuss.python.org/t/python-was-not-found-run-without-arguments-to-install-from-the-microsoft-store/80479 · https://websockets.readthedocs.io/en/stable/project/changelog.html

---

## Verification log (2026-09-19, independent re-derivation)

Method: PyPI JSON re-pulled for all 31 packages; python-sdk `main` sources/docs re-downloaded (29 files) and grepped at the cited line numbers; `pmaillot/X32-Behringer@master` `X32.c`, `X32_Command.c`, `Makefile`, `X32port.h`, `README.md` re-read; uv/anyio/etcetera Rust and Python sources fetched; Claude Code, MCP, uv, python.org, devguide, pytest-asyncio, Maillot site and websockets changelog pages re-fetched; read-only local checks (`where.exe python`, `py -0p`, `Get-Item …\WindowsApps\python.exe`, `uv --version`, `claude --version`, config-file existence) run on this Windows 10 machine. GitHub's REST API was rate-limited from this IP, so two asset listings could not be completed (noted below).

**Corrections (VERIFIED-CORRECTED):**

1. **§6.3 `/batchsubscribe` / `/formatsubscribe` / `/renew`** — was "UNCONFIRMED, may be handled inside `function_meters`/`function_renew`". Fact: none is implemented. `/bat`/`/for` appear only in the verbose-echo `if` chain (`X32.c` 1016–1025); `Xheader[]` has no such entry, so the packet is dropped (`whoto = 0`, `s_len = 0`, `Xsend()` sends only when `s_len`); `function_renew()` (4954–4958) is `// Ignored for now / Todo … return S_SND;` with an empty buffer. Practical impact: subscription/keep-alive code cannot be exercised against the emulator; use `/meters` re-sent every ≤9 s and `/xremote`.
2. **§6.2 X32_Command default IP** — was "default IP 192.168.0.64". Fact: `X32_Command.c` usage text says `192.168.1.62`, but the code never uses either; with no `-i` it takes the host's first address, sets the last octet to `0xff` and enables `SO_BROADCAST` (local /24 broadcast). README line 91's `192.168.0.64` is stale. Always pass `-i`.
3. **§6.2 Windows build line** — was "`gcc ... -lX32_lib -lws2_32`". Fact: the repo `Makefile` (Javier Iglesias, Linux) links `-LX32lib -lm -lX32`; no `-lX32_lib`, no `-lws2_32`. Windows linking (`w2_32` etc.) is described in README line 32 for the author's Eclipse/MinGW setup.
4. **§6.3 meter float counts** — `/meters/13`–`/16` were listed as "also present" without numbers. Fact: 48, 80, 50, 48 (`X32.c` 4918–4927). Added the verbatim `Xprepmeter()` blob-building code, a worked byte example for `/meters/0` (size `00 00 01 1C`, count `46 00 00 00`, 304-byte datagram), the 10-second validity timer, the 1–99 time-factor clamp and the one-client-per-meter limitation.
5. **§3.1 misattributed quote** — "Your shell's environment is not there" was attributed to the MCP `connect-local-servers` page. Fact: that page has no such sentence; the SDK's `docs/get-started/real-host.md` lines 96–97 say "Claude Desktop starts your server in its own process, so your shell's environment variables are not there." Quote replaced and re-sourced.
6. **§3.1 `cwd` key** — was UNCONFIRMED. Fact: ignored by Claude Desktop and Claude Code (`anthropics/claude-code` #17565, #42883, #75266 — the last closed as not planned); absent from every official field list. Marked "do not use".
7. **§5.3 Store-stub message** — was "quoted from memory, UNCONFIRMED". Fact confirmed from three independent reproductions; exact text recorded, plus the second `fwlink` line. Added the local evidence (0-byte `ReparsePoint`; `where.exe` order; `py -0p` shows only a 3.8 install on this PC — below `mcp`'s `>=3.10` floor).
8. **§5.1 uv managed-Python directory** — was UNCONFIRMED between `%APPDATA%\uv\python` and `%APPDATA%\uv\data\python`. Fact: current uv source (`uv-dirs`/`uv-state`/`uv-python`) → `%APPDATA%\uv\python` on a fresh machine, with an existing legacy directory honoured; the docs page still says `%APPDATA%\uv\data`. Recorded both and kept `uv python dir` as authoritative.
9. **§2.2 Eutalix wheels** — was UNCONFIRMED whether cp314 exists. Fact: Eutalix's newest release is v2.46.3 (2026-05-01) with **cp310–cp312 only**, and it does not carry the `2.46.5` that pydantic 2.13.5 pins; the fork `Goplr/android-pydantic-core` (index `https://Goplr.github.io/android-pydantic-core/`) has v2.46.5/v2.48.0 (2026-09-05) and claims 3.9–3.14 with PEP 738 dual tags. Asset names of the Goplr release remain unverified (see below).
10. **§1.10 pytest-asyncio endorsement** — was UNCONFIRMED. Fact: the SDK's pyproject and docs never mention `pytest-asyncio`; they use anyio's pytest plugin. Neither endorsed nor discouraged.
11. **§1.7 log handler** (material omission, added): `configure_logging()` installs `RichHandler(console=Console(stderr=True))` when `rich` is importable — which it is under `mcp[cli]` because `typer` requires `rich>=13.8.0` — otherwise a bare `logging.StreamHandler()`; format is `%(message)s` (no level/name/timestamp). Configure logging yourself before `MCPServer()` if you want a conventional format in `mcp-server-x32.log`.
12. **§6.2 second shutdown path** (material omission, added): `/-stat/lock ,i 2` calls `X32Shutdown()` (`X32.c` 4580–4587; changelog 0.78) — saves and exits the emulator.
13. **§6.2 README usage block** — noted that README lines 279–288 omit `-i` (pre-0.75 text); the C usage (907–915) is authoritative.

**Confirmed unchanged (VERIFIED, spot list):** `mcp` 2.2.0 / `>=3.10` / pure wheel / requires_dist verbatim / 1.30.0 newest 1.x; `MCPServer.__init__` signature and positional order; `tool`/`add_tool`/`resource`/`prompt` signatures and line numbers; `run()` overloads and `anyio.run()` default `backend="asyncio"`; `fastmcp.py` shim text; import table incl. `from mcp import Client, MCPError, MCPDeprecationWarning`; structured-output rules and the `get_temperatureOutput` schema; the three error paths (`ToolError`, `MCPError` → JSON-RPC, other → sanitised) and `-32602`/`-32603` resource codes; RFC 6570 templates and the `Mismatch between URI parameters` message; prompt rules; `Context` method index and SEP-2577 deprecation decorators; lifespan quotes and `Context[T]` tool-only rule; `report_progress` no-op semantics; every dependency version/wheel classification in §2 (pydantic-core 2.49.0 / pin 2.46.5, rpds-py 2026.6.3 `>=3.11`, cryptography 50.0.1, python-osc 1.10.2 pure, PyYAML 6.0.3, websockets 17.1 `>=3.11` pure wheel, aiohttp 3.14.3 no pure wheel, pywin32 312 = 21 wheels/no sdist); Termux python 3.14.6, python-rpds-py 2026.6.3, python-cryptography 50.0.1, no pydantic-core package; Claude Desktop config path/logs/JSON/restart/`${APPDATA}` fix; Claude Code `.mcp.json` shape, `type` rule, expansion syntax, scopes, `claude mcp add` syntax, WSL-only import; pytest 9.1.1 / pytest-asyncio 1.4.0 (`pytest<10,>=8.4`, `event_loop` removed in 1.0.0) and all four config options; uv install command/location/0.12.17, PEP 514 registration, discovery order, build-system rule; python.org 3.14.7 / install manager 26.3 / 3.12.10 last installer / 3.12 EOL 2028-10; emulator port 10023, `getopt` string, usage text, `-i` bind logic, `.X32res.rc` persistence, `Xheader[]` table, `/info`/`/xinfo`/`/status` replies, xremote 4 clients × 11 s, meter blob endianness (size big-endian, count little-endian), TODO/not-implemented comments, site links and dates.

**Still UNCONFIRMED after this pass:** §7 items 2 (`cmd /c` on native Windows), 5 (zip contents / DLLs — Google Drive), 7-residual (Goplr v2.46.5 asset names: GitHub page asset list failed to render, API rate-limited), 9 (`CARGO_BUILD_JOBS`), 11 (uv Windows exe name), and the §3.1 note that Claude Desktop spawns without a shell (consistent with docs, not stated).
