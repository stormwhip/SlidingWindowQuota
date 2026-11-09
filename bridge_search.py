"""Find a common upper supporting line by simultaneous tree descent."""


def descend(node, a, b, right):
    child = node.right if right else node.left
    return child, a + child.a, b + child.b


def line(node, a, b):
    x = node.anchor_x
    return x, node.anchor_y + a * x + b, node.dy + a * node.dx, node.dx


def point(node, a, b):
    return node.lo, node.value + a * node.lo + b


def bridge_points(left, right):
    """Find any support contact on each side, in time linear in tree height."""
    separator_twice = left.hi + right.lo
    la, lb = left.a, left.b
    ra, rb = right.a, right.b
    while left.left is not None or right.left is not None:
        if left.left is None:
            lx, ly = point(left, la, lb)
            rx, ry, rdy, rdx = line(right, ra, rb)
            sign = (ly - ry) * rdx - rdy * (lx - rx)
            if not sign:
                return (lx, ly), (rx, ry)
            right, ra, rb = descend(right, ra, rb, sign > 0)
            continue
        if right.left is None:
            lx, ly, ldy, ldx = line(left, la, lb)
            rx, ry = point(right, ra, rb)
            sign = (ry - ly) * ldx - ldy * (rx - lx)
            if not sign:
                return (lx, ly), (rx, ry)
            left, la, lb = descend(left, la, lb, sign < 0)
            continue

        lx, ly, ldy, ldx = line(left, la, lb)
        rx, ry, rdy, rdx = line(right, ra, rb)
        slope_cmp = ldy * rdx - rdy * ldx
        left_height = 2 * ly * ldx + ldy * (separator_twice - 2 * lx)
        right_height = 2 * ry * rdx + rdy * (separator_twice - 2 * rx)
        height_cmp = left_height * rdx - right_height * ldx
        if not (slope_cmp or height_cmp):
            return (lx, ly), (rx, ry)
        # The sign table follows from comparing the supporting lines on
        # either side of the fixed separator. Each step drops one child.
        if slope_cmp >= 0:
            if height_cmp >= 0:
                left, la, lb = descend(left, la, lb, True)
            else:
                right, ra, rb = descend(right, ra, rb, False)
        elif height_cmp >= 0:
            right, ra, rb = descend(right, ra, rb, True)
        else:
            left, la, lb = descend(left, la, lb, False)
    return point(left, la, lb), point(right, ra, rb)
