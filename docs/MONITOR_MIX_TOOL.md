# Monitor-mix and routing tools — spec for the next product track

*Written 2026-09-25 by the reviewer from Jim's request that day: "ultimately I want all the functionality available in X32 Edit
to be available to Claude, so I can say things like 'create a sane default mixbus for a guitarist with IEMs plugged into
output 4 on the SD16'. Claude will know the channel mappings already, and be able to do all the routing needed." For the
frontier model to design and build; the reviewer will write the acceptance tests against the FakeDesk.*

## 1. What the server cannot do today (2026-09-25)

The desk-side job that happens before every rehearsal and gig — a visiting band's monitor mixes — is entirely manual.
The server has `set_send` (levels only), `label_channel` (channels only), `set_channel_config` (input source, channel
link), `apply_patch_plan` (names / colours / sources from `patches/*.yaml`, which already carry `owner` and
`monitor_bus` per channel). It has **no** tool for:

| Need | Address family (all in `device.yaml`, verified in `docs/research/fx_routing_scenes.md`) | Tier |
|---|---|---|
| Name / colour a mix bus | `/bus/NN/config/name` (≤ 12 chars), `/bus/NN/config/color` | 1 |
| Stereo-link a bus pair | `/config/buslink/N-M` (odd–even pairs; re-syncs the pair) | 2 |
| Send tap point (pre/post fader) | `/ch/CC/mix/NN/type` enum `send_type` on odd sends (the even send of a linked pair follows), `/ch/CC/mix/NN/panFollow`, `/ch/CC/mix/NN/pan` | 1 |
| Send on/off | `/ch/CC/mix/NN/on` | 1 |
| Put a bus on a physical output | `/outputs/main/TT/src` (the 16 "OUT" taps: 4..19 = MixBus 1..16, 1/2 Main L/R, 20..25 Mtx, 26..57 direct outs) and `/outputs/main/TT/pos` | 2 |
| Which taps reach which connector | `/config/routing/OUT/{1-4,5-8,9-12,13-16}` (local XLR blocks, list A/B enums) and `/config/routing/AES50A|AES50B/<block>` (SD16/S16 outputs: value 20 = taps OUT1-8, 21 = OUT9-16) | 2 |
| Bus EQ / dynamics / insert | already covered for reads; `set_eq_band` / `set_comp` accept bus targets | 1 |

On X32RACK-Jim today: XLR outputs 1–6 carry taps OUT1–6 = MixBus 1–6, outputs 7–8 = Main L/R (`/outputs/main/01..08/src`
= 4,5,6,7,8,9,1,2); buses 1–12 are unnamed ("MixBus N"), 13–16 are "Fx 1–4"; nothing is linked. An SD16 on AES50-A with
`/config/routing/AES50A/1-8 = 20` puts taps OUT1–8 on the box's outputs 1–8, so "SD16 output 4" = tap `/outputs/main/04`.

## 2. The tool

```
setup_monitor_mix(bus, *, name=None, color=None, owner=None, template="generic", output=None,
                  stereo_with=None, pre_fader=True, levels=None, dry_run=True, confirm_token=None)
```

* `bus` 1..16; `owner` a name from the current patch plan (`patches/<band>.yaml`, `owner` column) — the person the mix is
  for; `template` one of `vocalist | guitarist | bassist | drummer | keys | generic`, the starting balance (§3).
* `output`: `("xlr", 4)`, `("sd16", 4)` / `("aes50a", 4)`, `("aes50b", n)`, `("p16", n)` or None (leave the patch alone).
* `stereo_with`: the even partner bus for a stereo IEM mix (links the pair, pans follow).
* `levels`: overrides `{channel_or_name: dB}` on top of the template; `pre_fader` sets every send's tap to PRE.
* **Tier 2 confirmation dance** like `apply_patch_plan(include_source=True)`: the first call (and any `dry_run=True` call)
  returns the full plan — every address and value that would change, grouped as *bus config*, *sends* (a table: channel,
  name, owner, current → new level, tap), *link*, *output patch*, *routing block* — plus warnings; the second call with
  the token applies it through `Desk` (pre-write snapshot as usual) and returns what was written and what already matched.
* Never touches Main, never changes a routing block that would move an output somebody else is using without saying so in
  the plan (a routing block re-patches eight outputs at once: list which ones change), never writes a send hotter than 0 dB.
* Idempotent: running it again on the same bus proposes nothing.
* Companion reads: `get_monitor_mix(bus)` — the sends table with owners and taps, the link state, which connectors the bus
  reaches (the tap/routing resolution in `fx_routing_scenes.md` §4.6 already exists as `provision.outputs_for`-style
  logic for the RTA source); the dashboard shows it.

## 3. "Sane default" templates

A starting balance, not a mix; pre-fader so the FOH faders do not move the ears. Levels relative to the owner's own
channels at 0 dB reference (they are written as absolute send levels):

| template | own channels | lead vocal | other vocals | kick / snare | rest of kit | bass | guitars | keys | click / talkback | everything else |
|---|---|---|---|---|---|---|---|---|---|---|
| vocalist | −3 | (own) | −8 | −12 | −18 | −12 | −14 | −14 | −6 | off |
| guitarist | −6 | −8 | −14 | −10 | −16 | −10 | (own) | −16 | −6 | off |
| bassist | −6 | −8 | −14 | −6 | −14 | (own) | −14 | −16 | −6 | off |
| drummer | −6 | −8 | −14 | (own) | (own) | −8 | −14 | −16 | 0 | off |
| keys | −6 | −8 | −14 | −12 | −18 | −12 | −14 | (own) | −6 | off |
| generic | −6 | −8 | −14 | −12 | −18 | −12 | −14 | −16 | −6 | off |

Channel roles come from the patch plan (`owner`, name keywords, `mic`), with a `role` column added to the plan format
(`vocal | guitar | bass | kick | snare | drums | keys | click | talkback | playback | other`) so the templates do not guess
from names. Anything unresolved is proposed at "off" and listed under warnings.

## 4. Behind it: the X32 Edit parity list

What X32 Edit does that the server does not, in the order the studio needs it: (1) the above; (2) bus / matrix EQ,
dynamics and insert setting (writes exist for channels and buses through `set_eq_band` / `set_comp`; matrices and the
insert selector have no tool); (3) DCA and mute-group membership; (4) scene / snippet safes; (5) the routing pages in
full (input blocks, AES50, card, user-in); (6) headamp gain / phantom for the stage box (`/headamp/{n}`, Tier 2); (7)
talkback and monitor source; (8) FX slot type and parameters beyond the GEQ. Each is a descriptor-backed tool with a plan
/ confirm shape like `apply_patch_plan`, and each gets a FakeDesk test before it touches the desk.
