"""build --esprade-chars: Black drawn as ESP Ra.De.'s J-B 5th and Chihiro as its Irori.

Only the player's body frames change; the game (states, timing, hitboxes, shots) is the Arrange
port's. anim.port_animation keeps Arrange's animation state machine (spec/arrange-characters.md
"Player animation": 24 states, frame lists of fixed lengths stepped every reload+1 frames) and
fills its frame lists with ESP Ra.De. frames instead of the PS2's (PlayerFrames.records):

  Arrange state (frame table)              ESP Ra.De. animation (ESP below)
  0 idle (0x2D77F0)                        idle loop
  1 lean right (0x2D7A80: 28 + 8 loop)     roll to the right, then the right hold loop
  2 lean left (own table: mirrored in      roll to the left, then the left hold loop
    Arrange, ESP Ra.De. draws its own)
  5 / 6 lean returns (0x2D91F0; 6 own)     the rolls back to idle
  3 / 4 up / down (11 + 8 loop)            lean up / down, then the up / down hold loop
  7 / 8 their returns                      the returns from up / down
  14 -> 9 -> 15 Power Shot                 laser start, laser hold loop, laser release
  18 -> 19 -> 20 -> 21 -> 22 barrier       guard start, hold loop, end, recover loop, exit
  23 entry / respawn (20-byte records)     the hit tumble and recovery (34 frames, as Arrange's);
                                           the records' timer / speed / voice words stay
                                           Arrange's (they move the player)

A frame list of n records shown reload+1 frames each takes the ESP frames by time: when the
counts match, one to one; otherwise the frame shown at each record's start time. Offsets put
each frame where ESP Ra.De. draws it relative to the player's position point (the hitbox). Each character's frames share one 32-colour palette, quantised from
its ESP palette, loaded into both of the character's body palettes (normal and Kakusei form:
Arrange's two are identical too).

The pixels and palettes are read from the user's esprade.zip (espgalarr/esprade.py); this file
holds only sprite numbers, sizes, timings and ROM addresses.
"""
from __future__ import annotations

import struct

import numpy as np

from . import esprade

# Arrange frame tables (ELF) by state, anim.py
IDLE, LEAN, UP, DOWN, RETURN, UP_RET, DOWN_RET, S9 = (0x2D77F0, 0x2D7A80, 0x2D85D0, 0x2D8BE0, 0x2D91F0,
                                                       0x2D9340, 0x2D9490, 0x2D95E0)
POWER1, POWER2, BAR18, BAR19, BAR20, BAR21, S22, ENTRY = (0x2DA650, 0x2DA840, 0x2DAE10, 0x2DB520, 0x2DB670,
                                                           0x2DB840, 0x2DB990, 0x2DBAC0)
LEAN_L, RETURN_L = LEAN + 1, RETURN + 1        # this mode's own tables for states 2 and 6 (not ELF)
STATE_2, STATE_6 = 2, 6
# body palettes (program ROM, 32 colours): (normal, Kakusei) per character (+0x4A)
BODY_PALETTES = {0: (0xC3D78, 0xC3DB8), 2: (0xC3E38, 0xC3DF8)}
RECORD_OFFSET, RECORD_ADD, RECORD_SPRITE, RECORD_SIZE = 0, 4, 8, 0xC

