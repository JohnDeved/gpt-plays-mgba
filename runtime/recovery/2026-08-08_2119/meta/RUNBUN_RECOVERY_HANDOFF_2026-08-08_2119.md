# Pokémon Run & Bun — Recovery Handoff

Timestamp: 2026-08-08 21:19 Europe/Berlin

## Authoritative checkpoint

`checkpoints/r109_double_post_nido_replacements.ss0`

This is the furthest verified Route 109 state recovered from the previous full runtime archive. It is later than the older 16:11 handoff that described a Flaaffy mid-battle state.

## Current progression

- Steven's Letter delivered in Granite Cave.
- Briney voyage to Slateport/Route 109 completed.
- Party had been raised to the Dewford cap (Lv17): Grotle, Staravia, Venipede.
- Route 106 trainers were cleared before Slateport.
- Route 109 progression advanced into a double battle.
- Earlier RAM inspection identified opponents Flaaffy + Luxio and active Grotle + Venipede; later checkpoint chain progressed through Nidorina replacement/KO handling.

## Resume procedure

1. Restore the normal README mGBA/Xvfb/RPC setup.
2. Load `checkpoints/r109_double_post_nido_replacements.ss0`.
3. Inspect party + double-battle battler RAM before issuing input.
4. Continue from the exact live command/replacement state; do not replay the older Flaaffy-only handoff.
5. Save a fresh physical checkpoint immediately after the double battle resolves.

## Included recovery material

- The authoritative `.ss0` and matching screenshot.
- The prior full 18 MB recovery archive, preserving scripts/code/config and all intermediate savestates.
- SHA256 manifest and machine-readable latest-state metadata.

The ROM and emulator binaries are still intentionally not duplicated inside the recovery archive; restore them from their existing Drive sources as documented by the project.
