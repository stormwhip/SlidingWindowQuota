package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.math.BigInteger;
import java.util.Iterator;
import java.util.Map;
import java.util.NavigableMap;

import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;

/** Exact scalar memory integral, with millisecond timestamps and MiB-ms sums. */
final class ExactRleWindow {
  private ExactRleWindow() {}

  record Peak(long memoryTime, long windowEnd) {}

  /**
   * Window ends range over [first, last]. Call while the plan snapshot is stable.
   * The caller supplies the end range and handles instantaneous resource limits.
   */
  static Peak proposed(Plan plan, ReservationAllocation candidate,
      long window, long first, long last) throws PlanningException {
    var old = plan.getReservationById(candidate.getReservationId());
    if (old != null && !old.getUser().equals(candidate.getUser())) {
      throw new PlanningException("Replacement must keep the reservation owner");
    }
    return prepare(plan, old, candidate, window, first, last).evaluate(Long.MAX_VALUE);
  }

  record Prepared(NavigableMap<Long, Resource> history, NavigableMap<Long, Resource> removed,
      NavigableMap<Long, Resource> added, long window, long first, long last) {
    Peak evaluate(long stopAbove) {
      return scan(history, removed, added, window, first, last, stopAbove);
    }

    /** Check the proposed state against the original state's per-window overage. */
    boolean nonWorsening(long quota) {
      if (quota < 0) throw new IllegalArgumentException("Expected a nonnegative quota");
      long start = supportStart(window, first, last);
      long leftEnd = Math.subtractExact(last, window);
      var left = new Edge(history, removed, added, start, leftEnd);
      var right = new Edge(history, removed, added, start, last);
      long old = 0, proposed = 0, t = start;
      for (long next = right.nextOr(last); next <= first && next < last;
          next = right.nextOr(last)) {
        old = advance(old, right.history.rate, t, next);
        proposed = advance(proposed, right.rate(), t, next);
        right.stepAt(next);
        t = next;
      }
      old = advance(old, right.history.rate, t, first);
      proposed = advance(proposed, right.rate(), t, first);
      if (proposed > Math.max(quota, old)) return false;
      for (t = first; t < last;) {
        long enter = right.nextOr(last);
        long leftNext = left.nextOr(leftEnd);
        long leave = Math.addExact(leftNext, window);
        long next = Math.min(enter, leave);
        long oldNext = advance(old,
            Math.subtractExact(right.history.rate, left.history.rate), t, next);
        long proposedNext = advance(proposed,
            Math.subtractExact(right.rate(), left.rate()), t, next);
        if (proposedNext > Math.max(quota, oldNext)
            || violatesAtCrossing(old, oldNext, proposed, proposedNext, quota)) return false;
        if (next < last) {
          if (next == enter) right.stepAt(enter);
          if (next == leave) left.stepAt(leftNext);
        }
        old = oldNext;
        proposed = proposedNext;
        t = next;
      }
      return true;
    }
  }

  private static boolean violatesAtCrossing(long old, long oldNext, long proposed,
      long proposedNext, long quota) {
    if (!((old < quota && oldNext > quota) || (old > quota && oldNext < quota))) return false;
    // Use the opposite endpoint's distance to the crossing as each weight.
    long weightHere = Math.abs(Math.subtractExact(oldNext, quota));
    long weightNext = Math.abs(Math.subtractExact(old, quota));
    long here = Math.subtractExact(proposed, quota);
    long next = Math.subtractExact(proposedNext, quota);
    try {
      return Math.addExact(Math.multiplyExact(weightHere, here),
          Math.multiplyExact(weightNext, next)) > 0;
    } catch (ArithmeticException overflow) {
      return BigInteger.valueOf(weightHere).multiply(BigInteger.valueOf(here))
          .add(BigInteger.valueOf(weightNext).multiply(BigInteger.valueOf(next))).signum() > 0;
    }
  }