# ESP Ra.De. player animations (filled in from the program's animation scripts, see ANIMS docs):
# character -> name -> [(code, w, h, dx, dy, frames)], dx/dy = sprite top-left minus the
# player's position (pixels, native orientation), frames = 60 Hz frames shown
ANIMS: dict[str, dict[str, list[tuple[int, int, int, int, int, int]]]] = {
    "jb5": {
        "idle": [(0x2372, 48, 48, -24, -24, 3), (0x237B, 48, 48, -24, -24, 3), (0x2384, 48, 48, -24, -24, 3), (0x238D, 48, 48, -24, -24, 3), (0x2396, 48, 48, -24, -24, 3), (0x239F, 48, 48, -24, -24, 3), (0x23A8, 48, 48, -24, -24, 3), (0x23B1, 48, 48, -24, -24, 3)],
        "lean_left": [(0x20F3, 48, 48, -24, -24, 1), (0x20EA, 48, 48, -24, -24, 1), (0x20E1, 48, 48, -24, -24, 1), (0x20D8, 48, 48, -24, -24, 1), (0x20CF, 48, 48, -24, -24, 1), (0x20C6, 48, 48, -24, -24, 1), (0x20BD, 48, 48, -24, -24, 1), (0x20B4, 48, 48, -24, -24, 1), (0x20AB, 48, 48, -24, -24, 1), (0x20A2, 48, 48, -24, -24, 2), (0x2099, 48, 48, -24, -24, 1), (0x2090, 48, 48, -24, -24, 2), (0x2087, 48, 48, -24, -24, 1), (0x207E, 48, 48, -24, -24, 2), (0x2075, 48, 48, -24, -24, 1), (0x206C, 48, 48, -24, -24, 2), (0x2063, 48, 48, -24, -24, 1), (0x205A, 48, 48, -24, -24, 2), (0x2051, 48, 48, -24, -24, 1), (0x2048, 48, 48, -24, -24, 2), (0x203F, 48, 48, -24, -24, 1), (0x2036, 48, 48, -24, -24, 2), (0x202D, 48, 48, -24, -24, 1), (0x2024, 48, 48, -24, -24, 2), (0x201B, 48, 48, -24, -24, 1), (0x2012, 48, 48, -24, -24, 2), (0x2009, 48, 48, -24, -24, 1), (0x2000, 48, 48, -24, -24, 1)],
        "left_hold_loop": [(0x23BA, 48, 48, -24, -24, 3), (0x23C3, 48, 48, -24, -24, 3), (0x23CC, 48, 48, -24, -24, 3), (0x23D5, 48, 48, -24, -24, 3), (0x23DE, 48, 48, -24, -24, 3), (0x23E7, 48, 48, -24, -24, 3), (0x23F0, 48, 48, -24, -24, 3), (0x23F9, 48, 48, -24, -24, 3)],
        "lean_right": [(0x2105, 48, 48, -24, -24, 1), (0x210E, 48, 48, -24, -24, 1), (0x2117, 48, 48, -24, -24, 1), (0x2120, 48, 48, -24, -24, 1), (0x2129, 48, 48, -24, -24, 1), (0x2132, 48, 48, -24, -24, 1), (0x213B, 48, 48, -24, -24, 1), (0x2144, 48, 48, -24, -24, 1), (0x214D, 48, 48, -24, -24, 1), (0x2156, 48, 48, -24, -24, 2), (0x215F, 48, 48, -24, -24, 1), (0x2168, 48, 48, -24, -24, 2), (0x2171, 48, 48, -24, -24, 1), (0x217A, 48, 48, -24, -24, 2), (0x2183, 48, 48, -24, -24, 1), (0x218C, 48, 48, -24, -24, 2), (0x2195, 48, 48, -24, -24, 1), (0x219E, 48, 48, -24, -24, 2), (0x21A7, 48, 48, -24, -24, 1), (0x21B0, 48, 48, -24, -24, 2), (0x21B9, 48, 48, -24, -24, 1), (0x21C2, 48, 48, -24, -24, 2), (0x21CB, 48, 48, -24, -24, 1), (0x21D4, 48, 48, -24, -24, 2), (0x21DD, 48, 48, -24, -24, 1), (0x21E6, 48, 48, -24, -24, 2), (0x21EF, 48, 48, -24, -24, 1), (0x21F8, 48, 48, -24, -24, 1)],
        "right_hold_loop": [(0x2402, 48, 48, -24, -24, 3), (0x240B, 48, 48, -24, -24, 3), (0x2414, 48, 48, -24, -24, 3), (0x241D, 48, 48, -24, -24, 3), (0x2426, 48, 48, -24, -24, 3), (0x242F, 48, 48, -24, -24, 3), (0x2438, 48, 48, -24, -24, 3), (0x2441, 48, 48, -24, -24, 3)],
        "lean_up": [(0x22AC, 48, 48, -24, -24, 1), (0x22A3, 48, 48, -24, -24, 1), (0x229A, 48, 48, -24, -24, 1), (0x2291, 48, 48, -24, -24, 1), (0x2288, 48, 48, -24, -24, 1), (0x227F, 48, 48, -24, -24, 1), (0x2276, 48, 48, -24, -24, 1), (0x226D, 48, 48, -24, -24, 1), (0x2264, 48, 48, -24, -24, 1), (0x225B, 48, 48, -24, -24, 2), (0x2252, 48, 48, -24, -24, 1)],
        "up_hold_loop": [(0x244A, 48, 48, -24, -24, 3), (0x2453, 48, 48, -24, -24, 3), (0x245C, 48, 48, -24, -24, 3), (0x2465, 48, 48, -24, -24, 3), (0x246E, 48, 48, -24, -24, 3), (0x2477, 48, 48, -24, -24, 3), (0x2480, 48, 48, -24, -24, 3), (0x2489, 48, 48, -24, -24, 3)],
        "lean_down": [(0x22BE, 48, 48, -24, -24, 1), (0x22C7, 48, 48, -24, -24, 1), (0x22D0, 48, 48, -24, -24, 1), (0x22D9, 48, 48, -24, -24, 1), (0x22E2, 48, 48, -24, -24, 1), (0x22EB, 48, 48, -24, -24, 1), (0x22F4, 48, 48, -24, -24, 1), (0x22FD, 48, 48, -24, -24, 1), (0x2306, 48, 48, -24, -24, 1), (0x230F, 48, 48, -24, -24, 2), (0x2318, 48, 48, -24, -24, 1)],
        "down_hold_loop": [(0x2492, 48, 48, -24, -24, 3), (0x249B, 48, 48, -24, -24, 3), (0x24A4, 48, 48, -24, -24, 3), (0x24AD, 48, 48, -24, -24, 3), (0x24B6, 48, 48, -24, -24, 3), (0x24BF, 48, 48, -24, -24, 3), (0x24C8, 48, 48, -24, -24, 3), (0x24D1, 48, 48, -24, -24, 3)],
        "return_from_left": [(0x2201, 48, 48, -24, -24, 2), (0x220A, 48, 48, -24, -24, 3), (0x2213, 48, 48, -24, -24, 3), (0x221C, 48, 48, -24, -24, 3)],
        "return_from_right": [(0x2249, 48, 48, -24, -24, 2), (0x2240, 48, 48, -24, -24, 3), (0x2237, 48, 48, -24, -24, 3), (0x222E, 48, 48, -24, -24, 3)],
        "return_from_up": [(0x2321, 48, 48, -24, -24, 2), (0x232A, 48, 48, -24, -24, 3), (0x2333, 48, 48, -24, -24, 3), (0x233C, 48, 48, -24, -24, 3)],
        "return_from_down": [(0x2369, 48, 48, -24, -24, 2), (0x2360, 48, 48, -24, -24, 3), (0x2357, 48, 48, -24, -24, 3), (0x234E, 48, 48, -24, -24, 3)],
        "laser_start": [(0x24DA, 48, 48, -24, -24, 1), (0x24E3, 48, 48, -24, -24, 1), (0x24EC, 48, 48, -24, -24, 1), (0x24F5, 48, 48, -24, -24, 1), (0x24FE, 48, 48, -24, -24, 1), (0x2507, 48, 48, -24, -24, 1)],
        "laser_hold_loop": [(0x2510, 48, 48, -24, -24, 2), (0x2519, 48, 48, -24, -24, 2), (0x2522, 48, 48, -24, -24, 2), (0x252B, 48, 48, -24, -24, 1)],
        "laser_release": [(0x2534, 48, 48, -24, -24, 3), (0x253D, 48, 48, -24, -24, 3), (0x2546, 48, 48, -24, -24, 3), (0x254F, 48, 48, -24, -24, 3), (0x2558, 48, 48, -24, -24, 3), (0x2561, 48, 48, -24, -24, 3), (0x256A, 48, 48, -24, -24, 3), (0x2573, 48, 48, -24, -24, 3), (0x257C, 48, 48, -24, -24, 3), (0x2585, 48, 48, -24, -24, 3), (0x258E, 48, 48, -24, -24, 3), (0x2597, 48, 48, -24, -24, 3), (0x25A0, 48, 48, -24, -24, 3), (0x25A9, 48, 48, -24, -24, 3)],
        "guard_start": [(0x25B2, 48, 48, -24, -24, 1), (0x25BB, 48, 48, -24, -24, 1), (0x25C4, 48, 48, -24, -24, 1), (0x25CD, 48, 48, -24, -24, 1), (0x25D6, 48, 48, -24, -24, 1), (0x25DF, 48, 48, -24, -24, 1), (0x25E8, 48, 48, -24, -24, 1), (0x25F1, 48, 48, -24, -24, 1), (0x25FA, 48, 48, -24, -24, 1), (0x2603, 48, 48, -24, -24, 1), (0x260C, 48, 48, -24, -24, 1), (0x2615, 48, 48, -24, -24, 1), (0x261E, 48, 48, -24, -24, 1), (0x2627, 48, 48, -24, -24, 1), (0x2630, 48, 48, -24, -24, 1), (0x2639, 48, 48, -24, -24, 2), (0x2642, 48, 48, -24, -24, 3), (0x264B, 48, 48, -24, -24, 4), (0x2654, 48, 48, -24, -24, 2), (0x265D, 48, 48, -24, -24, 2), (0x2666, 48, 48, -24, -24, 2), (0x266F, 48, 48, -24, -24, 2)],
        "guard_hold_loop": [(0x2678, 48, 48, -24, -24, 3), (0x2681, 48, 48, -24, -24, 3), (0x268A, 48, 48, -24, -24, 3), (0x2693, 48, 48, -24, -24, 3)],
        "guard_end": [(0x269C, 48, 48, -24, -24, 2), (0x26A5, 48, 48, -24, -24, 2), (0x26AE, 48, 48, -24, -24, 2), (0x26B7, 48, 48, -24, -24, 2), (0x26C0, 48, 48, -24, -24, 2), (0x26C9, 48, 48, -24, -24, 2)],
        "guard_recover_loop": [(0x26D2, 48, 48, -24, -24, 3), (0x26DB, 48, 48, -24, -24, 3), (0x26E4, 48, 48, -24, -24, 3), (0x26ED, 48, 48, -24, -24, 3)],
        "guard_exit": [(0x26F6, 48, 48, -24, -24, 3), (0x26FF, 48, 48, -24, -24, 3), (0x2708, 48, 48, -24, -24, 3), (0x2711, 48, 48, -24, -24, 3)],
        "death_knockback_respawn": [(0x2723, 48, 48, -24, -24, 2), (0x272C, 48, 48, -24, -24, 4), (0x2735, 48, 48, -24, -24, 4), (0x273E, 48, 48, -24, -24, 3), (0x2747, 48, 48, -24, -24, 2), (0x2750, 48, 48, -24, -24, 1), (0x2759, 48, 48, -24, -24, 1), (0x2762, 48, 48, -24, -24, 1), (0x276B, 48, 48, -24, -24, 2), (0x2774, 48, 48, -24, -24, 2), (0x277D, 48, 48, -24, -24, 2), (0x2786, 48, 48, -24, -24, 3), (0x278F, 48, 48, -24, -24, 3), (0x2798, 48, 48, -24, -24, 3), (0x27A1, 48, 48, -24, -24, 3), (0x27AA, 48, 48, -24, -24, 3), (0x27B3, 48, 48, -24, -24, 3), (0x27BC, 48, 48, -24, -24, 3), (0x27C5, 48, 48, -24, -24, 3), (0x27CE, 48, 48, -24, -24, 3), (0x27D7, 48, 48, -24, -24, 3), (0x27E0, 48, 48, -24, -24, 3), (0x27E9, 48, 48, -24, -24, 3), (0x27F2, 48, 48, -24, -24, 3), (0x27FB, 48, 48, -24, -24, 3), (0x2804, 48, 48, -24, -24, 3), (0x280D, 48, 48, -24, -24, 3), (0x2816, 48, 48, -24, -24, 3), (0x281F, 48, 48, -24, -24, 3), (0x2828, 48, 48, -24, -24, 3), (0x2831, 48, 48, -24, -24, 3), (0x283A, 48, 48, -24, -24, 4), (0x2843, 48, 48, -24, -24, 4), (0x284C, 48, 48, -24, -24, 4)],
    },
    "irori": {
        "idle": [(0x3372, 48, 48, -24, -24, 3), (0x337B, 48, 48, -24, -24, 3), (0x3384, 48, 48, -24, -24, 3), (0x338D, 48, 48, -24, -24, 3), (0x3396, 48, 48, -24, -24, 3), (0x339F, 48, 48, -24, -24, 3), (0x33A8, 48, 48, -24, -24, 3), (0x33B1, 48, 48, -24, -24, 3)],
        "lean_left": [(0x30F3, 48, 48, -24, -24, 1), (0x30EA, 48, 48, -24, -24, 1), (0x30E1, 48, 48, -24, -24, 1), (0x30D8, 48, 48, -24, -24, 1), (0x30CF, 48, 48, -24, -24, 1), (0x30C6, 48, 48, -24, -24, 1), (0x30BD, 48, 48, -24, -24, 1), (0x30B4, 48, 48, -24, -24, 1), (0x30AB, 48, 48, -24, -24, 1), (0x30A2, 48, 48, -24, -24, 2), (0x3099, 48, 48, -24, -24, 1), (0x3090, 48, 48, -24, -24, 2), (0x3087, 48, 48, -24, -24, 1), (0x307E, 48, 48, -24, -24, 2), (0x3075, 48, 48, -24, -24, 1), (0x306C, 48, 48, -24, -24, 2), (0x3063, 48, 48, -24, -24, 1), (0x305A, 48, 48, -24, -24, 2), (0x3051, 48, 48, -24, -24, 1), (0x3048, 48, 48, -24, -24, 2), (0x303F, 48, 48, -24, -24, 1), (0x3036, 48, 48, -24, -24, 2), (0x302D, 48, 48, -24, -24, 1), (0x3024, 48, 48, -24, -24, 2), (0x301B, 48, 48, -24, -24, 1), (0x3012, 48, 48, -24, -24, 2), (0x3009, 48, 48, -24, -24, 1), (0x3000, 48, 48, -24, -24, 1)],
        "left_hold_loop": [(0x33BA, 48, 48, -24, -24, 3), (0x33C3, 48, 48, -24, -24, 3), (0x33CC, 48, 48, -24, -24, 3), (0x33D5, 48, 48, -24, -24, 3), (0x33DE, 48, 48, -24, -24, 3), (0x33E7, 48, 48, -24, -24, 3), (0x33F0, 48, 48, -24, -24, 3), (0x33F9, 48, 48, -24, -24, 3)],
        "lean_right": [(0x3105, 48, 48, -24, -24, 1), (0x310E, 48, 48, -24, -24, 1), (0x3117, 48, 48, -24, -24, 1), (0x3120, 48, 48, -24, -24, 1), (0x3129, 48, 48, -24, -24, 1), (0x3132, 48, 48, -24, -24, 1), (0x313B, 48, 48, -24, -24, 1), (0x3144, 48, 48, -24, -24, 1), (0x314D, 48, 48, -24, -24, 1), (0x3156, 48, 48, -24, -24, 2), (0x315F, 48, 48, -24, -24, 1), (0x3168, 48, 48, -24, -24, 2), (0x3171, 48, 48, -24, -24, 1), (0x317A, 48, 48, -24, -24, 2), (0x3183, 48, 48, -24, -24, 1), (0x318C, 48, 48, -24, -24, 2), (0x3195, 48, 48, -24, -24, 1), (0x319E, 48, 48, -24, -24, 2), (0x31A7, 48, 48, -24, -24, 1), (0x31B0, 48, 48, -24, -24, 2), (0x31B9, 48, 48, -24, -24, 1), (0x31C2, 48, 48, -24, -24, 2), (0x31CB, 48, 48, -24, -24, 1), (0x31D4, 48, 48, -24, -24, 2), (0x31DD, 48, 48, -24, -24, 1), (0x31E6, 48, 48, -24, -24, 2), (0x31EF, 48, 48, -24, -24, 1), (0x31F8, 48, 48, -24, -24, 1)],
        "right_hold_loop": [(0x3402, 48, 48, -24, -24, 3), (0x340B, 48, 48, -24, -24, 3), (0x3414, 48, 48, -24, -24, 3), (0x341D, 48, 48, -24, -24, 3), (0x3426, 48, 48, -24, -24, 3), (0x342F, 48, 48, -24, -24, 3), (0x3438, 48, 48, -24, -24, 3), (0x3441, 48, 48, -24, -24, 3)],
        "lean_up": [(0x32AC, 48, 48, -24, -24, 1), (0x32A3, 48, 48, -24, -24, 1), (0x329A, 48, 48, -24, -24, 1), (0x3291, 48, 48, -24, -24, 1), (0x3288, 48, 48, -24, -24, 1), (0x327F, 48, 48, -24, -24, 1), (0x3276, 48, 48, -24, -24, 1), (0x326D, 48, 48, -24, -24, 1), (0x3264, 48, 48, -24, -24, 1), (0x325B, 48, 48, -24, -24, 2), (0x3252, 48, 48, -24, -24, 1)],
        "up_hold_loop": [(0x344A, 48, 48, -24, -24, 3), (0x3453, 48, 48, -24, -24, 3), (0x345C, 48, 48, -24, -24, 3), (0x3465, 48, 48, -24, -24, 3), (0x346E, 48, 48, -24, -24, 3), (0x3477, 48, 48, -24, -24, 3), (0x3480, 48, 48, -24, -24, 3), (0x3489, 48, 48, -24, -24, 3)],
        "lean_down": [(0x32BE, 48, 48, -24, -24, 1), (0x32C7, 48, 48, -24, -24, 1), (0x32D0, 48, 48, -24, -24, 1), (0x32D9, 48, 48, -24, -24, 1), (0x32E2, 48, 48, -24, -24, 1), (0x32EB, 48, 48, -24, -24, 1), (0x32F4, 48, 48, -24, -24, 1), (0x32FD, 48, 48, -24, -24, 1), (0x3306, 48, 48, -24, -24, 1), (0x330F, 48, 48, -24, -24, 2), (0x3318, 48, 48, -24, -24, 1)],
        "down_hold_loop": [(0x3492, 48, 48, -24, -24, 3), (0x349B, 48, 48, -24, -24, 3), (0x34A4, 48, 48, -24, -24, 3), (0x34AD, 48, 48, -24, -24, 3), (0x34B6, 48, 48, -24, -24, 3), (0x34BF, 48, 48, -24, -24, 3), (0x34C8, 48, 48, -24, -24, 3), (0x34D1, 48, 48, -24, -24, 3)],
        "return_from_left": [(0x3201, 48, 48, -24, -24, 2), (0x320A, 48, 48, -24, -24, 3), (0x3213, 48, 48, -24, -24, 3), (0x321C, 48, 48, -24, -24, 3)],
        "return_from_right": [(0x3249, 48, 48, -24, -24, 2), (0x3240, 48, 48, -24, -24, 3), (0x3237, 48, 48, -24, -24, 3), (0x322E, 48, 48, -24, -24, 3)],
        "return_from_up": [(0x3321, 48, 48, -24, -24, 2), (0x332A, 48, 48, -24, -24, 3), (0x3333, 48, 48, -24, -24, 3), (0x333C, 48, 48, -24, -24, 3)],
        "return_from_down": [(0x3369, 48, 48, -24, -24, 2), (0x3360, 48, 48, -24, -24, 3), (0x3357, 48, 48, -24, -24, 3), (0x334E, 48, 48, -24, -24, 3)],
        "laser_start": [(0x34E3, 48, 48, -24, -24, 1), (0x34EC, 48, 48, -24, -24, 1), (0x34F5, 48, 48, -24, -24, 1), (0x34FE, 48, 48, -24, -24, 1), (0x3507, 48, 48, -24, -24, 1)],
        "laser_hold_loop": [(0x3510, 48, 48, -24, -24, 2), (0x3519, 48, 48, -24, -24, 2), (0x3522, 48, 48, -24, -24, 2), (0x352B, 48, 48, -24, -24, 1)],
        "laser_release": [(0x3534, 48, 48, -24, -24, 2), (0x353D, 48, 48, -24, -24, 2), (0x3546, 48, 48, -24, -24, 2), (0x354F, 48, 48, -24, -24, 2), (0x3558, 48, 48, -24, -24, 2), (0x3561, 48, 48, -24, -24, 2), (0x356A, 48, 48, -24, -24, 2), (0x3573, 48, 48, -24, -24, 2), (0x357C, 48, 48, -24, -24, 2), (0x3585, 48, 48, -24, -24, 2), (0x358E, 48, 48, -24, -24, 2), (0x3597, 48, 48, -24, -24, 2), (0x35A0, 48, 48, -24, -24, 2)],
        "guard_start": [(0x35B2, 48, 48, -24, -24, 2), (0x35BB, 48, 48, -24, -24, 2), (0x35C4, 48, 48, -24, -24, 2), (0x35CD, 48, 48, -24, -24, 2), (0x35D6, 48, 48, -24, -24, 2), (0x35DF, 48, 48, -24, -24, 2), (0x35E8, 48, 48, -24, -24, 2), (0x35F1, 48, 48, -24, -24, 2), (0x35FA, 48, 48, -24, -24, 2), (0x3603, 48, 48, -24, -24, 2), (0x360C, 48, 48, -24, -24, 2), (0x3615, 48, 48, -24, -24, 2), (0x361E, 48, 48, -24, -24, 2), (0x3627, 48, 48, -24, -24, 2), (0x3630, 48, 48, -24, -24, 2), (0x3639, 48, 48, -24, -24, 2), (0x3642, 48, 48, -24, -24, 2), (0x364B, 48, 48, -24, -24, 2), (0x3654, 48, 48, -24, -24, 2), (0x365D, 48, 48, -24, -24, 2), (0x3666, 48, 48, -24, -24, 2), (0x366F, 48, 48, -24, -24, 2)],
        "guard_hold_loop": [(0x3678, 48, 48, -24, -24, 3), (0x3681, 48, 48, -24, -24, 3), (0x368A, 48, 48, -24, -24, 3), (0x3693, 48, 48, -24, -24, 3)],
        "guard_end": [(0x369C, 48, 48, -24, -24, 2), (0x36A5, 48, 48, -24, -24, 2), (0x36AE, 48, 48, -24, -24, 2), (0x36B7, 48, 48, -24, -24, 2), (0x36C0, 48, 48, -24, -24, 2), (0x36C9, 48, 48, -24, -24, 2)],
        "guard_recover_loop": [(0x36D2, 48, 48, -24, -24, 3), (0x36DB, 48, 48, -24, -24, 3), (0x36E4, 48, 48, -24, -24, 3), (0x36ED, 48, 48, -24, -24, 3)],
        "guard_exit": [(0x36F6, 48, 48, -24, -24, 3), (0x36FF, 48, 48, -24, -24, 3)],
        "death_knockback_respawn": [(0x3723, 48, 48, -24, -24, 2), (0x372C, 48, 48, -24, -24, 4), (0x3735, 48, 48, -24, -24, 4), (0x373E, 48, 48, -24, -24, 3), (0x3747, 48, 48, -24, -24, 2), (0x3750, 48, 48, -24, -24, 1), (0x3759, 48, 48, -24, -24, 1), (0x3762, 48, 48, -24, -24, 1), (0x376B, 48, 48, -24, -24, 2), (0x3774, 48, 48, -24, -24, 2), (0x377D, 48, 48, -24, -24, 2), (0x3786, 48, 48, -24, -24, 3), (0x378F, 48, 48, -24, -24, 3), (0x3798, 48, 48, -24, -24, 3), (0x37A1, 48, 48, -24, -24, 3), (0x37AA, 48, 48, -24, -24, 3), (0x37B3, 48, 48, -24, -24, 3), (0x37BC, 48, 48, -24, -24, 3), (0x37C5, 48, 48, -24, -24, 3), (0x37CE, 48, 48, -24, -24, 3), (0x37D7, 48, 48, -24, -24, 3), (0x37E0, 48, 48, -24, -24, 3), (0x37E9, 48, 48, -24, -24, 3), (0x37F2, 48, 48, -24, -24, 3), (0x37FB, 48, 48, -24, -24, 3), (0x3804, 48, 48, -24, -24, 3), (0x380D, 48, 48, -24, -24, 3), (0x3816, 48, 48, -24, -24, 3), (0x381F, 48, 48, -24, -24, 3), (0x3828, 48, 48, -24, -24, 3), (0x3831, 48, 48, -24, -24, 3), (0x383A, 48, 48, -24, -24, 3), (0x3843, 48, 48, -24, -24, 3), (0x384C, 48, 48, -24, -24, 3)],
    },
}
# character -> program ROM address of its 256 colours (palette block 4, as the game loads it)
PALETTES: dict[str, int] = {"jb5": 0xE0800, "irori": 0xE0800}
CHARACTERS = {0: "irori", 2: "jb5"}            # Arrange +0x4A -> ESP character
DRAW_SHIFT_X = -4                              # ESP Ra.De. draws the body 4 px off its X (native)


