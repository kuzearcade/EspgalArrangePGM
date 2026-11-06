"""Generate the MAME driver patch that adds an `espgalarr` set to src/mame/igs/pgm.cpp.

The patch is produced from the build manifest so the hashes are always right. It clones the
espgalbl definition (init_ddp2, DDP2 donor ARM) with our ROM files and uses the `espgalarr`
machine config from patches/mame/enhanced-sprites.diff (pgm_arm_type2 + the enhanced sprite
engine that reads the continuation sprite list in cart ARM RAM, the 0xE00000 RAM and the BGM stream
device).
"""
from __future__ import annotations

import json
from pathlib import Path

ROM_START = '''ROM_START( espgalarr ) // Arrange-mode conversion built by espgalarr (DDP2 donor cart, like espgalbl)
	ROM_REGION( 0x600000, "maincpu", 0 ) /* 68000 Code */
	PGM_68K_BIOS
	ROM_LOAD16_WORD_SWAP( "espgaluda_u8.bin", 0x000000, 0x400000, CRC({p_crc}) SHA1({p_sha}) )

	ROM_REGION( 0x4000, "prot", 0 ) /* ARM protection ASIC - internal ROM */ // leftover from donor
	ROM_LOAD( "ddp2_igs027a_japan.bin", 0x000000, 0x04000, CRC(742d34d2) SHA1(4491c08f3cefef2933ad5a741f4bb05cc2f3e1a0) )

	ROM_REGION32_LE( 0x400000, "user1", 0 ) /* Protection Data (encrypted external ARM data) */ // leftover from donor
	ROM_LOAD( "v100.u23", 0x000000, 0x20000, CRC(06c3dd29) SHA1(20c9479f158467fc2037dcf162b6c6be18c91d46) )

	ROM_REGION( 0xa00000, "igs023", 0 ) /* 8x8 Text Tiles + 32x32 BG Tiles */
	PGM_VIDEO_BIOS
	ROM_LOAD( "cave_t04801w064.u19",   0x180000, 0x800000, CRC({t_crc}) SHA1({t_sha}) )

	ROM_REGION16_LE( 0x1000000, "igs023:sprcol", 0 ) /* Sprite Colour Data */
	ROM_LOAD( "cave_a04801w064.u7",    0x0000000, 0x0800000, CRC({a0_crc}) SHA1({a0_sha}) )
	ROM_LOAD( "cave_a04802w064.u8",    0x0800000, 0x0800000, CRC({a1_crc}) SHA1({a1_sha}) )

	ROM_REGION16_LE( 0x0800000, "igs023:sprmask", 0 ) /* Sprite Masks + Colour Indexes */
	ROM_LOAD( "cave_b04801w064.u1",    0x0000000, 0x0800000, CRC({b_crc}) SHA1({b_sha}) )

	ROM_REGION( 0x1000000, "ics", ROMREGION_ERASE00 ) /* Samples - streamed Arrange BGM + original SFX */
	PGM_AUDIO_BIOS
	ROM_LOAD( "cave_w04801b032.u17",    0x400000, {m_size:#x}, CRC({m_crc}) SHA1({m_sha}) )
{bgm}ROM_END
'''

# the lossless BGM (--bgm adx): the PS2's ADX streams for the espgalarr_bgm stream device at Z80 I/O
# 0x8800 (patches/mame/enhanced-sprites.diff, src/mame/igs/espgalarr_bgm.cpp); optional, so builds
# without it (--bgm ulaw) still run
BGM_REGION = '''
	ROM_REGION( 0x8000000, "bgm_stream", ROMREGION_ERASE00 ) /* espgalarr enhanced sound: the PS2 Arrange ADX streams (espgalarr_bgm.cpp) */
	ROM_LOAD_OPTIONAL( "espgalarr_bgm.bin", 0x000000, {size:#x}, CRC({crc}) SHA1({sha}) )
'''

GAME_LINE = ('GAME( 2026, espgalarr,    espgal,    espgalarr,              pgm,       pgm_arm_type2_state, init_ddp2,     ROT270, '
             '"hack (espgalarr)",   "Espgaluda (PS2 Arrange mode conversion, DDP2 cart)", MACHINE_IMPERFECT_SOUND | MACHINE_SUPPORTS_SAVE )\n')


def render(manifest: dict) -> str:
    f = manifest["files"]

    def crc(n):
        return f[n]["crc32"]

    def sha(n):
        return f[n]["sha1"]
    text = ROM_START.format(
        p_crc=crc("espgaluda_u8.bin"), p_sha=sha("espgaluda_u8.bin"),
        t_crc=crc("cave_t04801w064.u19"), t_sha=sha("cave_t04801w064.u19"),
        a0_crc=crc("cave_a04801w064.u7"), a0_sha=sha("cave_a04801w064.u7"),
        a1_crc=crc("cave_a04802w064.u8"), a1_sha=sha("cave_a04802w064.u8"),
        b_crc=crc("cave_b04801w064.u1"), b_sha=sha("cave_b04801w064.u1"),
        m_crc=crc("cave_w04801b032.u17"), m_sha=sha("cave_w04801b032.u17"),
        m_size=f["cave_w04801b032.u17"]["size"],
        bgm=BGM_REGION.format(size=f["espgalarr_bgm.bin"]["size"], crc=crc("espgalarr_bgm.bin"),
                              sha=sha("espgalarr_bgm.bin")) if "espgalarr_bgm.bin" in f else "",
    )
    return text


def write_patch(manifest_path: Path, out_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    body = render(manifest)
    # Unified-diff style instructions (anchors on the espgalbl definitions) rather than a
    # line-numbered diff, so it applies to any recent MAME: see README "MAME patch".
    patch = ("# 0. apply patches/mame/enhanced-sprites.diff to the MAME tree (git apply; made against 0.289)\n"
             "# Apply to src/mame/igs/pgm.cpp of MAME >= 0.285:\n"
             "#  1. insert the ROM_START block below right after `ROM_START( espgalbl ) ... ROM_END`\n"
             "#  2. insert the GAME line below right after the `GAME( 2003, espgalbl, ...` line\n"
             "#  3. rebuild MAME; `mame -verifyroms espgalarr` must report the set is good.\n\n"
             + body + "\n" + GAME_LINE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(patch)


def update_tree(manifest_path: Path, mame_dir: Path) -> bool:
    """Write the current espgalarr ROM_START block and GAME line into a MAME tree's pgm.cpp
    (one that already carries them, e.g. after a first manual application). Returns True if
    the file changed; the engine diff (enhanced-sprites.diff) is applied separately."""
    import re
    drv = Path(mame_dir) / "src" / "mame" / "igs" / "pgm.cpp"
    text = drv.read_text()
    block = render(json.loads(Path(manifest_path).read_text()))
    new = re.sub(r"ROM_START\( espgalarr \).*?ROM_END\n", lambda m: block, text, count=1, flags=re.S)
    new = re.sub(r"GAME\( 2026, espgalarr,.*\n", lambda m: GAME_LINE, new, count=1)
    if new != text:
        drv.write_text(new)
    return new != text