  static Prepared prepare(Plan plan, ReservationAllocation old, ReservationAllocation candidate,
      long window, long first, long last) {
    long start = supportStart(window, first, last);
    var history = plan.getConsumptionForUserOverTime(candidate.getUser(), start, last);
    var removed = old == null ? null : old.getResourcesOverTime(start, last);
    var added = candidate.getResourcesOverTime(start, last);
    return new Prepared(history.getCumulative(), removed == null ? null : removed.getCumulative(),
        added.getCumulative(), window, first, last);
  }

  /**
   * Integrate the first window, then advance both edges. Each edge combines
   * history - removed + added in place, using at most three sorted cursors.
   */
  private static Peak scan(NavigableMap<Long, Resource> history,
      NavigableMap<Long, Resource> removed, NavigableMap<Long, Resource> added,
      long window, long first, long last, long stopAbove) {
    long start = supportStart(window, first, last);
    // Exclude last: a change at that exact time has zero area in every window.
    long leftEnd = Math.subtractExact(last, window);
    var left = new Edge(history, removed, added, start, leftEnd);
    var right = new Edge(history, removed, added, start, last);
    long value = 0;
    long t = start;
    for (long next = right.nextOr(last); next <= first && next < last;
        next = right.nextOr(last)) {
      value = advance(value, right.rate(), t, next);
      right.stepAt(next);
      t = next;
    }
    value = advance(value, right.rate(), t, first);
    if (value > stopAbove) return new Peak(value, first);
    t = first;
    long best = value, earliest = first;
    while (t < last) {
      long enter = right.nextOr(last);
      long leftNext = left.nextOr(leftEnd);
      long leave = Math.addExact(leftNext, window);
      long next = Math.min(enter, leave);
      value = advance(value, Math.subtractExact(right.rate(), left.rate()), t, next);
      if (value > stopAbove) return new Peak(value, next);
      if (value > best) {
        best = value;
        earliest = next;
      }
      if (next < last) {
        if (next == enter) right.stepAt(enter);
        if (next == leave) left.stepAt(leftNext);
      }
      t = next;
    }
    return new Peak(best, earliest);
  }

  private static long supportStart(long window, long first, long last) {
    if (window <= 0 || first > last) {
      throw new IllegalArgumentException("Expected a positive window and an ordered end range");
    }
    return Math.subtractExact(first, window);
  }

  private static long advance(long value, long rate, long from, long to) {
    return Math.addExact(value, Math.multiplyExact(rate, Math.subtractExact(to, from)));
  }

  private static final class Edge {
    private final Cursor history;
    private final Cursor removed;
    private final Cursor added;

    Edge(NavigableMap<Long, Resource> history, NavigableMap<Long, Resource> removed,
        NavigableMap<Long, Resource> added, long start, long end) {
      this.history = new Cursor(history, start, end);
      this.removed = removed == null ? null : new Cursor(removed, start, end);
      this.added = new Cursor(added, start, end);
    }

    long rate() {
      long kept = removed == null ? history.rate : Math.subtractExact(history.rate, removed.rate);
      return Math.addExact(kept, added.rate);
    }

    long nextOr(long end) {
      long next = Math.min(history.nextOr(end), added.nextOr(end));
      return removed == null ? next : Math.min(next, removed.nextOr(end));
    }

    void stepAt(long time) {
      history.stepAt(time);
      if (removed != null) removed.stepAt(time);
      added.stepAt(time);
    }
  }

  private static final class Cursor {
    private final Iterator<Map.Entry<Long, Resource>> changes;
    private Map.Entry<Long, Resource> next;
    private long rate;

    Cursor(NavigableMap<Long, Resource> points, long start, long end) {
      var atStart = points.floorEntry(start);
      rate = atStart == null ? 0 : memory(atStart.getValue());
      changes = points.subMap(start, false, end, false).entrySet().iterator();
      next = changes.hasNext() ? changes.next() : null;
    }

    long nextOr(long end) {
      return next == null ? end : next.getKey();
    }

    void stepAt(long time) {
      if (next != null && next.getKey() == time) {
        rate = memory(next.getValue());
        next = changes.hasNext() ? changes.next() : null;
      }
    }

    private static long memory(Resource value) {
      return value == null ? 0 : value.getMemorySize();
    }
  }
}