def _segments(table: int) -> list[tuple[str, int | None]]:
    """The ESP animations a frame table is made of: [(name, records or None = the rest)].
    The lean and up / down tables are a transition then a hold loop (their restart index is the
    transition's length: 28 and 11 records, as ESP Ra.De.'s); the Power Shot runs 14 -> 9 -> 15
    (laser start, hold, release) and the barrier 18 -> 19 -> 20 -> 21 -> 22 (guard start, hold,
    end, recover, exit)."""
    return {
        IDLE: [("idle", None)],
        LEAN: [("lean_right", 28), ("right_hold_loop", None)],
        LEAN_L: [("lean_left", 28), ("left_hold_loop", None)],
        UP: [("lean_up", 11), ("up_hold_loop", None)],
        DOWN: [("lean_down", 11), ("down_hold_loop", None)],
        RETURN: [("return_from_right", None)],
        RETURN_L: [("return_from_left", None)],
        UP_RET: [("return_from_up", None)],
        DOWN_RET: [("return_from_down", None)],
        POWER1: [("laser_start", None)],
        S9: [("laser_hold_loop", None)],
        POWER2: [("laser_release", None)],
        BAR18: [("guard_start", None)],
        BAR19: [("guard_hold_loop", None)],
        BAR20: [("guard_end", None)],
        BAR21: [("guard_recover_loop", None)],
        S22: [("guard_exit", None)],
        ENTRY: [("death_knockback_respawn", None)],
    }[table]


