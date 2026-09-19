# Patch plans

A patch plan is the input list of one band as data: which channel carries what, how the strip is
named and coloured on the desk, which physical input feeds it, and the facts the desk cannot hold
but CFS² (feedback watch / ring-out) and the mic-discovery preflight need — whether the channel is a
live stage **mic**, whose it is (**owner**) and which **monitor bus** that person listens to.

Files live in this directory (`patches/`, `Settings.patch_dir`) and are used by the
`apply_patch_plan(file, include_source=False)` and `export_patch_plan(file)` tools, by
`discover_mics(bus, patch_file=…)` and by `feedback_watch` / `ring_out` (`patch_file=…`). They are
also readable as the MCP resource `x32://patches/{name}`.

`example_band.yaml` is a complete, fictional 16-channel example.

## Two forms

### YAML (`.yaml` / `.yml`)

```yaml
band: The Molecules                    # optional
venue: Festival main stage             # optional
channels:
  - channel: 1
    name: Kick
    color: RD
    source: In01
    mic: true
    owner: Ray
    monitor_bus: 2
    notes: inside kick, dynamic
  - {channel: 13, name: Vox Lead, color: MG, source: In13, mic: true, owner: Tony, monitor_bus: 1}
```

A bare list of rows (no `band`/`venue`/`channels` wrapper) is also accepted.

### CSV (`.csv`)

```
channel,name,color,source,mic,owner,monitor_bus,notes
1,Kick,RD,In01,true,Ray,2,inside kick
13,Vox Lead,MG,In13,yes,Tony,1,
16,Talkback,OFF,In16,no,,,
```

Header names are case-insensitive; `colour` and `monitor bus` are accepted for `color` and
`monitor_bus`. Blank cells mean "not given". Unknown columns are ignored with a warning. UTF-8
(a BOM is tolerated); the CSV form has no band/venue.

## Fields

| field | required | value |
|---|---|---|
| `channel` | yes | 1..32, unique within the file (`ch 5` / `5` both work) |
| `name` | yes | strip name; the desk keeps 12 characters — longer names are applied truncated and reported (`truncated`) |
| `color` | no | desk token `OFF RD GN YE BL MG CY WH` (+ `i` suffix = inverted, e.g. `RDi`), any case; or `red green yellow blue magenta cyan white off` (add `i` / ` inv` / ` inverted` for the inverted variant); or the raw index 0..15 |
| `source` | no | input source: `In01`..`In32` (`in 1`, `IN01`), `Aux1`..`Aux6`, `USBL`/`USBR`, `FX1L`..`FX4R`, `Bus01`..`Bus16`, `OFF`, or the raw index 0..64 the desk prints |
| `mic` | no | `true`/`false`, `yes`/`no`, `y`/`n`, `on`/`off`, `1`/`0`, `x` (= true); blank = unknown |
| `owner` | no | who plays/sings into it (first names are enough) |
| `monitor_bus` | no | 1..16 — the mix bus (wedge / IEM) that owner listens to |
| `notes` | no | free text |

Values are normalised on load: `color` becomes the desk token (`red` → `RD`), `source` the desk
token (`In01` → `IN01`). Every problem in the file is reported at once (`PatchError`).

### What `mic` means

`mic: true` marks a live microphone on stage — something that can feed back and that a ring-out
should include. DIs, line inputs, playback and the FOH talkback are `mic: false`. Leave it blank
when you do not know; the preflight then falls back to desk state (unmuted, physical preamp
source, send to the bus above the floor) and asks.

## Applying

`apply_patch_plan(file)` labels every channel in the plan — **name and colour only** (Tier 1, mix
moves: snapshot-before-write and rate-limited like any other write). Only values that differ from
the desk are written, so re-applying a plan is free and does not disturb an operator on X32-Edit.

Input sources (`/ch/NN/config/source`) are routing and therefore **Tier 2**: they are written only
with `include_source=true`, which needs the usual confirmation token; without it the tool reports
the channels whose source it left alone (`skipped_source`). Nothing else on the desk is touched:
no faders, mutes, sends, EQ, dynamics or icons.

The result lists `applied` (with what `changed` per channel), `unchanged`, `truncated`, `failed`
(per-channel desk errors; the run continues, except that a lost connection aborts) and a
one-line `summary`.

## Exporting

`export_patch_plan(file)` reads name, colour and source of all 32 channels from the desk and
writes them as YAML or CSV (by extension). Passing an existing plan as `merge_with` (the tool does
this when the target file already exists) carries `mic`, `owner`, `monitor_bus`, `notes`, `band`
and `venue` over from it, so the exported file remains the one place those facts live. Channels
the desk did not answer for raise `TIMEOUT` rather than silently exporting blanks.

## Python API (`x32mcp.patches`)

```python
plan = load_patch_plan("patches/example_band.yaml")      # PatchPlan(rows=[PatchRow…], band, venue, source_file, warnings)
plan.row(13).owner                                        # "Tony"
plan.mics()                                               # rows with mic == True
plan.for_bus(2)                                           # rows whose monitor_bus == 2
await apply_patch_plan(desk, plan)                        # names + colours
await apply_patch_plan(desk, plan, include_source=True)   # + sources (after the confirmation dance)
await export_patch_plan(desk, "patches/tonight.csv", merge_with=plan)
```
