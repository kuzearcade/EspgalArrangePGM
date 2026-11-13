"""statecmp.py OLD_SHOOT NEW_SHOOT: did a speed change keep the game the same?

Two replays of the same trace (shoot.lua logs: "frame key state...") at different speeds sample
the state at frame ends, which falls mid-step whenever a step overruns, so states differ there
whenever the timing differs. At keys held one frame in both runs, with the previous key also
held one frame, the sample is the finished step: those must match exactly.
(Same build and clock: 0 differences anywhere.)
"""
import collections
import sys


def load(fn):
    st, cnt, order = {}, collections.Counter(), []
    for line in open(fn):
        x = line.split(None, 2)
        if len(x) < 3:
            continue
        k = x[1]
        if k not in st:
            order.append(k)
        st.setdefault(k, x[2])
        cnt[k] += 1
    return st, cnt, order


a, ac, ao = load(sys.argv[1])
b, bc, _ = load(sys.argv[2])
clean = [k for i, k in enumerate(ao) if i > 0 and k in b and ac[k] == 1 and bc[k] == 1
         and ac[ao[i - 1]] == 1 and bc.get(ao[i - 1]) == 1]
diff = [k for k in clean if a[k] != b[k]]
print(f"{len(clean)} keys without lag around them in both runs; states differ at {len(diff)}"
      + (": " + " ".join(diff[:20]) if diff else ""))
sys.exit(1 if diff else 0)
