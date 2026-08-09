# Route 109 Tuber Hailey execution

- Target: `map:0:24/local:21`; script `0x08223e10`; trainer ID `697`; live format `single`.
- Pre-contact checkpoint: `route109-local21-precontact-forward-20260809.ss` (`e0804eb153db4232092657cfc7044bdb58b100396d4fded101c72aef4ec03a04`).
- Victory checkpoint: `route109-tuber-hailey-defeated-forward-20260809.ss` (`4691a9c341663414382753b247886147eb547eaeffde64c9e6c6567625b553fd`).
- Authoritative completion: battle text reported `Player defeated Tuber Hailey!`; final RAM had Route 109 `(0,24)`, `battle_active=false`, and `field_message_box_mode=0`.

## Pre-action certificate and line

- Initial Grotle was 13/57 against Mienfoo 40/40. Direct attacks left a guaranteed incoming Force Palm KO; switch to Venipede was minimax/expected-best.
- Venipede took Force Palm 4, used Poison Tail, and poisoned Mienfoo. Mienfoo used Rock Tomb for 18 and lowered Venipede Speed; switch to Onix, then Phanpy, kept the remaining party alive but exposed Phanpy to the next send-out timing.
- Mienfoo was defeated by the delayed queued Phanpy Bulldoze. Nidorina then used Water Pulse before the queued action resolved and fainted Phanpy from 20 HP.
- Forced replacement certificate selected full-health Cufant over Staravia, Grotle, Venipede, and Onix for Nidorina's Water Pulse/Venoshock coverage.
- Cufant used Bulldoze twice to defeat Nidorina. Flaaffy entered; Cufant's first Bulldoze left Flaaffy at 13 and Thunder Wave paralyzed Cufant, so the controller switched to Onix.
- Onix's Rock Tomb twice defeated Flaaffy. Onix ended at 40/44; Cufant ended at 35/56 and paralyzed; Phanpy was the only fainted party member.

## Model mismatches retained as experience

- Mienfoo Rock Tomb → Venipede: observed 18, above the prior 10–13 estimate.
- Cufant Bulldoze → Nidorina: observed 30 on the first hit, above the prior 11–15 estimate.
- Cufant Bulldoze → Flaaffy: observed 34, below the prior 52–62 estimate.
- Onix Rock Tomb → Flaaffy: observed 9, below the prior 11–14 estimate.
- A command-menu observation can precede the final queued move by hundreds of emulator frames; accept a turn only after the move text/HP/PP/species transition has drained and a stable command prompt is observed.
