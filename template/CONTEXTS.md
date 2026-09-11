# Contexts

This file is the registry of every context you work in from this root. `CLAUDE.md` says how it is
used. Edit by hand or with the `new-context` skill. Keys per block: `kind` (whatever categories
suit your work), `status` (its stage of life, for example active, dormant, stub or known),
`aliases` (comma separated, case-insensitive), `cloud` (per-context cloud or account scoping, or
none), `home` (folder relative to this root, or none), `iac_names` (names under an infrastructure
layout, if you have one), `owns` (extra folders, one per line), `rules` (standing rules, one per
line). Only blocks below the registry marker are contexts. A block's own name is a single token:
no spaces (`set_context.sh` writes it into a session marker's filename and refuses one that has a
space). Aliases never reach that script and may contain spaces; they usually should, since aliases
exist to match how you actually say the thing.

Delete the three examples once you have your own. Until you do, the drift check will list those
three as blocks whose home folder does not exist: that is expected, not a fault, since the examples
are illustrations with no folder on disk. The `ignore` block at the end lists paths that are
deliberately not contexts, so the drift check stays quiet about them.

<!-- registry -->

## northwind
kind: client
status: active
aliases: nw, northwind traders
cloud: northwind (prod)
home: clients/northwind
iac_names:
rules:
  - read-only by default; anything that changes their environment needs explicit approval

## platform
kind: platform
status: active
aliases: shared, modules
cloud: none
home: repos/platform
iac_names:
owns:
  - repos/platform/modules
rules:
  - name every consumer affected before editing anything shared

## sideproject
kind: personal
status: dormant
aliases:
cloud: none
home: personal/sideproject
iac_names:

## ignore
paths: scratch
