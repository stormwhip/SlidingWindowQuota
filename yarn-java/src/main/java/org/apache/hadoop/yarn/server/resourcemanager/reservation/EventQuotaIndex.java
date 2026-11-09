package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.util.ArrayDeque;
import java.util.SplittableRandom;
import java.util.TreeMap;

import static org.apache.hadoop.yarn.server.resourcemanager.reservation.QuotaChanges.*;

/** Ordered slope events with prefix slope and moment, used by the exact scan. */
final class EventQuotaIndex {
  private final SplittableRandom random = new SplittableRandom(701);
  private Node root;

  EventQuotaIndex(TreeMap<Long, Long> jumps) {
    var stack = new ArrayDeque<Node>();
    jumps.forEach((x, delta) -> {
      if (delta == 0) return;
      var node = new Node(x, delta, random.nextLong());
      while (!stack.isEmpty() && stack.peek().priority < node.priority) {
        node.left = stack.pop();
      }
      if (!stack.isEmpty()) stack.peek().right = node;
      stack.push(node);
    });
    root = stack.peekLast();
    initialize(root);
  }

  long valueAt(long x) {
    Prefix p = prefix(x);
    return Math.subtractExact(Math.multiplyExact(p.slope, x), p.moment);
  }

  boolean allows(Transaction change, long quota) {
    for (Piece piece : change.pieces()) {
      var p = prefix(piece.lo());
      long initial = Math.addExact(
          Math.subtractExact(Math.multiplyExact(p.slope, piece.lo()), p.moment),
          piece.at(piece.lo()));
      if (initial > quota || Math.addExact(valueAt(piece.hi()), piece.at(piece.hi())) > quota) {
        return false;
      }
      var sweep = new Sweep(initial, Math.addExact(p.slope, piece.slope()), piece.lo(), quota);
      if (scan(root, piece.lo(), piece.hi(), sweep)) return false;
    }
    return true;
  }

  Peak peak(Piece piece) {
    var p = prefix(piece.lo());
    long initial = Math.addExact(
        Math.subtractExact(Math.multiplyExact(p.slope, piece.lo()), p.moment),
        piece.at(piece.lo()));
    var sweep = new Sweep(initial, Math.addExact(p.slope, piece.slope()), piece.lo(), Long.MAX_VALUE);
    scan(root, piece.lo(), piece.hi(), sweep);
    sweep.visit(piece.hi(), 0);
    return new Peak(sweep.best, sweep.witness);
  }

  void commit(Transaction change) {
    for (Jump jump : change.jumps()) root = adjust(root, jump.x(), jump.delta());
  }

  private record Prefix(long slope, long moment) {}

  private Prefix prefix(long x) {
    long slope = 0, moment = 0;
    for (Node n = root; n != null;) {
      if (x < n.x) n = n.left;
      else {
        slope = Math.addExact(slope, Math.addExact(n.delta, slope(n.left)));
        moment = Math.addExact(moment, Math.addExact(Math.multiplyExact(n.x, n.delta), moment(n.left)));
        n = n.right;
      }
    }
    return new Prefix(slope, moment);
  }

  private static final class Sweep {
    long value, slope, previous, best, witness;
    final long quota;

    Sweep(long value, long slope, long previous, long quota) {
      this.value = best = value;
      this.slope = slope;
      this.previous = witness = previous;
      this.quota = quota;
    }

    boolean visit(long x, long jump) {
      value = Math.addExact(value, Math.multiplyExact(slope, x - previous));
      slope = Math.addExact(slope, jump);
      previous = x;
      if (value > best) {
        best = value;
        witness = x;
      }
      return value > quota;
    }
  }

  private static boolean scan(Node n, long lo, long hi, Sweep sweep) {
    if (n == null) return false;
    return (n.x > lo && scan(n.left, lo, hi, sweep))
        || (lo < n.x && n.x < hi && sweep.visit(n.x, n.delta))
        || (n.x < hi && scan(n.right, lo, hi, sweep));
  }

  private static final class Node {
    final long x, priority;
    long delta, slope, moment;
    Node left, right;

    Node(long x, long delta, long priority) {
      this.x = x;
      this.delta = delta;
      this.priority = priority;
    }
  }

  private static long slope(Node n) {
    return n == null ? 0 : n.slope;
  }

  private static long moment(Node n) {
    return n == null ? 0 : n.moment;
  }

  private static Node refresh(Node n) {
    n.slope = Math.addExact(n.delta, Math.addExact(slope(n.left), slope(n.right)));
    n.moment = Math.addExact(Math.multiplyExact(n.x, n.delta),
        Math.addExact(moment(n.left), moment(n.right)));
    return n;
  }

  private static void initialize(Node n) {
    if (n == null) return;
    initialize(n.left);
    initialize(n.right);
    refresh(n);
  }

  private static Node merge(Node l, Node r) {
    if (l == null) return r;
    if (r == null) return l;
    if (l.priority >= r.priority) {
      l.right = merge(l.right, r);
      return refresh(l);
    }
    r.left = merge(l, r.left);
    return refresh(r);
  }

  private Node adjust(Node n, long x, long delta) {
    if (n == null) return refresh(new Node(x, delta, random.nextLong()));
    if (x == n.x) {
      n.delta = Math.addExact(n.delta, delta);
      if (n.delta == 0) return merge(n.left, n.right);
    } else if (x < n.x) {
      n.left = adjust(n.left, x, delta);
      if (n.left != null && n.left.priority > n.priority) {
        Node top = n.left;
        n.left = top.right;
        top.right = refresh(n);
        return refresh(top);
      }
    } else {
      n.right = adjust(n.right, x, delta);
      if (n.right != null && n.right.priority > n.priority) {
        Node top = n.right;
        n.right = top.left;
        top.left = refresh(n);
        return refresh(top);
      }
    }
    return refresh(n);
  }
}
