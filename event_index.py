"""An augmented event treap for exact local scanning."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    x: int
    delta: int
    priority: int
    left: "Event | None"
    right: "Event | None"
    slope: int
    moment: int
    count: int


def make(x, delta, priority, left=None, right=None):
    slope, moment, count = delta, x * delta, 1
    for child in (left, right):
        if child is not None:
            slope += child.slope
            moment += child.moment
            count += child.count
    return Event(x, delta, priority, left, right, slope, moment, count)


def merge(left, right):
    if left is None or right is None:
        return right if left is None else left
    if left.priority > right.priority:
        return make(left.x, left.delta, left.priority, left.left, merge(left.right, right))
    return make(right.x, right.delta, right.priority, merge(left, right.left), right.right)


def change(root, x, delta, rng):
    if delta == 0:
        return root
    if root is None:
        return make(x, delta, rng.getrandbits(64))
    if root.x == x:
        total = root.delta + delta
        return make(x, total, root.priority, root.left, root.right) if total else merge(root.left, root.right)
    if x < root.x:
        left = change(root.left, x, delta, rng)
        if left and left.priority > root.priority:
            lower = make(root.x, root.delta, root.priority, left.right, root.right)
            return make(left.x, left.delta, left.priority, left.left, lower)
        return make(root.x, root.delta, root.priority, left, root.right)
    right = change(root.right, x, delta, rng)
    if right and right.priority > root.priority:
        lower = make(root.x, root.delta, root.priority, root.left, right.left)
        return make(right.x, right.delta, right.priority, lower, right.right)
    return make(root.x, root.delta, root.priority, root.left, right)


def events(root, l, r):
    """Yield events in the open interval (l,r), visiting each reported node once."""
    stack = []
    while root or stack:
        while root:
            if root.x <= l:
                root = root.right
            else:
                stack.append(root)
                root = root.left
        if not stack:
            break
        root = stack.pop()
        if root.x >= r:
            break
        yield root.x, root.delta
        root = root.right


def prefix(root, x):
    slope = moment = 0
    while root:
        if x < root.x:
            root = root.left
        else:
            slope += root.delta
            moment += root.x * root.delta
            if root.left:
                slope += root.left.slope
                moment += root.left.moment
            root = root.right
    return slope, moment


def value_at(root, x):
    slope, moment = prefix(root, x)
    return slope * x - moment


def allowed(root, lo, hi, pieces, quota):
    for l, r, a, b in pieces:
        l, r = max(l, lo), min(r, hi)
        if l > r:
            continue
        slope, moment = prefix(root, l)
        value = slope * l - moment + a * l + b
        if value > quota or value_at(root, r) + a * r + b > quota:
            return False
        slope += a
        for x, delta in events(root, l, r):
            value += slope * (x - l)
            if value > quota:
                return False
            slope += delta
            l = x
    return True


def jumps(w, reservation):
    a, b, demand = reservation
    return ((a, demand), (b, -demand), (a + w, -demand), (b + w, demand))


def commit(root, w, old, new, rng):
    combined = {}
    for reservation, sign in ((old, -1), (new, 1)):
        if reservation:
            for x, d in jumps(w, reservation):
                combined[x] = combined.get(x, 0) + sign * d
    for x, d in combined.items():
        root = change(root, x, d, rng)
    return root
