from pathlib import Path

from espgalarr.pgm import m68k
from espgalarr.stages import code as stage


class _State:
    def __init__(self):
        self.p_free = 0x200000
        self.budget = {}

    def alloc_p(self, size, align=0x10, note=""):
        a = (self.p_free + align - 1) & ~(align - 1)
        self.p_free = a + size
        return a


def test_install_routines(tmp_path, monkeypatch):
    (tmp_path / "demo.s").write_text("; replaces 0x123456\n; hook 0x130000 jsr\n; entry demo\ndemo:\n    moveq #1,d0\n    rts\n")
    monkeypatch.setattr(stage, "ASM_DIR", tmp_path)
    st = _State()
    ps = m68k.PatchSet()
    assert stage.install_routines(st, ps) == 1
    kinds = {p.note.split()[0] for p in ps.patches}
    assert kinds == {"routine", "demo.s"}
    jmp = next(p for p in ps.patches if "replaces" in p.note)
    assert jmp.offset == 0x123456 and jmp.data[:2] == b"\x4e\xf9" and int.from_bytes(jmp.data[2:], "big") == 0x200000
    jsr = next(p for p in ps.patches if "hook" in p.note)
    assert jsr.offset == 0x130000 and jsr.data[:2] == b"\x4e\xb9"
