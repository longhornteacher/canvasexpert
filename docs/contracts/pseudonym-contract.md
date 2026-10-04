# Canonical pseudonym contract

Status: authoritative for every Canvas Expert surface that assigns, stores, displays,
scrubs to, or accepts a student pseudonym.

## Teacher-visible outcome

Each student has one stable, unique, single-word pseudonym. The word is a Pokemon
species name. It is never a human-style first/last name or a sequential identifier.

Pseudonymized means identity-reduced, not anonymous. The existing review and outbound
safety boundaries still apply.

## Canonical vocabulary

`api/data/pseudonym_words.json` is the only vocabulary source. It has exactly one
top-level category array: `pokemon`, holding the National Pokedex species 1-1025 in
dex order, minus the omissions below.

- The flattened registry contains at least 256 unique words.
- Every entry is one ASCII alphabetic token in title case, with no whitespace,
  punctuation, digits, or suffix generation.
- Each word appears in the registry exactly once, compared case-insensitively.
- Every entry must be a real Pokemon species name that is spelled as one plain ASCII
  token. Species whose canonical spelling needs an apostrophe, accent, digit, hyphen, or
  space (Farfetch'd, Flabebe, Porygon2, Ho-Oh, Mr. Mime, Tapu Koko, the Gen 9 paradox
  names) are omitted rather than respelled. Human-style personal names are forbidden.
- Some species are permanently excluded because the word itself is not an acceptable
  thing to call a student, whatever the teacher's intent. A pseudonym is attached to a
  child's work in front of that child. The excluded set is `Jynx` (its original design is
  a racial caricature), the trash, sludge, and stink species `Grimer`, `Muk`, `Trubbish`,
  `Garbodor`, `Stunky`, and `Skuntank`, and `Hypno` (its Pokedex lore is about carrying
  off children). It also excludes names that directly evoke body-shaming through size,
  gluttony, sumo, or elephant/hippo/whale/pig imagery: `Snorlax`, `Swinub`, `Piloswine`,
  `Phanpy`, `Donphan`, `Miltank`, `Makuhita`, `Hariyama`, `Gulpin`, `Swalot`, `Wailmer`,
  `Wailord`, `Purugly`, `Munchlax`, `Hippopotas`, `Hippowdon`, `Lickilicky`, `Mamoswine`,
  `Tepig`, `Pignite`, `Emboar`, `Guzzlord`, `Greedent`, `Cufant`, `Copperajah`, `Lechonk`,
  `Oinkologne`, `Cetoddle`, and `Cetitan`. Preserve this exclusion set; add to it
  rather than removing from it. Names that
  directly imply poor intelligence or uselessness are also excluded: `Slowpoke`,
  `Slowbro`, `Slowking`, `Numel`, `Magikarp`, and `Wobbuffet`.
- The registry is an allowlist for new assignments. Code must never fall back to a word
  outside it.

Registry shape and uniqueness are validated when loaded. An invalid or exhausted registry
fails closed with an actionable local error; it never emits a placeholder or numbered word.

## Assignment and permanence

- The Identity Vault is the private source of truth and keys identity by stable Canvas
  user ID.
- First assignment chooses a registry word from an HMAC of the Canvas user ID under the
  teacher's shared pseudonym secret, skipping words already recorded. A computer without
  that secret refuses to assign (`pseudonym_secret_not_configured`); see
  `docs/guides/more-than-one-computer.md`.
- A new pseudonym must not case-fold to any real roster-name token supplied for collision
  avoidance.
- Pseudonyms are permanent. Once recorded, a pseudonym never changes, and nothing renames
  or regenerates it. A recorded word that later leaves the registry stays valid.
- Pseudonyms are unique across the whole vault under case-folded comparison. When two
  computers record the same word for different students before syncing, the earliest
  assignment wins and the other student is provisional; scoring work for that student
  refuses with `pseudonym_provisional` until it is resolved.
- A pseudonym is always transported and compared as the exact stored string. Consumers do
  not split, decorate, concatenate, or infer a second name component.

## Private vault storage

The vault lives in the synced workspace at `_Shared/vault/`: an immutable `seed.v1.json`,
an immutable `pokemon.v1.json` copy of the registry, and one append-only
`journal.<machine>.jsonl` per computer. Journal events either assign a pseudonym or record
private identity fields (real name, SIS ID, nicknames, first seen). A missing vault starts
from an empty seed and is populated by the normal roster upsert. Runtime code does not
migrate or dual-read an older vault, and a file at a retired vault location blocks
student-data access without being opened.

## Consumer rules

- Names displays the exact one-word value in the private console table. MCP roster tools
  identify a student by the exact `pseudonym` string.
- Scrubbing maps the full real name, every real-name token, and each nickname to the same
  full one-word pseudonym. IDs continue to map to the neutral ID placeholder.
- SAFE artifacts, PowerGrader, CanvasMirror projections, and MCP
  payloads carry the same stored string. None owns a second pseudonym vocabulary or schema.
- Re-identification remains local and exact through the private vault reverse index.

## Privacy and logging

Pseudonyms, mappings, roster tokens, and private-vault contents are student data. Tests use
fictional registry words and IDs only. Generic logs and execution reports may contain counts
and error classes, never mappings or actual pseudonym values from the configured vault.

The teacher may store the Identity Vault and other private student records in the private
M365 OneDrive workspace so they sync across the teacher's devices. Storage in that tenant
does not change the agent boundary: local services may re-identify records for teacher
workflows, while agent-facing MCP results remain pseudonymized and safety-scanned. MCP
responses and operational logs never return real names, Canvas/SIS IDs, vault contents, or
private filesystem paths.
