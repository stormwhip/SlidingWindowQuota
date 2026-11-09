package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeMap;

import static org.apache.hadoop.yarn.server.resourcemanager.reservation.QuotaChanges.*;

/** Leaf-oriented AVL tree with one upper common tangent per internal node. */
final class BridgeQuotaIndex {
  private Node root;
  private final long end;

  BridgeQuotaIndex(TreeMap<Long, Long> jumps, long end) {
    this.end = end;
    var leaves = new ArrayList<Node>();
    long slope = 0, intercept = 0;
    jumps.putIfAbsent(0L, 0L);
    jumps.putIfAbsent(end, 0L);
    for (var entry : jumps.entrySet()) {
      long x = entry.getKey(), delta = entry.getValue();
      if (x > end) break;
      slope = Math.addExact(slope, delta);
      intercept = Math.subtractExact(intercept, Math.multiplyExact(delta, x));
      if (x >= 0) {
        leaves.add(new Node(x, affine(slope, x, intercept)));
      }
    }
    root = build(leaves, 0, leaves.size());
  }

  long valueAt(long x) {
    return value(new Cursor(root, 0, 0), x);
  }

  boolean allows(Transaction change, long quota) {
    for (Piece piece : change.pieces()) {
      if (Math.addExact(valueAt(piece.lo()), piece.at(piece.lo())) > quota
          || Math.addExact(valueAt(piece.hi()), piece.at(piece.hi())) > quota
          || exceeds(new Cursor(root, piece.slope(), piece.intercept()),
              piece.lo(), piece.hi(), quota)) return false;
    }
    return true;
  }

  Peak peak(Piece piece) {
    long lo = piece.lo(), hi = piece.hi();
    var best = new Peak(Math.addExact(valueAt(lo), piece.at(lo)), lo);
    best = best.include(Math.addExact(valueAt(hi), piece.at(hi)), hi);
    return peak(new Cursor(root, piece.slope(), piece.intercept()), lo, hi, best);
  }

  void commit(Transaction change) {
    for (long x : change.added()) {
      Node old = find(root, x);
      if (old == null) root = insert(root, x, valueAt(x));
      else old.references++;
    }
    apply(root, change.pieces(), end);
    for (long x : change.removed()) {
      Node old = find(root, x);
      if (old == null) throw new IllegalStateException("Missing reservation endpoint " + x);
      if (--old.references == 0) root = erase(root, x);
    }
  }

  private static final class Node {
    Node left, right;
    long lo, hi, y, a, b, dx = 1, dy, anchorX, anchorY;
    int height = 1, references = 1;

    Node(long x, long y) {
      lo = hi = x;
      this.y = y;
    }

    boolean leaf() {
      return left == null;
    }
  }

  private static final class Cursor {
    Node node;
    long a, b;

    Cursor(Node node, long a, long b) {
      this.a = a;
      this.b = b;
      enter(node);
    }

    void enter(Node child) {
      node = child;
      a = Math.addExact(a, child.a);
      b = Math.addExact(b, child.b);
    }

    long y() {
      return Math.addExact(node.y, affine(a, node.lo, b));
    }

    long anchorY() {
      return Math.addExact(node.anchorY, affine(a, node.anchorX, b));
    }

    long dy() {
      return affine(a, node.dx, node.dy);
    }
  }

  private static long affine(long a, long x, long b) {
    return Math.addExact(Math.multiplyExact(a, x), b);
  }

  private static int products(long a, long b, long c, long d) {
    int high = Long.compare(Math.multiplyHigh(a, b), Math.multiplyHigh(c, d));
    return high != 0 ? high : Long.compareUnsigned(a * b, c * d);
  }

