package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/** Changes to a scalar sliding-window integral, in coordinates relative to its domain. */
final class QuotaChanges {
  record Reservation(long start, long end, long demand) {
    static final Reservation NONE = new Reservation(0, 0, 0);

    long overlap(long t, long window) {
      return Math.max(0, Math.min(t, end) - Math.max(t - window, start));
    }
  }

  record Piece(long lo, long hi, long slope, long intercept) {
    long at(long x) {
      return Math.addExact(Math.multiplyExact(slope, x), intercept);
    }
  }

  record Jump(long x, long delta) {}
  record Peak(long value, long time) {
    Peak include(long candidate, long x) {
      return candidate > value || (candidate == value && x < time)
          ? new Peak(candidate, x) : this;
    }
  }

  record Transaction(List<Piece> pieces, List<Jump> jumps, long[] added, long[] removed) {}

  private QuotaChanges() {}

  static void addReservation(Map<Long, Long> jumps, Reservation r, long window) {
    if (r.demand == 0 || r.start >= r.end) return;
    long rate = r.demand;
    jumps.merge(r.start, rate, Math::addExact);
    jumps.merge(r.end, Math.negateExact(rate), Math::addExact);
    jumps.merge(Math.addExact(r.start, window), Math.negateExact(rate), Math::addExact);
    jumps.merge(Math.addExact(r.end, window), rate, Math::addExact);
  }

  static Transaction between(Reservation old, Reservation next, long window, long end) {
    var oldKeys = new TreeMap<Long, Long>();
    var newKeys = new TreeMap<Long, Long>();
    addReservation(oldKeys, old, window);
    addReservation(newKeys, next, window);
    return betweenJumps(oldKeys, newKeys, end);
  }

  static Transaction betweenJumps(TreeMap<Long, Long> oldKeys, TreeMap<Long, Long> newKeys,
      long end) {
    long[] added = difference(newKeys, oldKeys, end);
    long[] removed = difference(oldKeys, newKeys, end);
    var net = new TreeMap<Long, Long>();
    oldKeys.forEach((x, delta) -> net.put(x, Math.negateExact(delta)));
    newKeys.forEach((x, delta) -> net.merge(x, delta, Math::addExact));
    return new Transaction(pieces(net, end), nonzero(net), added, removed);
  }

  private static long[] difference(TreeMap<Long, Long> a, TreeMap<Long, Long> b, long end) {
    var keys = new long[a.size()];
    int count = 0;
    for (long x : a.keySet()) {
      if (x > 0 && x < end && !b.containsKey(x)) keys[count++] = x;
    }
    return java.util.Arrays.copyOf(keys, count);
  }

  /** Keep the endpoint values on both ends of each nonzero affine piece. */
  static List<Piece> pieces(TreeMap<Long, Long> jumps, long end) {
    var result = new ArrayList<Piece>();
    long slope = 0, intercept = 0, previous = 0;
    for (var jump : jumps.entrySet()) {
      long x = jump.getKey(), delta = jump.getValue();
      if (delta == 0) continue;
      if (x >= end) break;
      if (x > 0) {
        if (slope != 0 || intercept != 0) result.add(new Piece(previous, x, slope, intercept));
        previous = x;
      }
      slope = Math.addExact(slope, delta);
      intercept = Math.subtractExact(intercept, Math.multiplyExact(x, delta));
    }
    if (slope != 0 || intercept != 0) result.add(new Piece(previous, end, slope, intercept));
    return result;
  }

  static List<Jump> nonzero(TreeMap<Long, Long> jumps) {
    var result = new ArrayList<Jump>();
    jumps.forEach((x, delta) -> {
      if (delta != 0) result.add(new Jump(x, delta));
    });
    return result;
  }
}
