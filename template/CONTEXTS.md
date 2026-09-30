# Contexts

This file is the registry of every context you work in from this root. `CLAUDE.md` says how it is
used. Edit by hand or with the `new-context` skill. Keys per block: `kind` (whatever categories
suit your work), `status` (its stage of life, for example active, dormant, stub or known),
`aliases` (comma separated, case-insensitive), `cloudctx` (the scope name or names your
per-context credential wrapper takes, each optionally followed by a detail in parentheses, or
`none`), `home` (folder relative to this root, or none), `iac_names` (names under an
infrastructure layout, if you have one), `owns` (extra folders, one per line), `rules` (standing
rules, one per line). Only blocks below the registry marker are contexts. A block's own name is a
single token: no spaces (`set_context.sh` writes it into a session marker's filename and refuses
one that has a space). Aliases never reach that script and may contain spaces; they usually
should, since aliases exist to match how you actually say the thing.

Delete the examples once you have your own. Until you do, the drift check will list them as blocks
whose home folder does not exist: that is expected, not a fault, since the examples are
illustrations with no folder on disk. The `ignore` block at the end lists paths that are
deliberately not contexts, so the drift check stays quiet about them.

## IaC layout (optional, repos/infra)

Keep this table only if you have infrastructure as code shared across contexts. `<iac_name>` comes
from a context's `iac_names`. Resolve folders at activation with
`find repos/infra -maxdepth 4 -type d -path '*/environments/*' -iname '<iac_name>*'`.
Set `"iac": {"root": "repos/infra"}` in `.claude/kit.json` and the guard treats each context's
own `environments/<stage>/<iac_name>*` folders as belonging to that context, even when they sit
inside a folder another context owns. Do not make the whole infrastructure folder one context's
`home`: give the shared-code context only the shared parts (the module repo, `_base`).

| Family | Repos | Per-context path | Shared paths (platform context; editing them affects every context in the repo) |
|---|---|---|---|
| Environments | infra-environments | environments/<stage>/<iac_name>/ | environments/_base/, root *.tf, .github/workflows/ |
| Module registry | infra-modules | none | modules/** (consumed by every environment repo via a pinned tag) |

Blast radius, highest first: infra-modules/modules, infra-environments root *.tf and _base,
per-repo .github/workflows.

<!-- registry -->

## northwind
kind: client
status: active
aliases: nw, northwind traders
cloudctx: northwind (Azure, prod tenant)
home: clients/northwind
iac_names: northwind
rules:
  - read-only by default; anything that changes their environment needs explicit approval

## globex
kind: client
status: active
aliases: globex corp
cloudctx: globex (AWS, prod account), globex-dev (AWS, dev account)
home: clients/globex
iac_names: globex
rules:
  - two scopes: confirm which one before any cloud call

## platform
kind: platform
status: active
aliases: shared, modules
cloudctx: none
home: repos/infra/infra-modules
iac_names:
owns:
  - repos/infra/infra-environments/environments/_base
rules:
  - name every consumer affected before editing anything shared

## sideproject
kind: personal
status: dormant
aliases:
cloudctx: none
home: personal/sideproject
iac_names:

## ignore
paths: scratch