  private static int relativeHeight(Cursor l, Cursor r, long leftY, long rightY,
      long leftDy, long rightDy, long middleTwice) {
    long ldx = l.node.dx, rdx = r.node.dx;
    long offsetL = Math.subtractExact(middleTwice, Math.multiplyExact(2, l.node.anchorX));
    long offsetR = Math.subtractExact(middleTwice, Math.multiplyExact(2, r.node.anchorX));
    try {
      long difference = Math.subtractExact(leftY, rightY);
      long lnum = affine(Math.multiplyExact(2, difference), ldx,
          Math.multiplyExact(leftDy, offsetL));
      long rnum = Math.multiplyExact(rightDy, offsetR);
      return products(lnum, rdx, rnum, ldx);
    } catch (ArithmeticException overflow) {
      BigInteger lnum = BigInteger.valueOf(leftY).subtract(BigInteger.valueOf(rightY))
          .shiftLeft(1).multiply(BigInteger.valueOf(ldx))
          .add(BigInteger.valueOf(leftDy).multiply(BigInteger.valueOf(offsetL)));
      BigInteger rnum = BigInteger.valueOf(rightDy).multiply(BigInteger.valueOf(offsetR));
      return lnum.multiply(BigInteger.valueOf(rdx)).compareTo(rnum.multiply(BigInteger.valueOf(ldx)));
    }
  }

  private static void refresh(Node n) {
    n.lo = n.left.lo;
    n.hi = n.right.hi;
    n.height = 1 + Math.max(n.left.height, n.right.height);
    Cursor l = new Cursor(n.left, 0, 0);
    Cursor r = new Cursor(n.right, 0, 0);
    long middleTwice = Math.addExact(n.left.hi, n.right.lo);
    long x, y, rx, ry;
    while (true) {
      boolean leftLeaf = l.node.leaf(), rightLeaf = r.node.leaf();
      x = leftLeaf ? l.node.lo : l.node.anchorX;
      y = leftLeaf ? l.y() : l.anchorY();
      rx = rightLeaf ? r.node.lo : r.node.anchorX;
      ry = rightLeaf ? r.y() : r.anchorY();
      if (leftLeaf && rightLeaf) break;
      if (leftLeaf) {
        int side = products(Math.subtractExact(y, ry), r.node.dx, r.dy(), x - rx);
        if (side == 0) break;
        r.enter(side > 0 ? r.node.right : r.node.left);
      } else if (rightLeaf) {
        int side = products(Math.subtractExact(ry, y), l.node.dx, l.dy(), rx - x);
        if (side == 0) break;
        l.enter(side < 0 ? l.node.right : l.node.left);
      } else {
        long leftDy = l.dy(), rightDy = r.dy();
        int slope = products(leftDy, r.node.dx, rightDy, l.node.dx);
        int height = relativeHeight(l, r, y, ry, leftDy, rightDy, middleTwice);
        if (slope == 0 && height == 0) break;
        if (slope >= 0 && height >= 0) l.enter(l.node.right);
        else if (slope >= 0) r.enter(r.node.left);
        else if (height >= 0) r.enter(r.node.right);
        else l.enter(l.node.left);
      }
    }
    n.anchorX = x;
    n.anchorY = y;
    n.dx = rx - x;
    n.dy = Math.subtractExact(ry, y);
  }

  private static Node join(Node left, Node right) {
    var n = new Node(0, 0);
    n.left = left;
    n.right = right;
    refresh(n);
    return n;
  }

  private static Node build(List<Node> leaves, int lo, int hi) {
    if (hi - lo == 1) return leaves.get(lo);
    int middle = (lo + hi) >>> 1;
    return join(build(leaves, lo, middle), build(leaves, middle, hi));
  }

  private static void shift(Node n, long a, long b) {
    n.a = Math.addExact(n.a, a);
    n.b = Math.addExact(n.b, b);
  }

  private static void push(Node n) {
    if (n.a != 0 || n.b != 0) {
      shift(n.left, n.a, n.b);
      shift(n.right, n.a, n.b);
      n.a = n.b = 0;
    }
  }

  private static Node rotateLeft(Node n) {
    Node top = n.right;
    push(top);
    n.right = top.left;
    refresh(n);
    top.left = n;
    refresh(top);
    return top;
  }