def _pick(frames: list, n: int) -> list:
    """n frames from an animation: one to one when the counts match, else by time."""
    if len(frames) == n:
        return list(frames)
    total = sum(f[5] for f in frames)
    starts = np.cumsum([0] + [f[5] for f in frames])
    out = []
    for j in range(n):
        t = j * total / n
        out.append(frames[int(np.searchsorted(starts, t, side="right")) - 1])
    return out


def quantise(pixels: list[np.ndarray], pal: np.ndarray, colours: int = 32) -> tuple[np.ndarray, list[np.ndarray]]:
    """One palette of `colours` RGB555 colours for all the frames (8-bit indices into pal, 0
    transparent) and each frame as indices 0..colours-1, 255 transparent. Median cut on the
    used colours weighted by their pixel counts."""
    counts = np.zeros(256, np.int64)
    for px in pixels:
        counts += np.bincount(px.ravel(), minlength=256)
    counts[0] = 0
    used = np.nonzero(counts)[0]
    boxes = [used]
    while len(boxes) < colours:
        # split the box with the largest weighted range
        best, bi, axis = -1, None, 0
        for i, b in enumerate(boxes):
            if len(b) < 2:
                continue
            rng = pal[b].max(0) - pal[b].min(0)
            score = rng.max() * np.log1p(counts[b].sum())
            if score > best:
                best, bi, axis = score, i, int(rng.argmax())
        if bi is None:
            break
        b = boxes.pop(bi)
        b = b[np.argsort(pal[b][:, axis], kind="stable")]
        w = np.cumsum(counts[b])
        k = int(np.searchsorted(w, w[-1] / 2)) + 1
        k = min(max(k, 1), len(b) - 1)
        boxes += [b[:k], b[k:]]
    lut = np.full(256, 255, np.uint8)
    out_pal = np.zeros((colours, 3), np.int64)
    for i, b in enumerate(boxes):
        wsum = counts[b].sum()
        out_pal[i] = np.round((pal[b] * counts[b, None]).sum(0) / max(wsum, 1))
        lut[b] = i
    return out_pal, [lut[px] for px in pixels]


