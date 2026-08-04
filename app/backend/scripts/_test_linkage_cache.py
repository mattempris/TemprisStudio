"""The persisted Ward tree, and the guard that stops it being reused when it must not be.

The tree is a deterministic function of the vectors, so caching it is safe — but a linkage
matrix encodes row *positions*, so reusing one after the item set changed would cut
clusters over the wrong rows and produce a plausible, wrong hierarchy. That is the failure
worth a test: it would not raise, it would just be incorrect.

No blob, no GPU. A fake ProjectService stands in for storage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes import tiers  # noqa: E402
from app.services.clustering import backbone as bb  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


class FakeService:
    """In-memory stand-in for the array and index blobs."""

    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.indexes: dict[str, list[str]] = {}
        self.saves = 0

    def save_array(self, client, project, name, arr):
        self.arrays[f"{project}/artifacts/{name}.npy"] = arr
        self.saves += 1
        return f"{project}/artifacts/{name}.npy"

    def load_array(self, client, path):
        return self.arrays.get(path)

    def save_index(self, client, project, name, index, **kw):
        self.indexes[f"{project}/artifacts/{name}_index.json"] = list(index)
        return f"{project}/artifacts/{name}_index.json"

    def load_index(self, client, path):
        return self.indexes.get(path)


def main() -> int:
    rng = np.random.default_rng(7)
    emb = rng.normal(size=(30, 16)).astype(np.float32)
    ids = [f"item-{i}" for i in range(30)]
    tree = bb.build_linkage_tree(emb)

    svc = FakeService()
    print("Nothing cached yet")
    check(
        "a cold load returns None",
        tiers._load_cached_tree(svc, "c", "p", "task", "profile", ids) is None,
    )

    svc.save_array("c", "p", tiers._linkage_name("task", "profile"), tree)
    svc.save_index("c", "p", tiers._linkage_name("task", "profile"), ids)

    print("\nExact same items")
    got = tiers._load_cached_tree(svc, "c", "p", "task", "profile", ids)
    check("the tree comes back", got is not None)
    check("byte-identical to what was saved", got is not None and np.array_equal(got, tree))
    check(
        "and cutting it gives the same clusters as the freshly-built tree",
        np.array_equal(bb.cut_tree(got, 5), bb.cut_tree(tree, 5)),
    )

    print("\nThe guard")
    check(
        "a different number of items is refused",
        tiers._load_cached_tree(svc, "c", "p", "task", "profile", ids[:-1]) is None,
    )
    check(
        "one renamed item is refused",
        tiers._load_cached_tree(svc, "c", "p", "task", "profile", ids[:-1] + ["other"]) is None,
    )
    # The dangerous one: same set, same size, different order. Row positions no longer
    # mean what the tree says they mean.
    reordered = [ids[1], ids[0], *ids[2:]]
    check(
        "the same items in a different order are refused",
        tiers._load_cached_tree(svc, "c", "p", "task", "profile", reordered) is None,
    )
    check(
        "a different tier does not read this tier's tree",
        tiers._load_cached_tree(svc, "c", "p", "task", "category", ids) is None,
    )
    check(
        "a different entity does not either",
        tiers._load_cached_tree(svc, "c", "p", "skill", "profile", ids) is None,
    )
    check(
        "a different project does not either",
        tiers._load_cached_tree(svc, "c", "other", "task", "profile", ids) is None,
    )

    print("\nA corrupt or truncated blob is refused rather than trusted")
    svc.arrays[f"p/artifacts/{tiers._linkage_name('task', 'profile')}.npy"] = tree[:5]
    check(
        "a tree with the wrong number of merge rows is refused",
        tiers._load_cached_tree(svc, "c", "p", "task", "profile", ids) is None,
    )
    svc.arrays[f"p/artifacts/{tiers._linkage_name('task', 'profile')}.npy"] = np.zeros(4)
    check(
        "a one-dimensional array is refused",
        tiers._load_cached_tree(svc, "c", "p", "task", "profile", ids) is None,
    )

    print("\nNaming is stable — a renamed blob would silently orphan every cached tree")
    check(
        "linkage name is derived from entity and tier",
        tiers._linkage_name("task", "category") == "tier_task_category_linkage",
        tiers._linkage_name("task", "category"),
    )

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