  private static Node rotateRight(Node n) {
    Node top = n.left;
    push(top);
    n.left = top.right;
    refresh(n);
    top.right = n;
    refresh(top);
    return top;
  }

  private static Node balance(Node n) {
    int difference = n.left.height - n.right.height;
    if (difference > 1) {
      push(n.left);
      if (n.left.left.height < n.left.right.height) n.left = rotateLeft(n.left);
      return rotateRight(n);
    }
    if (difference < -1) {
      push(n.right);
      if (n.right.left.height > n.right.right.height) n.right = rotateRight(n.right);
      return rotateLeft(n);
    }
    refresh(n);
    return n;
  }

  private static Node find(Node n, long x) {
    while (!n.leaf()) n = x <= n.left.hi ? n.left : n.right;
    return n.lo == x ? n : null;
  }

  private static Node insert(Node n, long x, long y) {
    if (n.leaf()) {
      var leaf = new Node(x, y);
      return x < n.lo ? join(leaf, n) : join(n, leaf);
    }
    push(n);
    if (x <= n.left.hi) n.left = insert(n.left, x, y);
    else n.right = insert(n.right, x, y);
    return balance(n);
  }

  private static Node erase(Node n, long x) {
    if (n.leaf()) return null;
    push(n);
    if (x <= n.left.hi) n.left = erase(n.left, x);
    else n.right = erase(n.right, x);
    if (n.left == null) return n.right;
    if (n.right == null) return n.left;
    return balance(n);
  }

  private static long value(Cursor at, long x) {
    while (!at.node.leaf()) {
      Node left = at.node.left, right = at.node.right;
      if (x <= left.hi) at.enter(left);
      else if (x >= right.lo) at.enter(right);
      else {
        long yl = value(new Cursor(left, at.a, at.b), left.hi);
        long yr = value(new Cursor(right, at.a, at.b), right.lo);
        long rise = Math.subtractExact(yr, yl);
        long width = right.lo - left.hi;
        if (rise % width != 0) throw new IllegalStateException("Noninteger interval slope");
        return affine(rise / width, x - left.hi, yl);
      }
    }
    return at.y();
  }

  private static boolean apply(Node n, List<Piece> pieces, long end) {
    Piece only = null;
    boolean multiple = false;
    for (Piece p : pieces) {
      if (p.lo() <= n.hi && (n.lo < p.hi() || (n.lo == end && p.hi() == end))) {
        if (only != null) {
          multiple = true;
          break;
        }
        only = p;
      }
    }
    if (only == null) return false;
    if (!multiple && only.lo() <= n.lo && (n.hi < only.hi() || only.hi() == end)) {
      shift(n, only.slope(), only.intercept());
      return true;
    }
    push(n);
    boolean changed = apply(n.left, pieces, end) | apply(n.right, pieces, end);
    if (changed) refresh(n);
    return changed;
  }

  private static void maximize(Cursor at) {
    while (!at.node.leaf()) at.enter(at.dy() > 0 ? at.node.right : at.node.left);
  }

  private static boolean exceeds(Cursor at, long lo, long hi, long quota) {
    Node n = at.node;
    if (hi <= n.lo || n.hi <= lo) return false;
    if (lo < n.lo && n.hi < hi) {
      maximize(at);
      return at.y() > quota;
    }
    return exceeds(new Cursor(n.left, at.a, at.b), lo, hi, quota)
        || exceeds(new Cursor(n.right, at.a, at.b), lo, hi, quota);
  }

  private static Peak peak(Cursor at, long lo, long hi, Peak best) {
    Node n = at.node;
    if (hi <= n.lo || n.hi <= lo) return best;
    if (lo < n.lo && n.hi < hi) {
      maximize(at);
      return best.include(at.y(), at.node.lo);
    }
    best = peak(new Cursor(n.left, at.a, at.b), lo, hi, best);
    return peak(new Cursor(n.right, at.a, at.b), lo, hi, best);
  }
}
