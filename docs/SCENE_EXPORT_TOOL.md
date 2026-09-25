# `export_scene_file` — a desk-loadable `.scn` from the server's own dump (spec, 2026-09-25)

*Written by the reviewer after comparing a console-exported scene file with the server's node rendering of the live desk.
Reference file: `docs/research/data/PRE-MCP-BACKUP_2026-09-25.scn` (2117 lines). Note: that file is NOT the live desk of
2026-09-25 — it came from an X32 Edit session that had not been synced from the console (channel names "Windows",
"Ableton", "GLX Mix L", a different routing table) — but it is a genuine console scene file and the format reference.*

## 1. The format

* Line 1: `#4.0# "NAME" "NOTES" %000000000 1` — firmware major, name, notes (quoted), the 9-bit scene-safes bitmap
  (`%` + 9 digits, cf. `/-show/showfile/scene/NNN/safes`), and a trailing `1`. LF line endings, ASCII.
* Then one node line per section, exactly the `/node` text the server already renders with `nodes.render_node_line`:
  of the 2017 paths common to the file and the server's dump, **1789 were byte-identical** and every remaining difference
  was a value (the file being a different state), none a format difference.
* Family order in the file: `config, ch, auxin, fxrtn, bus, mtx, main, dca, fx, outputs, headamp`. Within `config` the
  desk's own order (chlink, auxlink, fxlink, buslink, mtxlink, mute, linkcfg, mono, solo, talk, osc, userrout/in,
  userrout/out, routing/IN, routing/AES50A, routing/AES50B, routing/CARD, routing/OUT, …). The server's dump order is
  family-by-descriptor; the export must emit in the file's order (simplest: keep a `scene_order` list of path templates
  derived from this file in the descriptor and sort by it).
* Not scene data, must be excluded: everything under `/-stat`, `/-prefs`, `/-show`, `/-action`.

## 2. What the descriptor does not model yet (100 lines + 32 slots)

| pattern | lines | example from the file |
|---|---|---|
| `/ch/NN/automix` for all 32 channels (the descriptor has ch 01–08 only, and no node section) | 32 | `/ch/01/automix OFF  +0.0` |
| `/outputs/main/NN/delay` | 16 | `/outputs/main/01/delay OFF   0.3` |
| `/outputs/p16/NN` | 16 | `/outputs/p16/01 26 <-EQ OFF` |
| `/outputs/p16/NN/iQ` | 16 | `/outputs/p16/01/iQ OFF none Linear 0` |
| `/outputs/aes/NN`, `/outputs/rec/NN` | 2 + 2 | `/outputs/aes/01 1 POST OFF`, `/outputs/rec/01 1 <-EQ` |
| `/config/osc` | 1 | `/config/osc -40.0 100.2 1k00 F1 PINK 0` (level dB, f1, f2, fsel, type, dest) |
| `/config/userctrl/{A,B,C}` + `/enc` + `/btn` | 6+ | `/config/userctrl/A CY`, `/config/userctrl/A/enc "X001" "X101" "X201" "X300"` |
| `/config/userrout/out` slots 17–48 (the descriptor has 16; the desk prints 48) | +32 fields | `/config/userrout/out 1 2 3 4 161 162 7 8 … (48 values)` |

Each is a descriptor addition in the style of its neighbours (`docs/research/fx_routing_scenes.md` documents most of the
enums) plus node sections, with the pinned counts in `tests/test_descriptor.py`, `tests/test_device_yaml.py`,
`tests/test_nodes.py` updated.

## 3. The tool

```
export_scene_file(name: str, notes: str = "", file: str | None = None, safes: str = "%000000000") -> {ok, path, lines, missing}
```

Tier 0 (a read of the desk, a file under `X32MCP_HOME/scenes/<name>.scn` unless `file` is given). Dumps the desk
(`Desk.dump`), renders the scene families in the desk's order, writes the header and the lines. `missing` lists any
section the desk did not answer, so a partial file is never silent. Test: render the FakeDesk, parse every line back with
the node parser, and diff the path order against the reference file's order. Before it is trusted as a backup: export
from the live desk, save the same state to a console slot, export that slot from X32 Edit (synced), and diff the two.

## 4. Why it matters

A `.scn` is the backup the operator can load from the console's own USB port or X32 Edit without this server. The
server's JSON snapshots (`snapshot_desk` / `restore_snapshot`) already cover the undo case, and the console's scene slots
cover the console-side case; the file export closes the loop for a desk this server is not connected to.