def palette_words(rgb: np.ndarray) -> bytes:
    """PGM palette words (xRRRRRGGGGGBBBBB, big-endian)."""
    return b"".join(struct.pack(">H", int(r) << 10 | int(g) << 5 | int(b)) for r, g, b in rgb)


class PlayerFrames:
    def __init__(self, state, elf):
        self.elf = elf
        self.rom = esprade.EspRade(state.esprade_chars)
        assert ANIMS and PALETTES, "esprade_chars: animation inventory missing"
        self.slots: list[tuple[int, str]] = []
        self.pictures: dict[str, np.ndarray] = {}
        self.palettes: dict[int, np.ndarray] = {}
        for ch, name in CHARACTERS.items():
            frames = sorted({f[:3] for a in ANIMS[name].values() for f in a})
            raw = [self.rom.sprite(c, w, h) for c, w, h in frames]
            pal, idx = quantise(raw, self.rom.palette(PALETTES[name]))
            self.palettes[ch] = pal
            for (c, w, h), px in zip(frames, idx):
                self.pictures[f"esp_{name}_{c:05x}_{w}x{h}"] = px

    def adjust_row(self, row: list) -> list:
        row = list(row)
        for s, tab in ((STATE_2, LEAN_L), (STATE_6, RETURN_L)):
            kind, _, k = row[s]
            k = {key: v for key, v in k.items() if key != "set6"}
            row[s] = (kind, tab, k)
        return row

    def _arrange_list(self, table: int, ch: int, n: int, size: int) -> list[bytes | None]:
        base = {LEAN_L: LEAN, RETURN_L: RETURN}.get(table, table)
        lp = struct.unpack("<4I", self.elf.read(base, 16))[ch]
        out = []
        for r in struct.unpack(f"<{n}I", self.elf.read(lp, 4 * n)):
            out.append(self.elf.read(r, size) if r else None)
        return out

    def _anchor(self, ch: int) -> tuple[int, int]:
        """Pixel offset added to ESP's dx/dy: ESP Ra.De.'s player draw routine shifts the body
        DRAW_SHIFT_X in native X from the position point (the hitbox), as here."""
        return DRAW_SHIFT_X, 0

    def records(self, table: int, form: int, n: int, size: int) -> list[tuple[bytes | None, str | None]]:
        ch = 2 * (form // 2)
        name = CHARACTERS[ch]
        arr = self._arrange_list(table, ch, n, size)
        want: list = []
        segs = _segments(table)
        left = n
        for k, (anim, count) in enumerate(segs):
            m = left if count is None or k == len(segs) - 1 else min(count, left)
            want += _pick(ANIMS[name][anim], m)
            left -= m
        cx, cy = self._anchor(ch)
        out = []
        for a, (c, w, h, dx, dy, _) in zip(arr, want):
            if a is None:
                out.append((None, None))
                continue
            rec = bytearray(a)
            add = struct.unpack_from(">hh", rec, RECORD_ADD)
            struct.pack_into(">hh", rec, RECORD_OFFSET, (dx + cx) * 64 - add[0], (dy + cy) * 64 - add[1])
            struct.pack_into(">I", rec, RECORD_SPRITE, 0)
            struct.pack_into(">H", rec, RECORD_SIZE, (w // 16) << 9 | h)
            out.append((bytes(rec), f"esp_{name}_{c:05x}_{w}x{h}"))
        return out

    def install(self, state, ps) -> None:
        state.keyed_pictures = {**getattr(state, "keyed_pictures", {}), **self.pictures}
        state.keyed_picture_slots = getattr(state, "keyed_picture_slots", []) + self.slots
        for ch, pal in self.palettes.items():
            words = palette_words(pal)
            for at in BODY_PALETTES[ch]:
                ps.add(at, words, f"ESP Ra.De. {CHARACTERS[ch]} body palette at {at:#x}")

    def summary(self) -> str:
        return (f"{len(self.pictures)} pictures for {len(self.slots)} records: "
                + ", ".join(f"{CHARACTERS[c]} as {'Chihiro' if c == 0 else 'Black'}" for c in CHARACTERS))
